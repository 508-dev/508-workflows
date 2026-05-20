"""ERPNext/Frappe API client helpers."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urljoin

import requests

from five08.tls import default_ca_bundle_path


class ERPNextAPIError(Exception):
    """Raised when ERPNext returns an error or unexpected response."""


class ERPNextClient:
    """Small authenticated client for ERPNext resource APIs."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.status_code: int | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"token {api_key}",
            }
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one request to ERPNext and return a JSON object."""
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self._session.request(
                method.upper(),
                url,
                params=params,
                json=payload,
                timeout=self.timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            raise ERPNextAPIError(f"HTTP request failed: {exc}") from exc

        self.status_code = response.status_code
        if not 200 <= response.status_code < 300:
            detail = self._error_detail(response)
            raise ERPNextAPIError(
                f"ERPNext request failed status={response.status_code}: {detail}"
            )

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise ERPNextAPIError("ERPNext response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ERPNextAPIError("ERPNext response is not a JSON object")
        return data

    def list_projects(
        self,
        *,
        status: str | None = "Open",
        limit: int = 500,
        offset: int = 0,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List ERPNext Project records."""
        selected_fields = fields or [
            "name",
            "project_name",
            "status",
            "customer",
            "project_type",
            "priority",
            "percent_complete",
            "expected_start_date",
            "expected_end_date",
            "actual_start_date",
            "actual_end_date",
            "modified",
            "_assign",
        ]
        params: dict[str, Any] = {
            "fields": json.dumps(selected_fields),
            "limit_page_length": str(max(1, limit)),
            "limit_start": str(max(0, offset)),
            "order_by": "modified desc",
        }
        if status:
            params["filters"] = json.dumps([["Project", "status", "=", status]])

        data = self.request("GET", "/api/resource/Project", params=params)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ERPNextAPIError("ERPNext Project list response is missing data rows")
        return [row for row in rows if isinstance(row, dict)]

    def list_records(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[Any] | None = None,
        or_filters: list[Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List generic ERPNext records for small dashboard lookups."""
        normalized_doctype = doctype.strip()
        if not normalized_doctype:
            raise ERPNextAPIError("DocType is required")
        params: dict[str, Any] = {
            "fields": json.dumps(fields),
            "limit_page_length": str(max(1, limit)),
        }
        if filters:
            params["filters"] = json.dumps(filters)
        if or_filters:
            params["or_filters"] = json.dumps(or_filters)

        data = self.request(
            "GET",
            f"/api/resource/{quote(normalized_doctype, safe='')}",
            params=params,
        )
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ERPNextAPIError(
                f"ERPNext {normalized_doctype} list response is missing data rows"
            )
        return [row for row in rows if isinstance(row, dict)]

    def search_users(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search ERPNext User records by email, id, or full name."""
        normalized_query = query.strip()
        if not normalized_query:
            return []
        if "@" in normalized_query:
            or_filters = [
                ["User", "name", "=", normalized_query],
                ["User", "email", "=", normalized_query],
            ]
        else:
            like_query = f"%{normalized_query}%"
            or_filters = [
                ["User", "name", "like", like_query],
                ["User", "email", "like", like_query],
                ["User", "full_name", "like", like_query],
            ]
        return self.list_records(
            "User",
            fields=["name", "email", "full_name", "enabled"],
            or_filters=or_filters,
            limit=limit,
        )

    def search_suppliers(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search ERPNext Supplier records by email, id, or supplier name."""
        normalized_query = query.strip()
        if not normalized_query:
            return []
        if "@" in normalized_query:
            or_filters = [
                ["Supplier", "name", "=", normalized_query],
                ["Supplier", "email_id", "=", normalized_query],
            ]
        else:
            like_query = f"%{normalized_query}%"
            or_filters = [
                ["Supplier", "name", "like", like_query],
                ["Supplier", "supplier_name", "like", like_query],
                ["Supplier", "email_id", "like", like_query],
            ]
        return self.list_records(
            "Supplier",
            fields=["name", "supplier_name", "email_id", "disabled", "is_frozen"],
            filters=[
                ["Supplier", "disabled", "=", 0],
                ["Supplier", "is_frozen", "=", 0],
            ],
            or_filters=or_filters,
            limit=limit,
        )

    def get_project(self, project_id: str) -> dict[str, Any]:
        """Read one ERPNext Project detail document."""
        normalized_id = project_id.strip()
        if not normalized_id:
            raise ERPNextAPIError("Project id is required")
        data = self.request(
            "GET",
            f"/api/resource/Project/{quote(normalized_id, safe='')}",
        )
        row = data.get("data")
        if not isinstance(row, dict):
            raise ERPNextAPIError("ERPNext Project detail is not an object")
        return row

    def update_project(self, project_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Update one ERPNext Project document and return the refreshed document."""
        normalized_id = project_id.strip()
        if not normalized_id:
            raise ERPNextAPIError("Project id is required")
        if not fields:
            return self.get_project(normalized_id)
        data = self.request(
            "PUT",
            f"/api/resource/Project/{quote(normalized_id, safe='')}",
            payload=fields,
        )
        row = data.get("data")
        if isinstance(row, dict):
            return row
        return self.get_project(normalized_id)

    def set_project_status(self, project_id: str, status: str) -> dict[str, Any]:
        """Set one ERPNext Project status."""
        normalized_status = status.strip()
        if not normalized_status:
            raise ERPNextAPIError("Project status is required")
        return self.update_project(project_id, {"status": normalized_status})

    def set_project_type(self, project_id: str, project_type: str) -> dict[str, Any]:
        """Set one ERPNext Project type."""
        normalized_type = project_type.strip()
        if not normalized_type:
            raise ERPNextAPIError("Project type is required")
        return self.update_project(project_id, {"project_type": normalized_type})

    def add_project_user(self, project_id: str, user: str) -> dict[str, Any]:
        """Append one ERPNext User to Project.users when absent."""
        normalized_user = user.strip()
        if not normalized_user:
            raise ERPNextAPIError("Project user is required")
        project = self.get_project(project_id)
        raw_users = project.get("users")
        current_users = raw_users if isinstance(raw_users, list) else []
        next_users: list[dict[str, Any]] = []
        for raw_user in current_users:
            if not isinstance(raw_user, dict):
                continue
            existing_user = str(raw_user.get("user") or "").strip().casefold()
            existing_email = str(raw_user.get("email") or "").strip().casefold()
            if normalized_user.casefold() in {existing_user, existing_email}:
                return project
            next_user = {
                "user": raw_user.get("user"),
                "view_attachments": raw_user.get("view_attachments", 0),
                "hide_timesheets": raw_user.get("hide_timesheets", 0),
            }
            if raw_user.get("name"):
                next_user["name"] = raw_user.get("name")
            next_users.append(next_user)
        next_users.append(
            {
                "user": normalized_user,
                "view_attachments": 0,
                "hide_timesheets": 0,
            }
        )
        return self.update_project(project_id, {"users": next_users})

    def remove_project_user(self, project_id: str, user: str) -> dict[str, Any]:
        """Remove one ERPNext User from Project.users when present."""
        normalized_user = user.strip()
        if not normalized_user:
            raise ERPNextAPIError("Project user is required")
        project = self.get_project(project_id)
        raw_users = project.get("users")
        current_users = raw_users if isinstance(raw_users, list) else []
        next_users: list[dict[str, Any]] = []
        removed = False
        for raw_user in current_users:
            if not isinstance(raw_user, dict):
                continue
            existing_user = str(raw_user.get("user") or "").strip().casefold()
            existing_email = str(raw_user.get("email") or "").strip().casefold()
            if normalized_user.casefold() in {existing_user, existing_email}:
                removed = True
                continue
            next_user = {
                "user": raw_user.get("user"),
                "view_attachments": raw_user.get("view_attachments", 0),
                "hide_timesheets": raw_user.get("hide_timesheets", 0),
            }
            if raw_user.get("name"):
                next_user["name"] = raw_user.get("name")
            next_users.append(next_user)
        if not removed:
            return project
        return self.update_project(project_id, {"users": next_users})

    @staticmethod
    def _error_detail(response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text.strip()[:500] or "Unknown error"
        if not isinstance(data, dict):
            return str(data)[:500]
        for key in ("_error_message", "message", "exception"):
            value = data.get(key)
            if value:
                return str(value)[:500]
        server_messages = data.get("_server_messages")
        if server_messages:
            return str(server_messages)[:500]
        return str(data)[:500]
