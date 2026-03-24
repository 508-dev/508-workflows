"""Shared client helpers for authenticated Discord bot internal APIs."""

from typing import Any

import requests


class DiscordBotAPIError(Exception):
    """Raised when a Discord bot internal API call fails."""


class DiscordBotClient:
    """Minimal client for internal Discord bot automation endpoints."""

    def __init__(
        self,
        base_url: str,
        api_secret: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_secret = api_secret
        self.timeout_seconds = timeout_seconds
        self.status_code: int | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request to the Discord bot internal API."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "X-API-Secret": self.api_secret,
        }

        try:
            response = requests.request(
                method.upper(),
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise DiscordBotAPIError(f"HTTP request failed: {exc}") from exc

        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            message = response.text.strip() or "Unknown Error"
            raise DiscordBotAPIError(
                f"Wrong request, status code is {response.status_code}, reason is {message}"
            )

        if not response.content:
            return {}

        try:
            json_data = response.json()
        except ValueError as exc:
            body_preview = " ".join((response.text or "").strip().split())
            if len(body_preview) > 200:
                body_preview = body_preview[:200] + "..."
            if not body_preview:
                body_preview = "<empty>"
            raise DiscordBotAPIError(
                f"Failed to decode JSON response (status {response.status_code}). "
                f"Body preview: {body_preview}"
            ) from exc
        if not isinstance(json_data, dict):
            raise DiscordBotAPIError("API response is not a JSON object")
        return json_data

    def grant_member_role(
        self,
        *,
        discord_user_id: str,
        contact_id: str | None = None,
        contact_name: str | None = None,
        submission_id: int | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any]:
        """Ask the Discord bot to add the Member role to one linked user."""
        payload = {
            "discord_user_id": discord_user_id,
            "contact_id": contact_id,
            "contact_name": contact_name,
            "submission_id": submission_id,
            "completed_at": completed_at,
        }
        return self.request(
            "POST",
            "internal/member-agreements/member-role",
            payload,
        )


def grant_member_role_for_signed_agreement(
    *,
    base_url: str,
    api_secret: str,
    discord_user_id: str,
    contact_id: str | None = None,
    contact_name: str | None = None,
    submission_id: int | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Shared helper for granting Member role after agreement signing."""
    client = DiscordBotClient(base_url, api_secret)
    return client.grant_member_role(
        discord_user_id=discord_user_id,
        contact_id=contact_id,
        contact_name=contact_name,
        submission_id=submission_id,
        completed_at=completed_at,
    )
