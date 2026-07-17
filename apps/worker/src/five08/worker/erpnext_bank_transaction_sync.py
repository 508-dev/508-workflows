"""ERPNext Bank Transaction ingestion into the local payment automation layer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from five08.automation import (
    AutomationEventInput,
    AutomationRuleMode,
    evaluate_automation_rules,
)
from five08.automation_store import (
    list_enabled_automation_rules,
    list_retryable_automation_action_ids_for_event,
    persist_automation_event_and_evaluation,
)
from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.project_payments import (
    BANK_TRANSACTION_POSTED_EVENT,
    PROJECT_PAYMENT_ALLOWED_ACTION_TYPES,
    PROJECT_PAYMENT_ALLOWED_FACT_PATHS,
    erpnext_bank_transaction_to_input,
    payment_automation_facts,
    payment_automation_subject_snapshot,
    project_payment_evaluation_for_execution,
    project_payment_rule_for_evaluation,
    upsert_bank_transaction,
)
from five08.settings import SharedSettings
from five08.worker.config import settings


logger = logging.getLogger(__name__)


class ERPNextBankTransactionProcessor:
    """Fetch canonical ERP transactions before they enter project automation."""

    def __init__(self, app_settings: SharedSettings = settings) -> None:
        self.settings = app_settings
        base_url = (app_settings.erpnext_base_url or "").strip()
        api_key = (app_settings.erpnext_api_key or "").strip()
        if not base_url or not api_key:
            raise ValueError("ERPNEXT_BASE_URL and ERPNEXT_API_KEY must be configured")
        self.client = ERPNextClient(
            base_url,
            api_key,
            timeout_seconds=app_settings.erpnext_api_timeout_seconds,
        )

    def ingest_bank_transaction(
        self,
        *,
        transaction_name: str,
        event_type: str = BANK_TRANSACTION_POSTED_EVENT,
        source_revision: str | None = None,
    ) -> dict[str, Any]:
        """Fetch, sanitize, project, and evaluate one submitted bank transaction."""
        normalized_name = transaction_name.strip()
        if not normalized_name:
            raise ValueError("transaction_name is required")
        if event_type != BANK_TRANSACTION_POSTED_EVENT:
            raise ValueError(f"Unsupported Bank Transaction event type: {event_type}")

        try:
            try:
                document = self.client.get_bank_transaction(normalized_name)
            except ERPNextAPIError as exc:
                if exc.status_code == 404:
                    return {
                        "status": "ignored",
                        "reason": "bank_transaction_not_found",
                        "transaction_name": normalized_name,
                    }
                raise

            transaction = erpnext_bank_transaction_to_input(document)
            if transaction is None:
                return {
                    "status": "ignored",
                    "reason": "bank_transaction_not_submitted_or_empty",
                    "transaction_name": normalized_name,
                }

            stored_transaction = upsert_bank_transaction(self.settings, transaction)
            if not stored_transaction.accepted:
                return {
                    "status": "ignored",
                    "reason": "bank_transaction_revision_superseded",
                    "transaction_name": normalized_name,
                    "transaction_id": stored_transaction.id,
                }
            occurred_at = transaction.posted_at or datetime.now(timezone.utc)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            # The canonical ERP document's ``modified`` field is the revision
            # identity. Webhook metadata is advisory and must never make a
            # stale fetch look like a fresh document revision.
            revision = transaction.source_revision
            event_key = (
                f"erpnext-bank-transaction:v1:{transaction.external_id}:"
                f"{revision or 'unversioned'}"
            )
            event = AutomationEventInput(
                event_id=event_key,
                event_type=event_type,
                source="erpnext",
                subject_id=stored_transaction.id,
                occurred_at=occurred_at,
                facts=payment_automation_facts(transaction),
                subject_revision=revision,
                subject_snapshot=payment_automation_subject_snapshot(transaction),
            )
            rules = list_enabled_automation_rules(
                self.settings,
                event_type=event_type,
            )
            evaluation_rules = [
                project_payment_rule_for_evaluation(rule) for rule in rules
            ]
            # A nonempty but unparsable ERPNext ``modified`` value cannot
            # establish revision ordering. Keep it reviewable for diagnosis,
            # but never emit an automatic money-routing action from it.
            if revision is None or transaction.source_modified_at is None:
                evaluation_rules = [
                    rule.model_copy(update={"mode": AutomationRuleMode.SUGGEST})
                    if rule.mode is AutomationRuleMode.AUTOMATIC
                    else rule
                    for rule in evaluation_rules
                ]
            downgraded_rule_ids = [
                original.id
                for original, evaluation_rule in zip(
                    rules, evaluation_rules, strict=True
                )
                if original.mode != evaluation_rule.mode
            ]
            if downgraded_rule_ids:
                logger.warning(
                    "Downgraded unsafe, unversioned, or unorderable automatic payment rule(s) to suggestions: %s",
                    downgraded_rule_ids,
                )
            evaluation = project_payment_evaluation_for_execution(
                evaluate_automation_rules(
                    event,
                    evaluation_rules,
                    allowed_fact_paths=PROJECT_PAYMENT_ALLOWED_FACT_PATHS,
                    allowed_action_types=PROJECT_PAYMENT_ALLOWED_ACTION_TYPES,
                )
            )
            stored_event, action_ids = persist_automation_event_and_evaluation(
                self.settings,
                event=event,
                event_key=event_key,
                evaluation=evaluation,
            )
            if not stored_event.created:
                action_ids = list_retryable_automation_action_ids_for_event(
                    self.settings,
                    event_id=stored_event.id,
                )

            logger.info(
                "Ingested ERPNext Bank Transaction name=%s local_id=%s event_created=%s actions=%s",
                normalized_name,
                stored_transaction.id,
                stored_event.created,
                len(action_ids),
            )
            return {
                "status": "ingested",
                "transaction_name": normalized_name,
                "transaction_id": stored_transaction.id,
                "transaction_created": stored_transaction.created,
                "event_id": stored_event.id,
                "event_created": stored_event.created,
                "action_ids": action_ids,
                "direction": transaction.direction.value,
            }
        finally:
            self.client.close()
