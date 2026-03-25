"""Docuseal member agreement processing workflow."""

import logging
from datetime import datetime, timezone
from typing import Any

from five08.clients.discord_bot import (
    DiscordBotAPIError,
    grant_member_role_for_signed_agreement,
)
from five08.clients.espo import EspoAPIError, EspoClient
from five08.worker.config import settings
from five08.worker.masking import mask_email

logger = logging.getLogger(__name__)
_DISCORD_USER_ID_FIELDS = (
    "cDiscordUserId",
    "discordUserId",
    "cDiscordID",
    "cDiscordId",
    "cDiscordUserID",
)


class DocusealAgreementProcessingError(RuntimeError):
    """Raised when Docuseal processing hits a retryable execution error."""


class DocusealAgreementNonRetryableError(RuntimeError):
    """Raised when Docuseal processing fails with non-retryable input/state."""


class DocusealAgreementProcessor:
    """Look up a CRM contact by email and mark their member agreement as signed."""

    def __init__(self) -> None:
        self.api = EspoClient(settings.espo_base_url, settings.espo_api_key)

    @staticmethod
    def _normalize_completed_at(completed_at: str) -> str:
        """Normalize timestamp to the CRM-expected UTC format."""
        parsed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _contact_discord_user_id(contact: dict[str, Any]) -> str | None:
        """Read the linked Discord user id from the supported CRM aliases."""
        for key in _DISCORD_USER_ID_FIELDS:
            candidate = str(contact.get(key) or "").strip()
            if candidate:
                return candidate
        return None

    def _grant_member_role(
        self,
        *,
        contact_id: str,
        contact_name: str | None,
        discord_user_id: str | None,
        submission_id: int,
        completed_at: str,
    ) -> dict[str, Any]:
        """Best-effort Discord role assignment after CRM write succeeds."""
        normalized_user_id = str(discord_user_id or "").strip()
        if not normalized_user_id:
            return {"status": "not_linked"}

        base_url = settings.discord_bot_internal_base_url.strip()
        if not base_url:
            return {"status": "bot_endpoint_not_configured"}

        api_secret = str(settings.api_shared_secret or "").strip()
        if not api_secret:
            return {"status": "api_secret_not_configured"}

        try:
            result = grant_member_role_for_signed_agreement(
                base_url=base_url,
                api_secret=api_secret,
                discord_user_id=normalized_user_id,
                contact_id=contact_id,
                contact_name=contact_name,
                submission_id=submission_id,
                completed_at=completed_at,
            )
        except DiscordBotAPIError as exc:
            logger.warning(
                "Best-effort Member role assignment failed contact_id=%s discord_user_id=%s: %s",
                contact_id,
                normalized_user_id,
                exc,
            )
            return {
                "status": "error",
                "discord_user_id": normalized_user_id,
                "error": str(exc),
            }

        return {
            "status": str(result.get("status") or "unknown"),
            "discord_user_id": normalized_user_id,
            "result": result,
        }

    def process_agreement(
        self,
        email: str,
        completed_at: str,
        submission_id: int,
    ) -> dict[str, Any]:
        """Search for the signer by email and update cMemberAgreementSignedAt.

        Expected input is the queue contract value:
        ``YYYY-MM-DD HH:mm:ss`` in UTC.
        """
        masked_email = mask_email(email)

        try:
            result = self.api.request(
                "GET",
                "Contact",
                {
                    "where": [
                        {
                            "type": "equals",
                            "attribute": "emailAddress",
                            "value": email,
                        }
                    ],
                    "maxSize": 1,
                    "select": (
                        "id,name,emailAddress,cDiscordUsername,"
                        + ",".join(_DISCORD_USER_ID_FIELDS)
                    ),
                },
            )
        except EspoAPIError as exc:
            logger.error("CRM search failed for masked_email=%s: %s", masked_email, exc)
            raise DocusealAgreementProcessingError(
                f"CRM search failed for masked_email={masked_email}: {exc}"
            ) from exc

        contacts = result.get("list", [])
        if not contacts:
            logger.warning(
                "No CRM contact found for masked_email=%s submission_id=%s",
                masked_email,
                submission_id,
            )
            return {
                "success": False,
                "masked_email": masked_email,
                "error": "contact_not_found",
            }

        contact = contacts[0]
        contact_id = contact["id"]
        contact_name = str(contact.get("name") or "").strip() or None
        discord_user_id = self._contact_discord_user_id(contact)

        try:
            crm_completed_at = self._normalize_completed_at(completed_at)
        except ValueError as exc:
            logger.error(
                "CRM update failed for contact_id=%s due to invalid datetime=%s: %s",
                contact_id,
                completed_at,
                exc,
            )
            raise DocusealAgreementNonRetryableError(
                f"invalid_completed_at for contact_id={contact_id}: {exc}"
            ) from exc

        try:
            self.api.request(
                "PUT",
                f"Contact/{contact_id}",
                {
                    "cMemberAgreementSignedAt": crm_completed_at,
                },
            )
        except EspoAPIError as exc:
            logger.error("CRM update failed for contact_id=%s: %s", contact_id, exc)
            raise DocusealAgreementProcessingError(
                f"CRM update failed for contact_id={contact_id}: {exc}"
            ) from exc

        member_role = self._grant_member_role(
            contact_id=contact_id,
            contact_name=contact_name,
            discord_user_id=discord_user_id,
            submission_id=submission_id,
            completed_at=crm_completed_at,
        )

        logger.info(
            "Marked member agreement signed contact_id=%s masked_email=%s",
            contact_id,
            masked_email,
        )
        return {
            "success": True,
            "masked_email": masked_email,
            "contact_id": contact_id,
            "submission_id": submission_id,
            "completed_at": crm_completed_at,
            "discord_user_id": discord_user_id,
            "member_role": member_role,
        }
