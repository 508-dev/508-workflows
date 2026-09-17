"""Focused contract tests for bounded wiki-editing persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from five08.wiki_editing.models import (
    WikiBaseDocumentSnapshot,
    WikiConflictDetails,
    WikiConversationProvenance,
    WikiEditConflictError,
    WikiEditRequestInput,
    WikiEditStateError,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiProposalOutput,
    WikiPublishResult,
    WikiSelectedConversationSource,
    WikiSourceReference,
    wiki_content_hash,
)
from five08.wiki_editing.store import InMemoryWikiEditingStore


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
    duplicate = store.claim_authoring(
        proposal.id,
        organization_id="org-1",
        omp_metadata=_metadata(),
        now=started_at + timedelta(seconds=59),
        authoring_lease_seconds=60,
    )

    assert first is not None
    assert duplicate is None
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
    store.complete_proposal(
        proposal.id,
        organization_id="org-1",
        output=_output(),
    )

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
    store.complete_proposal(
        proposal.id,
        organization_id="org-1",
        output=_output(),
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
