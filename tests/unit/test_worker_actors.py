"""Unit tests for worker actor job state transitions."""

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from five08.queue import JobRecord, JobStatus
from five08.worker import actors
from five08.worker.crm.docuseal_processor import (
    DocusealAgreementNonRetryableError,
    DocusealAgreementProcessingError,
)


def test_run_job_schedules_retry_for_docuseal_processing_error() -> None:
    """Retryable Docuseal failures should be recorded as failed + retried."""
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-123",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={
            "args": ["member@508.dev", "2026-02-25 12:00:00", 42],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=None,
        locked_by="worker-1:claim-123",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _raise_docuseal_processing_error(*args: object, **kwargs: object) -> None:
        raise DocusealAgreementProcessingError("CRM unavailable")

    with (
        patch(
            "five08.worker.actors.claim_job_for_execution", return_value=job
        ) as mock_claim,
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch("five08.worker.actors._schedule_job_lease_recovery"),
        patch(
            "five08.worker.actors._renew_job_lease_while_running",
            return_value=nullcontext(),
        ) as mock_heartbeat,
        patch.dict(
            actors._HANDLERS,
            {"process_docuseal_agreement_job": _raise_docuseal_processing_error},
            clear=False,
        ),
    ):
        actors._run_job("job-123")

    mock_claim.assert_called_once()
    mock_mark_succeeded.assert_not_called()
    mock_mark_dead.assert_not_called()
    mock_schedule_retry.assert_called_once()
    call_args = mock_schedule_retry.call_args
    assert isinstance(call_args.args[0], JobRecord)
    assert call_args.args[0].id == "job-123"
    assert call_args.args[1] == 1
    assert (
        "DocusealAgreementProcessingError: CRM unavailable" == call_args.kwargs["error"]
    )
    mock_heartbeat.assert_called_once_with("job-123", "worker-1:claim-123")


def test_run_job_marks_dead_for_non_retryable_docuseal_error() -> None:
    """Non-retryable Docuseal failures should be marked dead immediately."""
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-124",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={
            "args": ["member@508.dev", "not-a-date", 42],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=None,
        locked_by="worker-1:claim-124",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _raise_docuseal_non_retryable_error(*args: object, **kwargs: object) -> None:
        raise DocusealAgreementNonRetryableError(
            "invalid_completed_at for contact_id=c-1"
        )

    with (
        patch(
            "five08.worker.actors.claim_job_for_execution", return_value=job
        ) as mock_claim,
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch("five08.worker.actors._schedule_job_lease_recovery"),
        patch.dict(
            actors._HANDLERS,
            {"process_docuseal_agreement_job": _raise_docuseal_non_retryable_error},
            clear=False,
        ),
    ):
        actors._run_job("job-124")

    mock_claim.assert_called_once()
    mock_mark_succeeded.assert_not_called()
    mock_schedule_retry.assert_not_called()
    mock_mark_dead.assert_called_once()
    call_args = mock_mark_dead.call_args
    assert call_args.args[1] == "job-124"
    assert call_args.kwargs["attempts"] == 1
    assert (
        call_args.kwargs["last_error"]
        == "DocusealAgreementNonRetryableError: invalid_completed_at for contact_id=c-1"
    )


def test_run_job_executes_only_one_duplicate_broker_delivery() -> None:
    """A second message cannot bypass the persisted execution claim."""

    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-125",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={"args": ["member@508.dev", "2026-02-25 12:00:00", 42], "kwargs": {}},
        idempotency_key="docuseal:125",
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=now,
        locked_by="worker-1",
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    handler = Mock(return_value={"ok": True})

    with (
        patch(
            "five08.worker.actors.claim_job_for_execution",
            side_effect=[job, None],
        ) as mock_claim,
        patch("five08.worker.actors._requeue_running_job_after_lease"),
        patch("five08.worker.actors._schedule_job_lease_recovery") as mock_recovery,
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch.dict(
            actors._HANDLERS,
            {"process_docuseal_agreement_job": handler},
            clear=False,
        ),
    ):
        actors._run_job("job-125")
        actors._run_job("job-125")

    assert mock_claim.call_count == 2
    handler.assert_called_once_with("member@508.dev", "2026-02-25 12:00:00", 42)
    mock_mark_succeeded.assert_called_once()
    mock_recovery.assert_called_once_with("job-125", delay_seconds=600)


def test_run_job_requeues_duplicate_delivery_when_a_lease_is_still_fresh() -> None:
    """An acknowledged duplicate leaves a recovery delivery at lease expiry."""

    locked_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    job = JobRecord(
        id="job-lease",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={"args": [], "kwargs": {}},
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=locked_at,
        locked_by="worker-1:claim-lease",
        last_error=None,
        created_at=locked_at,
        updated_at=locked_at,
    )

    with (
        patch("five08.worker.actors.claim_job_for_execution", return_value=None),
        patch("five08.worker.actors.get_job", return_value=job),
        patch("five08.worker.actors.datetime") as mock_datetime,
        patch.object(actors.execute_job, "send_with_options") as mock_send,
    ):
        mock_datetime.now.return_value = locked_at
        actors._run_job("job-lease")

    mock_send.assert_called_once_with(args=("job-lease",), delay=600_000)


def test_job_lease_heartbeat_renews_the_current_claim() -> None:
    """A long-running handler keeps its claim fresh until it exits."""

    stop_event = Mock()
    stop_event.wait.side_effect = [False, True]
    lease_lost = Mock()
    lease_lost.is_set.return_value = False

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()  # type: ignore[operator]

        def join(self, *, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    with (
        patch("five08.worker.actors.Event", side_effect=[stop_event, lease_lost]),
        patch("five08.worker.actors.Thread", InlineThread),
        patch(
            "five08.worker.actors.renew_job_execution_lease",
            return_value=True,
        ) as mock_renew,
    ):
        with actors._renew_job_lease_while_running("job-heartbeat", "worker-1:claim"):
            pass

    mock_renew.assert_called_once_with(
        actors.settings,
        "job-heartbeat",
        claim_token="worker-1:claim",
    )
    stop_event.set.assert_called_once()
    lease_lost.set.assert_not_called()


def test_job_lease_heartbeat_fences_completion_after_a_renewal_failure() -> None:
    """A failed renewal cannot let the claimed worker record a stale outcome."""

    stop_event = Mock()
    stop_event.wait.return_value = False
    stop_event.is_set.return_value = False
    lease_lost = Mock()
    lease_lost.is_set.return_value = True

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()  # type: ignore[operator]

        def join(self, *, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    with (
        patch("five08.worker.actors.Event", side_effect=[stop_event, lease_lost]),
        patch("five08.worker.actors.Thread", InlineThread),
        patch("five08.worker.actors.get_ident", return_value=1234),
        patch("five08.worker.actors.renew_job_execution_lease", return_value=False),
        patch("five08.worker.actors.raise_thread_exception") as mock_interrupt,
        pytest.raises(actors.JobLeaseLostError),
    ):
        with actors._renew_job_lease_while_running("job-heartbeat", "worker-1:claim"):
            pass

    lease_lost.set.assert_called_once()
    stop_event.set.assert_called_once()
    mock_interrupt.assert_called_once_with(1234, actors.JobLeaseLostError)


def test_job_lease_heartbeat_retries_a_transient_renewal_error() -> None:
    """A temporary database error does not discard a still-valid lease early."""

    stop_event = Mock()
    stop_event.wait.side_effect = [False, False, True]
    lease_lost = Mock()
    lease_lost.is_set.return_value = False

    class InlineThread:
        def __init__(self, *, target: object, **_kwargs: object) -> None:
            self._target = target

        def start(self) -> None:
            self._target()  # type: ignore[operator]

        def join(self, *, timeout: float | None = None) -> None:
            del timeout

        def is_alive(self) -> bool:
            return False

    with (
        patch("five08.worker.actors.Event", side_effect=[stop_event, lease_lost]),
        patch("five08.worker.actors.Thread", InlineThread),
        patch(
            "five08.worker.actors.renew_job_execution_lease",
            side_effect=[RuntimeError("temporary outage"), True],
        ) as mock_renew,
        patch("five08.worker.actors.raise_thread_exception") as mock_interrupt,
    ):
        with actors._renew_job_lease_while_running("job-heartbeat", "worker-1:claim"):
            pass

    assert mock_renew.call_count == 2
    lease_lost.set.assert_not_called()
    mock_interrupt.assert_not_called()


def test_execute_job_does_not_persist_success_after_a_lost_lease() -> None:
    """The lease guard stops the stale actor before it can finish the job row."""

    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-lost-lease",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={"args": [], "kwargs": {}},
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=now,
        locked_by="worker-1:claim-lost",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    class LostLeaseContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            raise actors.JobLeaseLostError("lease lost")

    with (
        patch("five08.worker.actors.claim_job_for_execution", return_value=job),
        patch(
            "five08.worker.actors._renew_job_lease_while_running",
            return_value=LostLeaseContext(),
        ),
        patch("five08.worker.actors._schedule_job_lease_recovery") as mock_recovery,
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch.dict(
            actors._HANDLERS,
            {"process_docuseal_agreement_job": Mock(return_value={"ok": True})},
            clear=False,
        ),
    ):
        actors.execute_job("job-lost-lease")

    mock_recovery.assert_called_once_with("job-lost-lease", delay_seconds=600)
    mock_mark_succeeded.assert_not_called()
    mock_schedule_retry.assert_not_called()


def test_execute_job_time_limit_reserves_margin_for_a_non_default_lease() -> None:
    """The actor deadline cannot inherit Dramatiq's unrelated 600-second default."""

    non_default_options = actors._execute_job_actor_options(lease_seconds=30)
    default_options = actors._execute_job_actor_options()

    assert non_default_options["time_limit"] == 25_000
    assert non_default_options["time_limit"] < 30_000
    assert actors.execute_job.queue_name == default_options.pop("queue_name")
    assert actors.execute_job.options == default_options


def test_execute_job_time_limit_rejects_a_lease_without_cleanup_room() -> None:
    """The deadline must stay positive and strictly before a reclaimable lease."""

    assert actors._job_actor_time_limit_milliseconds(lease_seconds=6) == 1_000
    with pytest.raises(ValueError, match="must exceed the five-second"):
        actors._job_actor_time_limit_milliseconds(lease_seconds=5)


def test_schedule_retry_does_not_redeliver_after_lease_is_replaced() -> None:
    """A stale worker must not schedule another delivery after losing its lease."""

    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-126",
        type="process_docuseal_agreement_job",
        status=JobStatus.RUNNING,
        payload={"args": [], "kwargs": {}},
        idempotency_key=None,
        attempts=1,
        max_attempts=8,
        run_after=None,
        locked_at=now,
        locked_by="worker-1:expired-claim",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    with (
        patch("five08.worker.actors.mark_job_retry", return_value=False) as mock_retry,
        patch.object(actors.execute_job, "send_with_options") as mock_send,
    ):
        actors._schedule_retry(job, 2, error="transient failure")

    mock_retry.assert_called_once()
    assert mock_retry.call_args.kwargs["claim_token"] == "worker-1:expired-claim"
    mock_send.assert_not_called()
