"""Backend-owned orchestration for approval-gated wiki editing.

The authoring harness may read narrowly scoped sources and propose Markdown, but
this service owns all durable state, authorization, conflict detection, and
Outline writes. In particular, an Outline write is never queued or retried as
an ordinary background job.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from five08.agent.models import AgentIdentityContext
from five08.agent.policy import PolicyEngine
from five08.clients.outline import (
    OutlineClient,
    OutlineConflictError,
    OutlineDocument,
    OutlineNoWriteError,
)
from five08.settings import SharedSettings
from five08.wiki_editing.models import (
    WikiBaseDocumentSnapshot,
    WikiConflictDetails,
    WikiEditActionRequest,
    WikiEditCreateRequest,
    WikiEditNotFoundError,
    WikiEditPermissionError,
    WikiEditProposal,
    WikiEditReviewAcknowledgementRequest,
    WikiEditReviewArtifact,
    WikiEditResponse,
    WikiEditRevisionRequest,
    WikiEditStateError,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiProposalOutput,
    WikiPublishOperation,
    WikiPublishResult,
    wiki_content_hash,
)
from five08.wiki_editing.omp import (
    WikiAuthoringError,
    WikiAuthoringRunner,
    WikiAuthoringTransientError,
)
from five08.wiki_editing.store import WikiEditingStore


class WikiEditingConfigurationError(RuntimeError):
    """Wiki editing is disabled or lacks a required backend-only setting."""


class WikiEditingValidationError(ValueError):
    """A request violates a deterministic safety or workflow constraint."""


@dataclass(frozen=True, slots=True)
class WikiProposalStart:
    """Result of creating/resuming a queued proposal for the API queue layer."""

    response: WikiEditResponse
    should_enqueue: bool


def build_outline_writer_client(settings: SharedSettings) -> OutlineClient:
    """Create the backend-only Outline client used for drafts and publishing."""
    api_key = str(getattr(settings, "outline_admin_api_key", "") or "").strip()
    if not api_key:
        raise WikiEditingConfigurationError(
            "Wiki editing requires OUTLINE_ADMIN_API_KEY in the backend and worker."
        )
    return OutlineClient(
        api_key=api_key,
        base_url=str(getattr(settings, "outline_base_url", "") or "").strip(),
        timeout_seconds=max(
            1.0,
            float(getattr(settings, "outline_api_timeout_seconds", 20.0)),
        ),
    )


class WikiEditingService:
    """Coordinate typed proposals without granting the authoring model writes."""

    def __init__(
        self,
        *,
        settings: SharedSettings,
        store: WikiEditingStore,
        outline_client_factory: Callable[[], OutlineClient] | None = None,
        policy: PolicyEngine | None = None,
        authoring_runner: WikiAuthoringRunner | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.outline_client_factory = outline_client_factory or (
            lambda: build_outline_writer_client(settings)
        )
        self.policy = policy or PolicyEngine()
        self.authoring_runner = authoring_runner

    def create(self, request: WikiEditCreateRequest) -> WikiProposalStart:
        """Persist an explicit request and reserve its first draft revision."""
        organization_id = self._authorize(request.context, scope="wiki:propose")
        self._assert_wiki_editing_configured()
        self._validate_create_request(request, organization_id=organization_id)
        base_snapshot = self._snapshot_for_target(request.target_document_id)
        request_input = request.to_request_input()
        proposal, should_enqueue = self.store.create_or_get_initial_proposal(
            request_input,
            WikiProposalCreate(
                request_id=request_input.id,
                organization_id=organization_id,
                target_action=("update" if base_snapshot is not None else "create"),
                target_document_id=request.target_document_id,
                base_document=base_snapshot,
            ),
        )
        return WikiProposalStart(
            response=self._response_for(proposal),
            should_enqueue=should_enqueue,
        )

    def revise(self, request: WikiEditRevisionRequest) -> WikiProposalStart:
        """Reserve a fresh immutable proposal revision with explicit feedback."""
        organization_id = self._authorize(request.context, scope="wiki:propose")
        self._assert_wiki_editing_configured()
        self._validate_instruction(request.instruction)
        proposal = self._owned_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        if proposal.status == "canceled":
            # A process can die after atomically reserving this child but
            # before handing it to Redis. Retrying the original Discord card
            # must redispatch that queued replacement rather than strand it or
            # manufacture a second revision.
            replacement = self.store.get_revision_child(
                proposal.id,
                organization_id=organization_id,
            )
            if replacement is not None:
                return WikiProposalStart(
                    response=self._response_for(
                        replacement,
                        message=(
                            "The replacement draft was already reserved; "
                            "refresh its status for the latest progress."
                        ),
                    ),
                    should_enqueue=replacement.status == "queued",
                )
        if proposal.status not in {"proposed", "conflict", "failed"}:
            raise WikiEditingValidationError(
                "This draft cannot be revised in its current state."
            )
        base_snapshot = self._snapshot_for_target(proposal.target_document_id)
        revised = self.store.create_revision(
            WikiProposalCreate(
                request_id=proposal.request_id,
                organization_id=organization_id,
                target_action=proposal.target_action,
                target_document_id=proposal.target_document_id,
                base_document=base_snapshot,
                revision_parent_id=proposal.id,
                revision_instruction=request.instruction,
            )
        )
        return WikiProposalStart(
            response=self._response_for(revised),
            should_enqueue=True,
        )

    def status(self, request: WikiEditActionRequest) -> WikiEditResponse:
        """Return an actor-scoped proposal view and its complete review packet."""
        organization_id = self._authorize(request.context, scope="wiki:propose")
        proposal = self._owned_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        return self._response_for(
            proposal,
            operation=self.store.get_publish_operation(
                proposal.id,
                organization_id=organization_id,
            ),
            include_review=True,
        )

    def acknowledge_review(
        self,
        request: WikiEditReviewAcknowledgementRequest,
    ) -> WikiEditResponse:
        """Record the requester's explicit acknowledgement of a rendered packet."""
        organization_id = self._authorize(request.context, scope="wiki:publish")
        proposal = self._owned_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        if proposal.status != "proposed":
            raise WikiEditingValidationError(
                "This wiki review cannot be acknowledged in its current state."
            )
        try:
            review = WikiEditReviewArtifact.from_proposal(proposal)
        except (ValueError, WikiEditStateError) as exc:
            raise WikiEditingValidationError(
                "The complete wiki review packet is unavailable; request a revision."
            ) from exc
        if review.review_id != request.review_id:
            raise WikiEditingValidationError(
                "The review packet changed or was not rendered. Refresh it before acknowledging."
            )
        acknowledged = self.store.acknowledge_review(
            proposal.id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
            review_content_hash=review.content_hash,
        )
        return self._response_for(
            acknowledged,
            message="Review acknowledged. You may now publish, revise, or cancel it.",
        )

    def cancel(self, request: WikiEditActionRequest) -> WikiEditResponse:
        """Cancel a requester-owned proposal before its external write begins."""
        organization_id = self._authorize(request.context, scope="wiki:propose")
        proposal = self.store.cancel_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        return self._response_for(proposal, message="Wiki draft canceled.")

    def mark_authoring_enqueue_failed(
        self,
        request: WikiEditActionRequest,
    ) -> WikiEditResponse:
        """Expose a failed queue handoff instead of leaving a draft orphaned.

        This is only called by the trusted API immediately after it cannot
        deliver the authoring job. If delivery actually succeeded despite the
        error and the worker already claimed the lease, preserve that active
        state instead of racing it into a failure.
        """
        organization_id = self._authorize(request.context, scope="wiki:propose")
        proposal = self._owned_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        failed = self.store.fail_proposal_if_status(
            proposal.id,
            organization_id=organization_id,
            failure_code="authoring_enqueue_failed",
            expected_statuses=frozenset({"queued"}),
        )
        if failed is None:
            latest = self._owned_proposal(
                proposal.id,
                organization_id=organization_id,
                actor_id=request.context.discord_user_id,
            )
            return self._response_for(latest)
        return self._response_for(
            failed,
            message="The draft could not be queued. Request a revision to try again.",
        )

    def publish(self, request: WikiEditActionRequest) -> WikiEditResponse:
        """Make at most one confirmed Outline write, never an automatic retry."""
        organization_id = self._authorize(request.context, scope="wiki:publish")
        self._assert_wiki_editing_configured()
        proposal = self._owned_proposal(
            request.proposal_id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        if proposal.status != "proposed":
            operation = self.store.get_publish_operation(
                proposal.id,
                organization_id=organization_id,
            )
            return self._response_for(proposal, operation=operation)
        if not proposal.review_acknowledged:
            raise WikiEditingValidationError(
                "Review the complete wiki packet and acknowledge it before publishing."
            )
        self._require_current_review_acknowledgement(proposal)

        if proposal.target_action == "update":
            conflict = self._current_conflict(proposal)
            if conflict is not None:
                updated = self.store.mark_conflict(
                    proposal.id,
                    organization_id=organization_id,
                    conflict=conflict,
                )
                return self._response_for(updated)

        # This durable state transition happens immediately before the only
        # provider call. A duplicate click can observe it but can never issue a
        # second create/update request.
        claim = self.store.claim_publish_attempt(
            proposal.id,
            organization_id=organization_id,
        )
        if not claim.should_execute:
            latest = self._owned_proposal(
                proposal.id,
                organization_id=organization_id,
                actor_id=request.context.discord_user_id,
            )
            return self._response_for(latest, operation=claim.operation)

        try:
            published = self._write_confirmed_proposal(proposal)
        except OutlineNoWriteError:
            # A 401/403 is a definitive pre-write rejection. Keep the one-shot
            # operation as an audit record, but make the reviewed proposal
            # revisionable instead of treating a credential error as ambiguous.
            try:
                operation = self.store.mark_publish_rejected(
                    proposal.id,
                    organization_id=organization_id,
                    failure_code="outline_no_write_rejected",
                )
                latest = self._owned_proposal(
                    proposal.id,
                    organization_id=organization_id,
                    actor_id=request.context.discord_user_id,
                )
            except Exception as persistence_error:
                self._mark_publish_unknown_safely(proposal.id, organization_id)
                raise WikiEditingValidationError(
                    "Outline rejected the publish before writing, but its final state needs reconciliation."
                ) from persistence_error
            return self._response_for(
                latest,
                operation=operation,
                message=(
                    "Outline rejected the publish before writing. Resolve the writer "
                    "credentials, then request a revision to retry."
                ),
            )
        except OutlineConflictError as exc:
            # A 409 is an explicit no-write response. It is safe to turn into a
            # reviewable conflict rather than treating it as an ambiguous retry.
            if proposal.target_action != "update":
                # A create has no stable target to re-fetch and compare. Keep
                # the one-shot write barrier intact rather than guessing a
                # conflicting page or allowing a second creation attempt.
                self._mark_publish_unknown_safely(proposal.id, organization_id)
                raise WikiEditingValidationError(
                    "Outline rejected the new article; the write will not be retried automatically."
                ) from exc
            conflict = self._conflict_from_latest(proposal)
            try:
                updated = self.store.mark_conflict(
                    proposal.id,
                    organization_id=organization_id,
                    conflict=conflict,
                )
            except Exception as exc:
                self._mark_publish_unknown_safely(proposal.id, organization_id)
                raise WikiEditingValidationError(
                    "Outline rejected the stale update; its final state needs reconciliation."
                ) from exc
            return self._response_for(
                updated,
                operation=self.store.get_publish_operation(
                    proposal.id,
                    organization_id=organization_id,
                ),
            )
        except Exception as exc:
            # Timeouts, disconnects, and invalid provider replies might hide a
            # successful write. Preserve that ambiguity and never call Outline
            # again from this workflow.
            self._mark_publish_unknown_safely(proposal.id, organization_id)
            raise WikiEditingValidationError(
                "The Outline publish result is unknown and will not be retried automatically."
            ) from exc

        try:
            # Result validation occurs after the provider write. An invalid
            # success payload is therefore ambiguous just like a disconnect:
            # preserve the one-shot barrier and require reconciliation.
            result = self._publish_result(proposal, published)
            operation = self.store.mark_publish_succeeded(
                proposal.id,
                organization_id=organization_id,
                result=result,
            )
        except Exception as exc:
            self._mark_publish_unknown_safely(proposal.id, organization_id)
            raise WikiEditingValidationError(
                "The Outline write may have succeeded but requires reconciliation."
            ) from exc
        latest = self._owned_proposal(
            proposal.id,
            organization_id=organization_id,
            actor_id=request.context.discord_user_id,
        )
        return self._response_for(
            latest,
            operation=operation,
            message="Published the approved wiki update.",
        )

    def author_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
    ) -> WikiEditResponse:
        """Run the safe-to-retry OMP authoring phase for one reserved proposal."""
        self._assert_authoring_configured()
        if self.authoring_runner is None:
            raise WikiEditingConfigurationError(
                "The OMP authoring worker is unavailable."
            )
        proposal = self.store.get_proposal(proposal_id, organization_id=organization_id)
        if proposal is None:
            raise WikiEditNotFoundError("Wiki proposal was not found.")
        metadata = proposal.omp_metadata or WikiOmpRunMetadata(
            session_id=f"no-session:{proposal.id}",
            model=str(getattr(self.settings, "wiki_omp_model", "omp")),
            run_id=str(uuid4()),
            provider="openrouter",
        )
        work_item = self.store.claim_authoring(
            proposal.id,
            organization_id=organization_id,
            omp_metadata=metadata,
            authoring_lease_seconds=(
                float(
                    getattr(
                        self.settings,
                        "wiki_omp_authoring_timeout_seconds",
                        300.0,
                    )
                )
                + float(
                    getattr(
                        self.settings,
                        "wiki_omp_startup_timeout_seconds",
                        30.0,
                    )
                )
                + 60.0
            ),
        )
        if work_item is None:
            latest = self.store.get_proposal(
                proposal.id, organization_id=organization_id
            )
            if latest is None:  # pragma: no cover - state-store invariant
                raise WikiEditNotFoundError("Wiki proposal was not found.")
            return self._response_for(latest)

        try:
            draft = self.authoring_runner.author(work_item, metadata=metadata)
            self._validate_draft_size(draft.title, draft.text)
            output = WikiProposalOutput(
                proposed_title=draft.title,
                proposed_text=draft.text,
                proposed_diff=self._proposal_diff(work_item, draft.title, draft.text),
                summary=draft.summary,
                source_refs=list(draft.source_refs),
            )
            # The review UI never falls back to a truncated preview. Reject an
            # oversized or malformed packet before this immutable revision can
            # become publishable.
            WikiEditReviewArtifact.from_output(
                proposed_title=output.proposed_title,
                proposed_article=output.proposed_text,
                complete_diff=output.proposed_diff,
                source_refs=output.source_refs,
            )
            completed = self.store.complete_proposal(
                proposal.id,
                organization_id=organization_id,
                output=output,
            )
        except WikiAuthoringTransientError:
            # The OMP draft phase has no write capability, so a transport or
            # capacity retry is safe. Release the durable claim before raising
            # so the queue retry can actually acquire it; never apply this to
            # malformed model output, which remains a reviewable failure.
            try:
                self.store.release_authoring(
                    proposal.id,
                    organization_id=organization_id,
                )
            except WikiEditStateError:
                # A requester may cancel while the sidecar is unavailable. Do
                # not revive that terminal result merely to retry authoring.
                latest = self.store.get_proposal(
                    proposal.id,
                    organization_id=organization_id,
                )
                if latest is None:  # pragma: no cover - state-store invariant
                    raise WikiEditNotFoundError("Wiki proposal was not found.")
                return self._response_for(latest)
            raise
        except WikiAuthoringError:
            return self._fail_authoring(proposal.id, organization_id)
        except WikiEditStateError:
            # Cancellation may win while OMP is writing its final draft. Do not
            # resurrect the proposal or change the cancellation outcome.
            latest = self.store.get_proposal(
                proposal.id, organization_id=organization_id
            )
            if latest is None:  # pragma: no cover - state-store invariant
                raise
            return self._response_for(latest)
        except Exception:
            return self._fail_authoring(proposal.id, organization_id)
        return self._response_for(completed)

    def mark_authoring_retry_exhausted(
        self,
        proposal_id: str,
        *,
        organization_id: str,
    ) -> WikiEditResponse:
        """Expose exhausted transient retries as a revisable proposal failure.

        The generic worker queue owns retry accounting. This narrow worker-only
        hook prevents a proposal from remaining queued forever when that queue
        has exhausted its configured retry budget.
        """
        failed = self.store.fail_proposal_if_status(
            proposal_id,
            organization_id=organization_id,
            failure_code="authoring_retry_exhausted",
            expected_statuses=frozenset({"queued", "authoring"}),
        )
        if failed is None:
            latest = self.store.get_proposal(
                proposal_id,
                organization_id=organization_id,
            )
            if latest is None:
                raise WikiEditNotFoundError("Wiki proposal was not found.")
            return self._response_for(latest)
        return self._response_for(
            failed,
            message="Wiki authoring was unavailable after its retry budget. Request a revision to try again.",
        )

    def _assert_wiki_editing_configured(self) -> None:
        """Validate only configuration needed by API-owned workflow actions.

        The API creates proposals, checks conflicts, and publishes approved
        changes, but it must not receive the worker/sandbox RPC credential.
        Requiring the worker-only sandbox settings here made a correctly
        isolated API fail closed simply because it could not see that secret.
        """
        if not bool(getattr(self.settings, "wiki_editing_enabled", False)):
            raise WikiEditingConfigurationError("Wiki editing is disabled.")
        if not str(
            getattr(self.settings, "wiki_outline_collection_id", "") or ""
        ).strip():
            raise WikiEditingConfigurationError(
                "WIKI_OUTLINE_COLLECTION_ID is required for wiki editing."
            )
        # Validate the admin credential now rather than allowing an authoring
        # request to progress with the member-safe read-only credential.
        build_outline_writer_client(self.settings)

    def _assert_authoring_configured(self) -> None:
        """Validate worker-only sandbox configuration before an OMP run."""
        self._assert_wiki_editing_configured()
        configured = getattr(self.settings, "wiki_authoring_configured", None)
        if configured is not True:
            raise WikiEditingConfigurationError(
                "Wiki authoring is not fully configured in the worker."
            )

    def _authorize(self, context: AgentIdentityContext, *, scope: str) -> str:
        organization_id = (context.organization_id or "").strip()
        guild_id = (context.guild_id or "").strip()
        configured_guild_id = str(
            getattr(self.settings, "discord_server_id", "") or ""
        ).strip()
        if (
            not configured_guild_id
            or organization_id != configured_guild_id
            or guild_id != configured_guild_id
            or context.impersonation
        ):
            raise WikiEditPermissionError(
                "Wiki editing is limited to the configured co-op Discord server."
            )
        if scope not in self.policy.scopes_for_context(context):
            raise WikiEditPermissionError(
                "Your current Discord roles do not allow this wiki action."
            )
        return organization_id

    def _validate_create_request(
        self,
        request: WikiEditCreateRequest,
        *,
        organization_id: str,
    ) -> None:
        self._validate_instruction(request.instruction)
        total_source_characters = 0
        for source in request.selected_conversation:
            provenance = source.provenance
            if provenance.guild_id != organization_id:
                raise WikiEditingValidationError(
                    "Selected conversation must come from the configured co-op server."
                )
            total_source_characters += len(source.organization_visible_text)
        # The OMP adapter admits 48k material characters. At most 4k are
        # reserved for the explicit instruction, 16k for an update target,
        # and another 16k for a reviewed predecessor revision, leaving 12k
        # for a selected public conversation.
        max_source_characters = min(
            12_000,
            int(getattr(self.settings, "knowledge_capture_max_characters", 12_000)),
        )
        if total_source_characters > max_source_characters:
            raise WikiEditingValidationError(
                "Selected conversation is too large for a bounded wiki authoring run."
            )

    def _validate_instruction(self, instruction: str) -> None:
        max_instruction = int(
            getattr(self.settings, "wiki_editing_max_instruction_characters", 4_000)
        )
        if len(instruction) > max_instruction:
            raise WikiEditingValidationError(
                f"Wiki update instructions must be {max_instruction} characters or fewer."
            )

    def _snapshot_for_target(
        self,
        target_document_id: str | None,
    ) -> WikiBaseDocumentSnapshot | None:
        if target_document_id is None:
            return None
        document = self.outline_client_factory().get_document(
            document_id=target_document_id
        )
        self._validate_document_collection(document)
        self._require_update_revision(document)
        self._validate_document_size(document)
        return WikiBaseDocumentSnapshot(
            document_id=document.id,
            title=document.title,
            document_url=document.url,
            document_version=_document_version(document),
            content_hash=wiki_content_hash(document.text),
            content=document.text,
            fetched_at=datetime.now(timezone.utc),
        )

    def _validate_document_size(self, document: OutlineDocument) -> None:
        maximum = int(
            getattr(self.settings, "wiki_editing_max_document_characters", 16_000)
        )
        if len(document.text) > maximum:
            raise WikiEditingValidationError(
                "The target article is too large for the configured bounded authoring workflow."
            )

    def _validate_document_collection(self, document: OutlineDocument) -> None:
        expected_collection_id = str(
            getattr(self.settings, "wiki_outline_collection_id", "") or ""
        ).strip()
        if (document.collection_id or "").strip() != expected_collection_id:
            raise WikiEditingValidationError(
                "The target article is outside the configured shared wiki collection."
            )

    @staticmethod
    def _require_update_revision(document: OutlineDocument) -> None:
        if document.revision is None or document.revision < 0:
            raise WikiEditingValidationError(
                "The target article has no usable Outline revision for a safe update."
            )

    def _owned_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str,
    ) -> WikiEditProposal:
        proposal = self.store.get_proposal(proposal_id, organization_id=organization_id)
        if proposal is None:
            raise WikiEditNotFoundError("Wiki proposal was not found.")
        if proposal.actor_id != actor_id:
            raise WikiEditPermissionError("Wiki proposal belongs to another requester.")
        return proposal

    def _current_conflict(
        self,
        proposal: WikiEditProposal,
    ) -> WikiConflictDetails | None:
        base = proposal.base_document
        if proposal.target_action != "update" or base is None:
            return None
        current = self.outline_client_factory().get_document(
            document_id=base.document_id
        )
        if (current.collection_id or "").strip() != str(
            getattr(self.settings, "wiki_outline_collection_id", "") or ""
        ).strip():
            return WikiConflictDetails(
                current_document_id=current.id,
                current_content_hash=wiki_content_hash(current.text),
                current_document_version=_document_version(current),
                message="The target article is no longer in the configured shared wiki collection.",
            )
        current_hash = wiki_content_hash(current.text)
        current_version = _document_version(current)
        if (
            current_hash == base.content_hash
            and current_version == base.document_version
        ):
            return None
        return WikiConflictDetails(
            current_document_id=current.id,
            current_content_hash=current_hash,
            current_document_version=current_version,
            message="The Outline article changed after this draft was prepared.",
        )

    def _conflict_from_latest(self, proposal: WikiEditProposal) -> WikiConflictDetails:
        base = proposal.base_document
        if base is None:  # pragma: no cover - update proposal invariant
            raise WikiEditingValidationError("Update proposal has no base document.")
        try:
            current = self.outline_client_factory().get_document(
                document_id=base.document_id
            )
        except Exception:
            return WikiConflictDetails(
                current_document_id=base.document_id,
                current_content_hash=base.content_hash,
                current_document_version=base.document_version,
                message="Outline rejected this update because the article is no longer current.",
            )
        return WikiConflictDetails(
            current_document_id=current.id,
            current_content_hash=wiki_content_hash(current.text),
            current_document_version=_document_version(current),
            message="Outline rejected this update because the article changed.",
        )

    def _write_confirmed_proposal(self, proposal: WikiEditProposal) -> OutlineDocument:
        if proposal.proposed_title is None or proposal.proposed_text is None:
            raise WikiEditingValidationError(
                "The selected proposal has no draft content."
            )
        client = self.outline_client_factory()
        if proposal.target_action == "create":
            collection_id = str(
                getattr(self.settings, "wiki_outline_collection_id", "") or ""
            ).strip()
            if not collection_id:
                raise WikiEditingConfigurationError(
                    "WIKI_OUTLINE_COLLECTION_ID is required to create a wiki article."
                )
            return client.create_document(
                title=proposal.proposed_title,
                text=proposal.proposed_text,
                collection_id=collection_id,
                publish=True,
            )
        base = proposal.base_document
        if base is None or proposal.target_document_id is None:
            raise WikiEditingValidationError(
                "Update proposal is missing its target snapshot."
            )
        expected_revision = _revision_number(base.document_version)
        if expected_revision is None:
            raise WikiEditingValidationError(
                "The target article has no usable Outline revision for a safe update."
            )
        return client.update_document(
            document_id=proposal.target_document_id,
            title=proposal.proposed_title,
            text=proposal.proposed_text,
            publish=True,
            expected_revision=expected_revision,
        )

    @staticmethod
    def _require_current_review_acknowledgement(proposal: WikiEditProposal) -> None:
        """Verify the durable acknowledgement still binds the immutable output."""
        try:
            review = WikiEditReviewArtifact.from_proposal(proposal)
        except (ValueError, WikiEditStateError) as exc:
            raise WikiEditingValidationError(
                "The complete wiki review packet is unavailable; request a revision."
            ) from exc
        if proposal.review_acknowledged_content_hash != review.content_hash:
            raise WikiEditingValidationError(
                "The acknowledged review does not match this immutable draft."
            )

    @staticmethod
    def _publish_result(
        proposal: WikiEditProposal,
        document: OutlineDocument,
    ) -> WikiPublishResult:
        if (
            proposal.target_action == "update"
            and document.id != proposal.target_document_id
        ):
            raise WikiEditingValidationError(
                "Outline update response does not match the requested document."
            )
        return WikiPublishResult(
            document_id=document.id,
            document_url=document.url,
            document_version=_document_version(document),
            content_hash=wiki_content_hash(document.text),
        )

    def _mark_publish_unknown_safely(
        self,
        proposal_id: str,
        organization_id: str,
    ) -> None:
        try:
            self.store.mark_publish_unknown(
                proposal_id,
                organization_id=organization_id,
            )
        except Exception:
            # The durable write-start record remains the safety barrier even if
            # a subsequent state update is unavailable.
            return

    def _fail_authoring(
        self,
        proposal_id: str,
        organization_id: str,
    ) -> WikiEditResponse:
        latest = self.store.get_proposal(proposal_id, organization_id=organization_id)
        if latest is None:  # pragma: no cover - state-store invariant
            raise WikiEditNotFoundError("Wiki proposal was not found.")
        if latest.status == "authoring":
            latest = self.store.fail_proposal(
                proposal_id,
                organization_id=organization_id,
                failure_code="authoring_failed",
            )
        return self._response_for(latest)

    def _validate_draft_size(self, title: str, text: str) -> None:
        if not title.strip() or not text.strip():
            raise WikiAuthoringError("OMP submitted an empty wiki draft.")
        maximum = int(
            getattr(self.settings, "wiki_editing_max_document_characters", 16_000)
        )
        if len(text) > maximum:
            raise WikiAuthoringError(
                "OMP submitted a draft exceeding the configured size."
            )

    @staticmethod
    def _proposal_diff(
        work_item: object,
        title: str,
        text: str,
    ) -> str:
        proposal = getattr(work_item, "proposal")
        snapshot = getattr(proposal, "base_snapshot", None)
        revision_parent = getattr(proposal, "revision_parent_draft", None)
        if snapshot is not None:
            old_name = f"{snapshot.title}.md"
            old = _document_for_diff(snapshot.title, snapshot.content)
        elif revision_parent is not None:
            old_name = f"{revision_parent.title}.md"
            old = _document_for_diff(revision_parent.title, revision_parent.text)
        else:
            old_name = "/dev/null"
            old = ""
        new_name = f"{title}.md"
        new = _document_for_diff(title, text)
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=old_name,
                tofile=new_name,
                lineterm="",
            )
        )
        if not diff:
            raise WikiAuthoringError("OMP submitted a draft with no changes to review.")
        return diff

    @staticmethod
    def _response_for(
        proposal: WikiEditProposal,
        *,
        operation: WikiPublishOperation | None = None,
        message: str | None = None,
        include_review: bool = False,
    ) -> WikiEditResponse:
        status_messages: dict[
            str,
            tuple[
                str,
                Literal["none", "review", "publish", "revise", "cancel", "reconcile"],
            ],
        ] = {
            "queued": (
                "Wiki draft is queued for bounded authoring. Refresh this card shortly.",
                "review",
            ),
            "authoring": (
                "Wiki draft is being researched and prepared. Refresh this card shortly.",
                "review",
            ),
            "proposed": (
                "Open the complete private review packet, acknowledge it, then publish, revise, or cancel it.",
                "review",
            ),
            "conflict": (
                "The target article changed. Request a revision to rebase the draft.",
                "revise",
            ),
            "failed": (
                "The draft could not be prepared. Request a revision to try again.",
                "revise",
            ),
            "canceled": ("Wiki draft canceled.", "none"),
            "publishing": (
                "A publish attempt is already in progress; it will not be repeated.",
                "reconcile",
            ),
            "published": ("Published the approved wiki update.", "none"),
            "publish_unknown": (
                "The publish outcome is unknown and will not be retried automatically.",
                "reconcile",
            ),
        }
        default_message, action = status_messages[proposal.status]
        if proposal.status == "proposed" and proposal.review_acknowledged:
            default_message = (
                "Review acknowledged. You may now publish, revise, or cancel it."
            )
            action = "publish"
        review: WikiEditReviewArtifact | None = None
        if include_review and proposal.status == "proposed":
            try:
                review = WikiEditReviewArtifact.from_proposal(proposal)
            except (ValueError, WikiEditStateError):
                # A previously persisted malformed packet must not silently
                # become publishable. The acknowledgement endpoint will reject
                # it, while this response gives the owner a revision path.
                default_message = (
                    "The complete review packet is unavailable. Request a revision "
                    "before publishing."
                )
                action = "revise"
        return WikiEditResponse.from_proposal(
            proposal,
            message=message or default_message,
            action=action,
            operation=operation,
            review=review,
        )


def _document_version(document: OutlineDocument) -> str | None:
    if document.revision is not None:
        return str(document.revision)
    return document.updated_at


def _revision_number(document_version: str | None) -> int | None:
    if document_version is None:
        return None
    try:
        value = int(document_version)
    except ValueError:
        return None
    return value if value >= 0 else None


def _document_for_diff(title: str, text: str) -> str:
    """Render title + Markdown body so title-only edits have a visible diff."""
    return f"# {title}\n\n{text}"
