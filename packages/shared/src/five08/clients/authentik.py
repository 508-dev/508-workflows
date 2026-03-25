"""Shared Authentik admin API helpers."""

from __future__ import annotations

from typing import Any

import requests


class AuthentikAPIError(Exception):
    """Raised when an Authentik API call fails."""


def _normalize_api_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/v3"):
        return normalized
    return f"{normalized}/api/v3"


class AuthentikClient:
    """Minimal client for Authentik's admin user endpoints."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = _normalize_api_base_url(base_url)
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.status_code: int | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request to the Authentik admin API."""
        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = requests.request(
                method.upper(),
                url,
                headers=self._headers(),
                params=params,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AuthentikAPIError(f"HTTP request failed: {exc}") from exc

        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            message = str(getattr(response, "reason", "") or "").strip()
            if not message:
                message = "Upstream error"
            raise AuthentikAPIError(
                f"Authentik request failed with status {response.status_code}: {message}"
            )

        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            body_preview = " ".join((response.text or "").strip().split())
            if len(body_preview) > 200:
                body_preview = body_preview[:200] + "..."
            if not body_preview:
                body_preview = "<empty>"
            raise AuthentikAPIError(
                f"Failed to decode JSON response (status {response.status_code}). "
                f"Body preview: {body_preview}"
            ) from exc

    def list_users(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.request("GET", "core/users/", params=params)
        if not isinstance(response, dict):
            raise AuthentikAPIError("API response is not a JSON object")
        return response

    def get_user(self, user_id: int | str) -> dict[str, Any]:
        response = self.request("GET", f"core/users/{user_id}/")
        if not isinstance(response, dict):
            raise AuthentikAPIError("API response is not a JSON object")
        return response

    def list_email_stages(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.request("GET", "stages/email/", params=params)
        if not isinstance(response, dict):
            raise AuthentikAPIError("API response is not a JSON object")
        return response

    def create_user(
        self,
        *,
        username: str,
        name: str,
        email: str | None = None,
        is_active: bool = True,
        path: str | None = None,
        user_type: str = "internal",
        groups: list[str] | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "username": username,
            "name": name,
            "is_active": is_active,
            "type": user_type,
        }
        if email:
            payload["email"] = email
        if path:
            payload["path"] = path
        if groups:
            payload["groups"] = groups
        if roles:
            payload["roles"] = roles
        if attributes is not None:
            payload["attributes"] = attributes

        response = self.request("POST", "core/users/", payload=payload)
        if not isinstance(response, dict):
            raise AuthentikAPIError("API response is not a JSON object")
        return response

    def send_recovery_email(
        self,
        *,
        user_id: int | str,
        email_stage: str,
        token_duration: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"email_stage": email_stage}
        if token_duration:
            payload["token_duration"] = token_duration
        self.request(
            "POST",
            f"core/users/{user_id}/recovery_email/",
            payload=payload,
        )

    @staticmethod
    def _stage_pk(stage: dict[str, Any]) -> str:
        raw_value = stage.get("pk")
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        raise AuthentikAPIError("Authentik stage response did not include a UUID.")

    def resolve_email_stage_id(
        self,
        *,
        stage_name: str,
        stage_id: str | None = None,
        page_size: int = 20,
    ) -> str:
        """Resolve one Authentik Email Stage UUID, preferring an explicit override."""
        if isinstance(stage_id, str) and stage_id.strip():
            return stage_id.strip()

        normalized_name = stage_name.strip()
        if not normalized_name:
            raise AuthentikAPIError("Authentik email stage name must not be empty.")

        response = self.list_email_stages(
            params={"name": normalized_name, "page_size": page_size}
        )
        raw_results = response.get("results")
        results = raw_results if isinstance(raw_results, list) else []
        matches = [
            stage
            for stage in results
            if isinstance(stage, dict)
            and str(stage.get("name") or "").strip() == normalized_name
        ]

        if not matches:
            raise AuthentikAPIError(
                f"No Authentik email stage found named '{normalized_name}'."
            )
        if len(matches) > 1:
            raise AuthentikAPIError(
                f"Multiple Authentik email stages matched '{normalized_name}'."
            )

        return self._stage_pk(matches[0])

    def find_users_by_username_or_email(
        self,
        *,
        username: str,
        email: str,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Search exact username and exact email, deduplicated by user id."""
        matches: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        normalized_username = username.casefold()
        normalized_email = email.casefold()

        for params, field_name, expected in (
            (
                {"username": username, "page_size": page_size},
                "username",
                normalized_username,
            ),
            ({"email": email, "page_size": page_size}, "email", normalized_email),
        ):
            response = self.list_users(params=params)
            raw_results = response.get("results")
            results = raw_results if isinstance(raw_results, list) else []
            for user in results:
                if not isinstance(user, dict):
                    continue
                if str(user.get(field_name) or "").casefold() != expected:
                    continue
                key = str(
                    user.get("pk")
                    or user.get("uuid")
                    or user.get("uid")
                    or f"{user.get('username')}:{user.get('email')}"
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                matches.append(user)

        return matches
