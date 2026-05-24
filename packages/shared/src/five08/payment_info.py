"""Self-service ERPNext Supplier payment-info helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient


class PaymentInfoError(RuntimeError):
    """Raised when a self-service payment-info request cannot be completed."""


@dataclass(frozen=True, slots=True)
class PaymentIdentity:
    """ERPNext identity resolved from one 508.dev email."""

    email: str
    user_id: str
    employee_id: str | None
    supplier_id: str
    supplier_name: str | None


@dataclass(frozen=True, slots=True)
class PaymentInfoInput:
    """User-submitted payment information."""

    account_name: str | None = None
    bank: str | None = None
    bank_account_no: str | None = None
    branch_code: str | None = None
    iban: str | None = None


PAYMENT_INFO_BLOCK_START = "=== 508 Payment Info ==="
PAYMENT_INFO_BLOCK_END = "=== End 508 Payment Info ==="
_ACCOUNT_NUMBER_LABELS = (
    "account number",
    "account no",
    "account #",
    "acct number",
    "acct no",
    "iban",
)
_MASKABLE_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 -]{3,}[A-Za-z0-9]")


def normalize_508_email(value: str | None) -> str:
    """Return a normalized 508.dev email or raise PaymentInfoError."""
    email = str(value or "").strip().lower()
    if not email or not email.endswith("@508.dev"):
        raise PaymentInfoError("A linked @508.dev email is required.")
    return email


def resolve_payment_identity(client: ERPNextClient, email: str) -> PaymentIdentity:
    """Resolve the ERPNext User, Employee, and Supplier for one 508.dev email."""
    normalized_email = normalize_508_email(email)

    try:
        user = client.get_record("User", normalized_email)
    except ERPNextAPIError as exc:
        if exc.status_code == 404:
            raise PaymentInfoError(
                f"No ERPNext User exists for {normalized_email}."
            ) from exc
        raise

    user_id = _text(user.get("name") or user.get("email")) or normalized_email
    user_email = _text(user.get("email") or user.get("name"))
    if user_email and user_email.casefold() != normalized_email.casefold():
        raise PaymentInfoError("ERPNext User email did not match the linked CRM email.")

    employee = _employee_for_user(client, normalized_email)
    supplier_id = _text((employee or {}).get("supplier"))
    if supplier_id:
        supplier = _supplier_by_id(client, supplier_id)
    else:
        supplier = _supplier_for_email(client, normalized_email)
        supplier_id = _text((supplier or {}).get("name"))

    if not supplier or not supplier_id:
        raise PaymentInfoError(
            "No ERPNext Supplier is linked to your ERP user yet. "
            "Ask the operations team to finish ERP onboarding first."
        )

    if not _supplier_owned_by_email_or_employee(
        supplier=supplier,
        email=normalized_email,
        employee_supplier_id=_text((employee or {}).get("supplier")),
    ):
        raise PaymentInfoError(
            "The linked ERP Supplier could not be verified for your ERP user."
        )

    return PaymentIdentity(
        email=normalized_email,
        user_id=user_id,
        employee_id=_text((employee or {}).get("name")),
        supplier_id=supplier_id,
        supplier_name=_text(supplier.get("supplier_name") or supplier.get("name")),
    )


def get_supplier_payment_details(
    client: ERPNextClient,
    identity: PaymentIdentity,
) -> dict[str, Any]:
    """Return the resolved Supplier record that stores payment details."""
    supplier = client.get_record("Supplier", identity.supplier_id)
    supplier_id = _text(supplier.get("name"))
    if supplier_id != identity.supplier_id:
        raise PaymentInfoError("ERPNext Supplier lookup returned the wrong record.")
    return supplier


def update_supplier_payment_details(
    client: ERPNextClient,
    identity: PaymentIdentity,
    payment_info: PaymentInfoInput,
) -> tuple[dict[str, Any], list[str]]:
    """Replace the managed payment block inside Supplier.supplier_details."""
    fields = _payment_info_fields(payment_info)
    if not fields:
        raise PaymentInfoError("Enter at least one payment-info field to update.")

    supplier = get_supplier_payment_details(client, identity)
    existing_details = str(supplier.get("supplier_details") or "").strip()
    payment_block = _format_payment_info_block(fields)
    updated_details = _replace_managed_payment_block(existing_details, payment_block)
    updated_supplier = client.update_record(
        "Supplier",
        identity.supplier_id,
        {"supplier_details": updated_details},
    )
    return updated_supplier, list(fields)


def payment_info_summary(
    identity: PaymentIdentity, supplier: dict[str, Any]
) -> list[str]:
    """Return safe-to-display, masked Supplier payment-info summary lines."""
    details = str(supplier.get("supplier_details") or "").strip()
    lines = [
        f"ERP user: `{identity.email}`",
        f"Supplier: `{identity.supplier_name or identity.supplier_id}`",
    ]
    if not details:
        lines.append("Supplier Details: not set")
        return lines

    masked_details = mask_payment_details_for_display(details)
    if len(masked_details) > 1500:
        masked_details = masked_details[:1497].rstrip() + "..."
    lines.append("Supplier Details:")
    lines.append(f"```text\n{masked_details}\n```")
    return lines


def mask_payment_details_for_display(details: str) -> str:
    """Mask account-number style lines while leaving bank and routing text readable."""
    output_lines: list[str] = []
    for line in details.splitlines():
        label, separator, value = line.partition(":")
        if separator and _is_account_number_label(label):
            output_lines.append(f"{label}{separator}{_mask_payment_token(value)}")
        else:
            output_lines.append(line)
    return "\n".join(output_lines)


def _replace_managed_payment_block(existing_details: str, payment_block: str) -> str:
    if not existing_details:
        return payment_block

    pattern = re.compile(
        rf"{re.escape(PAYMENT_INFO_BLOCK_START)}.*?"
        rf"{re.escape(PAYMENT_INFO_BLOCK_END)}",
        flags=re.DOTALL,
    )
    if pattern.search(existing_details):
        return pattern.sub(payment_block, existing_details).strip()

    return f"{existing_details}\n\n{payment_block}".strip()


def _format_payment_info_block(fields: dict[str, str]) -> str:
    lines = [PAYMENT_INFO_BLOCK_START]
    for field, label in (
        ("account_name", "Account holder"),
        ("bank", "Bank"),
        ("branch_code", "Routing / SWIFT / branch"),
        ("bank_account_no", "Account number"),
        ("iban", "IBAN"),
    ):
        value = fields.get(field)
        if value:
            lines.append(f"{label}: {value}")
    lines.append(PAYMENT_INFO_BLOCK_END)
    return "\n".join(lines)


def _employee_for_user(
    client: ERPNextClient,
    email: str,
) -> dict[str, Any] | None:
    employees = client.list_records(
        "Employee",
        fields=["name", "employee_name", "user_id", "supplier"],
        filters=[["Employee", "user_id", "=", email]],
        limit=1,
    )
    if not employees:
        return None
    employee_id = _text(employees[0].get("name"))
    if employee_id is None:
        return employees[0]
    return client.get_record("Employee", employee_id)


def _supplier_by_id(client: ERPNextClient, supplier_id: str) -> dict[str, Any] | None:
    try:
        return client.get_record("Supplier", supplier_id)
    except ERPNextAPIError as exc:
        if exc.status_code == 404:
            return None
        raise


def _supplier_for_email(client: ERPNextClient, email: str) -> dict[str, Any] | None:
    for supplier in client.search_suppliers(email, limit=10):
        supplier_id = _text(supplier.get("name"))
        detail = _supplier_by_id(client, supplier_id) if supplier_id else supplier
        if detail and _supplier_owned_by_email_or_employee(
            supplier=detail,
            email=email,
            employee_supplier_id=None,
        ):
            return detail
    return None


def _supplier_owned_by_email_or_employee(
    *,
    supplier: dict[str, Any],
    email: str,
    employee_supplier_id: str | None,
) -> bool:
    supplier_id = _text(supplier.get("name"))
    if employee_supplier_id and supplier_id == employee_supplier_id:
        return True
    supplier_email = _text(supplier.get("email_id"))
    if supplier_email and supplier_email.casefold() == email.casefold():
        return True
    portal_users = supplier.get("portal_users")
    if not isinstance(portal_users, list):
        return False
    for row in portal_users:
        if not isinstance(row, dict):
            continue
        portal_user = _text(row.get("user"))
        if portal_user and portal_user.casefold() == email.casefold():
            return True
    return False


def _payment_info_fields(payment_info: PaymentInfoInput) -> dict[str, str]:
    fields: dict[str, str] = {}
    for field in (
        "account_name",
        "bank",
        "bank_account_no",
        "branch_code",
        "iban",
    ):
        value = _text(getattr(payment_info, field))
        if value:
            fields[field] = value
    return fields


def _is_account_number_label(label: str) -> bool:
    normalized = label.strip().casefold()
    return any(token in normalized for token in _ACCOUNT_NUMBER_LABELS)


def _mask_payment_token(value: str) -> str:
    leading_space = value[: len(value) - len(value.lstrip())]
    trailing_space = value[len(value.rstrip()) :]
    core = value.strip()
    if not core:
        return value
    masked = _MASKABLE_TOKEN_RE.sub(
        lambda match: _mask_token(match.group(0)),
        core,
    )
    return f"{leading_space}{masked}{trailing_space}"


def _mask_token(value: str) -> str:
    compact = re.sub(r"\s+", "", value.strip())
    if len(compact) <= 4:
        return compact
    return f"{compact[:2]}****{compact[-2:]}"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
