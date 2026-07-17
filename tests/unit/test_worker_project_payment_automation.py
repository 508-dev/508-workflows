"""Tests for durable execution of typed project-payment routing actions."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from five08.automation_store import AutomationActionStatus, StoredAutomationAction
from five08.project_payments import (
    ProjectPaymentActionApplication,
    ProjectPaymentActionApplicationStatus,
    ProjectPaymentAllocationStatus,
    ProjectPaymentNotification,
    ProjectPaymentNotificationStatus,
)
from five08.worker import jobs as worker_jobs
from five08.worker import project_payment_automation as payment_automation


def _executor(
    *,
    automation_enabled: bool = True,
    notifications_enabled: bool = False,
):
    executor = payment_automation.ProjectPaymentActionExecutor.__new__(
        payment_automation.ProjectPaymentActionExecutor
    )
    executor.settings = SimpleNamespace(
        project_payment_automation_enabled=automation_enabled,
        project_payment_notifications_enabled=notifications_enabled,
        api_shared_secret="secret",
        discord_bot_internal_base_url="http://discord-bot.internal",
        erpnext_base_url="https://erp.example.test",
        erpnext_api_key="key:secret",
        erpnext_api_timeout_seconds=20.0,
    )
    return executor


def _action() -> StoredAutomationAction:
    return StoredAutomationAction(
        id="action-uuid",
        event_id="event-uuid",
        action_type="project_payment.route",
        payload={"project_id": "00000000-0000-0000-0000-000000000001"},
        mode="automatic",
        disposition="ready",
        status=AutomationActionStatus.RUNNING,
        attempts=1,
        idempotency_key="action-key",
        lease_token="00000000-0000-0000-0000-000000000010",
        approved_by=None,
        rule_project_id="00000000-0000-0000-0000-000000000001",
    )


def _notification() -> ProjectPaymentNotification:
    return ProjectPaymentNotification(
        id="notification-uuid",
        allocation_id="allocation-uuid",
        project_id="00000000-0000-0000-0000-000000000001",
        channel_id="discord-channel",
        payload={},
        status=ProjectPaymentNotificationStatus.SENDING,
        attempts=1,
        lease_token="00000000-0000-0000-0000-000000000020",
    )


def test_project_payment_action_is_disabled_without_claiming(monkeypatch) -> None:
    executor = _executor(automation_enabled=False)
    monkeypatch.setattr(
        payment_automation,
        "claim_automation_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not claim")
        ),
    )

    assert executor.execute_action("action-uuid") == {
        "status": "disabled",
        "action_id": "action-uuid",
    }


def test_project_payment_action_uses_atomic_application_boundary(monkeypatch) -> None:
    executor = _executor(notifications_enabled=False)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "claim_automation_action",
        lambda *_args, **_kwargs: _action(),
    )
    monkeypatch.setattr(
        executor, "_refresh_project_from_erpnext", lambda _project_id: None
    )
    monkeypatch.setattr(
        executor, "_refresh_bank_transaction_from_erpnext", lambda _action_id: None
    )
    monkeypatch.setattr(
        payment_automation,
        "apply_project_payment_automation_action",
        lambda *_args, **kwargs: (
            captured.update(kwargs)
            or ProjectPaymentActionApplication(
                status=ProjectPaymentActionApplicationStatus.APPLIED,
                action_id="action-uuid",
                allocation_id="allocation-uuid",
                allocation_created=True,
                notification_ids=("notification-1",),
            )
        ),
    )

    result = executor.execute_action("action-uuid")

    assert captured == {
        "action_id": "action-uuid",
        "lease_token": "00000000-0000-0000-0000-000000000010",
    }
    assert result == {
        "status": "succeeded",
        "action_id": "action-uuid",
        "allocation_id": "allocation-uuid",
        "allocation_created": True,
        "notification_ids": ["notification-1"],
        "delivered_notification_ids": [],
    }


def test_project_payment_action_reports_atomic_policy_block(monkeypatch) -> None:
    executor = _executor()
    monkeypatch.setattr(
        payment_automation,
        "claim_automation_action",
        lambda *_args, **_kwargs: _action(),
    )
    monkeypatch.setattr(
        executor, "_refresh_project_from_erpnext", lambda _project_id: None
    )
    monkeypatch.setattr(
        executor, "_refresh_bank_transaction_from_erpnext", lambda _action_id: None
    )
    monkeypatch.setattr(
        payment_automation,
        "apply_project_payment_automation_action",
        lambda *_args, **_kwargs: ProjectPaymentActionApplication(
            status=ProjectPaymentActionApplicationStatus.BLOCKED,
            action_id="action-uuid",
            reason="payment_source_revision_superseded",
        ),
    )

    assert executor.execute_action("action-uuid") == {
        "status": "blocked",
        "action_id": "action-uuid",
        "reason": "payment_source_revision_superseded",
    }


def test_project_payment_action_marks_unregistered_action_dead_with_its_lease(
    monkeypatch,
) -> None:
    executor = _executor()
    action = _action()
    action = action.__class__(**{**action.__dict__, "action_type": "unknown.action"})
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "claim_automation_action",
        lambda *_args, **_kwargs: action,
    )
    monkeypatch.setattr(
        payment_automation,
        "mark_automation_action_failed",
        lambda *_args, **kwargs: captured.update(kwargs) or True,
    )

    result = executor.execute_action("action-uuid")

    assert result["status"] == "ignored"
    assert captured["dead"] is True
    assert captured["lease_token"] == action.lease_token


def test_notification_delivery_posts_only_canonical_notification_id(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    delivered: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "block_project_payment_notification_if_ineligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        payment_automation,
        "claim_project_payment_notification",
        lambda *_args, **_kwargs: _notification(),
    )
    monkeypatch.setattr(
        executor,
        "_notification_source_block_reason",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "_refresh_project_from_erpnext",
        lambda _project_id: None,
    )

    class _Client:
        status_code = None

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def post_project_payment_notification(self, **kwargs):
            delivered["request"] = kwargs
            return {"message_id": "discord-message"}

    monkeypatch.setattr(payment_automation, "DiscordBotClient", _Client)
    monkeypatch.setattr(
        payment_automation,
        "mark_project_payment_notification_sent",
        lambda *_args, **kwargs: delivered.update(kwargs) or True,
    )

    result = executor.deliver_notification("notification-uuid")

    assert result["status"] == "sent"
    assert delivered["request"] == {
        "notification_id": "notification-uuid",
        "lease_token": "00000000-0000-0000-0000-000000000020",
    }
    assert delivered["lease_token"] == "00000000-0000-0000-0000-000000000020"


def test_notification_delivery_blocks_bot_policy_rejection_with_worker_lease(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    failed: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "block_project_payment_notification_if_ineligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        payment_automation,
        "claim_project_payment_notification",
        lambda *_args, **_kwargs: _notification(),
    )
    monkeypatch.setattr(
        executor,
        "_notification_source_block_reason",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        executor,
        "_refresh_project_from_erpnext",
        lambda _project_id: None,
    )

    class _Client:
        status_code = 409

        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def post_project_payment_notification(self, **_kwargs):
            raise payment_automation.DiscordBotAPIError("mapping revoked")

    monkeypatch.setattr(payment_automation, "DiscordBotClient", _Client)
    monkeypatch.setattr(
        payment_automation,
        "mark_project_payment_notification_failed",
        lambda *_args, **kwargs: failed.update(kwargs) or True,
    )

    result = executor.deliver_notification("notification-uuid")

    assert result["status"] == "blocked"
    assert failed["blocked"] is True
    assert failed["lease_token"] == "00000000-0000-0000-0000-000000000020"


def test_notification_delivery_blocks_a_superseded_source_before_discord(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    failed: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "block_project_payment_notification_if_ineligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        payment_automation,
        "claim_project_payment_notification",
        lambda *_args, **_kwargs: _notification(),
    )
    monkeypatch.setattr(
        executor,
        "_notification_source_block_reason",
        lambda **_kwargs: "payment_source_revision_superseded",
    )
    monkeypatch.setattr(
        executor,
        "_refresh_project_from_erpnext",
        lambda _project_id: None,
    )
    monkeypatch.setattr(
        payment_automation,
        "mark_project_payment_notification_failed",
        lambda *_args, **kwargs: failed.update(kwargs) or True,
    )
    monkeypatch.setattr(
        payment_automation,
        "DiscordBotClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not send a superseded payment")
        ),
    )

    result = executor.deliver_notification("notification-uuid")

    assert result == {
        "status": "blocked",
        "notification_id": "notification-uuid",
        "reason": "payment_source_revision_superseded",
    }
    assert failed["blocked"] is True
    assert failed["lease_token"] == "00000000-0000-0000-0000-000000000020"


def test_notification_delivery_blocks_a_closed_erp_project_before_discord(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    failed: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "block_project_payment_notification_if_ineligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        payment_automation,
        "claim_project_payment_notification",
        lambda *_args, **_kwargs: _notification(),
    )
    monkeypatch.setattr(
        executor,
        "_refresh_project_from_erpnext",
        lambda _project_id: (_ for _ in ()).throw(
            payment_automation.ProjectPaymentActionPreflightBlocked(
                "payment_project_is_not_open_in_erpnext"
            )
        ),
    )
    monkeypatch.setattr(
        payment_automation,
        "mark_project_payment_notification_failed",
        lambda *_args, **kwargs: failed.update(kwargs) or True,
    )
    monkeypatch.setattr(
        executor,
        "_notification_source_block_reason",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not check a closed project payment")
        ),
    )

    result = executor.deliver_notification("notification-uuid")

    assert result == {
        "status": "blocked",
        "notification_id": "notification-uuid",
        "reason": "payment_project_is_not_open_in_erpnext",
    }
    assert failed["blocked"] is True
    assert failed["lease_token"] == "00000000-0000-0000-0000-000000000020"


def test_notification_delivery_retries_when_bot_configuration_is_repaired(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    executor.settings.discord_bot_internal_base_url = ""
    failed: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "block_project_payment_notification_if_ineligible",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        payment_automation,
        "claim_project_payment_notification",
        lambda *_args, **_kwargs: _notification(),
    )
    monkeypatch.setattr(
        payment_automation,
        "mark_project_payment_notification_failed",
        lambda *_args, **kwargs: failed.update(kwargs) or True,
    )

    result = executor.deliver_notification("notification-uuid")

    assert result == {
        "status": "failed",
        "notification_id": "notification-uuid",
        "reason": "discord_bot_not_configured",
    }
    assert "blocked" not in failed
    assert failed["lease_token"] == "00000000-0000-0000-0000-000000000020"


def test_payment_action_refreshes_the_current_erp_project_before_allocation(
    monkeypatch,
) -> None:
    executor = _executor()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "erpnext_project_external_id",
        lambda *_args, **_kwargs: "PROJ-001",
    )

    class _ERPClient:
        def __init__(self, base_url, api_key, *, timeout_seconds) -> None:
            captured.update(
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout_seconds": timeout_seconds,
                }
            )

        def get_project(self, external_id: str) -> dict[str, str]:
            captured["external_id"] = external_id
            return {"name": external_id, "project_name": "Current", "status": "Open"}

        def close(self) -> None:
            captured["closed"] = True

    payload = SimpleNamespace(
        external_id="PROJ-001",
        source_status="Open",
        source_modified_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(payment_automation, "ERPNextClient", _ERPClient)
    monkeypatch.setattr(
        payment_automation, "erpnext_project_to_input", lambda document: payload
    )
    monkeypatch.setattr(
        payment_automation,
        "upsert_project",
        lambda _settings, received_payload: (
            captured.update({"payload": received_payload})
            or "00000000-0000-0000-0000-000000000001"
        ),
    )

    executor._refresh_project_from_erpnext("00000000-0000-0000-0000-000000000001")

    assert captured["external_id"] == "PROJ-001"
    assert captured["payload"] is payload
    assert captured["closed"] is True


@pytest.mark.parametrize(
    ("source_status", "source_modified_at", "reason"),
    [
        (
            "Completed",
            datetime(2026, 7, 16, tzinfo=timezone.utc),
            "payment_project_is_not_open_in_erpnext",
        ),
        ("Open", None, "payment_project_has_no_canonical_revision"),
    ],
)
def test_payment_action_fails_closed_for_noncurrent_erp_project(
    monkeypatch,
    source_status: str,
    source_modified_at: datetime | None,
    reason: str,
) -> None:
    executor = _executor()
    monkeypatch.setattr(
        payment_automation,
        "erpnext_project_external_id",
        lambda *_args, **_kwargs: "PROJ-001",
    )

    class _ERPClient:
        def get_project(self, _external_id: str) -> dict[str, object]:
            return {"name": "PROJ-001"}

        def close(self) -> None:
            return None

    monkeypatch.setattr(executor, "_erpnext_client", lambda: _ERPClient())
    monkeypatch.setattr(
        payment_automation,
        "erpnext_project_to_input",
        lambda _document: SimpleNamespace(
            external_id="PROJ-001",
            source_status=source_status,
            source_modified_at=source_modified_at,
        ),
    )
    monkeypatch.setattr(
        payment_automation,
        "upsert_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not cache a noncurrent project")
        ),
    )

    with pytest.raises(payment_automation.ProjectPaymentActionPreflightBlocked) as exc:
        executor._refresh_project_from_erpnext("00000000-0000-0000-0000-000000000001")

    assert str(exc.value) == reason


def test_payment_action_rechecks_canonical_bank_transaction_before_allocation(
    monkeypatch,
) -> None:
    executor = _executor()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "automation_action_subject_id",
        lambda *_args, **_kwargs: "transaction-uuid",
    )
    monkeypatch.setattr(
        payment_automation,
        "get_bank_transaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="transaction-uuid",
            source="erpnext",
            external_id="ACC-BTN-0001",
            source_revision="2026-07-16T12:34:56+00:00",
        ),
    )

    class _ERPClient:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def get_bank_transaction(self, external_id: str) -> dict[str, object]:
            captured["external_id"] = external_id
            return {
                "name": external_id,
                "docstatus": 1,
                "deposit": "1250.00",
                "withdrawal": "0",
                "modified": "2026-07-16T12:34:56+00:00",
            }

        def close(self) -> None:
            captured["closed"] = True

    normalized = SimpleNamespace(
        external_id="ACC-BTN-0001",
        source_revision="2026-07-16T12:34:56+00:00",
        source_modified_at=datetime(2026, 7, 16, 12, 34, 56, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(payment_automation, "ERPNextClient", _ERPClient)
    monkeypatch.setattr(
        payment_automation,
        "erpnext_bank_transaction_to_input",
        lambda document: normalized,
    )
    monkeypatch.setattr(
        payment_automation,
        "upsert_bank_transaction",
        lambda _settings, transaction: (
            captured.update({"transaction": transaction})
            or SimpleNamespace(id="transaction-uuid", accepted=True)
        ),
    )

    executor._refresh_bank_transaction_from_erpnext("action-uuid")

    assert captured == {
        "external_id": "ACC-BTN-0001",
        "closed": True,
        "transaction": normalized,
    }


@pytest.mark.parametrize(
    ("canonical_revision", "source_modified_at", "upsert_accepted", "reason"),
    [
        (None, None, True, "bank_transaction_has_no_canonical_revision"),
        (
            "not-an-erpnext-timestamp",
            None,
            True,
            "bank_transaction_has_no_canonical_revision",
        ),
        (
            "2026-07-16T12:34:56+00:00",
            datetime(2026, 7, 16, 12, 34, 56, tzinfo=timezone.utc),
            False,
            "bank_transaction_revision_refresh_was_not_accepted",
        ),
    ],
)
def test_payment_action_fails_closed_when_canonical_revision_cannot_be_persisted(
    monkeypatch,
    canonical_revision: str | None,
    source_modified_at: datetime | None,
    upsert_accepted: bool,
    reason: str,
) -> None:
    executor = _executor()
    monkeypatch.setattr(
        payment_automation,
        "automation_action_subject_id",
        lambda *_args, **_kwargs: "transaction-uuid",
    )
    monkeypatch.setattr(
        payment_automation,
        "get_bank_transaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="transaction-uuid",
            source="erpnext",
            external_id="ACC-BTN-0001",
            source_revision="2026-07-16T12:34:56+00:00",
        ),
    )

    class _ERPClient:
        def get_bank_transaction(self, _external_id: str) -> dict[str, object]:
            return {"name": "ACC-BTN-0001", "docstatus": 1}

        def close(self) -> None:
            return None

    monkeypatch.setattr(executor, "_erpnext_client", lambda: _ERPClient())
    monkeypatch.setattr(
        payment_automation,
        "erpnext_bank_transaction_to_input",
        lambda _document: SimpleNamespace(
            external_id="ACC-BTN-0001",
            source_revision=canonical_revision,
            source_modified_at=source_modified_at,
        ),
    )
    monkeypatch.setattr(
        payment_automation,
        "upsert_bank_transaction",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="transaction-uuid",
            accepted=upsert_accepted,
        ),
    )

    with pytest.raises(payment_automation.ProjectPaymentActionPreflightBlocked) as exc:
        executor._refresh_bank_transaction_from_erpnext("action-uuid")

    assert str(exc.value) == reason


def test_notification_source_recheck_blocks_a_cancelled_canonical_document(
    monkeypatch,
) -> None:
    executor = _executor(notifications_enabled=True)
    captured: dict[str, object] = {}
    source_context = SimpleNamespace(
        notification_id="notification-uuid",
        bank_transaction_id="transaction-uuid",
        source="erpnext",
        external_id="ACC-BTN-0001",
        allocation_status=ProjectPaymentAllocationStatus.CONFIRMED,
        allocation_source_revision="2026-07-16T12:34:56+00:00",
        current_source_revision="2026-07-16T12:34:56+00:00",
    )
    monkeypatch.setattr(
        payment_automation,
        "get_project_payment_notification_source_context",
        lambda *_args, **kwargs: captured.update(kwargs) or source_context,
    )

    class _ERPClient:
        def get_bank_transaction(self, external_id: str) -> dict[str, object]:
            captured["external_id"] = external_id
            return {"name": external_id, "docstatus": 2}

        def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(executor, "_erpnext_client", lambda: _ERPClient())
    monkeypatch.setattr(
        payment_automation,
        "erpnext_bank_transaction_to_input",
        lambda _document: None,
    )

    result = executor._notification_source_block_reason(
        notification_id="notification-uuid",
        lease_token="00000000-0000-0000-0000-000000000020",
    )

    assert result == "bank_transaction_is_no_longer_submitted"
    assert captured == {
        "notification_id": "notification-uuid",
        "lease_token": "00000000-0000-0000-0000-000000000020",
        "external_id": "ACC-BTN-0001",
        "closed": True,
    }


def test_payment_action_blocks_when_canonical_bank_transaction_is_cancelled(
    monkeypatch,
) -> None:
    executor = _executor()
    action = _action()
    failed: dict[str, object] = {}
    monkeypatch.setattr(
        payment_automation,
        "claim_automation_action",
        lambda *_args, **_kwargs: action,
    )
    monkeypatch.setattr(
        executor,
        "_refresh_bank_transaction_from_erpnext",
        lambda _action_id: (_ for _ in ()).throw(
            payment_automation.ProjectPaymentActionPreflightBlocked(
                "bank_transaction_is_no_longer_submitted"
            )
        ),
    )
    monkeypatch.setattr(
        payment_automation,
        "mark_automation_action_failed",
        lambda *_args, **kwargs: failed.update(kwargs) or True,
    )
    monkeypatch.setattr(
        payment_automation,
        "apply_project_payment_automation_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not allocate a canceled transaction")
        ),
    )

    result = executor.execute_action("action-uuid")

    assert result == {
        "status": "blocked",
        "action_id": "action-uuid",
        "reason": "bank_transaction_is_no_longer_submitted",
    }
    assert failed["dead"] is True
    assert failed["lease_token"] == action.lease_token


def test_payment_recovery_does_nothing_when_automation_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_jobs.settings, "project_payment_automation_enabled", False
    )
    monkeypatch.setattr(
        worker_jobs,
        "list_pending_automation_action_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not list")
        ),
    )

    assert worker_jobs.recover_project_payment_automation_job() == {
        "status": "disabled"
    }


def test_payment_recovery_executes_actions_and_retries_notifications_when_enabled(
    monkeypatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class _RecoveryExecutor:
        def execute_action(self, action_id: str) -> dict[str, str]:
            calls.append(("action", action_id))
            return {"status": "succeeded", "action_id": action_id}

        def deliver_notification(self, notification_id: str) -> dict[str, str]:
            calls.append(("notification", notification_id))
            return {"status": "sent", "notification_id": notification_id}

    monkeypatch.setattr(
        worker_jobs.settings, "project_payment_automation_enabled", True
    )
    monkeypatch.setattr(
        worker_jobs.settings, "project_payment_notifications_enabled", True
    )
    monkeypatch.setattr(
        worker_jobs,
        "ProjectPaymentActionExecutor",
        lambda: _RecoveryExecutor(),
    )
    monkeypatch.setattr(
        worker_jobs,
        "list_pending_automation_action_ids",
        lambda *_args, **_kwargs: ["stale-action"],
    )
    monkeypatch.setattr(
        worker_jobs,
        "list_retryable_project_payment_notification_ids",
        lambda *_args, **_kwargs: ["stale-notification"],
    )
    monkeypatch.setattr(
        worker_jobs,
        "recover_project_payment_learning",
        lambda *_args, **_kwargs: {
            "attempted": 1,
            "created": 1,
            "existing": 0,
            "ineligible": 0,
            "failed": 0,
        },
    )

    result = worker_jobs.recover_project_payment_automation_job()

    assert result["status"] == "recovered"
    assert calls == [("action", "stale-action"), ("notification", "stale-notification")]
    assert result["learning"] == {
        "attempted": 1,
        "created": 1,
        "existing": 0,
        "ineligible": 0,
        "failed": 0,
    }
