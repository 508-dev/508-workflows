"""Resume extraction + CRM profile update workflow."""

from __future__ import annotations

import ast
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from psycopg import connect

from five08.clients.espo import EspoAPIError, EspoClient
from five08.crm_normalization import (
    ROLE_NORMALIZATION_MAP,
    normalized_website_identity_key,
    normalize_city,
    normalize_country,
    normalize_role,
    normalize_roles,
    normalize_state,
    normalize_seniority,
    normalize_timezone,
    normalize_website_url,
)
from five08.skills import (
    DISALLOWED_RESUME_SKILLS,
    normalize_skill,
    normalize_skill_list,
    normalize_skill_payload,
)
from five08.resume_document_processor import DocumentProcessor
from five08.resume_extractor import ResumeExtractedProfile, ResumeProfileExtractor
from five08.resume_processing_models import (
    ResumeApplyResult,
    ExtractedSkills,
    ResumeExtractionResult,
    ResumeFieldChange,
    ResumeSkipReason,
    SkillAttributes,
)
from five08.resume_skills_extractor import SkillsExtractor

logger = logging.getLogger(__name__)
DEFAULT_SKILL_STRENGTH = 3


@dataclass(frozen=True)
class ResumeProcessorConfig:
    """Runtime configuration required by the shared resume processor."""

    espo_base_url: str
    espo_api_key: str
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    resume_model: str = "gpt-5-mini"
    resume_extractor_max_tokens: int = 2000
    crm_linkedin_field: str = "cLinkedIn"
    allowed_file_extensions: set[str] = field(
        default_factory=lambda: {"pdf", "docx", "txt"}
    )
    max_file_size_mb: int = 10
    resume_extractor_version: str = "v1"
    postgres_url: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> "ResumeProcessorConfig":
        allowed_extensions = getattr(settings, "allowed_file_extensions", None)
        if not isinstance(allowed_extensions, set):
            raw_allowed_types = str(getattr(settings, "allowed_file_types", "")).strip()
            allowed_extensions = {
                ext.strip().lower()
                for ext in raw_allowed_types.split(",")
                if ext.strip()
            }
        if not allowed_extensions:
            allowed_extensions = {"pdf", "docx", "txt"}

        resume_model = (
            str(getattr(settings, "resolved_resume_ai_model", "")).strip()
            or str(getattr(settings, "resume_ai_model", "")).strip()
            or str(getattr(settings, "openai_model", "")).strip()
            or "gpt-5-mini"
        )

        return cls(
            espo_base_url=str(getattr(settings, "espo_base_url")),
            espo_api_key=str(getattr(settings, "espo_api_key")),
            openai_api_key=getattr(settings, "openai_api_key", None),
            openai_base_url=getattr(settings, "openai_base_url", None),
            resume_model=resume_model,
            resume_extractor_max_tokens=int(
                getattr(settings, "resume_extractor_max_tokens", 2000)
            ),
            crm_linkedin_field=str(
                getattr(settings, "crm_linkedin_field", "cLinkedIn")
            ).strip()
            or "cLinkedIn",
            allowed_file_extensions={
                ext.strip().lower() for ext in allowed_extensions if str(ext).strip()
            },
            max_file_size_mb=int(getattr(settings, "max_file_size_mb", 10)),
            resume_extractor_version=str(
                getattr(settings, "resume_extractor_version", "v1")
            ).strip()
            or "v1",
            postgres_url=str(getattr(settings, "postgres_url", "")).strip(),
        )


class ResumeProfileProcessor:
    """End-to-end extraction and apply operations for uploaded resumes."""

    def __init__(self, config: ResumeProcessorConfig) -> None:
        self.config = config
        self.crm = EspoClient(config.espo_base_url, config.espo_api_key)
        self.extractor = ResumeProfileExtractor(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.resume_model,
            max_tokens=config.resume_extractor_max_tokens,
        )
        self.skills_extractor = SkillsExtractor(
            model=config.resume_model,
            openai_api_key=config.openai_api_key,
            openai_base_url=config.openai_base_url,
        )
        self.document_processor = DocumentProcessor(
            allowed_extensions=config.allowed_file_extensions,
            max_file_size_mb=config.max_file_size_mb,
        )

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
            content_hash = self.document_processor.get_content_hash(content, filename)
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
                proposed_changes.append(
                    ResumeFieldChange(
                        field="additional_emails",
                        label="Additional Emails",
                        current=None,
                        proposed=", ".join(extracted.additional_emails),
                        reason="Extracted additional emails from uploaded resume",
                    )
                )
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
                crm_field=self.config.crm_linkedin_field,
                label="LinkedIn",
                current=contact.get(self.config.crm_linkedin_field),
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
                crm_field="addressCountry",
                label="Country",
                current=contact.get("addressCountry"),
                proposed=self._normalize_country(extracted.address_country),
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="cTimezone",
                label="Timezone",
                current=contact.get("cTimezone"),
                proposed=self._normalize_timezone(extracted.timezone),
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="addressCity",
                label="City",
                current=contact.get("addressCity"),
                proposed=self._normalize_city(extracted.address_city),
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="addressState",
                label="State",
                current=contact.get("addressState"),
                proposed=self._normalize_state(extracted.address_state),
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            self._collect_change(
                crm_field="description",
                label="Description",
                current=contact.get("description"),
                proposed=extracted.description.strip()
                if extracted.description
                else None,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
            )
            extracted_roles = self._normalize_roles(extracted.primary_roles)
            existing_roles = self._normalize_roles(contact.get("cRoles"))
            if extracted_roles and sorted(extracted_roles) != sorted(existing_roles):
                proposed_updates["cRoles"] = extracted_roles
                proposed_changes.append(
                    ResumeFieldChange(
                        field="cRoles",
                        label="Roles",
                        current=", ".join(existing_roles),
                        proposed=", ".join(extracted_roles),
                        reason="Extracted from uploaded resume",
                    )
                )
            current_seniority = self._normalize_seniority(contact.get("cSeniority"))
            proposed_seniority = self._normalize_seniority(extracted.seniority_level)
            self._collect_change(
                crm_field="cSeniority",
                label="Seniority",
                current=current_seniority,
                proposed=proposed_seniority,
                proposed_updates=proposed_updates,
                proposed_changes=proposed_changes,
                skipped=skipped,
                is_blocked=lambda value: bool(
                    current_seniority
                    and current_seniority != "unknown"
                    and value != current_seniority
                ),
                blocked_reason="Existing seniority preserved",
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
            if "cRoles" in normalized_updates:
                normalized_roles = self._normalize_roles(normalized_updates["cRoles"])
                if normalized_roles:
                    normalized_updates["cRoles"] = normalized_roles
                else:
                    normalized_updates.pop("cRoles", None)
            if "cTimezone" in normalized_updates:
                normalized_tz = self._normalize_timezone(
                    normalized_updates.get("cTimezone")
                )
                if normalized_tz:
                    normalized_updates["cTimezone"] = normalized_tz
                else:
                    normalized_updates.pop("cTimezone", None)
            if "addressCity" in normalized_updates:
                normalized_city = self._normalize_city(
                    normalized_updates.get("addressCity")
                )
                if normalized_city:
                    normalized_updates["addressCity"] = normalized_city
                else:
                    normalized_updates.pop("addressCity", None)
            if "addressState" in normalized_updates:
                normalized_state = self._normalize_state(
                    normalized_updates.get("addressState")
                )
                if normalized_state:
                    normalized_updates["addressState"] = normalized_state
                else:
                    normalized_updates.pop("addressState", None)

            allowed_fields = {
                "emailAddressData",
                "cGitHubUsername",
                self.config.crm_linkedin_field,
                "cSeniority",
                "addressCountry",
                "cTimezone",
                "addressCity",
                "addressState",
                "description",
                "phoneNumber",
                "cRoles",
                "skills",
                "cSkillAttrs",
                "cWebsiteLink",
                "cSocialLinks",
            }
            approved_updates: dict[str, Any] = {
                field: value
                for field, value in normalized_updates.items()
                if field in allowed_fields and value
            }
            parsed_skills_for_apply = self._normalize_skills_for_apply(
                approved_updates.get("skills")
            )
            if parsed_skills_for_apply is not None:
                approved_updates["skills"] = parsed_skills_for_apply

            if not approved_updates:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=[],
                    updated_values={},
                    success=False,
                    error="No valid profile fields provided",
                )

            link_applied = False
            if link_discord:
                discord_user_id = str(link_discord.get("user_id", "")).strip()
                discord_username = str(link_discord.get("username", "")).strip()
                if discord_user_id and discord_username:
                    approved_updates["cDiscordUserID"] = discord_user_id
                    approved_updates["cDiscordUsername"] = (
                        f"{discord_username} (ID: {discord_user_id})"
                    )
                    link_applied = True

            if not approved_updates:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=[],
                    updated_values={},
                    success=False,
                    error="No valid profile fields provided",
                )

            # NOTE: cResumeLastProcessed is stored as UTC for CRM compatibility.
            crm_update_payload = dict(approved_updates)
            crm_update_payload["cResumeLastProcessed"] = datetime.now(
                tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")

            try:
                self.crm.update_contact(contact_id, crm_update_payload)
                verified_fields = self._verify_updated_fields(
                    contact_id=contact_id,
                    baseline_contact=pre_update_contact,
                    candidate_fields=list(approved_updates.keys()),
                )
                if verified_fields is None:
                    verified_fields = sorted(approved_updates.keys())
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=verified_fields,
                    updated_values={
                        field: approved_updates[field]
                        for field in verified_fields
                        if field in approved_updates
                    },
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
            for field, value in approved_updates.items():
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

                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=sorted(updated_fields),
                    updated_values={
                        field: approved_updates[field]
                        for field in sorted(updated_fields)
                        if field in approved_updates
                    },
                    link_discord_applied=link_applied,
                    success=True,
                )

            if updated_fields:
                return ResumeApplyResult(
                    contact_id=contact_id,
                    updated_fields=sorted(updated_fields),
                    updated_values={
                        field: approved_updates[field]
                        for field in sorted(updated_fields)
                        if field in approved_updates
                    },
                    link_discord_applied=link_applied,
                    success=False,
                    error="; ".join(batch_errors)
                    if batch_errors
                    else "Some fields did not persist after update",
                )

            return ResumeApplyResult(
                contact_id=contact_id,
                updated_fields=sorted(approved_updates.keys()),
                updated_values=dict(approved_updates),
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
                updated_values={},
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
                updated_values={},
                success=False,
                error=str(exc),
            )

    def _normalize_skills_for_apply(self, value: Any) -> list[str] | None:
        """Normalize optional skills updates into an array-shaped payload."""
        if value is None:
            return None

        if isinstance(value, str):
            raw_skills = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_skills = [str(item).strip() for item in value if str(item).strip()]
        else:
            return None

        normalized = normalize_skill_list(raw_skills)
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
            raw_skills = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            raw_skills = [item.strip() for item in value.split(",") if item.strip()]
        else:
            return []

        return normalize_skill_list(raw_skills)

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
        for field_name in candidate_fields:
            baseline[field_name] = self._normalize_compare_value(
                baseline_contact.get(field_name, "")
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
        for field_name in candidate_fields:
            after_value = self._normalize_compare_value(
                after_contact.get(field_name, "")
            )
            if after_value != baseline.get(field_name, ""):
                changed_fields.append(field_name)
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
        return normalize_seniority(value, empty_as_unknown=True)

    @staticmethod
    def _normalize_country(value: Any) -> str | None:
        return normalize_country(value)

    @staticmethod
    def _normalize_city(value: Any) -> str | None:
        return normalize_city(value, strip_parenthetical=False)

    @staticmethod
    def _normalize_state(value: Any) -> str | None:
        return normalize_state(value)

    @staticmethod
    def _normalize_timezone(value: Any) -> str | None:
        return normalize_timezone(value)

    @staticmethod
    def _normalize_role(value: Any) -> str | None:
        return normalize_role(value, ROLE_NORMALIZATION_MAP)

    @staticmethod
    def _normalize_roles(value: Any) -> list[str]:
        return normalize_roles(value, ROLE_NORMALIZATION_MAP)

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

        return normalize_skill_list(raw_skills)

    def _normalize_skill(self, value: Any) -> str | None:
        normalized = normalize_skill(str(value))
        return normalized or None

    def _coerce_profile_skill_result(
        self,
        extracted: ResumeExtractedProfile,
        resume_text: str,
    ) -> ExtractedSkills:
        raw_skills = extracted.skills or []
        normalized_skills, normalized_attrs_raw = normalize_skill_payload(
            skills_value=raw_skills,
            skill_attrs_value=getattr(extracted, "skill_attrs", {}),
            disallowed=DISALLOWED_RESUME_SKILLS,
        )
        normalized_attrs = {
            skill.casefold(): SkillAttributes(strength=strength)
            for skill, strength in normalized_attrs_raw.items()
        }

        if not normalized_attrs and normalized_skills:
            for skill in normalized_skills:
                normalized_attrs[skill.casefold()] = SkillAttributes(strength=3)

        if normalized_attrs or normalized_skills:
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
            candidate = self._decode_json_like(raw)
            if candidate is None:
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

        # Ensure every merged skill has a structured strength so attrs never shrink
        # to a partial subset when extraction omitted some per-skill scores.
        for raw_skill in merged_skills:
            canonical = self._normalize_skill(raw_skill)
            if not canonical:
                continue
            key = canonical.casefold()
            if key not in merged:
                merged[key] = DEFAULT_SKILL_STRENGTH

        return merged

    def _coerce_skill_attrs_updates(self, value: Any) -> str | None:
        if value is None:
            return None

        candidate = value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            candidate = self._decode_json_like(raw)
            if candidate is None:
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

    @staticmethod
    def _decode_json_like(raw: str) -> Any:
        """Decode JSON-like strings, including double-encoded and repr payloads."""
        parsed: Any
        try:
            parsed = json.loads(raw)
        except Exception:
            try:
                parsed = ast.literal_eval(raw)
            except Exception:
                return None

        if isinstance(parsed, str):
            nested = parsed.strip()
            if not nested:
                return None
            try:
                reparsed = json.loads(nested)
            except Exception:
                try:
                    reparsed = ast.literal_eval(nested)
                except Exception:
                    return parsed
            return reparsed

        return parsed

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
            dedupe_key = normalized_website_identity_key(normalized_link)
            if dedupe_key is None or dedupe_key in seen:
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
            dedupe_key = normalized_website_identity_key(normalized)
            if dedupe_key is None or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(normalized)

        for value in extracted:
            if not isinstance(value, str):
                continue
            normalized = self._normalize_website_url(value)
            if not normalized:
                continue
            dedupe_key = normalized_website_identity_key(normalized)
            if dedupe_key is None or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(normalized)

        return merged

    @staticmethod
    def _normalize_website_url(value: str) -> str | None:
        return normalize_website_url(value, allow_scheme_less=True)

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

        if candidate_lower is not None:
            for entry in merged.values():
                entry["primary"] = False
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
                if candidate_lower is not None:
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

    def _configured_model_name(self) -> str:
        """Model identity used for idempotency/ledger keys."""
        if self.config.openai_api_key:
            return self.config.resume_model
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
        if not self.config.postgres_url:
            return
        try:
            with connect(self.config.postgres_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        query,
                        (
                            contact_id,
                            attachment_id,
                            content_hash,
                            self.config.resume_extractor_version,
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
                self.config.resume_extractor_version,
                model_name,
                status,
                exc,
            )
