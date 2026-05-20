from __future__ import annotations

import json
from typing import Any

import pytest

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient


class FakeERPNextClient(ERPNextClient):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__("https://erp.example.test", "key:secret")
        self.response = response

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.response


class CaptureERPNextClient(FakeERPNextClient):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(response)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "payload": payload,
            }
        )
        return self.response


def test_list_projects_rejects_missing_data_rows() -> None:
    client = FakeERPNextClient({})

    with pytest.raises(ERPNextAPIError, match="missing data rows"):
        client.list_projects()


def test_list_projects_rejects_non_list_data_rows() -> None:
    client = FakeERPNextClient({"data": {"name": "PROJ-001"}})

    with pytest.raises(ERPNextAPIError, match="missing data rows"):
        client.list_projects()


def test_search_suppliers_filters_disabled_and_frozen_records() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["params"] = params
            return {"data": []}

    client = CaptureClient({"data": []})

    assert client.search_suppliers("fabien") == []

    params = captured["params"]
    filters = json.loads(params["filters"])
    assert ["Supplier", "disabled", "=", 0] in filters
    assert ["Supplier", "is_frozen", "=", 0] in filters


def test_get_record_validates_inputs_and_response_shape() -> None:
    client = FakeERPNextClient({"data": None})

    with pytest.raises(ERPNextAPIError, match="DocType is required"):
        client.get_record(" ", "USER-001")
    with pytest.raises(ERPNextAPIError, match="User id is required"):
        client.get_record("User", " ")
    with pytest.raises(ERPNextAPIError, match="detail response is not an object"):
        client.get_record("User", "USER-001")


def test_create_record_validates_inputs_and_response_shape() -> None:
    client = CaptureERPNextClient({"data": []})

    with pytest.raises(ERPNextAPIError, match="DocType is required"):
        client.create_record(" ", {"email": "member@508.dev"})
    with pytest.raises(ERPNextAPIError, match="User payload is required"):
        client.create_record("User", {})
    with pytest.raises(ERPNextAPIError, match="User payload is required"):
        client.create_record("User", {"doctype": "Supplier"})
    with pytest.raises(ERPNextAPIError, match="create response is not an object"):
        client.create_record("User", {"email": "member@508.dev"})

    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["path"] == "/api/resource/User"
    assert client.calls[0]["payload"] == {"email": "member@508.dev"}


def test_create_record_ignores_payload_doctype() -> None:
    client = CaptureERPNextClient({"data": {"name": "USER-001"}})

    assert client.create_record(
        "User",
        {"doctype": "Supplier", "email": "member@508.dev"},
    ) == {"name": "USER-001"}

    assert client.calls[0]["path"] == "/api/resource/User"
    assert client.calls[0]["payload"] == {"email": "member@508.dev"}


def test_update_record_validates_inputs_and_falls_back_to_detail() -> None:
    client = CaptureERPNextClient({"data": {"name": "USER-001"}})

    with pytest.raises(ERPNextAPIError, match="DocType is required"):
        client.update_record(" ", "USER-001", {"enabled": 1})
    with pytest.raises(ERPNextAPIError, match="User id is required"):
        client.update_record("User", " ", {"enabled": 1})

    assert client.update_record("User", "USER-001", {}) == {"name": "USER-001"}
    assert client.calls[-1]["method"] == "GET"
    assert client.calls[-1]["path"] == "/api/resource/User/USER-001"


def test_update_record_fetches_detail_when_update_response_has_invalid_data() -> None:
    class UpdateThenGetClient(CaptureERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self.calls.append(
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "payload": payload,
                }
            )
            if method == "PUT":
                return {"data": None}
            return {"data": {"name": "USER-001", "enabled": 1}}

    client = UpdateThenGetClient({})

    assert client.update_record("User", "USER-001", {"enabled": 1}) == {
        "name": "USER-001",
        "enabled": 1,
    }
    assert [call["method"] for call in client.calls] == ["PUT", "GET"]


def test_call_method_validates_method_name_and_selects_http_method() -> None:
    client = CaptureERPNextClient({"message": {"value": "ok"}})

    with pytest.raises(ERPNextAPIError, match="Frappe method is required"):
        client.call_method(" ")

    assert client.call_method("frappe.client.get_value", params={"doctype": "User"})
    assert client.call_method("frappe.client.set_value", payload={"value": "ok"})
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == "/api/method/frappe.client.get_value"
    assert client.calls[1]["method"] == "POST"
    assert client.calls[1]["path"] == "/api/method/frappe.client.set_value"


def test_create_customer_posts_dashboard_defaults() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"data": {"name": "Acme", "customer_name": "Acme"}}

    client = CaptureClient({"data": {}})

    result = client.create_customer(
        customer_name=" Acme ",
        account_manager="owner@example.test",
        default_currency="usd",
        customer_details=" Important customer ",
        website=" https://acme.example ",
    )

    assert result["name"] == "Acme"
    assert captured == {
        "method": "POST",
        "path": "/api/resource/Customer",
        "payload": {
            "customer_name": "Acme",
            "customer_type": "Company",
            "account_manager": "owner@example.test",
            "default_currency": "USD",
            "customer_details": "Important customer",
            "website": "https://acme.example",
        },
    }


def test_create_address_links_customer() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"data": {"name": "ADDR-0001"}}

    client = CaptureClient({"data": {}})

    result = client.create_address(
        customer="Acme",
        address_line1="123 Main St",
        city="Missoula",
        country="United States",
    )

    assert result["name"] == "ADDR-0001"
    assert captured == {
        "method": "POST",
        "path": "/api/resource/Address",
        "payload": {
            "address_title": "Acme",
            "address_type": "Billing",
            "address_line1": "123 Main St",
            "city": "Missoula",
            "country": "United States",
            "links": [{"link_doctype": "Customer", "link_name": "Acme"}],
        },
    }


def test_create_address_falls_back_for_whitespace_title_and_type() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["payload"] = payload
            return {"data": {"name": "ADDR-0001"}}

    client = CaptureClient({"data": {}})

    client.create_address(
        customer="Acme",
        address_title="   ",
        address_type="   ",
        address_line1="123 Main St",
    )

    assert captured["payload"]["address_title"] == "Acme"
    assert captured["payload"]["address_type"] == "Billing"


def test_delete_record_deletes_one_document() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params
            captured["payload"] = payload
            return {}

    client = CaptureClient({"data": []})

    client.delete_record("Customer", "Acme LLC")

    assert captured == {
        "method": "DELETE",
        "path": "/api/resource/Customer/Acme%20LLC",
        "params": None,
        "payload": None,
    }


def test_search_contacts_matches_dashboard_visible_fields() -> None:
    calls: list[dict[str, Any]] = []

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append({"method": method, "path": path, "params": params})
            if len(calls) == 1:
                return {"data": [{"name": "CONTACT-0001", "full_name": "Acme Contact"}]}
            return {"data": []}

    client = CaptureClient({"data": []})

    result = client.search_contacts("co")

    assert result == [{"name": "CONTACT-0001", "full_name": "Acme Contact"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/api/resource/Contact"
    params = calls[0]["params"]
    assert json.loads(params["or_filters"]) == [
        ["Contact", "full_name", "like", "%co%"],
        ["Contact", "mobile_no", "like", "%co%"],
        ["Contact", "phone", "like", "%co%"],
        ["Contact", "company_name", "like", "%co%"],
    ]
    assert json.loads(calls[1]["params"]["or_filters"]) == [
        ["Contact", "email_id", "like", "%co%"]
    ]


def test_search_contacts_ignores_email_tld_only_matches() -> None:
    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            filters = json.loads((params or {})["or_filters"])
            if filters == [["Contact", "email_id", "like", "%co%"]]:
                return {
                    "data": [
                        {"name": "CONTACT-0001", "email_id": "ada@example.com"},
                        {"name": "CONTACT-0002", "email_id": "cody@example.net"},
                        {"name": "CONTACT-0003", "email_id": "lead@company.org"},
                    ]
                }
            return {"data": []}

    client = CaptureClient({"data": []})

    result = client.search_contacts("co")

    assert result == [
        {"name": "CONTACT-0002", "email_id": "cody@example.net"},
        {"name": "CONTACT-0003", "email_id": "lead@company.org"},
    ]


def test_create_contact_links_customer() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return {"data": {"name": "CONT-0001"}}

    client = CaptureClient({"data": {}})

    result = client.create_contact(
        customer="Acme",
        first_name="Ada",
        last_name="Lovelace",
        email_id="ada@example.test",
        phone="555-0100",
    )

    assert result["name"] == "CONT-0001"
    assert captured == {
        "method": "POST",
        "path": "/api/resource/Contact",
        "payload": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email_ids": [{"email_id": "ada@example.test", "is_primary": 1}],
            "phone_nos": [{"phone": "555-0100", "is_primary_phone": 1}],
            "links": [{"link_doctype": "Customer", "link_name": "Acme"}],
        },
    }


def test_link_contact_to_customer_preserves_existing_links() -> None:
    calls: list[dict[str, Any]] = []

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append({"method": method, "path": path, "payload": payload})
            if method == "GET":
                return {
                    "data": {
                        "name": "CONT-0001",
                        "links": [{"link_doctype": "Supplier", "link_name": "Ada LLC"}],
                    }
                }
            return {"data": {"name": "CONT-0001"}}

    client = CaptureClient({"data": {}})

    result = client.link_contact_to_customer(contact="CONT-0001", customer="Acme")

    assert result["name"] == "CONT-0001"
    assert calls == [
        {
            "method": "GET",
            "path": "/api/resource/Contact/CONT-0001",
            "payload": None,
        },
        {
            "method": "PUT",
            "path": "/api/resource/Contact/CONT-0001",
            "payload": {
                "links": [
                    {"link_doctype": "Supplier", "link_name": "Ada LLC"},
                    {"link_doctype": "Customer", "link_name": "Acme"},
                ]
            },
        },
    ]


def test_create_project_posts_external_project_then_fetches_detail() -> None:
    calls: list[dict[str, Any]] = []

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append({"method": method, "path": path, "payload": payload})
            if method == "POST":
                return {"data": {"name": "PROJ-0001"}}
            return {
                "data": {
                    "name": "PROJ-0001",
                    "project_name": "Acme Portal",
                    "customer": "Acme",
                }
            }

    client = CaptureClient({"data": {}})

    result = client.create_project(project_name="Acme Portal", customer="Acme")

    assert result["name"] == "PROJ-0001"
    assert calls[0] == {
        "method": "POST",
        "path": "/api/resource/Project",
        "payload": {
            "project_name": "Acme Portal",
            "customer": "Acme",
            "project_type": "External",
            "status": "Open",
            "cost_center": "Projects - 5",
        },
    }
    assert calls[1]["method"] == "GET"
    assert calls[1]["path"] == "/api/resource/Project/PROJ-0001"


def test_create_project_returns_posted_record_when_detail_fetch_fails() -> None:
    calls: list[dict[str, Any]] = []

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            calls.append({"method": method, "path": path, "payload": payload})
            if method == "POST":
                return {"data": {"name": "PROJ-0001", "project_name": "Acme Portal"}}
            raise ERPNextAPIError("detail fetch failed")

    client = CaptureClient({"data": {}})

    result = client.create_project(project_name="Acme Portal", customer="Acme")

    assert result == {"name": "PROJ-0001", "project_name": "Acme Portal"}
    assert calls[0]["method"] == "POST"
    assert calls[1]["method"] == "GET"
    assert calls[1]["path"] == "/api/resource/Project/PROJ-0001"


def test_search_users_can_filter_enabled_domain_before_limit() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params
            return {"data": [{"name": "owner@508.dev", "email": "owner@508.dev"}]}

    client = CaptureClient({"data": []})

    result = client.search_users(
        "owner",
        limit=10,
        enabled_only=True,
        email_domain="@508.dev",
    )

    assert result == [{"name": "owner@508.dev", "email": "owner@508.dev"}]
    assert captured["method"] == "GET"
    assert captured["path"] == "/api/resource/User"
    params = captured["params"]
    assert params["limit_page_length"] == "10"
    assert json.loads(params["filters"]) == [
        ["User", "enabled", "=", 1],
        ["User", "email", "like", "%@508.dev"],
    ]
    assert json.loads(params["or_filters"]) == [
        ["User", "name", "like", "%owner%"],
        ["User", "email", "like", "%owner%"],
        ["User", "full_name", "like", "%owner%"],
    ]


def test_ensure_activity_type_reuses_existing_record() -> None:
    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            assert method == "GET"
            filters = json.loads((params or {})["filters"])
            assert filters == [
                ["Activity Type", "name", "=", "Engineering for Acme Portal"]
            ]
            return {"data": [{"name": "Engineering for Acme Portal"}]}

    client = CaptureClient({"data": []})

    assert client.ensure_activity_type("Engineering for Acme Portal") == {
        "name": "Engineering for Acme Portal"
    }


def test_remove_project_user_updates_project_users() -> None:
    class CaptureClient(FakeERPNextClient):
        def __init__(self) -> None:
            super().__init__({})
            self.updated_fields: dict[str, Any] | None = None

        def get_project(self, project_id: str) -> dict[str, Any]:
            assert project_id == "PROJ-001"
            return {
                "name": "PROJ-001",
                "users": [
                    {"name": "row-1", "user": "keep@508.dev", "view_attachments": 1},
                    {"name": "row-2", "user": "remove@508.dev", "hide_timesheets": 1},
                ],
            }

        def update_project(
            self, project_id: str, fields: dict[str, Any]
        ) -> dict[str, Any]:
            assert project_id == "PROJ-001"
            self.updated_fields = fields
            return {"name": "PROJ-001", **fields}

    client = CaptureClient()

    result = client.remove_project_user("PROJ-001", "remove@508.dev")

    assert result["users"] == [
        {
            "name": "row-1",
            "user": "keep@508.dev",
            "view_attachments": 1,
            "hide_timesheets": 0,
        }
    ]
    assert client.updated_fields == {"users": result["users"]}


def test_remove_project_user_does_not_match_child_row_name() -> None:
    class CaptureClient(FakeERPNextClient):
        def __init__(self) -> None:
            super().__init__({})
            self.updated = False

        def get_project(self, project_id: str) -> dict[str, Any]:
            assert project_id == "PROJ-001"
            return {
                "name": "PROJ-001",
                "users": [
                    {
                        "name": "row-2",
                        "user": "remove@508.dev",
                        "email": "remove@508.dev",
                    }
                ],
            }

        def update_project(
            self, project_id: str, fields: dict[str, Any]
        ) -> dict[str, Any]:
            self.updated = True
            return {"name": project_id, **fields}

    client = CaptureClient()

    with pytest.raises(
        ERPNextAPIError,
        match="Project user not found: row-2 for project PROJ-001",
    ):
        client.remove_project_user("PROJ-001", "row-2")

    assert client.updated is False


# ---------------------------------------------------------------------------
# ERPNextClient invoice method tests
# ---------------------------------------------------------------------------


VALID_SALES_INVOICE: dict[str, Any] = {
    "name": "TEST-SINV-0001",
    "docstatus": 0,
    "project": "TEST-PROJ-001",
    "cost_center": "Projects - TEST",
    "posting_date": "2026-01-01",
    "due_date": "2026-02-01",
    "items": [
        {
            "idx": 1,
            "project": "TEST-PROJ-001",
            "cost_center": "Projects - TEST",
        }
    ],
}


def test_get_invoice_returns_invoice_dict() -> None:
    client = FakeERPNextClient({"data": VALID_SALES_INVOICE})
    result = client.get_invoice("Sales Invoice", "TEST-SINV-0001")
    assert result is not None
    assert result["name"] == "TEST-SINV-0001"


def test_get_invoice_raises_on_unexpected_payload() -> None:
    client = FakeERPNextClient({"data": []})
    with pytest.raises(ERPNextAPIError, match="unexpected payload"):
        client.get_invoice("Sales Invoice", "TEST-SINV-0001")


def test_search_invoices_returns_list() -> None:
    client = FakeERPNextClient({"data": [{"name": "TEST-SINV-0001"}]})
    assert client.search_invoices("Sales Invoice") == [{"name": "TEST-SINV-0001"}]


def test_search_invoices_returns_empty_list_when_data_missing() -> None:
    client = FakeERPNextClient({})
    assert client.search_invoices("Sales Invoice") == []


def test_search_invoices_includes_query_filter_when_given() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["params"] = params
            return {"data": []}

    CaptureClient({}).search_invoices("Sales Invoice", query="TEST-SINV")

    filters = json.loads(captured["params"]["filters"])
    assert ["Sales Invoice", "name", "like", "%TEST-SINV%"] in filters


def test_search_invoices_fetches_expected_fields() -> None:
    captured: dict[str, Any] = {}

    class CaptureClient(FakeERPNextClient):
        def request(
            self,
            method: str,
            path: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            captured["params"] = params
            return {"data": []}

    CaptureClient({}).search_invoices("Sales Invoice")

    fields = json.loads(captured["params"]["fields"])
    assert "name" in fields
    assert "docstatus" in fields
    assert "posting_date" in fields
    assert "owner" in fields
