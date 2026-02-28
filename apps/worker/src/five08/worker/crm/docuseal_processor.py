"""Docuseal member agreement processing workflow."""

import logging
import hashlib
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.worker.config import settings

logger = logging.getLogger(__name__)


class DocusealAgreementProcessor:
    """Look up a CRM contact by email and mark their member agreement as signed."""

    @staticmethod
    def _masked_email(email: str) -> str:
        return hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)

    def process_agreement(
        self,
        email: str,
        completed_at: str,
        submission_id: int,
    ) -> dict[str, Any]:
        """Search for the signer by email and update cMemberAgreementSignedAt."""
        masked_email = self._masked_email(email)

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
            return {
                "success": False,
                "masked_email": masked_email,
                "error": f"CRM search failed: {exc}",
            }

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
            self.api.request(
                "PUT",
                f"Contact/{contact_id}",
                {
                    "cMemberAgreementSignedAt": completed_at,
                    "cSignedMemberAgreement": True,
                },
            )
        except EspoAPIError as exc:
            logger.error("CRM update failed for contact_id=%s: %s", contact_id, exc)
            return {
                "success": False,
                "masked_email": masked_email,
                "submission_id": submission_id,
                "contact_id": contact_id,
                "error": f"CRM update failed: {exc}",
            }

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
            "completed_at": completed_at,
        }
