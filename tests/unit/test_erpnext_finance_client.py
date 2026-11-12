from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.clients.erpnext_finance import ERPNextFinanceClient


@pytest.fixture
def transport() -> Mock:
    client = Mock(spec=ERPNextClient)
    client.call_method.return_value = {"message": {"api_version": 1}}
    return client


def test_finance_reads_use_fixed_endpoints_and_get_parameters(transport: Mock) -> None:
    finance = ERPNextFinanceClient(transport)
    finance.get_finance_status("Co-op")
    transport.call_method.assert_called_with(
        "books_assistant.api.get_finance_status", params={"company": "Co-op"}
    )
    finance.get_supplier_payment_status("Alex", "Co-op")
    transport.call_method.assert_called_with(
        "books_assistant.api.get_supplier_payment_status",
        params={"company": "Co-op", "supplier": "Alex"},
    )
    finance.get_books_health("Co-op", include_info=True, limit=10, offset=20)
    transport.call_method.assert_called_with(
        "books_health.api.get_books_health",
        params={"company": "Co-op", "include_info": 1, "limit": 10, "offset": 20},
    )


def test_billing_review_requires_structured_snapshot_before_confirmation(
    transport: Mock,
) -> None:
    transport.call_method.return_value = {
        "message": {
            "api_version": 1,
            "preview_id": "preview-1",
            "expires_at": "2026-11-13 12:10:00",
            "snapshot": {
                "projects": [{"sales_invoice": "INV-1", "client_amount": 100}]
            },
        }
    }
    finance = ERPNextFinanceClient(transport)
    preview = finance.preview_billing_approval("RUN-1", ["PROJECT-1"], "Co-op")
    assert preview["snapshot"]["projects"][0]["sales_invoice"] == "INV-1"
    transport.call_method.assert_called_with(
        "books_assistant.billing_api.preview_billing_approval",
        payload={"run_id": "RUN-1", "projects": ["PROJECT-1"], "company": "Co-op"},
    )
    transport.call_method.return_value = {
        "message": {
            "api_version": 1,
            "operation_id": "op-1",
            "status": "partial_failure",
        }
    }
    result = finance.approve_billing(preview["preview_id"], "stable-request-1")
    assert result["status"] == "partial_failure"
    transport.call_method.assert_called_with(
        "books_assistant.billing_api.approve_billing",
        payload={"preview_id": "preview-1", "request_id": "stable-request-1"},
    )


@pytest.mark.parametrize(
    "operation,endpoint",
    [
        ("approve_bank", "books_assistant.bank_review.approve"),
        ("prepare_supplier_payment", "books_assistant.pay_supplier.prepare"),
    ],
)
def test_financial_execution_sends_only_frozen_preview_and_request_ids(
    transport: Mock, operation: str, endpoint: str
) -> None:
    transport.call_method.return_value = {
        "message": {"api_version": 1, "operation_id": "op-1"}
    }
    finance = ERPNextFinanceClient(transport)
    getattr(finance, operation)("preview-1", "request-1")
    transport.call_method.assert_called_with(
        endpoint, payload={"preview_id": "preview-1", "request_id": "request-1"}
    )


@pytest.mark.parametrize("projects", [[], [""], [1], "PROJECT-1", ["P", "P"]])
def test_invalid_billing_selection_never_makes_a_request(
    transport: Mock, projects: Any
) -> None:
    with pytest.raises(ERPNextAPIError):
        ERPNextFinanceClient(transport).preview_billing_approval(
            "RUN-1", projects, "Co-op"
        )
    transport.call_method.assert_not_called()


def test_bank_preview_cannot_omit_a_selected_lines_reviewed_fingerprint(
    transport: Mock,
) -> None:
    with pytest.raises(ERPNextAPIError, match="fingerprint"):
        ERPNextFinanceClient(transport).preview_bank_approval(["BT-1"], {}, "Co-op")
    transport.call_method.assert_not_called()


def test_bank_preview_rejects_whitespace_fingerprint_without_request(
    transport: Mock,
) -> None:
    with pytest.raises(ERPNextAPIError, match="fingerprint"):
        ERPNextFinanceClient(transport).preview_bank_approval(
            ["BT-1"], {"BT-1": " \t "}, "Co-op"
        )
    transport.call_method.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"invoices": []}, {"invoices": ["BILL-1"], "all_eligible": True}],
)
def test_supplier_preparation_never_expands_missing_or_empty_selection(
    transport: Mock, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(ERPNextAPIError):
        ERPNextFinanceClient(transport).preview_supplier_preparation(
            "Alex", "Co-op", **kwargs
        )
    transport.call_method.assert_not_called()


@pytest.mark.parametrize(
    "message", [None, [], {}, {"api_version": True}, {"api_version": 2}]
)
def test_unsupported_contract_is_rejected(transport: Mock, message: Any) -> None:
    transport.call_method.return_value = {"message": message}
    with pytest.raises(ERPNextAPIError, match="contract"):
        ERPNextFinanceClient(transport).get_finance_status("Co-op")


def test_missing_review_data_and_operation_id_are_rejected(transport: Mock) -> None:
    finance = ERPNextFinanceClient(transport)
    with pytest.raises(ERPNextAPIError, match="review data"):
        finance.preview_billing_approval("RUN-1", ["PROJECT-1"], "Co-op")
    with pytest.raises(ERPNextAPIError, match="operation ID"):
        finance.approve_billing("preview-1", "request-1")


def test_transport_preserves_stable_erp_error_code() -> None:
    client = ERPNextClient("https://erp.example.invalid", "test:test")
    response = Mock(status_code=417)
    response.json.return_value = {
        "error_code": "PREVIEW_STALE",
        "message": "Review again",
    }
    client._session.request = Mock(return_value=response)
    with pytest.raises(ERPNextAPIError) as error:
        client.request(
            "POST", "/api/method/books_assistant.billing_api.approve_billing"
        )
    assert error.value.error_code == "PREVIEW_STALE"
    assert error.value.status_code == 417
