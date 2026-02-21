"""Typed models for worker webhook and skills processing flows."""

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
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str


class SkillsExtractionResult(BaseModel):
    """End-to-end processing result."""

    contact_id: str
    extracted_skills: ExtractedSkills
    existing_skills: list[str]
    new_skills: list[str]
    updated_skills: list[str]
    success: bool
    error: str | None = None
