"""Keila API client helpers shared across services."""

from __future__ import annotations

from typing import Any

import requests

KEILA_API_BASE_URL = "https://app.keila.io"


class KeilaAPIError(RuntimeError):
    """Raised when the Keila API request fails or returns invalid data."""


class KeilaClient:
    """Small Keila API wrapper for contact synchronization."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = KEILA_API_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get_contact_by_email(self, email: str) -> dict[str, Any] | None:
        """Return one Keila contact by email, or None when it does not exist."""
        normalized_email = email.strip().lower()
        response = self._request(
            "GET",
            f"/api/v1/contacts/{normalized_email}",
            params={"id_type": "email"},
            allow_not_found=True,
        )
        return response

    def upsert_active_contact(
        self,
        *,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a Keila contact without changing suppressed statuses."""
        normalized_email = email.strip().lower()
        if not normalized_email or normalized_email.count("@") != 1:
            raise ValueError("Keila contact email must be a full email address.")

        payload: dict[str, Any] = {
            "email": normalized_email,
            "status": "active",
            "data": data or {},
        }
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name

        existing = self.get_contact_by_email(normalized_email)
        if existing is None:
            return (
                self._request("POST", "/api/v1/contacts", json={"data": payload}) or {}
            )

        contact_id = str(existing.get("id") or normalized_email)
        payload.pop("status", None)
        return (
            self._request(
                "PATCH",
                f"/api/v1/contacts/{contact_id}",
                json={"data": payload},
            )
            or {}
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if json is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                params=params,
                json=json,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise KeilaAPIError(f"Keila API request failed: {exc}") from exc

        if allow_not_found and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            raise KeilaAPIError(
                "Keila API request failed: "
                f"status={response.status_code}, body={response.text}"
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise KeilaAPIError("Keila response payload must be valid JSON.") from exc
        if not isinstance(data, dict):
            raise KeilaAPIError("Keila response payload must be a JSON object.")
        nested = data.get("data")
        if isinstance(nested, dict):
            return nested
        return data
