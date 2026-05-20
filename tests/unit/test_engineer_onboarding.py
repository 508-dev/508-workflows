from __future__ import annotations

import math
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
    ensure_engineer_role,
    ensure_supplier,
    ensure_user,
    setup_engineer,
)


class FakeERPNextClient:
    def __init__(self) -> None:
        self.status_code: int | None = None
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
            record = dict(self.records[doctype][record_id])
            self.status_code = 200
            return record
        except KeyError as exc:
            self.status_code = 404
            raise ERPNextAPIError(
                f"{doctype} {record_id} not found",
                status_code=404,
            ) from exc

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
    assert client.records["Employee"]["HR-EMP-00001"]["company_email"] == "jane@508.dev"
    assert client.records["Employee"]["HR-EMP-00001"]["gender"] == "Male"
    assert client.records["Employee"]["HR-EMP-00001"]["date_of_birth"] == "1980-01-01"
    assert (
        client.records["Employee"]["HR-EMP-00001"]["prefered_email"] == "Company Email"
    )
    assert client.records["Supplier"]["Jane Engineer"]["email_id"] == "jane@508.dev"


def test_setup_engineer_creates_employee_with_advanced_fields() -> None:
    client = FakeERPNextClient()

    result = setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            middle_name="Q",
            last_name="Engineer",
            country="Taiwan",
            gender="Female",
            date_of_birth="1990-03-04",
            date_of_joining="2024-01-02",
            personal_email="jane@example.com",
            prefered_email="Personal Email",
        ),
    )

    employee = client.records["Employee"][result["employee"]]
    assert employee["first_name"] == "Jane"
    assert employee["middle_name"] == "Q"
    assert employee["last_name"] == "Engineer"
    assert employee["employee_name"] == "Jane Q Engineer"
    assert employee["company_email"] == "jane@508.dev"
    assert employee["personal_email"] == "jane@example.com"
    assert employee["gender"] == "Female"
    assert employee["date_of_birth"] == "1990-03-04"
    assert employee["date_of_joining"] == "2024-01-02"
    assert employee["prefered_email"] == "Personal Email"


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


@pytest.mark.parametrize(
    "email",
    [
        "jane @508.dev",
        "jane@508.dev extra",
        "@508.dev",
        "jane@@508.dev",
        "jane@engineering.508.dev",
    ],
)
def test_setup_engineer_rejects_malformed_508_email(email: str) -> None:
    with pytest.raises(EngineerOnboardingError, match="@508.dev"):
        setup_engineer(
            FakeERPNextClient(),  # type: ignore[arg-type]
            EngineerSetupRequest(
                email=email,
                first_name="Jane",
                country="Taiwan",
            ),
        )


@pytest.mark.parametrize(
    ("request_kwargs", "expected_error"),
    [
        ({"gender": "Man"}, "gender"),
        ({"date_of_birth": "01/01/1980"}, "date_of_birth"),
        ({"date_of_joining": "January 1 2024"}, "date_of_joining"),
        ({"prefered_email": "Work Email"}, "Preferred contact email"),
    ],
)
def test_setup_engineer_rejects_invalid_employee_fields(
    request_kwargs: dict[str, str],
    expected_error: str,
) -> None:
    client = FakeERPNextClient()

    with pytest.raises(EngineerOnboardingError, match=expected_error):
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                country="Taiwan",
                **request_kwargs,
            ),
        )
    assert client.created == []


def test_setup_engineer_reraises_user_read_errors_without_creating() -> None:
    class FailingUserReadClient(FakeERPNextClient):
        def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
            if doctype == "User":
                self.status_code = 500
                raise ERPNextAPIError("permission denied")
            return super().get_record(doctype, record_id)

    client = FailingUserReadClient()

    with pytest.raises(ERPNextAPIError, match="permission denied"):
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                country="Taiwan",
            ),
        )

    assert client.created == []


def test_ensure_user_does_not_retry_create_for_generic_errors() -> None:
    class FailingUserCreateClient(FakeERPNextClient):
        def __init__(self) -> None:
            super().__init__()
            self.user_create_attempts = 0

        def create_record(self, doctype: str, fields: dict[str, Any]) -> dict[str, Any]:
            if doctype == "User":
                self.user_create_attempts += 1
                raise ERPNextAPIError("HTTP request failed: timeout")
            return super().create_record(doctype, fields)

    client = FailingUserCreateClient()

    with pytest.raises(ERPNextAPIError, match="timeout"):
        ensure_user(
            client,  # type: ignore[arg-type]
            email="jane@508.dev",
            full_name="Jane Engineer",
        )

    assert client.user_create_attempts == 1
    assert client.records["User"] == {}


def test_ensure_user_retries_create_without_role_profile_for_role_profile_errors() -> (
    None
):
    class RoleProfileCreateClient(FakeERPNextClient):
        def __init__(self) -> None:
            super().__init__()
            self.user_create_attempts = 0

        def create_record(self, doctype: str, fields: dict[str, Any]) -> dict[str, Any]:
            if doctype == "User":
                self.user_create_attempts += 1
                if "role_profile_name" in fields:
                    raise ERPNextAPIError("Unknown field role_profile_name")
            return super().create_record(doctype, fields)

    client = RoleProfileCreateClient()

    user, created = ensure_user(
        client,  # type: ignore[arg-type]
        email="jane@508.dev",
        full_name="Jane Engineer",
    )

    assert created is True
    assert user["name"] == "jane@508.dev"
    assert user["roles"] == [{"role": "Employee"}]
    assert client.user_create_attempts == 2


def test_ensure_engineer_role_does_not_fallback_for_generic_role_profile_errors() -> (
    None
):
    class FailingRoleProfileUpdateClient(FakeERPNextClient):
        def update_record(
            self,
            doctype: str,
            record_id: str,
            fields: dict[str, Any],
        ) -> dict[str, Any]:
            if doctype == "User" and "role_profile_name" in fields:
                raise ERPNextAPIError("HTTP request failed: timeout")
            return super().update_record(doctype, record_id, fields)

    client = FailingRoleProfileUpdateClient()
    user = {
        "name": "jane@508.dev",
        "email": "jane@508.dev",
        "roles": [],
    }
    client.records["User"]["jane@508.dev"] = dict(user)

    with pytest.raises(ERPNextAPIError, match="timeout"):
        ensure_engineer_role(client, user)  # type: ignore[arg-type]

    assert client.records["User"]["jane@508.dev"]["roles"] == []


def test_ensure_engineer_role_falls_back_for_role_profile_errors() -> None:
    class RoleProfileUpdateClient(FakeERPNextClient):
        def update_record(
            self,
            doctype: str,
            record_id: str,
            fields: dict[str, Any],
        ) -> dict[str, Any]:
            if doctype == "User" and "role_profile_name" in fields:
                raise ERPNextAPIError("Unknown field role_profile_name")
            return super().update_record(doctype, record_id, fields)

    client = RoleProfileUpdateClient()
    user = {
        "name": "jane@508.dev",
        "email": "jane@508.dev",
        "roles": [],
    }
    client.records["User"]["jane@508.dev"] = dict(user)

    updated, result = ensure_engineer_role(client, user)  # type: ignore[arg-type]

    assert result == "employee_role_assigned"
    assert updated["roles"] == [{"role": "Employee"}]


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


def test_setup_engineer_blocks_similar_supplier_for_existing_user_without_employee() -> (
    None
):
    client = FakeERPNextClient()
    client.records["User"]["jane@508.dev"] = {
        "name": "jane@508.dev",
        "email": "jane@508.dev",
        "full_name": "Jane Engineer",
    }
    client.records["Supplier"]["SUP-0001"] = {
        "name": "SUP-0001",
        "supplier_name": "Jane Engineer",
        "email_id": "",
        "portal_users": [],
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

    assert exc.value.matches[0]["doctype"] == "Supplier"
    assert client.created == []
    assert client.records["Supplier"]["SUP-0001"]["portal_users"] == []


def test_setup_engineer_reraises_stale_not_found_status_errors_without_creating() -> (
    None
):
    class StaleStatusClient(FakeERPNextClient):
        def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
            if doctype == "User":
                self.status_code = 404
                raise ERPNextAPIError("HTTP request failed: timeout")
            return super().get_record(doctype, record_id)

    client = StaleStatusClient()

    with pytest.raises(ERPNextAPIError, match="timeout"):
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                country="Taiwan",
            ),
        )

    assert client.created == []


def test_setup_engineer_allows_existing_supplier_with_same_email() -> None:
    client = FakeERPNextClient()
    client.records["Supplier"]["SUP-0001"] = {
        "name": "SUP-0001",
        "supplier_name": "Jane Engineer",
        "email_id": "jane@508.dev",
        "portal_users": [],
    }

    result = setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
        ),
    )

    assert result["supplier"] == "SUP-0001"
    assert client.records["Supplier"]["SUP-0001"]["portal_users"] == [
        {"user": "jane@508.dev"}
    ]


def test_setup_engineer_allows_existing_supplier_with_same_portal_user() -> None:
    client = FakeERPNextClient()
    client.records["Supplier"]["SUP-0001"] = {
        "name": "SUP-0001",
        "supplier_name": "Jane Engineer",
        "email_id": "",
        "portal_users": [{"user": "jane@508.dev"}],
    }

    result = setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
        ),
    )

    assert result["supplier"] == "SUP-0001"
    assert client.records["Supplier"]["SUP-0001"]["portal_users"] == [
        {"user": "jane@508.dev"}
    ]


def test_setup_engineer_reuses_employee_supplier_link_before_country_check() -> None:
    client = FakeERPNextClient()
    client.records["User"]["jane@508.dev"] = {
        "name": "jane@508.dev",
        "email": "jane@508.dev",
        "first_name": "Jane Engineer",
        "enabled": 1,
        "roles": [{"role": "Employee"}],
    }
    client.records["Employee"]["HR-EMP-00001"] = {
        "name": "HR-EMP-00001",
        "employee_name": "Jane Renamed",
        "user_id": "jane@508.dev",
        "supplier": "SUP-0001",
    }
    client.records["Supplier"]["SUP-0001"] = {
        "name": "SUP-0001",
        "supplier_name": "Jane Original",
        "email_id": "",
        "portal_users": [],
    }

    result = setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
        ),
    )

    assert result["supplier"] == "SUP-0001"
    assert result["created"]["supplier"] is False
    assert client.records["Supplier"]["SUP-0001"]["portal_users"] == [
        {"user": "jane@508.dev"}
    ]
    assert "Jane Renamed" not in client.records["Supplier"]


def test_setup_engineer_requires_country_before_user_or_employee_writes() -> None:
    client = FakeERPNextClient()

    with pytest.raises(EngineerOnboardingError, match="Country is required"):
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                last_name="Engineer",
            ),
        )

    assert client.records["User"] == {}
    assert client.records["Employee"] == {}
    assert client.records["Supplier"] == {}


def test_setup_engineer_requires_employee_defaults_before_user_write() -> None:
    class MissingCompanyClient(FakeERPNextClient):
        def call_method(
            self,
            method: str,
            *,
            params: dict[str, Any] | None = None,
            payload: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            del method, params, payload
            return {"message": None}

    client = MissingCompanyClient()
    client.records["Company"] = {}

    with pytest.raises(EngineerOnboardingError, match="default company"):
        setup_engineer(
            client,  # type: ignore[arg-type]
            EngineerSetupRequest(
                email="jane@508.dev",
                first_name="Jane",
                last_name="Engineer",
                country="Taiwan",
            ),
        )

    assert client.records["User"] == {}
    assert client.records["Employee"] == {}
    assert client.records["Supplier"] == {}


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


def test_ensure_supplier_reraises_non_404_supplier_read_errors() -> None:
    class FailingSupplierReadClient(FakeERPNextClient):
        def get_record(self, doctype: str, record_id: str) -> dict[str, Any]:
            if doctype == "Supplier":
                self.status_code = 502
                raise ERPNextAPIError("bad gateway")
            return super().get_record(doctype, record_id)

    client = FailingSupplierReadClient()

    with pytest.raises(ERPNextAPIError, match="bad gateway"):
        ensure_supplier(
            client,  # type: ignore[arg-type]
            supplier_name="Jane Engineer",
            user="jane@508.dev",
            country="Taiwan",
        )

    assert client.records["Supplier"] == {}


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


def test_configure_engineer_activity_cost_rejects_negative_rates() -> None:
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

    with pytest.raises(EngineerOnboardingError, match="non-negative"):
        configure_engineer_activity_cost(
            client,  # type: ignore[arg-type]
            ActivityCostRequest(
                user="jane@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=-1,
                costing_rate=100,
            ),
        )


@pytest.mark.parametrize("rate", [math.nan, math.inf, -math.inf])
def test_configure_engineer_activity_cost_rejects_non_finite_rates(
    rate: float,
) -> None:
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

    with pytest.raises(EngineerOnboardingError, match="finite"):
        configure_engineer_activity_cost(
            client,  # type: ignore[arg-type]
            ActivityCostRequest(
                user="jane@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=rate,
                costing_rate=100,
            ),
        )


def test_add_engineer_to_project_requires_activity_cost_user_to_match() -> None:
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

    with pytest.raises(EngineerOnboardingError, match="activity_cost.user"):
        add_engineer_to_project(
            client,  # type: ignore[arg-type]
            project_id="PROJ-001",
            user="jane@508.dev",
            activity_cost=ActivityCostRequest(
                user="other@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=150,
                costing_rate=100,
            ),
        )

    assert client.records["Project"]["PROJ-001"]["users"] == []
    assert client.records["Activity Cost"] == {}


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


def test_add_engineer_to_project_adds_roster_before_activity_cost_write() -> None:
    class FailingProjectClient(FakeERPNextClient):
        def add_project_user(self, project_id: str, user: str) -> dict[str, Any]:
            raise ERPNextAPIError("project not found")

    client = FailingProjectClient()
    setup_engineer(
        client,  # type: ignore[arg-type]
        EngineerSetupRequest(
            email="jane@508.dev",
            first_name="Jane",
            last_name="Engineer",
            country="Taiwan",
        ),
    )

    with pytest.raises(ERPNextAPIError, match="project not found"):
        add_engineer_to_project(
            client,  # type: ignore[arg-type]
            project_id="PROJ-MISSING",
            user="jane@508.dev",
            activity_cost=ActivityCostRequest(
                user="jane@508.dev",
                activity_type="Engineering for Test Project",
                billing_rate=150,
                costing_rate=100,
            ),
        )

    assert client.records["Activity Cost"] == {}


def test_add_engineer_to_project_reports_partial_success_when_activity_cost_fails() -> (
    None
):
    class FailingActivityCostClient(FakeERPNextClient):
        def create_record(self, doctype: str, fields: dict[str, Any]) -> dict[str, Any]:
            if doctype == "Activity Cost":
                raise ERPNextAPIError("activity cost write denied")
            return super().create_record(doctype, fields)

    client = FailingActivityCostClient()
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

    assert result["partial_success"] is True
    assert result["activity_cost"] is None
    assert result["activity_cost_error"] == "activity cost write denied"
    assert client.records["Project"]["PROJ-001"]["users"] == [
        {"user": "jane@508.dev", "view_attachments": 0, "hide_timesheets": 0}
    ]
