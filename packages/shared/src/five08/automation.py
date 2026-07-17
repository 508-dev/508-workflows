"""Typed, deterministic rule evaluation for operational automations.

The module deliberately evaluates only declarative facts, operators, and action
types.  It never executes an action itself.  Callers persist the evaluation and
hand only registered action types to a separate, policy-aware executor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_FACT_PATH_COMPONENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_ACTION_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_MISSING = object()


class AutomationRuleMode(StrEnum):
    """The strongest side-effect level a rule is allowed to request."""

    OBSERVE = "observe"
    SUGGEST = "suggest"
    AUTOMATIC = "automatic"


class AutomationRuleOrigin(StrEnum):
    """Whether an operator configured a rule or feedback derived it."""

    CONFIGURED = "configured"
    LEARNED = "learned"


class AutomationConditionOperator(StrEnum):
    """Small, auditable set of supported fact predicates."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    IN = "in"
    EXISTS = "exists"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"


class AutomationActionDisposition(StrEnum):
    """How an action proposal must be treated by its executor."""

    OBSERVED = "observed"
    SUGGESTED = "suggested"
    READY = "ready"


class AutomationReviewDecision(StrEnum):
    """A human verdict retained as labeled feedback for future suggestions."""

    APPROVED = "approved"
    REJECTED = "rejected"


class AutomationEventInput(BaseModel):
    """Normalized event facts presented to the deterministic rule engine."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    source: str
    subject_id: str
    occurred_at: datetime
    facts: dict[str, Any] = Field(default_factory=dict)
    # ``subject_snapshot`` is executor-only evidence captured when the event is
    # accepted. Rules remain constrained to ``facts`` by their allowlist, so a
    # future snapshot field cannot silently become a rule input.
    subject_revision: str | None = None
    subject_snapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "event_type", "source", "subject_id")
    @classmethod
    def _require_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class AutomationCondition(BaseModel):
    """One typed predicate evaluated against a dotted path in event facts."""

    model_config = ConfigDict(extra="forbid")

    fact: str
    operator: AutomationConditionOperator
    value: Any = None

    @field_validator("fact")
    @classmethod
    def _validate_fact_path(cls, value: str) -> str:
        normalized = value.strip()
        parts = normalized.split(".")
        if not normalized or any(
            not _FACT_PATH_COMPONENT_PATTERN.fullmatch(part) for part in parts
        ):
            raise ValueError("fact must be a dotted lowercase identifier path")
        return normalized

    @model_validator(mode="after")
    def _validate_value_for_operator(self) -> "AutomationCondition":
        if self.operator is AutomationConditionOperator.IN and not isinstance(
            self.value, (list, tuple, set, frozenset)
        ):
            raise ValueError("in conditions require a list-like value")
        if (
            self.operator
            in {
                AutomationConditionOperator.GREATER_THAN_OR_EQUAL,
                AutomationConditionOperator.LESS_THAN_OR_EQUAL,
            }
            and _decimal_or_none(self.value) is None
        ):
            raise ValueError("numeric conditions require a finite numeric value")
        return self


class AutomationAction(BaseModel):
    """A declarative request that a separately registered executor may handle."""

    model_config = ConfigDict(extra="forbid")

    action_type: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_type")
    @classmethod
    def _validate_action_type(cls, value: str) -> str:
        normalized = value.strip()
        if not _ACTION_TYPE_PATTERN.fullmatch(normalized):
            raise ValueError("action_type must be a stable lowercase action identifier")
        return normalized


class AutomationRule(BaseModel):
    """Versioned declarative rule that can produce one or more action proposals."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str | None = None
    event_type: str
    origin: AutomationRuleOrigin = AutomationRuleOrigin.CONFIGURED
    # A non-sensitive, deterministic fingerprint lets feedback-derived rules
    # be created idempotently without making source transaction text part of a
    # public identifier.
    learning_key: str | None = Field(default=None, max_length=200)
    priority: int = 0
    mode: AutomationRuleMode = AutomationRuleMode.SUGGEST
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    conditions: list[AutomationCondition] = Field(default_factory=list)
    actions: list[AutomationAction] = Field(min_length=1)

    @field_validator("id", "event_type")
    @classmethod
    def _require_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @model_validator(mode="after")
    def _validate_learning_provenance(self) -> "AutomationRule":
        learning_key = (self.learning_key or "").strip() or None
        if self.origin is AutomationRuleOrigin.LEARNED and learning_key is None:
            raise ValueError("learned rules require a learning_key")
        if self.origin is AutomationRuleOrigin.CONFIGURED and learning_key is not None:
            raise ValueError("configured rules cannot have a learning_key")
        self.learning_key = learning_key
        return self


class ConditionEvaluation(BaseModel):
    """Audit-friendly trace of a single predicate evaluation."""

    model_config = ConfigDict(extra="forbid")

    fact: str
    operator: AutomationConditionOperator
    matched: bool
    reason: str | None = None


class MatchedAutomationRule(BaseModel):
    """One evaluated rule and its condition trace."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule_version: int
    project_id: str | None = None
    priority: int
    mode: AutomationRuleMode
    matched: bool
    conditions: list[ConditionEvaluation]
    # This preserves the exact predicate/action definition that was evaluated.
    # ``automation_rules`` is intentionally mutable/versioned for operators,
    # while payment audit evidence must remain reconstructable after edits.
    rule_snapshot: dict[str, Any] = Field(default_factory=dict)


class AutomationActionProposal(BaseModel):
    """An action proposal resulting from a matched rule."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    rule_id: str
    rule_version: int
    rule_project_id: str | None = None
    rule_priority: int
    mode: AutomationRuleMode
    disposition: AutomationActionDisposition
    action: AutomationAction


class AutomationEvaluation(BaseModel):
    """Complete deterministic output for one event/rule-set evaluation."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    rules: list[MatchedAutomationRule]
    action_proposals: list[AutomationActionProposal]


def _decimal_or_none(value: Any) -> Decimal | None:
    """Safely coerce JSON-compatible values to a finite Decimal."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, (int, float, str)):
        try:
            candidate = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    return candidate if candidate.is_finite() else None


def resolve_fact_path(facts: Mapping[str, Any], path: str) -> Any:
    """Resolve a safe dotted fact path from mappings only.

    Object attributes, indexes, and expressions are intentionally unsupported.
    They would make a rule definition executable rather than declarative.
    """
    current: Any = facts
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return _MISSING
        current = current[component]
    return current


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return expected.casefold() in actual.casefold()
    if isinstance(actual, Mapping):
        return expected in actual
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)):
        return expected in actual
    return False


def _condition_matches(condition: AutomationCondition, actual: Any) -> bool:
    if condition.operator is AutomationConditionOperator.EXISTS:
        expected = True if condition.value is None else bool(condition.value)
        exists = actual is not _MISSING and actual is not None
        return exists is expected
    if actual is _MISSING:
        return False
    if condition.operator is AutomationConditionOperator.EQUALS:
        return _values_equal(actual, condition.value)
    if condition.operator is AutomationConditionOperator.NOT_EQUALS:
        return not _values_equal(actual, condition.value)
    if condition.operator is AutomationConditionOperator.CONTAINS:
        return _contains(actual, condition.value)
    if condition.operator is AutomationConditionOperator.IN:
        expected_values = condition.value
        if not isinstance(expected_values, (list, tuple, set, frozenset)):
            return False
        if isinstance(actual, Sequence) and not isinstance(
            actual, (str, bytes, bytearray)
        ):
            return any(item in expected_values for item in actual)
        return actual in expected_values
    actual_number = _decimal_or_none(actual)
    expected_number = _decimal_or_none(condition.value)
    if actual_number is None or expected_number is None:
        return False
    if condition.operator is AutomationConditionOperator.GREATER_THAN_OR_EQUAL:
        return actual_number >= expected_number
    if condition.operator is AutomationConditionOperator.LESS_THAN_OR_EQUAL:
        return actual_number <= expected_number
    return False


def _values_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values while accepting equivalent finite numeric values.

    Rule payloads arrive through JSON, so an operator may enter ``1250`` while
    a bank fact is stored as the canonical decimal string ``"1250.00"``. Keep
    nonnumeric comparisons exact, but compare two finite numeric values as
    ``Decimal`` so the typed rule contract is not accidentally format-bound.
    """
    actual_number = _decimal_or_none(actual)
    expected_number = _decimal_or_none(expected)
    if actual_number is not None and expected_number is not None:
        return actual_number == expected_number
    return actual == expected


def _disposition_for_mode(mode: AutomationRuleMode) -> AutomationActionDisposition:
    if mode is AutomationRuleMode.AUTOMATIC:
        return AutomationActionDisposition.READY
    if mode is AutomationRuleMode.SUGGEST:
        return AutomationActionDisposition.SUGGESTED
    return AutomationActionDisposition.OBSERVED


def evaluate_automation_rules(
    event: AutomationEventInput,
    rules: Sequence[AutomationRule],
    *,
    allowed_fact_paths: set[str] | frozenset[str] | None = None,
    allowed_action_types: set[str] | frozenset[str] | None = None,
) -> AutomationEvaluation:
    """Evaluate applicable rules without executing any actions.

    The optional allowlists are a policy boundary owned by the caller.  An
    unregistered fact path or action type fails closed rather than making a
    future event schema change silently alter an existing automation.
    """
    traces: list[MatchedAutomationRule] = []
    proposals: list[AutomationActionProposal] = []
    applicable_rules = sorted(
        (
            rule
            for rule in rules
            if rule.enabled and rule.event_type == event.event_type
        ),
        key=lambda rule: (-rule.priority, rule.id),
    )

    for rule in applicable_rules:
        condition_traces: list[ConditionEvaluation] = []
        for condition in rule.conditions:
            if (
                allowed_fact_paths is not None
                and condition.fact not in allowed_fact_paths
            ):
                condition_traces.append(
                    ConditionEvaluation(
                        fact=condition.fact,
                        operator=condition.operator,
                        matched=False,
                        reason="fact_path_not_allowed",
                    )
                )
                continue
            actual = resolve_fact_path(event.facts, condition.fact)
            matched = _condition_matches(condition, actual)
            condition_traces.append(
                ConditionEvaluation(
                    fact=condition.fact,
                    operator=condition.operator,
                    matched=matched,
                    reason=("fact_missing" if actual is _MISSING else None),
                )
            )

        matched_rule = all(trace.matched for trace in condition_traces)
        traces.append(
            MatchedAutomationRule(
                rule_id=rule.id,
                rule_version=rule.version,
                project_id=rule.project_id,
                priority=rule.priority,
                mode=rule.mode,
                matched=matched_rule,
                conditions=condition_traces,
                rule_snapshot=rule.model_dump(mode="json"),
            )
        )
        if not matched_rule:
            continue

        for action in rule.actions:
            if (
                allowed_action_types is not None
                and action.action_type not in allowed_action_types
            ):
                continue
            proposals.append(
                AutomationActionProposal(
                    event_id=event.event_id,
                    rule_id=rule.id,
                    rule_version=rule.version,
                    rule_project_id=rule.project_id,
                    rule_priority=rule.priority,
                    mode=rule.mode,
                    disposition=_disposition_for_mode(rule.mode),
                    action=action,
                )
            )

    return AutomationEvaluation(
        event_id=event.event_id,
        event_type=event.event_type,
        rules=traces,
        action_proposals=proposals,
    )
