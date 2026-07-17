"""Unit tests for the durable automation event/action store SQL contracts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

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
import five08.automation_store as automation_store


class _CursorStub:
    def __init__(self, rows: list[dict | None]) -> None:
        self.rows = list(rows)
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_CursorStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def execute(self, query: str, params: tuple) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict | None:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[dict]:
        rows = [row for row in self.rows if row is not None]
        self.rows.clear()
        return rows


class _ConnectionStub:
    def __init__(self, cursor: _CursorStub) -> None:
        self.cursor_stub = cursor

    def cursor(self, row_factory=None) -> _CursorStub:  # noqa: ARG002
        return self.cursor_stub

    def __enter__(self) -> "_ConnectionStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _install_connection_stub(monkeypatch, cursor: _CursorStub) -> None:
    @contextmanager
    def _connection():
        yield _ConnectionStub(cursor)

    monkeypatch.setattr(
        automation_store, "get_postgres_connection", lambda _: _connection()
    )


def _event() -> AutomationEventInput:
    return AutomationEventInput(
        event_id="event-key",
        event_type="bank_transaction.posted.v1",
        source="erpnext",
        subject_id="transaction-uuid",
        occurred_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        facts={"transaction": {"direction": "inbound"}},
    )


def _rule_row(*, version: int = 1, enabled: bool = True) -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "project_id": "00000000-0000-0000-0000-000000000002",
        "event_type": "bank_transaction.posted.v1",
        "origin": "configured",
        "learning_key": None,
        "priority": 10,
        "mode": "suggest",
        "enabled": enabled,
        "version": version,
        "conditions": [
            {
                "fact": "transaction.direction",
                "operator": "equals",
                "value": "inbound",
            }
        ],
        "actions": [
            {
                "action_type": "project_payment.route",
                "payload": {"project_id": "00000000-0000-0000-0000-000000000002"},
            }
        ],
        "created_by": "discord:steering-1",
    }


def _rule(*, version: int = 1) -> AutomationRule:
    row = _rule_row(version=version)
    row.pop("created_by")
    return AutomationRule.model_validate(row)


def _action_row(
    *,
    status: str,
    review_decision: str | None,
    reviewed_by: str | None,
) -> dict:
    return {
        "id": "00000000-0000-0000-0000-000000000003",
        "event_id": "00000000-0000-0000-0000-000000000004",
        "action_type": "project_payment.route",
        "payload": {"project_id": "00000000-0000-0000-0000-000000000002"},
        "mode": "suggest",
        "disposition": "suggested",
        "status": status,
        "attempts": 0,
        "idempotency_key": "action-key",
        "lease_token": None,
        "approved_by": (reviewed_by if review_decision == "approved" else None),
        "rule_project_id": "00000000-0000-0000-0000-000000000002",
        "review_decision": review_decision,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime(2026, 7, 16, tzinfo=timezone.utc),
    }


def test_upsert_automation_event_returns_created_inbox_row(monkeypatch) -> None:
    cursor = _CursorStub([{"id": "event-uuid"}])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.upsert_automation_event(
        automation_store.SharedSettings(), event=_event(), event_key="event-key"
    )

    assert result == automation_store.StoredAutomationEvent(
        id="event-uuid", created=True
    )
    assert "ON CONFLICT (event_key) DO NOTHING" in cursor.executed[0][0]
    assert cursor.executed[0][1][1] == "event-key"


def test_upsert_automation_event_loads_existing_duplicate(monkeypatch) -> None:
    cursor = _CursorStub([None, {"id": "existing-event"}])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.upsert_automation_event(
        automation_store.SharedSettings(), event=_event(), event_key="event-key"
    )

    assert result == automation_store.StoredAutomationEvent(
        id="existing-event", created=False
    )
    assert "SELECT id FROM automation_events" in cursor.executed[1][0]


def test_persist_automation_evaluation_writes_semantic_action_outbox(
    monkeypatch,
) -> None:
    cursor = _CursorStub([{"id": "evaluation-uuid"}, {"id": "action-uuid"}])
    _install_connection_stub(monkeypatch, cursor)
    event = _event()
    rule = AutomationRule(
        id="00000000-0000-0000-0000-000000000001",
        event_type=event.event_type,
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
                payload={"project_id": "00000000-0000-0000-0000-000000000002"},
            )
        ],
    )
    evaluation = evaluate_automation_rules(event, [rule])

    action_ids = automation_store.persist_automation_evaluation(
        automation_store.SharedSettings(),
        stored_event_id="00000000-0000-0000-0000-000000000003",
        evaluation=evaluation,
    )

    assert action_ids == ["action-uuid"]
    assert "automation_rule_evaluations" in cursor.executed[0][0]
    assert "rule_snapshot" in cursor.executed[0][0]
    assert cursor.executed[0][1][-1].obj["actions"] == [
        {
            "action_type": "project_payment.route",
            "payload": {"project_id": "00000000-0000-0000-0000-000000000002"},
        }
    ]
    assert "automation_actions" in cursor.executed[1][0]
    assert cursor.executed[1][1][7] == "awaiting_review"


def test_payment_recovery_lists_stale_running_actions(monkeypatch) -> None:
    cursor = _CursorStub([{"id": "stale-action"}])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.list_pending_automation_action_ids(
        automation_store.SharedSettings(),
        stale_after_seconds=90,
    )

    assert result == ["stale-action"]
    assert "status = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1][3:5] == ("running", 90)


def test_persist_event_and_evaluation_is_one_atomic_inbox_outbox_transaction(
    monkeypatch,
) -> None:
    cursor = _CursorStub(
        [
            {"id": "event-uuid"},
            {"id": "evaluation-uuid"},
            {"id": "action-uuid"},
        ]
    )
    _install_connection_stub(monkeypatch, cursor)
    event = _event()
    evaluation = evaluate_automation_rules(event, [_rule()])

    stored_event, action_ids = automation_store.persist_automation_event_and_evaluation(
        automation_store.SharedSettings(),
        event=event,
        event_key="event-key",
        evaluation=evaluation,
    )

    assert stored_event == automation_store.StoredAutomationEvent(
        id="event-uuid", created=True
    )
    assert action_ids == ["action-uuid"]
    assert "INSERT INTO automation_events" in cursor.executed[0][0]
    assert "automation_rule_evaluations" in cursor.executed[1][0]
    assert "automation_actions" in cursor.executed[2][0]


def test_create_automation_rule_persists_typed_definition(monkeypatch) -> None:
    cursor = _CursorStub([_rule_row()])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.create_automation_rule(
        automation_store.SharedSettings(),
        rule=_rule(),
        created_by="discord:steering-1",
    )

    assert result.rule.id == "00000000-0000-0000-0000-000000000001"
    assert result.created_by == "discord:steering-1"
    assert "INSERT INTO automation_rules" in cursor.executed[0][0]
    assert cursor.executed[0][1][3] == AutomationRuleOrigin.CONFIGURED.value
    assert cursor.executed[0][1][6] == AutomationRuleMode.SUGGEST.value


def test_update_automation_rule_uses_optimistic_version(monkeypatch) -> None:
    cursor = _CursorStub([_rule_row(version=2)])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.update_automation_rule(
        automation_store.SharedSettings(),
        rule=_rule(version=1),
        expected_version=1,
    )

    assert result is not None
    assert result.rule.version == 2
    assert "AND version = %s" in cursor.executed[0][0]
    assert "AND origin = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1][-2:] == (1, AutomationRuleOrigin.CONFIGURED.value)


def test_create_learned_automation_rule_loads_an_equivalent_existing_rule(
    monkeypatch,
) -> None:
    row = _rule_row()
    row.update(
        {
            "origin": "learned",
            "learning_key": "project-payment:v1:abc123",
            "priority": -100,
        }
    )
    rule = AutomationRule.model_validate(
        {key: value for key, value in row.items() if key != "created_by"}
    )
    cursor = _CursorStub([None, row])
    _install_connection_stub(monkeypatch, cursor)

    stored_rule, created = automation_store.create_automation_rule_if_absent(
        automation_store.SharedSettings(),
        rule=rule,
        created_by="system:project-payment-learning:action-1",
    )

    assert created is False
    assert stored_rule.rule == rule
    assert "ON CONFLICT DO NOTHING" in cursor.executed[0][0]
    assert "learning_key" in cursor.executed[1][0]


def test_update_refuses_to_rewrite_a_learned_rule(monkeypatch) -> None:
    cursor = _CursorStub([None])
    _install_connection_stub(monkeypatch, cursor)
    row = _rule_row()
    row.update({"origin": "learned", "learning_key": "project-payment:v1:abc123"})
    learned_rule = AutomationRule.model_validate(
        {key: value for key, value in row.items() if key != "created_by"}
    )

    result = automation_store.update_automation_rule(
        automation_store.SharedSettings(),
        rule=learned_rule,
        expected_version=1,
    )

    assert result is None
    assert "AND origin = %s" in cursor.executed[0][0]
    assert cursor.executed[0][1][-1] == AutomationRuleOrigin.CONFIGURED.value


def test_disable_automation_rule_preserves_history_with_new_version(
    monkeypatch,
) -> None:
    cursor = _CursorStub([_rule_row(version=2, enabled=False)])
    _install_connection_stub(monkeypatch, cursor)

    result = automation_store.disable_automation_rule(
        automation_store.SharedSettings(),
        rule_id="00000000-0000-0000-0000-000000000001",
        expected_version=1,
    )

    assert result is not None
    assert result.rule.enabled is False
    assert "SET enabled = FALSE" in cursor.executed[0][0]


def test_approve_automation_action_records_typed_review_feedback(monkeypatch) -> None:
    cursor = _CursorStub(
        [
            _action_row(
                status="approved",
                review_decision="approved",
                reviewed_by="discord:admin-1",
            )
        ]
    )
    _install_connection_stub(monkeypatch, cursor)

    action = automation_store.approve_automation_action(
        automation_store.SharedSettings(),
        action_id="00000000-0000-0000-0000-000000000003",
        approved_by="discord:admin-1",
    )

    assert action is not None
    assert action.review_decision is automation_store.AutomationReviewDecision.APPROVED
    assert action.reviewed_by == "discord:admin-1"
    query, params = cursor.executed[0]
    assert "review_decision = %s" in query
    assert "reviewed_by = %s" in query
    assert params[:4] == (
        "approved",
        "discord:admin-1",
        "approved",
        "discord:admin-1",
    )


def test_reject_automation_action_records_typed_review_feedback(monkeypatch) -> None:
    cursor = _CursorStub(
        [
            _action_row(
                status="dead",
                review_decision="rejected",
                reviewed_by="discord:admin-1",
            )
        ]
    )
    _install_connection_stub(monkeypatch, cursor)

    action = automation_store.reject_automation_action(
        automation_store.SharedSettings(),
        action_id="00000000-0000-0000-0000-000000000003",
        rejected_by="discord:admin-1",
    )

    assert action is not None
    assert action.review_decision is automation_store.AutomationReviewDecision.REJECTED
    assert action.reviewed_by == "discord:admin-1"
    query, params = cursor.executed[0]
    assert "review_decision = %s" in query
    assert "AND disposition = %s" in query
    assert params[:4] == (
        "dead",
        "rejected",
        "discord:admin-1",
        "rejected_by:discord:admin-1",
    )
    assert params[-1] == "suggested"


def test_list_approved_review_actions_returns_immutable_learning_evidence(
    monkeypatch,
) -> None:
    row = _action_row(
        status="approved",
        review_decision="approved",
        reviewed_by="discord:admin-1",
    )
    row.update(
        {
            "subject_id": "transaction-uuid",
            "subject_snapshot": {
                "direction": "inbound",
                "currency": "GBP",
                "counterparty": "Acme Ltd",
            },
        }
    )
    cursor = _CursorStub([row])
    _install_connection_stub(monkeypatch, cursor)

    reviews = automation_store.list_approved_automation_review_actions(
        automation_store.SharedSettings(),
        event_type="bank_transaction.posted.v1",
        action_type="project_payment.route",
    )

    assert len(reviews) == 1
    assert reviews[0].subject_snapshot["counterparty"] == "Acme Ltd"
    query, params = cursor.executed[0]
    assert "action.review_decision = %s" in query
    assert params[:4] == (
        "bank_transaction.posted.v1",
        "project_payment.route",
        "suggested",
        "approved",
    )
