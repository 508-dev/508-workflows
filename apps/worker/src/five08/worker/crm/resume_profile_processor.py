"""Resume extraction + CRM profile update workflow."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.skills import normalize_skill
from five08.resume_extractor import ResumeProfileExtractor
from five08.queue import get_postgres_connection
from five08.worker.config import settings
from five08.worker.crm.document_processor import DocumentProcessor
from five08.worker.crm.skills_extractor import SkillsExtractor
from five08.worker.models import (
    ResumeApplyResult,
    ResumeExtractedProfile,
    ExtractedSkills,
    ResumeExtractionResult,
    ResumeFieldChange,
    ResumeSkipReason,
    SkillAttributes,
)

logger = logging.getLogger(__name__)


class ResumeEspoClient:
    """Minimal EspoCRM client wrapper for resume profile flows."""

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        return self.api.request("GET", f"Contact/{contact_id}")

    def download_attachment(self, attachment_id: str) -> bytes:
        return self.api.download_file(f"Attachment/file/{attachment_id}")

    def update_contact(self, contact_id: str, updates: dict[str, Any]) -> None:
        self.api.request("PUT", f"Contact/{contact_id}", updates)


class ResumeProfileProcessor:
    """End-to-end extraction and apply operations for uploaded resumes."""

    def __init__(self) -> None:
        self.crm = ResumeEspoClient()
        self.extractor = ResumeProfileExtractor(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.resolved_resume_ai_model,
        )
        self.skills_extractor = SkillsExtractor()
        self.document_processor = DocumentProcessor()

    def extract_profile_proposal(
        self,
        *,
        contact_id: str,
        attachment_id: str,
        filename: str,
    ) -> ResumeExtractionResult:
        """Build preview proposal from an uploaded resume attachment."""
        content_hash: str | None = None
        model_name = self._configured_model_name()
        try:
            contact = self.crm.get_contact(contact_id)
            content = self.crm.download_attachment(attachment_id)
            content_hash = self.document_processor.get_content_hash(content)
            text = self.document_processor.extract_text(content, filename)
            extracted = self.extractor.extract(text)
            model_name = extracted.source
            extracted_skills_result = self._coerce_profile_skill_result(extracted, text)
            extracted_skills = extracted_skills_result.skills
            normalized_extracted_skills = self._dedupe_normalized_skills(
                extracted_skills
            )
            existing_skills = self._parse_existing_skills(contact.get("skills"))
            existing_skill_attrs = self._parse_skill_attrs(contact.get("cSkillAttrs"))
            existing_websites = self._coerce_website_links(contact.get("cWebsiteLink"))
            existing_social_links = self._coerce_website_links(
                contact.get("cSocialLinks")
            )
            existing_lower = {item.casefold() for item in existing_skills}
            new_skills = [
                skill
                for skill in normalized_extracted_skills
                if skill.casefold() not in existing_lower
            ]
            merged_skills = self._dedupe_normalized_skills(existing_skills + new_skills)
            merged_websites = self._merge_website_links(
                existing=existing_websites,
                extracted=extracted.website_links,
            )
            merged_social_links = self._merge_website_links(
                existing=existing_social_links,
                extracted=extracted.social_links,
            )
            merged_skill_attrs = self._merge_skill_attrs(
                existing_attrs=existing_skill_attrs,
                extracted_attrs=extracted_skills_result.skill_attrs,
                merged_skills=merged_skills,
            )

            proposed_updates: dict[str, Any] = {}
            proposed_changes: list[ResumeFieldChange] = []
            skipped: list[ResumeSkipReason] = []

            self._collect_change(
                crm_field="emailAddress",
                label="Email",
                current=contact.get("emailAddress"),
                proposed=extracted.email,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
                blocked_reason="Skipped because @508.dev emails are managed separately",
                is_blocked=lambda value: value.lower().endswith("@508.dev"),
            )
            if extracted.additional_emails:
                proposed_updates["additional_emails"] = extracted.additional_emails
            self._collect_change(
                crm_field="cGitHubUsername",
                label="GitHub",
                current=contact.get("cGitHubUsername"),
                proposed=extracted.github_username,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field=settings.crm_linkedin_field,
                label="LinkedIn",
                current=contact.get(settings.crm_linkedin_field),
                proposed=extracted.linkedin_url,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="phoneNumber",
                label="Phone",
                current=contact.get("phoneNumber"),
                proposed=extracted.phone,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="cSeniority",
                label="Seniority",
                current=self._normalize_seniority(contact.get("cSeniority")),
                proposed=self._normalize_seniority(extracted.seniority_level),
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            if new_skills:
                proposed_updates["skills"] = merged_skills
                if merged_skill_attrs:
                    skill_attrs_payload = self._serialize_skill_attrs(
                        merged_skill_attrs
                    )
                    if skill_attrs_payload:
                        proposed_updates["cSkillAttrs"] = skill_attrs_payload
                proposed_changes.append(
                    ResumeFieldChange(
                        field="skills",
                        label="Skills",
                        current=(
                            self._format_skills_with_strength(
                                existing_skills, existing_skill_attrs
                            )
                            if existing_skills
                            else None
                        ),
                        proposed=self._format_skills_with_strength(
                            merged_skills, merged_skill_attrs
                        ),
                        reason="Added skills from resume extraction",
                    )
                )

            if merged_websites != existing_websites:
                proposed_updates["cWebsiteLink"] = merged_websites
                proposed_changes.append(
                    ResumeFieldChange(
                        field="cWebsiteLink",
                        label="Website",
                        current=", ".join(existing_websites),
                        proposed=", ".join(merged_websites),
                        reason="Extracted from uploaded resume",
                    )
                )

            if merged_social_links != existing_social_links:
                proposed_updates["cSocialLinks"] = merged_social_links
                proposed_changes.append(
                    ResumeFieldChange(
                        field="cSocialLinks",
                        label="Social Links",
                        current=", ".join(existing_social_links),
                        proposed=", ".join(merged_social_links),
                        reason="Extracted from uploaded resume",
                    )
                )

            # Track extraction completion before user confirmation/apply step.
            self._mark_resume_processed(contact_id)
            self._record_processing_run(
                contact_id=contact_id,
                attachment_id=attachment_id,
                content_hash=content_hash,
                model_name=model_name,
                status="succeeded",
            )

            return ResumeExtractionResult(
                contact_id=contact_id,
                attachment_id=attachment_id,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
                extracted_profile=extracted,
                extracted_skills=extracted_skills,
                new_skills=new_skills,
                success=True,
            )
        except Exception as exc:
            logger.error(
                "Resume extraction proposal failed contact_id=%s attachment_id=%s error=%s",
                contact_id,
                attachment_id,
                exc,
            )
            self._record_processing_run(
                contact_id=contact_id,
                attachment_id=attachment_id,
                content_hash=content_hash,
                model_name=model_name,
                status="failed",
                last_error=str(exc),
            )
            return ResumeExtractionResult(
                contact_id=contact_id,
                attachment_id=attachment_id,
                proposed_updates={},
                proposed_changes=[],
                skipped=[],
                extracted_profile=ResumeExtractedProfile(
                    email=None,
                    github_username=None,
                    linkedin_url=None,
                    phone=None,
                    confidence=0.0,
                    source="error",
                ),
                extracted_skills=[],
                new_skills=[],
                success=False,
                error=str(exc),
            )

    def apply_profile_updates(
        self,
        *,
        contact_id: str,
        updates: dict[str, Any],
        link_discord: dict[str, str] | None = None,
    ) -> ResumeApplyResult:
        """Apply confirmed updates to contact in CRM."""
        try:
            candidate_email = None
            normalized_updates = dict(updates)

            pre_update_contact: dict[str, Any] | None = None
            try:
                pre_update_contact = self.crm.get_contact(contact_id)
            except Exception as exc:
                logger.debug(
                    "Failed to read pre-update contact for verification contact_id=%s: %s",
                    contact_id,
                    exc,
                )

            if "emailAddress" in normalized_updates:
                candidate_email = self._normalize_email_address(
                    normalized_updates.get("emailAddress")
                )
            if "emailAddress" in normalized_updates:
                normalized_updates.pop("emailAddress", None)

            additional_emails: list[str] = []
            if "additional_emails" in normalized_updates:
                additional_emails = self._coerce_additional_emails(
                    normalized_updates.get("additional_emails")
                )
                normalized_updates.pop("additional_emails", None)

            if "skills" in normalized_updates:
                normalized_skills = self._coerce_skills_updates(
                    normalized_updates["skills"]
                )
                if normalized_skills:
                    normalized_updates["skills"] = normalized_skills
                else:
                    normalized_updates.pop("skills", None)

            if "cSkillAttrs" in normalized_updates:
                serialized_attrs = self._coerce_skill_attrs_updates(
                    normalized_updates["cSkillAttrs"]
                )
                if serialized_attrs:
                    normalized_updates["cSkillAttrs"] = serialized_attrs
                else:
                    normalized_updates.pop("cSkillAttrs", None)

            if "cWebsiteLink" in normalized_updates:
                normalized_websites = self._coerce_website_links(
                    normalized_updates["cWebsiteLink"]
                )
                if normalized_websites:
                    normalized_updates["cWebsiteLink"] = normalized_websites
                else:
                    normalized_updates.pop("cWebsiteLink", None)

            if candidate_email is not None:
                if candidate_email.endswith("@508.dev"):
                    candidate_email = None
            if candidate_email is not None:
                existing_email_data = (
                    pre_update_contact.get("emailAddressData")
                    if pre_update_contact
                    else None
                )
                email_address_data = self._build_email_address_data(
                    email_candidate=candidate_email,
                    additional_emails=additional_emails,
                    existing_email_data=existing_email_data,
                )
                if email_address_data:
                    normalized_updates["emailAddressData"] = email_address_data
            elif additional_emails:
                existing_email_data = (
                    pre_update_contact.get("emailAddressData")
                    if pre_update_contact
                    else None
                )
                email_address_data = self._build_email_address_data(
                    email_candidate=None,
                    additional_emails=additional_emails,
                    existing_email_data=existing_email_data,
                )
                if email_address_data:
                    normalized_updates["emailAddressData"] = email_address_data

            if "emailAddressData" in normalized_updates:
                normalized_email_data = self._coerce_email_address_data(
                    normalized_updates["emailAddressData"]
                )
                if normalized_email_data is not None:
                    normalized_updates["emailAddressData"] = normalized_email_data
                else:
                    normalized_updates.pop("emailAddressData", None)

            if "cSeniority" in normalized_updates:
                normalized_updates["cSeniority"] = self._normalize_seniority(
                    normalized_updates.get("cSeniority")
                )
                if not normalized_updates["cSeniority"]:
                    normalized_updates.pop("cSeniority", None)

            allowed_fields = {
                "emailAddressData",
                "cGitHubUsername",
                settings.crm_linkedin_field,
                "cSeniority",
                "phoneNumber",
                "skills",
                "cSkillAttrs",
                "cWebsiteLink",
                "cSocialLinks",
            }
            sanitized_updates: dict[str, Any] = {
                field: value
                for field, value in normalized_updates.items()
                if field in allowed_fields and value
            }
            parsed_skills_for_apply = self._normalize_skills_for_apply(
                sanitized_updates.get("skills")
            )
            if parsed_skills_for_apply is not None:
                sanitized_updates["skills"] = parsed_skills_for_apply

            if not sanitized_updates:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=[],
                    success=False,
                    error="No valid profile fields provided",
                )

            link_applied = False
            if link_discord:
                discord_user_id = str(link_discord.get("user_id", "")).strip()
                discord_username = str(link_discord.get("username", "")).strip()
                if discord_user_id and discord_username:
                    sanitized_updates["cDiscordUserID"] = discord_user_id
                    sanitized_updates["cDiscordUsername"] = (
                        f"{discord_username} (ID: {discord_user_id})"
                    )
                    link_applied = True

            if not sanitized_updates:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=[],
                    success=False,
                    error="No valid profile fields provided",
                )

            try:
                self.crm.update_contact(contact_id, sanitized_updates)
                verified_fields = self._verify_updated_fields(
                    contact_id=contact_id,
                    baseline_contact=pre_update_contact,
                    candidate_fields=list(sanitized_updates.keys()),
                )
                if verified_fields is None:
                    verified_fields = sorted(sanitized_updates.keys())
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=verified_fields,
                    link_discord_applied=link_applied,
                    success=bool(verified_fields),
                    error=None if verified_fields else "No fields were updated",
                )
            except EspoAPIError as batch_error:
                logger.warning(
                    "Resume profile batch update failed for contact_id=%s; applying fields individually. error=%s",
                    contact_id,
                    batch_error,
                )

            updated_fields: list[str] = []
            batch_errors: list[str] = []
            for field, value in sanitized_updates.items():
                try:
                    self.crm.update_contact(contact_id, {field: value})
                    updated_fields.append(field)
                except EspoAPIError as field_error:
                    batch_errors.append(f"{field}: {field_error}")
                except Exception as field_error:
                    batch_errors.append(f"{field}: {field_error}")

            if updated_fields:
                verified_fields = self._verify_updated_fields(
                    contact_id=contact_id,
                    baseline_contact=pre_update_contact,
                    candidate_fields=updated_fields,
                )
                if verified_fields is not None:
                    updated_fields = verified_fields

            if len(updated_fields) == len(sanitized_updates):
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=sorted(updated_fields),
                    link_discord_applied=link_applied,
                    success=True,
                )

            if updated_fields:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=sorted(updated_fields),
                    link_discord_applied=link_applied,
                    success=False,
                    error="; ".join(batch_errors)
                    if batch_errors
                    else "Some fields did not persist after update",
                )

            return ResumeApplyResult(
                contact_id=contact_id,
                updated_fields=sorted(sanitized_updates.keys()),
                link_discord_applied=link_applied,
                success=False,
                error="; ".join(batch_errors)
                if batch_errors
                else "No fields were updated",
            )
        except EspoAPIError as exc:
            logger.error("EspoCRM apply failed contact_id=%s error=%s", contact_id, exc)
            return ResumeApplyResult(
                contact_id=contact_id,
                updated_fields=[],
                success=False,
                error=str(exc),
            )
        except Exception as exc:
            logger.error(
                "Unexpected apply error contact_id=%s error=%s", contact_id, exc
            )
            return ResumeApplyResult(
                contact_id=contact_id,
                updated_fields=[],
                success=False,
                error=str(exc),
            )

    def _normalize_skills_for_apply(self, value: Any) -> list[str] | None:
        """Normalize optional skills updates into an array-shaped payload."""
        if value is None:
            return None

        if isinstance(value, str):
            raw_skills = [item.strip() for item in value.split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_skills = [str(item).strip() for item in value]
        else:
            return None

        normalized: list[str] = []
        seen: set[str] = set()
        for skill in raw_skills:
            if not skill:
                continue
            key = self._normalize_skill(skill)
            if key is None:
                continue
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        return normalized if normalized else None

    @staticmethod
    def _normalize_compare_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, sort_keys=True, separators=(",", ":"))
            except Exception:
                return str(value)
        if isinstance(value, bool):
            return str(value).lower()
        return str(value).strip()

    def _coerce_skills_updates(self, value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            raw_skills = [item for item in value]
        elif isinstance(value, str):
            raw_skills = [item.strip() for item in value.split(",") if item.strip()]
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_skill in raw_skills:
            skill = self._normalize_skill(str(raw_skill).strip())
            if not skill:
                continue
            key = skill.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(skill)
        return normalized

    def _verify_updated_fields(
        self,
        *,
        contact_id: str,
        baseline_contact: dict[str, Any] | None,
        candidate_fields: list[str],
    ) -> list[str] | None:
        if baseline_contact is None:
            return None

        baseline: dict[str, str] = {}
        for field in candidate_fields:
            baseline[field] = self._normalize_compare_value(
                baseline_contact.get(field, "")
            )

        try:
            after_contact = self.crm.get_contact(contact_id)
        except Exception as exc:
            logger.debug(
                "Failed to read post-update contact for verification contact_id=%s: %s",
                contact_id,
                exc,
            )
            return None
        if after_contact is baseline_contact:
            return None
        if not isinstance(after_contact, dict):
            return None

        changed_fields: list[str] = []
        for field in candidate_fields:
            after_value = self._normalize_compare_value(after_contact.get(field, ""))
            if after_value != baseline.get(field, ""):
                changed_fields.append(field)
        if not changed_fields:
            return candidate_fields
        return changed_fields

    def _collect_change(
        self,
        *,
        crm_field: str,
        label: str,
        current: Any,
        proposed: str | None,
        proposed_updates: dict[str, Any],
        proposed_changes: list[ResumeFieldChange],
        skipped: list[ResumeSkipReason],
        blocked_reason: str | None = None,
        is_blocked: Callable[[str], bool] | None = None,
    ) -> None:
        if not proposed:
            return

        if callable(is_blocked) and is_blocked(proposed):
            skipped.append(
                ResumeSkipReason(
                    field=crm_field,
                    value=proposed,
                    reason=blocked_reason or "Update blocked by policy",
                )
            )
            return

        current_value = str(current).strip() if current is not None else None
        if current_value and current_value == proposed:
            return

        proposed_updates[crm_field] = proposed
        proposed_changes.append(
            ResumeFieldChange(
                field=crm_field,
                label=label,
                current=current_value,
                proposed=proposed,
                reason="Extracted from uploaded resume",
            )
        )

    def _parse_existing_skills(self, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, list):
            raw_skills = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, (tuple, set)):
            raw_skills = [str(item).strip() for item in value if str(item).strip()]
        else:
            raw_skills = [
                item.strip() for item in str(value).split(",") if item.strip()
            ]
        return self._dedupe_normalized_skills(raw_skills)

    @staticmethod
    def _normalize_seniority(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace("_", "-")
        if not normalized:
            return "unknown"
        if normalized in {
            "jr",
            "junior",
            "intern",
            "internship",
            "entry",
            "entry-level",
            "entry level",
        }:
            return "junior"
        if normalized in {"mid", "mid-level", "midlevel", "intermediate"}:
            return "midlevel"
        if normalized in {
            "senior",
            "sr",
            "sr. engineer",
            "senior engineer",
            "lead",
            "lead engineer",
            "lead engineer/tech lead",
            "tech lead",
        }:
            return "senior"
        if normalized in {
            "staff",
            "staff+",
            "staff and beyond",
            "principal",
            "principal engineer",
        }:
            return "staff"
        if "lead " in normalized and "engineer" in normalized:
            return "senior"
        if normalized.startswith("lead "):
            return "senior"
        return "unknown"

    def _format_skills_with_strength(
        self,
        skills: list[str],
        attrs: dict[str, int],
    ) -> str:
        deduped_skills = self._dedupe_normalized_skills(skills)
        formatted: list[str] = []
        for raw_skill in deduped_skills:
            skill = raw_skill.strip()
            if not skill:
                continue
            strength = attrs.get(skill.casefold())
            if strength:
                formatted.append(f"{skill} ({strength})")
            else:
                formatted.append(skill)
        return ", ".join(formatted)

    def _dedupe_normalized_skills(self, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            raw_skills = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_skills = [str(item).strip() for item in value if str(item).strip()]
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_skill in raw_skills:
            canonical = self._normalize_skill(raw_skill)
            if not canonical:
                continue
            key = canonical.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(canonical)
        return normalized

    def _normalize_skill(self, value: Any) -> str | None:
        normalized = normalize_skill(str(value))
        return normalized or None

    def _coerce_profile_skill_result(
        self,
        extracted: ResumeExtractedProfile,
        resume_text: str,
    ) -> ExtractedSkills:
        normalized_attrs: dict[str, SkillAttributes] = {}
        skills = extracted.skills or []
        normalized_skills = self._dedupe_normalized_skills(skills)
        for raw_skill, raw_attr in getattr(extracted, "skill_attrs", {}).items():
            skill = self._normalize_skill(raw_skill)
            if not skill:
                continue
            try:
                strength = int(float(raw_attr))
            except Exception:
                continue
            if not 1 <= strength <= 5:
                continue
            normalized_attrs[skill.casefold()] = SkillAttributes(strength=strength)

        if not normalized_attrs and isinstance(skills, list) and skills:
            for raw_skill in skills:
                skill = self._normalize_skill(raw_skill)
                if not skill:
                    continue
                key = skill.casefold()
                if key in normalized_attrs:
                    continue
                normalized_attrs[key] = SkillAttributes(strength=3)

        if normalized_attrs or skills:
            return ExtractedSkills(
                skills=normalized_skills,
                skill_attrs=normalized_attrs,
                confidence=extracted.confidence,
                source=extracted.source,
            )

        fallback = self.skills_extractor.extract_skills(resume_text)
        fallback_skills = self._dedupe_normalized_skills(fallback.skills)
        if fallback_skills or fallback.skill_attrs:
            return ExtractedSkills(
                skills=fallback_skills,
                skill_attrs=fallback.skill_attrs,
                confidence=fallback.confidence,
                source=fallback.source,
            )
        return fallback

    def _parse_skill_attrs(self, value: Any) -> dict[str, int]:
        if value is None:
            return {}

        candidate = value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return {}
            try:
                candidate = json.loads(raw)
            except Exception:
                return {}

        if not isinstance(candidate, dict):
            return {}

        parsed: dict[str, int] = {}
        for raw_skill, raw_payload in candidate.items():
            normalized = self._normalize_skill(raw_skill)
            if not normalized:
                continue
            skill = normalized.casefold()

            strength_value = raw_payload
            if isinstance(raw_payload, dict):
                strength_value = raw_payload.get("strength")

            try:
                strength = int(float(strength_value))
            except Exception:
                strength = 0
            parsed[skill] = max(1, min(5, strength)) if strength else 0

        return {skill: strength for skill, strength in parsed.items() if strength > 0}

    def _merge_skill_attrs(
        self,
        *,
        existing_attrs: dict[str, int],
        extracted_attrs: dict[str, SkillAttributes],
        merged_skills: list[str],
    ) -> dict[str, int]:
        merged: dict[str, int] = dict(existing_attrs)

        for skill, attrs in extracted_attrs.items():
            key = self._normalize_skill(skill)
            if not key:
                continue
            key = key.casefold()
            if key:
                merged[key] = max(1, min(5, int(attrs.strength)))

        return merged

    def _coerce_skill_attrs_updates(self, value: Any) -> str | None:
        if value is None:
            return None

        candidate = value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                candidate = json.loads(raw)
            except Exception:
                return None

        if not isinstance(candidate, dict):
            return None

        parsed: dict[str, int] = {}
        for raw_skill, raw_payload in candidate.items():
            skill = self._normalize_skill(raw_skill)
            if not skill:
                continue
            strength_source = raw_payload
            if isinstance(raw_payload, dict):
                strength_source = raw_payload.get("strength")
            try:
                strength = int(float(strength_source))
            except Exception:
                continue
            if not 1 <= strength <= 5:
                continue
            parsed[skill] = strength

        return self._serialize_skill_attrs(parsed)

    def _serialize_skill_attrs(self, attrs: dict[str, int]) -> str | None:
        normalized: dict[str, dict[str, int]] = {}
        for raw_skill, raw_strength in attrs.items():
            skill = self._normalize_skill(raw_skill)
            if not skill:
                continue
            try:
                strength = int(float(raw_strength))
            except Exception:
                continue
            clamped = max(1, min(5, strength))
            normalized[skill] = {"strength": clamped}
        if not normalized:
            return None
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    def _coerce_website_links(self, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            raw_values = [item for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_values = list(value)
        else:
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            normalized_link = self._normalize_website_url(raw_value.strip())
            if normalized_link is None:
                continue
            dedupe_key = normalized_link.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(normalized_link)

        return normalized

    def _merge_website_links(
        self, *, existing: list[str], extracted: list[str]
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()

        for value in existing:
            if not isinstance(value, str):
                continue
            normalized = self._normalize_website_url(value)
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(normalized)

        for value in extracted:
            if not isinstance(value, str):
                continue
            normalized = self._normalize_website_url(value)
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(normalized)

        return merged

    @staticmethod
    def _normalize_website_url(value: str) -> str | None:
        candidate = value.strip().strip(")]},.;:")
        if not candidate:
            return None

        if candidate.lower().startswith("www."):
            candidate = f"https://{candidate}"
        if not candidate.startswith(("http://", "https://")):
            return None

        try:
            parsed = urlsplit(candidate)
        except Exception:
            return None

        if "@" in parsed.netloc:
            return None

        host = parsed.hostname or ""
        if host.lower().startswith("www."):
            host = host[4:]
        if not host:
            return None

        normalized_netloc = parsed.netloc
        lower_netloc = parsed.netloc.lower()
        if lower_netloc.startswith("www."):
            normalized_netloc = parsed.netloc[4:]
        elif host and lower_netloc.startswith(f"www.{host}"):
            normalized_netloc = parsed.netloc.replace(parsed.netloc[:4], "", 1)

        parsed = parsed._replace(netloc=normalized_netloc)
        normalized = parsed.geturl().rstrip("/")
        if normalized.startswith("https://www."):
            normalized = normalized.replace("https://www.", "https://", 1)
        elif normalized.startswith("http://www."):
            normalized = normalized.replace("http://www.", "http://", 1)
        return normalized

    def _normalize_email_address(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if not normalized or "@" not in normalized:
            return None
        return normalized

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _coerce_additional_emails(self, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                return []
            try:
                parsed = json.loads(raw_value)
            except Exception:
                parsed = [raw_value]
            else:
                parsed = parsed if isinstance(parsed, list) else [parsed]
        elif isinstance(value, (list, tuple, set)):
            parsed = list(value)
        else:
            parsed = [value]

        deduped: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            normalized = self._normalize_email_address(item)
            if normalized is None:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def _coerce_email_address_data(self, value: Any) -> list[dict[str, Any]] | None:
        parsed = self._parse_email_address_data(value)
        return parsed if parsed else None

    def _parse_email_address_data(self, value: Any) -> list[dict[str, Any]]:
        if value is None:
            return []

        candidate = value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            try:
                candidate = json.loads(raw)
            except Exception:
                return []

        if not isinstance(candidate, list):
            if isinstance(candidate, dict):
                candidate = [candidate]
            else:
                return []

        parsed: list[dict[str, Any]] = []
        for entry in candidate:
            if not isinstance(entry, dict):
                continue
            raw_email = str(
                entry.get("lower")
                or entry.get("emailAddress")
                or entry.get("email")
                or ""
            ).strip()
            normalized_email = self._normalize_email_address(raw_email)
            if normalized_email is None:
                continue

            parsed.append(
                {
                    "emailAddress": str(
                        entry.get("emailAddress", normalized_email)
                    ).strip(),
                    "lower": normalized_email,
                    "primary": self._coerce_bool(entry.get("primary")),
                    "optOut": self._coerce_bool(entry.get("optOut")),
                    "invalid": self._coerce_bool(entry.get("invalid")),
                }
            )

        return parsed

    def _build_email_address_data(
        self,
        *,
        email_candidate: str | None,
        additional_emails: list[str] | None = None,
        existing_email_data: Any,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}

        for entry in self._parse_email_address_data(existing_email_data):
            merged[entry["lower"]] = entry
            merged[entry["lower"]]["emailAddress"] = (
                str(entry.get("emailAddress", entry["lower"])).strip().lower()
            )

        candidate_lower = self._normalize_email_address(email_candidate)
        if candidate_lower is None:
            if not additional_emails:
                return list(merged.values())
            candidate_lower = None

        for _, entry in merged.items():
            entry["primary"] = False

        if candidate_lower is not None:
            merged[candidate_lower] = {
                "emailAddress": candidate_lower,
                "lower": candidate_lower,
                "primary": True,
                "optOut": False,
                "invalid": False,
            }

        for extra in additional_emails or []:
            normalized_extra = self._normalize_email_address(extra)
            if normalized_extra is None or normalized_extra == candidate_lower:
                continue
            if normalized_extra in merged:
                merged[normalized_extra]["primary"] = False
                continue
            merged[normalized_extra] = {
                "emailAddress": normalized_extra,
                "lower": normalized_extra,
                "primary": False,
                "optOut": False,
                "invalid": False,
            }

        return list(merged.values())

    def _mark_resume_processed(self, contact_id: str) -> None:
        """Best-effort update for extraction completion tracking."""
        processed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.crm.update_contact(contact_id, {"cResumeLastProcessed": processed_at})
        except Exception as exc:
            logger.warning(
                "Failed to update cResumeLastProcessed contact_id=%s error=%s",
                contact_id,
                exc,
            )

    def _configured_model_name(self) -> str:
        """Model identity used for idempotency/ledger keys."""
        if settings.openai_api_key:
            return settings.resolved_resume_ai_model
        return "heuristic"

    def _record_processing_run(
        self,
        *,
        contact_id: str,
        attachment_id: str,
        content_hash: str | None,
        model_name: str,
        status: str,
        last_error: str | None = None,
    ) -> None:
        """Persist one processing result keyed by contact+attachment+version+model."""
        query = """
            INSERT INTO resume_processing_runs (
                contact_id,
                attachment_id,
                content_hash,
                extractor_version,
                model_name,
                status,
                last_error,
                processed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (contact_id, attachment_id, extractor_version, model_name)
            DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                status = EXCLUDED.status,
                last_error = EXCLUDED.last_error,
                processed_at = NOW();
        """
        try:
            with get_postgres_connection(settings) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        query,
                        (
                            contact_id,
                            attachment_id,
                            content_hash,
                            settings.resume_extractor_version,
                            model_name,
                            status,
                            last_error,
                        ),
                    )
        except Exception as exc:
            logger.warning(
                "Failed to persist resume processing run contact_id=%s attachment_id=%s "
                "version=%s model=%s status=%s error=%s",
                contact_id,
                attachment_id,
                settings.resume_extractor_version,
                model_name,
                status,
                exc,
            )
