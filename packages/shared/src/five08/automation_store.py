"""Durable storage for typed automation events, rules, and action proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.automation import (
    AutomationActionDisposition,
    AutomationEvaluation,
    AutomationEventInput,
    AutomationReviewDecision,
    AutomationRule,
    AutomationRuleOrigin,
)
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class AutomationActionStatus(StrEnum):
    """Lifecycle states for an action proposal's durable side effect."""

    PENDING = "pending"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


_AUTOMATION_ACTION_MAX_ATTEMPTS = 8
_AUTOMATION_ACTION_RETRY_BASE_SECONDS = 15
_AUTOMATION_ACTION_RETRY_MAX_SECONDS = 3600


@dataclass(frozen=True)
class StoredAutomationEvent:
    """Persistent event inbox row."""

    id: str
    created: bool


@dataclass(frozen=True)
class StoredAutomationAction:
    """One action proposal loaded from the durable semantic outbox."""

    id: str
    event_id: str
    action_type: str
    payload: dict[str, Any]
    mode: str
    disposition: str
    status: AutomationActionStatus
    attempts: int
    idempotency_key: str
    lease_token: str | None
    approved_by: str | None
    rule_project_id: str | None
    review_decision: AutomationReviewDecision | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass(frozen=True)
class StoredAutomationReviewAction:
    """A human-reviewable action with its immutable payment event evidence."""

    action: StoredAutomationAction
    subject_id: str
    subject_snapshot: dict[str, Any]


@dataclass(frozen=True)
class StoredAutomationRule:
    """One dashboard-manageable rule plus its immutable creator attribution."""

    rule: AutomationRule
    created_by: str | None


def _as_stored_review_action(row: dict[str, Any]) -> StoredAutomationReviewAction:
    """Decode a review row while preserving only immutable subject evidence."""
    snapshot = row.get("subject_snapshot")
    return StoredAutomationReviewAction(
        action=_as_stored_action(row),
        subject_id=str(row["subject_id"]),
        subject_snapshot=dict(snapshot) if isinstance(snapshot, dict) else {},
    )


def _as_action_status(value: object) -> AutomationActionStatus:
    try:
        return AutomationActionStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Unknown automation action status: {value!r}") from exc


def _as_review_decision(value: object) -> AutomationReviewDecision | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return AutomationReviewDecision(normalized)
    except ValueError as exc:
        raise ValueError(f"Unknown automation review decision: {value!r}") from exc


def _as_stored_action(row: dict[str, Any]) -> StoredAutomationAction:
    payload = row.get("payload")
    return StoredAutomationAction(
        id=str(row["id"]),
        event_id=str(row["event_id"]),
        action_type=str(row["action_type"]),
        payload=dict(payload) if isinstance(payload, dict) else {},
        mode=str(row["mode"]),
        disposition=str(row["disposition"]),
        status=_as_action_status(row["status"]),
        attempts=int(row["attempts"]),
        idempotency_key=str(row["idempotency_key"]),
        lease_token=str(row.get("lease_token") or "").strip() or None,
        approved_by=str(row.get("approved_by") or "").strip() or None,
        rule_project_id=(
            str(row["rule_project_id"])
            if row.get("rule_project_id") is not None
            else None
        ),
        review_decision=_as_review_decision(row.get("review_decision")),
        reviewed_by=str(row.get("reviewed_by") or "").strip() or None,
        reviewed_at=(
            row.get("reviewed_at")
            if isinstance(row.get("reviewed_at"), datetime)
            else None
        ),
    )


def _as_stored_rule(row: dict[str, Any]) -> StoredAutomationRule:
    """Decode a rule row through the same strict Pydantic contract as runtime."""
    conditions = row.get("conditions")
    actions = row.get("actions")
    rule = AutomationRule.model_validate(
        {
            "id": str(row["id"]),
            "project_id": row.get("project_id"),
            "event_type": row["event_type"],
            "origin": row.get("origin") or AutomationRuleOrigin.CONFIGURED.value,
            "learning_key": row.get("learning_key"),
            "priority": row["priority"],
            "mode": row["mode"],
            "enabled": row["enabled"],
            "version": row["version"],
            "conditions": conditions if isinstance(conditions, list) else [],
            "actions": actions if isinstance(actions, list) else [],
        }
    )
    created_by = str(row.get("created_by") or "").strip() or None
    return StoredAutomationRule(rule=rule, created_by=created_by)


def _rule_database_values(rule: AutomationRule) -> tuple[Any, ...]:
    """Normalize a typed rule into the JSON/UUID values expected by Postgres."""
    try:
        rule_id = str(UUID(rule.id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("rule id must be a UUID") from exc
    project_id: str | None = None
    if rule.project_id is not None:
        try:
            project_id = str(UUID(rule.project_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("project_id must be a UUID") from exc
    return (
        rule_id,
        project_id,
        rule.event_type,
        rule.origin.value,
        rule.learning_key,
        rule.priority,
        rule.mode.value,
        rule.enabled,
        rule.version,
        Jsonb([condition.model_dump(mode="json") for condition in rule.conditions]),
        Jsonb([action.model_dump(mode="json") for action in rule.actions]),
    )


def _insert_automation_event(
    cursor: Any,
    *,
    event: AutomationEventInput,
    event_key: str,
) -> StoredAutomationEvent:
    """Insert/load an inbox event using the caller's active transaction."""
    normalized_key = event_key.strip()
    if not normalized_key:
        raise ValueError("event_key is required")
    event_id = str(uuid4())
    cursor.execute(
        """
        INSERT INTO automation_events (
            id,
            event_key,
            event_type,
            source,
            subject_id,
            subject_revision,
            subject_snapshot,
            occurred_at,
            facts
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (event_key) DO NOTHING
        RETURNING id
        """,
        (
            event_id,
            normalized_key,
            event.event_type,
            event.source,
            event.subject_id,
            event.subject_revision,
            Jsonb(event.subject_snapshot),
            event.occurred_at,
            Jsonb(event.facts),
        ),
    )
    row = cursor.fetchone()
    if row is not None:
        return StoredAutomationEvent(id=str(row["id"]), created=True)
    cursor.execute(
        "SELECT id FROM automation_events WHERE event_key = %s",
        (normalized_key,),
    )
    existing = cursor.fetchone()
    if existing is None:
        raise RuntimeError("Unable to load duplicate automation event")
    return StoredAutomationEvent(id=str(existing["id"]), created=False)


def upsert_automation_event(
    settings: SharedSettings,
    *,
    event: AutomationEventInput,
    event_key: str,
) -> StoredAutomationEvent:
    """Write an event inbox row exactly once for a caller-defined dedupe key."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            return _insert_automation_event(cursor, event=event, event_key=event_key)


def list_enabled_automation_rules(
    settings: SharedSettings,
    *,
    event_type: str,
) -> list[AutomationRule]:
    """Load enabled, versioned rules for one event type in evaluation order."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, event_type, origin, learning_key, priority, mode,
                       enabled, version, project_id::text, conditions, actions, created_by
                FROM automation_rules
                WHERE event_type = %s AND enabled IS TRUE
                ORDER BY priority DESC, id ASC
                """,
                (event_type,),
            )
            rows = cursor.fetchall()
    return [_as_stored_rule(row).rule for row in rows]


def list_automation_rules(
    settings: SharedSettings,
    *,
    event_type: str | None = None,
    project_id: str | None = None,
) -> list[StoredAutomationRule]:
    """List persisted rules for the protected operator configuration surface."""
    normalized_event_type = str(event_type or "").strip() or None
    normalized_project_id: str | None = None
    if project_id is not None:
        try:
            normalized_project_id = str(UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("project_id must be a UUID") from exc
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, event_type, origin, learning_key, priority, mode,
                       enabled, version, project_id::text, conditions, actions, created_by
                FROM automation_rules
                WHERE (%s::text IS NULL OR event_type = %s)
                  AND (%s::uuid IS NULL OR project_id = %s::uuid)
                ORDER BY priority DESC, id ASC
                """,
                (
                    normalized_event_type,
                    normalized_event_type,
                    normalized_project_id,
                    normalized_project_id,
                ),
            )
            rows = cursor.fetchall()
    return [_as_stored_rule(row) for row in rows]


def create_automation_rule(
    settings: SharedSettings,
    *,
    rule: AutomationRule,
    created_by: str | None,
) -> StoredAutomationRule:
    """Persist a new versioned typed rule after API-side policy validation."""
    (
        rule_id,
        project_id,
        event_type,
        origin,
        learning_key,
        priority,
        mode,
        enabled,
        version,
        conditions,
        actions,
    ) = _rule_database_values(rule)
    normalized_created_by = str(created_by or "").strip() or None
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO automation_rules (
                    id,
                    project_id,
                    event_type,
                    origin,
                    learning_key,
                    priority,
                    mode,
                    enabled,
                    version,
                    conditions,
                    actions,
                    created_by
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING id::text, event_type, origin, learning_key, priority,
                          mode, enabled, version, project_id::text, conditions,
                          actions, created_by
                """,
                (
                    rule_id,
                    project_id,
                    event_type,
                    origin,
                    learning_key,
                    priority,
                    mode,
                    enabled,
                    version,
                    conditions,
                    actions,
                    normalized_created_by,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Unable to create automation rule")
    return _as_stored_rule(row)


def create_automation_rule_if_absent(
    settings: SharedSettings,
    *,
    rule: AutomationRule,
    created_by: str | None,
) -> tuple[StoredAutomationRule, bool]:
    """Create a feedback-derived rule once, or return its exact prior copy.

    The caller supplies a deterministic rule id and opaque learning key.  Both
    have unique constraints, so separate approval requests can safely race
    without creating duplicate future suggestions.
    """
    if rule.origin is not AutomationRuleOrigin.LEARNED:
        raise ValueError("only learned rules may be created idempotently")
    (
        rule_id,
        project_id,
        event_type,
        origin,
        learning_key,
        priority,
        mode,
        enabled,
        version,
        conditions,
        actions,
    ) = _rule_database_values(rule)
    normalized_created_by = str(created_by or "").strip() or None
    existing_row: dict[str, Any] | None = None
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO automation_rules (
                    id,
                    project_id,
                    event_type,
                    origin,
                    learning_key,
                    priority,
                    mode,
                    enabled,
                    version,
                    conditions,
                    actions,
                    created_by
                ) VALUES (
                    %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                RETURNING id::text, event_type, origin, learning_key, priority,
                          mode, enabled, version, project_id::text, conditions,
                          actions, created_by
                """,
                (
                    rule_id,
                    project_id,
                    event_type,
                    origin,
                    learning_key,
                    priority,
                    mode,
                    enabled,
                    version,
                    conditions,
                    actions,
                    normalized_created_by,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return _as_stored_rule(row), True
            cursor.execute(
                """
                SELECT id::text, event_type, origin, learning_key, priority, mode,
                       enabled, version, project_id::text, conditions, actions,
                       created_by
                FROM automation_rules
                WHERE id = %s::uuid OR learning_key = %s
                ORDER BY (id = %s::uuid) DESC
                LIMIT 1
                """,
                (rule_id, learning_key, rule_id),
            )
            existing_row = cursor.fetchone()
    if existing_row is None:
        raise RuntimeError("Unable to load duplicate automation rule")
    stored_rule = _as_stored_rule(existing_row)
    if stored_rule.rule != rule:
        raise ValueError("automation_rule_id_or_learning_key_conflict")
    return stored_rule, False


def update_automation_rule(
    settings: SharedSettings,
    *,
    rule: AutomationRule,
    expected_version: int,
) -> StoredAutomationRule | None:
    """Replace an operator-configured rule only when its version is current.

    Learned rules are intentionally immutable apart from disablement: editing
    their predicate would erase the exact feedback-derived provenance while
    making it appear that a human configured the result.
    """
    if expected_version < 1:
        raise ValueError("expected_version must be at least 1")
    (
        rule_id,
        project_id,
        event_type,
        _origin,
        _learning_key,
        priority,
        mode,
        enabled,
        _version,
        conditions,
        actions,
    ) = _rule_database_values(rule)
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE automation_rules
                SET project_id = %s::uuid,
                    event_type = %s,
                    priority = %s,
                    mode = %s,
                    enabled = %s,
                    version = version + 1,
                    conditions = %s,
                    actions = %s,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND version = %s
                  AND origin = %s
                RETURNING id::text, event_type, origin, learning_key, priority,
                          mode, enabled, version, project_id::text, conditions,
                          actions, created_by
                """,
                (
                    project_id,
                    event_type,
                    priority,
                    mode,
                    enabled,
                    conditions,
                    actions,
                    rule_id,
                    expected_version,
                    AutomationRuleOrigin.CONFIGURED.value,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                _invalidate_pending_automatic_actions(
                    cursor,
                    rule_id=rule_id,
                    reason="rule_version_superseded",
                )
    return _as_stored_rule(row) if row is not None else None


def disable_automation_rule(
    settings: SharedSettings,
    *,
    rule_id: str,
    expected_version: int,
) -> StoredAutomationRule | None:
    """Soft-disable a rule while preserving its evaluation/audit history."""
    if expected_version < 1:
        raise ValueError("expected_version must be at least 1")
    try:
        normalized_rule_id = str(UUID(rule_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("rule_id must be a UUID") from exc
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE automation_rules
                SET enabled = FALSE,
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND version = %s
                RETURNING id::text, event_type, origin, learning_key, priority,
                          mode, enabled, version, project_id::text, conditions,
                          actions, created_by
                """,
                (normalized_rule_id, expected_version),
            )
            row = cursor.fetchone()
            if row is not None:
                _invalidate_pending_automatic_actions(
                    cursor,
                    rule_id=normalized_rule_id,
                    reason="rule_disabled",
                )
    return _as_stored_rule(row) if row is not None else None


def _invalidate_pending_automatic_actions(
    cursor: Any,
    *,
    rule_id: str,
    reason: str,
) -> None:
    """Fence queued automatic side effects after an operator changes a rule."""
    cursor.execute(
        """
        UPDATE automation_actions action
        SET status = %s,
            last_error = %s,
            locked_at = NULL,
            lease_token = NULL,
            next_attempt_at = NULL,
            updated_at = NOW()
        FROM automation_rule_evaluations evaluation
        WHERE action.rule_evaluation_id = evaluation.id
          AND evaluation.rule_id = %s::uuid
          AND action.mode = 'automatic'
          AND action.status IN (%s, %s)
        """,
        (
            AutomationActionStatus.DEAD.value,
            reason,
            rule_id,
            AutomationActionStatus.PENDING.value,
            AutomationActionStatus.FAILED.value,
        ),
    )


def _persist_automation_evaluation(
    cursor: Any,
    *,
    stored_event_id: str,
    evaluation: AutomationEvaluation,
) -> list[str]:
    """Persist a fully evaluated event through the caller's transaction."""
    action_ids: list[str] = []
    proposals_by_rule: dict[tuple[str, int], list[tuple[int, Any]]] = {}
    for index, proposal in enumerate(evaluation.action_proposals):
        proposals_by_rule.setdefault(
            (proposal.rule_id, proposal.rule_version), []
        ).append((index, proposal))

    for trace in evaluation.rules:
        evaluation_id = str(uuid4())
        cursor.execute(
            """
            INSERT INTO automation_rule_evaluations (
                id,
                event_id,
                rule_id,
                rule_version,
                rule_project_id,
                matched,
                condition_trace,
                rule_snapshot
            ) VALUES (%s, %s::uuid, %s::uuid, %s, %s::uuid, %s, %s, %s)
            ON CONFLICT (event_id, rule_id, rule_version) DO UPDATE
            SET matched = EXCLUDED.matched,
                condition_trace = EXCLUDED.condition_trace,
                rule_snapshot = EXCLUDED.rule_snapshot,
                evaluated_at = NOW()
            RETURNING id
            """,
            (
                evaluation_id,
                stored_event_id,
                trace.rule_id,
                trace.rule_version,
                trace.project_id,
                trace.matched,
                Jsonb(
                    [
                        condition.model_dump(mode="json")
                        for condition in trace.conditions
                    ]
                ),
                Jsonb(trace.rule_snapshot),
            ),
        )
        evaluation_row = cursor.fetchone()
        if evaluation_row is None:
            raise RuntimeError("Unable to persist automation rule evaluation")
        persisted_evaluation_id = str(evaluation_row["id"])
        for position, proposal in proposals_by_rule.get(
            (trace.rule_id, trace.rule_version), []
        ):
            action_id = str(uuid4())
            idempotency_key = (
                f"automation:v1:{stored_event_id}:{trace.rule_id}:"
                f"{trace.rule_version}:{position}"
            )
            initial_status = (
                AutomationActionStatus.PENDING
                if proposal.disposition is AutomationActionDisposition.READY
                else (
                    AutomationActionStatus.AWAITING_REVIEW
                    if proposal.disposition is AutomationActionDisposition.SUGGESTED
                    else AutomationActionStatus.DEAD
                )
            )
            cursor.execute(
                """
                INSERT INTO automation_actions (
                    id,
                    event_id,
                    rule_evaluation_id,
                    action_type,
                    payload,
                    mode,
                    disposition,
                    status,
                    idempotency_key
                ) VALUES (%s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (
                    action_id,
                    stored_event_id,
                    persisted_evaluation_id,
                    proposal.action.action_type,
                    Jsonb(proposal.action.payload),
                    proposal.mode.value,
                    proposal.disposition.value,
                    initial_status.value,
                    idempotency_key,
                ),
            )
            action_row = cursor.fetchone()
            if action_row is not None:
                action_ids.append(str(action_row["id"]))
    return action_ids


def persist_automation_evaluation(
    settings: SharedSettings,
    *,
    stored_event_id: str,
    evaluation: AutomationEvaluation,
) -> list[str]:
    """Persist rule traces and action proposals in one database transaction."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            return _persist_automation_evaluation(
                cursor,
                stored_event_id=stored_event_id,
                evaluation=evaluation,
            )


def persist_automation_event_and_evaluation(
    settings: SharedSettings,
    *,
    event: AutomationEventInput,
    event_key: str,
    evaluation: AutomationEvaluation,
) -> tuple[StoredAutomationEvent, list[str]]:
    """Atomically persist a new inbox event with its semantic action outbox.

    A process crash cannot leave a deduped event that has no evaluation: the
    event row, traces, and proposed actions commit together or roll back
    together. Duplicate event keys simply return the existing event and let the
    caller ask for genuinely retryable actions.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            stored_event = _insert_automation_event(
                cursor,
                event=event,
                event_key=event_key,
            )
            if not stored_event.created:
                return stored_event, []
            action_ids = _persist_automation_evaluation(
                cursor,
                stored_event_id=stored_event.id,
                evaluation=evaluation,
            )
            return stored_event, action_ids


def get_automation_action(
    settings: SharedSettings,
    *,
    action_id: str,
) -> StoredAutomationAction | None:
    """Load a single semantic action from the durable outbox."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT action.id::text, action.event_id::text, action.action_type,
                       action.payload, action.mode, action.disposition,
                       action.status, action.attempts, action.idempotency_key,
                       action.lease_token::text, action.approved_by,
                       action.review_decision, action.reviewed_by, action.reviewed_at,
                       evaluation.rule_project_id::text
                FROM automation_actions action
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                WHERE action.id = %s::uuid
                """,
                (action_id,),
            )
            row = cursor.fetchone()
    return _as_stored_action(row) if row is not None else None


def claim_automation_action(
    settings: SharedSettings,
    *,
    action_id: str,
    stale_after_seconds: int = 300,
) -> StoredAutomationAction | None:
    """Atomically claim due work with an ownership token for finalization."""
    lease_token = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = %s,
                    attempts = attempts + 1,
                    locked_at = NOW(),
                    lease_token = %s::uuid,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND (
                    status IN (%s, %s)
                    OR (
                        status = %s
                        AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                    )
                    OR (
                        status = %s
                        AND locked_at < NOW() - (%s * INTERVAL '1 second')
                    )
                  )
                RETURNING id::text, event_id::text, action_type, payload, mode,
                          disposition, status, attempts, idempotency_key,
                          lease_token::text, approved_by,
                          review_decision, reviewed_by, reviewed_at,
                          (
                              SELECT rule_project_id::text
                              FROM automation_rule_evaluations
                              WHERE id = automation_actions.rule_evaluation_id
                          ) AS rule_project_id
                """,
                (
                    AutomationActionStatus.RUNNING.value,
                    lease_token,
                    action_id,
                    AutomationActionStatus.PENDING.value,
                    AutomationActionStatus.APPROVED.value,
                    AutomationActionStatus.FAILED.value,
                    AutomationActionStatus.RUNNING.value,
                    max(1, stale_after_seconds),
                ),
            )
            row = cursor.fetchone()
    return _as_stored_action(row) if row is not None else None


def mark_automation_action_succeeded(
    settings: SharedSettings,
    *,
    action_id: str,
    result: dict[str, Any],
    lease_token: str,
) -> bool:
    """Record a completed action only if this worker still owns its lease."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = %s,
                    result = %s,
                    last_error = NULL,
                    locked_at = NULL,
                    lease_token = NULL,
                    next_attempt_at = NULL,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND status = %s
                  AND lease_token = %s::uuid
                RETURNING id
                """,
                (
                    AutomationActionStatus.SUCCEEDED.value,
                    Jsonb(result),
                    action_id,
                    AutomationActionStatus.RUNNING.value,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def mark_automation_action_failed(
    settings: SharedSettings,
    *,
    action_id: str,
    error: str,
    dead: bool = False,
    lease_token: str,
) -> bool:
    """Record an owned action failure with bounded backoff and a dead policy."""
    normalized_error = error.strip()[:2000] or "automation action failed"
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = CASE
                        WHEN %s OR attempts >= %s THEN %s
                        ELSE %s
                    END,
                    last_error = %s,
                    locked_at = NULL,
                    lease_token = NULL,
                    next_attempt_at = CASE
                        WHEN %s OR attempts >= %s THEN NULL
                        ELSE NOW() + (
                            LEAST(
                                %s,
                                %s * POWER(2, GREATEST(attempts - 1, 0))
                            ) * INTERVAL '1 second'
                        )
                    END,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND status = %s
                  AND lease_token = %s::uuid
                RETURNING id
                """,
                (
                    dead,
                    _AUTOMATION_ACTION_MAX_ATTEMPTS,
                    AutomationActionStatus.DEAD.value,
                    AutomationActionStatus.FAILED.value,
                    normalized_error,
                    dead,
                    _AUTOMATION_ACTION_MAX_ATTEMPTS,
                    _AUTOMATION_ACTION_RETRY_MAX_SECONDS,
                    _AUTOMATION_ACTION_RETRY_BASE_SECONDS,
                    action_id,
                    AutomationActionStatus.RUNNING.value,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def list_pending_automation_action_ids(
    settings: SharedSettings,
    *,
    limit: int = 100,
    stale_after_seconds: int = 300,
) -> list[str]:
    """List due actions, including leases stranded after a worker crash."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text
                FROM automation_actions
                WHERE status IN (%s, %s)
                   OR (
                       status = %s
                       AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                   )
                   OR (
                       status = %s
                       AND locked_at < NOW() - (%s * INTERVAL '1 second')
                   )
                ORDER BY
                    CASE WHEN status IN (%s, %s) THEN 0 ELSE 1 END,
                    COALESCE(next_attempt_at, created_at) ASC,
                    created_at ASC
                LIMIT %s
                """,
                (
                    AutomationActionStatus.PENDING.value,
                    AutomationActionStatus.APPROVED.value,
                    AutomationActionStatus.FAILED.value,
                    AutomationActionStatus.RUNNING.value,
                    max(1, stale_after_seconds),
                    AutomationActionStatus.PENDING.value,
                    AutomationActionStatus.APPROVED.value,
                    max(1, min(limit, 1000)),
                ),
            )
            rows = cursor.fetchall()
    return [str(row["id"]) for row in rows]


def list_retryable_automation_action_ids_for_event(
    settings: SharedSettings,
    *,
    event_id: str,
) -> list[str]:
    """Return automatic actions from one event that still need execution."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text
                FROM automation_actions
                WHERE event_id = %s::uuid
                  AND (
                      status IN (%s, %s)
                      OR (
                          status = %s
                          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                      )
                  )
                ORDER BY
                    CASE WHEN status IN (%s, %s) THEN 0 ELSE 1 END,
                    COALESCE(next_attempt_at, created_at) ASC,
                    created_at ASC
                """,
                (
                    event_id,
                    AutomationActionStatus.PENDING.value,
                    AutomationActionStatus.APPROVED.value,
                    AutomationActionStatus.FAILED.value,
                    AutomationActionStatus.PENDING.value,
                    AutomationActionStatus.APPROVED.value,
                ),
            )
            rows = cursor.fetchall()
    return [str(row["id"]) for row in rows]


def automation_action_subject_id(
    settings: SharedSettings,
    *,
    action_id: str,
) -> str | None:
    """Load the normalized event subject attached to an action proposal."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT event.subject_id
                FROM automation_actions action
                INNER JOIN automation_events event ON event.id = action.event_id
                WHERE action.id = %s::uuid
                """,
                (action_id,),
            )
            row = cursor.fetchone()
    return str(row["subject_id"]) if row is not None else None


def automation_action_rule_project_id(
    settings: SharedSettings,
    *,
    action_id: str,
) -> str | None:
    """Return the immutable project scope captured with the evaluation."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT evaluation.rule_project_id::text AS project_id
                FROM automation_actions action
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                WHERE action.id = %s::uuid
                """,
                (action_id,),
            )
            row = cursor.fetchone()
    if row is None or row.get("project_id") is None:
        return None
    return str(row["project_id"])


def list_automation_actions_awaiting_review(
    settings: SharedSettings,
    *,
    event_type: str,
    project_id: str | None = None,
    limit: int = 100,
) -> list[StoredAutomationReviewAction]:
    """List durable suggestions with the immutable evidence a human reviews."""
    normalized_project_id: str | None = None
    if project_id is not None:
        try:
            normalized_project_id = str(UUID(project_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("project_id must be a UUID") from exc
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT action.id::text, action.event_id::text, action.action_type,
                       action.payload, action.mode, action.disposition,
                       action.status, action.attempts, action.idempotency_key,
                       action.lease_token::text, action.approved_by,
                       action.review_decision, action.reviewed_by, action.reviewed_at,
                       evaluation.rule_project_id::text,
                       event.subject_id, event.subject_snapshot
                FROM automation_actions action
                INNER JOIN automation_events event ON event.id = action.event_id
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                WHERE event.event_type = %s
                  AND action.status = %s
                  AND (%s::uuid IS NULL OR evaluation.rule_project_id = %s::uuid)
                ORDER BY action.created_at ASC
                LIMIT %s
                """,
                (
                    event_type,
                    AutomationActionStatus.AWAITING_REVIEW.value,
                    normalized_project_id,
                    normalized_project_id,
                    max(1, min(limit, 1000)),
                ),
            )
            rows = cursor.fetchall()
    return [_as_stored_review_action(row) for row in rows]


def get_automation_review_action(
    settings: SharedSettings,
    *,
    action_id: str,
) -> StoredAutomationReviewAction | None:
    """Load the immutable evidence for one action after a review transition."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT action.id::text, action.event_id::text, action.action_type,
                       action.payload, action.mode, action.disposition,
                       action.status, action.attempts, action.idempotency_key,
                       action.lease_token::text, action.approved_by,
                       action.review_decision, action.reviewed_by, action.reviewed_at,
                       evaluation.rule_project_id::text,
                       event.subject_id, event.subject_snapshot
                FROM automation_actions action
                INNER JOIN automation_events event ON event.id = action.event_id
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                WHERE action.id = %s::uuid
                """,
                (action_id,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return _as_stored_review_action(row)


def list_approved_automation_review_actions(
    settings: SharedSettings,
    *,
    event_type: str,
    action_type: str,
    limit: int = 100,
) -> list[StoredAutomationReviewAction]:
    """Return durable positive feedback for idempotent learning recovery.

    A direct approval should normally derive its rule immediately. This query
    lets the periodic payment recovery sweep retry that derivation after a
    transient database/API process failure without replaying the money action.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT action.id::text, action.event_id::text, action.action_type,
                       action.payload, action.mode, action.disposition,
                       action.status, action.attempts, action.idempotency_key,
                       action.lease_token::text, action.approved_by,
                       action.review_decision, action.reviewed_by, action.reviewed_at,
                       evaluation.rule_project_id::text,
                       event.subject_id, event.subject_snapshot
                FROM automation_actions action
                INNER JOIN automation_events event ON event.id = action.event_id
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                WHERE event.event_type = %s
                  AND action.action_type = %s
                  AND action.disposition = %s
                  AND action.review_decision = %s
                  AND action.approved_by IS NOT NULL
                ORDER BY action.reviewed_at DESC NULLS LAST, action.id ASC
                LIMIT %s
                """,
                (
                    event_type,
                    action_type,
                    AutomationActionDisposition.SUGGESTED.value,
                    AutomationReviewDecision.APPROVED.value,
                    max(1, min(limit, 1000)),
                ),
            )
            rows = cursor.fetchall()
    return [_as_stored_review_action(row) for row in rows]


def approve_automation_action(
    settings: SharedSettings,
    *,
    action_id: str,
    approved_by: str,
) -> StoredAutomationAction | None:
    """Turn one suggestion into an explicitly human-authorized side effect."""
    normalized_approver = approved_by.strip()
    if not normalized_approver:
        raise ValueError("approved_by is required")
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = %s,
                    approved_by = %s,
                    approved_at = NOW(),
                    review_decision = %s,
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    next_attempt_at = NOW(),
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND status = %s
                  AND disposition = %s
                RETURNING id::text, event_id::text, action_type, payload, mode,
                          disposition, status, attempts, idempotency_key,
                          lease_token::text, approved_by,
                          review_decision, reviewed_by, reviewed_at,
                          (
                              SELECT rule_project_id::text
                              FROM automation_rule_evaluations
                              WHERE id = automation_actions.rule_evaluation_id
                          ) AS rule_project_id
                """,
                (
                    AutomationActionStatus.APPROVED.value,
                    normalized_approver,
                    AutomationReviewDecision.APPROVED.value,
                    normalized_approver,
                    action_id,
                    AutomationActionStatus.AWAITING_REVIEW.value,
                    AutomationActionDisposition.SUGGESTED.value,
                ),
            )
            row = cursor.fetchone()
    return _as_stored_action(row) if row is not None else None


def reject_automation_action(
    settings: SharedSettings,
    *,
    action_id: str,
    rejected_by: str,
) -> StoredAutomationAction | None:
    """Record a human rejection without deleting the original suggestion."""
    normalized_reviewer = rejected_by.strip()
    if not normalized_reviewer:
        raise ValueError("rejected_by is required")
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = %s,
                    review_decision = %s,
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    last_error = %s,
                    locked_at = NULL,
                    lease_token = NULL,
                    next_attempt_at = NULL,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND status = %s
                  AND disposition = %s
                RETURNING id::text, event_id::text, action_type, payload, mode,
                          disposition, status, attempts, idempotency_key,
                          lease_token::text, approved_by,
                          review_decision, reviewed_by, reviewed_at,
                          (
                              SELECT rule_project_id::text
                              FROM automation_rule_evaluations
                              WHERE id = automation_actions.rule_evaluation_id
                          ) AS rule_project_id
                """,
                (
                    AutomationActionStatus.DEAD.value,
                    AutomationReviewDecision.REJECTED.value,
                    normalized_reviewer,
                    f"rejected_by:{normalized_reviewer}"[:2000],
                    action_id,
                    AutomationActionStatus.AWAITING_REVIEW.value,
                    AutomationActionDisposition.SUGGESTED.value,
                ),
            )
            row = cursor.fetchone()
    return _as_stored_action(row) if row is not None else None
