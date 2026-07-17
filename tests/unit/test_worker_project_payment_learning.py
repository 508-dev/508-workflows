"""Tests for durable recovery of feedback-derived payment suggestions."""

from __future__ import annotations

from five08.automation_store import (
    AutomationActionStatus,
    StoredAutomationAction,
    StoredAutomationReviewAction,
    StoredAutomationRule,
)
from five08.worker import project_payment_learning as payment_learning


PROJECT_ID = "00000000-0000-0000-0000-000000000001"
ACTION_ID = "00000000-0000-0000-0000-000000000002"


def _review(
    *, snapshot: dict[str, object] | None = None
) -> StoredAutomationReviewAction:
    return StoredAutomationReviewAction(
        action=StoredAutomationAction(
            id=ACTION_ID,
            event_id="00000000-0000-0000-0000-000000000003",
            action_type="project_payment.route",
            payload={"project_id": PROJECT_ID},
            mode="suggest",
            disposition="suggested",
            status=AutomationActionStatus.APPROVED,
            attempts=0,
            idempotency_key="action-key",
            lease_token=None,
            approved_by="discord:admin-1",
            rule_project_id=PROJECT_ID,
        ),
        subject_id="00000000-0000-0000-0000-000000000004",
        subject_snapshot=snapshot
        or {
            "direction": "inbound",
            "currency": "GBP",
            "counterparty": "Acme Ltd",
        },
    )


def test_recovery_derives_a_missing_suggestion_rule_once(monkeypatch) -> None:
    review = _review()
    rule = payment_learning.learned_project_payment_suggestion_rule(
        project_id=PROJECT_ID,
        subject_snapshot=review.subject_snapshot,
    )
    assert rule is not None
    monkeypatch.setattr(
        payment_learning,
        "list_approved_automation_review_actions",
        lambda *_args, **_kwargs: [review],
    )
    monkeypatch.setattr(
        payment_learning, "project_is_open", lambda *_args, **_kwargs: True
    )
    captured: dict[str, object] = {}

    def create(*args, **kwargs):  # noqa: ANN001, ANN202
        captured["args"] = args
        captured["kwargs"] = kwargs
        return StoredAutomationRule(rule=rule, created_by="system"), True

    monkeypatch.setattr(payment_learning, "create_automation_rule_if_absent", create)

    result = payment_learning.recover_project_payment_learning(
        payment_learning.SharedSettings(),
    )

    assert result == {
        "attempted": 1,
        "created": 1,
        "existing": 0,
        "ineligible": 0,
        "failed": 0,
    }
    assert captured["kwargs"] == {
        "rule": rule,
        "created_by": f"system:project-payment-learning:{ACTION_ID}",
    }


def test_recovery_skips_feedback_without_safe_identity_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_learning,
        "list_approved_automation_review_actions",
        lambda *_args, **_kwargs: [
            _review(snapshot={"direction": "inbound", "currency": "GBP"})
        ],
    )
    monkeypatch.setattr(
        payment_learning, "project_is_open", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        payment_learning,
        "create_automation_rule_if_absent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not create")
        ),
    )

    result = payment_learning.recover_project_payment_learning(
        payment_learning.SharedSettings(),
    )

    assert result == {
        "attempted": 1,
        "created": 0,
        "existing": 0,
        "ineligible": 1,
        "failed": 0,
    }


def test_recovery_skips_closed_project_feedback(monkeypatch) -> None:
    monkeypatch.setattr(
        payment_learning,
        "list_approved_automation_review_actions",
        lambda *_args, **_kwargs: [_review()],
    )
    monkeypatch.setattr(
        payment_learning, "project_is_open", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        payment_learning,
        "create_automation_rule_if_absent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not create")
        ),
    )

    result = payment_learning.recover_project_payment_learning(
        payment_learning.SharedSettings(),
    )

    assert result == {
        "attempted": 1,
        "created": 0,
        "existing": 0,
        "ineligible": 1,
        "failed": 0,
    }


def test_recovery_records_one_failure_without_aborting_other_feedback(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        payment_learning,
        "list_approved_automation_review_actions",
        lambda *_args, **_kwargs: [_review()],
    )
    monkeypatch.setattr(
        payment_learning, "project_is_open", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        payment_learning,
        "create_automation_rule_if_absent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("temporary db failure")
        ),
    )

    result = payment_learning.recover_project_payment_learning(
        payment_learning.SharedSettings(),
    )

    assert result == {
        "attempted": 1,
        "created": 0,
        "existing": 0,
        "ineligible": 0,
        "failed": 1,
    }
