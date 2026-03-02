"""Typed models for worker webhook and skills processing flows."""

from datetime import datetime
from typing import Literal
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


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


class DocusealSubmitter(BaseModel):
    """Single submitter entry from a Docuseal webhook payload."""

    class Template(BaseModel):
        """Template metadata attached to a Docuseal submitter."""

        id: int | None = None

    id: int
    email: str
    status: str
    submission_id: int | None = None
    completed_at: str | None = None
    name: str | None = None
    external_id: str | None = None
    template: Template | None = None


class DocusealWebhookPayload(BaseModel):
    """Docuseal form.completed webhook payload."""

    event_type: str
    timestamp: str
    data: DocusealSubmitter


class GoogleFormsIntakePayload(BaseModel):
    """Google Forms member intake webhook payload (sent via Apps Script)."""

    model_config = {"populate_by_name": True}

    email: str = Field(..., validation_alias=AliasChoices("email", "emailAddress"))
    first_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("first_name", "firstName", "first name"),
    )
    last_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("last_name", "lastName", "last name"),
    )
    name: str | None = Field(
        default=None, validation_alias=AliasChoices("name", "full name", "full_name")
    )
    phone: str | None = Field(
        default=None,
        validation_alias=AliasChoices("phone", "phone_number", "phoneNumber"),
    )
    discord_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "discord_username", "discord", "discordUsername", "cDiscordUsername"
        ),
    )
    linkedin_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "linkedin_url", "linkedin", "linkedIn", "cLinkedIn", "linkedInProfile"
        ),
    )
    github_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "github_username", "github", "github_profile_link", "githubProfile"
        ),
    )
    address_country: str | None = Field(
        default=None,
        validation_alias=AliasChoices("address_country", "addressCountry"),
    )
    primary_role: str | None = Field(
        default=None,
        validation_alias=AliasChoices("primary_role", "primaryRole", "cRoles"),
    )
    seniority_level: str | None = Field(
        default=None,
        validation_alias=AliasChoices("seniority_level", "seniority", "cSeniority"),
    )
    availability: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "availability", "current_availability", "cAvailableTimes"
        ),
    )
    rate_range: str | None = Field(
        default=None, validation_alias=AliasChoices("rate_range", "rate", "cRateRange")
    )
    referred_by: str | None = Field(
        default=None, validation_alias=AliasChoices("referred_by", "cReferredBy")
    )
    primary_skills_interests: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "primary_skills_interests", "primary_skills_and_interests"
        ),
    )
    top_question_about_508: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "top_question_about_508",
            "top_question",
            "top_question_about_508_dev",
        ),
    )
    resume_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "resume_url", "resume", "resumeLink", "resumeUrl"
        ),
    )
    resume_file_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("resume_file_name", "resumeFileName"),
    )
    form_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("form_id", "formId", "google_forms_form_id"),
    )
    website_link: str | None = Field(
        default=None,
        validation_alias=AliasChoices("website", "website_link", "websiteLink"),
    )
    skill_proficiency_next_js: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_next_js", "Skill Proficiency [Next.js]"
        ),
    )
    skill_proficiency_react_native_expo: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_react_native_expo",
            "Skill Proficiency [React Native / Expo]",
        ),
    )
    skill_proficiency_supabase: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_supabase", "Skill Proficiency [Supabase]"
        ),
    )
    skill_proficiency_ai_ml_engineering: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_ai_ml_engineering",
            "Skill Proficiency [AI/ML Engineering]",
        ),
    )
    skill_proficiency_python_django_fastapi: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_python_django_fastapi",
            "Skill Proficiency [Python / Django / FastAPI]",
        ),
    )
    skill_proficiency_wordpress: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_wordpress", "Skill Proficiency [WordPress]"
        ),
    )
    skill_proficiency_devops: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_devops", "Skill Proficiency [DevOps]"
        ),
    )
    skill_proficiency_crypto_blockchain: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_crypto_blockchain",
            "Skill Proficiency [Crypto/Blockchain]",
        ),
    )
    skill_proficiency_chat_bots: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_chat_bots",
            "Skill Proficiency [Chat bots (Line, Telegram, etc,...)]",
        ),
    )
    skill_proficiency_unity_video_game: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_unity_video_game",
            "Skill Proficiency [Unity / Video Game Development]",
        ),
    )
    skill_proficiency_project_management: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_project_management",
            "Skill Proficiency [Project Management]",
        ),
    )
    skill_proficiency_client_management: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_client_management",
            "Skill Proficiency [Client Management]",
        ),
    )
    skill_proficiency_sales_marketing: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_sales_marketing",
            "Skill Proficiency [Sales / Marketing]",
        ),
    )
    skill_proficiency_internal_business_development: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skill_proficiency_internal_business_development",
            "Skill Proficiency [Internal business development (HR, etc,...)]",
        ),
    )
    submission_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("submission_id", "submissionId"),
    )
    submitted_at: str | None = Field(
        default=None,
        validation_alias=AliasChoices("submitted_at", "submittedAt"),
    )

    @classmethod
    @field_validator("email")
    def validate_email(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("email is required")
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("email is required")
        return normalized

    @field_validator("first_name", "last_name", "name", mode="before")
    @classmethod
    def normalize_name_fields(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return str(value).strip() or None

    @field_validator(
        "phone",
        "discord_username",
        "linkedin_url",
        "github_username",
        "address_country",
        "primary_role",
        "seniority_level",
        "availability",
        "rate_range",
        "referred_by",
        "primary_skills_interests",
        "top_question_about_508",
        "resume_url",
        "resume_file_name",
        "form_id",
        "website_link",
        "skill_proficiency_next_js",
        "skill_proficiency_react_native_expo",
        "skill_proficiency_supabase",
        "skill_proficiency_ai_ml_engineering",
        "skill_proficiency_python_django_fastapi",
        "skill_proficiency_wordpress",
        "skill_proficiency_devops",
        "skill_proficiency_crypto_blockchain",
        "skill_proficiency_chat_bots",
        "skill_proficiency_unity_video_game",
        "skill_proficiency_project_management",
        "skill_proficiency_client_management",
        "skill_proficiency_sales_marketing",
        "skill_proficiency_internal_business_development",
        "submission_id",
        "submitted_at",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return str(value).strip() or None

    @model_validator(mode="before")
    @classmethod
    def coerce_full_name(cls, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("GoogleFormsIntakePayload expects an object payload")

        normalized = dict(payload)
        name_aliases = {
            "name": "name",
            "full name": "name",
            "full_name": "name",
            "first name": "first_name",
            "first": "first_name",
            "last name": "last_name",
            "last": "last_name",
            "lastname": "last_name",
            "email address": "email",
            "emailaddress": "email",
            "current country": "address_country",
            "current country (from form)": "address_country",
            "primary role": "primary_role",
            "primary roles": "primary_role",
            "primary_role": "primary_role",
            "seniority": "seniority_level",
            "seniority level": "seniority_level",
            "current availability": "availability",
            "current availability for co-op projects": "availability",
            "current availability for co-op projects (in weekly hours)": "availability",
            "availability": "availability",
            "primary skills and interests": "primary_skills_interests",
            "primary_skills_and_interests": "primary_skills_interests",
            "top question about 508": "top_question_about_508",
            "top question": "top_question_about_508",
            "what is your top question about 508": "top_question_about_508",
            "top_question_about_508_dev": "top_question_about_508",
            "top_question": "top_question_about_508",
            "how did you hear about 508.dev": "referred_by",
            "how did you hear about 508.dev?": "referred_by",
            "how did you hear about 508": "referred_by",
            "referred by": "referred_by",
            "discord_username": "discord_username",
            "linkedin_url": "linkedin_url",
            "github_username": "github_username",
            "website": "website_link",
            "website link": "website_link",
            "website_link": "website_link",
            "rate range": "rate_range",
            "rate_range": "rate_range",
            "rate": "rate_range",
            "submission id": "submission_id",
            "submission_id": "submission_id",
            "submitted at": "submitted_at",
            "submitted_at": "submitted_at",
            "submission timestamp": "submitted_at",
            "form id": "form_id",
            "form_id": "form_id",
            "google form id": "form_id",
            "google_forms_form_id": "form_id",
        }

        transformed: dict[str, Any] = {}
        for key, value in normalized.items():
            if not isinstance(key, str):
                continue
            mapped_key = name_aliases.get(key.strip().lower(), key)
            transformed[mapped_key] = value

        raw_email_value = normalized.get("email")
        if isinstance(raw_email_value, str) and raw_email_value.strip():
            transformed["email"] = raw_email_value.strip().lower()
        else:
            for raw_key, raw_value in normalized.items():
                if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                    continue
                if raw_key.strip().lower() in {
                    "emailaddress",
                    "email address",
                    "email_address",
                }:
                    if raw_value.strip():
                        transformed["email"] = raw_value.strip().lower()
                        break

        name_value = transformed.get("name")
        if name_value and (
            not transformed.get("first_name") or not transformed.get("last_name")
        ):
            name_parts = str(name_value).split()
            if len(name_parts) > 1:
                transformed["first_name"] = (
                    transformed.get("first_name") or name_parts[0].strip()
                )
                transformed["last_name"] = (
                    transformed.get("last_name") or " ".join(name_parts[1:]).strip()
                )

        if transformed.get("submission_id") is not None:
            transformed["submission_id"] = str(transformed["submission_id"]).strip()

        if not transformed.get("first_name") or not transformed.get("last_name"):
            raise ValueError("first_name and last_name are required")
        return transformed


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
