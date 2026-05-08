"""Typed models for the agent gateway contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
ModelTier = Literal["fast", "strong", "reasoning"]


class AgentIdentityContext(BaseModel):
    """Resolved actor and tenant context for one agent request."""

    discord_user_id: str
    internal_user_id: str | None = None
    organization_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    guild_id: str | None = None
    channel_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    impersonation: bool = False
    interaction_id: str | None = None
    message_id: str | None = None


class AgentRequest(BaseModel):
    """Natural-language agent request from Discord or another client."""

    message: str
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
    """A model-proposed, policy-checked plan that may be executed later."""

    plan_id: str
    intent: str
    model_tier: ModelTier
    model: AgentModelSelection
    actions: list[AgentToolAction] = Field(default_factory=list)
    human_summary: str
    requires_confirmation: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


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
        "denied",
        "failed",
    ]
    plan: AgentPlan | None = None
    results: list[AgentExecutionResult] = Field(default_factory=list)
    message: str
    clarification_question: str | None = None
