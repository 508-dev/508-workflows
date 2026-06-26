"""Keila API client helpers shared across services."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from five08.clients.contact_email import normalize_provider_contact_email
from five08.redaction import redact_email_addresses

KEILA_API_BASE_URL = "https://app.keila.io"
ERROR_BODY_MAX_LENGTH = 500
_EXISTING_CONTACT_UNSET = object()


class KeilaAPIError(RuntimeError):
    """Raised when the Keila API request fails or returns invalid data."""


def _response_body_excerpt(body: object) -> str:
    """Return a bounded response-body excerpt for persisted/logged errors."""
    text = redact_email_addresses(" ".join(str(body or "").split()))
    if len(text) <= ERROR_BODY_MAX_LENGTH:
        return text
    return f"{text[:ERROR_BODY_MAX_LENGTH]}..."


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
        normalized_email = normalize_provider_contact_email(email, "Keila")
        response = self._request(
            "GET",
            f"/api/v1/contacts/{quote(normalized_email, safe='')}",
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
        existing_contact: dict[str, Any] | None | object = _EXISTING_CONTACT_UNSET,
    ) -> dict[str, Any]:
        """Create or update a Keila contact without changing suppressed statuses."""
        normalized_email = normalize_provider_contact_email(email, "Keila")

        payload: dict[str, Any] = {
            "email": normalized_email,
            "status": "active",
            "data": data or {},
        }
        if first_name:
            payload["first_name"] = first_name
        if last_name:
            payload["last_name"] = last_name

        if existing_contact is _EXISTING_CONTACT_UNSET:
            existing = self.get_contact_by_email(normalized_email)
        else:
            if existing_contact is not None and not isinstance(existing_contact, dict):
                raise TypeError("existing_contact must be a Keila contact object.")
            existing = existing_contact
        if existing is None:
            return (
                self._request("POST", "/api/v1/contacts", json={"data": payload}) or {}
            )

        contact_id_value = existing.get("id")
        if not contact_id_value:
            raise KeilaAPIError(
                f"Existing Keila contact {normalized_email} missing id."
            )
        contact_id = str(contact_id_value)
        existing_data = existing.get("data")
        payload["data"] = _merge_contact_data(existing_data, data or {})
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
                f"status={response.status_code}, "
                f"body={_response_body_excerpt(response.text)}"
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


def _merge_contact_data(
    existing_data: object,
    new_data: dict[str, Any],
) -> dict[str, Any]:
    """Merge Keila contact data while preserving existing audience tags."""
    existing = existing_data if isinstance(existing_data, dict) else {}
    merged = {**existing, **new_data}
    existing_audiences = existing.get("audiences")
    new_audiences = new_data.get("audiences")
    if isinstance(existing_audiences, list) and isinstance(new_audiences, list):
        merged["audiences"] = _unique_values([*existing_audiences, *new_audiences])
    return merged


def _unique_values(values: list[Any]) -> list[Any]:
    unique: list[Any] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique
