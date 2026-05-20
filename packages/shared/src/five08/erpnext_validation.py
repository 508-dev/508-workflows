"""Invoice validation rules for ERPNext invoices."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


PROJECTS_COST_CENTER_PREFIX = "Projects"
VALID_DOCTYPES = {"Sales Invoice", "Purchase Invoice"}
# Per community billing guidelines (see wiki); update here if the policy changes.
MIN_DUE_DATE_DAYS = 29


@dataclass
class ValidationIssue:
    field: str
    message: str


@dataclass
class ValidationResult:
    invoice_name: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


def validate_invoice(invoice: dict[str, Any], doctype: str) -> ValidationResult:
    """Run all validation rules against an invoice."""
    if doctype not in VALID_DOCTYPES:
        raise ValueError(f"Unsupported doctype: {doctype!r}")
    result = ValidationResult(invoice_name=invoice.get("name", "Unknown"))

    _check_cost_center(invoice, result)
    _check_line_item_cost_center_and_project(invoice, result)

    if doctype == "Purchase Invoice":
        _check_due_date(invoice, result)

    return result


def _check_cost_center(invoice: dict[str, Any], result: ValidationResult) -> None:
    """If Project is set, Cost Center must start with 'Projects'."""
    project = invoice.get("project")
    cost_center = (invoice.get("cost_center") or "").strip()

    if not project:
        return

    if not cost_center:
        result.issues.append(
            ValidationIssue(
                field="cost_center",
                message=(
                    f"**Cost Center not set**\n"
                    f"Invoice has Project `{project}` but Cost Center is empty.\n"
                    f"→ Set **Cost Center** to your Projects cost center (e.g. `Projects - 5`)"
                ),
            )
        )
    elif not cost_center.startswith(PROJECTS_COST_CENTER_PREFIX):
        result.issues.append(
            ValidationIssue(
                field="cost_center",
                message=(
                    f"**Cost Center mismatch**\n"
                    f"`{cost_center}` should be `Projects` when a Project is selected.\n"
                    f"→ Change **Cost Center** to your Projects cost center (e.g. `Projects - 5`)"
                ),
            )
        )


def _check_line_item_cost_center_and_project(
    invoice: dict[str, Any], result: ValidationResult
) -> None:
    """Each line item's Cost Center and Project must match the invoice level."""
    invoice_project = invoice.get("project")
    invoice_cost_center = (invoice.get("cost_center") or "").strip()

    if not invoice_cost_center and not invoice_project:
        return

    for idx, item in enumerate(invoice.get("items", []), start=1):
        item_cost_center = (item.get("cost_center") or "").strip()
        item_project = item.get("project")

        if invoice_cost_center and item_cost_center != invoice_cost_center:
            result.issues.append(
                ValidationIssue(
                    field=f"items[{idx}].cost_center",
                    message=(
                        f"**Line item #{idx} — Cost Center mismatch**\n"
                        f"Item has `{item_cost_center or '(empty)'}` but invoice is `{invoice_cost_center}`.\n"
                        f"→ In the Items table, set line #{idx} **Cost Center** to `{invoice_cost_center}`"
                    ),
                )
            )

        if invoice_project and item_project != invoice_project:
            result.issues.append(
                ValidationIssue(
                    field=f"items[{idx}].project",
                    message=(
                        f"**Line item #{idx} — Project mismatch**\n"
                        f"Item has `{item_project or '(empty)'}` but invoice is `{invoice_project}`.\n"
                        f"→ In the Items table, set line #{idx} **Project** to `{invoice_project}`"
                    ),
                )
            )


def _check_due_date(invoice: dict[str, Any], result: ValidationResult) -> None:
    """Flag Purchase Invoices whose due date is too soon after the posting date."""
    posting_date_raw = invoice.get("posting_date")
    due_date_raw = invoice.get("due_date")

    if not posting_date_raw or not due_date_raw:
        return

    try:
        posting_date = datetime.strptime(posting_date_raw, "%Y-%m-%d").date()
        due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date()
    except ValueError:
        return

    delta = (due_date - posting_date).days
    if delta < MIN_DUE_DATE_DAYS:
        earliest = (posting_date + timedelta(days=MIN_DUE_DATE_DAYS)).strftime(
            "%Y-%m-%d"
        )
        result.issues.append(
            ValidationIssue(
                field="due_date",
                message=(
                    f"**Due date too early**\n"
                    f"`{due_date_raw}` is only {delta} day(s) after posting date `{posting_date_raw}` (min {MIN_DUE_DATE_DAYS} days).\n"
                    f"→ Change **Due Date** to at least `{earliest}`"
                ),
            )
        )
