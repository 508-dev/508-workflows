"""Focused contract tests for bounded wiki-editing persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from five08.settings import SharedSettings
from five08.wiki_editing.models import (
    WikiBaseDocumentSnapshot,
    WikiConflictDetails,
    WikiConversationProvenance,
    WikiAuthoringLeaseHeldError,
    WikiEditConflictError,
    WikiEditRequestInput,
    WikiEditStateError,
    WikiEditReviewArtifact,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiProposalOutput,
    WikiPublishResult,
    WikiSelectedConversationSource,
    WikiSourceReference,
    wiki_content_hash,
)
from five08.wiki_editing.store import (
    InMemoryWikiEditingStore,
    PostgresWikiEditingStore,
)


def _request() -> WikiEditRequestInput:
    source_text = "We decided to require review before a wiki edit is published."
    return WikiEditRequestInput(
        organization_id="org-1",
        actor_id="actor-1",
        instruction="Update the deployment guide with our decision.",
        request_idempotency_key="discord-interaction-1",
        selected_conversation=[
            WikiSelectedConversationSource(
                provenance=WikiConversationProvenance(
                    source_type="discord_thread",
                    source_ref="thread-1",
                    title="Deployment decision",
                ),
                organization_visible_text=source_text,
            )
        ],
    )


def _metadata() -> WikiOmpRunMetadata:
    return WikiOmpRunMetadata(
        session_id="omp-session-1",
        model="openrouter/example",
        run_id="omp-run-1",
        provider="openrouter",
    )


def _output() -> WikiProposalOutput:
    return WikiProposalOutput(
        proposed_title="Deployment guide",
        proposed_text="# Deployment\n\nAll wiki edits require review before publishing.",
        proposed_diff="@@ -1 +1 @@\n-Old guide\n+All wiki edits require review.",
        summary="Adds the explicit review-before-publish requirement.",
        source_refs=[
            WikiSourceReference(
                source_type="discord_thread",
                source_ref="thread-1",
                title="Deployment decision",
            )
        ],
    )


def _initial_proposal_input(request: WikiEditRequestInput) -> WikiProposalCreate:
    return WikiProposalCreate(
        request_id=request.id,
        organization_id=request.organization_id,
        target_action="create",
    )


def test_postgres_authoring_claim_locks_request_before_proposal() -> None:
    store = PostgresWikiEditingStore(SharedSettings())
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"request_id": "request-1"}
    lock_order: list[str] = []

    def lock_request(*_args: object, **_kwargs: object) -> dict[str, object]:
        lock_order.append("request")
        return {}

    def lock_proposal(*_args: object, **_kwargs: object) -> dict[str, object]:
        lock_order.append("proposal")
        return {}

    with (
        patch.object(store, "_connection", return_value=connection),
        patch.object(store, "_locked_request", side_effect=lock_request),
        patch.object(store, "_locked_proposal", side_effect=lock_proposal),
        patch(
            "five08.wiki_editing.store._proposal_from_row",
            return_value=SimpleNamespace(status="proposed"),
        ),
    ):
        result = store.claim_authoring(
            "proposal-1",
            organization_id="org-1",
            omp_metadata=_metadata(),
        )

    assert result is None
    assert lock_order == ["request", "proposal"]
    assert cursor.execute.call_args_list[0].args[1] == ("proposal-1", "org-1")


def test_request_idempotency_and_public_reads_exclude_source_text() -> None:
    store = InMemoryWikiEditingStore()
    request = _request()

    created, was_created = store.create_or_get_request(request)
    repeated, was_repeated_created = store.create_or_get_request(request)

    assert was_created is True
    assert was_repeated_created is False
    assert repeated.id == created.id
    public_payload = created.model_dump(mode="json")
    assert "instruction" not in public_payload
    assert "organization_visible_text" not in public_payload
    assert "require review before" not in str(public_payload)

    authoring = store.get_authoring_request(
        created.id,
        organization_id="org-1",
    )
    assert authoring is not None
    assert authoring.instruction == request.instruction
    assert (
        authoring.selected_source_text[0].organization_visible_text
        == "We decided to require review before a wiki edit is published."
    )

    conflicting_request = request.model_copy(
        update={"instruction": "Use the same idempotency key for a different request."}
    )
    with pytest.raises(WikiEditConflictError, match="idempotency"):
        store.create_or_get_request(conflicting_request)


def test_proposal_output_is_committed_once_and_revision_increments() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    first = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )

    work_item = store.claim_authoring(
        first.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )
    assert work_item is not None
    assert work_item.proposal.status == "authoring"
    proposed = store.complete_proposal(
        first.id,
        organization_id="org-1",
        output=_output(),
    )
    assert proposed.status == "proposed"
    assert proposed.revision == 1
    assert proposed.proposed_title == "Deployment guide"

    with pytest.raises(WikiEditStateError, match="proposed.*proposed"):
        store.complete_proposal(
            first.id,
            organization_id="org-1",
            output=_output().model_copy(update={"proposed_title": "Changed"}),
        )

    second = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    assert second.revision == 2
    assert second.status == "queued"
    latest = store.get_latest_proposal_for_request(
        request.id,
        organization_id="org-1",
    )
    assert latest is not None
    assert latest.id == second.id


def test_initial_proposal_reservation_is_idempotent_and_repairs_orphaned_request() -> (
    None
):
    store = InMemoryWikiEditingStore()

    # A normal Discord retry supplies a fresh request/proposal ID, but the same
    # idempotency key. It must recover the original first revision instead of
    # making a duplicate proposal.
    request = _request()
    initial, should_enqueue = store.create_or_get_initial_proposal(
        request,
        _initial_proposal_input(request),
    )
    retry_request = request.model_copy(update={"id": "discord-retry-request"})
    repeated, should_reenqueue = store.create_or_get_initial_proposal(
        retry_request,
        _initial_proposal_input(retry_request),
    )

    assert should_enqueue is True
    assert should_reenqueue is True
    assert initial.status == "queued"
    assert initial.revision == 1
    assert repeated.id == initial.id
    assert repeated.request_id == request.id
    assert repeated.revision == 1

    # This models the old two-transaction failure mode: an idempotent request
    # exists, but a process died before reserving its first proposal. Retrying
    # the same request repairs it rather than leaving an unserviceable record.
    orphaned_request = _request().model_copy(
        update={"request_idempotency_key": "orphaned-discord-interaction"}
    )
    stored_request, was_created = store.create_or_get_request(orphaned_request)
    repaired, should_enqueue_repaired = store.create_or_get_initial_proposal(
        orphaned_request,
        _initial_proposal_input(orphaned_request),
    )

    assert was_created is True
    assert should_enqueue_repaired is True
    assert repaired.request_id == stored_request.id
    assert repaired.revision == 1
    assert repaired.status == "queued"


def test_authoring_claim_blocks_duplicate_delivery_during_lease() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    started_at = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)

    first = store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
        now=started_at,
        authoring_lease_seconds=60,
    )
    assert first is not None
    with pytest.raises(WikiAuthoringLeaseHeldError) as held:
        store.claim_authoring(
            proposal.id,
            organization_id="org-1",
            omp_metadata=_metadata(),
            now=started_at + timedelta(seconds=59),
            authoring_lease_seconds=60,
        )
    assert held.value.retry_after_seconds == pytest.approx(1.0)
    persisted = store.get_proposal(proposal.id, organization_id="org-1")
    assert persisted is not None
    assert persisted.status == "authoring"
    assert persisted.authoring_started_at == started_at


def test_authoring_claim_reclaims_expired_lease_for_bound_omp_run() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    started_at = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    lease_seconds = 60
    metadata = _metadata()
    first = store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=metadata,
        now=started_at,
        authoring_lease_seconds=lease_seconds,
    )
    assert first is not None

    with pytest.raises(WikiEditConflictError, match="bound to another OMP run"):
        store.claim_authoring(
            proposal.id,
            organization_id="org-1",
            omp_metadata=metadata.model_copy(update={"run_id": "omp-run-2"}),
            now=started_at + timedelta(seconds=lease_seconds),
            authoring_lease_seconds=lease_seconds,
        )

    reclaimed_at = started_at + timedelta(seconds=lease_seconds)
    reclaimed = store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=metadata,
        now=reclaimed_at,
        authoring_lease_seconds=lease_seconds,
    )

    assert reclaimed is not None
    assert reclaimed.proposal.omp_metadata == metadata
    assert reclaimed.proposal.authoring_started_at == reclaimed_at


def test_revision_retires_predecessor_and_preserves_its_immutable_draft() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    predecessor = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    store.claim_authoring(
        predecessor.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )
    completed = store.complete_proposal(
        predecessor.id,
        organization_id="org-1",
        output=_output(),
    )

    revision = store.create_revision(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
            revision_parent_id=predecessor.id,
            revision_instruction="Shorten the second paragraph.",
        )
    )

    retired = store.get_proposal(predecessor.id, organization_id="org-1")
    assert retired is not None
    assert retired.status == "canceled"
    assert revision.status == "queued"
    assert revision.revision == completed.revision + 1

    work_item = store.claim_authoring(
        revision.id,
        organization_id="org-1",
        omp_metadata=_metadata().model_copy(update={"run_id": "omp-run-2"}),
    )
    assert work_item is not None
    assert work_item.proposal.revision_parent_id == predecessor.id
    assert work_item.proposal.revision_instruction == "Shorten the second paragraph."
    parent_draft = work_item.proposal.revision_parent_draft
    assert parent_draft is not None
    assert parent_draft.proposal_id == predecessor.id
    assert parent_draft.revision == completed.revision
    assert parent_draft.title == _output().proposed_title
    assert parent_draft.text == _output().proposed_text
    assert parent_draft.content_hash == wiki_content_hash(_output().proposed_text)

    # The child only receives a private copy reconstructed from its retired
    # predecessor; mutating that copy must not alter the durable revision base.
    parent_draft.text = "tampered local copy"
    reread = store.get_authoring_work_item(revision.id, organization_id="org-1")
    assert reread is not None
    assert reread.proposal.revision_parent_draft is not None
    assert reread.proposal.revision_parent_draft.text == _output().proposed_text


def test_revision_of_failed_child_preserves_nearest_reviewed_ancestor() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    original = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    store.claim_authoring(
        original.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )
    store.complete_proposal(
        original.id,
        organization_id="org-1",
        output=_output(),
    )
    failed_child = store.create_revision(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
            revision_parent_id=original.id,
            revision_instruction="Make the heading shorter.",
        )
    )
    store.fail_proposal(
        failed_child.id,
        organization_id="org-1",
        failure_code="authoring_failed",
    )

    replacement = store.create_revision(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
            revision_parent_id=failed_child.id,
            revision_instruction="Try the shortened heading again.",
        )
    )
    work_item = store.claim_authoring(
        replacement.id,
        organization_id="org-1",
        omp_metadata=_metadata().model_copy(update={"run_id": "omp-run-3"}),
    )

    assert work_item is not None
    assert work_item.proposal.revision_parent_id == failed_child.id
    assert work_item.proposal.revision_parent_draft is not None
    assert work_item.proposal.revision_parent_draft.proposal_id == original.id
    assert work_item.proposal.revision_parent_draft.text == _output().proposed_text


def test_conditional_failure_does_not_overwrite_an_authoring_claim() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    claimed = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    store.claim_authoring(
        claimed.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )

    not_failed = store.fail_proposal_if_status(
        claimed.id,
        organization_id="org-1",
        failure_code="authoring_enqueue_failed",
        expected_statuses=frozenset({"queued"}),
    )
    still_authoring = store.get_proposal(claimed.id, organization_id="org-1")
    assert not_failed is None
    assert still_authoring is not None
    assert still_authoring.status == "authoring"

    queued = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    failed = store.fail_proposal_if_status(
        queued.id,
        organization_id="org-1",
        failure_code="authoring_enqueue_failed",
        expected_statuses=frozenset({"queued"}),
    )
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_code == "authoring_enqueue_failed"


def test_publish_attempt_is_recorded_before_external_write_and_never_reclaimed() -> (
    None
):
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )
    completed = store.complete_proposal(
        proposal.id,
        organization_id="org-1",
        output=_output(),
    )

    with pytest.raises(WikiEditStateError, match="acknowledged"):
        store.claim_publish_attempt(proposal.id, organization_id="org-1")

    review = WikiEditReviewArtifact.from_proposal(completed)
    acknowledged = store.acknowledge_review(
        proposal.id,
        organization_id="org-1",
        actor_id="actor-1",
        review_content_hash=review.content_hash,
    )
    assert acknowledged.review_acknowledged is True

    first_claim = store.claim_publish_attempt(
        proposal.id,
        organization_id="org-1",
    )
    second_claim = store.claim_publish_attempt(
        proposal.id,
        organization_id="org-1",
    )

    assert first_claim.should_execute is True
    assert first_claim.operation.status == "write_started"
    assert second_claim.should_execute is False
    assert second_claim.operation.id == first_claim.operation.id

    unknown = store.mark_publish_unknown(proposal.id, organization_id="org-1")
    assert unknown.status == "unknown"
    still_not_claimed = store.claim_publish_attempt(
        proposal.id,
        organization_id="org-1",
    )
    assert still_not_claimed.should_execute is False

    published = store.mark_publish_succeeded(
        proposal.id,
        organization_id="org-1",
        result=WikiPublishResult(
            document_id="outline-doc-1",
            document_url="https://outline.example/doc-1",
            document_version="7",
            content_hash=wiki_content_hash(_output().proposed_text),
        ),
    )
    assert published.status == "succeeded"
    final = store.get_proposal(proposal.id, organization_id="org-1")
    assert final is not None
    assert final.status == "published"
    assert final.published_document_id == "outline-doc-1"


def test_known_provider_conflict_after_claim_does_not_become_unknown() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="create",
        )
    )
    store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
    )
    completed = store.complete_proposal(
        proposal.id,
        organization_id="org-1",
        output=_output(),
    )
    review = WikiEditReviewArtifact.from_proposal(completed)
    store.acknowledge_review(
        proposal.id,
        organization_id="org-1",
        actor_id="actor-1",
        review_content_hash=review.content_hash,
    )
    store.claim_publish_attempt(proposal.id, organization_id="org-1")

    conflicted = store.mark_conflict(
        proposal.id,
        organization_id="org-1",
        conflict=WikiConflictDetails(
            current_document_id="outline-doc-1",
            current_content_hash=wiki_content_hash("newer content"),
            message="The document changed before Outline accepted this edit.",
        ),
    )

    assert conflicted.status == "conflict"
    operation = store.get_publish_operation(proposal.id, organization_id="org-1")
    assert operation is not None
    assert operation.status == "conflict"
    assert (
        store.claim_publish_attempt(
            proposal.id,
            organization_id="org-1",
        ).should_execute
        is False
    )


def test_update_snapshot_is_private_but_its_hash_is_exposed() -> None:
    store = InMemoryWikiEditingStore()
    request, _ = store.create_or_get_request(_request())
    text = "# Existing guide\n\nInternal wiki article body."
    snapshot = WikiBaseDocumentSnapshot(
        document_id="outline-doc-1",
        title="Deployment guide",
        document_url="https://outline.example/doc-1",
        document_version="6",
        content=text,
        content_hash=wiki_content_hash(text),
    )

    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="org-1",
            target_action="update",
            target_document_id="outline-doc-1",
            base_document=snapshot,
        )
    )

    payload = proposal.model_dump(mode="json")
    assert proposal.base_document is not None
    assert proposal.base_document.content_hash == wiki_content_hash(text)
    assert "Internal wiki article body" not in str(payload)


def test_review_packet_keeps_only_safe_source_links_and_never_truncates() -> None:
    packet = WikiEditReviewArtifact.from_output(
        proposed_title="Deployment guide",
        proposed_article="The complete proposed article.",
        complete_diff="@@ -1 +1 @@\n-Old\n+New",
        source_refs=[
            WikiSourceReference(
                source_type="outline_document",
                source_ref="shared-doc",
                title="Shared deployment guide",
                source_url="https://outline.example/doc/shared",
            ),
            WikiSourceReference(
                source_type="other",
                source_ref="unsafe-uri",
                title="Unsafe URI",
                source_url="javascript:alert(1)",
            ),
            WikiSourceReference(
                source_type="other",
                source_ref="credential-uri",
                title="Credential URI",
                source_url="https://user:secret@example.test/private",
            ),
        ],
    )

    assert [link.url for link in packet.source_links] == [
        "https://outline.example/doc/shared"
    ]
    rendered = packet.attachment_bytes().decode("utf-8")
    assert "The complete proposed article." in rendered
    assert "@@ -1 +1 @@" in rendered
    assert "javascript:" not in rendered
    assert "secret@example" not in rendered


def test_review_packet_escapes_markdown_source_titles_but_keeps_safe_url() -> None:
    """A source title cannot add clickable links to an approval attachment."""
    title = (
        "[Release checklist](https://attacker.example) <https://also-attacker.example>"
    )
    packet = WikiEditReviewArtifact.from_output(
        proposed_title="Deployment guide",
        proposed_article="The complete proposed article.",
        complete_diff="@@ -1 +1 @@\n-Old\n+New",
        source_refs=[
            WikiSourceReference(
                source_type="discord_thread",
                source_ref="thread-1",
                title=title,
                source_url="https://outline.example/doc/shared",
            )
        ],
    )

    rendered = packet.attachment_bytes().decode("utf-8")

    # Keep the original provenance text for the immutable review binding, but
    # render every Markdown delimiter literally. The allowlisted source URL is
    # consequently the attachment's only clickable source link.
    assert packet.source_links[0].title == title
    assert (
        r"1. discord_thread: \[Release checklist\]\(https\:\/\/attacker\.example\) "
        r"\<https\:\/\/also\-attacker\.example\>"
    ) in rendered
    assert (
        "1. discord_thread: [Release checklist](https://attacker.example)"
        not in rendered
    )
    assert "\n   <https://outline.example/doc/shared>" in rendered
