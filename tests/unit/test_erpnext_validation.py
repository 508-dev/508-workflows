from __future__ import annotations

from typing import Any

from five08.erpnext_validation import validate_invoice


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

VALID_PURCHASE_INVOICE: dict[str, Any] = {
    "name": "TEST-PINV-0001",
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


def test_valid_sales_invoice_passes() -> None:
    assert validate_invoice(VALID_SALES_INVOICE, "Sales Invoice").passed


def test_valid_purchase_invoice_passes() -> None:
    assert validate_invoice(VALID_PURCHASE_INVOICE, "Purchase Invoice").passed


def test_cost_center_mismatch_flagged() -> None:
    invoice = {**VALID_SALES_INVOICE, "cost_center": "Main - TEST"}
    result = validate_invoice(invoice, "Sales Invoice")
    assert not result.passed
    assert any("Cost Center" in i.message for i in result.issues)


def test_null_items_does_not_crash() -> None:
    invoice = {**VALID_SALES_INVOICE, "items": None}
    assert validate_invoice(invoice, "Sales Invoice").passed


def test_no_project_skips_cost_center_check() -> None:
    invoice = {
        **VALID_SALES_INVOICE,
        "project": None,
        "cost_center": "Main - TEST",
        "items": [{"idx": 1, "project": None, "cost_center": "Main - TEST"}],
    }
    assert validate_invoice(invoice, "Sales Invoice").passed


def test_line_item_project_mismatch_flagged_even_when_invoice_cost_center_empty() -> (
    None
):
    invoice = {
        **VALID_SALES_INVOICE,
        "cost_center": "",
        "items": [{"idx": 1, "project": "TEST-PROJ-999", "cost_center": ""}],
    }
    result = validate_invoice(invoice, "Sales Invoice")
    assert not result.passed
    assert any(
        "Line item #1" in i.message and "Project" in i.message for i in result.issues
    )


def test_line_item_cost_center_mismatch_flagged() -> None:
    invoice = {
        **VALID_SALES_INVOICE,
        "items": [{"idx": 1, "project": "TEST-PROJ-001", "cost_center": "Main - TEST"}],
    }
    result = validate_invoice(invoice, "Sales Invoice")
    assert not result.passed
    assert any(
        "Line item #1" in i.message and "Cost Center" in i.message
        for i in result.issues
    )


def test_line_item_project_mismatch_flagged() -> None:
    invoice = {
        **VALID_SALES_INVOICE,
        "items": [
            {"idx": 1, "project": "TEST-PROJ-999", "cost_center": "Projects - TEST"}
        ],
    }
    result = validate_invoice(invoice, "Sales Invoice")
    assert not result.passed
    assert any(
        "Line item #1" in i.message and "Project" in i.message for i in result.issues
    )


def test_due_date_too_soon_flagged() -> None:
    invoice = {**VALID_PURCHASE_INVOICE, "due_date": "2026-01-10"}
    result = validate_invoice(invoice, "Purchase Invoice")
    assert not result.passed
    assert any("Due date" in i.message for i in result.issues)


def test_due_date_exactly_29_days_passes() -> None:
    invoice = {
        **VALID_PURCHASE_INVOICE,
        "posting_date": "2026-01-01",
        "due_date": "2026-01-30",
    }
    assert validate_invoice(invoice, "Purchase Invoice").passed


def test_due_date_non_string_value_skipped() -> None:
    invoice = {**VALID_PURCHASE_INVOICE, "posting_date": 20260101, "due_date": 20260102}
    assert validate_invoice(invoice, "Purchase Invoice").passed


def test_due_date_not_checked_for_sales_invoice() -> None:
    invoice = {**VALID_SALES_INVOICE, "due_date": "2026-01-02"}
    assert validate_invoice(invoice, "Sales Invoice").passed


def test_multiple_issues_reported() -> None:
    invoice = {
        **VALID_PURCHASE_INVOICE,
        "cost_center": "Main - TEST",
        "due_date": "2026-01-02",
        "items": [{"idx": 1, "project": "TEST-PROJ-001", "cost_center": "Main - TEST"}],
    }
    result = validate_invoice(invoice, "Purchase Invoice")
    assert len(result.issues) >= 2
