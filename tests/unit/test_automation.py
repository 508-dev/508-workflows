"""Unit tests for the typed automation rule evaluator."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from five08.automation import (
    AutomationAction,
    AutomationActionDisposition,
    AutomationCondition,
    AutomationConditionOperator,
    AutomationEventInput,
    AutomationRule,
    AutomationRuleMode,
    AutomationRuleOrigin,
    evaluate_automation_rules,
    resolve_fact_path,
)


def _payment_event() -> AutomationEventInput:
    return AutomationEventInput(
        event_id="event-1",
        event_type="bank_transaction.posted.v1",
        source="erpnext",
        subject_id="BT-0001",
        occurred_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
        facts={
            "transaction": {
                "direction": "inbound",
                "amount": "1250.00",
                "description": "Acme Design Sprint payment",
                "counterparty": "Acme Ltd",
            }
        },
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "id": "rule-1",
            "event_type": "bank_transaction.posted.v1",
            "origin": AutomationRuleOrigin.LEARNED,
            "actions": [{"action_type": "project_payment.route"}],
        },
        {
            "id": "rule-1",
            "event_type": "bank_transaction.posted.v1",
            "origin": AutomationRuleOrigin.CONFIGURED,
            "learning_key": "project-payment:v1:abc123",
            "actions": [{"action_type": "project_payment.route"}],
        },
    ],
)
def test_rule_learning_provenance_is_not_optional_or_spoofable(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AutomationRule.model_validate(payload)


def test_evaluator_matches_typed_conditions_in_deterministic_priority_order() -> None:
    event = _payment_event()
    lower_priority = AutomationRule(
        id="rule-z",
        event_type=event.event_type,
        priority=1,
        mode=AutomationRuleMode.SUGGEST,
        conditions=[
            AutomationCondition(
                fact="transaction.counterparty",
                operator=AutomationConditionOperator.EQUALS,
                value="Acme Ltd",
            )
        ],
        actions=[AutomationAction(action_type="project_payment.allocate")],
    )
    higher_priority = AutomationRule(
        id="rule-a",
        event_type=event.event_type,
        priority=10,
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.direction",
                operator=AutomationConditionOperator.EQUALS,
                value="inbound",
            ),
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.GREATER_THAN_OR_EQUAL,
                value="1000",
            ),
            AutomationCondition(
                fact="transaction.description",
                operator=AutomationConditionOperator.CONTAINS,
                value="design sprint",
            ),
        ],
        actions=[AutomationAction(action_type="discord.project_payment.notify")],
    )

    result = evaluate_automation_rules(
        event,
        [lower_priority, higher_priority],
        allowed_fact_paths={
            "transaction.direction",
            "transaction.amount",
            "transaction.description",
            "transaction.counterparty",
        },
        allowed_action_types={
            "project_payment.allocate",
            "discord.project_payment.notify",
        },
    )

    assert [trace.rule_id for trace in result.rules] == ["rule-a", "rule-z"]
    assert [proposal.action.action_type for proposal in result.action_proposals] == [
        "discord.project_payment.notify",
        "project_payment.allocate",
    ]
    assert result.action_proposals[0].disposition is AutomationActionDisposition.READY
    assert (
        result.action_proposals[1].disposition is AutomationActionDisposition.SUGGESTED
    )
    assert result.rules[0].rule_snapshot["conditions"][1]["value"] == "1000"
    assert result.rules[0].rule_snapshot["actions"] == [
        {"action_type": "discord.project_payment.notify", "payload": {}}
    ]


def test_evaluator_fails_closed_for_unregistered_fact_paths_and_actions() -> None:
    event = _payment_event()
    rule = AutomationRule(
        id="rule-1",
        event_type=event.event_type,
        mode=AutomationRuleMode.AUTOMATIC,
        conditions=[
            AutomationCondition(
                fact="transaction.counterparty",
                operator=AutomationConditionOperator.EXISTS,
            )
        ],
        actions=[AutomationAction(action_type="discord.project_payment.notify")],
    )

    result = evaluate_automation_rules(
        event,
        [rule],
        allowed_fact_paths={"transaction.amount"},
        allowed_action_types={"project_payment.allocate"},
    )

    assert result.rules[0].matched is False
    assert result.rules[0].conditions[0].reason == "fact_path_not_allowed"
    assert result.action_proposals == []


def test_evaluator_does_not_treat_missing_values_as_not_equal_matches() -> None:
    event = _payment_event()
    rule = AutomationRule(
        id="rule-1",
        event_type=event.event_type,
        conditions=[
            AutomationCondition(
                fact="transaction.reference_number",
                operator=AutomationConditionOperator.NOT_EQUALS,
                value="ignore-me",
            )
        ],
        actions=[AutomationAction(action_type="project_payment.allocate")],
    )

    result = evaluate_automation_rules(event, [rule])

    assert result.rules[0].matched is False
    assert result.rules[0].conditions[0].reason == "fact_missing"


def test_evaluator_compares_equivalent_finite_json_numbers_for_equality() -> None:
    event = _payment_event()
    rule = AutomationRule(
        id="rule-1",
        event_type=event.event_type,
        conditions=[
            AutomationCondition(
                fact="transaction.amount",
                operator=AutomationConditionOperator.EQUALS,
                value=1250,
            )
        ],
        actions=[AutomationAction(action_type="project_payment.allocate")],
    )

    result = evaluate_automation_rules(event, [rule])

    assert result.rules[0].matched is True


def test_rule_contract_rejects_expressions_and_invalid_numeric_conditions() -> None:
    with pytest.raises(ValidationError, match="dotted lowercase identifier"):
        AutomationCondition(
            fact="transaction.__class__",
            operator=AutomationConditionOperator.EXISTS,
        )

    with pytest.raises(ValidationError, match="finite numeric"):
        AutomationCondition(
            fact="transaction.amount",
            operator=AutomationConditionOperator.GREATER_THAN_OR_EQUAL,
            value="not-a-number",
        )


def test_resolve_fact_path_only_traverses_mappings() -> None:
    assert (
        resolve_fact_path({"transaction": {"amount": "10"}}, "transaction.amount")
        == "10"
    )
    assert resolve_fact_path({"transaction": ["10"]}, "transaction.0") is not None
