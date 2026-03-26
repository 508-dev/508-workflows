"""Shared typed models for resume processing flows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from five08.resume_extractor import ResumeExtractedProfile


class SkillAttributes(BaseModel):
    """Structured per-skill metadata for CRM persistence."""

    strength: int = Field(..., ge=1, le=5)


class ExtractedSkills(BaseModel):
    """Skills extraction response."""

    skills: list[str]
    skill_attrs: dict[str, SkillAttributes] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str

    @field_validator("skill_attrs", mode="before")
    @classmethod
    def _coerce_skill_attrs(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        normalized: dict[str, dict[str, int] | SkillAttributes] = {}
        for skill, payload in value.items():
            if isinstance(payload, SkillAttributes):
                normalized[str(skill)] = payload
                continue

            if isinstance(payload, dict):
                normalized[str(skill)] = payload
                continue

            strength = getattr(payload, "strength", None)
            if strength is None:
                continue
            normalized[str(skill)] = {"strength": int(strength)}
        return normalized


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


class ResumeSourceEnrichment(BaseModel):
    """External source fetch attempt used to enrich resume parsing."""

    label: str
    url: str
    origin: str
    status: str
    detail: str | None = None


class ResumeExtractionResult(BaseModel):
    """Resume extraction output used by preview/confirmation flows."""

    contact_id: str
    attachment_id: str
    proposed_updates: dict[str, Any]
    proposed_changes: list[ResumeFieldChange]
    skipped: list[ResumeSkipReason]
    source_enrichments: list[ResumeSourceEnrichment] = Field(default_factory=list)
    existing_websites: list[str] = Field(default_factory=list)
    extracted_profile: ResumeExtractedProfile
    extracted_skills: list[str] = Field(default_factory=list)
    new_skills: list[str] = Field(default_factory=list)
    success: bool
    error: str | None = None


class ResumeApplyResult(BaseModel):
    """CRM apply-phase result."""

    contact_id: str
    updated_fields: list[str]
    updated_values: dict[str, Any] = Field(default_factory=dict)
    link_discord_applied: bool = False
    success: bool
    error: str | None = None
    warning: str | None = None
