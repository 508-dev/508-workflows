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
    with pytest.raises(ERPNextAPIError, match="detail is not an object"):
        client.get_record("User", "USER-001")


def test_create_record_validates_inputs_and_response_shape() -> None:
    client = CaptureERPNextClient({"data": []})

    with pytest.raises(ERPNextAPIError, match="DocType is required"):
        client.create_record(" ", {})
    with pytest.raises(ERPNextAPIError, match="create response is not an object"):
        client.create_record("User", {"email": "member@508.dev"})

    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["path"] == "/api/resource/User"
    assert client.calls[0]["payload"] == {
        "doctype": "User",
        "email": "member@508.dev",
    }


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
