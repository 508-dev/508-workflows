"""Typed models for the agent gateway contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
ModelTier = Literal["fast", "strong", "reasoning"]
ResponseDestinationVisibility = Literal["private", "public", "restricted"]
AgentContextSourceType = Literal[
    "discord_thread",
    "discord_channel",
    "discord_message",
    "memory_fact",
    "crm",
    "docs",
    "request",
]
MemoryScopeType = Literal["user", "project", "org"]
MemoryVisibility = Literal["private", "project", "org"]
MemoryVerificationStatus = Literal[
    "inferred",
    "user_confirmed",
    "admin_confirmed",
    "authoritative",
]


class AgentContextSnippet(BaseModel):
    """One bounded, source-labeled context snippet for an agent operation."""

    source_id: str = Field(default_factory=lambda: str(uuid4()))
    source_type: AgentContextSourceType
    source_ref: str
    label: str
    text: str = Field(max_length=2048)
    token_count: int = Field(default=0, ge=0)
    channel_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    author_id: str | None = None
    created_at: datetime | None = None
    trusted: bool = False


class AgentContextSource(BaseModel):
    """Audit-safe metadata for context loaded into an agent operation."""

    source_id: str
    operation_id: str | None = None
    source_type: AgentContextSourceType
    source_ref: str
    scope_type: MemoryScopeType | Literal["discord"] | None = None
    scope_id: str | None = None
    loaded_by: str
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    token_count: int = Field(default=0, ge=0)
    policy_decision_id: str | None = None


class MemoryFact(BaseModel):
    """Durable memory fact with provenance, visibility, and retention metadata."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    scope_type: MemoryScopeType
    scope_id: str
    key: str = Field(min_length=1, max_length=128)
    value_json: dict[str, Any]
    visibility: MemoryVisibility
    source_type: AgentContextSourceType
    source_ref: str
    source_excerpt_hash: str | None = None
    created_by: str
    verification_status: MemoryVerificationStatus = "inferred"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentIdentityContext(BaseModel):
    """Resolved actor and tenant context for one agent request."""

    discord_user_id: str
    operation_id: str | None = None
    internal_user_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    parent_message_id: str | None = None
    response_destination_visibility: ResponseDestinationVisibility = "private"
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    impersonation: bool = False
    interaction_id: str | None = None
    message_id: str | None = None
    context_snippets: list[AgentContextSnippet] = Field(default_factory=list)


class AgentRequest(BaseModel):
    """Natural-language agent request from Discord or another client."""

    message: str = Field(max_length=4096)
    context: AgentIdentityContext


class AgentToolAction(BaseModel):
    """One frozen tool call proposed by the agent."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = "low"
    requires_confirmation: bool = False
    required_scopes: list[str] = Field(default_factory=list)
    summary: str


class AgentModelSelection(BaseModel):
    """Resolved model/provider metadata for a plan, excluding credentials."""

    tier: ModelTier
    model: str
    base_url: str | None = None
    source_tier: ModelTier | Literal["openai_default", "built_in_default"]
    fallback_used: bool = False
    api_key_configured: bool = False


class AgentPlan(BaseModel):
    """A deterministic, policy-checked plan that may be executed later."""

    plan_id: str
    operation_id: str | None = None
    intent: str
    planner: Literal["deterministic_regex", "live_model"] = "deterministic_regex"
    model_tier: ModelTier
    model: AgentModelSelection
    actions: list[AgentToolAction] = Field(default_factory=list)
    human_summary: str
    requires_confirmation: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    context_sources: list[AgentContextSource] = Field(default_factory=list)


class AgentExecutionResult(BaseModel):
    """Result of executing a single approved tool call."""

    tool_name: str
    status: Literal["succeeded", "failed", "denied"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentResponse(BaseModel):
    """Gateway response returned to Discord clients."""

    status: Literal[
        "executed",
        "requires_confirmation",
        "needs_clarification",
        "canceled",
        "denied",
        "failed",
    ]
    plan: AgentPlan | None = None
    results: list[AgentExecutionResult] = Field(default_factory=list)
    message: str
    clarification_question: str | None = None
