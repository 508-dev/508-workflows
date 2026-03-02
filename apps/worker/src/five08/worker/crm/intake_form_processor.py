"""Google Forms member intake processing workflow."""

import logging
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.worker.config import settings
from five08.worker.masking import mask_email

logger = logging.getLogger(__name__)


class IntakeFormProcessor:
    """Process a Google Forms member intake submission against CRM."""

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)

    def process_intake(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        phone: str | None = None,
        discord_username: str | None = None,
        linkedin_url: str | None = None,
        github_username: str | None = None,
        submitted_at: str | None = None,
    ) -> dict[str, Any]:
        """Look up CRM contact by email and apply intake form fields."""
        masked_email = mask_email(email)
        try:
            contacts = self.api.request(
                "GET",
                "Contact",
                {
                    "where[0][type]": "equals",
                    "where[0][attribute]": "emailAddress",
                    "where[0][value]": email,
                    "select": "id,firstName,lastName,emailAddress",
                },
            )
        except EspoAPIError as exc:
            logger.error(
                "CRM search failed masked_email=%s error=%s", masked_email, exc
            )
            return {"success": False, "error": "CRM search failed"}

        contact_list = contacts.get("list", [])
        if not isinstance(contact_list, list):
            logger.error(
                "CRM search returned unexpected response for masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "CRM search failed"}
        if not contact_list:
            logger.warning("No CRM contact found for masked_email=%s", masked_email)
            return {"success": False, "error": "No contact found for submitted email"}
        if len(contact_list) > 1:
            contact_ids = [
                str(contact.get("id", ""))
                for contact in contact_list
                if isinstance(contact, dict) and "id" in contact
            ]
            logger.error(
                "Multiple CRM contacts found masked_email=%s ids=%s",
                masked_email,
                contact_ids,
            )
            return {
                "success": False,
                "error": "Multiple contacts found for submitted email",
            }

        contact = contact_list[0]
        if not isinstance(contact, dict) or "id" not in contact:
            logger.error(
                "CRM search returned malformed contact payload for masked_email=%s",
                masked_email,
            )
            return {"success": False, "error": "CRM search failed"}
        contact_id: str = contact["id"]

        field_map: dict[str, str] = {
            "phone": "phoneNumber",
            "discord_username": "cDiscordUsername",
            "linkedin_url": settings.crm_linkedin_field,
            "github_username": "cGitHubUsername",
        }

        updates: dict[str, Any] = {
            "firstName": first_name,
            "lastName": last_name,
        }

        optional_fields = {
            "phone": phone,
            "discord_username": discord_username,
            "linkedin_url": linkedin_url,
            "github_username": github_username,
        }
        for local_key, value in optional_fields.items():
            if value:
                updates[field_map[local_key]] = value

        completed_field = (settings.crm_intake_completed_field or "").strip()
        if submitted_at and completed_field:
            updates[completed_field] = submitted_at

        try:
            self.api.request("PUT", f"Contact/{contact_id}", updates)
        except EspoAPIError as exc:
            logger.error("CRM update failed contact_id=%s error=%s", contact_id, exc)
            return {"success": False, "error": "CRM update failed"}

        logger.info(
            "Intake applied contact_id=%s fields=%s", contact_id, sorted(updates.keys())
        )
        return {
            "success": True,
            "contact_id": contact_id,
            "updated_fields": sorted(updates.keys()),
        }
