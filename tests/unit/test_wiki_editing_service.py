"""Behavior tests for the backend-owned wiki editing workflow."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from five08.agent.models import AgentIdentityContext
from five08.clients.outline import OutlineConflictError, OutlineDocument
from five08.wiki_editing.models import (
    WikiEditActionRequest,
    WikiEditCreateRequest,
    WikiEditPermissionError,
    WikiEditReviewAcknowledgementRequest,
    WikiEditRevisionRequest,
    WikiOmpRunMetadata,
)
from five08.wiki_editing.omp import WikiAuthoringTransientError, WikiOmpDraft
from five08.wiki_editing.service import (
    WikiEditingService,
    WikiEditingValidationError,
)
from five08.wiki_editing.store import InMemoryWikiEditingStore


class _Outline:
    def __init__(self) -> None:
        self.document = OutlineDocument(
            id="doc-1",
            title="Deployment guide",
            text="Deploy with the existing release checklist.",
            url="https://outline.example/doc/deployment",
            collection_id="collection-1",
            parent_document_id=None,
            revision=1,
            updated_at="2026-09-17T12:00:00Z",
        )
        self.update_calls = 0
        self.create_calls = 0
        self.raise_on_update: Exception | None = None
        self.raise_on_create: Exception | None = None

    def get_document(self, *, document_id: str) -> OutlineDocument:
        assert document_id == self.document.id
        return self.document

    def update_document(self, **kwargs: Any) -> OutlineDocument:
        self.update_calls += 1
        if self.raise_on_update is not None:
            raise self.raise_on_update
        assert kwargs["document_id"] == self.document.id
        assert kwargs["expected_revision"] == 1
        self.document = replace(
            self.document,
            title=kwargs["title"],
            text=kwargs["text"],
            revision=(self.document.revision or 0) + 1,
        )
        return self.document

    def create_document(self, **kwargs: Any) -> OutlineDocument:
        self.create_calls += 1
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return OutlineDocument(
            id="created-1",
            title=kwargs["title"],
            text=kwargs["text"],
            url="https://outline.example/doc/created",
            collection_id=kwargs["collection_id"],
            parent_document_id=None,
            revision=1,
            updated_at="2026-09-17T13:00:00Z",
        )


class _Author:
    def __init__(self) -> None:
        self.work_items: list[object] = []

    def author(
        self, work_item: object, *, metadata: WikiOmpRunMetadata
    ) -> WikiOmpDraft:
        self.work_items.append(work_item)
        proposal = getattr(work_item, "proposal")
        suffix = proposal.revision_instruction or "initial"
        return WikiOmpDraft(
            title="Deployment guide",
            text=f"Deploy with the approved release checklist. ({suffix})",
            summary="Clarifies the approved deployment path.",
            source_refs=(),
            metadata=metadata,
        )


class _TransientAuthor:
    def author(
        self, _work_item: object, *, metadata: WikiOmpRunMetadata
    ) -> WikiOmpDraft:
        del metadata
        raise WikiAuthoringTransientError("sandbox is starting")


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "discord_server_id": "guild-1",
        "wiki_editing_enabled": True,
        "wiki_authoring_configured": True,
        "wiki_outline_collection_id": "collection-1",
        "wiki_editing_api_timeout_seconds": 5.0,
        "wiki_editing_max_instruction_characters": 4_000,
        "wiki_editing_max_document_characters": 16_000,
        "knowledge_capture_max_characters": 20_000,
        "outline_admin_api_key": "writer-key",
        "outline_base_url": "https://outline.example",
        "outline_api_timeout_seconds": 5.0,
        "wiki_omp_model": "openrouter/test-model",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(*, user_id: str = "writer") -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id=user_id,
        organization_id="guild-1",
        guild_id="guild-1",
        channel_id="channel-1",
        roles=["Workflows Engineer"],
    )


def _service(
    outline: _Outline,
    author: object | None = None,
) -> WikiEditingService:
    return WikiEditingService(
        settings=_settings(),  # type: ignore[arg-type]
        store=InMemoryWikiEditingStore(),
        outline_client_factory=lambda: outline,  # type: ignore[arg-type]
        authoring_runner=author,  # type: ignore[arg-type]
    )


def _create_request(
    *, target_document_id: str | None = "doc-1"
) -> WikiEditCreateRequest:
    return WikiEditCreateRequest(
        context=_context(),
        instruction="Document the release decision we just made.",
        target_document_id=target_document_id,
        request_idempotency_key="interaction-1",
    )


def _action(proposal_id: str) -> WikiEditActionRequest:
    return WikiEditActionRequest(context=_context(), proposal_id=proposal_id)


def _acknowledge(service: WikiEditingService, proposal_id: str) -> None:
    review = service.status(_action(proposal_id)).review
    assert review is not None
    acknowledged = service.acknowledge_review(
        WikiEditReviewAcknowledgementRequest(
            context=_context(),
            proposal_id=proposal_id,
            review_id=review.review_id,
        )
    )
    assert acknowledged.review_acknowledged is True


def _propose(service: WikiEditingService) -> str:
    started = service.create(_create_request())
    assert started.response.proposal_id is not None
    drafted = service.author_proposal(
        started.response.proposal_id,
        organization_id="guild-1",
    )
    assert drafted.status == "proposed"
    return started.response.proposal_id


def test_authoring_creates_reviewable_diff_and_idempotent_start() -> None:
    outline = _Outline()
    author = _Author()
    service = _service(outline, author)

    first = service.create(_create_request())
    second = service.create(_create_request())

    assert first.should_enqueue is True
    assert second.should_enqueue is True
    assert second.response.proposal_id == first.response.proposal_id
    assert first.response.proposal_id is not None
    drafted = service.author_proposal(
        first.response.proposal_id, organization_id="guild-1"
    )

    assert drafted.status == "proposed"
    # Worker authoring results stay redacted: the complete packet is only
    # available through the owner-scoped status read used by Discord.
    assert drafted.diff is None
    assert drafted.review is None
    reviewed = service.status(_action(first.response.proposal_id))
    assert reviewed.review is not None
    assert "approved release checklist" in reviewed.review.complete_diff
    assert "Deploy with the approved" in reviewed.review.proposed_article
    assert drafted.source_count == 0
    assert len(author.work_items) == 1


def test_failed_queue_handoff_becomes_a_revisionable_proposal() -> None:
    outline = _Outline()
    service = _service(outline, _Author())
    started = service.create(_create_request())
    assert started.response.proposal_id is not None

    failed = service.mark_authoring_enqueue_failed(
        _action(started.response.proposal_id)
    )

    assert failed.status == "failed"
    assert failed.action == "revise"


def test_transient_authoring_releases_the_lease_for_queue_retry() -> None:
    outline = _Outline()
    service = _service(outline, _TransientAuthor())
    started = service.create(_create_request())
    assert started.response.proposal_id is not None

    with pytest.raises(WikiAuthoringTransientError, match="sandbox is starting"):
        service.author_proposal(
            started.response.proposal_id,
            organization_id="guild-1",
        )

    released = service.store.get_proposal(
        started.response.proposal_id,
        organization_id="guild-1",
    )
    assert released is not None
    assert released.status == "queued"
    assert released.authoring_started_at is None
    assert released.omp_metadata is not None

    exhausted = service.mark_authoring_retry_exhausted(
        started.response.proposal_id,
        organization_id="guild-1",
    )
    assert exhausted.status == "failed"
    assert exhausted.action == "revise"


def test_revision_preserves_immutable_history_and_passes_feedback_to_author() -> None:
    outline = _Outline()
    author = _Author()
    service = _service(outline, author)
    proposal_id = _propose(service)

    revised = service.revise(
        WikiEditRevisionRequest(
            context=_context(),
            proposal_id=proposal_id,
            instruction="Make the wording more explicit about approvals.",
        )
    )
    assert revised.response.proposal_id is not None
    assert revised.response.revision == 2
    second = service.author_proposal(
        revised.response.proposal_id,
        organization_id="guild-1",
    )

    assert second.status == "proposed"
    reviewed = service.status(_action(revised.response.proposal_id))
    assert reviewed.review is not None
    assert "more explicit" in reviewed.review.complete_diff
    latest_work = author.work_items[-1]
    assert getattr(getattr(latest_work, "proposal"), "revision_instruction") == (
        "Make the wording more explicit about approvals."
    )


def test_publish_uses_one_confirmed_write_and_never_repeats_it() -> None:
    outline = _Outline()
    service = _service(outline, _Author())
    proposal_id = _propose(service)

    with pytest.raises(WikiEditingValidationError, match="acknowledge"):
        service.publish(_action(proposal_id))
    assert outline.update_calls == 0

    _acknowledge(service, proposal_id)
    published = service.publish(_action(proposal_id))
    repeated = service.publish(_action(proposal_id))

    assert published.status == "published"
    assert repeated.status == "published"
    assert outline.update_calls == 1
    assert published.document_url == "https://outline.example/doc/deployment"


def test_publish_marks_a_changed_document_conflicted_without_writing() -> None:
    outline = _Outline()
    service = _service(outline, _Author())
    proposal_id = _propose(service)
    _acknowledge(service, proposal_id)
    outline.document = replace(
        outline.document,
        text="Someone else changed the release guide.",
        revision=2,
    )

    response = service.publish(_action(proposal_id))

    assert response.status == "conflict"
    assert outline.update_calls == 0


def test_ambiguous_publish_failure_is_not_retried() -> None:
    outline = _Outline()
    outline.raise_on_update = TimeoutError("provider timeout")
    service = _service(outline, _Author())
    proposal_id = _propose(service)
    _acknowledge(service, proposal_id)

    with pytest.raises(WikiEditingValidationError, match="result is unknown"):
        service.publish(_action(proposal_id))

    status = service.status(_action(proposal_id))
    repeated = service.publish(_action(proposal_id))
    assert status.status == "publish_unknown"
    assert repeated.status == "publish_unknown"
    assert outline.update_calls == 1


def test_known_outline_conflict_after_claim_stays_reviewable() -> None:
    outline = _Outline()
    outline.raise_on_update = OutlineConflictError("stale revision")
    service = _service(outline, _Author())
    proposal_id = _propose(service)
    _acknowledge(service, proposal_id)

    response = service.publish(_action(proposal_id))

    assert response.status == "conflict"
    assert response.operation_status == "conflict"
    assert outline.update_calls == 1


def test_create_conflict_remains_unknown_and_is_never_retried() -> None:
    outline = _Outline()
    outline.raise_on_create = OutlineConflictError("collection conflict")
    service = _service(outline, _Author())
    started = service.create(_create_request(target_document_id=None))
    assert started.response.proposal_id is not None
    drafted = service.author_proposal(
        started.response.proposal_id,
        organization_id="guild-1",
    )
    assert drafted.status == "proposed"
    _acknowledge(service, started.response.proposal_id)

    with pytest.raises(WikiEditingValidationError, match="will not be retried"):
        service.publish(_action(started.response.proposal_id))

    repeated = service.publish(_action(started.response.proposal_id))
    assert repeated.status == "publish_unknown"
    assert outline.create_calls == 1


def test_update_rejects_an_article_outside_the_shared_collection() -> None:
    outline = _Outline()
    outline.document = replace(outline.document, collection_id="private-collection")
    service = _service(outline, _Author())

    with pytest.raises(WikiEditingValidationError, match="shared wiki collection"):
        service.create(_create_request())

    assert outline.update_calls == 0


def test_update_requires_an_outline_revision_for_server_side_conflict_checks() -> None:
    outline = _Outline()
    outline.document = replace(outline.document, revision=None)
    service = _service(outline, _Author())

    with pytest.raises(WikiEditingValidationError, match="usable Outline revision"):
        service.create(_create_request())

    assert outline.update_calls == 0


def test_revision_feedback_obeys_the_same_bounded_instruction_limit() -> None:
    outline = _Outline()
    service = _service(outline, _Author())
    proposal_id = _propose(service)

    with pytest.raises(WikiEditingValidationError, match="4000 characters"):
        service.revise(
            WikiEditRevisionRequest(
                context=_context(),
                proposal_id=proposal_id,
                instruction="x" * 4_001,
            )
        )


def test_review_acknowledgement_is_owner_scoped_and_binds_the_complete_packet() -> None:
    outline = _Outline()
    service = _service(outline, _Author())
    proposal_id = _propose(service)
    review = service.status(_action(proposal_id)).review
    assert review is not None

    with pytest.raises(WikiEditingValidationError, match="changed or was not rendered"):
        service.acknowledge_review(
            WikiEditReviewAcknowledgementRequest(
                context=_context(),
                proposal_id=proposal_id,
                review_id="0" * 16,
            )
        )
    with pytest.raises(WikiEditPermissionError, match="belongs to another requester"):
        service.acknowledge_review(
            WikiEditReviewAcknowledgementRequest(
                context=_context(user_id="another-writer"),
                proposal_id=proposal_id,
                review_id=review.review_id,
            )
        )

    _acknowledge(service, proposal_id)
    published = service.publish(_action(proposal_id))
    assert published.status == "published"


def test_only_privileged_roles_receive_wiki_scopes() -> None:
    from five08.agent.policy import PolicyEngine

    policy = PolicyEngine()
    assert "wiki:publish" not in policy.scopes_for_context(
        _context(user_id="member").model_copy(update={"roles": ["Member"]})
    )
    assert "wiki:propose" in policy.scopes_for_context(_context())
    assert "wiki:publish" in policy.scopes_for_context(
        _context().model_copy(update={"roles": ["Steering Committee"]})
    )
