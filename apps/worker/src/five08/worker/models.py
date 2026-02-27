"""Typed models for worker webhook and skills processing flows."""

from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import BaseModel, Field


class WebhookEvent(BaseModel):
    """Single webhook event from EspoCRM."""

    id: str = Field(..., description="Record ID")
    name: str | None = Field(None, description="Record name")


class EspoCRMWebhookPayload(BaseModel):
    """Webhook payload wrapper."""

    events: list[WebhookEvent] = Field(..., description="List of webhook events")

    @classmethod
    def from_list(cls, data: list[Any]) -> "EspoCRMWebhookPayload":
        """Build payload model from raw webhook list."""
        events = [WebhookEvent.model_validate(event) for event in data]
        return cls(events=events)


class ContactData(BaseModel):
    """Normalized contact shape from EspoCRM."""

    id: str
    name: str | None = None
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    email_address: str | None = Field(default=None, alias="emailAddress")
    skills: str | None = None


class ExtractedSkills(BaseModel):
    """Skills extraction response."""

    skills: list[str]
    skill_attrs: dict[str, "SkillAttributes"] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str


class SkillAttributes(BaseModel):
    """Structured per-skill metadata for CRM persistence."""

    strength: int = Field(..., ge=1, le=5)


class SkillsExtractionResult(BaseModel):
    """End-to-end processing result."""

    contact_id: str
    extracted_skills: ExtractedSkills
    existing_skills: list[str]
    new_skills: list[str]
    updated_skills: list[str]
    success: bool
    error: str | None = None


class ResumeExtractedProfile(BaseModel):
    """Normalized profile fields extracted from resume text."""

    email: str | None = None
    github_username: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str


class ResumeFieldChange(BaseModel):
    """Single proposed CRM field update."""

    field: str
    label: str
    current: str | None = None
    proposed: str
    reason: str


class ResumeSkipReason(BaseModel):
    """Field extraction skip explanation for preview UX."""

    field: str
    value: str
    reason: str


class ResumeExtractionResult(BaseModel):
    """Worker output used by bot preview/confirmation flow."""

    contact_id: str
    attachment_id: str
    proposed_updates: dict[str, str]
    proposed_changes: list[ResumeFieldChange]
    skipped: list[ResumeSkipReason]
    extracted_profile: ResumeExtractedProfile
    extracted_skills: list[str] = Field(default_factory=list)
    new_skills: list[str] = Field(default_factory=list)
    success: bool
    error: str | None = None


class ResumeApplyResult(BaseModel):
    """CRM apply-phase result."""

    contact_id: str
    updated_fields: list[str]
    link_discord_applied: bool = False
    success: bool
    error: str | None = None


class GoogleFormsIntakePayload(BaseModel):
    """Google Forms member intake webhook payload (sent via Apps Script)."""

    email: str
    first_name: str
    last_name: str
    phone: str | None = None
    discord_username: str | None = None
    linkedin_url: str | None = None
    github_username: str | None = None
    submission_id: str | None = None
    submitted_at: str | None = None


class AuditEventPayload(BaseModel):
    """Inbound payload for creating a human audit event."""

    source: Literal["discord", "admin_dashboard"]
    action: str = Field(..., min_length=1)
    result: Literal["success", "denied", "error"] = "success"
    actor_provider: Literal["discord", "admin_sso"]
    actor_subject: str = Field(..., min_length=1)
    resource_type: str | None = None
    resource_id: str | None = None
    actor_display_name: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
