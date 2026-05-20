"""ERPNext engineer onboarding orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient

ENGINEER_ROLE_PROFILE = "Engineer"
EMPLOYEE_ROLE = "Employee"
DEFAULT_SUPPLIER_GROUP = "Subcontractors"
DEFAULT_SUPPLIER_CURRENCY = "USD"
DEFAULT_EMPLOYEE_DATE_OF_BIRTH = "1980-01-01"
DEFAULT_EMPLOYEE_GENDER = "Male"
DEFAULT_PREFERRED_EMAIL = "Company Email"
EMPLOYEE_GENDER_OPTIONS = frozenset(
    {
        "Female",
        "Genderqueer",
        "Male",
        "Non-Conforming",
        "Other",
        "Prefer not to say",
        "Transgender",
    }
)
PREFERRED_EMAIL_OPTIONS = frozenset({"Company Email", "Personal Email", "User ID"})


class EngineerOnboardingError(ValueError):
    """Raised for expected engineer onboarding validation failures."""


class EngineerOnboardingDuplicateNameError(EngineerOnboardingError):
    """Raised when a new engineer name resembles an existing account."""

    def __init__(self, message: str, *, matches: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.matches = matches


@dataclass(frozen=True)
class EngineerSetupRequest:
    """Inputs for setting up an existing ERPNext User as an engineer."""

    email: str
    first_name: str
    middle_name: str | None = None
    last_name: str | None = None
    country: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    date_of_joining: str | None = None
    personal_email: str | None = None
    prefered_email: str | None = None


@dataclass(frozen=True)
class ActivityCostRequest:
    """Inputs for configuring one engineer Activity Cost row."""

    user: str
    activity_type: str
    billing_rate: float | None = None
    costing_rate: float | None = None


def setup_engineer(
    client: ERPNextClient,
    request: EngineerSetupRequest,
) -> dict[str, Any]:
    """Create or link the ERPNext records needed for one engineer."""
    email = _normalize_508_email(request.email)
    first_name = _required_text(request.first_name, "first_name")
    middle_name = _optional_text(request.middle_name)
    last_name = _optional_text(request.last_name)
    full_name = " ".join(part for part in (first_name, middle_name, last_name) if part)
    if not full_name:
        raise EngineerOnboardingError("Engineer name is required")
    country = _optional_text(request.country)
    employee_gender = _employee_gender_value(request.gender)
    employee_date_of_birth = _date_value(
        request.date_of_birth,
        "date_of_birth",
        default=DEFAULT_EMPLOYEE_DATE_OF_BIRTH,
    )
    employee_date_of_joining = _date_value(
        request.date_of_joining,
        "date_of_joining",
        default=date.today().isoformat(),
    )
    employee_personal_email = _optional_text(request.personal_email)
    employee_prefered_email = _preferred_email_value(request.prefered_email)

    preflight_employee = employee_for_user(client, email)
    if preflight_employee is None:
        ensure_no_similar_engineer_name(client, email=email, full_name=full_name)
    if preflight_employee is None:
        ensure_employee_preconditions(client)
    preflight_supplier_name = (
        _optional_text((preflight_employee or {}).get("employee_name")) or full_name
    )
    preflight_supplier_id = _optional_text((preflight_employee or {}).get("supplier"))
    ensure_supplier_preconditions(
        client,
        supplier_name=preflight_supplier_name,
        supplier_id=preflight_supplier_id,
        country=country,
    )

    user, user_created = ensure_user(client, email=email, full_name=full_name)
    user, role_result = ensure_engineer_role(client, user)
    employee, employee_created = ensure_employee(
        client,
        email=email,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        full_name=full_name,
        gender=employee_gender,
        date_of_birth=employee_date_of_birth,
        date_of_joining=employee_date_of_joining,
        personal_email=employee_personal_email,
        prefered_email=employee_prefered_email,
        create_user_permission=True,
    )
    employee_name = _optional_text(employee.get("employee_name")) or full_name
    employee_supplier = _optional_text(employee.get("supplier"))
    supplier, supplier_created, portal_user_added = ensure_supplier(
        client,
        supplier_name=employee_name,
        user=email,
        country=country,
        supplier_id=employee_supplier,
    )
    employee, supplier_linked = ensure_employee_supplier(
        client,
        employee=employee,
        supplier=str(supplier.get("name") or employee_name),
    )

    return {
        "user": user.get("name") or email,
        "employee": employee.get("name"),
        "employee_name": employee_name,
        "supplier": supplier.get("name"),
        "role": role_result,
        "created": {
            "user": user_created,
            "employee": employee_created,
            "supplier": supplier_created,
        },
        "updated": {
            "supplier_portal_user": portal_user_added,
            "employee_supplier": supplier_linked,
        },
    }


def ensure_user(
    client: ERPNextClient,
    *,
    email: str,
    full_name: str,
) -> tuple[dict[str, Any], bool]:
    """Find or create the ERPNext User for an engineer."""
    normalized_email = _normalize_508_email(email)
    normalized_full_name = _required_text(full_name, "full_name")
    try:
        return client.get_record("User", normalized_email), False
    except ERPNextAPIError as exc:
        if not _is_not_found_error(client, exc):
            raise
        # ERPNext operator convention: put the engineer's full name in User.first_name.
        fields = {
            "email": normalized_email,
            "first_name": normalized_full_name,
            "last_name": "",
            "enabled": 1,
            "send_welcome_email": 1,
            "role_profile_name": ENGINEER_ROLE_PROFILE,
        }
        try:
            return client.create_record("User", fields), True
        except ERPNextAPIError as exc:
            if not _is_role_profile_error(exc):
                raise
            fields.pop("role_profile_name", None)
            fields["roles"] = [{"role": EMPLOYEE_ROLE}]
            return client.create_record("User", fields), True


def ensure_engineer_role(
    client: ERPNextClient,
    user: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Ensure the User has Engineer role access."""
    user_id = _required_text(user.get("name") or user.get("email"), "user")
    if _optional_text(user.get("role_profile_name")) == ENGINEER_ROLE_PROFILE:
        return user, "role_profile_exists"

    try:
        updated = client.update_record(
            "User",
            user_id,
            {"role_profile_name": ENGINEER_ROLE_PROFILE},
        )
        return updated, "role_profile_assigned"
    except ERPNextAPIError as exc:
        if not _is_role_profile_error(exc):
            raise

    roles = _normalized_child_rows(user.get("roles"))
    if any(_optional_text(role.get("role")) == EMPLOYEE_ROLE for role in roles):
        return user, "employee_role_exists"

    roles.append({"role": EMPLOYEE_ROLE})
    updated = client.update_record("User", user_id, {"roles": roles})
    return updated, "employee_role_assigned"


def ensure_employee(
    client: ERPNextClient,
    *,
    email: str,
    first_name: str,
    middle_name: str | None,
    last_name: str | None,
    full_name: str,
    department: str | None = None,
    gender: str | None = None,
    date_of_birth: str | None = None,
    date_of_joining: str | None = None,
    personal_email: str | None = None,
    prefered_email: str | None = None,
    create_user_permission: bool = True,
) -> tuple[dict[str, Any], bool]:
    """Find or create an ERPNext Employee linked to a User."""
    normalized_email = _normalize_508_email(email)
    existing = client.list_records(
        "Employee",
        fields=["name", "employee_name", "user_id"],
        filters=[["Employee", "user_id", "=", normalized_email]],
        limit=1,
    )
    if existing:
        return client.get_record("Employee", str(existing[0]["name"])), False

    company = default_company(client)
    if not company:
        raise EngineerOnboardingError(
            "ERPNext default company is required before creating Employee records."
        )

    fields: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name or "",
        "employee_name": full_name,
        "user_id": normalized_email,
        "company_email": normalized_email,
        "status": "Active",
        "date_of_joining": date_of_joining or date.today().isoformat(),
        "company": company,
        "create_user_permission": 1 if create_user_permission else 0,
    }
    if middle_name:
        fields["middle_name"] = middle_name
    if department:
        fields["department"] = department
    if gender:
        fields["gender"] = gender
    if date_of_birth:
        fields["date_of_birth"] = date_of_birth
    if personal_email:
        fields["personal_email"] = personal_email
    if prefered_email:
        fields["prefered_email"] = prefered_email
    try:
        return client.create_record("Employee", fields), True
    except ERPNextAPIError as exc:
        raise EngineerOnboardingError(
            "ERPNext Employee creation failed. Check required Employee fields "
            f"and defaults: {exc}"
        ) from exc


def ensure_employee_preconditions(client: ERPNextClient) -> None:
    """Validate Employee creation requirements without mutating ERPNext."""
    if default_company(client):
        return
    raise EngineerOnboardingError(
        "ERPNext default company is required before creating Employee records."
    )


def ensure_supplier(
    client: ERPNextClient,
    *,
    supplier_name: str,
    user: str,
    country: str | None,
    supplier_id: str | None = None,
) -> tuple[dict[str, Any], bool, bool]:
    """Find or create the matching Supplier and add the portal user."""
    normalized_supplier_name = _required_text(supplier_name, "supplier_name")
    normalized_user = _normalize_508_email(user)
    normalized_supplier_id = _optional_text(supplier_id)
    supplier = None
    if normalized_supplier_id is not None:
        supplier = supplier_by_id(client, normalized_supplier_id)
    if supplier is None:
        supplier = supplier_by_supplier_name(client, normalized_supplier_name)
    created = False
    if supplier is None:
        normalized_country = _optional_text(country)
        if normalized_country is None:
            raise EngineerOnboardingError(
                "Country is required when creating a new Supplier."
            )
        supplier = client.create_record(
            "Supplier",
            {
                "supplier_name": normalized_supplier_name,
                "supplier_type": "Individual",
                "supplier_group": DEFAULT_SUPPLIER_GROUP,
                "country": normalized_country,
                "default_currency": DEFAULT_SUPPLIER_CURRENCY,
                "email_id": normalized_user,
                "portal_users": [{"user": normalized_user}],
            },
        )
        return supplier, True, True

    portal_users = _normalized_child_rows(supplier.get("portal_users"))
    if any(_optional_text(row.get("user")) == normalized_user for row in portal_users):
        return supplier, created, False

    portal_users.append({"user": normalized_user})
    updated = client.update_record(
        "Supplier",
        str(supplier.get("name") or normalized_supplier_name),
        {"portal_users": portal_users},
    )
    return updated, created, True


def ensure_supplier_preconditions(
    client: ERPNextClient,
    *,
    supplier_name: str,
    supplier_id: str | None = None,
    country: str | None,
) -> None:
    """Validate Supplier requirements without mutating ERPNext records."""
    normalized_supplier_name = _required_text(supplier_name, "supplier_name")
    normalized_supplier_id = _optional_text(supplier_id)
    if (
        normalized_supplier_id is not None
        and supplier_by_id(client, normalized_supplier_id) is not None
    ):
        return
    if supplier_by_supplier_name(client, normalized_supplier_name) is not None:
        return
    if _optional_text(country) is None:
        raise EngineerOnboardingError(
            "Country is required when creating a new Supplier."
        )


def supplier_by_id(client: ERPNextClient, supplier_id: str) -> dict[str, Any] | None:
    """Return Supplier by document id, treating only 404 as absent."""
    normalized_supplier_id = _required_text(supplier_id, "supplier")
    try:
        return client.get_record("Supplier", normalized_supplier_id)
    except ERPNextAPIError as exc:
        if _is_not_found_error(client, exc):
            return None
        raise


def supplier_by_supplier_name(
    client: ERPNextClient,
    supplier_name: str,
) -> dict[str, Any] | None:
    """Return Supplier matching its display name, including naming-series sites."""
    normalized_supplier_name = _required_text(supplier_name, "supplier_name")
    matches = client.list_records(
        "Supplier",
        fields=["name", "supplier_name", "email_id"],
        filters=[["Supplier", "supplier_name", "=", normalized_supplier_name]],
        limit=1,
    )
    if matches:
        return client.get_record("Supplier", str(matches[0]["name"]))
    try:
        return client.get_record("Supplier", normalized_supplier_name)
    except ERPNextAPIError as exc:
        if _is_not_found_error(client, exc):
            return None
        raise


def ensure_employee_supplier(
    client: ERPNextClient,
    *,
    employee: dict[str, Any],
    supplier: str,
) -> tuple[dict[str, Any], bool]:
    """Ensure Employee.supplier points at the matching Supplier."""
    employee_id = _required_text(employee.get("name"), "employee")
    normalized_supplier = _required_text(supplier, "supplier")
    if _optional_text(employee.get("supplier")) == normalized_supplier:
        return employee, False
    updated = client.update_record(
        "Employee",
        employee_id,
        {"supplier": normalized_supplier},
    )
    return updated, True


def configure_engineer_activity_cost(
    client: ERPNextClient,
    request: ActivityCostRequest,
) -> dict[str, Any]:
    """Create or update Activity Cost for an already set-up engineer."""
    _user, activity_type, employee_id = _activity_cost_preconditions(client, request)
    billing_rate = request.billing_rate
    costing_rate = request.costing_rate
    assert billing_rate is not None
    assert costing_rate is not None
    existing = client.list_records(
        "Activity Cost",
        fields=["name", "employee", "activity_type", "billing_rate", "costing_rate"],
        filters=[
            ["Activity Cost", "employee", "=", employee_id],
            ["Activity Cost", "activity_type", "=", activity_type],
        ],
        limit=1,
    )

    fields = _activity_cost_rate_fields(request)
    if existing:
        name = _required_text(existing[0].get("name"), "activity_cost")
        activity_cost = client.update_record("Activity Cost", name, fields)
        created = False
    else:
        create_fields = {
            "employee": employee_id,
            "activity_type": activity_type,
            "billing_rate": float(billing_rate),
            "costing_rate": float(costing_rate),
        }
        activity_cost = client.create_record("Activity Cost", create_fields)
        created = True

    return {
        "activity_cost": activity_cost.get("name"),
        "employee": employee_id,
        "activity_type": activity_type,
        "billing_rate": activity_cost.get("billing_rate", request.billing_rate),
        "costing_rate": activity_cost.get("costing_rate", request.costing_rate),
        "created": created,
    }


def _activity_cost_preconditions(
    client: ERPNextClient,
    request: ActivityCostRequest,
) -> tuple[str, str, str]:
    """Validate Activity Cost inputs without mutating financial records."""
    user = _normalize_508_email(request.user)
    activity_type = _required_text(request.activity_type, "activity_type")
    if request.billing_rate is None or request.costing_rate is None:
        raise EngineerOnboardingError(
            "Billing rate and costing rate are required for Activity Cost."
        )
    if not math.isfinite(request.billing_rate) or not math.isfinite(
        request.costing_rate
    ):
        raise EngineerOnboardingError(
            "Billing rate and costing rate must be finite numbers."
        )
    if request.billing_rate < 0 or request.costing_rate < 0:
        raise EngineerOnboardingError(
            "Billing rate and costing rate must be non-negative."
        )

    employee = employee_for_user(client, user)
    if employee is None:
        raise EngineerOnboardingError(
            f"Employee linked to ERPNext User {user} was not found. Run setup first."
        )
    return user, activity_type, _required_text(employee.get("name"), "employee")


def add_engineer_to_project(
    client: ERPNextClient,
    *,
    project_id: str,
    user: str,
    activity_cost: ActivityCostRequest | None = None,
) -> dict[str, Any]:
    """Add an ERPNext User to Project.users and optionally configure rates."""
    normalized_project_id = _required_text(project_id, "project_id")
    normalized_user = _normalize_508_email(user)
    activity_cost_result = None
    if activity_cost is not None:
        activity_cost_user = _normalize_508_email(activity_cost.user)
        if activity_cost_user != normalized_user:
            raise EngineerOnboardingError(
                "activity_cost.user must match user for project assignment."
            )
        _activity_cost_preconditions(client, activity_cost)
    project = client.add_project_user(normalized_project_id, normalized_user)
    if activity_cost is not None:
        try:
            activity_cost_result = configure_engineer_activity_cost(
                client, activity_cost
            )
        except (EngineerOnboardingError, ERPNextAPIError) as exc:
            return {
                "project": project,
                "activity_cost": None,
                "activity_cost_error": str(exc),
                "partial_success": True,
            }
    return {"project": project, "activity_cost": activity_cost_result}


def employee_for_user(client: ERPNextClient, user: str) -> dict[str, Any] | None:
    """Return the Employee document linked to a User, if present."""
    normalized_user = _normalize_508_email(user)
    employees = client.list_records(
        "Employee",
        fields=["name", "employee_name", "user_id"],
        filters=[["Employee", "user_id", "=", normalized_user]],
        limit=1,
    )
    if not employees:
        return None
    return client.get_record("Employee", str(employees[0]["name"]))


def ensure_no_similar_engineer_name(
    client: ERPNextClient,
    *,
    email: str,
    full_name: str,
) -> None:
    """Reject new setup when the name resembles another ERP account."""
    normalized_email = _normalize_508_email(email)
    normalized_full_name = _required_text(full_name, "full_name")
    like_name = f"%{normalized_full_name}%"
    matches: list[dict[str, Any]] = []

    for user in client.list_records(
        "User",
        fields=["name", "email", "full_name", "enabled"],
        or_filters=[
            ["User", "full_name", "like", like_name],
            ["User", "first_name", "like", like_name],
        ],
        limit=5,
    ):
        user_email = _optional_text(user.get("email") or user.get("name"))
        if user_email and user_email.casefold() == normalized_email.casefold():
            continue
        matches.append(
            {
                "doctype": "User",
                "name": user.get("name"),
                "email": user_email,
                "label": user.get("full_name") or user.get("name"),
            }
        )

    for employee in client.list_records(
        "Employee",
        fields=["name", "employee_name", "user_id"],
        filters=[["Employee", "employee_name", "like", like_name]],
        limit=5,
    ):
        user_email = _optional_text(employee.get("user_id"))
        if user_email and user_email.casefold() == normalized_email.casefold():
            continue
        matches.append(
            {
                "doctype": "Employee",
                "name": employee.get("name"),
                "email": user_email,
                "label": employee.get("employee_name") or employee.get("name"),
            }
        )

    for supplier in client.list_records(
        "Supplier",
        fields=["name", "supplier_name", "email_id"],
        or_filters=[
            ["Supplier", "name", "like", like_name],
            ["Supplier", "supplier_name", "like", like_name],
        ],
        limit=5,
    ):
        supplier_detail = supplier
        supplier_id = _optional_text(supplier.get("name"))
        if supplier_id is not None:
            resolved_supplier = supplier_by_id(client, supplier_id)
            if resolved_supplier is not None:
                supplier_detail = resolved_supplier
        supplier_email = _optional_text(supplier_detail.get("email_id"))
        if _supplier_owned_by_email(supplier_detail, normalized_email):
            continue
        matches.append(
            {
                "doctype": "Supplier",
                "name": supplier_detail.get("name"),
                "email": supplier_email,
                "label": supplier_detail.get("supplier_name")
                or supplier_detail.get("name"),
            }
        )

    if matches:
        raise EngineerOnboardingDuplicateNameError(
            "A similar ERPNext account already exists. Confirm the intended "
            "person before creating a new engineer.",
            matches=matches[:10],
        )


def default_company(client: ERPNextClient) -> str | None:
    """Return ERPNext's configured default company, falling back to first Company."""
    try:
        response = client.call_method(
            "frappe.client.get_value",
            params={
                "doctype": "Global Defaults",
                "fieldname": "default_company",
            },
        )
        value = _extract_frappe_value(response.get("message"), "default_company")
        if value:
            return value
    except ERPNextAPIError:
        pass

    companies = client.list_records("Company", fields=["name"], limit=1)
    if companies:
        return _optional_text(companies[0].get("name"))
    return None


def _activity_cost_rate_fields(request: ActivityCostRequest) -> dict[str, float]:
    fields: dict[str, float] = {}
    if request.billing_rate is not None:
        fields["billing_rate"] = float(request.billing_rate)
    if request.costing_rate is not None:
        fields["costing_rate"] = float(request.costing_rate)
    return fields


def _supplier_owned_by_email(supplier: dict[str, Any], email: str) -> bool:
    normalized_email = _normalize_508_email(email)
    supplier_email = _optional_text(supplier.get("email_id"))
    if supplier_email and supplier_email.casefold() == normalized_email.casefold():
        return True
    portal_users = _normalized_child_rows(supplier.get("portal_users"))
    for row in portal_users:
        portal_user = _optional_text(row.get("user"))
        if portal_user and portal_user.casefold() == normalized_email.casefold():
            return True
    return False


def _is_not_found_error(_client: ERPNextClient, exc: ERPNextAPIError) -> bool:
    return exc.status_code == 404


def _is_role_profile_error(exc: ERPNextAPIError) -> bool:
    detail = str(exc).casefold()
    return "role_profile_name" in detail or "role profile" in detail


def _normalized_child_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row.pop("doctype", None)
        rows.append(row)
    return rows


def _extract_frappe_value(message: Any, fieldname: str) -> str | None:
    if isinstance(message, dict):
        return _optional_text(message.get(fieldname) or message.get("value"))
    if isinstance(message, str):
        return _optional_text(message)
    if isinstance(message, list) and message:
        return _extract_frappe_value(message[0], fieldname)
    return None


def _required_text(value: Any, field_name: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise EngineerOnboardingError(f"{field_name} is required")
    if len(normalized) > 200:
        raise EngineerOnboardingError(f"{field_name} is too long")
    return normalized


def _normalize_508_email(value: Any) -> str:
    email = _required_text(value, "email").lower()
    if any(character.isspace() for character in email):
        raise EngineerOnboardingError("Engineer email must be a valid @508.dev address")
    if email.count("@") != 1:
        raise EngineerOnboardingError("Engineer email must be a valid @508.dev address")
    local_part, domain = email.split("@", 1)
    if not local_part or domain != "508.dev":
        raise EngineerOnboardingError("Engineer email must be a @508.dev address")
    return email


def _employee_gender_value(value: Any) -> str:
    gender = _optional_text(value) or DEFAULT_EMPLOYEE_GENDER
    if gender not in EMPLOYEE_GENDER_OPTIONS:
        raise EngineerOnboardingError(
            "Employee gender is not a supported ERPNext value"
        )
    return gender


def _preferred_email_value(value: Any) -> str:
    preferred = _optional_text(value) or DEFAULT_PREFERRED_EMAIL
    if preferred not in PREFERRED_EMAIL_OPTIONS:
        raise EngineerOnboardingError("Preferred contact email is not supported")
    return preferred


def _date_value(value: Any, field_name: str, *, default: str) -> str:
    normalized = _optional_text(value) or default
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise EngineerOnboardingError(f"{field_name} must be YYYY-MM-DD") from exc
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
