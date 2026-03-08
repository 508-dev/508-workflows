"""CRM to local people-cache sync workflow."""

from __future__ import annotations

import logging
import re
from typing import Any

from five08.audit import PeopleSyncStatus, PersonRecord, upsert_person
from five08.clients.espo import EspoAPIError, EspoClient
from five08.skills import normalize_skill_payload
from five08.worker.config import settings

logger = logging.getLogger(__name__)

_DISCORD_ID_RE = re.compile(r"\(ID:\s*(\d+)\)")


class EspoPeopleSyncClient:
    """Fetch contact identity data from EspoCRM."""

    def __init__(self) -> None:
        self.api = EspoClient(settings.espo_base_url, settings.espo_api_key)

    def list_contact_page(
        self, *, offset: int, max_size: int
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Load one page of contacts for identity sync."""
        select_fields = (
            "id,name,emailAddress,emailAddressData,c508Email,"
            "cDiscordUsername,cDiscordUserId,cDiscordRoles,cDiscordUserID,"
            "cGithubUsername,githubUsername,type,contactType,"
            "addressCountry,addressCity,cTimezone,cSeniority,cMemberAgreementSignedAt,"
            f"{settings.crm_linkedin_field},skills,cSkillAttrs,resumeIds,resumeNames"
        )
        raw = self.api.request(
            "GET",
            "Contact",
            {
                "offset": offset,
                "maxSize": max_size,
                "select": select_fields,
            },
        )
        contacts = raw.get("list", [])
        if not isinstance(contacts, list):
            contacts = []

        total_raw = raw.get("total")
        total = total_raw if isinstance(total_raw, int) else None
        parsed_contacts = [item for item in contacts if isinstance(item, dict)]
        return parsed_contacts, total

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Load one contact by id."""
        raw = self.api.request("GET", f"Contact/{contact_id}")
        if not isinstance(raw, dict):
            raise ValueError("Unexpected contact payload from EspoCRM")
        return raw


class PeopleSyncProcessor:
    """Sync local people cache from EspoCRM contacts."""

    def __init__(self) -> None:
        self.client = EspoPeopleSyncClient()

    def sync_all_contacts(self) -> dict[str, Any]:
        """Run a paginated full sync into the local people table."""
        synced_count = 0
        failed_ids: list[str] = []
        offset = 0
        page_size = max(1, settings.crm_sync_page_size)
        pages = 0
        total_seen = 0

        while True:
            try:
                contacts, total = self.client.list_contact_page(
                    offset=offset,
                    max_size=page_size,
                )
            except EspoAPIError as exc:
                logger.error("Failed loading contacts page offset=%s: %s", offset, exc)
                break

            if not contacts:
                break

            pages += 1
            total_seen += len(contacts)
            for raw_contact in contacts:
                person = self._to_person_record(raw_contact)
                if person is None:
                    failed_ids.append(str(raw_contact.get("id", "unknown")))
                    continue

                try:
                    upsert_person(settings, person)
                    synced_count += 1
                except Exception as exc:
                    contact_id = person.crm_contact_id
                    failed_ids.append(contact_id)
                    logger.warning(
                        "Failed syncing CRM contact id=%s into people cache: %s",
                        contact_id,
                        exc,
                    )

            offset += len(contacts)
            if total is not None and offset >= total:
                break
            if len(contacts) < page_size:
                break

        return {
            "synced_count": synced_count,
            "failed_count": len(failed_ids),
            "failed_contact_ids": failed_ids,
            "total_seen": total_seen,
            "pages": pages,
        }

    def sync_contact(self, contact_id: str) -> dict[str, Any]:
        """Sync one contact into the local people table."""
        try:
            raw_contact = self.client.get_contact(contact_id)
        except EspoAPIError as exc:
            logger.error("Failed loading contact id=%s: %s", contact_id, exc)
            return {
                "contact_id": contact_id,
                "synced": False,
                "error": str(exc),
            }

        person = self._to_person_record(raw_contact)
        if person is None:
            return {
                "contact_id": contact_id,
                "synced": False,
                "error": "contact_missing_id",
            }

        try:
            upsert_person(settings, person)
        except Exception as exc:
            logger.warning("Failed syncing contact id=%s: %s", contact_id, exc)
            return {
                "contact_id": contact_id,
                "synced": False,
                "error": str(exc),
            }

        return {
            "contact_id": contact_id,
            "synced": True,
        }

    def _to_person_record(self, raw_contact: dict[str, Any]) -> PersonRecord | None:
        contact_id = str(raw_contact.get("id", "")).strip()
        if not contact_id:
            return None

        discord_username = self._discord_username(raw_contact)
        discord_user_id = self._discord_user_id(raw_contact, discord_username)
        skills, skill_attrs = self._extract_skills(raw_contact)

        resume_ids = self._coerce_list(raw_contact.get("resumeIds"))
        latest_resume_id = resume_ids[-1] if resume_ids else None
        latest_resume_name = self._extract_latest_resume_name(
            resume_ids=resume_ids,
            resume_names=raw_contact.get("resumeNames"),
        )

        return PersonRecord(
            crm_contact_id=contact_id,
            name=_text_or_none(raw_contact.get("name")),
            email=self._email(raw_contact),
            email_508=self._email_508(raw_contact),
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            discord_roles=self._discord_roles(raw_contact.get("cDiscordRoles")),
            github_username=self._github_username(raw_contact),
            contact_type=_text_or_none(
                self._first_not_none(
                    raw_contact.get("type"), raw_contact.get("contactType")
                )
            ),
            is_member=self._is_member(raw_contact),
            address_country=_text_or_none(raw_contact.get("addressCountry")),
            address_city=_text_or_none(raw_contact.get("addressCity")),
            timezone=_text_or_none(raw_contact.get("cTimezone")),
            seniority=_text_or_none(raw_contact.get("cSeniority")),
            linkedin=self._coerce_linkedin(raw_contact),
            skills=skills,
            skill_attrs=skill_attrs,
            latest_resume_id=latest_resume_id,
            latest_resume_name=latest_resume_name,
            sync_status=PeopleSyncStatus.ACTIVE,
        )

    @staticmethod
    def _first_not_none(*values: Any) -> Any | None:
        for value in values:
            if _text_or_none(value) is not None:
                return value
        return None

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            return []

        normalized: list[str] = []
        for item in value:
            text = _text_or_none(item)
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _extract_latest_resume_name(
        *, resume_ids: list[str], resume_names: Any
    ) -> str | None:
        if not resume_ids:
            return None
        latest_id = resume_ids[-1]
        if not isinstance(resume_names, dict):
            return None
        raw_name = resume_names.get(latest_id)
        if raw_name is None:
            return None
        return _text_or_none(raw_name)

    def _email(self, raw_contact: dict[str, Any]) -> str | None:
        direct = _text_or_none(raw_contact.get("emailAddress"))
        if direct:
            return direct

        email_data = raw_contact.get("emailAddressData")
        if not isinstance(email_data, list):
            return None

        for item in email_data:
            if not isinstance(item, dict):
                continue
            candidate = _text_or_none(item.get("emailAddress"))
            if candidate and bool(item.get("primary")):
                return candidate

        for item in email_data:
            if not isinstance(item, dict):
                continue
            candidate = _text_or_none(item.get("emailAddress"))
            if candidate:
                return candidate

        return None

    def _email_508(self, raw_contact: dict[str, Any]) -> str | None:
        return _text_or_none(raw_contact.get("c508Email"))

    def _discord_username(self, raw_contact: dict[str, Any]) -> str | None:
        raw_username = _text_or_none(raw_contact.get("cDiscordUsername"))
        if raw_username is None:
            raw_username = _text_or_none(raw_contact.get("discordUsername"))
        if raw_username is None:
            return None

        cleaned = _DISCORD_ID_RE.sub("", raw_username).strip()
        return cleaned or None

    def _discord_user_id(
        self,
        raw_contact: dict[str, Any],
        discord_username: str | None,
    ) -> str | None:
        for key in (
            "cDiscordUserId",
            "discordUserId",
            "cDiscordID",
            "cDiscordId",
            "cDiscordUserID",
        ):
            candidate = _text_or_none(raw_contact.get(key))
            if candidate:
                return candidate

        if discord_username is None:
            return None

        raw_username = _text_or_none(raw_contact.get("cDiscordUsername"))
        if raw_username is None:
            return None

        match = _DISCORD_ID_RE.search(raw_username)
        if match is None:
            return None
        return match.group(1)

    def _discord_roles(self, raw_roles: Any) -> list[str]:
        if isinstance(raw_roles, list):
            return [_text for item in raw_roles if (_text := _text_or_none(item))]
        if isinstance(raw_roles, str):
            values = [item.strip() for item in raw_roles.split(",")]
            return [value for value in values if value]
        if isinstance(raw_roles, dict):
            roles: list[str] = []
            for value in raw_roles.values():
                text = _text_or_none(value)
                if text:
                    roles.append(text)
            return roles
        return []

    def _github_username(self, raw_contact: dict[str, Any]) -> str | None:
        for key in ("cGithubUsername", "githubUsername", "cGithubUserName"):
            candidate = _text_or_none(raw_contact.get(key))
            if candidate:
                return candidate
        return None

    def _coerce_linkedin(self, raw_contact: dict[str, Any]) -> str | None:
        configured_field = settings.crm_linkedin_field
        for key in (configured_field, "cLinkedIn", "linkedin"):
            value = _text_or_none(raw_contact.get(key))
            if value:
                return value
        return None

    def _extract_skills(
        self, raw_contact: dict[str, Any]
    ) -> tuple[list[str], dict[str, int]]:
        raw_skill_attrs = raw_contact.get("cSkillAttrs")
        skills, attrs = normalize_skill_payload(
            raw_contact.get("skills"),
            raw_skill_attrs,
            disallowed=frozenset(),
        )
        if not skills and isinstance(raw_skill_attrs, dict):
            for key, payload in raw_skill_attrs.items():
                raw = _text_or_none(key)
                if raw and isinstance(payload, dict):
                    skills.append(raw)
                    value = payload.get("strength")
                    parsed = self._coerce_int(value)
                    if parsed is not None:
                        attrs[raw] = max(attrs.get(raw, 0), parsed)
        return skills, attrs

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except Exception:
            return None
        if not 1 <= parsed <= 5:
            return None
        return parsed

    def _is_member(self, raw_contact: dict[str, Any]) -> bool:
        contact_type = _text_or_none(
            self._first_not_none(
                raw_contact.get("type"), raw_contact.get("contactType")
            )
        )
        if contact_type and "member" in contact_type.casefold():
            return True

        agreement = _text_or_none(raw_contact.get("cMemberAgreementSignedAt"))
        if agreement:
            return True

        is_member_field = raw_contact.get("isMember")
        if isinstance(is_member_field, bool):
            return is_member_field

        if isinstance(is_member_field, str) and is_member_field.strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            return True
        return False


def _text_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
