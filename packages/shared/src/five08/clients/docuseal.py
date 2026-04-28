"""Shared DocuSeal client helpers."""

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from five08.tls import default_ca_bundle_path


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
        self.base_url = normalize_docuseal_base_url(base_url)
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

        response = self._send_request(method, url, headers, payload)
        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            message = response.text.strip() or "Unknown Error"
            raise DocusealAPIError(
                f"DocuSeal request to {url} failed: "
                f"status code is {response.status_code}, reason is {message}"
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
            raise DocusealAPIError(
                f"Failed to decode JSON response (status {response.status_code}). "
                f"Body preview: {body_preview}"
            ) from exc
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

    def _send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
    ) -> requests.Response:
        """Send one HTTP request to DocuSeal."""
        try:
            return requests.request(
                method.upper(),
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            raise DocusealAPIError(f"HTTP request failed: {exc}") from exc


def normalize_docuseal_base_url(base_url: str) -> str:
    """Normalize DocuSeal Cloud URLs and self-hosted root URLs to the API base."""
    normalized = base_url.strip().rstrip("/")
    parts = urlsplit(normalized)
    if not parts.scheme or not parts.netloc or not parts.hostname:
        raise ValueError(
            "DOCUSEAL_BASE_URL must be an absolute URL including scheme and host."
        )
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")).rstrip("/")
    hostname = (parts.hostname or "").lower()
    if hostname in {"docuseal.com", "www.docuseal.com"}:
        return "https://api.docuseal.com"

    if hostname == "api.docuseal.com":
        return "https://api.docuseal.com"

    if parts.path in {"", "/"}:
        return urlunsplit(
            (
                parts.scheme or "https",
                parts.netloc,
                "/api",
                "",
                "",
            )
        ).rstrip("/")

    return cleaned


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
