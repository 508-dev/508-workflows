"""Private persistence adapters for bounded, review-first wiki editing.

Only the authoring-specific methods return raw request/source or base-document
text.  Ordinary reads return the safe public record models from ``models``.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, cast
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings
from five08.wiki_editing.models import (
    WikiAuthoringWorkItem,
    WikiBaseDocumentSnapshot,
    WikiConflictDetails,
    WikiEditConflictError,
    WikiEditNotFoundError,
    WikiEditPermissionError,
    WikiEditProposal,
    WikiEditRequest,
    WikiEditRequestForAuthoring,
    WikiEditRequestInput,
    WikiEditStateError,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiProposalForAuthoring,
    WikiProposalOutput,
    WikiProposalStatus,
    WikiPublishClaim,
    WikiPublishOperation,
    WikiPublishResult,
    WikiPublishOperationStatus,
    WikiSelectedConversationSource,
    WikiSourceReference,
    WikiConversationProvenance,
    ensure_proposal_transition,
)


_DEFAULT_AUTHORING_LEASE_SECONDS = 900.0


class WikiEditingStore(Protocol):
    """Small persistence contract owned by the wiki-editing service layer."""

    def create_or_get_request(
        self, request: WikiEditRequestInput
    ) -> tuple[WikiEditRequest, bool]:
        """Create one idempotent authoring request, or return its safe record."""

    def get_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequest | None:
        """Return a safe request view scoped to one organization."""

    def get_authoring_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequestForAuthoring | None:
        """Return private request/source text to a trusted authoring runtime."""

    def create_proposal(self, proposal: WikiProposalCreate) -> WikiEditProposal:
        """Reserve the next immutable proposal revision for a request."""

    def get_proposal(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        """Return a safe proposal view scoped to one organization."""

    def get_latest_proposal_for_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        """Return the newest immutable revision for an idempotent request retry."""

    def get_authoring_work_item(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiAuthoringWorkItem | None:
        """Return private source/base text only for an active authoring proposal."""

    def claim_authoring(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        omp_metadata: WikiOmpRunMetadata,
        now: datetime | None = None,
        authoring_lease_seconds: float = _DEFAULT_AUTHORING_LEASE_SECONDS,
    ) -> WikiAuthoringWorkItem | None:
        """Claim a bounded OMP authoring lease for one proposal revision."""

    def complete_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        output: WikiProposalOutput,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        """Attach the one immutable authored result and move it to review."""

    def mark_conflict(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        conflict: WikiConflictDetails,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        """Record a safe stale-base conflict before an external write."""

    def fail_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        failure_code: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        """Mark a pre-publish failure using a sanitized code, never raw errors."""

    def cancel_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        """Cancel a proposal before publishing; actor ownership is optional to enforce."""

    def acknowledge_review(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str,
        review_content_hash: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        """Durably bind the requester to one immutable review packet."""

    def create_or_get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> tuple[WikiPublishOperation, bool]:
        """Allocate the unique external-write operation for a reviewable proposal."""

    def get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiPublishOperation | None:
        """Return the safe external-write operation state, if allocated."""

    def claim_publish_attempt(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishClaim:
        """Durably record write-start before the one permitted external call."""

    def mark_publish_succeeded(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        result: WikiPublishResult,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        """Resolve an attempted external write after a confirmed provider response."""

    def mark_publish_unknown(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        """Resolve an ambiguous external-write outcome without retrying it."""


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _validated_authoring_lease_seconds(value: float) -> float:
    """Validate the bounded time a worker may own an authoring attempt."""
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("authoring_lease_seconds must be positive") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("authoring_lease_seconds must be positive")
    return seconds


def _authoring_lease_expired(
    started_at: datetime | None,
    *,
    now: datetime,
    lease_seconds: float,
) -> bool:
    """Return whether a known authoring start is safely eligible for recovery."""
    if started_at is None:
        # A missing start time cannot prove the prior worker is no longer live.
        # Preserve the safe no-second-run behavior rather than guessing.
        return False
    return now >= _now(started_at) + timedelta(seconds=lease_seconds)


def _request_fingerprint(request: WikiEditRequestInput) -> str:
    """Fingerprint all idempotent input, including selected source hashes only."""
    payload = {
        "instruction_hash": request.instruction_hash,
        "target_document_id": request.target_document_id,
        "selected_conversation": [
            source.provenance.model_dump(mode="json")
            for source in request.selected_conversation
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_from_row(row: dict[str, Any]) -> WikiEditRequest:
    payload = row.get("selected_conversation_payload") or []
    provenance = [
        WikiConversationProvenance.model_validate(item.get("provenance") or {})
        for item in payload
        if isinstance(item, dict)
    ]
    return WikiEditRequest(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        actor_id=str(row["actor_id"]),
        target_document_id=row.get("target_document_id"),
        instruction_hash=str(row["instruction_hash"]),
        selected_conversation=provenance,
        created_at=row["created_at"],
    )


def _authoring_request_from_row(row: dict[str, Any]) -> WikiEditRequestForAuthoring:
    public = _request_from_row(row)
    payload = row.get("selected_conversation_payload") or []
    sources = [
        WikiSelectedConversationSource.from_storage_payload(item)
        for item in payload
        if isinstance(item, dict)
    ]
    return WikiEditRequestForAuthoring(
        **public.model_dump(mode="python"),
        instruction=str(row["request_text"]),
        selected_source_text=sources,
        request_idempotency_key=str(row["idempotency_key"]),
    )


def _proposal_from_row(row: dict[str, Any]) -> WikiEditProposal:
    base_payload = row.get("base_document_payload")
    base_snapshot = (
        WikiBaseDocumentSnapshot.from_storage_payload(base_payload)
        if isinstance(base_payload, dict)
        else None
    )
    source_payload = row.get("proposed_source_refs") or []
    omp_payload = row.get("omp_metadata")
    conflict_payload = row.get("conflict_payload")
    return WikiEditProposal(
        id=str(row["id"]),
        request_id=str(row["request_id"]),
        organization_id=str(row["organization_id"]),
        actor_id=str(row["actor_id"]),
        revision=int(row["revision"]),
        status=cast(WikiProposalStatus, row["status"]),
        target_action=row["target_action"],
        target_document_id=row.get("target_document_id"),
        base_document=base_snapshot.reference if base_snapshot is not None else None,
        proposed_title=row.get("proposed_title"),
        proposed_text=row.get("proposed_text"),
        proposed_diff=row.get("proposed_diff"),
        summary=row.get("summary"),
        source_refs=[
            WikiSourceReference.model_validate(item)
            for item in source_payload
            if isinstance(item, dict)
        ],
        omp_metadata=(
            WikiOmpRunMetadata.model_validate(omp_payload)
            if isinstance(omp_payload, dict)
            else None
        ),
        conflict=(
            WikiConflictDetails.model_validate(conflict_payload)
            if isinstance(conflict_payload, dict)
            else None
        ),
        failure_code=row.get("failure_code"),
        published_document_id=row.get("published_document_id"),
        document_url=row.get("document_url"),
        published_document_version=row.get("published_document_version"),
        published_content_hash=row.get("published_content_hash"),
        review_acknowledged_by=row.get("review_acknowledged_by"),
        review_acknowledged_content_hash=row.get("review_acknowledged_content_hash"),
        review_acknowledged_at=row.get("review_acknowledged_at"),
        created_at=row["created_at"],
        authoring_started_at=row.get("authoring_started_at"),
        proposed_at=row.get("proposed_at"),
        published_at=row.get("published_at"),
        updated_at=row["updated_at"],
    )


def _authoring_proposal_from_row(row: dict[str, Any]) -> WikiProposalForAuthoring:
    public = _proposal_from_row(row)
    base_payload = row.get("base_document_payload")
    snapshot = (
        WikiBaseDocumentSnapshot.from_storage_payload(base_payload)
        if isinstance(base_payload, dict)
        else None
    )
    return WikiProposalForAuthoring(
        **public.model_dump(mode="python"),
        base_snapshot=snapshot,
        revision_instruction=row.get("revision_instruction"),
    )


def _operation_from_row(row: dict[str, Any]) -> WikiPublishOperation:
    result_payload = row.get("result_payload")
    return WikiPublishOperation(
        id=str(row["id"]),
        proposal_id=str(row["proposal_id"]),
        organization_id=str(row["organization_id"]),
        target_action=row["target_action"],
        target_document_id=row.get("target_document_id"),
        status=cast(WikiPublishOperationStatus, row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        write_started_at=row.get("write_started_at"),
        resolved_at=row.get("resolved_at"),
        result=(
            WikiPublishResult.model_validate(result_payload)
            if isinstance(result_payload, dict)
            else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _make_authoring_request(
    request: WikiEditRequestInput,
) -> WikiEditRequestForAuthoring:
    return WikiEditRequestForAuthoring(
        id=request.id,
        organization_id=request.organization_id,
        actor_id=request.actor_id,
        target_document_id=request.target_document_id,
        instruction_hash=request.instruction_hash,
        selected_conversation=[
            source.provenance for source in request.selected_conversation
        ],
        created_at=request.created_at,
        instruction=request.instruction,
        selected_source_text=request.selected_conversation,
        request_idempotency_key=request.request_idempotency_key,
    )


def _public_request(request: WikiEditRequestForAuthoring) -> WikiEditRequest:
    return WikiEditRequest.model_validate(request.model_dump(mode="python"))


def _make_authoring_proposal(
    proposal: WikiProposalCreate,
    *,
    request: WikiEditRequestForAuthoring,
    revision: int,
) -> WikiProposalForAuthoring:
    now = proposal.created_at
    return WikiProposalForAuthoring(
        id=proposal.id,
        request_id=proposal.request_id,
        organization_id=proposal.organization_id,
        actor_id=request.actor_id,
        revision=revision,
        status="queued",
        target_action=proposal.target_action,
        target_document_id=proposal.target_document_id,
        base_document=(
            proposal.base_document.reference
            if proposal.base_document is not None
            else None
        ),
        base_snapshot=proposal.base_document,
        revision_instruction=proposal.revision_instruction,
        created_at=now,
        updated_at=now,
    )


def _public_proposal(proposal: WikiProposalForAuthoring) -> WikiEditProposal:
    return WikiEditProposal.model_validate(proposal.model_dump(mode="python"))


def _validate_owned_actor(proposal: WikiEditProposal, actor_id: str | None) -> None:
    if actor_id is not None and proposal.actor_id != actor_id:
        raise WikiEditPermissionError("wiki proposal is not owned by this actor")


def _validated_review_content_hash(value: str) -> str:
    """Validate the immutable review digest accepted by persistence adapters."""
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("review_content_hash must be a SHA-256 hexadecimal digest")
    return normalized


def _require_review_acknowledgement(proposal: WikiEditProposal) -> None:
    """Fail closed before allocating the one permitted external write attempt."""
    if not proposal.review_acknowledged:
        raise WikiEditStateError(
            "wiki review must be acknowledged by its requester before publishing"
        )


def _new_operation(
    proposal: WikiEditProposal, *, now: datetime
) -> WikiPublishOperation:
    return WikiPublishOperation(
        id=str(uuid4()),
        proposal_id=proposal.id,
        organization_id=proposal.organization_id,
        target_action=proposal.target_action,
        target_document_id=proposal.target_document_id,
        status="pending",
        idempotency_key=f"wiki-edit-publish:{proposal.id}",
        created_at=now,
        updated_at=now,
    )


class InMemoryWikiEditingStore:
    """Thread-safe test implementation with the same idempotency guarantees."""

    def __init__(self) -> None:
        self._requests: dict[str, WikiEditRequestForAuthoring] = {}
        self._request_idempotencies: dict[tuple[str, str], tuple[str, str]] = {}
        self._proposals: dict[str, WikiProposalForAuthoring] = {}
        self._operations: dict[str, WikiPublishOperation] = {}
        self._lock = threading.RLock()

    def create_or_get_request(
        self, request: WikiEditRequestInput
    ) -> tuple[WikiEditRequest, bool]:
        fingerprint = _request_fingerprint(request)
        key = (request.organization_id, request.request_idempotency_key)
        with self._lock:
            existing = self._request_idempotencies.get(key)
            if existing is not None:
                request_id, existing_fingerprint = existing
                if existing_fingerprint != fingerprint:
                    raise WikiEditConflictError(
                        "request idempotency key was already used with different input"
                    )
                return _public_request(self._requests[request_id]).model_copy(
                    deep=True
                ), False
            stored = _make_authoring_request(request)
            self._requests[stored.id] = stored.model_copy(deep=True)
            self._request_idempotencies[key] = (stored.id, fingerprint)
            return _public_request(stored).model_copy(deep=True), True

    def get_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequest | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.organization_id != organization_id:
                return None
            return _public_request(request).model_copy(deep=True)

    def get_authoring_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequestForAuthoring | None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is None or request.organization_id != organization_id:
                return None
            return request.model_copy(deep=True)

    def create_proposal(self, proposal: WikiProposalCreate) -> WikiEditProposal:
        with self._lock:
            request = self._requests.get(proposal.request_id)
            if request is None:
                raise WikiEditNotFoundError("wiki edit request was not found")
            if request.organization_id != proposal.organization_id:
                raise WikiEditPermissionError(
                    "wiki edit request is outside this organization"
                )
            if proposal.id in self._proposals:
                raise WikiEditConflictError("wiki proposal id already exists")
            revision = 1 + sum(
                item.request_id == proposal.request_id
                for item in self._proposals.values()
            )
            stored = _make_authoring_proposal(
                proposal,
                request=request,
                revision=revision,
            )
            self._proposals[stored.id] = stored.model_copy(deep=True)
            return _public_proposal(stored).model_copy(deep=True)

    def get_proposal(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None or proposal.organization_id != organization_id:
                return None
            return _public_proposal(proposal).model_copy(deep=True)

    def get_latest_proposal_for_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        with self._lock:
            candidates = [
                proposal
                for proposal in self._proposals.values()
                if proposal.request_id == request_id
                and proposal.organization_id == organization_id
            ]
            if not candidates:
                return None
            latest = max(candidates, key=lambda proposal: proposal.revision)
            return _public_proposal(latest).model_copy(deep=True)

    def get_authoring_work_item(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiAuthoringWorkItem | None:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if (
                proposal is None
                or proposal.organization_id != organization_id
                or proposal.status != "authoring"
            ):
                return None
            request = self._requests.get(proposal.request_id)
            if request is None:  # pragma: no cover - InMemory invariant
                raise WikiEditNotFoundError("wiki edit request was not found")
            return WikiAuthoringWorkItem(
                request=request.model_copy(deep=True),
                proposal=proposal.model_copy(deep=True),
            )

    def claim_authoring(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        omp_metadata: WikiOmpRunMetadata,
        now: datetime | None = None,
        authoring_lease_seconds: float = _DEFAULT_AUTHORING_LEASE_SECONDS,
    ) -> WikiAuthoringWorkItem | None:
        comparison_time = _now(now)
        lease_seconds = _validated_authoring_lease_seconds(authoring_lease_seconds)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            if proposal.status == "queued":
                ensure_proposal_transition(proposal.status, "authoring")
                proposal = proposal.model_copy(
                    update={
                        "status": "authoring",
                        "omp_metadata": omp_metadata,
                        "authoring_started_at": comparison_time,
                        "updated_at": comparison_time,
                    },
                    deep=True,
                )
                self._proposals[proposal_id] = proposal
            elif proposal.status == "authoring":
                if not _authoring_lease_expired(
                    proposal.authoring_started_at,
                    now=comparison_time,
                    lease_seconds=lease_seconds,
                ):
                    return None
                if proposal.omp_metadata != omp_metadata:
                    raise WikiEditConflictError(
                        "wiki proposal is already bound to another OMP run"
                    )
                proposal = proposal.model_copy(
                    update={
                        "authoring_started_at": comparison_time,
                        "updated_at": comparison_time,
                    },
                    deep=True,
                )
                self._proposals[proposal_id] = proposal
            else:
                return None
            request = self._requests.get(proposal.request_id)
            if request is None:  # pragma: no cover - InMemory invariant
                raise WikiEditNotFoundError("wiki edit request was not found")
            return WikiAuthoringWorkItem(
                request=request.model_copy(deep=True),
                proposal=proposal.model_copy(deep=True),
            )

    def complete_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        output: WikiProposalOutput,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            ensure_proposal_transition(proposal.status, "proposed")
            if proposal.proposed_at is not None:
                raise WikiEditConflictError("wiki proposal output is already immutable")
            updated = proposal.model_copy(
                update={
                    "status": "proposed",
                    "proposed_title": output.proposed_title,
                    "proposed_text": output.proposed_text,
                    "proposed_diff": output.proposed_diff,
                    "summary": output.summary,
                    "source_refs": output.source_refs,
                    "proposed_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._proposals[proposal_id] = updated
            return _public_proposal(updated).model_copy(deep=True)

    def mark_conflict(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        conflict: WikiConflictDetails,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            ensure_proposal_transition(proposal.status, "conflict")
            operation = self._operations.get(proposal_id)
            if operation is not None:
                if operation.status not in {"pending", "write_started"}:
                    raise WikiEditStateError(
                        "wiki publish operation cannot be resolved as a conflict"
                    )
                self._operations[proposal_id] = operation.model_copy(
                    update={
                        "status": "conflict",
                        "resolved_at": comparison_time,
                        "updated_at": comparison_time,
                    },
                    deep=True,
                )
            updated = proposal.model_copy(
                update={
                    "status": "conflict",
                    "conflict": conflict,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._proposals[proposal_id] = updated
            return _public_proposal(updated).model_copy(deep=True)

    def fail_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        failure_code: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        normalized_code = _failure_code(failure_code)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            ensure_proposal_transition(proposal.status, "failed")
            updated = proposal.model_copy(
                update={
                    "status": "failed",
                    "failure_code": normalized_code,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._proposals[proposal_id] = updated
            return _public_proposal(updated).model_copy(deep=True)

    def cancel_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            _validate_owned_actor(proposal, actor_id)
            ensure_proposal_transition(proposal.status, "canceled")
            updated = proposal.model_copy(
                update={"status": "canceled", "updated_at": comparison_time},
                deep=True,
            )
            self._proposals[proposal_id] = updated
            return _public_proposal(updated).model_copy(deep=True)

    def acknowledge_review(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str,
        review_content_hash: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        normalized_hash = _validated_review_content_hash(review_content_hash)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            _validate_owned_actor(proposal, actor_id)
            if proposal.review_acknowledged:
                if (
                    proposal.review_acknowledged_by != actor_id
                    or proposal.review_acknowledged_content_hash != normalized_hash
                ):
                    raise WikiEditConflictError(
                        "wiki proposal was acknowledged for a different review packet"
                    )
                return _public_proposal(proposal).model_copy(deep=True)
            if proposal.status != "proposed":
                raise WikiEditStateError(
                    "wiki proposal must be proposed before its review can be acknowledged"
                )
            acknowledged = proposal.model_copy(
                update={
                    "review_acknowledged_by": actor_id,
                    "review_acknowledged_content_hash": normalized_hash,
                    "review_acknowledged_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._proposals[proposal_id] = acknowledged
            return _public_proposal(acknowledged).model_copy(deep=True)

    def create_or_get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> tuple[WikiPublishOperation, bool]:
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            existing = self._operations.get(proposal_id)
            if existing is not None:
                return existing.model_copy(deep=True), False
            if proposal.status != "proposed":
                raise WikiEditStateError(
                    "wiki proposal must be proposed before publishing can begin"
                )
            _require_review_acknowledgement(proposal)
            operation = _new_operation(_public_proposal(proposal), now=_now())
            self._operations[proposal_id] = operation
            return operation.model_copy(deep=True), True

    def get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiPublishOperation | None:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None or proposal.organization_id != organization_id:
                return None
            operation = self._operations.get(proposal_id)
            return operation.model_copy(deep=True) if operation is not None else None

    def claim_publish_attempt(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishClaim:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            operation = self._operations.get(proposal_id)
            if operation is None:
                if proposal.status != "proposed":
                    raise WikiEditStateError(
                        "wiki proposal must be proposed before publishing can begin"
                    )
                _require_review_acknowledgement(proposal)
                operation = _new_operation(
                    _public_proposal(proposal), now=comparison_time
                )
                self._operations[proposal_id] = operation
            if operation.status != "pending":
                return WikiPublishClaim(
                    operation=operation.model_copy(deep=True), should_execute=False
                )
            ensure_proposal_transition(proposal.status, "publishing")
            started_operation = operation.model_copy(
                update={
                    "status": "write_started",
                    "write_started_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            updated_proposal = proposal.model_copy(
                update={"status": "publishing", "updated_at": comparison_time},
                deep=True,
            )
            self._operations[proposal_id] = started_operation
            self._proposals[proposal_id] = updated_proposal
            return WikiPublishClaim(
                operation=started_operation.model_copy(deep=True), should_execute=True
            )

    def mark_publish_succeeded(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        result: WikiPublishResult,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            operation = self._required_operation(proposal_id)
            if operation.status == "succeeded":
                if operation.result != result:
                    raise WikiEditConflictError(
                        "wiki publish operation already has a different result"
                    )
                return operation.model_copy(deep=True)
            if operation.status not in {"write_started", "unknown"}:
                raise WikiEditStateError("wiki publish operation was not attempted")
            ensure_proposal_transition(proposal.status, "published")
            succeeded = operation.model_copy(
                update={
                    "status": "succeeded",
                    "result": result,
                    "resolved_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            published = proposal.model_copy(
                update={
                    "status": "published",
                    "published_document_id": result.document_id,
                    "document_url": result.document_url,
                    "published_document_version": result.document_version,
                    "published_content_hash": result.content_hash,
                    "published_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._operations[proposal_id] = succeeded
            self._proposals[proposal_id] = published
            return succeeded.model_copy(deep=True)

    def mark_publish_unknown(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        comparison_time = _now(now)
        with self._lock:
            proposal = self._required_proposal(proposal_id, organization_id)
            operation = self._required_operation(proposal_id)
            if operation.status == "unknown":
                return operation.model_copy(deep=True)
            if operation.status != "write_started":
                raise WikiEditStateError("wiki publish operation was not attempted")
            ensure_proposal_transition(proposal.status, "publish_unknown")
            unknown = operation.model_copy(
                update={
                    "status": "unknown",
                    "resolved_at": comparison_time,
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            unresolved = proposal.model_copy(
                update={
                    "status": "publish_unknown",
                    "updated_at": comparison_time,
                },
                deep=True,
            )
            self._operations[proposal_id] = unknown
            self._proposals[proposal_id] = unresolved
            return unknown.model_copy(deep=True)

    def _required_proposal(
        self, proposal_id: str, organization_id: str
    ) -> WikiProposalForAuthoring:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise WikiEditNotFoundError("wiki proposal was not found")
        if proposal.organization_id != organization_id:
            raise WikiEditPermissionError("wiki proposal is outside this organization")
        return proposal

    def _required_operation(self, proposal_id: str) -> WikiPublishOperation:
        operation = self._operations.get(proposal_id)
        if operation is None:
            raise WikiEditNotFoundError("wiki publish operation was not found")
        return operation


class PostgresWikiEditingStore:
    """PostgreSQL source of truth with locks around revision and publish claims."""

    def __init__(self, settings: SharedSettings) -> None:
        self.settings = settings

    def _connection(self) -> Any:
        timeout_seconds = max(
            1.0,
            float(getattr(self.settings, "wiki_editing_api_timeout_seconds", 10.0)),
        )
        return get_postgres_connection(
            self.settings,
            connect_timeout_seconds=timeout_seconds,
            statement_timeout_seconds=timeout_seconds,
        )

    def create_or_get_request(
        self, request: WikiEditRequestInput
    ) -> tuple[WikiEditRequest, bool]:
        fingerprint = _request_fingerprint(request)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO wiki_edit_requests (
                        id, organization_id, actor_id, request_text,
                        instruction_hash, request_fingerprint, target_document_id,
                        selected_conversation_payload, idempotency_key, created_at
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (organization_id, idempotency_key) DO NOTHING
                    RETURNING *
                    """,
                    (
                        request.id,
                        request.organization_id,
                        request.actor_id,
                        request.instruction,
                        request.instruction_hash,
                        fingerprint,
                        request.target_document_id,
                        Jsonb(
                            [
                                source.storage_payload()
                                for source in request.selected_conversation
                            ]
                        ),
                        request.request_idempotency_key,
                        request.created_at,
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    return _request_from_row(row), True
                cursor.execute(
                    """
                    SELECT * FROM wiki_edit_requests
                    WHERE organization_id = %s AND idempotency_key = %s
                    """,
                    (request.organization_id, request.request_idempotency_key),
                )
                existing = cursor.fetchone()
                if existing is None:  # pragma: no cover - unique conflict invariant
                    raise RuntimeError("unable to load wiki edit idempotency record")
                if existing["request_fingerprint"] != fingerprint:
                    raise WikiEditConflictError(
                        "request idempotency key was already used with different input"
                    )
                return _request_from_row(existing), False

    def get_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequest | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM wiki_edit_requests
                    WHERE id = %s::uuid AND organization_id = %s
                    """,
                    (request_id, organization_id),
                )
                row = cursor.fetchone()
        return _request_from_row(row) if row is not None else None

    def get_authoring_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditRequestForAuthoring | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM wiki_edit_requests
                    WHERE id = %s::uuid AND organization_id = %s
                    """,
                    (request_id, organization_id),
                )
                row = cursor.fetchone()
        return _authoring_request_from_row(row) if row is not None else None

    def create_proposal(self, proposal: WikiProposalCreate) -> WikiEditProposal:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"wiki-edit-request:{proposal.request_id}",),
                )
                request_row = self._locked_request(
                    cursor,
                    proposal.request_id,
                    proposal.organization_id,
                )
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(revision), 0) + 1 AS next_revision
                    FROM wiki_edit_proposals
                    WHERE request_id = %s::uuid
                    """,
                    (proposal.request_id,),
                )
                revision_row = cursor.fetchone()
                if revision_row is None:  # pragma: no cover - aggregate invariant
                    raise RuntimeError("unable to reserve wiki proposal revision")
                base_payload = (
                    proposal.base_document.storage_payload()
                    if proposal.base_document is not None
                    else None
                )
                cursor.execute(
                    """
                    INSERT INTO wiki_edit_proposals (
                        id, request_id, organization_id, actor_id, revision, status,
                        target_action, target_document_id, base_document_payload,
                        base_content_hash, revision_instruction, created_at, updated_at
                    ) VALUES (
                        %s::uuid, %s::uuid, %s, %s, %s, 'queued',
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING *
                    """,
                    (
                        proposal.id,
                        proposal.request_id,
                        proposal.organization_id,
                        request_row["actor_id"],
                        revision_row["next_revision"],
                        proposal.target_action,
                        proposal.target_document_id,
                        Jsonb(base_payload) if base_payload is not None else None,
                        (
                            proposal.base_document.content_hash
                            if proposal.base_document is not None
                            else None
                        ),
                        proposal.revision_instruction,
                        proposal.created_at,
                        proposal.created_at,
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - INSERT RETURNING invariant
                    raise RuntimeError("unable to persist wiki proposal")
                return _proposal_from_row(row)

    def get_proposal(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM wiki_edit_proposals
                    WHERE id = %s::uuid AND organization_id = %s
                    """,
                    (proposal_id, organization_id),
                )
                row = cursor.fetchone()
        return _proposal_from_row(row) if row is not None else None

    def get_latest_proposal_for_request(
        self, request_id: str, *, organization_id: str
    ) -> WikiEditProposal | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM wiki_edit_proposals
                    WHERE request_id = %s::uuid AND organization_id = %s
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    (request_id, organization_id),
                )
                row = cursor.fetchone()
        return _proposal_from_row(row) if row is not None else None

    def get_authoring_work_item(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiAuthoringWorkItem | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT p.*, r.request_text, r.instruction_hash,
                           r.idempotency_key, r.selected_conversation_payload,
                           r.target_document_id AS request_target_document_id,
                           r.created_at AS request_created_at
                    FROM wiki_edit_proposals p
                    JOIN wiki_edit_requests r ON r.id = p.request_id
                    WHERE p.id = %s::uuid
                      AND p.organization_id = %s
                      AND p.status = 'authoring'
                    """,
                    (proposal_id, organization_id),
                )
                joined = cursor.fetchone()
        if joined is None:
            return None
        request_row = {
            "id": joined["request_id"],
            "organization_id": joined["organization_id"],
            "actor_id": joined["actor_id"],
            "request_text": joined["request_text"],
            "instruction_hash": joined["instruction_hash"],
            "target_document_id": joined["request_target_document_id"],
            "selected_conversation_payload": joined["selected_conversation_payload"],
            "idempotency_key": joined["idempotency_key"],
            "created_at": joined["request_created_at"],
        }
        return WikiAuthoringWorkItem(
            request=_authoring_request_from_row(request_row),
            proposal=_authoring_proposal_from_row(joined),
        )

    def claim_authoring(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        omp_metadata: WikiOmpRunMetadata,
        now: datetime | None = None,
        authoring_lease_seconds: float = _DEFAULT_AUTHORING_LEASE_SECONDS,
    ) -> WikiAuthoringWorkItem | None:
        comparison_time = _now(now)
        lease_seconds = _validated_authoring_lease_seconds(authoring_lease_seconds)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                current = _proposal_from_row(row)
                if current.status == "queued":
                    ensure_proposal_transition(current.status, "authoring")
                    cursor.execute(
                        """
                        UPDATE wiki_edit_proposals
                        SET status = 'authoring', omp_metadata = %s,
                            authoring_started_at = %s, updated_at = %s
                        WHERE id = %s::uuid
                        RETURNING *
                        """,
                        (
                            Jsonb(omp_metadata.model_dump(mode="json")),
                            comparison_time,
                            comparison_time,
                            proposal_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:  # pragma: no cover - locked row invariant
                        raise RuntimeError("unable to start wiki proposal authoring")
                elif current.status == "authoring":
                    if not _authoring_lease_expired(
                        current.authoring_started_at,
                        now=comparison_time,
                        lease_seconds=lease_seconds,
                    ):
                        return None
                    if current.omp_metadata != omp_metadata:
                        raise WikiEditConflictError(
                            "wiki proposal is already bound to another OMP run"
                        )
                    cursor.execute(
                        """
                        UPDATE wiki_edit_proposals
                        SET authoring_started_at = %s, updated_at = %s
                        WHERE id = %s::uuid
                        RETURNING *
                        """,
                        (comparison_time, comparison_time, proposal_id),
                    )
                    row = cursor.fetchone()
                    if row is None:  # pragma: no cover - locked row invariant
                        raise RuntimeError("unable to reclaim wiki proposal authoring")
                else:
                    return None
                request_row = self._locked_request(
                    cursor,
                    current.request_id,
                    organization_id,
                )
                return WikiAuthoringWorkItem(
                    request=_authoring_request_from_row(request_row),
                    proposal=_authoring_proposal_from_row(row),
                )

    def complete_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        output: WikiProposalOutput,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                proposal = _proposal_from_row(row)
                ensure_proposal_transition(proposal.status, "proposed")
                if row.get("output_committed_at") is not None:
                    raise WikiEditConflictError(
                        "wiki proposal output is already immutable"
                    )
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'proposed', proposed_title = %s, proposed_text = %s,
                        proposed_diff = %s, summary = %s, proposed_source_refs = %s,
                        proposed_at = %s, output_committed_at = %s, updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (
                        output.proposed_title,
                        output.proposed_text,
                        output.proposed_diff,
                        output.summary,
                        Jsonb(
                            [
                                source.model_dump(mode="json")
                                for source in output.source_refs
                            ]
                        ),
                        comparison_time,
                        comparison_time,
                        comparison_time,
                        proposal_id,
                    ),
                )
                updated = cursor.fetchone()
                if updated is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to complete wiki proposal")
                return _proposal_from_row(updated)

    def mark_conflict(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        conflict: WikiConflictDetails,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                proposal = _proposal_from_row(row)
                ensure_proposal_transition(proposal.status, "conflict")
                operation_row = self._locked_operation(cursor, proposal_id)
                if operation_row is not None:
                    operation = _operation_from_row(operation_row)
                    if operation.status not in {"pending", "write_started"}:
                        raise WikiEditStateError(
                            "wiki publish operation cannot be resolved as a conflict"
                        )
                    cursor.execute(
                        """
                        UPDATE wiki_edit_publish_operations
                        SET status = 'conflict', resolved_at = %s, updated_at = %s
                        WHERE id = %s::uuid
                        """,
                        (comparison_time, comparison_time, operation.id),
                    )
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'conflict', conflict_payload = %s, updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (
                        Jsonb(conflict.model_dump(mode="json")),
                        comparison_time,
                        proposal_id,
                    ),
                )
                updated = cursor.fetchone()
                if updated is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to mark wiki proposal conflict")
                return _proposal_from_row(updated)

    def fail_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        failure_code: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        normalized_code = _failure_code(failure_code)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                proposal = _proposal_from_row(row)
                ensure_proposal_transition(proposal.status, "failed")
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'failed', failure_code = %s, updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (normalized_code, comparison_time, proposal_id),
                )
                updated = cursor.fetchone()
                if updated is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to fail wiki proposal")
                return _proposal_from_row(updated)

    def cancel_proposal(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str | None = None,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                proposal = _authoring_proposal_from_row(row)
                _validate_owned_actor(proposal, actor_id)
                ensure_proposal_transition(proposal.status, "canceled")
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'canceled', updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (comparison_time, proposal_id),
                )
                updated = cursor.fetchone()
                if updated is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to cancel wiki proposal")
                return _proposal_from_row(updated)

    def acknowledge_review(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        actor_id: str,
        review_content_hash: str,
        now: datetime | None = None,
    ) -> WikiEditProposal:
        comparison_time = _now(now)
        normalized_hash = _validated_review_content_hash(review_content_hash)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                row = self._locked_proposal(cursor, proposal_id, organization_id)
                proposal = _proposal_from_row(row)
                _validate_owned_actor(proposal, actor_id)
                if proposal.review_acknowledged:
                    if (
                        proposal.review_acknowledged_by != actor_id
                        or proposal.review_acknowledged_content_hash != normalized_hash
                    ):
                        raise WikiEditConflictError(
                            "wiki proposal was acknowledged for a different review packet"
                        )
                    return proposal
                if proposal.status != "proposed":
                    raise WikiEditStateError(
                        "wiki proposal must be proposed before its review can be acknowledged"
                    )
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET review_acknowledged_by = %s,
                        review_acknowledged_content_hash = %s,
                        review_acknowledged_at = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (
                        actor_id,
                        normalized_hash,
                        comparison_time,
                        comparison_time,
                        proposal_id,
                    ),
                )
                acknowledged = cursor.fetchone()
                if acknowledged is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to acknowledge wiki review")
                return _proposal_from_row(acknowledged)

    def create_or_get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> tuple[WikiPublishOperation, bool]:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                proposal_row = self._locked_proposal(
                    cursor, proposal_id, organization_id
                )
                proposal = _proposal_from_row(proposal_row)
                operation_row = self._locked_operation(cursor, proposal_id)
                if operation_row is not None:
                    return _operation_from_row(operation_row), False
                if proposal.status != "proposed":
                    raise WikiEditStateError(
                        "wiki proposal must be proposed before publishing can begin"
                    )
                _require_review_acknowledgement(proposal)
                operation = self._insert_operation(cursor, proposal, now=_now())
                return operation, True

    def get_publish_operation(
        self, proposal_id: str, *, organization_id: str
    ) -> WikiPublishOperation | None:
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT o.*
                    FROM wiki_edit_publish_operations o
                    JOIN wiki_edit_proposals p ON p.id = o.proposal_id
                    WHERE o.proposal_id = %s::uuid AND p.organization_id = %s
                    """,
                    (proposal_id, organization_id),
                )
                row = cursor.fetchone()
        return _operation_from_row(row) if row is not None else None

    def claim_publish_attempt(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishClaim:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                proposal_row = self._locked_proposal(
                    cursor, proposal_id, organization_id
                )
                proposal = _proposal_from_row(proposal_row)
                operation_row = self._locked_operation(cursor, proposal_id)
                if operation_row is None:
                    if proposal.status != "proposed":
                        raise WikiEditStateError(
                            "wiki proposal must be proposed before publishing can begin"
                        )
                    _require_review_acknowledgement(proposal)
                    operation = self._insert_operation(
                        cursor, proposal, now=comparison_time
                    )
                    operation_row = self._operation_row(cursor, operation.id)
                    if operation_row is None:  # pragma: no cover - insert invariant
                        raise RuntimeError("unable to reload wiki publish operation")
                operation = _operation_from_row(operation_row)
                if operation.status != "pending":
                    return WikiPublishClaim(operation=operation, should_execute=False)
                ensure_proposal_transition(proposal.status, "publishing")
                cursor.execute(
                    """
                    UPDATE wiki_edit_publish_operations
                    SET status = 'write_started', write_started_at = %s, updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (comparison_time, comparison_time, operation.id),
                )
                started_row = cursor.fetchone()
                if started_row is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to start wiki publish operation")
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'publishing', updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    (comparison_time, proposal_id),
                )
                return WikiPublishClaim(
                    operation=_operation_from_row(started_row), should_execute=True
                )

    def mark_publish_succeeded(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        result: WikiPublishResult,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                proposal_row = self._locked_proposal(
                    cursor, proposal_id, organization_id
                )
                proposal = _proposal_from_row(proposal_row)
                operation_row = self._locked_operation(cursor, proposal_id)
                if operation_row is None:
                    raise WikiEditNotFoundError("wiki publish operation was not found")
                operation = _operation_from_row(operation_row)
                if operation.status == "succeeded":
                    if operation.result != result:
                        raise WikiEditConflictError(
                            "wiki publish operation already has a different result"
                        )
                    return operation
                if operation.status not in {"write_started", "unknown"}:
                    raise WikiEditStateError("wiki publish operation was not attempted")
                ensure_proposal_transition(proposal.status, "published")
                cursor.execute(
                    """
                    UPDATE wiki_edit_publish_operations
                    SET status = 'succeeded', result_payload = %s, resolved_at = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (
                        Jsonb(result.model_dump(mode="json")),
                        comparison_time,
                        comparison_time,
                        operation.id,
                    ),
                )
                completed_row = cursor.fetchone()
                if completed_row is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to complete wiki publish operation")
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'published', published_document_id = %s,
                        document_url = %s, published_document_version = %s,
                        published_content_hash = %s, published_at = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    (
                        result.document_id,
                        result.document_url,
                        result.document_version,
                        result.content_hash,
                        comparison_time,
                        comparison_time,
                        proposal_id,
                    ),
                )
                return _operation_from_row(completed_row)

    def mark_publish_unknown(
        self,
        proposal_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> WikiPublishOperation:
        comparison_time = _now(now)
        with self._connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                proposal_row = self._locked_proposal(
                    cursor, proposal_id, organization_id
                )
                proposal = _proposal_from_row(proposal_row)
                operation_row = self._locked_operation(cursor, proposal_id)
                if operation_row is None:
                    raise WikiEditNotFoundError("wiki publish operation was not found")
                operation = _operation_from_row(operation_row)
                if operation.status == "unknown":
                    return operation
                if operation.status != "write_started":
                    raise WikiEditStateError("wiki publish operation was not attempted")
                ensure_proposal_transition(proposal.status, "publish_unknown")
                cursor.execute(
                    """
                    UPDATE wiki_edit_publish_operations
                    SET status = 'unknown', resolved_at = %s, updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (comparison_time, comparison_time, operation.id),
                )
                unknown_row = cursor.fetchone()
                if unknown_row is None:  # pragma: no cover - locked row invariant
                    raise RuntimeError("unable to mark wiki publish operation unknown")
                cursor.execute(
                    """
                    UPDATE wiki_edit_proposals
                    SET status = 'publish_unknown', updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    (comparison_time, proposal_id),
                )
                return _operation_from_row(unknown_row)

    def _locked_request(
        self, cursor: Any, request_id: str, organization_id: str
    ) -> dict[str, Any]:
        cursor.execute(
            "SELECT * FROM wiki_edit_requests WHERE id = %s::uuid FOR UPDATE",
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WikiEditNotFoundError("wiki edit request was not found")
        if row["organization_id"] != organization_id:
            raise WikiEditPermissionError(
                "wiki edit request is outside this organization"
            )
        return row

    def _locked_proposal(
        self, cursor: Any, proposal_id: str, organization_id: str
    ) -> dict[str, Any]:
        cursor.execute(
            "SELECT * FROM wiki_edit_proposals WHERE id = %s::uuid FOR UPDATE",
            (proposal_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise WikiEditNotFoundError("wiki proposal was not found")
        if row["organization_id"] != organization_id:
            raise WikiEditPermissionError("wiki proposal is outside this organization")
        return row

    def _locked_operation(self, cursor: Any, proposal_id: str) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT * FROM wiki_edit_publish_operations
            WHERE proposal_id = %s::uuid
            FOR UPDATE
            """,
            (proposal_id,),
        )
        return cursor.fetchone()

    def _operation_row(self, cursor: Any, operation_id: str) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT * FROM wiki_edit_publish_operations WHERE id = %s::uuid",
            (operation_id,),
        )
        return cursor.fetchone()

    def _insert_operation(
        self,
        cursor: Any,
        proposal: WikiEditProposal,
        *,
        now: datetime,
    ) -> WikiPublishOperation:
        operation = _new_operation(proposal, now=now)
        cursor.execute(
            """
            INSERT INTO wiki_edit_publish_operations (
                id, proposal_id, organization_id, target_action, target_document_id,
                status, idempotency_key, created_at, updated_at
            ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'pending', %s, %s, %s)
            ON CONFLICT (proposal_id) DO NOTHING
            RETURNING *
            """,
            (
                operation.id,
                operation.proposal_id,
                operation.organization_id,
                operation.target_action,
                operation.target_document_id,
                operation.idempotency_key,
                operation.created_at,
                operation.updated_at,
            ),
        )
        row = cursor.fetchone()
        if row is not None:
            return _operation_from_row(row)
        existing = self._locked_operation(cursor, proposal.id)
        if existing is None:  # pragma: no cover - unique conflict invariant
            raise RuntimeError("unable to persist wiki publish operation")
        return _operation_from_row(existing)


def _failure_code(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("failure_code must not be blank")
    if len(normalized) > 256:
        raise ValueError("failure_code must be at most 256 characters")
    return normalized
