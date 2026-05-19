from __future__ import annotations

from typing import Any

import pytest

from five08.clients.erpnext import ERPNextAPIError
from five08.engineer_onboarding import (
    ActivityCostRequest,
    EngineerOnboardingDuplicateNameError,
    EngineerOnboardingError,
    EngineerSetupRequest,
    add_engineer_to_project,
    configure_engineer_activity_cost,
    ensure_supplier,
    setup_engineer,
)


class FakeERPNextClient:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, dict[str, Any]]] = {
            "Role Profile": {"Engineer": {"name": "Engineer"}},
            "Company": {"508.dev": {"name": "508.dev"}},
            "User": {},
            "Employee": {},
            "Supplier": {},
            "Activity Cost": {},
            "Project": {
                "PROJ-001": {
                    "name": "PROJ-001",
                    "project_name": "Test Project",
                    "users": [],
                }
            },
        }
        self.created: list[tuple[str, dict[str, Any]]] = []

    def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
        try:
            return dict(self.records[doctype][record_id])
        except KeyError as exc:
            raise ERPNextAPIError(f"{doctype} {record_id} not found") from exc

    def create_record(self, doctype: str, fields: dict[str, Any]) -> dict[str, Any]:
        name = str(
            fields.get("name")
            or fields.get("email")
            or fields.get("supplier_name")
            or ""
        )
        if not name and doctype == "Employee":
            name = f"HR-EMP-{len(self.records[doctype]) + 1:05d}"
        if not name and doctype == "Activity Cost":
            name = f"ACT-COST-{len(self.records[doctype]) + 1:05d}"
        record = {"name": name, **fields}
        self.records.setdefault(doctype, {})[name] = record
        self.created.append((doctype, record))
        return dict(record)

    def update_record(
        self,
        doctype: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        record = self.records[doctype][record_id]
        record.update(fields)
        return dict(record)

    def list_records(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[Any] | None = None,
        or_filters: list[Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        del fields
        rows = list(self.records.get(doctype, {}).values())
        matched = [
            row
            for row in rows
            if self._matches(row, filters or [], require_all=True)
            and (not or_filters or self._matches(row, or_filters, require_all=False))
        ]
        return [dict(row) for row in matched[:limit]]

    def add_project_user(self, project_id: str, user: str) -> dict[str, Any]:
        project = self.records["Project"][project_id]
        users = project.setdefault("users", [])
        if not any(row.get("user") == user for row in users):
            users.append({"user": user, "view_attachments": 0, "hide_timesheets": 0})
        return dict(project)

    def call_method(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del params, payload
        if method == "frappe.client.get_value":
            return {"message": {"default_company": "508.dev"}}
        return {"message": None}

    @staticmethod
    def _matches(
        row: dict[str, Any],
        filters: list[Any],
        *,
        require_all: bool,
    ) -> bool:
        if not filters:
            return True
        results = []
        for item in filters:
            if len(item) != 4:
                continue
            _doctype, field, op, expected = item
            actual = str(row.get(field) or "")
            if op == "=":
                results.append(actual == str(expected))
            elif op == "like":
                token = str(expected).replace("%", "").casefold()
                results.append(token in actual.casefold())
        return all(results) if require_all else any(results)


def test_setup_engineer_creates_user_employee_supplier_and_links_supplier() -> None:
    client = FakeERPNextClient()

    result = setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
            country="Taiwan",
        ),
    )

    assert result["user"] == "jane@508.dev"
    assert result["employee"] == "HR-EMP-00001"
    assert result["supplier"] == "Jane Engineer"
    assert client.records["Employee"]["HR-EMP-00001"]["supplier"] == "Jane Engineer"
    assert client.records["User"]["jane@508.dev"]["role_profile_name"] == "Engineer"
    assert client.records["User"]["jane@508.dev"]["first_name"] == "Jane Engineer"
    assert client.records["Employee"]["HR-EMP-00001"]["create_user_permission"] == 1


def test_setup_engineer_requires_508_email() -> None:
    with pytest.raises(EngineerOnboardingError, match="@508.dev"):
        setup_engineer(
            FakeERPNextClient(),  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@example.com",
                first_name="Jane",
                country="Taiwan",
            ),
        )


def test_setup_engineer_blocks_similar_name_for_new_email() -> None:
    client = FakeERPNextClient()
    client.records["User"]["jane.old@508.dev"] = {
        "name": "jane.old@508.dev",
        "email": "jane.old@508.dev",
        "full_name": "Jane Engineer",
    }

    with pytest.raises(EngineerOnboardingDuplicateNameError) as exc:
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                last_name="Engineer",
                country="Taiwan",
            ),
        )

    assert exc.value.matches[0]["doctype"] == "User"
    assert exc.value.matches[0]["email"] == "jane.old@508.dev"


def test_ensure_supplier_reuses_supplier_matched_by_supplier_name() -> None:
    client = FakeERPNextClient()
    client.records["Supplier"]["SUP-0001"] = {
        "name": "SUP-0001",
        "supplier_name": "Jane Engineer",
        "portal_users": [],
    }

    supplier, created, portal_user_added = ensure_supplier(
        client,  # type: ignore[arg-type]
        supplier_name="Jane Engineer",
        user="jane@508.dev",
        country="Taiwan",
    )

    assert supplier["name"] == "SUP-0001"
    assert created is False
    assert portal_user_added is True
    assert "Jane Engineer" not in client.records["Supplier"]
    assert client.records["Supplier"]["SUP-0001"]["portal_users"] == [
        {"user": "jane@508.dev"}
    ]


def test_add_engineer_to_project_optionally_configures_activity_cost() -> None:
    client = FakeERPNextClient()
    setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
            country="Taiwan",
        ),
    )

    result = add_engineer_to_project(
        client,  # type: ignore[arg-type]
        project_id="PROJ-001",
        user="jane@508.dev",
        activity_cost=ActivityCostRequest(
            user="jane@508.dev",
            activity_type="Engineering for Test Project",
            billing_rate=150,
            costing_rate=100,
        ),
    )

    assert result["project"]["users"] == [
        {"user": "jane@508.dev", "view_attachments": 0, "hide_timesheets": 0}
    ]
    assert result["activity_cost"]["activity_cost"] == "ACT-COST-00001"
    assert client.records["Activity Cost"]["ACT-COST-00001"]["billing_rate"] == 150
    assert client.records["Activity Cost"]["ACT-COST-00001"]["costing_rate"] == 100


def test_configure_engineer_activity_cost_requires_both_rates() -> None:
    client = FakeERPNextClient()
    setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
            country="Taiwan",
        ),
    )

    with pytest.raises(EngineerOnboardingError, match="Billing rate and costing rate"):
        configure_engineer_activity_cost(
            client,  # type: ignore[arg-type]
            ActivityCostRequest(
                user="jane@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=150,
            ),
        )


def test_add_engineer_to_project_validates_activity_cost_before_roster_update() -> None:
    client = FakeERPNextClient()

    with pytest.raises(EngineerOnboardingError, match="Employee linked"):
        add_engineer_to_project(
            client,  # type: ignore[arg-type]
            project_id="PROJ-001",
            user="missing@508.dev",
            activity_cost=ActivityCostRequest(
                user="missing@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=150,
                costing_rate=100,
            ),
        )

    assert client.records["Project"]["PROJ-001"]["users"] == []
