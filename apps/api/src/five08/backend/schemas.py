"""Pydantic request schemas for the backend API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from five08.agent import AgentIdentityContext
from five08.automation import (
    AutomationAction,
    AutomationCondition,
    AutomationRuleMode,
)


class ResumeExtractRequest(BaseModel):
    """Request schema for queued resume extraction."""

    contact_id: str
    attachment_id: str
    filename: str
    refresh_token: str | None = None


class ResumeApplyRequest(BaseModel):
    """Request schema for queued resume apply updates."""

    contact_id: str
    updates: dict[str, Any]
    link_discord: dict[str, str] | None = None


class DiscordLinkCreateRequest(BaseModel):
    """Payload for creating one-time admin deep links from Discord commands."""

    discord_user_id: str
    next_path: str | None = None
    discord_display_name: str | None = None
    discord_roles: list[str] = Field(default_factory=list)


class AgentConfirmationRequest(BaseModel):
    """Payload for confirming or canceling a frozen agent plan."""

    context: AgentIdentityContext
    confirm: bool = True


class DashboardAssignOnboarderRequest(BaseModel):
    """Payload for assigning an onboarder from the dashboard."""

    onboarder: str


class DashboardOnboardingStatusRequest(BaseModel):
    """Payload for updating one dashboard onboarding status."""

    status: str


class DashboardOnboardingEmailDraftRequest(BaseModel):
    """Payload for drafting one dashboard onboarding email."""

    has_contributed: bool = False
    discord_joined: Literal["yes", "no", "unknown"] = "unknown"
    agreement_signed: Literal["yes", "no", "unknown"] = "unknown"


class DashboardOnboardingEmailSendRequest(BaseModel):
    """Payload for sending one reviewed dashboard onboarding email."""

    markdown_body: str
    has_contributed: bool = False
    discord_joined: Literal["yes", "no", "unknown"] = "unknown"
    agreement_signed: Literal["yes", "no", "unknown"] = "unknown"


class DashboardGigStatusRequest(BaseModel):
    """Payload for updating one dashboard gig status."""

    status: str


class DashboardJobLeadReviewRequest(BaseModel):
    """Payload for reviewing one sourced dashboard job lead."""

    status: Literal["approved", "rejected"]


class DashboardJobLeadPostRequest(BaseModel):
    """Payload for posting one approved sourced job lead to Discord."""

    channel_id: str | None = None
    tags: str | None = None
    engagement_status: Literal["lead", "recruiting"] = "lead"


class DashboardJobChannelUpdateRequest(BaseModel):
    """Payload for registering or updating one Discord job channel."""

    posting_type: Literal[
        "part_time",
        "full_time",
        "part_time_or_full_time",
        "unknown",
    ] = "part_time"


class DashboardJobLeadSyncRequest(BaseModel):
    """Payload for enqueuing a sourced job lead scrape."""

    source: str = "hackernews_who_is_hiring"
    story_id: int | None = Field(default=None, ge=1)


class DashboardProjectStatusRequest(BaseModel):
    """Payload for updating one ERPNext Project status."""

    status: str


class DashboardBulkProjectUpdateRequest(BaseModel):
    """Payload for bulk ERPNext Project field updates."""

    project_ids: list[str]
    status: str | None = None
    project_type: str | None = None


class DashboardProjectUserRequest(BaseModel):
    """Payload for adding one ERPNext User to a Project roster."""

    user: str
    candidate_id: str | None = None
    activity_type: str | None = None
    billing_rate: float | None = None
    costing_rate: float | None = None


class DashboardEngineerSetupRequest(BaseModel):
    """Payload for setting up one ERPNext engineer account."""

    email: str
    first_name: str
    middle_name: str | None = None
    last_name: str | None = None
    country: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    date_of_joining: str | None = None
    personal_email: str | None = None
    prefered_email: str | None = None


class DashboardProjectUserRemoveRequest(BaseModel):
    """Payload for removing one ERPNext User from a Project roster."""

    user: str


class DashboardProjectHistoricalMemberRequest(BaseModel):
    """Payload for adding one local historical Project roster member."""

    person: str
    candidate_id: str | None = None


class DashboardProjectHistoricalMemberRemoveRequest(BaseModel):
    """Payload for removing one local historical Project roster member."""

    source_user_id: str


class DashboardProjectWikiMatchRequest(BaseModel):
    """Payload for saving a manual project-to-wiki match decision."""

    status: str
    row_key: str | None = None


class DashboardProjectCreateRequest(BaseModel):
    """Payload for creating a Customer-backed ERPNext Project."""

    project_name: str
    customer_mode: Literal["new", "existing"] = "new"
    customer_name: str | None = None
    customer: str | None = None
    account_manager: str | None = None
    default_billing_currency: str | None = "USD"
    default_cost_center: str | None = "Projects - 5"
    activity_type: str | None = None
    customer_details: str | None = None
    customer_website: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    address_country: str | None = None
    address_postal_code: str | None = None
    contact: str | None = None
    contact_first_name: str | None = None
    contact_last_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_mobile: str | None = None


class DashboardGigApplicationStatusRequest(BaseModel):
    """Payload for updating one dashboard gig candidate/application status."""

    status: str


class DashboardGigApplicationCreateRequest(BaseModel):
    """Payload for adding one CRM-verified gig candidate/application."""

    crm_profile: str = Field(min_length=1, max_length=500)


class DashboardConfigurationUpdateRequest(BaseModel):
    """Payload for updating one admin-managed configuration value."""

    value: str | bool | int | float | None = None
    clear: bool = False


class DashboardProjectPaymentRuleCreateRequest(BaseModel):
    """Operator-managed declarative rule for one ERP bank receipt project."""

    project_id: str = Field(min_length=1, max_length=64)
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    mode: AutomationRuleMode = AutomationRuleMode.SUGGEST
    enabled: bool = True
    conditions: list[AutomationCondition] = Field(default_factory=list)
    actions: list[AutomationAction] = Field(min_length=1)


class DashboardProjectPaymentRuleUpdateRequest(
    DashboardProjectPaymentRuleCreateRequest
):
    """Version-checked replacement for one declarative payment rule."""

    expected_version: int = Field(ge=1)


class DashboardProjectPaymentRuleDisableRequest(BaseModel):
    """Version-checked soft disable to retain payment rule audit history."""

    expected_version: int = Field(ge=1)


class DashboardProjectPaymentSuggestionReviewRequest(BaseModel):
    """Empty acknowledgement for a server-side suggestion state transition.

    The action id and decision are deliberately supplied by the route, rather
    than trusting a client-controlled target, amount, or project payload.
    """

    model_config = ConfigDict(extra="forbid")
