"""Google Forms member intake processing workflow."""

import logging
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.worker.config import settings

logger = logging.getLogger(__name__)

# NOTE: These CRM field names follow the c-prefix convention observed in the
# codebase (cDiscordUsername, cGitHubUsername, cLinkedInUrl).  The intake-specific
# field ``cIntakeCompletedAt`` is a placeholder — the actual field name should be
# confirmed by the CRM administrator.


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
            logger.error("CRM search failed email=%s error=%s", email, exc)
            return {"success": False, "error": f"CRM search failed: {exc}"}

        contact_list = contacts.get("list", [])
        if not contact_list:
            logger.warning("No CRM contact found for email=%s", email)
            return {"success": False, "error": f"No contact found for {email}"}

        contact = contact_list[0]
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

        if submitted_at:
            updates["cIntakeCompletedAt"] = submitted_at

        try:
            self.api.request("PUT", f"Contact/{contact_id}", updates)
        except EspoAPIError as exc:
            logger.error(
                "CRM update failed contact_id=%s error=%s", contact_id, exc
            )
            return {"success": False, "error": f"CRM update failed: {exc}"}

        logger.info("Intake applied contact_id=%s fields=%s", contact_id, sorted(updates.keys()))
        return {
            "success": True,
            "contact_id": contact_id,
            "updated_fields": sorted(updates.keys()),
        }
