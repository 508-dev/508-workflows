"""Typed contracts for knowledge capture, retrieval, and Discord rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from five08.agent.models import AgentIdentityContext

KnowledgeScopeType = Literal["user", "project", "org"]
KnowledgeVisibility = Literal["private", "project", "org"]
KnowledgeSourceType = Literal[
    "discord_message", "discord_thread", "memory", "outline", "erpnext", "crm"
]
KnowledgeVerificationStatus = Literal[
    "inferred",
    "source_recorded",
    "author_confirmed",
    "user_confirmed",
    "admin_confirmed",
    "authoritative",
]
KnowledgeFactStatus = Literal["active", "superseded", "disputed", "deleted"]


class KnowledgeDiscordMessage(BaseModel):
    """One bounded Discord message supplied by the gateway for capture."""

    message_id: str = Field(min_length=1, max_length=32)
    author_id: str = Field(min_length=1, max_length=32)
    author_name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4096)
    created_at: datetime
    jump_url: str | None = Field(default=None, max_length=1000)
    author_is_bot: bool = False

    @field_validator("message_id", "author_id", "author_name", "content")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class KnowledgeDiscordSource(BaseModel):
    """Discord location and gateway-resolved visibility for a capture."""

    source_type: Literal["discord_message", "discord_thread"]
    source_ref: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=256)
    guild_id: str = Field(min_length=1, max_length=32)
    channel_id: str = Field(min_length=1, max_length=32)
    thread_id: str | None = Field(default=None, max_length=32)
    source_visibility: KnowledgeVisibility = "private"


class KnowledgeCaptureCandidate(BaseModel):
    """A frozen Q&A entry proposed from a bounded source snapshot."""

    candidate_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=3000)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    source_message_ids: list[str] = Field(min_length=1, max_length=12)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("question", "answer")
    @classmethod
    def _collapse_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            alias = " ".join(value.split())[:200]
            key = alias.casefold()
            if alias and key not in seen:
                normalized.append(alias)
                seen.add(key)
        return normalized[:8]


class KnowledgeCaptureRequest(BaseModel):
    """Request to extract reviewable knowledge from Discord messages."""

    context: AgentIdentityContext
    source: KnowledgeDiscordSource
    messages: list[KnowledgeDiscordMessage] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _validate_source_context(self) -> "KnowledgeCaptureRequest":
        if self.context.guild_id != self.source.guild_id:
            raise ValueError("capture source guild must match request context")
        if self.context.channel_id != self.source.channel_id:
            raise ValueError("capture source channel must match request context")
        if self.source.thread_id and self.context.thread_id != self.source.thread_id:
            raise ValueError("capture source thread must match request context")
        return self


class KnowledgeCaptureConfirmationRequest(BaseModel):
    """Confirmation envelope for one frozen capture draft."""

    context: AgentIdentityContext
    confirm: bool = True


class KnowledgeFact(BaseModel):
    """Durable knowledge entry returned by storage adapters."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    scope_type: KnowledgeScopeType
    scope_id: str
    kind: Literal["fact", "qa", "decision"] = "qa"
    key: str = Field(min_length=1, max_length=128)
    question: str | None = Field(default=None, max_length=1000)
    answer: str = Field(min_length=1, max_length=3000)
    aliases: list[str] = Field(default_factory=list)
    visibility: KnowledgeVisibility
    verification_status: KnowledgeVerificationStatus = "source_recorded"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: KnowledgeFactStatus = "active"
    created_by: str
    review_after: datetime | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    supersedes_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KnowledgeCaptureDraft(BaseModel):
    """Frozen capture candidate awaiting the original actor's confirmation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    actor_id: str
    scope_type: KnowledgeScopeType
    scope_id: str
    visibility: KnowledgeVisibility
    verification_status: KnowledgeVerificationStatus
    source: KnowledgeDiscordSource
    messages: list[KnowledgeDiscordMessage]
    candidates: list[KnowledgeCaptureCandidate]
    expires_at: datetime
    consumed_at: datetime | None = None
    confirmed_fact_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KnowledgeEvidence(BaseModel):
    """Normalized, authorization-checked evidence for answer synthesis."""

    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: KnowledgeSourceType
    source_ref: str
    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=4000)
    retrieval_text: str | None = Field(default=None, exclude=True, max_length=5000)
    url: str | None = Field(default=None, max_length=1000)
    visibility: KnowledgeVisibility
    authority: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance: float = Field(default=0.0, ge=0.0)
    updated_at: datetime | None = None
    stale: bool = False


class KnowledgeCitation(BaseModel):
    """User-visible citation attached to a grounded answer."""

    citation_id: str
    source_type: KnowledgeSourceType
    title: str
    source_ref: str
    url: str | None = None
    updated_at: datetime | None = None
    stale: bool = False


class KnowledgeCaptureResponse(BaseModel):
    """Capture preview or confirmation result returned to Discord."""

    status: Literal[
        "requires_confirmation",
        "saved",
        "canceled",
        "needs_clarification",
        "denied",
        "failed",
    ]
    message: str
    draft_id: str | None = None
    candidates: list[KnowledgeCaptureCandidate] = Field(default_factory=list)
    scope_type: KnowledgeScopeType | None = None
    scope_id: str | None = None
    visibility: KnowledgeVisibility | None = None
    facts: list[KnowledgeFact] = Field(default_factory=list)
    expires_at: datetime | None = None


class KnowledgeQueryRequest(BaseModel):
    """A natural-language organizational knowledge question."""

    question: str = Field(min_length=1, max_length=1000)
    context: AgentIdentityContext

    @field_validator("question")
    @classmethod
    def _normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class KnowledgeQueryResponse(BaseModel):
    """Grounded answer plus typed citations and destination policy."""

    status: Literal["answered", "insufficient", "denied", "failed"]
    answer: str
    citations: list[KnowledgeCitation] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    public_safe: bool = False
    visibility: KnowledgeVisibility = "private"
    source_errors: list[str] = Field(default_factory=list, max_length=8)
