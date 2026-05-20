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
