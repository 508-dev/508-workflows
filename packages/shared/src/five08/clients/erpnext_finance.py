"""Named ERP finance actions for trusted backend callers.

Authenticate the supplied client as the verified ERP actor. A shared bot token
does not delegate a Discord user's accounting permissions. No method accepts an
actor override or a caller-selected Frappe method path.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict, cast

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient


class FinancePreview(TypedDict):
    api_version: Literal[1]
    preview_id: str
    expires_at: str
    snapshot: dict[str, Any]


class ERPNextFinanceClient:
    """Read state, freeze reviewed actions, and confirm with durable request IDs."""

    def __init__(self, client: ERPNextClient) -> None:
        self.client = client

    @staticmethod
    def _required(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ERPNextAPIError(f"{label} is required (at most 128 characters)")
        return value

    @staticmethod
    def _selection(values: list[str], label: str) -> list[str]:
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 100
            or any(not isinstance(value, str) or not value.strip() for value in values)
            or len(set(values)) != len(values)
        ):
            raise ERPNextAPIError(f"{label} must contain 1–100 distinct document names")
        return values

    def _call(
        self, method: str, values: dict[str, Any], *, write: bool = False
    ) -> dict[str, Any]:
        envelope = self.client.call_method(
            method, **({"payload": values} if write else {"params": values})
        )
        result = envelope.get("message")
        if (
            not isinstance(result, dict)
            or type(result.get("api_version")) is not int
            or result["api_version"] != 1
        ):
            raise ERPNextAPIError("ERP finance response has an unsupported contract")
        return result

    def _preview(self, method: str, values: dict[str, Any]) -> FinancePreview:
        result = self._call(method, values, write=True)
        if (
            not isinstance(result.get("preview_id"), str)
            or not result["preview_id"]
            or not isinstance(result.get("expires_at"), str)
            or not result["expires_at"]
            or not isinstance(result.get("snapshot"), dict)
        ):
            raise ERPNextAPIError("ERP finance preview is missing review data")
        return cast(FinancePreview, result)

    def _confirm(self, method: str, preview_id: str, request_id: str) -> dict[str, Any]:
        result = self._call(
            method,
            {
                "preview_id": self._required(preview_id, "Preview ID"),
                "request_id": self._required(request_id, "Request ID"),
            },
            write=True,
        )
        if (
            not isinstance(result.get("operation_id"), str)
            or not result["operation_id"]
        ):
            raise ERPNextAPIError("ERP finance result is missing its operation ID")
        return result

    def get_finance_status(self, company: str) -> dict[str, Any]:
        return self._call(
            "books_assistant.api.get_finance_status",
            {"company": self._required(company, "Company")},
        )

    def get_supplier_payment_status(
        self, supplier: str, company: str
    ) -> dict[str, Any]:
        return self._call(
            "books_assistant.api.get_supplier_payment_status",
            {
                "supplier": self._required(supplier, "Supplier"),
                "company": self._required(company, "Company"),
            },
        )

    def get_books_health(
        self,
        company: str,
        *,
        include_info: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 200
            or type(offset) is not int
            or not 0 <= offset <= 100000
            or type(include_info) is not bool
        ):
            raise ERPNextAPIError("Invalid Books Health pagination or include_info")
        return self._call(
            "books_health.api.get_books_health",
            {
                "company": self._required(company, "Company"),
                "include_info": int(include_info),
                "limit": limit,
                "offset": offset,
            },
        )

    def preview_billing_approval(
        self, run_id: str, projects: list[str], company: str
    ) -> FinancePreview:
        return self._preview(
            "books_assistant.billing_api.preview_billing_approval",
            {
                "run_id": self._required(run_id, "Billing Run"),
                "projects": self._selection(projects, "Projects"),
                "company": self._required(company, "Company"),
            },
        )

    def approve_billing(self, preview_id: str, request_id: str) -> dict[str, Any]:
        return self._confirm(
            "books_assistant.billing_api.approve_billing", preview_id, request_id
        )

    def preview_bank_approval(
        self,
        bank_transactions: list[str],
        expected_fingerprints: dict[str, str],
        company: str,
    ) -> FinancePreview:
        names = self._selection(bank_transactions, "Bank transactions")
        if (
            not isinstance(expected_fingerprints, dict)
            or set(expected_fingerprints) != set(names)
            or any(
                not isinstance(value, str) or not value
                for value in expected_fingerprints.values()
            )
        ):
            raise ERPNextAPIError(
                "Every selected bank line needs its reviewed fingerprint"
            )
        return self._preview(
            "books_assistant.bank_review.preview_approval",
            {
                "bank_transactions": names,
                "expected_fingerprints": expected_fingerprints,
                "company": self._required(company, "Company"),
            },
        )

    def approve_bank(self, preview_id: str, request_id: str) -> dict[str, Any]:
        return self._confirm(
            "books_assistant.bank_review.approve", preview_id, request_id
        )

    def preview_supplier_preparation(
        self,
        supplier: str,
        company: str,
        *,
        invoices: list[str] | None = None,
        all_eligible: bool = False,
        bank_account: str | None = None,
    ) -> FinancePreview:
        if type(all_eligible) is not bool or (invoices is None) != all_eligible:
            raise ERPNextAPIError(
                "Select invoices or explicitly request all eligible bills"
            )
        values: dict[str, Any] = {
            "supplier": self._required(supplier, "Supplier"),
            "company": self._required(company, "Company"),
            "all_eligible": all_eligible,
        }
        if invoices is not None:
            values["invoices"] = self._selection(invoices, "Invoices")
        if bank_account is not None:
            values["bank_account"] = self._required(bank_account, "Bank account")
        return self._preview("books_assistant.pay_supplier.preview_preparation", values)

    def prepare_supplier_payment(
        self, preview_id: str, request_id: str
    ) -> dict[str, Any]:
        return self._confirm(
            "books_assistant.pay_supplier.prepare", preview_id, request_id
        )
