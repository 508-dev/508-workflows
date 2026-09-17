"""Typed, bounded contracts for durable wiki-editing workflows.

The public response models intentionally do not contain raw Discord context,
request instructions, or base-document/source text.  Those values are only
available through the explicitly named ``*ForAuthoring`` internal models.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from five08.agent.models import AgentIdentityContext

WikiEditTargetAction = Literal["create", "update"]
WikiProposalStatus = Literal[
    "queued",
    "authoring",
    "proposed",
    "conflict",
    "failed",
    "canceled",
    "publishing",
    "published",
    "publish_unknown",
]
WikiPublishOperationStatus = Literal[
    "pending",
    "write_started",
    "succeeded",
    "unknown",
    "conflict",
]
WikiEditResponseAction = Literal[
    "none",
    "review",
    "publish",
    "revise",
    "cancel",
    "reconcile",
]
WikiSourceType = Literal[
    "discord_message",
    "discord_thread",
    "outline_document",
    "memory_fact",
    "other",
]

# Discord's default upload allowance is much larger, but a review packet is a
# deliberately narrow handoff rather than a general file-transfer channel. The
# service rejects an over-limit draft before it becomes reviewable, so the UI
# never silently truncates the article or diff that a requester must approve.
WIKI_REVIEW_ATTACHMENT_MAX_BYTES = 1_000_000
WIKI_REVIEW_ID_LENGTH = 16


def wiki_content_hash(value: str) -> str:
    """Return the stable SHA-256 hash used for snapshots and provenance."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_review_source_url(value: str | None) -> str | None:
    """Return a safe HTTP(S) source URL for a private Discord review packet."""
    candidate = (value or "").strip()
    if (
        not candidate
        or len(candidate) > 2_000
        or any(character.isspace() for character in candidate)
        or any(character in candidate for character in "<>")
    ):
        return None
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return candidate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _strip_required(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class WikiEditingError(RuntimeError):
    """Base error for deterministic wiki-editing domain failures."""


class WikiEditNotFoundError(WikiEditingError):
    """Raised when a requested workflow record does not exist."""


class WikiEditPermissionError(WikiEditingError, PermissionError):
    """Raised when an actor attempts to mutate another actor's workflow."""


class WikiEditConflictError(WikiEditingError):
    """Raised for stale or duplicate workflow writes needing user resolution."""


class WikiEditStateError(WikiEditingError):
    """Raised when an operation is invalid for the current lifecycle state."""


class WikiConversationProvenance(BaseModel):
    """Safe metadata for an organization-visible selected conversation source."""

    source_type: Literal["discord_message", "discord_thread"]
    source_ref: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2000)
    guild_id: str | None = Field(default=None, max_length=128)
    channel_id: str | None = Field(default=None, max_length=128)
    thread_id: str | None = Field(default=None, max_length=128)
    message_ids: list[str] = Field(default_factory=list, max_length=100)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator(
        "source_ref", "title", "source_url", "guild_id", "channel_id", "thread_id"
    )
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("message_ids")
    @classmethod
    def _normalize_message_ids(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            message_id = _strip_required(value)[:128]
            if message_id not in seen:
                normalized.append(message_id)
                seen.add(message_id)
        return normalized

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower().strip()
        if len(normalized) != 64 or any(
            char not in "0123456789abcdef" for char in normalized
        ):
            raise ValueError("content_hash must be a SHA-256 hexadecimal digest")
        return normalized


class WikiSelectedConversationSource(BaseModel):
    """Internal organization-visible text selected for one authoring request.

    ``organization_visible_text`` is excluded from ordinary Pydantic dumps so
    it cannot accidentally appear in a Discord/API response.  Storage adapters
    persist it only in their private authoring payload.
    """

    provenance: WikiConversationProvenance
    visibility: Literal["org"] = "org"
    organization_visible_text: str = Field(
        min_length=1,
        max_length=24_000,
        exclude=True,
        repr=False,
    )

    @field_validator("organization_visible_text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("organization_visible_text must not be blank")
        return value

    @model_validator(mode="after")
    def _bind_content_hash(self) -> "WikiSelectedConversationSource":
        content_hash = wiki_content_hash(self.organization_visible_text)
        if (
            self.provenance.content_hash is not None
            and self.provenance.content_hash != content_hash
        ):
            raise ValueError("conversation provenance content_hash does not match text")
        if self.provenance.content_hash is None:
            self.provenance = self.provenance.model_copy(
                update={"content_hash": content_hash}
            )
        return self

    def storage_payload(self) -> dict[str, object]:
        """Return the private storage shape; never use this for a response."""
        return {
            "provenance": self.provenance.model_dump(mode="json"),
            "visibility": self.visibility,
            "organization_visible_text": self.organization_visible_text,
        }

    @classmethod
    def from_storage_payload(
        cls, payload: dict[str, object]
    ) -> "WikiSelectedConversationSource":
        raw_visibility = payload.get("visibility", "org")
        return cls(
            provenance=WikiConversationProvenance.model_validate(
                payload.get("provenance") or {}
            ),
            visibility=cast(
                Literal["org"],
                raw_visibility if isinstance(raw_visibility, str) else "",
            ),
            organization_visible_text=str(
                payload.get("organization_visible_text") or ""
            ),
        )


class WikiSourceReference(BaseModel):
    """Safe citation metadata retained with a proposal, without source text."""

    source_type: WikiSourceType
    source_ref: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2000)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("source_ref", "title", "source_url")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str | None) -> str | None:
        return WikiConversationProvenance._validate_content_hash(value)


class WikiReviewSourceLink(BaseModel):
    """A source link safe to include in a private Discord review attachment.

    The opaque ``source_ref`` and any source text intentionally stay out of
    this representation. A model may cite an approved source with no link, but
    it cannot cause arbitrary URI schemes or credential-bearing URLs to appear
    in the review packet.
    """

    source_type: WikiSourceType
    title: str = Field(min_length=1, max_length=512)
    url: str = Field(min_length=1, max_length=2_000)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        safe_url = _safe_review_source_url(value)
        if safe_url is None:
            raise ValueError("review source URLs must be safe HTTP(S) URLs")
        return safe_url

    @classmethod
    def from_source_reference(
        cls,
        source: "WikiSourceReference",
    ) -> "WikiReviewSourceLink | None":
        """Keep only a source reference that has a safe displayable URL."""
        safe_url = _safe_review_source_url(source.source_url)
        if safe_url is None:
            return None
        return cls(
            source_type=source.source_type,
            title=source.title,
            url=safe_url,
        )


class WikiBaseDocumentReference(BaseModel):
    """Safe identity/version metadata for the document used as an edit base."""

    document_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    document_url: str | None = Field(default=None, max_length=2000)
    document_version: str | None = Field(default=None, max_length=512)
    content_hash: str = Field(min_length=64, max_length=64)
    fetched_at: datetime = Field(default_factory=_utc_now)

    @field_validator("document_id", "title", "document_url", "document_version")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        normalized = WikiConversationProvenance._validate_content_hash(value)
        if normalized is None:  # pragma: no cover - Field requires a string
            raise ValueError("content_hash must not be empty")
        return normalized

    @field_validator("fetched_at")
    @classmethod
    def _normalize_fetched_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class WikiBaseDocumentSnapshot(WikiBaseDocumentReference):
    """Private complete document snapshot used for authoring and conflict checks."""

    content: str = Field(max_length=500_000, exclude=True, repr=False)

    @model_validator(mode="after")
    def _validate_snapshot_hash(self) -> "WikiBaseDocumentSnapshot":
        if self.content_hash != wiki_content_hash(self.content):
            raise ValueError("base document content_hash does not match content")
        return self

    @property
    def reference(self) -> WikiBaseDocumentReference:
        return WikiBaseDocumentReference(
            document_id=self.document_id,
            title=self.title,
            document_url=self.document_url,
            document_version=self.document_version,
            content_hash=self.content_hash,
            fetched_at=self.fetched_at,
        )

    def storage_payload(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["content"] = self.content
        return payload

    @classmethod
    def from_storage_payload(
        cls, payload: dict[str, object]
    ) -> "WikiBaseDocumentSnapshot":
        return cls.model_validate(payload)


class WikiOmpRunMetadata(BaseModel):
    """Opaque operational metadata for the bounded OMP authoring run."""

    session_id: str = Field(min_length=1, max_length=512)
    model: str = Field(min_length=1, max_length=512)
    run_id: str = Field(min_length=1, max_length=512)
    provider: str | None = Field(default=None, max_length=256)
    attempt: int = Field(default=1, ge=1, le=100)

    @field_validator("session_id", "model", "run_id", "provider")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None


class WikiConflictDetails(BaseModel):
    """Safe stale-base metadata that tells the caller why publishing stopped."""

    current_document_id: str = Field(min_length=1, max_length=256)
    current_content_hash: str = Field(min_length=64, max_length=64)
    current_document_version: str | None = Field(default=None, max_length=512)
    message: str = Field(min_length=1, max_length=2000)
    detected_at: datetime = Field(default_factory=_utc_now)

    @field_validator("current_document_id", "current_document_version", "message")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("current_content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        normalized = WikiConversationProvenance._validate_content_hash(value)
        if normalized is None:  # pragma: no cover - Field requires a string
            raise ValueError("current_content_hash must not be empty")
        return normalized

    @field_validator("detected_at")
    @classmethod
    def _normalize_detected_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)


class WikiProposalOutput(BaseModel):
    """One immutable authored revision result, safe for organization review."""

    proposed_title: str = Field(min_length=1, max_length=512)
    proposed_text: str = Field(min_length=1, max_length=500_000)
    proposed_diff: str = Field(min_length=1, max_length=250_000)
    summary: str = Field(min_length=1, max_length=8_000)
    source_refs: list[WikiSourceReference] = Field(default_factory=list, max_length=100)

    @field_validator("proposed_title", "summary")
    @classmethod
    def _strip_required_fields(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("proposed_text", "proposed_diff")
    @classmethod
    def _validate_document_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document output must not be blank")
        return value


class WikiPublishResult(BaseModel):
    """Safe result captured after a confirmed external Outline write."""

    document_id: str = Field(min_length=1, max_length=256)
    document_url: str | None = Field(default=None, max_length=2000)
    document_version: str | None = Field(default=None, max_length=512)
    content_hash: str = Field(min_length=64, max_length=64)

    @field_validator("document_id", "document_url", "document_version")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("content_hash")
    @classmethod
    def _validate_content_hash(cls, value: str) -> str:
        normalized = WikiConversationProvenance._validate_content_hash(value)
        if normalized is None:  # pragma: no cover - Field requires a string
            raise ValueError("content_hash must not be empty")
        return normalized


class WikiEditCreateRequest(BaseModel):
    """Public API/Discord input for an explicitly requested wiki edit.

    The context, instruction, selected text, and idempotency key are excluded
    from standard serialization because this is an input envelope, not a
    response payload.
    """

    context: AgentIdentityContext = Field(exclude=True, repr=False)
    instruction: str = Field(min_length=1, max_length=12_000, exclude=True, repr=False)
    target_document_id: str | None = Field(default=None, max_length=256)
    selected_conversation: list[WikiSelectedConversationSource] = Field(
        default_factory=list,
        max_length=100,
        exclude=True,
        repr=False,
    )
    request_idempotency_key: str | None = Field(
        default=None,
        max_length=512,
        exclude=True,
        repr=False,
    )

    @field_validator("instruction", "target_document_id", "request_idempotency_key")
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @model_validator(mode="after")
    def _require_organization(self) -> "WikiEditCreateRequest":
        if not self.context.organization_id:
            raise ValueError("wiki editing requires an organization_id")
        return self

    def to_request_input(self) -> "WikiEditRequestInput":
        """Make the internal record input without persisting arbitrary context."""
        organization_id = self.context.organization_id
        if not organization_id:  # pragma: no cover - validated above
            raise ValueError("wiki editing requires an organization_id")
        return WikiEditRequestInput(
            organization_id=organization_id,
            actor_id=self.context.discord_user_id,
            instruction=self.instruction,
            target_document_id=self.target_document_id,
            selected_conversation=self.selected_conversation,
            request_idempotency_key=(
                self.request_idempotency_key
                or self.context.interaction_id
                or self.context.operation_id
                or str(uuid4())
            ),
        )


class WikiEditRevisionRequest(BaseModel):
    """Public input to request a fresh immutable revision of a proposal."""

    context: AgentIdentityContext = Field(exclude=True, repr=False)
    proposal_id: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=12_000, exclude=True, repr=False)

    @field_validator("proposal_id", "instruction")
    @classmethod
    def _strip_text_fields(cls, value: str) -> str:
        return _strip_required(value)


class WikiEditActionRequest(BaseModel):
    """Public input for a proposal action selected through Discord controls."""

    context: AgentIdentityContext = Field(exclude=True, repr=False)
    proposal_id: str = Field(min_length=1, max_length=256)

    @field_validator("proposal_id")
    @classmethod
    def _strip_proposal_id(cls, value: str) -> str:
        return _strip_required(value)


class WikiEditReviewAcknowledgementRequest(BaseModel):
    """Requester confirmation bound to one immutable rendered review packet."""

    context: AgentIdentityContext = Field(exclude=True, repr=False)
    proposal_id: str = Field(min_length=1, max_length=256)
    review_id: str = Field(
        min_length=WIKI_REVIEW_ID_LENGTH, max_length=WIKI_REVIEW_ID_LENGTH
    )

    @field_validator("proposal_id")
    @classmethod
    def _strip_proposal_id(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("review_id")
    @classmethod
    def _validate_review_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != WIKI_REVIEW_ID_LENGTH or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError("review_id must be a short hexadecimal review binding")
        return normalized


class WikiEditRequestInput(BaseModel):
    """Trusted internal input persisted as one idempotent authoring request."""

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=256)
    organization_id: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=12_000, exclude=True, repr=False)
    target_document_id: str | None = Field(default=None, max_length=256)
    selected_conversation: list[WikiSelectedConversationSource] = Field(
        default_factory=list,
        max_length=100,
        exclude=True,
        repr=False,
    )
    request_idempotency_key: str = Field(
        min_length=1,
        max_length=512,
        exclude=True,
        repr=False,
    )
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator(
        "id",
        "organization_id",
        "actor_id",
        "instruction",
        "target_document_id",
        "request_idempotency_key",
    )
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @property
    def instruction_hash(self) -> str:
        return wiki_content_hash(self.instruction)


class WikiEditRequest(BaseModel):
    """Safe externally-readable request record with no raw request/context text."""

    id: str
    organization_id: str
    actor_id: str
    target_document_id: str | None = None
    instruction_hash: str
    selected_conversation: list[WikiConversationProvenance] = Field(
        default_factory=list
    )
    created_at: datetime


class WikiEditRequestForAuthoring(WikiEditRequest):
    """Trusted internal request view returned only to the authoring runtime."""

    instruction: str = Field(exclude=True, repr=False)
    selected_source_text: list[WikiSelectedConversationSource] = Field(
        default_factory=list,
        exclude=True,
        repr=False,
    )
    request_idempotency_key: str = Field(exclude=True, repr=False)


class WikiProposalCreate(BaseModel):
    """Input for reserving the next immutable proposal revision."""

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)
    organization_id: str = Field(min_length=1, max_length=256)
    target_action: WikiEditTargetAction
    target_document_id: str | None = Field(default=None, max_length=256)
    base_document: WikiBaseDocumentSnapshot | None = Field(default=None, exclude=True)
    revision_instruction: str | None = Field(
        default=None,
        max_length=12_000,
        exclude=True,
        repr=False,
    )
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator(
        "id",
        "request_id",
        "organization_id",
        "target_document_id",
        "revision_instruction",
    )
    @classmethod
    def _strip_text_fields(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def _validate_target(self) -> "WikiProposalCreate":
        if self.target_action == "create":
            if self.target_document_id is not None or self.base_document is not None:
                raise ValueError("create proposals must not carry a base document")
            return self
        if self.target_document_id is None or self.base_document is None:
            raise ValueError("update proposals require a target document snapshot")
        if self.base_document.document_id != self.target_document_id:
            raise ValueError("target_document_id must match the base document")
        return self


class WikiEditProposal(BaseModel):
    """Safe durable proposal record; revision payload is never overwritten."""

    id: str
    request_id: str
    organization_id: str
    actor_id: str
    revision: int = Field(ge=1)
    status: WikiProposalStatus
    target_action: WikiEditTargetAction
    target_document_id: str | None = None
    base_document: WikiBaseDocumentReference | None = None
    proposed_title: str | None = None
    proposed_text: str | None = None
    proposed_diff: str | None = None
    summary: str | None = None
    source_refs: list[WikiSourceReference] = Field(default_factory=list)
    omp_metadata: WikiOmpRunMetadata | None = None
    conflict: WikiConflictDetails | None = None
    failure_code: str | None = None
    published_document_id: str | None = None
    document_url: str | None = None
    published_document_version: str | None = None
    published_content_hash: str | None = None
    review_acknowledged_by: str | None = None
    review_acknowledged_content_hash: str | None = None
    review_acknowledged_at: datetime | None = None
    created_at: datetime
    authoring_started_at: datetime | None = None
    proposed_at: datetime | None = None
    published_at: datetime | None = None
    updated_at: datetime

    @field_validator("review_acknowledged_by")
    @classmethod
    def _strip_review_acknowledged_by(cls, value: str | None) -> str | None:
        return _strip_required(value) if value is not None else None

    @field_validator("review_acknowledged_content_hash")
    @classmethod
    def _validate_review_acknowledged_content_hash(
        cls,
        value: str | None,
    ) -> str | None:
        return WikiConversationProvenance._validate_content_hash(value)

    @field_validator("review_acknowledged_at")
    @classmethod
    def _normalize_review_acknowledged_at(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _normalize_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_review_acknowledgement(self) -> "WikiEditProposal":
        values = (
            self.review_acknowledged_by,
            self.review_acknowledged_content_hash,
            self.review_acknowledged_at,
        )
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("wiki review acknowledgement fields must be set together")
        if (
            self.review_acknowledged_by is not None
            and self.review_acknowledged_by != self.actor_id
        ):
            raise ValueError("wiki review must be acknowledged by its requester")
        return self

    @property
    def review_acknowledged(self) -> bool:
        """Whether the requester durably acknowledged this immutable revision."""
        return self.review_acknowledged_at is not None


class WikiProposalForAuthoring(WikiEditProposal):
    """Trusted internal proposal view containing the base snapshot text."""

    base_snapshot: WikiBaseDocumentSnapshot | None = Field(default=None, exclude=True)
    revision_instruction: str | None = Field(default=None, exclude=True, repr=False)


class WikiEditReviewArtifact(BaseModel):
    """Complete owner-only review packet rendered as one bounded attachment.

    The article and diff are deliberately not part of ordinary workflow
    responses. Only the actor-scoped status read creates this artifact, and the
    Discord cog emits it as an ephemeral attachment before offering its
    acknowledgement action.
    """

    review_id: str = Field(
        min_length=WIKI_REVIEW_ID_LENGTH,
        max_length=WIKI_REVIEW_ID_LENGTH,
    )
    proposed_title: str = Field(min_length=1, max_length=512)
    proposed_article: str = Field(min_length=1, max_length=500_000)
    complete_diff: str = Field(min_length=1, max_length=250_000)
    source_links: list[WikiReviewSourceLink] = Field(
        default_factory=list, max_length=100
    )

    @field_validator("review_id")
    @classmethod
    def _validate_review_id(cls, value: str) -> str:
        return WikiEditReviewAcknowledgementRequest._validate_review_id(value)

    @field_validator("proposed_title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("proposed_article", "complete_diff")
    @classmethod
    def _require_document_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review content must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_review_binding(self) -> "WikiEditReviewArtifact":
        if self.review_id != self.content_hash[:WIKI_REVIEW_ID_LENGTH]:
            raise ValueError("review_id does not bind this complete review packet")
        return self

    @property
    def content_hash(self) -> str:
        """Full immutable review hash stored with an acknowledgement."""
        payload = {
            "version": 1,
            "proposed_title": self.proposed_title,
            "proposed_article": self.proposed_article,
            "complete_diff": self.complete_diff,
            "source_links": [
                source.model_dump(mode="json") for source in self.source_links
            ],
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return wiki_content_hash(serialized)

    @property
    def attachment_filename(self) -> str:
        """Return a deterministic filename with no untrusted title component."""
        return f"wiki-review-{self.review_id}.md"

    def attachment_bytes(self) -> bytes:
        """Render the exact review packet or reject it rather than truncating."""
        sources = "\n".join(
            (
                f"{index}. {source.source_type}: "
                f"{' '.join(source.title.split())}\n   <{source.url}>"
            )
            for index, source in enumerate(self.source_links, start=1)
        )
        if not sources:
            sources = "No linked sources were supplied with this draft."
        rendered = (
            "# Wiki update review\n\n"
            f"Review ID: {self.review_id}\n"
            "Audience: shared co-op wiki\n\n"
            "## Proposed article\n\n"
            f"# {self.proposed_title}\n\n"
            f"{self.proposed_article}\n\n"
            "## Complete diff\n\n"
            f"{self.complete_diff}\n\n"
            "## Linked sources\n\n"
            f"{sources}\n"
        )
        encoded = rendered.encode("utf-8")
        if len(encoded) > WIKI_REVIEW_ATTACHMENT_MAX_BYTES:
            raise ValueError("wiki review attachment exceeds the bounded size")
        return encoded

    @classmethod
    def from_proposal(cls, proposal: WikiEditProposal) -> "WikiEditReviewArtifact":
        """Build the review artifact only from one immutable completed proposal."""
        if (
            proposal.proposed_title is None
            or proposal.proposed_text is None
            or proposal.proposed_diff is None
        ):
            raise WikiEditStateError("wiki proposal has no complete review output")
        return cls.from_output(
            proposed_title=proposal.proposed_title,
            proposed_article=proposal.proposed_text,
            complete_diff=proposal.proposed_diff,
            source_refs=proposal.source_refs,
        )

    @classmethod
    def from_output(
        cls,
        *,
        proposed_title: str,
        proposed_article: str,
        complete_diff: str,
        source_refs: list[WikiSourceReference],
    ) -> "WikiEditReviewArtifact":
        """Build and size-check a packet before its proposal output is persisted."""
        source_links = [
            source_link
            for source in source_refs
            if (source_link := WikiReviewSourceLink.from_source_reference(source))
            is not None
        ]
        payload = {
            "version": 1,
            "proposed_title": proposed_title,
            "proposed_article": proposed_article,
            "complete_diff": complete_diff,
            "source_links": [source.model_dump(mode="json") for source in source_links],
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = wiki_content_hash(serialized)
        artifact = cls(
            review_id=content_hash[:WIKI_REVIEW_ID_LENGTH],
            proposed_title=proposed_title,
            proposed_article=proposed_article,
            complete_diff=complete_diff,
            source_links=source_links,
        )
        # Validate the rendered representation at the same boundary that
        # creates it. The UI will never replace omitted data with a preview.
        artifact.attachment_bytes()
        return artifact


class WikiAuthoringWorkItem(BaseModel):
    """The only store output that deliberately joins private input with a draft."""

    request: WikiEditRequestForAuthoring = Field(exclude=True, repr=False)
    proposal: WikiProposalForAuthoring = Field(exclude=True, repr=False)


class WikiPublishOperation(BaseModel):
    """One append-only external-write operation allocated per proposal."""

    id: str
    proposal_id: str
    organization_id: str
    target_action: WikiEditTargetAction
    target_document_id: str | None = None
    status: WikiPublishOperationStatus
    idempotency_key: str
    write_started_at: datetime | None = None
    resolved_at: datetime | None = None
    result: WikiPublishResult | None = None
    created_at: datetime
    updated_at: datetime


class WikiPublishClaim(BaseModel):
    """Result of atomically claiming the one permitted external write attempt."""

    operation: WikiPublishOperation
    should_execute: bool


class WikiEditResponse(BaseModel):
    """Workflow response with an optional owner-scoped review artifact.

    Ordinary lifecycle responses contain only metadata. The complete article and
    diff can appear in ``review`` only after the service has authenticated the
    proposal owner for a status read; callers must not persist or audit it.
    """

    proposal_id: str | None = None
    request_id: str | None = None
    status: WikiProposalStatus | None = None
    # Wiki editing is deliberately limited to the configured organization-wide
    # Outline collection; expose that audience in the review card so the
    # requester can see the sharing boundary before publishing.
    audience: Literal["shared_coop_wiki"] = "shared_coop_wiki"
    message: str = Field(min_length=1, max_length=8_000)
    action: WikiEditResponseAction = "none"
    target_document_id: str | None = None
    title: str | None = None
    document_url: str | None = None
    diff: str | None = None
    summary: str | None = None
    source_count: int = Field(default=0, ge=0)
    revision: int | None = Field(default=None, ge=1)
    operation_status: WikiPublishOperationStatus | None = None
    review_acknowledged: bool = False
    review: WikiEditReviewArtifact | None = None

    @classmethod
    def from_proposal(
        cls,
        proposal: WikiEditProposal,
        *,
        message: str,
        action: WikiEditResponseAction = "none",
        operation: WikiPublishOperation | None = None,
        review: WikiEditReviewArtifact | None = None,
    ) -> "WikiEditResponse":
        """Create a safe response without exposing authoring-only payloads."""
        return cls(
            proposal_id=proposal.id,
            request_id=proposal.request_id,
            status=proposal.status,
            message=message,
            action=action,
            target_document_id=(
                proposal.published_document_id or proposal.target_document_id
            ),
            title=proposal.proposed_title
            or (proposal.base_document.title if proposal.base_document else None),
            document_url=proposal.document_url
            or (
                proposal.base_document.document_url
                if proposal.base_document is not None
                else None
            ),
            summary=proposal.summary,
            source_count=len(proposal.source_refs),
            revision=proposal.revision,
            operation_status=operation.status if operation is not None else None,
            review_acknowledged=proposal.review_acknowledged,
            review=review,
        )


PROPOSAL_TRANSITIONS: dict[WikiProposalStatus, frozenset[WikiProposalStatus]] = {
    "queued": frozenset({"authoring", "failed", "canceled"}),
    # An authoring transport/capacity failure can release this lease back to
    # queued. That repeats only the non-mutating draft phase and retains the
    # same durable proposal revision/run metadata.
    "authoring": frozenset({"queued", "proposed", "conflict", "failed", "canceled"}),
    "proposed": frozenset({"conflict", "canceled", "publishing"}),
    "conflict": frozenset({"canceled"}),
    "failed": frozenset(),
    "canceled": frozenset(),
    "publishing": frozenset({"published", "publish_unknown", "conflict"}),
    "published": frozenset(),
    "publish_unknown": frozenset({"published"}),
}


def ensure_proposal_transition(
    current: WikiProposalStatus,
    target: WikiProposalStatus,
) -> None:
    """Raise a deterministic error when a lifecycle transition is unsafe."""
    if target not in PROPOSAL_TRANSITIONS[current]:
        raise WikiEditStateError(
            f"cannot transition wiki proposal from {current!r} to {target!r}"
        )
