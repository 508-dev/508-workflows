"""Payment-domain normalization and durable project allocation helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings
from five08.automation import (
    AutomationAction,
    AutomationActionDisposition,
    AutomationCondition,
    AutomationConditionOperator,
    AutomationEvaluation,
    AutomationRule,
    AutomationRuleMode,
    AutomationRuleOrigin,
)


BANK_TRANSACTION_POSTED_EVENT = "bank_transaction.posted.v1"
PROJECT_PAYMENT_ROUTE_ACTION = "project_payment.route"
PROJECT_PAYMENT_ALLOWED_FACT_PATHS = frozenset(
    {
        "transaction.direction",
        "transaction.amount",
        "transaction.currency",
        "transaction.description",
        "transaction.counterparty",
        "transaction.reference_number",
        "transaction.bank_account",
        "transaction.has_reconciliation",
    }
)
PROJECT_PAYMENT_ALLOWED_ACTION_TYPES = frozenset({PROJECT_PAYMENT_ROUTE_ACTION})
_PROJECT_PAYMENT_IDENTITY_FACTS = frozenset(
    {
        "transaction.counterparty",
        "transaction.description",
        "transaction.reference_number",
    }
)
_PROJECT_PAYMENT_MAX_ATTEMPTS = 8
_PROJECT_PAYMENT_RETRY_BASE_SECONDS = 15
_PROJECT_PAYMENT_RETRY_MAX_SECONDS = 3600
_PROJECT_PAYMENT_LEARNED_RULE_PRIORITY = -100
_PROJECT_PAYMENT_LEARNED_IDENTITY_FACTS = (
    ("transaction.counterparty", "counterparty"),
    ("transaction.reference_number", "reference_number"),
    ("transaction.description", "description"),
)
_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Za-z]{3}$")


class PaymentDirection(StrEnum):
    """Direction relative to the cooperative's bank account."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ProjectPaymentAllocationStatus(StrEnum):
    """Human/accounting confirmation state for a project allocation."""

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProjectPaymentMatchMethod(StrEnum):
    """The evidence source used to classify a payment against a project."""

    ERP_RECONCILED = "erp_reconciled"
    CONFIGURED_RULE = "configured_rule"
    MANUAL = "manual"
    LEARNED_SUGGESTION = "learned_suggestion"


class ProjectPaymentNotificationStatus(StrEnum):
    """Delivery lifecycle for a payment receipt message."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"
    DEAD = "dead"


class ProjectPaymentActionApplicationStatus(StrEnum):
    """Outcome of the transactional project-payment action boundary."""

    APPLIED = "applied"
    BLOCKED = "blocked"
    NOT_OWNER = "not_owner"


def project_payment_rule_has_valid_scope(rule: AutomationRule) -> bool:
    """Check that every payment-route action targets its owning project UUID."""
    route_actions = [
        action
        for action in rule.actions
        if action.action_type == PROJECT_PAYMENT_ROUTE_ACTION
    ]
    if not route_actions:
        return True
    try:
        rule_project_id = str(UUID(str(rule.project_id)))
    except (AttributeError, TypeError, ValueError):
        return False
    for action in route_actions:
        try:
            action_project_id = str(UUID(str(action.payload.get("project_id"))))
        except (AttributeError, TypeError, ValueError):
            return False
        if action_project_id != rule_project_id:
            return False
    return True


def automatic_project_payment_rule_is_safe(rule: AutomationRule) -> bool:
    """Require matching project scope plus strong evidence before auto-confirm.

    Amount-only matches are useful suggestions but can collide across unrelated
    invoices. A generic ERP reconciliation flag is not project identity: the
    linked payment entry must be resolved to the same local project before it
    can be used as automatic evidence, and v1 deliberately does not do that.
    The action payload repeats the UUID deliberately, and must agree with the
    rule scope, so a generic rule cannot route a receipt to an unrelated
    project.
    """
    route_actions = [
        action
        for action in rule.actions
        if action.action_type == PROJECT_PAYMENT_ROUTE_ACTION
    ]
    if rule.mode is not AutomationRuleMode.AUTOMATIC or not route_actions:
        return True
    if rule.origin is AutomationRuleOrigin.LEARNED:
        return False
    if not project_payment_rule_has_valid_scope(rule):
        return False
    has_identifier = any(
        condition.fact in _PROJECT_PAYMENT_IDENTITY_FACTS
        and condition.operator
        in {
            AutomationConditionOperator.EQUALS,
            AutomationConditionOperator.CONTAINS,
        }
        and isinstance(condition.value, str)
        and bool(condition.value.strip())
        for condition in rule.conditions
    )
    exact_amounts = {
        amount
        for condition in rule.conditions
        if condition.fact == "transaction.amount"
        and condition.operator is AutomationConditionOperator.EQUALS
        if (amount := _decimal_or_none(condition.value)) is not None and amount > 0
    }
    has_currency = any(
        condition.fact == "transaction.currency"
        and condition.operator is AutomationConditionOperator.EQUALS
        and isinstance(condition.value, str)
        and bool(condition.value.strip())
        for condition in rule.conditions
    )
    if not (has_identifier and exact_amounts and has_currency):
        return False
    for action in route_actions:
        configured_amount = action.payload.get("amount")
        if configured_amount is None:
            continue
        action_amount = _decimal_or_none(configured_amount)
        if (
            action_amount is None
            or action_amount <= 0
            or action_amount not in exact_amounts
        ):
            return False
    return True


def project_payment_rule_for_evaluation(rule: AutomationRule) -> AutomationRule:
    """Downgrade unsafe automatic payment routing to a reviewable suggestion."""
    if automatic_project_payment_rule_is_safe(rule):
        return rule
    return rule.model_copy(update={"mode": AutomationRuleMode.SUGGEST})


def learned_project_payment_suggestion_rule(
    *,
    project_id: str,
    subject_snapshot: Mapping[str, object],
) -> AutomationRule | None:
    """Compile one approved receipt into a conservative future suggestion.

    This is deliberately a small, deterministic learning loop rather than a
    statistical classifier: an explicit human approval may teach a future
    *review* suggestion only when the canonical ERP evidence includes an
    inbound direction, a 3-letter currency, and a meaningful payer/reference
    identity.  The generated rule is permanently ``suggest`` mode, so it can
    never create a payment allocation or Discord post without another human
    approval.
    """
    try:
        normalized_project_id = str(UUID(str(project_id)))
    except (AttributeError, TypeError, ValueError):
        return None
    direction = _text_or_none(subject_snapshot.get("direction"))
    if direction != PaymentDirection.INBOUND.value:
        return None
    currency = _text_or_none(subject_snapshot.get("currency"))
    if currency is None or not _CURRENCY_CODE_PATTERN.fullmatch(currency):
        return None
    normalized_currency = currency.upper()

    identity_fact: str | None = None
    identity_value: str | None = None
    for fact, snapshot_key in _PROJECT_PAYMENT_LEARNED_IDENTITY_FACTS:
        candidate = _normalized_learned_identity(subject_snapshot.get(snapshot_key))
        if candidate is not None:
            identity_fact = fact
            identity_value = candidate
            break
    if identity_fact is None or identity_value is None:
        return None

    learning_material = json.dumps(
        {
            "event_type": BANK_TRANSACTION_POSTED_EVENT,
            "project_id": normalized_project_id,
            "identity_fact": identity_fact,
            "identity": identity_value.casefold(),
            "currency": normalized_currency,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    # Keep payer/reference values out of stable database identifiers. The
    # content remains only in the rule condition, where an authorized operator
    # can inspect or disable it.
    learning_key = (
        "project-payment:v1:" + sha256(learning_material.encode("utf-8")).hexdigest()
    )
    return AutomationRule(
        id=str(uuid5(NAMESPACE_URL, learning_key)),
        project_id=normalized_project_id,
        event_type=BANK_TRANSACTION_POSTED_EVENT,
        origin=AutomationRuleOrigin.LEARNED,
        learning_key=learning_key,
        priority=_PROJECT_PAYMENT_LEARNED_RULE_PRIORITY,
        mode=AutomationRuleMode.SUGGEST,
        conditions=[
            AutomationCondition(
                fact="transaction.direction",
                operator=AutomationConditionOperator.EQUALS,
                value=PaymentDirection.INBOUND.value,
            ),
            AutomationCondition(
                fact=identity_fact,
                operator=AutomationConditionOperator.CONTAINS,
                value=identity_value,
            ),
            AutomationCondition(
                fact="transaction.currency",
                operator=AutomationConditionOperator.EQUALS,
                value=normalized_currency,
            ),
        ],
        actions=[
            AutomationAction(
                action_type=PROJECT_PAYMENT_ROUTE_ACTION,
                payload={"project_id": normalized_project_id},
            )
        ],
    )


def _normalized_learned_identity(value: object) -> str | None:
    """Return a compact meaningful identity value, or reject vague evidence."""
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if len(normalized) < 3 or not any(character.isalnum() for character in normalized):
        return None
    return normalized


def project_payment_evaluation_for_execution(
    evaluation: AutomationEvaluation,
) -> AutomationEvaluation:
    """Resolve competing automatic payment routes conservatively.

    Rule priority selects one automatic route only when the highest-priority
    match is unique. Any tie, and all lower-priority automatic matches, become
    human-review suggestions instead of silently splitting or racing a receipt
    between projects.
    """
    automatic_routes = [
        proposal
        for proposal in evaluation.action_proposals
        if proposal.action.action_type == PROJECT_PAYMENT_ROUTE_ACTION
        and proposal.mode is AutomationRuleMode.AUTOMATIC
        and proposal.disposition is AutomationActionDisposition.READY
    ]
    if len(automatic_routes) <= 1:
        return evaluation

    highest_priority = max(proposal.rule_priority for proposal in automatic_routes)
    highest = [
        proposal
        for proposal in automatic_routes
        if proposal.rule_priority == highest_priority
    ]
    winning_key: tuple[str, int, int] | None = None
    if len(highest) == 1:
        winner = highest[0]
        winning_key = (winner.rule_id, winner.rule_version, winner.rule_priority)

    resolved_proposals = []
    for proposal in evaluation.action_proposals:
        if proposal not in automatic_routes:
            resolved_proposals.append(proposal)
            continue
        proposal_key = (
            proposal.rule_id,
            proposal.rule_version,
            proposal.rule_priority,
        )
        if proposal_key == winning_key:
            resolved_proposals.append(proposal)
            continue
        resolved_proposals.append(
            proposal.model_copy(
                update={
                    "mode": AutomationRuleMode.SUGGEST,
                    "disposition": AutomationActionDisposition.SUGGESTED,
                }
            )
        )
    return evaluation.model_copy(update={"action_proposals": resolved_proposals})


def project_payment_notification_idempotency_key(
    *,
    payment_transaction_id: str,
    allocation_id: str,
    project_discord_channel_id: str,
) -> str:
    """Build an outbox key scoped to the durable channel registration.

    A Discord channel ID can be intentionally unregistered then registered
    again. Its mapping UUID is the authorization object for a delivery, so it
    must be part of deduplication rather than the reusable Discord ID.
    """
    return (
        "payment-received:v2:"
        f"{payment_transaction_id}:{allocation_id}:{project_discord_channel_id}"
    )


@dataclass(frozen=True)
class BankTransactionInput:
    """Sanitized local projection of a canonical ERPNext Bank Transaction."""

    source: str
    external_id: str
    source_revision: str | None
    source_modified_at: datetime | None
    transaction_id: str | None
    posted_at: datetime | None
    bank_account: str | None
    direction: PaymentDirection
    amount: Decimal
    currency: str | None
    counterparty: str | None
    description: str | None
    reference_number: str | None
    reconciliation_entries: list[dict[str, Any]]
    source_payload: dict[str, Any]


@dataclass(frozen=True)
class StoredBankTransaction:
    """Identity of a local transaction projection."""

    id: str
    created: bool
    accepted: bool = True


@dataclass(frozen=True)
class BankTransactionRecord:
    """Fields needed by the typed payment action executor."""

    id: str
    source: str
    external_id: str
    source_revision: str | None
    direction: PaymentDirection
    amount: Decimal
    currency: str | None
    bank_account: str | None
    counterparty: str | None
    description: str | None
    reference_number: str | None
    posted_at: datetime | None


@dataclass(frozen=True)
class ProjectPaymentAllocation:
    """One persisted project share of a bank receipt."""

    id: str
    created: bool


@dataclass(frozen=True)
class ProjectPaymentNotification:
    """One durable request for the bot to announce a confirmed receipt."""

    id: str
    allocation_id: str
    project_id: str
    channel_id: str
    payload: dict[str, Any]
    status: ProjectPaymentNotificationStatus
    attempts: int
    lease_token: str | None


@dataclass(frozen=True)
class ProjectPaymentNotificationDeliveryContext:
    """Canonical notification data the bot may render after DB authorization."""

    notification_id: str
    project_discord_channel_id: str
    project_id: str
    guild_id: str
    channel_id: str
    allocation_id: str
    amount: Decimal
    currency: str | None
    posted_at: datetime | None
    bank_transaction_id: str


@dataclass(frozen=True)
class ProjectPaymentNotificationSourceContext:
    """Canonical source identity bound to an owned notification lease.

    The worker uses this compact context to re-read ERPNext immediately before
    asking Discord to publish.  Keeping the allocation's captured revision
    alongside the current local projection makes a delayed delivery fail
    closed when a Bank Transaction has been corrected or canceled.
    """

    notification_id: str
    bank_transaction_id: str
    source: str
    external_id: str
    allocation_status: ProjectPaymentAllocationStatus
    allocation_source_revision: str | None
    current_source_revision: str | None


@dataclass(frozen=True)
class ProjectPaymentActionApplication:
    """Result of applying one claimed payment route inside one DB transaction."""

    status: ProjectPaymentActionApplicationStatus
    action_id: str
    allocation_id: str | None = None
    allocation_created: bool = False
    notification_ids: tuple[str, ...] = ()
    reason: str | None = None


def _text_or_none(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _decimal_or_none(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        candidate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return candidate if candidate.is_finite() else None


def _datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    normalized = _text_or_none(value)
    if normalized is None:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reconciliation_entries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(entry) for entry in value if isinstance(entry, dict)]


def erpnext_bank_transaction_to_input(
    transaction: dict[str, Any],
) -> BankTransactionInput | None:
    """Normalize a submitted ERPNext Bank Transaction without trusting a webhook.

    Returns ``None`` for canceled/draft records and empty zero-value records.
    A document with both a deposit and a withdrawal is ambiguous and must be
    repaired in ERPNext before it can feed project routing.
    """
    external_id = _text_or_none(transaction.get("name"))
    if external_id is None:
        raise ValueError("ERPNext Bank Transaction is missing name")

    docstatus = transaction.get("docstatus")
    if isinstance(docstatus, bool):
        submitted = False
    elif isinstance(docstatus, int | str):
        try:
            submitted = int(docstatus) == 1
        except ValueError:
            submitted = False
    else:
        submitted = False
    if not submitted:
        return None

    deposit = _decimal_or_none(transaction.get("deposit")) or Decimal("0")
    withdrawal = _decimal_or_none(transaction.get("withdrawal")) or Decimal("0")
    if deposit < 0 or withdrawal < 0:
        raise ValueError("ERPNext Bank Transaction contains a negative amount")
    if deposit > 0 and withdrawal > 0:
        raise ValueError(
            "ERPNext Bank Transaction cannot be classified with both deposit and withdrawal"
        )
    if deposit == 0 and withdrawal == 0:
        return None

    direction = PaymentDirection.INBOUND if deposit > 0 else PaymentDirection.OUTBOUND
    amount = deposit if deposit > 0 else withdrawal
    entries = _reconciliation_entries(transaction.get("payment_entries"))
    transaction_id = _text_or_none(transaction.get("transaction_id"))
    source_revision = _text_or_none(transaction.get("modified"))
    return BankTransactionInput(
        source="erpnext",
        external_id=external_id,
        source_revision=source_revision,
        source_modified_at=_datetime_or_none(source_revision),
        transaction_id=transaction_id,
        posted_at=_datetime_or_none(
            transaction.get("date") or transaction.get("transaction_date")
        ),
        bank_account=_text_or_none(transaction.get("bank_account")),
        direction=direction,
        amount=amount,
        currency=_text_or_none(
            transaction.get("currency") or transaction.get("account_currency")
        ),
        counterparty=_text_or_none(
            # ``bank_party_name`` is the payer identity supplied by the bank
            # statement. ``party`` may instead be reconciliation-derived, so
            # do not let a later ERP match silently replace the raw rule fact.
            transaction.get("bank_party_name")
            or transaction.get("party")
            or transaction.get("party_name")
            or transaction.get("description")
        ),
        description=_text_or_none(transaction.get("description")),
        reference_number=_text_or_none(
            transaction.get("reference_number") or transaction.get("cheque_number")
        ),
        reconciliation_entries=entries,
        source_payload={
            "erpnext_name": external_id,
            "transaction_id": transaction_id,
            "docstatus": 1,
            "payment_entries": entries,
        },
    )


def payment_automation_facts(transaction: BankTransactionInput) -> dict[str, Any]:
    """Build the explicitly allowlisted fact shape exposed to payment rules."""
    return {
        "transaction": {
            "direction": transaction.direction.value,
            "amount": format(transaction.amount, "f"),
            "currency": transaction.currency,
            "description": transaction.description,
            "counterparty": transaction.counterparty,
            "reference_number": transaction.reference_number,
            "bank_account": transaction.bank_account,
            "has_reconciliation": bool(transaction.reconciliation_entries),
        }
    }


def payment_automation_subject_snapshot(
    transaction: BankTransactionInput,
) -> dict[str, Any]:
    """Capture the immutable payment evidence used by the action executor.

    The source projection may later be refreshed in place for a newer ERP
    document revision. Automation actions therefore execute from this compact,
    persisted snapshot and compare its canonical revision to the live row
    before allocating anything.
    """
    return {
        "source": transaction.source,
        "external_id": transaction.external_id,
        "source_revision": transaction.source_revision,
        "transaction_id": transaction.transaction_id,
        "direction": transaction.direction.value,
        "amount": format(transaction.amount, "f"),
        "currency": transaction.currency,
        "bank_account": transaction.bank_account,
        "counterparty": transaction.counterparty,
        "description": transaction.description,
        "reference_number": transaction.reference_number,
        "posted_at": (
            transaction.posted_at.isoformat()
            if transaction.posted_at is not None
            else None
        ),
        "reconciliation_entries": transaction.reconciliation_entries,
    }


def upsert_bank_transaction(
    settings: SharedSettings,
    transaction: BankTransactionInput,
) -> StoredBankTransaction:
    """Persist a sanitized source-of-truth projection keyed by source identity."""
    row_id = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO bank_transactions (
                    id,
                    source,
                    external_id,
                    source_revision,
                    source_modified_at,
                    transaction_id,
                    posted_at,
                    bank_account,
                    direction,
                    amount,
                    currency,
                    counterparty,
                    description,
                    reference_number,
                    reconciliation_entries,
                    source_payload
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (source, external_id) DO UPDATE
                SET source_revision = EXCLUDED.source_revision,
                    source_modified_at = EXCLUDED.source_modified_at,
                    transaction_id = EXCLUDED.transaction_id,
                    posted_at = EXCLUDED.posted_at,
                    bank_account = EXCLUDED.bank_account,
                    direction = EXCLUDED.direction,
                    amount = EXCLUDED.amount,
                    currency = EXCLUDED.currency,
                    counterparty = EXCLUDED.counterparty,
                    description = EXCLUDED.description,
                    reference_number = EXCLUDED.reference_number,
                    reconciliation_entries = EXCLUDED.reconciliation_entries,
                    source_payload = EXCLUDED.source_payload,
                    updated_at = NOW()
                WHERE (
                    EXCLUDED.source_modified_at IS NOT NULL
                    AND (
                        bank_transactions.source_modified_at IS NULL
                        OR EXCLUDED.source_modified_at >= bank_transactions.source_modified_at
                    )
                ) OR (
                    EXCLUDED.source_modified_at IS NULL
                    AND bank_transactions.source_modified_at IS NULL
                )
                RETURNING id::text, (xmax = 0) AS created
                """,
                (
                    row_id,
                    transaction.source,
                    transaction.external_id,
                    transaction.source_revision,
                    transaction.source_modified_at,
                    transaction.transaction_id,
                    transaction.posted_at,
                    transaction.bank_account,
                    transaction.direction.value,
                    transaction.amount,
                    transaction.currency,
                    transaction.counterparty,
                    transaction.description,
                    transaction.reference_number,
                    Jsonb(transaction.reconciliation_entries),
                    Jsonb(transaction.source_payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                stored_transaction = StoredBankTransaction(
                    id=str(row["id"]),
                    created=bool(row["created"]),
                    accepted=True,
                )
                if (
                    not stored_transaction.created
                    and transaction.source_revision is not None
                ):
                    # Allocations are evidence for one immutable ERP revision.
                    # When canonical source state advances, retain the old row
                    # for audit but prevent it (and its pending outbox rows)
                    # from being treated as a current confirmed allocation.
                    # A fresh event can produce a new rule evaluation instead.
                    cursor.execute(
                        """
                        UPDATE project_payment_allocations
                        SET status = %s,
                            superseded_reason = %s,
                            superseded_at = NOW(),
                            updated_at = NOW()
                        WHERE payment_transaction_id = %s::uuid
                          AND status = %s
                          AND match_method = %s
                          AND evidence ->> 'source_revision' IS DISTINCT FROM %s
                        """,
                        (
                            ProjectPaymentAllocationStatus.SUPERSEDED.value,
                            "source_revision_changed",
                            stored_transaction.id,
                            ProjectPaymentAllocationStatus.CONFIRMED.value,
                            ProjectPaymentMatchMethod.CONFIGURED_RULE.value,
                            transaction.source_revision,
                        ),
                    )
                return stored_transaction
            cursor.execute(
                """
                SELECT id::text
                FROM bank_transactions
                WHERE source = %s AND external_id = %s
                """,
                (transaction.source, transaction.external_id),
            )
            existing = cursor.fetchone()
    if existing is None:
        raise RuntimeError("Unable to persist bank transaction")
    return StoredBankTransaction(
        id=str(existing["id"]),
        created=False,
        accepted=False,
    )


def get_bank_transaction(
    settings: SharedSettings,
    *,
    transaction_id: str,
) -> BankTransactionRecord | None:
    """Load the constrained transaction shape needed for a routing action."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, source, external_id, source_revision, direction,
                       amount, currency, bank_account, counterparty, description,
                       reference_number, posted_at
                FROM bank_transactions
                WHERE id = %s::uuid
                """,
                (transaction_id,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return BankTransactionRecord(
        id=str(row["id"]),
        source=str(row["source"]),
        external_id=str(row["external_id"]),
        source_revision=_text_or_none(row.get("source_revision")),
        direction=PaymentDirection(str(row["direction"])),
        amount=Decimal(str(row["amount"])),
        currency=_text_or_none(row.get("currency")),
        bank_account=_text_or_none(row.get("bank_account")),
        counterparty=_text_or_none(row.get("counterparty")),
        description=_text_or_none(row.get("description")),
        reference_number=_text_or_none(row.get("reference_number")),
        posted_at=row.get("posted_at"),
    )


def create_project_payment_allocation(
    settings: SharedSettings,
    *,
    payment_transaction_id: str,
    project_id: str,
    amount: Decimal,
    currency: str | None,
    status: ProjectPaymentAllocationStatus,
    match_method: ProjectPaymentMatchMethod,
    idempotency_key: str,
    automation_action_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> ProjectPaymentAllocation:
    """Create an allocation, rejecting an over-allocated confirmed receipt.

    The parent transaction and open local project rows are locked in one
    transaction. That serializes concurrent allocations and project closure,
    so a receipt cannot be newly auto-confirmed after its project closes.
    """
    if amount <= 0:
        raise ValueError("allocation amount must be greater than zero")
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("idempotency_key is required")
    normalized_currency = _text_or_none(currency)

    allocation_id = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, amount, direction
                FROM bank_transactions
                WHERE id = %s::uuid
                FOR UPDATE
                """,
                (payment_transaction_id,),
            )
            transaction_row = cursor.fetchone()
            if transaction_row is None:
                raise ValueError("bank transaction was not found")
            if str(transaction_row["direction"]) != PaymentDirection.INBOUND.value:
                raise ValueError(
                    "only inbound transactions can be allocated as receipts"
                )

            cursor.execute(
                """
                SELECT id::text
                FROM project_payment_allocations
                WHERE idempotency_key = %s
                """,
                (normalized_key,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return ProjectPaymentAllocation(id=str(existing["id"]), created=False)

            cursor.execute(
                """
                SELECT id::text
                FROM projects
                WHERE id = %s::uuid
                  AND LOWER(COALESCE(source_status, '')) = 'open'
                FOR UPDATE
                """,
                (project_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("project_id does not identify an open local project")

            if status is ProjectPaymentAllocationStatus.CONFIRMED:
                cursor.execute(
                    """
                    SELECT amount
                    FROM project_payment_allocations
                    WHERE payment_transaction_id = %s::uuid
                      AND status = %s
                    FOR UPDATE
                    """,
                    (
                        payment_transaction_id,
                        ProjectPaymentAllocationStatus.CONFIRMED.value,
                    ),
                )
                allocated_amount = sum(
                    (Decimal(str(row["amount"])) for row in cursor.fetchall()),
                    Decimal("0"),
                )
                transaction_amount = Decimal(str(transaction_row["amount"]))
                if allocated_amount + amount > transaction_amount:
                    raise ValueError("confirmed allocations exceed transaction amount")

            cursor.execute(
                """
                INSERT INTO project_payment_allocations (
                    id,
                    payment_transaction_id,
                    project_id,
                    amount,
                    currency,
                    status,
                    match_method,
                    automation_action_id,
                    evidence,
                    idempotency_key
                ) VALUES (
                    %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s::uuid, %s, %s
                )
                RETURNING id::text
                """,
                (
                    allocation_id,
                    payment_transaction_id,
                    project_id,
                    amount,
                    normalized_currency,
                    status.value,
                    match_method.value,
                    automation_action_id,
                    Jsonb(evidence or {}),
                    normalized_key,
                ),
            )
            created = cursor.fetchone()
    if created is None:
        raise RuntimeError("Unable to persist project payment allocation")
    return ProjectPaymentAllocation(id=str(created["id"]), created=True)


def project_is_open(settings: SharedSettings, *, project_id: str) -> bool:
    """Return whether a local project is still active for automatic routing.

    Closed projects retain their payment history, but a new receipt must be
    reviewed rather than silently allocated/announced against a stale channel.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM projects
                WHERE id = %s::uuid
                  AND LOWER(COALESCE(source_status, '')) = 'open'
                """,
                (project_id,),
            )
            return cursor.fetchone() is not None


def create_project_payment_notification(
    settings: SharedSettings,
    *,
    allocation_id: str,
    project_id: str,
    project_discord_channel_id: str,
    channel_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> tuple[str, bool]:
    """Write one notification outbox row without duplicating a channel delivery."""
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise ValueError("idempotency_key is required")
    notification_id = str(uuid4())
    message_payload = dict(payload)
    message_payload.update(
        {
            "notification_id": notification_id,
            "project_id": project_id,
            "channel_id": channel_id,
            "allocation_id": allocation_id,
        }
    )
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO project_payment_notification_outbox (
                    id,
                    allocation_id,
                    project_discord_channel_id,
                    idempotency_key,
                    payload
                ) VALUES (%s, %s::uuid, %s::uuid, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id::text
                """,
                (
                    notification_id,
                    allocation_id,
                    project_discord_channel_id,
                    normalized_key,
                    Jsonb(message_payload),
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return str(row["id"]), True
            cursor.execute(
                """
                SELECT id::text
                FROM project_payment_notification_outbox
                WHERE allocation_id = %s::uuid
                  AND project_discord_channel_id = %s::uuid
                """,
                (allocation_id, project_discord_channel_id),
            )
            existing = cursor.fetchone()
    if existing is None:
        raise RuntimeError("Unable to load duplicate project payment notification")
    return str(existing["id"]), False


def _as_notification_status(value: object) -> ProjectPaymentNotificationStatus:
    try:
        return ProjectPaymentNotificationStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"Unknown payment notification status: {value!r}") from exc


def _as_project_payment_notification(row: dict[str, Any]) -> ProjectPaymentNotification:
    payload = row.get("payload")
    return ProjectPaymentNotification(
        id=str(row["id"]),
        allocation_id=str(row["allocation_id"]),
        project_id=str(row["project_id"]),
        channel_id=str(row["channel_id"]),
        payload=dict(payload) if isinstance(payload, dict) else {},
        status=_as_notification_status(row["status"]),
        attempts=int(row["attempts"]),
        lease_token=_text_or_none(row.get("lease_token")),
    )


def claim_project_payment_notification(
    settings: SharedSettings,
    *,
    notification_id: str,
    stale_after_seconds: int = 300,
) -> ProjectPaymentNotification | None:
    """Claim an eligible notification using an owned worker-side lease."""
    lease_token = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE project_payment_notification_outbox outbox
                SET status = %s,
                    attempts = attempts + 1,
                    locked_at = NOW(),
                    lease_token = %s::uuid,
                    updated_at = NOW()
                FROM project_discord_channels channel
                INNER JOIN projects project ON project.id = channel.project_id
                INNER JOIN project_payment_allocations allocation
                    ON allocation.id = outbox.allocation_id
                WHERE outbox.id = %s::uuid
                  AND channel.id = outbox.project_discord_channel_id
                  AND channel.active IS TRUE
                  AND LOWER(COALESCE(project.source_status, '')) = 'open'
                  AND allocation.status = %s
                  AND (
                    outbox.status = %s
                    OR (
                        outbox.status = %s
                        AND (
                            outbox.next_attempt_at IS NULL
                            OR outbox.next_attempt_at <= NOW()
                        )
                    )
                    OR (
                        outbox.status = %s
                        AND outbox.locked_at < NOW() - (%s * INTERVAL '1 second')
                    )
                  )
                RETURNING outbox.id::text, outbox.allocation_id::text,
                          channel.project_id::text, channel.channel_id, outbox.payload,
                          outbox.status, outbox.attempts, outbox.lease_token::text
                """,
                (
                    ProjectPaymentNotificationStatus.SENDING.value,
                    lease_token,
                    notification_id,
                    ProjectPaymentAllocationStatus.CONFIRMED.value,
                    ProjectPaymentNotificationStatus.PENDING.value,
                    ProjectPaymentNotificationStatus.FAILED.value,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    max(1, stale_after_seconds),
                ),
            )
            row = cursor.fetchone()
    return _as_project_payment_notification(row) if row is not None else None


def mark_project_payment_notification_sent(
    settings: SharedSettings,
    *,
    notification_id: str,
    discord_message_id: str,
    lease_token: str,
) -> bool:
    """Record the Discord message ID only for the claimant that sent it."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_notification_outbox
                SET status = %s,
                    discord_message_id = %s,
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
                    ProjectPaymentNotificationStatus.SENT.value,
                    discord_message_id,
                    notification_id,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def mark_project_payment_notification_failed(
    settings: SharedSettings,
    *,
    notification_id: str,
    error: str,
    blocked: bool = False,
    lease_token: str,
) -> bool:
    """Store a failure only for the claimant that owns the active lease."""
    normalized_error = error.strip()[:2000] or "project payment notification failed"
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_notification_outbox
                SET status = CASE
                        WHEN %s THEN %s
                        WHEN attempts >= %s THEN %s
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
                    blocked,
                    ProjectPaymentNotificationStatus.BLOCKED.value,
                    _PROJECT_PAYMENT_MAX_ATTEMPTS,
                    ProjectPaymentNotificationStatus.DEAD.value,
                    ProjectPaymentNotificationStatus.FAILED.value,
                    normalized_error,
                    blocked,
                    _PROJECT_PAYMENT_MAX_ATTEMPTS,
                    _PROJECT_PAYMENT_RETRY_MAX_SECONDS,
                    _PROJECT_PAYMENT_RETRY_BASE_SECONDS,
                    notification_id,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def list_retryable_project_payment_notification_ids(
    settings: SharedSettings,
    *,
    limit: int = 100,
    stale_after_seconds: int = 300,
) -> list[str]:
    """List retryable rows, including delivery leases stranded by a crash."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text
                FROM project_payment_notification_outbox
                WHERE status = %s
                   OR (
                       status = %s
                       AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                   )
                   OR (
                       status = %s
                       AND locked_at < NOW() - (%s * INTERVAL '1 second')
                   )
                ORDER BY
                    CASE WHEN status = %s THEN 0 ELSE 1 END,
                    COALESCE(next_attempt_at, created_at) ASC,
                    created_at ASC
                LIMIT %s
                """,
                (
                    ProjectPaymentNotificationStatus.PENDING.value,
                    ProjectPaymentNotificationStatus.FAILED.value,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    max(1, stale_after_seconds),
                    ProjectPaymentNotificationStatus.PENDING.value,
                    max(1, min(limit, 1000)),
                ),
            )
            rows = cursor.fetchall()
    return [str(row["id"]) for row in rows]


def block_project_payment_notification_if_ineligible(
    settings: SharedSettings,
    *,
    notification_id: str,
    stale_after_seconds: int = 300,
) -> bool:
    """Terminally block ineligible queued or abandoned notification work.

    A live sender keeps its lease. A stale ``sending`` row is abandoned work;
    blocking it on recovery prevents a revoked channel/project or superseded
    allocation from being retried indefinitely after the sending worker
    crashed.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_notification_outbox outbox
                SET status = %s,
                    last_error = 'project_channel_or_allocation_ineligible',
                    locked_at = NULL,
                    lease_token = NULL,
                    next_attempt_at = NULL,
                    updated_at = NOW()
                WHERE outbox.id = %s::uuid
                  AND (
                      outbox.status IN (%s, %s)
                      OR (
                          outbox.status = %s
                          AND outbox.locked_at < NOW() - (%s * INTERVAL '1 second')
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM project_discord_channels channel
                      INNER JOIN projects project ON project.id = channel.project_id
                      INNER JOIN project_payment_allocations allocation
                          ON allocation.id = outbox.allocation_id
                      WHERE channel.id = outbox.project_discord_channel_id
                        AND channel.active IS TRUE
                        AND LOWER(COALESCE(project.source_status, '')) = 'open'
                        AND allocation.status = %s
                  )
                RETURNING outbox.id
                """,
                (
                    ProjectPaymentNotificationStatus.BLOCKED.value,
                    notification_id,
                    ProjectPaymentNotificationStatus.PENDING.value,
                    ProjectPaymentNotificationStatus.FAILED.value,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    max(1, stale_after_seconds),
                    ProjectPaymentAllocationStatus.CONFIRMED.value,
                ),
            )
            return cursor.fetchone() is not None


def get_project_payment_notification_delivery_context(
    settings: SharedSettings,
    *,
    notification_id: str,
    worker_lease_token: str,
    project_discord_channel_id: str | None = None,
) -> ProjectPaymentNotificationDeliveryContext | None:
    """Load bot-renderable data from the durable outbox, never HTTP input.

    The query binds a notification to its original channel mapping and checks
    that mapping and the local ERP project are currently eligible. This is the
    authorization source for the Discord bot's internal endpoint. The worker
    lease is required so a holder of the shared API secret cannot force-send a
    historical, blocked, or otherwise unclaimed notification ID.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT outbox.id::text AS notification_id,
                       outbox.allocation_id::text AS allocation_id,
                       channel.id::text AS project_discord_channel_id,
                       channel.project_id::text AS project_id,
                       channel.guild_id,
                       channel.channel_id,
                       allocation.amount,
                       allocation.currency,
                       transaction.posted_at,
                       transaction.id::text AS bank_transaction_id
                FROM project_payment_notification_outbox outbox
                INNER JOIN project_discord_channels channel
                    ON channel.id = outbox.project_discord_channel_id
                INNER JOIN projects project ON project.id = channel.project_id
                INNER JOIN project_payment_allocations allocation
                    ON allocation.id = outbox.allocation_id
                INNER JOIN bank_transactions transaction
                    ON transaction.id = allocation.payment_transaction_id
                WHERE outbox.id = %s::uuid
                  AND outbox.status = %s
                  AND outbox.lease_token = %s::uuid
                  AND (%s::uuid IS NULL OR channel.id = %s::uuid)
                  AND channel.active IS TRUE
                  AND LOWER(COALESCE(project.source_status, '')) = 'open'
                  AND allocation.status = %s
                """,
                (
                    notification_id,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    worker_lease_token,
                    project_discord_channel_id,
                    project_discord_channel_id,
                    ProjectPaymentAllocationStatus.CONFIRMED.value,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return ProjectPaymentNotificationDeliveryContext(
        notification_id=str(row["notification_id"]),
        project_discord_channel_id=str(row["project_discord_channel_id"]),
        project_id=str(row["project_id"]),
        guild_id=str(row["guild_id"]),
        channel_id=str(row["channel_id"]),
        allocation_id=str(row["allocation_id"]),
        amount=Decimal(str(row["amount"])),
        currency=_text_or_none(row.get("currency")),
        posted_at=row.get("posted_at"),
        bank_transaction_id=str(row["bank_transaction_id"]),
    )


def get_project_payment_notification_source_context(
    settings: SharedSettings,
    *,
    notification_id: str,
    lease_token: str,
) -> ProjectPaymentNotificationSourceContext | None:
    """Load source identity for the worker that currently owns delivery.

    This deliberately does not trust outbox JSON supplied to another runtime:
    the allocation's immutable evidence is the expected revision and the
    durable transaction projection is the current local revision.  A missing
    row means the worker no longer owns this notification or its durable
    parent was removed, and must not attempt delivery.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT outbox.id::text AS notification_id,
                       transaction.id::text AS bank_transaction_id,
                       transaction.source,
                       transaction.external_id,
                       transaction.source_revision AS current_source_revision,
                       allocation.status AS allocation_status,
                       allocation.evidence ->> 'source_revision'
                           AS allocation_source_revision
                FROM project_payment_notification_outbox outbox
                INNER JOIN project_payment_allocations allocation
                    ON allocation.id = outbox.allocation_id
                INNER JOIN bank_transactions transaction
                    ON transaction.id = allocation.payment_transaction_id
                WHERE outbox.id = %s::uuid
                  AND outbox.status = %s
                  AND outbox.lease_token = %s::uuid
                """,
                (
                    notification_id,
                    ProjectPaymentNotificationStatus.SENDING.value,
                    lease_token,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return ProjectPaymentNotificationSourceContext(
        notification_id=str(row["notification_id"]),
        bank_transaction_id=str(row["bank_transaction_id"]),
        source=str(row["source"]),
        external_id=str(row["external_id"]),
        allocation_status=ProjectPaymentAllocationStatus(str(row["allocation_status"])),
        allocation_source_revision=_text_or_none(row.get("allocation_source_revision")),
        current_source_revision=_text_or_none(row.get("current_source_revision")),
    )


def _block_claimed_project_payment_action(
    cursor: Any,
    *,
    action_id: str,
    lease_token: str,
    reason: str,
) -> ProjectPaymentActionApplication:
    """Dead-letter an owned action that failed a deterministic policy check."""
    cursor.execute(
        """
        UPDATE automation_actions
        SET status = 'dead',
            last_error = %s,
            locked_at = NULL,
            lease_token = NULL,
            next_attempt_at = NULL,
            updated_at = NOW()
        WHERE id = %s::uuid
          AND status = 'running'
          AND lease_token = %s::uuid
        RETURNING id
        """,
        (reason[:2000], action_id, lease_token),
    )
    if cursor.fetchone() is None:
        return ProjectPaymentActionApplication(
            status=ProjectPaymentActionApplicationStatus.NOT_OWNER,
            action_id=action_id,
        )
    return ProjectPaymentActionApplication(
        status=ProjectPaymentActionApplicationStatus.BLOCKED,
        action_id=action_id,
        reason=reason,
    )


def _snapshot_value(snapshot: dict[str, Any], key: str) -> str | None:
    return _text_or_none(snapshot.get(key))


def project_payment_match_method_for_action(
    *,
    approved_by: str | None,
    rule_origin: object,
) -> ProjectPaymentMatchMethod:
    """Choose auditable allocation provenance from the immutable action path."""
    if approved_by is None:
        return ProjectPaymentMatchMethod.CONFIGURED_RULE
    if str(rule_origin) == AutomationRuleOrigin.LEARNED.value:
        return ProjectPaymentMatchMethod.LEARNED_SUGGESTION
    return ProjectPaymentMatchMethod.MANUAL


def apply_project_payment_automation_action(
    settings: SharedSettings,
    *,
    action_id: str,
    lease_token: str,
) -> ProjectPaymentActionApplication:
    """Apply one owned project-payment action behind all mutable-state fences.

    The action claim, current-rule fence, immutable ERP revision check, open
    project lock, allocation, notification outbox creation, and action success
    write occur in one transaction. This is the deterministic money boundary;
    Discord delivery remains a separate at-least-once side effect afterwards.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT action.id::text,
                       action.action_type,
                       action.payload,
                       action.mode AS action_mode,
                       action.disposition,
                       action.approved_by,
                       evaluation.rule_version,
                       evaluation.rule_project_id::text AS rule_project_id,
                       rule.version AS current_rule_version,
                       rule.mode AS current_rule_mode,
                       rule.origin AS current_rule_origin,
                       rule.enabled AS current_rule_enabled,
                       event.subject_id,
                       event.subject_revision AS event_subject_revision,
                       event.subject_snapshot
                FROM automation_actions action
                INNER JOIN automation_rule_evaluations evaluation
                    ON evaluation.id = action.rule_evaluation_id
                INNER JOIN automation_rules rule ON rule.id = evaluation.rule_id
                INNER JOIN automation_events event ON event.id = action.event_id
                WHERE action.id = %s::uuid
                  AND action.status = 'running'
                  AND action.lease_token = %s::uuid
                FOR UPDATE OF action, rule
                """,
                (action_id, lease_token),
            )
            action = cursor.fetchone()
            if action is None:
                return ProjectPaymentActionApplication(
                    status=ProjectPaymentActionApplicationStatus.NOT_OWNER,
                    action_id=action_id,
                )

            if str(action["action_type"]) != PROJECT_PAYMENT_ROUTE_ACTION:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="unregistered_project_payment_action_type",
                )

            approved_by = _text_or_none(action.get("approved_by"))
            action_mode = str(action["action_mode"])
            disposition = str(action["disposition"])
            if approved_by is None:
                if not (
                    action_mode == AutomationRuleMode.AUTOMATIC.value
                    and disposition == AutomationActionDisposition.READY.value
                    and bool(action["current_rule_enabled"])
                    and str(action["current_rule_mode"])
                    == AutomationRuleMode.AUTOMATIC.value
                    and int(action["rule_version"])
                    == int(action["current_rule_version"])
                ):
                    return _block_claimed_project_payment_action(
                        cursor,
                        action_id=action_id,
                        lease_token=lease_token,
                        reason="rule_version_superseded_or_not_automatic",
                    )
            elif disposition != AutomationActionDisposition.SUGGESTED.value:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="human_approval_requires_a_suggested_action",
                )

            payload = action.get("payload")
            payload = dict(payload) if isinstance(payload, dict) else {}
            project_id = _text_or_none(payload.get("project_id"))
            rule_project_id = _text_or_none(action.get("rule_project_id"))
            try:
                normalized_project_id = str(UUID(str(project_id)))
                normalized_rule_project_id = str(UUID(str(rule_project_id)))
            except (TypeError, ValueError, AttributeError):
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="project_payment_action_requires_a_project_scoped_rule",
                )
            if normalized_project_id != normalized_rule_project_id:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="project_payment_action_scope_mismatch",
                )

            snapshot_raw = action.get("subject_snapshot")
            snapshot = dict(snapshot_raw) if isinstance(snapshot_raw, dict) else {}
            snapshot_source = _snapshot_value(snapshot, "source")
            snapshot_external_id = _snapshot_value(snapshot, "external_id")
            snapshot_revision = _snapshot_value(snapshot, "source_revision")
            snapshot_amount = _decimal_or_none(snapshot.get("amount"))
            snapshot_direction = _snapshot_value(snapshot, "direction")
            snapshot_currency = _snapshot_value(snapshot, "currency")
            if (
                snapshot_source is None
                or snapshot_external_id is None
                or snapshot_amount is None
                or snapshot_amount <= 0
                or snapshot_direction is None
            ):
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_event_snapshot_is_incomplete",
                )
            if _text_or_none(action.get("event_subject_revision")) != snapshot_revision:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_event_snapshot_revision_mismatch",
                )
            if approved_by is None and snapshot_revision is None:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="automatic_payment_requires_a_canonical_erp_revision",
                )
            if approved_by is None and snapshot_currency is None:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="automatic_payment_requires_source_currency",
                )
            try:
                snapshot_payment_direction = PaymentDirection(snapshot_direction)
            except ValueError:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_event_snapshot_has_unknown_direction",
                )
            if snapshot_payment_direction is not PaymentDirection.INBOUND:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="outbound_transactions_cannot_route_project_payments",
                )
            configured_amount = payload.get("amount")
            allocation_amount = snapshot_amount
            if configured_amount is not None:
                expected_amount = _decimal_or_none(configured_amount)
                if expected_amount is None or expected_amount <= 0:
                    return _block_claimed_project_payment_action(
                        cursor,
                        action_id=action_id,
                        lease_token=lease_token,
                        reason="project_payment_action_amount_is_invalid",
                    )
                if approved_by is None and expected_amount != snapshot_amount:
                    return _block_claimed_project_payment_action(
                        cursor,
                        action_id=action_id,
                        lease_token=lease_token,
                        reason="automatic_payment_amount_must_equal_snapshot",
                    )
                allocation_amount = expected_amount

            subject_id = _text_or_none(action.get("subject_id"))
            if subject_id is None:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_event_has_no_transaction_subject",
                )
            cursor.execute(
                """
                SELECT id::text, source, external_id, source_revision, direction,
                       amount, currency
                FROM bank_transactions
                WHERE id = %s::uuid
                FOR UPDATE
                """,
                (subject_id,),
            )
            transaction = cursor.fetchone()
            if transaction is None:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_transaction_not_found",
                )
            current_revision = _text_or_none(transaction.get("source_revision"))
            if (
                str(transaction["source"]) != snapshot_source
                or str(transaction["external_id"]) != snapshot_external_id
                or current_revision != snapshot_revision
                or str(transaction["direction"]) != snapshot_payment_direction.value
                or Decimal(str(transaction["amount"])) != snapshot_amount
                or _text_or_none(transaction.get("currency")) != snapshot_currency
            ):
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="payment_source_revision_superseded",
                )

            cursor.execute(
                """
                SELECT id::text
                FROM projects
                WHERE id = %s::uuid
                  AND LOWER(COALESCE(source_status, '')) = 'open'
                FOR UPDATE
                """,
                (normalized_project_id,),
            )
            if cursor.fetchone() is None:
                return _block_claimed_project_payment_action(
                    cursor,
                    action_id=action_id,
                    lease_token=lease_token,
                    reason="project_is_not_open",
                )

            allocation_key = f"project-payment-route:v2:{action_id}"
            cursor.execute(
                """
                SELECT id::text
                FROM project_payment_allocations
                WHERE idempotency_key = %s
                """,
                (allocation_key,),
            )
            existing_allocation = cursor.fetchone()
            allocation_created = existing_allocation is None
            if existing_allocation is not None:
                allocation_id = str(existing_allocation["id"])
            else:
                cursor.execute(
                    """
                    SELECT id::text, amount
                    FROM project_payment_allocations
                    WHERE payment_transaction_id = %s::uuid
                      AND status = %s
                    FOR UPDATE
                    """,
                    (subject_id, ProjectPaymentAllocationStatus.CONFIRMED.value),
                )
                allocation_rows = cursor.fetchall()
                allocated_amount = sum(
                    (Decimal(str(row["amount"])) for row in allocation_rows),
                    Decimal("0"),
                )
                if approved_by is None and allocation_rows:
                    return _block_claimed_project_payment_action(
                        cursor,
                        action_id=action_id,
                        lease_token=lease_token,
                        reason="payment_revision_requires_manual_reconciliation",
                    )
                if allocated_amount + allocation_amount > snapshot_amount:
                    return _block_claimed_project_payment_action(
                        cursor,
                        action_id=action_id,
                        lease_token=lease_token,
                        reason="confirmed_allocations_exceed_payment_snapshot_amount",
                    )
                allocation_id = str(uuid4())
                cursor.execute(
                    """
                    INSERT INTO project_payment_allocations (
                        id,
                        payment_transaction_id,
                        project_id,
                        amount,
                        currency,
                        status,
                        match_method,
                        automation_action_id,
                        evidence,
                        idempotency_key
                    ) VALUES (
                        %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s::uuid, %s, %s
                    )
                    RETURNING id::text
                    """,
                    (
                        allocation_id,
                        subject_id,
                        normalized_project_id,
                        allocation_amount,
                        snapshot_currency,
                        ProjectPaymentAllocationStatus.CONFIRMED.value,
                        project_payment_match_method_for_action(
                            approved_by=approved_by,
                            rule_origin=action.get("current_rule_origin"),
                        ).value,
                        action_id,
                        Jsonb(
                            {
                                "automation_action_id": action_id,
                                "approved_by": approved_by,
                                "source_revision": snapshot_revision,
                                "event_snapshot": snapshot,
                            }
                        ),
                        allocation_key,
                    ),
                )
                created_allocation = cursor.fetchone()
                if created_allocation is None:
                    raise RuntimeError("Unable to create project payment allocation")
                allocation_id = str(created_allocation["id"])

            cursor.execute(
                """
                SELECT id::text, channel_id
                FROM project_discord_channels
                WHERE project_id = %s::uuid
                  AND active IS TRUE
                FOR SHARE
                """,
                (normalized_project_id,),
            )
            channels = cursor.fetchall()
            notification_ids: list[str] = []
            for channel in channels:
                notification_id = str(uuid4())
                project_discord_channel_id = str(channel["id"])
                notification_key = project_payment_notification_idempotency_key(
                    payment_transaction_id=subject_id,
                    allocation_id=allocation_id,
                    project_discord_channel_id=project_discord_channel_id,
                )
                cursor.execute(
                    """
                    INSERT INTO project_payment_notification_outbox (
                        id,
                        allocation_id,
                        project_discord_channel_id,
                        idempotency_key,
                        payload
                    ) VALUES (%s, %s::uuid, %s::uuid, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id::text
                    """,
                    (
                        notification_id,
                        allocation_id,
                        project_discord_channel_id,
                        notification_key,
                        Jsonb(
                            {
                                "automation_action_id": action_id,
                                "source_revision": snapshot_revision,
                            }
                        ),
                    ),
                )
                created_notification = cursor.fetchone()
                if created_notification is None:
                    cursor.execute(
                        """
                        SELECT id::text
                        FROM project_payment_notification_outbox
                        WHERE allocation_id = %s::uuid
                          AND project_discord_channel_id = %s::uuid
                        """,
                        (allocation_id, project_discord_channel_id),
                    )
                    created_notification = cursor.fetchone()
                if created_notification is None:
                    raise RuntimeError("Unable to persist project payment notification")
                notification_ids.append(str(created_notification["id"]))

            result = {
                "allocation_id": allocation_id,
                "allocation_created": allocation_created,
                "notification_ids": notification_ids,
                "source_revision": snapshot_revision,
            }
            cursor.execute(
                """
                UPDATE automation_actions
                SET status = 'succeeded',
                    result = %s,
                    last_error = NULL,
                    locked_at = NULL,
                    lease_token = NULL,
                    next_attempt_at = NULL,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND status = 'running'
                  AND lease_token = %s::uuid
                RETURNING id
                """,
                (Jsonb(result), action_id, lease_token),
            )
            if cursor.fetchone() is None:
                raise RuntimeError(
                    "Lost project payment action lease during application"
                )
    return ProjectPaymentActionApplication(
        status=ProjectPaymentActionApplicationStatus.APPLIED,
        action_id=action_id,
        allocation_id=allocation_id,
        allocation_created=allocation_created,
        notification_ids=tuple(notification_ids),
    )
