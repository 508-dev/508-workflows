"""Google Forms member intake processing workflow."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.resume_extractor import ResumeProfileExtractor
from five08.worker.config import settings
from five08.worker.crm.document_processor import DocumentProcessor
from five08.worker.crm.skills_extractor import SkillsExtractor
from five08.worker.masking import mask_email

import logging

logger = logging.getLogger(__name__)

DESCRIPTION_SECTIONS = {
    "primary_skills_interests": "Primary skills and interests",
    "top_question_about_508": "Top question about 508.dev",
}

FIELD_MAP = {
    "phone": "phoneNumber",
    "discord_username": "cDiscordUsername",
    "linkedin_url": settings.crm_linkedin_field,
    "github_username": "cGitHubUsername",
    "address_country": "addressCountry",
    "primary_role": "cRoles",
    "availability": "cAvailableTimes",
    "rate_range": "cRateRange",
    "referred_by": "cReferredBy",
}

SKILL_PROFICIENCY_TO_LABEL = {
    "skill_proficiency_next_js": "next.js",
    "skill_proficiency_react_native_expo": "react native / expo",
    "skill_proficiency_supabase": "supabase",
    "skill_proficiency_ai_ml_engineering": "ai/ml engineering",
    "skill_proficiency_python_django_fastapi": "python / django / fastapi",
    "skill_proficiency_wordpress": "wordpress",
    "skill_proficiency_devops": "devops",
    "skill_proficiency_crypto_blockchain": "crypto/blockchain",
    "skill_proficiency_chat_bots": "chat bots",
    "skill_proficiency_unity_video_game": "unity / video game development",
    "skill_proficiency_project_management": "project management",
    "skill_proficiency_client_management": "client management",
    "skill_proficiency_sales_marketing": "sales / marketing",
    "skill_proficiency_internal_business_development": "internal business development",
}

SENIORITY_MAP = {
    "junior": "junior",
    "mid-level": "midlevel",
    "midlevel": "midlevel",
    "senior": "senior",
    "principal": "staff",
    "principal engineer": "staff",
    "staff": "staff",
    "staff and beyond": "staff",
    "staff+": "staff",
}


ROLE_NORMALIZATION_MAP: dict[str, str] = {
    "developer": "developer",
    "data scientist": "data_scientist",
    "program manager": "program_manager",
    "designer": "designer",
    "user research": "user_research",
    "biz dev": "biz_dev",
    "marketing": "marketing",
}


class IntakeFormProcessor:
    """Process a Google Forms member intake submission against CRM."""

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)
        self.document_processor = DocumentProcessor()
        self.resume_extractor = ResumeProfileExtractor(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.resolved_resume_ai_model,
        )
        self.skills_extractor = SkillsExtractor()

    def process_intake(self, *, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Look up CRM contact by email and update/create prospect records."""
        email = self._normalize_text(payload.get("email"))
        first_name = self._normalize_text(payload.get("first_name"))
        last_name = self._normalize_text(payload.get("last_name"))
        masked_email = mask_email(email or "")

        if not email or not first_name or not last_name:
            return {"success": False, "error": "invalid_payload"}

        allowed_form_ids = settings.google_forms_allowed_form_ids_set
        form_id = self._normalize_text(payload.get("form_id"))
        if allowed_form_ids and not form_id:
            logger.warning(
                "Google forms submission missing form_id for masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "invalid_form_id"}
        if allowed_form_ids and form_id not in allowed_form_ids:
            logger.warning(
                "Google forms submission with unapproved form_id=%s masked_email=%s",
                form_id,
                masked_email,
            )
            return {"success": False, "error": "invalid_form_id"}

        try:
            contact_result = self.api.request(
                "GET",
                "Contact",
                {
                    "where[0][type]": "equals",
                    "where[0][attribute]": "emailAddress",
                    "where[0][value]": email,
                    "maxSize": 2,
                    "select": (
                        "id,firstName,lastName,emailAddress,type,cDiscordRoles,cDiscordUserId,"
                        "phoneNumber,cDiscordUsername,cGithubUsername,cRoles,cSeniority,cMemberAgreementSignedAt,"
                        "cAvailableTimes,cRateRange,cReferredBy,addressCountry,description,"
                        "cSkillAttrs"
                    ),
                },
            )
        except EspoAPIError as exc:
            logger.error(
                "CRM search failed masked_email=%s error=%s", masked_email, exc
            )
            return {"success": False, "error": "CRM search failed"}

        contact_list = contact_result.get("list", [])
        if not isinstance(contact_list, list):
            logger.error("CRM search response malformed masked_email=%s", masked_email)
            return {"success": False, "error": "CRM search failed"}

        if not contact_list:
            return self._create_prospect(
                email=email,
                first_name=first_name,
                last_name=last_name,
                payload=payload,
                masked_email=masked_email,
            )

        if len(contact_list) > 1:
            contact_ids = self._collect_contact_ids(contact_list)
            logger.error(
                "Multiple CRM contacts found for masked_email=%s ids=%s",
                masked_email,
                contact_ids,
            )
            return {"success": False, "error": "Multiple contacts found for email"}

        contact = contact_list[0]
        if not isinstance(contact, dict) or "id" not in contact:
            logger.error(
                "CRM search returned malformed contact payload masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "CRM search failed"}

        if self._is_member_contact(contact):
            logger.warning(
                "Attempted intake update against existing member masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "Contact already exists as member"}

        return self._update_prospect(
            contact=contact,
            email=email,
            first_name=first_name,
            last_name=last_name,
            payload=payload,
            masked_email=masked_email,
        )

    def _is_member_contact(self, contact: Mapping[str, Any]) -> bool:
        contact_type = self._normalize_text(contact.get("type"))
        if contact_type and contact_type.casefold() == "member":
            return True

        role_values = self._normalize_collection(contact.get("cDiscordRoles"))
        if any(role.casefold() == "member" for role in role_values):
            return True

        return bool(self._normalize_text(contact.get("cMemberAgreementSignedAt")))

    def _create_prospect(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        payload: Mapping[str, Any],
        masked_email: str,
    ) -> dict[str, Any]:
        base_updates = self._build_intake_updates(
            email=email,
            first_name=first_name,
            last_name=last_name,
            payload=payload,
        )
        if not base_updates:
            logger.warning(
                "Cannot create prospect with empty payload masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "No updates available"}

        try:
            created = self.api.request("POST", "Contact", base_updates)
            contact_id = (
                str(created.get("id", "")).strip()
                if isinstance(created, Mapping)
                else ""
            )
            if not contact_id:
                raise EspoAPIError("Contact create response did not return id")
        except EspoAPIError as exc:
            logger.error(
                "CRM create failed masked_email=%s error=%s", masked_email, exc
            )
            return {"success": False, "error": "CRM create failed"}

        logger.info(
            "Created prospect contact_id=%s masked_email=%s",
            contact_id,
            masked_email,
        )
        return {
            "success": True,
            "created": True,
            "contact_id": contact_id,
            "updated_fields": sorted(base_updates.keys()),
        }

    def _update_prospect(
        self,
        *,
        contact: Mapping[str, Any],
        email: str,
        first_name: str,
        last_name: str,
        payload: Mapping[str, Any],
        masked_email: str,
    ) -> dict[str, Any]:
        contact_id = str(contact.get("id", "")).strip()
        if not contact_id:
            logger.error("CRM contact missing id masked_email=%s", masked_email)
            return {"success": False, "error": "CRM search failed"}

        updates = self._build_intake_updates(
            email=email,
            first_name=first_name,
            last_name=last_name,
            payload=payload,
            include_email=False,
        )
        if not updates:
            logger.info("No prospect updates needed for contact_id=%s", contact_id)
            return {
                "success": True,
                "created": False,
                "contact_id": contact_id,
                "updated_fields": [],
            }

        try:
            self.api.request("PUT", f"Contact/{contact_id}", updates)
        except EspoAPIError as exc:
            logger.error(
                "CRM update failed contact_id=%s masked_email=%s error=%s",
                contact_id,
                masked_email,
                exc,
            )
            return {"success": False, "error": "CRM update failed"}

        logger.info(
            "Applied intake updates contact_id=%s masked_email=%s fields=%s",
            contact_id,
            masked_email,
            sorted(updates.keys()),
        )
        return {
            "success": True,
            "created": False,
            "contact_id": contact_id,
            "updated_fields": sorted(updates.keys()),
        }

    def _build_intake_updates(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        payload: Mapping[str, Any],
        include_email: bool = True,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {
            "firstName": first_name,
            "lastName": last_name,
        }
        if include_email:
            updates["emailAddress"] = email

        for local_key, crm_field in FIELD_MAP.items():
            if local_key == "github_username":
                value = self._normalize_github_username(payload.get(local_key))
            elif local_key == "primary_role":
                normalized_roles = self._parse_roles(payload.get(local_key))
                if not normalized_roles:
                    continue
                updates[crm_field] = normalized_roles
                continue
            else:
                value = self._normalize_text(payload.get(local_key))
            if value:
                updates[crm_field] = value

        seniority_level = self._normalize_seniority(payload.get("seniority_level"))
        if seniority_level:
            updates["cSeniority"] = seniority_level

        description = self._build_description(payload)
        if description:
            updates["description"] = description

        form_skill_attrs = self._build_form_skill_attrs(payload)
        if form_skill_attrs:
            updates["cSkillAttrs"] = json.dumps(form_skill_attrs)
            updates["skills"] = sorted(form_skill_attrs.keys())

        submitted_at = self._normalize_text(payload.get("submitted_at"))
        completed_field = (settings.crm_intake_completed_field or "").strip()
        if submitted_at and completed_field:
            updates[completed_field] = submitted_at

        resume_updates = self._build_resume_updates(payload)
        if resume_updates:
            for key, value in resume_updates.items():
                if key not in updates:
                    updates[key] = value

        return updates

    def _build_form_skill_attrs(
        self, payload: Mapping[str, Any]
    ) -> dict[str, dict[str, int]]:
        skills: dict[str, dict[str, int]] = {}
        for form_key, label in SKILL_PROFICIENCY_TO_LABEL.items():
            normalized = self._normalize_text(payload.get(form_key))
            strength = self._parse_skill_strength(normalized)
            if strength is None:
                continue
            normalized_label = self.skills_extractor.canonicalize_skill(label)
            if not normalized_label:
                continue
            skills[normalized_label] = {"strength": strength}
        return skills

    def _build_description(self, payload: Mapping[str, Any]) -> str | None:
        description_parts: list[str] = []
        for key, label in DESCRIPTION_SECTIONS.items():
            value = self._normalize_text(payload.get(key))
            if not value:
                continue
            description_parts.append(f"{label}: {value}")
        if not description_parts:
            return None
        return " | ".join(description_parts)

    def _build_resume_updates(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        resume_url = self._normalize_text(payload.get("resume_url"))
        if not resume_url:
            return {}

        resume_file_name = self._normalize_text(payload.get("resume_file_name"))
        resume_name = (
            resume_file_name or self._filename_from_url(resume_url) or "resume"
        )
        if not resume_name:
            return {}

        try:
            response = requests.get(resume_url, timeout=20)
            response.raise_for_status()
            content = response.content
        except Exception as exc:
            logger.warning("Failed to download resume_url=%s error=%s", resume_url, exc)
            return {}

        try:
            resume_text = self.document_processor.extract_text(content, resume_name)
        except Exception as exc:
            logger.warning(
                "Failed to parse resume masked_url=%s error=%s", resume_url, exc
            )
            return {}

        updates: dict[str, Any] = {}
        try:
            extra_sources: dict[str, str] = {}
            for field, raw_value in {
                "availability": payload.get("availability"),
                "rate_range": payload.get("rate_range"),
                "referred_by": payload.get("referred_by"),
                "content_email": payload.get("content_email"),
            }.items():
                normalized_value = self._normalize_text(raw_value)
                if normalized_value:
                    extra_sources[field] = normalized_value
            extracted_profile = self.resume_extractor.extract(
                resume_text,
                extra_sources=extra_sources,
            )
            profile_phone = self._normalize_text(extracted_profile.phone)
            profile_github = self._normalize_text(extracted_profile.github_username)
            profile_linkedin = self._normalize_text(extracted_profile.linkedin_url)
            profile_availability = self._normalize_text(
                getattr(extracted_profile, "availability", None)
            )
            profile_rate_range = self._normalize_text(
                getattr(extracted_profile, "rate_range", None)
            )
            profile_referred_by = self._normalize_text(
                getattr(extracted_profile, "referred_by", None)
            )
            if profile_phone:
                updates["phoneNumber"] = profile_phone
            if profile_github:
                updates["cGitHubUsername"] = profile_github
            if profile_linkedin:
                updates[settings.crm_linkedin_field] = profile_linkedin
            if profile_availability:
                updates.setdefault("cAvailableTimes", profile_availability)
            if profile_rate_range:
                updates.setdefault("cRateRange", profile_rate_range)
            if profile_referred_by:
                updates.setdefault("cReferredBy", profile_referred_by)
            profile_attrs = self._parse_profile_skill_attrs(extracted_profile)
            if profile_attrs:
                updates["cSkillAttrs"] = json.dumps(profile_attrs)
                updates["skills"] = sorted(profile_attrs.keys())
            profile_websites = self._parse_profile_website_links(
                getattr(extracted_profile, "website_links", [])
            )
            if profile_websites:
                updates["cWebsiteLink"] = profile_websites
            profile_social_links = self._parse_profile_social_links(
                getattr(extracted_profile, "social_links", [])
            )
            if profile_social_links:
                updates["cSocialLinks"] = profile_social_links
        except Exception as exc:
            logger.warning("Resume profile extraction failed: %s", exc)
        return updates

    def _parse_profile_website_links(self, links: Any) -> list[str]:
        if not isinstance(links, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_link in links:
            if not isinstance(raw_link, str):
                continue
            candidate = raw_link.strip().rstrip("/").strip(")]},.;:")
            if not candidate:
                continue
            if not candidate.startswith(("http://", "https://")):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    def _parse_profile_social_links(self, links: Any) -> list[str]:
        if not isinstance(links, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_link in links:
            if not isinstance(raw_link, str):
                continue
            candidate = raw_link.strip().rstrip("/").strip(")]},.;:")
            if not candidate:
                continue
            if not candidate.startswith(("http://", "https://")):
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    def _parse_profile_skill_attrs(self, profile: Any) -> dict[str, int]:
        raw_attrs = getattr(profile, "skill_attrs", {})
        if not isinstance(raw_attrs, dict):
            return {}
        parsed: dict[str, int] = {}
        for raw_skill, raw_payload in raw_attrs.items():
            normalized_name = self.skills_extractor.canonicalize_skill(str(raw_skill))
            if not normalized_name:
                continue
            if isinstance(raw_payload, dict):
                raw_payload = raw_payload.get("strength")
            try:
                parsed[normalized_name] = max(1, min(5, int(raw_payload)))
            except Exception:
                continue
        return parsed

    def _parse_skill_strength(self, value: str | None) -> int | None:
        if not value:
            return None

        normalized = value.strip().lower()
        if not normalized:
            return None

        if normalized in {"1", "2", "3", "4", "5"}:
            return int(normalized)
        if normalized in {"one", "beginner"}:
            return 1
        if normalized in {"two", "novice", "basic"}:
            return 2
        if normalized in {"three", "intermediate"}:
            return 3
        if normalized in {"four", "advanced"}:
            return 4
        if normalized in {"five", "expert", "expert+"}:
            return 5

        match = re.search(r"\b([1-5])\b", normalized)
        if match:
            return int(match.group(1))
        return None

    def _parse_skill_attrs(self, attrs: Mapping[str, Any] | None) -> dict[str, int]:
        parsed: dict[str, int] = {}
        if attrs is None:
            return parsed
        for raw_name, raw_attr in attrs.items():
            normalized_name = self.skills_extractor.canonicalize_skill(str(raw_name))
            if not normalized_name:
                continue
            strength = raw_attr
            if isinstance(raw_attr, Mapping):
                strength = raw_attr.get("strength")
            normalized_strength = self._parse_skill_strength(
                self._normalize_text(str(strength))
            )
            if normalized_strength is None:
                continue
            parsed[normalized_name] = normalized_strength
        return parsed

    def _normalize_seniority(self, value: Any) -> str | None:
        normalized = self._normalize_text(value)
        if not normalized:
            return None

        normalized = normalized.lower().replace("_", "-").strip()
        if normalized in SENIORITY_MAP:
            return SENIORITY_MAP[normalized]
        if "staff" in normalized:
            return "staff"
        if "senior" in normalized:
            return "senior"
        if "mid" in normalized:
            return "midlevel"
        if "junior" in normalized:
            return "junior"
        return "unknown"

    def _normalize_text(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _normalize_github_username(self, value: object) -> str | None:
        normalized = self._normalize_text(value)
        if normalized is None:
            return None

        text = normalized.strip()
        if not text:
            return None

        if text.startswith("@"):
            text = text[1:].strip()

        parsed = urlsplit(text)
        if not parsed.scheme or not parsed.netloc:
            return text

        host = parsed.netloc.lower()
        if not host.endswith("github.com"):
            return text

        path = parsed.path.strip("/")
        if not path:
            return None

        segments = [segment for segment in path.split("/") if segment]
        if not segments:
            return None

        if segments[0].lower() in {"users", "orgs"} and len(segments) >= 2:
            return segments[1]

        return segments[0]

    def _normalize_collection(self, value: Any) -> list[str]:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",")]
            return [item for item in items if item]
        if isinstance(value, list):
            return [
                item.strip() for item in value if isinstance(item, str) and item.strip()
            ]
        if isinstance(value, Mapping):
            return [
                item.strip()
                for item in value.values()
                if isinstance(item, str) and item.strip()
            ]
        return []

    def _normalize_role(self, value: str) -> str | None:
        normalized = self._normalize_text(value)
        if normalized is None:
            return None

        lowered = normalized.lower().strip()
        mapped = ROLE_NORMALIZATION_MAP.get(lowered)
        if mapped is not None:
            return mapped

        normalized_role = "_".join(lowered.split())
        normalized_role = "".join(
            ch for ch in normalized_role if ch.isalnum() or ch in {"_", "-"}
        )
        return normalized_role or None

    def _parse_roles(self, roles: Any) -> list[str]:
        parsed = self._normalize_collection(roles)
        normalized: list[str] = []
        seen: set[str] = set()
        for role in parsed:
            normalized_role = self._normalize_role(role)
            if normalized_role is None or normalized_role in seen:
                continue
            seen.add(normalized_role)
            normalized.append(normalized_role)
        return normalized

    def _filename_from_url(self, url: str) -> str | None:
        path = urlsplit(url).path.strip()
        if not path:
            return None
        name = Path(path).name.strip()
        return name or None

    def _collect_contact_ids(self, contact_list: list[Any]) -> list[str]:
        ids: list[str] = []
        for contact in contact_list:
            if not isinstance(contact, Mapping):
                continue
            raw_id = self._normalize_text(contact.get("id"))
            if raw_id:
                ids.append(raw_id)
        return ids
