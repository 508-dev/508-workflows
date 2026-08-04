"""Unit tests for worker actor job state transitions."""

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import Mock, patch

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

    with (
        patch("five08.worker.actors.Event", return_value=stop_event),
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
