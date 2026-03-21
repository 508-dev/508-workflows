"""Shared DocuSeal client helpers."""

from typing import Any

import requests


class DocusealAPIError(Exception):
    """Raised when a DocuSeal API call fails."""


class DocusealClient:
    """Minimal client for the DocuSeal submissions API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.status_code: int | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request to DocuSeal."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": self.api_key,
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
            raise DocusealAPIError(f"HTTP request failed: {exc}") from exc

        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            message = response.text.strip() or "Unknown Error"
            raise DocusealAPIError(
                f"Wrong request, status code is {response.status_code}, reason is {message}"
            )

        if not response.content:
            return {}

        json_data = response.json()
        if not isinstance(json_data, dict):
            raise DocusealAPIError("API response is not a JSON object")
        return json_data

    def create_submission(
        self,
        *,
        template_id: int,
        submitter_name: str | None,
        submitter_email: str,
        send_email: bool = True,
    ) -> dict[str, Any]:
        """Create one submission for the configured member agreement template."""
        submitter: dict[str, Any] = {
            "role": "First Party",
            "email": submitter_email,
        }
        normalized_name = (submitter_name or "").strip()
        if normalized_name:
            submitter["name"] = normalized_name

        payload = {
            "template_id": template_id,
            "send_email": send_email,
            "submitters": [submitter],
        }
        return self.request("POST", "submissions", payload)


def create_member_agreement_submission(
    *,
    base_url: str,
    api_key: str,
    template_id: int,
    submitter_name: str | None,
    submitter_email: str,
    send_email: bool = True,
) -> dict[str, Any]:
    """Shared helper for creating a member agreement submission."""
    client = DocusealClient(base_url, api_key)
    return client.create_submission(
        template_id=template_id,
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        send_email=send_email,
    )
