"""Resume extraction + CRM profile update workflow."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.queue import get_postgres_connection
from five08.worker.config import settings
from five08.worker.crm.document_processor import DocumentProcessor
from five08.worker.crm.skills_extractor import SkillsExtractor
from five08.worker.models import (
    ResumeApplyResult,
    ResumeExtractedProfile,
    ResumeExtractionResult,
    ResumeFieldChange,
    ResumeSkipReason,
    SkillAttributes,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import success depends on environment
    from openai import OpenAI as OpenAIClient
except Exception:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]


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


class ResumeProfileExtractor:
    """Extract candidate profile fields from resume text."""

    def __init__(self) -> None:
        self.model = settings.resolved_resume_ai_model
        self.client: Any = None

        if settings.openai_api_key and OpenAIClient is not None:
            self.client = OpenAIClient(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )

    def extract(self, resume_text: str) -> ResumeExtractedProfile:
        """Return extracted fields from resume text."""
        if self.client is None:
            return self._heuristic_extract(resume_text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract candidate contact fields from resumes for a CRM. "
                            "Return JSON only with no commentary. Be conservative: when unsure, use null. "
                            "Prefer candidate-owned contact info and ignore references or company contact details."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_prompt(resume_text),
                    },
                ],
                temperature=0.1,
                max_tokens=800,
            )
            raw_content = response.choices[0].message.content
            if not raw_content:
                raise ValueError("LLM returned empty content")

            parsed = self._parse_json(raw_content)
            return ResumeExtractedProfile(
                email=self._normalize_email(parsed.get("email")),
                github_username=self._normalize_github(parsed.get("github_username")),
                linkedin_url=self._normalize_linkedin(parsed.get("linkedin_url")),
                phone=self._normalize_phone(parsed.get("phone")),
                confidence=self._bounded_confidence(parsed.get("confidence", 0.75)),
                source=self.model,
            )
        except Exception as exc:
            logger.warning("LLM resume extraction failed, using fallback: %s", exc)
            return self._heuristic_extract(resume_text)

    def _heuristic_extract(self, resume_text: str) -> ResumeExtractedProfile:
        email_match = re.search(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", resume_text
        )
        github_match = re.search(
            r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
            resume_text,
            flags=re.IGNORECASE,
        )
        linkedin_match = re.search(
            r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?",
            resume_text,
            flags=re.IGNORECASE,
        )
        phone_match = re.search(
            r"(?:\+?\d[\d\s().-]{7,}\d)",
            resume_text,
        )

        github_value: str | None = None
        if github_match:
            github_value = github_match.group(1)

        linkedin_value: str | None = None
        if linkedin_match:
            linkedin_value = linkedin_match.group(0)

        phone_value: str | None = None
        if phone_match:
            phone_value = phone_match.group(0)

        return ResumeExtractedProfile(
            email=self._normalize_email(email_match.group(0) if email_match else None),
            github_username=self._normalize_github(github_value),
            linkedin_url=self._normalize_linkedin(linkedin_value),
            phone=self._normalize_phone(phone_value),
            confidence=0.45,
            source="heuristic",
        )

    def _build_prompt(self, resume_text: str) -> str:
        snippet = resume_text[:12000]
        return (
            "Extract candidate contact fields from this resume.\n"
            "Return JSON with exact keys and no extras:\n"
            '{"email": string|null, "github_username": string|null, '
            '"linkedin_url": string|null, "phone": string|null, '
            '"confidence": number}\n'
            "Rules:\n"
            "- prefer explicit values from header/contact sections\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- for phone return digits with optional leading +\n"
            "- use null for unknown/ambiguous fields\n"
            "- confidence is 0-1 for overall extraction reliability\n\n"
            f"Resume:\n{snippet}"
        )

    def _parse_json(self, content: str) -> dict[str, Any]:
        raw = content.strip()
        if raw.startswith("```"):
            lines = [line for line in raw.splitlines() if not line.startswith("```")]
            raw = "\n".join(lines).strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Model output was not a JSON object")
        return parsed

    def _bounded_confidence(self, value: Any) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.0
        return max(0.0, min(1.0, numeric))

    def _normalize_email(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    def _normalize_github(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None

        github_match = re.search(
            r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
            candidate,
            flags=re.IGNORECASE,
        )
        if github_match:
            candidate = github_match.group(1)

        candidate = candidate.lstrip("@").strip().strip("/")
        return candidate or None

    def _normalize_linkedin(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None

        if "linkedin.com" not in candidate.lower():
            return None
        if not candidate.lower().startswith(("http://", "https://")):
            candidate = f"https://{candidate}"
        return candidate.rstrip("/")

    def _normalize_phone(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate:
            return None

        digits = re.sub(r"\D", "", candidate)
        if len(digits) < 7:
            return None

        if candidate.startswith("+"):
            return "+" + digits
        return digits


class ResumeProfileProcessor:
    """End-to-end extraction and apply operations for uploaded resumes."""

    def __init__(self) -> None:
        self.crm = ResumeEspoClient()
        self.extractor = ResumeProfileExtractor()
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
            extracted_skills_result = self.skills_extractor.extract_skills(text)
            extracted_skills = extracted_skills_result.skills
            existing_skills = self._parse_existing_skills(contact.get("skills"))
            existing_skill_attrs = self._parse_skill_attrs(contact.get("cSkillAttrs"))
            existing_lower = {item.casefold() for item in existing_skills}
            new_skills = [
                skill
                for skill in extracted_skills
                if skill.casefold() not in existing_lower
            ]
            merged_skills = existing_skills + new_skills
            merged_skill_attrs = self._merge_skill_attrs(
                existing_attrs=existing_skill_attrs,
                extracted_attrs=extracted_skills_result.skill_attrs,
                merged_skills=merged_skills,
            )

            proposed_updates: dict[str, str] = {}
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
            if new_skills:
                proposed_updates["skills"] = ", ".join(merged_skills)
                proposed_changes.append(
                    ResumeFieldChange(
                        field="skills",
                        label="Skills",
                        current=", ".join(existing_skills) if existing_skills else None,
                        proposed=", ".join(merged_skills),
                        reason=f"Added {len(new_skills)} skills from resume extraction",
                    )
                )

            if merged_skill_attrs and merged_skill_attrs != existing_skill_attrs:
                proposed_updates["cSkillAttrs"] = self._serialize_skill_attrs(
                    merged_skill_attrs
                )
                proposed_changes.append(
                    ResumeFieldChange(
                        field="cSkillAttrs",
                        label="Skill Attributes",
                        current=(
                            f"{len(existing_skill_attrs)} skills rated"
                            if existing_skill_attrs
                            else None
                        ),
                        proposed=f"{len(merged_skill_attrs)} skills rated (strength 1-5)",
                        reason="Updated structured skill strengths from resume extraction",
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
        updates: dict[str, str],
        link_discord: dict[str, str] | None = None,
    ) -> ResumeApplyResult:
        """Apply confirmed updates to contact in CRM."""
        try:
            allowed_fields = {
                "emailAddress",
                "cGitHubUsername",
                settings.crm_linkedin_field,
                "phoneNumber",
                "skills",
                "cSkillAttrs",
            }
            sanitized_updates: dict[str, Any] = {
                field: value
                for field, value in updates.items()
                if field in allowed_fields and value
            }

            email_value = sanitized_updates.get("emailAddress")
            if email_value and email_value.lower().endswith("@508.dev"):
                sanitized_updates.pop("emailAddress")

            if "cSkillAttrs" in sanitized_updates:
                parsed_attrs = self._parse_skill_attrs(sanitized_updates["cSkillAttrs"])
                # Be forgiving: if value is malformed, overwrite with an empty object.
                if parsed_attrs:
                    sanitized_updates["cSkillAttrs"] = json.loads(
                        self._serialize_skill_attrs(parsed_attrs)
                    )
                else:
                    sanitized_updates["cSkillAttrs"] = {}

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

            if sanitized_updates:
                self.crm.update_contact(contact_id, sanitized_updates)

            return ResumeApplyResult(
                contact_id=contact_id,
                updated_fields=sorted(sanitized_updates.keys()),
                link_discord_applied=link_applied,
                success=True,
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

    def _collect_change(
        self,
        *,
        crm_field: str,
        label: str,
        current: Any,
        proposed: str | None,
        proposed_updates: dict[str, str],
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
        else:
            raw_skills = [
                item.strip() for item in str(value).split(",") if item.strip()
            ]

        normalized: list[str] = []
        seen: set[str] = set()
        for skill in raw_skills:
            canonical = self.skills_extractor.canonicalize_skill(skill)
            if not canonical:
                continue
            key = canonical.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(canonical)
        return normalized

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
            skill = self.skills_extractor.canonicalize_skill(str(raw_skill)).casefold()
            if not skill:
                continue

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

        for skill in merged_skills:
            key = str(skill).strip().casefold()
            if key and key not in merged:
                merged[key] = 3

        for skill, attrs in extracted_attrs.items():
            key = str(skill).strip().casefold()
            if key:
                merged[key] = max(1, min(5, int(attrs.strength)))

        return merged

    def _serialize_skill_attrs(self, attrs: dict[str, int]) -> str:
        payload = {
            skill: {"strength": max(1, min(5, int(strength)))}
            for skill, strength in sorted(attrs.items())
            if skill
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def _mark_resume_processed(self, contact_id: str) -> None:
        """Best-effort update for extraction completion tracking."""
        processed_at = datetime.now(tz=timezone.utc).isoformat()
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
