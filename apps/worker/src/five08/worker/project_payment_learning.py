"""Durably recover suggestion-only rules from approved payment feedback."""

from __future__ import annotations

import logging

from five08.automation_store import (
    create_automation_rule_if_absent,
    list_approved_automation_review_actions,
)
from five08.project_payments import (
    BANK_TRANSACTION_POSTED_EVENT,
    PROJECT_PAYMENT_ROUTE_ACTION,
    learned_project_payment_suggestion_rule,
    project_is_open,
)
from five08.settings import SharedSettings
from five08.worker.config import settings


logger = logging.getLogger(__name__)


def recover_project_payment_learning(
    app_settings: SharedSettings = settings,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Retry idempotent suggestion learning without replaying allocations.

    Feedback is durable before this function runs. Every attempt derives the
    same opaque rule id/key from its immutable ERP snapshot, so a scheduled
    retry can safely follow an API-process or database interruption.
    """
    reviews = list_approved_automation_review_actions(
        app_settings,
        event_type=BANK_TRANSACTION_POSTED_EVENT,
        action_type=PROJECT_PAYMENT_ROUTE_ACTION,
        limit=max(1, min(int(limit), 1000)),
    )
    result = {
        "attempted": len(reviews),
        "created": 0,
        "existing": 0,
        "ineligible": 0,
        "failed": 0,
    }
    for review in reviews:
        project_id = review.action.rule_project_id
        if project_id is None:
            result["ineligible"] += 1
            continue
        try:
            if not project_is_open(app_settings, project_id=project_id):
                result["ineligible"] += 1
                continue
        except Exception:
            result["failed"] += 1
            logger.exception(
                "Could not validate learned payment project action_id=%s",
                review.action.id,
            )
            continue
        rule = learned_project_payment_suggestion_rule(
            project_id=project_id,
            subject_snapshot=review.subject_snapshot,
        )
        if rule is None:
            result["ineligible"] += 1
            continue
        try:
            _stored_rule, created = create_automation_rule_if_absent(
                app_settings,
                rule=rule,
                created_by=f"system:project-payment-learning:{review.action.id}",
            )
        except Exception:
            result["failed"] += 1
            logger.exception(
                "Could not recover payment learning action_id=%s",
                review.action.id,
            )
            continue
        result["created" if created else "existing"] += 1
    return result
