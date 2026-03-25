"""Shared Authentik admin API helpers."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests


class AuthentikAPIError(Exception):
    """Raised when an Authentik API call fails."""


logger = logging.getLogger(__name__)


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

    @staticmethod
    def _redirect_location(response: requests.Response) -> str | None:
        location = response.headers.get("Location")
        if not isinstance(location, str):
            return None
        normalized = location.strip()
        return normalized or None

    @staticmethod
    def _normalize_error_text(value: Any) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) > 200:
            return text[:200].rstrip() + "..."
        return text

    @classmethod
    def _response_error_summary(cls, response: requests.Response) -> str:
        reason = str(getattr(response, "reason", "") or "").strip() or "Upstream error"
        try:
            payload = response.json()
        except ValueError:
            return reason

        detail = ""
        if isinstance(payload, dict):
            if "detail" in payload:
                detail = cls._normalize_error_text(payload.get("detail"))
            elif "non_field_errors" in payload:
                detail = cls._normalize_error_text(payload.get("non_field_errors"))
            else:
                for key, value in payload.items():
                    normalized_value = cls._normalize_error_text(value)
                    if normalized_value:
                        detail = f"{key}: {normalized_value}"
                        break
        elif isinstance(payload, list) and payload:
            detail = cls._normalize_error_text(payload[0])

        if not detail:
            return reason
        return f"{reason} ({detail})"

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
        response: requests.Response | None = None
        request_method = method.upper()
        redirect_limit = 5

        for redirect_count in range(redirect_limit):
            logger.debug(
                "Authentik request method=%s path=%s params=%s payload_keys=%s redirect_count=%s",
                request_method,
                path,
                params,
                sorted(payload.keys()) if isinstance(payload, dict) else None,
                redirect_count,
            )

            try:
                response = requests.request(
                    request_method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=payload,
                    timeout=self.timeout_seconds,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise AuthentikAPIError(f"HTTP request failed: {exc}") from exc

            if response.status_code not in {301, 302, 307, 308}:
                break

            location = self._redirect_location(response)
            if not location:
                break

            redirected_url = urljoin(url, location)
            logger.debug(
                "Authentik redirect method=%s from=%s to=%s status=%s",
                request_method,
                url,
                redirected_url,
                response.status_code,
            )
            url = redirected_url
        else:
            raise AuthentikAPIError("Too many redirects from Authentik API.")

        if response is None:
            raise AuthentikAPIError("No response received from Authentik API.")

        self.status_code = response.status_code
        logger.debug(
            "Authentik response method=%s path=%s status=%s",
            request_method,
            path,
            response.status_code,
        )
        if not 200 <= response.status_code < 300:
            message = self._response_error_summary(response)
            logger.debug(
                "Authentik error method=%s path=%s status=%s summary=%s",
                request_method,
                path,
                response.status_code,
                message,
            )
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

    @staticmethod
    def _pagination_next_page(response: dict[str, Any]) -> int | None:
        pagination = response.get("pagination")
        if not isinstance(pagination, dict):
            return None

        raw_next = pagination.get("next")
        if isinstance(raw_next, int):
            return raw_next if raw_next > 0 else None
        if isinstance(raw_next, str):
            normalized = raw_next.strip()
            if normalized.isdigit():
                next_page = int(normalized)
                return next_page if next_page > 0 else None

        raw_current = pagination.get("current")
        raw_total_pages = pagination.get("total_pages")
        if isinstance(raw_current, int) and isinstance(raw_total_pages, int):
            if raw_current < raw_total_pages:
                return raw_current + 1

        return None

    def _paginated_results(
        self,
        *,
        list_method: Any,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_params = dict(params or {})
        page = 1
        results: list[dict[str, Any]] = []
        seen_pages: set[int] = set()

        while True:
            page_params = dict(normalized_params)
            page_params["page"] = page
            response = list_method(params=page_params)
            raw_results = response.get("results")
            page_results = raw_results if isinstance(raw_results, list) else []
            for item in page_results:
                if isinstance(item, dict):
                    results.append(item)

            next_page = self._pagination_next_page(response)
            if next_page is None or next_page in seen_pages:
                break

            seen_pages.add(page)
            page = next_page

        return results

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
        logger.debug(
            "Authentik send_recovery_email user_id=%s email_stage=%s token_duration_present=%s",
            user_id,
            email_stage,
            bool(token_duration),
        )
        payload: dict[str, Any] = {"email_stage": email_stage}
        params: dict[str, Any] = {"email_stage": email_stage}
        if token_duration:
            payload["token_duration"] = token_duration
            params["token_duration"] = token_duration
        try:
            self.request(
                "POST",
                f"core/users/{user_id}/recovery_email/",
                payload=payload,
            )
        except AuthentikAPIError:
            if self.status_code != 400:
                raise
            logger.debug(
                "Authentik send_recovery_email retrying with query params user_id=%s email_stage=%s",
                user_id,
                email_stage,
            )
            self.request(
                "POST",
                f"core/users/{user_id}/recovery_email/",
                params=params,
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
            normalized_stage_id = stage_id.strip()
            logger.debug(
                "Authentik resolve_email_stage_id using explicit override stage_id=%s",
                normalized_stage_id,
            )
            return normalized_stage_id

        normalized_name = stage_name.strip()
        if not normalized_name:
            raise AuthentikAPIError("Authentik email stage name must not be empty.")

        logger.debug(
            "Authentik resolve_email_stage_id searching by name=%s page_size=%s",
            normalized_name,
            page_size,
        )
        results = self._paginated_results(
            list_method=self.list_email_stages,
            params={"name": normalized_name, "page_size": page_size},
        )
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

        resolved_stage_id = self._stage_pk(matches[0])
        logger.debug(
            "Authentik resolve_email_stage_id resolved name=%s stage_id=%s",
            normalized_name,
            resolved_stage_id,
        )
        return resolved_stage_id

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
            results = self._paginated_results(
                list_method=self.list_users,
                params=params,
            )
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
