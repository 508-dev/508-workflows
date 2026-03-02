"""Docuseal member agreement processing workflow."""

import logging
from datetime import datetime, timezone
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.worker.config import settings
from five08.worker.masking import mask_email

logger = logging.getLogger(__name__)


class DocusealAgreementProcessingError(RuntimeError):
    """Raised when Docuseal processing hits a retryable execution error."""


class DocusealAgreementNonRetryableError(RuntimeError):
    """Raised when Docuseal processing fails with non-retryable input/state."""


class DocusealAgreementProcessor:
    """Look up a CRM contact by email and mark their member agreement as signed."""

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)

    @staticmethod
    def _normalize_completed_at(completed_at: str) -> str:
        """Normalize timestamp to the CRM-expected UTC format."""
        parsed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")

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
                    "select": "id,name,emailAddress",
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
        }
