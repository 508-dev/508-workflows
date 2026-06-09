"""Brevo API client helpers shared across services."""

from __future__ import annotations

from typing import Any

import requests

BREVO_API_BASE_URL = "https://api.brevo.com/v3"


class BrevoAPIError(RuntimeError):
    """Raised when the Brevo API request fails or returns invalid data."""


class BrevoClient:
    """Small Brevo API wrapper for newsletter contact subscriptions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = BREVO_API_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def add_contact_to_list(
        self,
        *,
        email: str,
        list_id: int,
    ) -> dict[str, Any]:
        """Create or update one Brevo contact and add it to a list."""
        normalized_email = email.strip().lower()
        if not normalized_email or normalized_email.count("@") != 1:
            raise ValueError("Brevo contact email must be a full email address.")
        if list_id <= 0:
            raise ValueError("Brevo list ID must be a positive integer.")

        payload = {
            "email": normalized_email,
            "listIds": [list_id],
            "updateEnabled": True,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        try:
            response = requests.post(
                f"{self.base_url}/contacts",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise BrevoAPIError(f"Brevo API request failed: {exc}") from exc

        if response.status_code not in {200, 201, 204}:
            raise BrevoAPIError(
                "Brevo contact subscription failed: "
                f"status={response.status_code}, body={response.text}"
            )

        if not response.content:
            return {}

        try:
            data = response.json()
        except ValueError as exc:
            raise BrevoAPIError("Brevo response payload must be valid JSON.") from exc

        if not isinstance(data, dict):
            raise BrevoAPIError("Brevo response payload must be a JSON object.")
        return data
