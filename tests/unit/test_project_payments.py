"""Tests for ERPNext Bank Transaction normalization and rule facts."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

import five08.project_payments as project_payments
from five08.project_payments import (
    PaymentDirection,
    automatic_project_payment_rule_is_safe,
    erpnext_bank_transaction_to_input,
    learned_project_payment_suggestion_rule,
    payment_automation_facts,
    payment_automation_subject_snapshot,
    project_payment_evaluation_for_execution,
    project_payment_match_method_for_action,
    project_payment_notification_idempotency_key,
    project_payment_rule_for_evaluation,
)
from five08.automation import (
    AutomationAction,
    AutomationCondition,
    AutomationConditionOperator,
    AutomationEventInput,
    AutomationRule,
    AutomationRuleMode,
    AutomationRuleOrigin,
    evaluate_automation_rules,
)


def _install_cursor(monkeypatch, cursor: Mock) -> None:
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=None)
    cursor_context = Mock()
    cursor_context.__enter__ = Mock(return_value=cursor)
    cursor_context.__exit__ = Mock(return_value=None)
    connection.cursor.return_value = cursor_context
    monkeypatch.setattr(
        project_payments,
        "get_postgres_connection",
        lambda _settings: connection,
    )


def _bank_transaction(**overrides: object) -> dict[str, object]:
    transaction: dict[str, object] = {
        "name": "ACC-BTN-0001",
        "modified": "2026-07-16T12:34:56+00:00",
        "docstatus": 1,
        "date": "2026-07-16T00:00:00+00:00",
        "deposit": "1020.55",
        "withdrawal": "0",
        "currency": "USD",
        "bank_account": "Main Bank",
        "party": "Acme Ltd",
        "description": "Invoice payment",
        "reference_number": "WIRE-123",
        "payment_entries": [{"payment_entry": "PE-0001"}],
    }
    transaction.update(overrides)
    return transaction


def test_erpnext_bank_transaction_normalizes_inbound_money_as_decimal() -> None:
    result = erpnext_bank_transaction_to_input(_bank_transaction())

    assert result is not None
    assert result.direction is PaymentDirection.INBOUND
    assert result.amount == Decimal("1020.55")
    assert result.transaction_id is None
    assert result.reconciliation_entries == [{"payment_entry": "PE-0001"}]
    assert payment_automation_facts(result) == {
        "transaction": {
            "direction": "inbound",
            "amount": "1020.55",
            "currency": "USD",
            "description": "Invoice payment",
            "counterparty": "Acme Ltd",
            "reference_number": "WIRE-123",
            "bank_account": "Main Bank",
            "has_reconciliation": True,
        }
    }


def test_erpnext_bank_transaction_prefers_bank_statement_counterparty() -> None:
    result = erpnext_bank_transaction_to_input(
        _bank_transaction(
            bank_party_name="Acme Bank Statement Name",
            party="Reconciliation-derived Customer",
        )
    )

    assert result is not None
    assert result.counterparty == "Acme Bank Statement Name"


def test_approved_receipt_teaches_only_a_deterministic_review_suggestion() -> None:
    snapshot = {
        "direction": "inbound",
        "currency": "gbp",
        "counterparty": " Acme   Ltd ",
        "reference_number": "WIRE-123",
        "description": "Invoice payment",
    }

    rule = learned_project_payment_suggestion_rule(
        project_id="00000000-0000-0000-0000-000000000001",
        subject_snapshot=snapshot,
    )
    duplicate = learned_project_payment_suggestion_rule(
        project_id="00000000-0000-0000-0000-000000000001",
        subject_snapshot=snapshot,
    )

    assert rule is not None
    assert duplicate == rule
    assert rule.origin is AutomationRuleOrigin.LEARNED
    assert rule.mode is AutomationRuleMode.SUGGEST
    assert rule.priority == -100
    assert rule.learning_key is not None
    assert "Acme" not in rule.learning_key
    assert [condition.model_dump(mode="json") for condition in rule.conditions] == [
        {"fact": "transaction.direction", "operator": "equals", "value": "inbound"},
        {
            "fact": "transaction.counterparty",
            "operator": "contains",
            "value": "Acme Ltd",
        },
        {"fact": "transaction.currency", "operator": "equals", "value": "GBP"},
    ]
    assert rule.actions[0].payload == {
        "project_id": "00000000-0000-0000-0000-000000000001"
    }


@pytest.mark.parametrize(
    "snapshot",
    [
        {"direction": "outbound", "currency": "GBP", "counterparty": "Acme Ltd"},
        {"direction": "inbound", "currency": "GB", "counterparty": "Acme Ltd"},
        {"direction": "inbound", "currency": "GBP", "counterparty": "--"},
    ],
)
def test_learning_rejects_weak_or_nonreceipt_evidence(snapshot: dict[str, str]) -> None:
    assert (
        learned_project_payment_suggestion_rule(
            project_id="00000000-0000-0000-0000-000000000001",
            subject_snapshot=snapshot,
        )
        is None
    )


@pytest.mark.parametrize(
    ("approved_by", "origin", "expected"),
    [
        (
            None,
            "configured",
            project_payments.ProjectPaymentMatchMethod.CONFIGURED_RULE,
        ),
        (
            "discord:admin-1",
            "learned",
            project_payments.ProjectPaymentMatchMethod.LEARNED_SUGGESTION,
        ),
        (
            "discord:admin-1",
            "configured",
            project_payments.ProjectPaymentMatchMethod.MANUAL,
        ),
    ],
)
def test_payment_allocation_match_method_retains_learning_provenance(
    approved_by: str | None,
    origin: str,
    expected: project_payments.ProjectPaymentMatchMethod,
) -> None:
    assert (
        project_payment_match_method_for_action(
            approved_by=approved_by,
            rule_origin=origin,
        )
        is expected
    )


def test_newer_bank_transaction_revision_only_supersedes_different_automatic_evidence(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = {"id": "transaction-uuid", "created": False}
    _install_cursor(monkeypatch, cursor)
    transaction = erpnext_bank_transaction_to_input(_bank_transaction())
    assert transaction is not None

    result = project_payments.upsert_bank_transaction(
        project_payments.SharedSettings(),
        transaction,
    )

    assert result.id == "transaction-uuid"
    assert result.accepted is True
    supersede_query, supersede_params = cursor.execute.call_args_list[1].args
    assert "UPDATE project_payment_allocations" in supersede_query
    assert "match_method = %s" in supersede_query
    assert "source_revision' IS DISTINCT FROM %s" in supersede_query
    assert supersede_params == (
        "superseded",
        "source_revision_changed",
        "transaction-uuid",
        "confirmed",
        "configured_rule",
        "2026-07-16T12:34:56+00:00",
    )


def test_notification_idempotency_is_scoped_to_channel_registration() -> None:
    first_key = project_payment_notification_idempotency_key(
        payment_transaction_id="payment-1",
        allocation_id="allocation-1",
        project_discord_channel_id="mapping-old",
    )
    re_registered_key = project_payment_notification_idempotency_key(
        payment_transaction_id="payment-1",
        allocation_id="allocation-1",
        project_discord_channel_id="mapping-new",
    )

    assert first_key != re_registered_key
    assert first_key.endswith(":mapping-old")
    assert re_registered_key.endswith(":mapping-new")


def test_notification_create_handles_the_durable_allocation_channel_unique_key(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.side_effect = [None, {"id": "existing-notification"}]
    _install_cursor(monkeypatch, cursor)

    notification_id, created = project_payments.create_project_payment_notification(
        project_payments.SharedSettings(),
        allocation_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        project_discord_channel_id="00000000-0000-0000-0000-000000000003",
        channel_id="discord-channel",
        idempotency_key="payment-received:v2:existing",
        payload={},
    )

    assert (notification_id, created) == ("existing-notification", False)
    insert_query, _insert_params = cursor.execute.call_args_list[0].args
    lookup_query, lookup_params = cursor.execute.call_args_list[1].args
    assert "ON CONFLICT DO NOTHING" in insert_query
    assert "allocation_id = %s::uuid" in lookup_query
    assert lookup_params == (
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000003",
    )


def test_erpnext_bank_transaction_normalizes_outbound_without_receipt_semantics() -> (
    None
):
    result = erpnext_bank_transaction_to_input(
        _bank_transaction(deposit="0", withdrawal="42.10")
    )

    assert result is not None
    assert result.direction is PaymentDirection.OUTBOUND
    assert result.amount == Decimal("42.10")


def test_erpnext_bank_transaction_ignores_draft_and_zero_value_documents() -> None:
    assert erpnext_bank_transaction_to_input(_bank_transaction(docstatus=0)) is None
    assert (
        erpnext_bank_transaction_to_input(
            _bank_transaction(deposit="0", withdrawal="0")
        )
        is None
    )


def test_erpnext_bank_transaction_rejects_ambiguous_or_negative_amounts() -> None:
    with pytest.raises(ValueError, match="both deposit and withdrawal"):
        erpnext_bank_transaction_to_input(
            _bank_transaction(deposit="50", withdrawal="25")
        )
    with pytest.raises(ValueError, match="negative amount"):
        erpnext_bank_transaction_to_input(_bank_transaction(deposit="-1"))


def test_amount_only_automatic_payment_rule_is_downgraded_to_suggestion() -> None:
    rule = AutomationRule(
        id="rule-1",
        project_id="00000000-0000-0000-0000-000000000001",
        event_type="bank_transaction.posted.v1",
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.EQUALS,
                value="1250.00",
            )
        ],
        actions=[AutomationAction(action_type="project_payment.route")],
    )

    assert automatic_project_payment_rule_is_safe(rule) is False
    assert project_payment_rule_for_evaluation(rule).mode is AutomationRuleMode.SUGGEST


def test_named_and_amount_matched_automatic_payment_rule_is_safe() -> None:
    rule = AutomationRule(
        id="rule-1",
        project_id="00000000-0000-0000-0000-000000000001",
        event_type="bank_transaction.posted.v1",
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.EQUALS,
                value="1250.00",
            ),
            AutomationCondition(
                fact="transaction.description",
                operator=AutomationConditionOperator.CONTAINS,
                value="Acme Design Sprint",
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
                payload={"project_id": "00000000-0000-0000-0000-000000000001"},
            )
        ],
    )

    assert automatic_project_payment_rule_is_safe(rule) is True


def test_learned_payment_rule_cannot_become_automatic() -> None:
    rule = AutomationRule(
        id="rule-1",
        project_id="00000000-0000-0000-0000-000000000001",
        event_type="bank_transaction.posted.v1",
        origin=AutomationRuleOrigin.LEARNED,
        learning_key="project-payment:v1:abc123",
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.EQUALS,
                value="1250.00",
            ),
            AutomationCondition(
                fact="transaction.counterparty",
                operator=AutomationConditionOperator.CONTAINS,
                value="Acme Ltd",
            ),
            AutomationCondition(
                fact="transaction.currency",
                operator=AutomationConditionOperator.EQUALS,
                value="GBP",
            ),
        ],
        actions=[
            AutomationAction(
                action_type="project_payment.route",
                payload={"project_id": "00000000-0000-0000-0000-000000000001"},
            )
        ],
    )

    assert automatic_project_payment_rule_is_safe(rule) is False
    assert project_payment_rule_for_evaluation(rule).mode is AutomationRuleMode.SUGGEST


@pytest.mark.parametrize(
    ("rule_project_id", "action_project_id"),
    [
        (None, "00000000-0000-0000-0000-000000000001"),
        ("not-a-uuid", "00000000-0000-0000-0000-000000000001"),
        (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ),
        ("00000000-0000-0000-0000-000000000001", None),
    ],
)
def test_automatic_payment_rule_with_invalid_project_scope_is_downgraded(
    rule_project_id: str | None,
    action_project_id: str | None,
) -> None:
    payload = {"project_id": action_project_id} if action_project_id else {}
    rule = AutomationRule(
        id="rule-1",
        project_id=rule_project_id,
        event_type="bank_transaction.posted.v1",
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.has_reconciliation",
                operator=AutomationConditionOperator.EQUALS,
                value=True,
            )
        ],
        actions=[
            AutomationAction(
                action_type="project_payment.route",
                payload=payload,
            )
        ],
    )

    assert automatic_project_payment_rule_is_safe(rule) is False
    assert project_payment_rule_for_evaluation(rule).mode is AutomationRuleMode.SUGGEST


def test_payment_notification_recovery_lists_stale_sending_rows(monkeypatch) -> None:
    cursor = Mock()
    cursor.fetchall.return_value = [{"id": "stale-notification"}]
    _install_cursor(monkeypatch, cursor)

    result = project_payments.list_retryable_project_payment_notification_ids(
        project_payments.SharedSettings(),
        stale_after_seconds=90,
    )

    assert result == ["stale-notification"]
    query, params = cursor.execute.call_args.args
    assert "status = %s" in query
    assert params[2:4] == ("sending", 90)


def test_notification_failure_only_updates_active_sending_lease(monkeypatch) -> None:
    cursor = Mock()
    _install_cursor(monkeypatch, cursor)

    project_payments.mark_project_payment_notification_failed(
        project_payments.SharedSettings(),
        notification_id="notification-1",
        error="temporary failure",
        lease_token="00000000-0000-0000-0000-000000000001",
    )

    query, params = cursor.execute.call_args.args
    assert "AND status = %s" in query
    assert params[-2:] == ("sending", "00000000-0000-0000-0000-000000000001")


def test_notification_ineligibility_blocks_superseded_allocations_after_lease_abandons(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = {"id": "notification-1"}
    _install_cursor(monkeypatch, cursor)

    blocked = project_payments.block_project_payment_notification_if_ineligible(
        project_payments.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        stale_after_seconds=90,
    )

    assert blocked is True
    query, params = cursor.execute.call_args.args
    assert "outbox.status = %s" in query
    assert "outbox.locked_at < NOW()" in query
    assert "allocation.status = %s" in query
    assert params[-3:] == ("sending", 90, "confirmed")


def test_bot_delivery_context_requires_the_active_worker_outbox_lease(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = None
    _install_cursor(monkeypatch, cursor)

    context = project_payments.get_project_payment_notification_delivery_context(
        project_payments.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        worker_lease_token="00000000-0000-0000-0000-000000000002",
    )

    assert context is None
    query, params = cursor.execute.call_args.args
    assert "outbox.status = %s" in query
    assert "outbox.lease_token = %s::uuid" in query
    assert params[1:3] == ("sending", "00000000-0000-0000-0000-000000000002")
    assert params[-1] == "confirmed"


def test_notification_source_context_requires_the_active_worker_lease(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.return_value = {
        "notification_id": "00000000-0000-0000-0000-000000000001",
        "bank_transaction_id": "00000000-0000-0000-0000-000000000002",
        "source": "erpnext",
        "external_id": "ACC-BTN-0001",
        "allocation_status": "confirmed",
        "allocation_source_revision": "2026-07-16T12:34:56+00:00",
        "current_source_revision": "2026-07-16T12:34:56+00:00",
    }
    _install_cursor(monkeypatch, cursor)

    context = project_payments.get_project_payment_notification_source_context(
        project_payments.SharedSettings(),
        notification_id="00000000-0000-0000-0000-000000000001",
        lease_token="00000000-0000-0000-0000-000000000003",
    )

    assert context is not None
    assert context.external_id == "ACC-BTN-0001"
    assert (
        context.allocation_status
        is project_payments.ProjectPaymentAllocationStatus.CONFIRMED
    )
    assert context.allocation_source_revision == "2026-07-16T12:34:56+00:00"
    query, params = cursor.execute.call_args.args
    assert "outbox.status = %s" in query
    assert "outbox.lease_token = %s::uuid" in query
    assert params == (
        "00000000-0000-0000-0000-000000000001",
        "sending",
        "00000000-0000-0000-0000-000000000003",
    )


def test_payment_snapshot_preserves_canonical_revision_and_money() -> None:
    transaction = erpnext_bank_transaction_to_input(_bank_transaction())
    assert transaction is not None

    assert payment_automation_subject_snapshot(transaction) == {
        "source": "erpnext",
        "external_id": "ACC-BTN-0001",
        "source_revision": "2026-07-16T12:34:56+00:00",
        "transaction_id": None,
        "direction": "inbound",
        "amount": "1020.55",
        "currency": "USD",
        "bank_account": "Main Bank",
        "counterparty": "Acme Ltd",
        "description": "Invoice payment",
        "reference_number": "WIRE-123",
        "posted_at": "2026-07-16T00:00:00+00:00",
        "reconciliation_entries": [{"payment_entry": "PE-0001"}],
    }


def test_competing_top_priority_automatic_routes_become_suggestions() -> None:
    event = AutomationEventInput(
        event_id="event-1",
        event_type="bank_transaction.posted.v1",
        source="erpnext",
        subject_id="transaction-1",
        occurred_at=datetime.now(timezone.utc),
        facts={"transaction": {"direction": "inbound"}},
    )
    rules = [
        AutomationRule(
            id=f"rule-{suffix}",
            project_id=f"00000000-0000-0000-0000-00000000000{index}",
            event_type=event.event_type,
            priority=10,
            mode=AutomationRuleMode.AUTOMATIC,
            conditions=[
                AutomationCondition(
                    fact="transaction.direction",
                    operator=AutomationConditionOperator.EQUALS,
                    value="inbound",
                )
            ],
            actions=[
                AutomationAction(
                    action_type="project_payment.route",
                    payload={
                        "project_id": f"00000000-0000-0000-0000-00000000000{index}"
                    },
                )
            ],
        )
        for index, suffix in ((1, "a"), (2, "b"))
    ]
    evaluation = evaluate_automation_rules(event, rules)

    resolved = project_payment_evaluation_for_execution(evaluation)

    assert {proposal.disposition.value for proposal in resolved.action_proposals} == {
        "suggested"
    }


def test_unique_highest_priority_automatic_route_remains_ready() -> None:
    event = AutomationEventInput(
        event_id="event-1",
        event_type="bank_transaction.posted.v1",
        source="erpnext",
        subject_id="transaction-1",
        occurred_at=datetime.now(timezone.utc),
        facts={"transaction": {"direction": "inbound"}},
    )
    rules = [
        AutomationRule(
            id=f"rule-{priority}",
            project_id=f"00000000-0000-0000-0000-00000000000{priority}",
            event_type=event.event_type,
            priority=priority,
            mode=AutomationRuleMode.AUTOMATIC,
            conditions=[
                AutomationCondition(
                    fact="transaction.direction",
                    operator=AutomationConditionOperator.EQUALS,
                    value="inbound",
                )
            ],
            actions=[
                AutomationAction(
                    action_type="project_payment.route",
                    payload={
                        "project_id": f"00000000-0000-0000-0000-00000000000{priority}"
                    },
                )
            ],
        )
        for priority in (20, 10)
    ]
    evaluation = evaluate_automation_rules(event, rules)

    resolved = project_payment_evaluation_for_execution(evaluation)

    assert [proposal.disposition.value for proposal in resolved.action_proposals] == [
        "ready",
        "suggested",
    ]


def _claimed_action_row(*, current_rule_version: int = 1) -> dict[str, object]:
    return {
        "id": "00000000-0000-0000-0000-000000000100",
        "action_type": "project_payment.route",
        "payload": {"project_id": "00000000-0000-0000-0000-000000000001"},
        "action_mode": "automatic",
        "disposition": "ready",
        "approved_by": None,
        "rule_version": 1,
        "rule_project_id": "00000000-0000-0000-0000-000000000001",
        "current_rule_version": current_rule_version,
        "current_rule_mode": "automatic",
        "current_rule_enabled": True,
        "subject_id": "00000000-0000-0000-0000-000000000200",
        "event_subject_revision": "revision-1",
        "subject_snapshot": {
            "source": "erpnext",
            "external_id": "ACC-BTN-0001",
            "source_revision": "revision-1",
            "direction": "inbound",
            "amount": "1250.00",
            "currency": "USD",
        },
    }


def test_atomic_action_blocks_a_superseded_rule_before_money_is_written(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.side_effect = [
        _claimed_action_row(current_rule_version=2),
        {"id": "00000000-0000-0000-0000-000000000100"},
    ]
    _install_cursor(monkeypatch, cursor)

    result = project_payments.apply_project_payment_automation_action(
        project_payments.SharedSettings(),
        action_id="00000000-0000-0000-0000-000000000100",
        lease_token="00000000-0000-0000-0000-000000000300",
    )

    assert result.status.value == "blocked"
    assert result.reason == "rule_version_superseded_or_not_automatic"
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any(
        "INSERT INTO project_payment_allocations" in query for query in queries
    )


def test_atomic_action_blocks_a_superseded_erp_revision_before_money_is_written(
    monkeypatch,
) -> None:
    cursor = Mock()
    cursor.fetchone.side_effect = [
        _claimed_action_row(),
        {
            "id": "00000000-0000-0000-0000-000000000200",
            "source": "erpnext",
            "external_id": "ACC-BTN-0001",
            "source_revision": "revision-2",
            "direction": "inbound",
            "amount": "1250.00",
            "currency": "USD",
        },
        {"id": "00000000-0000-0000-0000-000000000100"},
    ]
    _install_cursor(monkeypatch, cursor)

    result = project_payments.apply_project_payment_automation_action(
        project_payments.SharedSettings(),
        action_id="00000000-0000-0000-0000-000000000100",
        lease_token="00000000-0000-0000-0000-000000000300",
    )

    assert result.status.value == "blocked"
    assert result.reason == "payment_source_revision_superseded"
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("FOR UPDATE OF action, rule" in query for query in queries)
    assert not any(
        "INSERT INTO project_payment_allocations" in query for query in queries
    )
