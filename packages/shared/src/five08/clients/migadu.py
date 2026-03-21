"""Migadu API client helpers shared across services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

MIGADU_API_BASE_URL = "https://api.migadu.com/v1"


class MigaduAPIError(ValueError):
    """Raised when the Migadu API request fails or returns invalid data."""


def normalize_migadu_mailbox_domain(domain: str | None) -> str:
    """Normalize the configured Migadu mailbox domain."""
    normalized = (domain or "508.dev").strip().lower().lstrip(".")
    if not normalized:
        return "508.dev"
    return normalized


@dataclass(frozen=True, slots=True)
class MigaduMailboxCreateRequest:
    """Payload fields required to create a Migadu mailbox."""

    local_part: str
    backup_email: str
    name: str


class MigaduClient:
    """Small Migadu API wrapper for mailbox creation."""

    def __init__(
        self,
        *,
        username: str,
        api_key: str,
        domain: str,
        base_url: str = MIGADU_API_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.username = username
        self.api_key = api_key
        self.domain = normalize_migadu_mailbox_domain(domain)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def create_mailbox(self, request: MigaduMailboxCreateRequest) -> dict[str, Any]:
        """Create one Migadu mailbox and return the JSON response."""
        payload = {
            "local_part": request.local_part,
            "name": request.name,
            "password_method": "invitation",
            "password_recovery_email": request.backup_email,
            "forwarding_to": request.backup_email,
        }

        try:
            response = requests.post(
                f"{self.base_url}/domains/{self.domain}/mailboxes",
                auth=(self.username, self.api_key),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise MigaduAPIError(f"Migadu API request failed: {exc}") from exc

        if response.status_code not in {200, 201}:
            raise MigaduAPIError(
                "Migadu mailbox creation failed: "
                f"status={response.status_code}, body={response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise MigaduAPIError("Migadu response payload must be valid JSON.") from exc

        if not isinstance(data, dict):
            raise MigaduAPIError("Migadu response payload must be a JSON object.")

        return data
