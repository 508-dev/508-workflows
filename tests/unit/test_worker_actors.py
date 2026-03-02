"""Unit tests for worker actor job state transitions."""

from datetime import datetime, timezone
from unittest.mock import patch

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
        status=JobStatus.QUEUED,
        payload={
            "args": ["member@508.dev", "2026-02-25 12:00:00", 42],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _raise_docuseal_processing_error(*args: object, **kwargs: object) -> None:
        raise DocusealAgreementProcessingError("CRM unavailable")

    with (
        patch("five08.worker.actors.get_job", return_value=job),
        patch("five08.worker.actors.mark_job_running") as mock_mark_running,
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch.dict(
            actors._HANDLERS,
            {"process_docuseal_agreement_job": _raise_docuseal_processing_error},
            clear=False,
        ),
    ):
        actors._run_job("job-123")

    mock_mark_running.assert_called_once()
    mock_mark_succeeded.assert_not_called()
    mock_mark_dead.assert_not_called()
    mock_schedule_retry.assert_called_once()
    call_args = mock_schedule_retry.call_args
    assert call_args.args[0] == "job-123"
    assert call_args.args[1] == 1
    assert (
        "DocusealAgreementProcessingError: CRM unavailable" == call_args.kwargs["error"]
    )


def test_run_job_marks_dead_for_non_retryable_docuseal_error() -> None:
    """Non-retryable Docuseal failures should be marked dead immediately."""
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-124",
        type="process_docuseal_agreement_job",
        status=JobStatus.QUEUED,
        payload={
            "args": ["member@508.dev", "not-a-date", 42],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=8,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _raise_docuseal_non_retryable_error(*args: object, **kwargs: object) -> None:
        raise DocusealAgreementNonRetryableError(
            "invalid_completed_at for contact_id=c-1"
        )

    with (
        patch("five08.worker.actors.get_job", return_value=job),
        patch("five08.worker.actors.mark_job_running") as mock_mark_running,
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

    mock_mark_running.assert_called_once()
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
