"""ERPNext/Frappe API client helpers."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urljoin

import requests

from five08.tls import default_ca_bundle_path


def _email_search_text_without_tld(email: str) -> str:
    normalized_email = email.strip().lower()
    if "@" not in normalized_email:
        return normalized_email
    local_part, domain = normalized_email.rsplit("@", 1)
    if "." not in domain:
        return normalized_email
    domain_without_tld = domain.rsplit(".", 1)[0]
    return f"{local_part}@{domain_without_tld}"


def _contact_email_matches_query(email: Any, query: str) -> bool:
    normalized_email = str(email or "").strip().lower()
    normalized_query = query.strip().lower()
    if not (normalized_email and normalized_query):
        return False
    if "@" in normalized_query:
        return normalized_query in normalized_email
    return normalized_query in _email_search_text_without_tld(normalized_email)


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

    def create_record(self, doctype: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create one generic ERPNext document and return the created document."""
        normalized_doctype = doctype.strip()
        if not normalized_doctype:
            raise ERPNextAPIError("DocType is required")
        if not payload:
            raise ERPNextAPIError(f"{normalized_doctype} payload is required")
        data = self.request(
            "POST",
            f"/api/resource/{quote(normalized_doctype, safe='')}",
            payload=payload,
        )
        row = data.get("data")
        if not isinstance(row, dict):
            raise ERPNextAPIError(
                f"ERPNext {normalized_doctype} create response is not an object"
            )
        return row

    def update_record(
        self,
        doctype: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Update one generic ERPNext document and return the updated document."""
        normalized_doctype = doctype.strip()
        normalized_id = record_id.strip()
        if not normalized_doctype:
            raise ERPNextAPIError("DocType is required")
        if not normalized_id:
            raise ERPNextAPIError(f"{normalized_doctype} id is required")
        if not fields:
            return self.get_record(normalized_doctype, normalized_id)
        data = self.request(
            "PUT",
            f"/api/resource/{quote(normalized_doctype, safe='')}/{quote(normalized_id, safe='')}",
            payload=fields,
        )
        row = data.get("data")
        if isinstance(row, dict):
            return row
        return self.get_record(normalized_doctype, normalized_id)

    def delete_record(self, doctype: str, record_id: str) -> None:
        """Delete one generic ERPNext document."""
        normalized_doctype = doctype.strip()
        normalized_id = record_id.strip()
        if not normalized_doctype:
            raise ERPNextAPIError("DocType is required")
        if not normalized_id:
            raise ERPNextAPIError(f"{normalized_doctype} id is required")
        self.request(
            "DELETE",
            f"/api/resource/{quote(normalized_doctype, safe='')}/{quote(normalized_id, safe='')}",
        )

    def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
        """Read one generic ERPNext document."""
        normalized_doctype = doctype.strip()
        normalized_id = record_id.strip()
        if not normalized_doctype:
            raise ERPNextAPIError("DocType is required")
        if not normalized_id:
            raise ERPNextAPIError(f"{normalized_doctype} id is required")
        data = self.request(
            "GET",
            f"/api/resource/{quote(normalized_doctype, safe='')}/{quote(normalized_id, safe='')}",
        )
        row = data.get("data")
        if not isinstance(row, dict):
            raise ERPNextAPIError(
                f"ERPNext {normalized_doctype} detail response is not an object"
            )
        return row

    def call_method(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call one whitelisted Frappe method."""
        normalized_method = method.strip()
        if not normalized_method:
            raise ERPNextAPIError("Frappe method is required")
        return self.request(
            "POST" if payload is not None else "GET",
            f"/api/method/{quote(normalized_method, safe='.')}",
            params=params,
            payload=payload,
        )

    def list_cost_centers(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List active, non-group Cost Center records."""
        return self.list_records(
            "Cost Center",
            fields=["name", "cost_center_name", "company"],
            filters=[
                ["Cost Center", "disabled", "=", 0],
                ["Cost Center", "is_group", "=", 0],
            ],
            limit=limit,
        )

    def search_customers(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search ERPNext Customer records by id or display name."""
        normalized_query = query.strip()
        if not normalized_query:
            return []
        like_query = f"%{normalized_query}%"
        return self.list_records(
            "Customer",
            fields=[
                "name",
                "customer_name",
                "customer_type",
                "default_currency",
                "account_manager",
            ],
            or_filters=[
                ["Customer", "name", "like", like_query],
                ["Customer", "customer_name", "like", like_query],
            ],
            limit=limit,
        )

    def search_contacts(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search ERPNext Contact records by fields shown in the dashboard picker."""
        normalized_query = query.strip()
        if not normalized_query:
            return []
        like_query = f"%{normalized_query}%"
        contact_fields = [
            "name",
            "first_name",
            "last_name",
            "full_name",
            "email_id",
            "mobile_no",
            "phone",
            "company_name",
        ]
        rows_by_name: dict[str, dict[str, Any]] = {}
        visible_rows = self.list_records(
            "Contact",
            fields=contact_fields,
            or_filters=[
                ["Contact", "full_name", "like", like_query],
                ["Contact", "mobile_no", "like", like_query],
                ["Contact", "phone", "like", like_query],
                ["Contact", "company_name", "like", like_query],
            ],
            limit=limit,
        )
        for row in visible_rows:
            contact_id = str(row.get("name") or "").strip()
            if contact_id:
                rows_by_name[contact_id] = row

        email_rows = self.list_records(
            "Contact",
            fields=contact_fields,
            or_filters=[["Contact", "email_id", "like", like_query]],
            limit=max(limit * 5, 50),
        )
        for row in email_rows:
            contact_id = str(row.get("name") or "").strip()
            if not contact_id or contact_id in rows_by_name:
                continue
            if _contact_email_matches_query(row.get("email_id"), normalized_query):
                rows_by_name[contact_id] = row
            if len(rows_by_name) >= limit:
                break

        return list(rows_by_name.values())[:limit]

    def create_customer(
        self,
        *,
        customer_name: str,
        account_manager: str | None = None,
        default_currency: str | None = "USD",
        customer_details: str | None = None,
        website: str | None = None,
    ) -> dict[str, Any]:
        """Create one ERPNext Customer with dashboard defaults."""
        normalized_name = customer_name.strip()
        if not normalized_name:
            raise ERPNextAPIError("Customer name is required")
        payload: dict[str, Any] = {
            "customer_name": normalized_name,
            "customer_type": "Company",
        }
        normalized_account_manager = (account_manager or "").strip()
        if normalized_account_manager:
            payload["account_manager"] = normalized_account_manager
        normalized_currency = (default_currency or "").strip().upper()
        if normalized_currency:
            payload["default_currency"] = normalized_currency
        normalized_details = (customer_details or "").strip()
        if normalized_details:
            payload["customer_details"] = normalized_details
        normalized_website = (website or "").strip()
        if normalized_website:
            payload["website"] = normalized_website
        return self.create_record("Customer", payload)

    def create_address(
        self,
        *,
        customer: str,
        address_line1: str,
        address_title: str | None = None,
        address_type: str = "Billing",
        address_line2: str | None = None,
        city: str | None = None,
        state: str | None = None,
        country: str | None = None,
        pincode: str | None = None,
        email_id: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Create one Address linked to a Customer."""
        normalized_customer = customer.strip()
        normalized_line1 = address_line1.strip()
        if not normalized_customer:
            raise ERPNextAPIError("Customer is required")
        if not normalized_line1:
            raise ERPNextAPIError("Address line 1 is required")
        normalized_title = (address_title or "").strip() or normalized_customer
        normalized_type = (address_type or "").strip() or "Billing"
        payload: dict[str, Any] = {
            "address_title": normalized_title,
            "address_type": normalized_type,
            "address_line1": normalized_line1,
            "links": [
                {"link_doctype": "Customer", "link_name": normalized_customer},
            ],
        }
        for field, value in {
            "address_line2": address_line2,
            "city": city,
            "state": state,
            "country": country,
            "pincode": pincode,
            "email_id": email_id,
            "phone": phone,
        }.items():
            normalized_value = (value or "").strip()
            if normalized_value:
                payload[field] = normalized_value
        return self.create_record("Address", payload)

    def create_contact(
        self,
        *,
        customer: str,
        first_name: str,
        last_name: str | None = None,
        email_id: str | None = None,
        phone: str | None = None,
        mobile_no: str | None = None,
    ) -> dict[str, Any]:
        """Create one Contact linked to a Customer."""
        normalized_customer = customer.strip()
        normalized_first_name = first_name.strip()
        if not normalized_customer:
            raise ERPNextAPIError("Customer is required")
        if not normalized_first_name:
            raise ERPNextAPIError("Contact first name is required")
        payload: dict[str, Any] = {
            "first_name": normalized_first_name,
            "links": [
                {"link_doctype": "Customer", "link_name": normalized_customer},
            ],
        }
        normalized_last_name = (last_name or "").strip()
        if normalized_last_name:
            payload["last_name"] = normalized_last_name
        normalized_email = (email_id or "").strip()
        if normalized_email:
            payload["email_ids"] = [{"email_id": normalized_email, "is_primary": 1}]
        normalized_phone = (phone or "").strip()
        if normalized_phone:
            payload["phone_nos"] = [{"phone": normalized_phone, "is_primary_phone": 1}]
        normalized_mobile = (mobile_no or "").strip()
        if normalized_mobile:
            payload.setdefault("phone_nos", []).append(
                {"phone": normalized_mobile, "is_primary_mobile_no": 1}
            )
        return self.create_record("Contact", payload)

    def link_contact_to_customer(
        self,
        *,
        contact: str,
        customer: str,
    ) -> dict[str, Any]:
        """Ensure an existing Contact has a Customer link."""
        normalized_contact = contact.strip()
        normalized_customer = customer.strip()
        if not normalized_contact:
            raise ERPNextAPIError("Contact is required")
        if not normalized_customer:
            raise ERPNextAPIError("Customer is required")
        contact_doc = self.get_record("Contact", normalized_contact)
        existing_links = [
            link for link in contact_doc.get("links") or [] if isinstance(link, dict)
        ]
        if any(
            link.get("link_doctype") == "Customer"
            and link.get("link_name") == normalized_customer
            for link in existing_links
        ):
            return contact_doc
        return self.update_record(
            "Contact",
            normalized_contact,
            {
                "links": [
                    *existing_links,
                    {"link_doctype": "Customer", "link_name": normalized_customer},
                ]
            },
        )

    def set_customer_primary_records(
        self,
        customer: str,
        *,
        address: str | None = None,
        contact: str | None = None,
        customer_details: str | None = None,
        website: str | None = None,
    ) -> dict[str, Any]:
        """Set primary linked records and optional detail fields on a Customer."""
        fields: dict[str, Any] = {}
        normalized_address = (address or "").strip()
        if normalized_address:
            fields["customer_primary_address"] = normalized_address
        normalized_contact = (contact or "").strip()
        if normalized_contact:
            fields["customer_primary_contact"] = normalized_contact
        normalized_details = (customer_details or "").strip()
        if normalized_details:
            fields["customer_details"] = normalized_details
        normalized_website = (website or "").strip()
        if normalized_website:
            fields["website"] = normalized_website
        return self.update_record("Customer", customer, fields)

    def create_project(
        self,
        *,
        project_name: str,
        customer: str,
        project_type: str = "External",
        default_cost_center: str = "Projects - 5",
    ) -> dict[str, Any]:
        """Create one ERPNext Project attached to a Customer."""
        normalized_project_name = project_name.strip()
        normalized_customer = customer.strip()
        normalized_project_type = project_type.strip()
        normalized_cost_center = default_cost_center.strip()
        if not normalized_project_name:
            raise ERPNextAPIError("Project name is required")
        if not normalized_customer:
            raise ERPNextAPIError("Customer is required")
        payload: dict[str, Any] = {
            "project_name": normalized_project_name,
            "customer": normalized_customer,
            "project_type": normalized_project_type or "External",
            "status": "Open",
        }
        if normalized_cost_center:
            payload["cost_center"] = normalized_cost_center
        row = self.create_record("Project", payload)
        project_id = str(row.get("name") or "").strip()
        if project_id:
            try:
                return self.get_project(project_id)
            except ERPNextAPIError:
                return row
        return row

    def ensure_activity_type(self, activity_type: str) -> dict[str, Any]:
        """Return an existing Activity Type or create it when missing."""
        normalized_activity_type = activity_type.strip()
        if not normalized_activity_type:
            raise ERPNextAPIError("Activity Type is required")
        existing = self.list_records(
            "Activity Type",
            fields=["name", "activity_type"],
            filters=[["Activity Type", "name", "=", normalized_activity_type]],
            limit=1,
        )
        if existing:
            return existing[0]
        return self.create_record(
            "Activity Type",
            {"activity_type": normalized_activity_type},
        )

    def search_users(
        self,
        query: str,
        *,
        limit: int = 10,
        enabled_only: bool = False,
        email_domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search ERPNext User records by email, id, or full name."""
        normalized_query = query.strip()
        if not normalized_query:
            return []
        filters: list[Any] = []
        if enabled_only:
            filters.append(["User", "enabled", "=", 1])
        normalized_domain = (email_domain or "").strip().casefold()
        if normalized_domain:
            domain_suffix = (
                normalized_domain
                if normalized_domain.startswith("@")
                else f"@{normalized_domain}"
            )
            filters.append(
                [
                    "User",
                    "email",
                    "like",
                    f"%{domain_suffix}",
                ]
            )
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
            filters=filters,
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
            raise ERPNextAPIError(
                f"Project user not found: {normalized_user} for project {project_id}"
            )
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
