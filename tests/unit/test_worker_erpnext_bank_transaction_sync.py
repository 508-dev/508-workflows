"""Tests for canonical ERPNext Bank Transaction ingestion."""

from __future__ import annotations

from decimal import Decimal

from five08.automation import (
    AutomationAction,
    AutomationActionDisposition,
    AutomationCondition,
    AutomationConditionOperator,
    AutomationRule,
    AutomationRuleMode,
)
from five08.automation_store import StoredAutomationEvent
from five08.project_payments import (
    BANK_TRANSACTION_POSTED_EVENT,
    PaymentDirection,
    StoredBankTransaction,
)
from five08.worker import erpnext_bank_transaction_sync as payment_sync


class FakeERPNextClient:
    def __init__(self, document: dict[str, object] | Exception) -> None:
        self.document = document
        self.requested_names: list[str] = []
        self.closed = False

    def get_bank_transaction(self, transaction_name: str) -> dict[str, object]:
        self.requested_names.append(transaction_name)
        if isinstance(self.document, Exception):
            raise self.document
        return self.document

    def close(self) -> None:
        self.closed = True


def _processor(
    client: FakeERPNextClient,
) -> payment_sync.ERPNextBankTransactionProcessor:
    processor = payment_sync.ERPNextBankTransactionProcessor.__new__(
        payment_sync.ERPNextBankTransactionProcessor
    )
    processor.client = client
    processor.settings = object()
    return processor


def _submitted_inbound_document() -> dict[str, object]:
    return {
        "name": "ACC-BTN-0001",
        "modified": "2026-07-16T12:34:56+00:00",
        "docstatus": 1,
        "date": "2026-07-16T00:00:00+00:00",
        "deposit": "1250.00",
        "withdrawal": "0",
        "currency": "USD",
        "bank_account": "Main Bank",
        "description": "Acme invoice payment",
        "payment_entries": [
            {"payment_document": "Sales Invoice", "payment_entry": "PE-1"}
        ],
    }


def _safe_automatic_rule() -> AutomationRule:
    project_id = "00000000-0000-0000-0000-000000000001"
    return AutomationRule(
        id="rule-1",
        project_id=project_id,
        event_type=BANK_TRANSACTION_POSTED_EVENT,
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.description",
                operator=AutomationConditionOperator.CONTAINS,
                value="Acme",
            ),
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.EQUALS,
                value="1250.00",
            ),
            AutomationCondition(
                fact="transaction.currency",
                operator=AutomationConditionOperator.EQUALS,
                value="USD",
            ),
        ],
        actions=[
            AutomationAction(
                action_type="project_payment.route",
                payload={"project_id": project_id},
            )
        ],
    )


def test_ingest_fetches_canonical_document_and_persists_typed_event(
    monkeypatch,
) -> None:
    client = FakeERPNextClient(_submitted_inbound_document())
    processor = _processor(client)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda _settings, transaction: (
            captured.update({"transaction": transaction})
            or StoredBankTransaction(id="local-transaction", created=True)
        ),
    )
    monkeypatch.setattr(
        payment_sync, "list_enabled_automation_rules", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        payment_sync,
        "persist_automation_event_and_evaluation",
        lambda _settings, *, event, event_key, evaluation: (
            captured.update(
                {"event": event, "event_key": event_key, "evaluation": evaluation}
            )
            or (StoredAutomationEvent(id="event-uuid", created=True), ["action-1"])
        ),
    )

    result = processor.ingest_bank_transaction(
        transaction_name="ACC-BTN-0001",
        source_revision="webhook-revision",
    )

    transaction = captured["transaction"]
    assert getattr(transaction, "amount") == Decimal("1250.00")
    assert getattr(transaction, "direction") is PaymentDirection.INBOUND
    event = captured["event"]
    assert getattr(event, "subject_id") == "local-transaction"
    assert getattr(event, "facts")["transaction"]["amount"] == "1250.00"
    assert result["status"] == "ingested"
    assert result["action_ids"] == ["action-1"]
    assert client.requested_names == ["ACC-BTN-0001"]
    assert client.closed is True


def test_ingest_ignores_non_submitted_canonical_document(monkeypatch) -> None:
    document = _submitted_inbound_document()
    document["docstatus"] = 0
    client = FakeERPNextClient(document)
    processor = _processor(client)

    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not persist")),
    )

    result = processor.ingest_bank_transaction(transaction_name="ACC-BTN-0001")

    assert result == {
        "status": "ignored",
        "reason": "bank_transaction_not_submitted_or_empty",
        "transaction_name": "ACC-BTN-0001",
    }
    assert client.closed is True


def test_ingest_duplicate_event_returns_retryable_action_ids(monkeypatch) -> None:
    client = FakeERPNextClient(_submitted_inbound_document())
    processor = _processor(client)

    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda *_args: StoredBankTransaction(id="local-transaction", created=False),
    )
    monkeypatch.setattr(
        payment_sync, "list_enabled_automation_rules", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        payment_sync,
        "persist_automation_event_and_evaluation",
        lambda *_args, **_kwargs: (
            StoredAutomationEvent(id="event-uuid", created=False),
            [],
        ),
    )
    monkeypatch.setattr(
        payment_sync,
        "list_retryable_automation_action_ids_for_event",
        lambda *_args, **_kwargs: ["failed-action"],
    )
    result = processor.ingest_bank_transaction(transaction_name="ACC-BTN-0001")

    assert result["event_created"] is False
    assert result["action_ids"] == ["failed-action"]
    assert client.closed is True


def test_ingest_ignores_a_stale_canonical_revision(monkeypatch) -> None:
    client = FakeERPNextClient(_submitted_inbound_document())
    processor = _processor(client)
    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda *_args: StoredBankTransaction(
            id="local-transaction", created=False, accepted=False
        ),
    )
    monkeypatch.setattr(
        payment_sync,
        "list_enabled_automation_rules",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not evaluate")
        ),
    )

    result = processor.ingest_bank_transaction(transaction_name="ACC-BTN-0001")

    assert result == {
        "status": "ignored",
        "reason": "bank_transaction_revision_superseded",
        "transaction_name": "ACC-BTN-0001",
        "transaction_id": "local-transaction",
    }
    assert client.closed is True


def test_ingest_downgrades_unorderable_erp_revision_to_suggestion(monkeypatch) -> None:
    document = _submitted_inbound_document()
    document["modified"] = "not-an-erpnext-timestamp"
    client = FakeERPNextClient(document)
    processor = _processor(client)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda *_args: StoredBankTransaction(id="local-transaction", created=True),
    )
    monkeypatch.setattr(
        payment_sync,
        "list_enabled_automation_rules",
        lambda *_args, **_kwargs: [_safe_automatic_rule()],
    )
    monkeypatch.setattr(
        payment_sync,
        "persist_automation_event_and_evaluation",
        lambda _settings, *, event, event_key, evaluation: (
            captured.update(
                {"event": event, "event_key": event_key, "evaluation": evaluation}
            )
            or (StoredAutomationEvent(id="event-uuid", created=True), ["action-1"])
        ),
    )

    result = processor.ingest_bank_transaction(transaction_name="ACC-BTN-0001")

    evaluation = captured["evaluation"]
    proposal = getattr(evaluation, "action_proposals")[0]
    assert proposal.mode is AutomationRuleMode.SUGGEST
    assert proposal.disposition is AutomationActionDisposition.SUGGESTED
    assert result["status"] == "ingested"
    assert client.closed is True


def test_ingest_closes_client_after_unexpected_error(monkeypatch) -> None:
    client = FakeERPNextClient(_submitted_inbound_document())
    processor = _processor(client)
    monkeypatch.setattr(
        payment_sync,
        "upsert_bank_transaction",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    try:
        processor.ingest_bank_transaction(transaction_name="ACC-BTN-0001")
    except RuntimeError as exc:
        assert str(exc) == "database unavailable"
    else:  # pragma: no cover - keeps the close assertion meaningful
        raise AssertionError("expected ingestion failure")

    assert client.closed is True
