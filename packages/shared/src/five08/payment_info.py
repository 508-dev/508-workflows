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

    supplier_details: str | None = None


_ACCOUNT_LINE_TOKENS = ("account", "acct", "iban")
_ACCOUNT_LINE_EXCLUSIONS = ("account holder", "account name", "account manager")
_MASKABLE_ACCOUNT_TOKEN_RE = re.compile(
    r"\b[A-Z]{0,4}\d[A-Za-z0-9]*(?:[ -]+[A-Za-z0-9]*\d[A-Za-z0-9]*)*\b",
    flags=re.IGNORECASE,
)
_PAYMENT_INFO_BLOCK_START = "=== 508 Payment Info ==="
_PAYMENT_INFO_BLOCK_END = "=== End 508 Payment Info ==="


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
        if supplier and not _supplier_is_active(supplier):
            supplier = _supplier_for_email(client, normalized_email)
            supplier_id = _text((supplier or {}).get("name"))
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
    payment_details = _normalize_payment_details(payment_info.supplier_details)
    if payment_details is None:
        raise PaymentInfoError("Enter Supplier Details to update.")

    supplier = get_supplier_payment_details(client, identity)
    existing_details = str(supplier.get("supplier_details") or "")
    supplier_details = _replace_payment_info_block(existing_details, payment_details)
    updated_supplier = client.update_record(
        "Supplier",
        identity.supplier_id,
        {"supplier_details": supplier_details},
    )
    return updated_supplier, ["supplier_details"]


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

    masked_details = _sanitize_code_block_text(
        mask_payment_details_for_display(details)
    )
    if len(masked_details) > 1500:
        masked_details = masked_details[:1497].rstrip() + "..."
    lines.append("Supplier Details:")
    lines.append(f"```text\n{masked_details}\n```")
    return lines


def mask_payment_details_for_display(details: str) -> str:
    """Mask account-number lines while leaving bank and routing text readable."""
    output_lines: list[str] = []
    for line in details.splitlines():
        output_lines.append(_mask_account_line(line))
    return "\n".join(output_lines)


def _sanitize_code_block_text(value: str) -> str:
    return value.replace("```", "'''")


def _replace_payment_info_block(existing_details: str, payment_details: str) -> str:
    payment_block = _payment_info_block(payment_details)
    if not existing_details.strip():
        return payment_block

    unmanaged_details = _remove_payment_info_blocks(existing_details)
    if unmanaged_details.strip():
        return f"{unmanaged_details}\n\n{payment_block}"

    return payment_block


def _normalize_payment_details(payment_details: str | None) -> str | None:
    text = _text(payment_details)
    if text is None:
        return None

    lines = [
        line
        for line in text.splitlines()
        if line.strip() not in {_PAYMENT_INFO_BLOCK_START, _PAYMENT_INFO_BLOCK_END}
    ]
    return _text("\n".join(lines))


def _remove_payment_info_blocks(existing_details: str) -> str:
    output_lines: list[str] = []
    block_depth = 0
    for line in existing_details.splitlines():
        marker = line.strip()
        if marker == _PAYMENT_INFO_BLOCK_START:
            block_depth += 1
            continue
        if marker == _PAYMENT_INFO_BLOCK_END:
            if block_depth > 0:
                block_depth -= 1
            continue
        if block_depth == 0:
            output_lines.append(line)

    return "\n".join(output_lines)


def _payment_info_block(payment_details: str) -> str:
    return "\n".join(
        [
            _PAYMENT_INFO_BLOCK_START,
            payment_details.strip(),
            _PAYMENT_INFO_BLOCK_END,
        ]
    )


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


def _supplier_is_active(supplier: dict[str, Any]) -> bool:
    return not _is_enabled_flag(supplier.get("disabled")) and not _is_enabled_flag(
        supplier.get("is_frozen")
    )


def _supplier_for_email(client: ERPNextClient, email: str) -> dict[str, Any] | None:
    for supplier in client.search_suppliers(email, limit=10):
        supplier_id = _text(supplier.get("name"))
        detail = _supplier_by_id(client, supplier_id) if supplier_id else supplier
        if (
            detail
            and _supplier_is_active(detail)
            and _supplier_owned_by_email_or_employee(
                supplier=detail,
                email=email,
                employee_supplier_id=None,
            )
        ):
            return detail
    for supplier in client.list_records(
        "Supplier",
        fields=[
            "name",
            "supplier_name",
            "email_id",
            "disabled",
            "is_frozen",
            "portal_users",
        ],
        filters=[
            ["Supplier", "disabled", "=", 0],
            ["Supplier", "is_frozen", "=", 0],
            ["Supplier", "portal_users.user", "=", email],
        ],
        limit=10,
    ):
        supplier_id = _text(supplier.get("name"))
        detail = _supplier_by_id(client, supplier_id) if supplier_id else supplier
        if (
            detail
            and _supplier_is_active(detail)
            and _supplier_owned_by_email_or_employee(
                supplier=detail,
                email=email,
                employee_supplier_id=None,
            )
        ):
            return detail
    return None


def _is_enabled_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return False


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


def _mask_account_line(line: str) -> str:
    if not _is_account_number_line(line):
        return line
    label, separator, value = line.partition(":")
    if separator:
        return f"{label}{separator}{_mask_account_value(value)}"
    return _MASKABLE_ACCOUNT_TOKEN_RE.sub(
        lambda match: _mask_token(match.group(0)),
        line,
    )


def _is_account_number_line(line: str) -> bool:
    normalized = line.casefold()
    if any(exclusion in normalized for exclusion in _ACCOUNT_LINE_EXCLUSIONS):
        return False
    return any(token in normalized for token in _ACCOUNT_LINE_TOKENS)


def _mask_account_value(value: str) -> str:
    leading_space = value[: len(value) - len(value.lstrip())]
    trailing_space = value[len(value.rstrip()) :]
    core = value.strip()
    if not core:
        return value
    masked = _MASKABLE_ACCOUNT_TOKEN_RE.sub(
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
