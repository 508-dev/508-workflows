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
