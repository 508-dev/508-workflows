"""Typed models for the agent gateway contract."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

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


# Keep individual facts deliberately small.  Agent memory is for concise,
# attributable preferences and decisions, not conversation transcripts or
# arbitrary document storage.
MAX_MEMORY_VALUE_JSON_BYTES = 8_192
MAX_MEMORY_VALUE_JSON_DEPTH = 4
MAX_MEMORY_VALUE_JSON_ITEMS = 50
MAX_MEMORY_VALUE_JSON_STRING_LENGTH = 2_048
MAX_MEMORY_FACT_KEY_CHARS = 128


def validate_memory_fact_key(value: object) -> str:
    """Normalize a durable-memory key before it reaches a store."""

    if not isinstance(value, str):
        raise ValueError("Memory key must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Memory key is required")
    if len(normalized) > MAX_MEMORY_FACT_KEY_CHARS:
        raise ValueError(
            f"Memory key must be at most {MAX_MEMORY_FACT_KEY_CHARS} characters"
        )
    return normalized


def validate_memory_value_json(value_json: dict[str, Any]) -> dict[str, Any]:
    """Validate that a memory value is a small, JSON-safe object.

    This validation intentionally lives at the model boundary so both the
    in-memory and Postgres stores reject oversized or non-JSON payloads before
    they can be retained.  Sensitive-content screening remains a storage
    concern because it applies only to new persistence, not historic rows
    being read from the database.
    """

    if not isinstance(value_json, dict):
        raise ValueError("Memory value_json must be an object")

    item_count = 0

    def visit(value: Any, *, depth: int) -> None:
        nonlocal item_count
        if depth > MAX_MEMORY_VALUE_JSON_DEPTH:
            raise ValueError("Memory value_json is nested too deeply")
        if value is None or isinstance(value, bool | int):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Memory value_json contains a non-finite number")
            return
        if isinstance(value, str):
            if len(value) > MAX_MEMORY_VALUE_JSON_STRING_LENGTH:
                raise ValueError("Memory value_json contains an overlong string")
            return
        if isinstance(value, dict):
            item_count += len(value)
            if item_count > MAX_MEMORY_VALUE_JSON_ITEMS:
                raise ValueError("Memory value_json contains too many items")
            for key, nested_value in value.items():
                if not isinstance(key, str):
                    raise ValueError("Memory value_json keys must be strings")
                if len(key) > 128:
                    raise ValueError("Memory value_json contains an overlong key")
                visit(nested_value, depth=depth + 1)
            return
        if isinstance(value, list):
            item_count += len(value)
            if item_count > MAX_MEMORY_VALUE_JSON_ITEMS:
                raise ValueError("Memory value_json contains too many items")
            for nested_value in value:
                visit(nested_value, depth=depth + 1)
            return
        raise ValueError("Memory value_json must contain JSON-compatible values")

    visit(value_json, depth=0)
    try:
        encoded = json.dumps(
            value_json,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Memory value_json must be JSON serializable") from exc
    if len(encoded) > MAX_MEMORY_VALUE_JSON_BYTES:
        raise ValueError(
            f"Memory value_json exceeds {MAX_MEMORY_VALUE_JSON_BYTES} bytes"
        )
    return value_json


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
    organization_id: str = Field(min_length=1, max_length=128)
    scope_type: MemoryScopeType
    scope_id: str
    key: str = Field(min_length=1, max_length=MAX_MEMORY_FACT_KEY_CHARS)
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

    @field_validator("organization_id")
    @classmethod
    def _normalize_organization_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("organization_id is required")
        return normalized

    @field_validator("key", mode="before")
    @classmethod
    def _normalize_key(cls, value: object) -> str:
        return validate_memory_fact_key(value)

    @field_validator("value_json")
    @classmethod
    def _validate_value_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_memory_value_json(value)

    @model_validator(mode="after")
    def _validate_scope_and_visibility(self) -> MemoryFact:
        expected_visibility: dict[MemoryScopeType, MemoryVisibility] = {
            "user": "private",
            "project": "project",
            "org": "org",
        }
        if self.visibility != expected_visibility[self.scope_type]:
            raise ValueError(
                "Memory visibility must match its user, project, or organization scope"
            )
        if self.scope_type == "org" and self.scope_id != self.organization_id:
            raise ValueError(
                "Organization memory must use its organization_id as scope_id"
            )
        return self


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
    # Discord role names are display metadata only in deployed environments.
    # Authorization binds to these immutable Discord snowflake IDs instead.
    role_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    impersonation: bool = False
    interaction_id: str | None = None
    message_id: str | None = None
    context_snippets: list[AgentContextSnippet] = Field(default_factory=list)

    @field_validator("role_ids")
    @classmethod
    def _validate_role_ids(cls, values: list[str]) -> list[str]:
        """Accept only positive decimal Discord snowflakes from the gateway."""

        normalized: list[str] = []
        for value in values:
            role_id = str(value).strip()
            if not role_id or not role_id.isdecimal() or int(role_id) <= 0:
                raise ValueError("role_ids must contain positive decimal Discord IDs")
            if role_id not in normalized:
                normalized.append(role_id)
        return normalized


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


class AgentResponsePlannerMetadata(BaseModel):
    """Audit-only planner selection retained when a response has no action plan."""

    operation_id: str | None = None
    intent: str | None = None
    planner: Literal["deterministic_regex", "live_model"]
    model_tier: ModelTier
    model: AgentModelSelection
    context_sources: list[AgentContextSource] = Field(default_factory=list)


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
    # Direct model answers have no action plan, but the API still needs the
    # model selection for its audit record. This stays out of the client
    # response because it is operational metadata rather than chat content.
    planner_metadata: AgentResponsePlannerMetadata | None = Field(
        default=None,
        exclude=True,
    )
