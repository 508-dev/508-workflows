"""Outline API client helpers shared across services."""

from __future__ import annotations

from typing import Any

import requests


OUTLINE_API_BASE_URL = "https://app.getoutline.com/api"


class OutlineAPIError(RuntimeError):
    """Raised when the Outline API request fails or returns invalid data."""


class OutlineClient:
    """Small Outline RPC API wrapper for user invitations."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = OUTLINE_API_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call one Outline RPC method and return the JSON response."""
        try:
            response = requests.post(
                f"{self.base_url}/{method.lstrip('/')}",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise OutlineAPIError(f"Outline API request failed: {exc}") from exc

        if not 200 <= response.status_code < 300:
            raise OutlineAPIError(
                "Outline API request failed: "
                f"status={response.status_code}, body={response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise OutlineAPIError(
                "Outline response payload must be valid JSON."
            ) from exc

        if not isinstance(data, dict):
            raise OutlineAPIError("Outline response payload must be a JSON object.")

        if data.get("ok") is False:
            error = str(data.get("error") or "unknown error")
            raise OutlineAPIError(f"Outline API returned an error: {error}")

        return data

    def invite_user(
        self,
        *,
        email: str,
        name: str | None = None,
        role: str | None = None,
        suppress_email: bool = False,
    ) -> dict[str, Any]:
        """Invite one user to Outline and return the response payload."""
        invite: dict[str, Any] = {"email": email}
        if name:
            invite["name"] = name
        if role:
            invite["role"] = role

        return self.request(
            "users.invite",
            {
                "invites": [invite],
                "suppressEmail": suppress_email,
            },
        )
