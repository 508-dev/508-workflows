"""Unit tests for worker actor job state transitions."""

from datetime import datetime, timezone
from unittest.mock import Mock, patch

from five08.queue import JobRecord, JobStatus
from five08.worker import actors
from five08.worker import jobs
from five08.worker.config import WorkerSettings
from five08.worker.crm.docuseal_processor import (
    DocusealAgreementNonRetryableError,
    DocusealAgreementProcessingError,
)
from five08.wiki_editing.models import WikiAuthoringLeaseHeldError


def test_run_job_reclaims_wiki_jobs_after_the_authoring_lease() -> None:
    with (
        patch("five08.worker.actors.claim_job", return_value=None) as mock_claim,
        patch("five08.worker.actors.get_job", return_value=None),
        patch.object(
            actors.settings,
            "wiki_omp_authoring_timeout_seconds",
            120.0,
        ),
        patch.object(
            actors.settings,
            "wiki_omp_startup_timeout_seconds",
            15.0,
        ),
    ):
        actors._run_job("job-wiki-stale")

    mock_claim.assert_called_once_with(
        actors.settings,
        "job-wiki-stale",
        worker_name=actors.settings.worker_name,
        reclaim_running_job_type="author_wiki_edit_proposal_job",
        reclaim_running_after_seconds=195.0,
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
        patch("five08.worker.actors.claim_job", return_value=job),
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
        patch("five08.worker.actors.claim_job", return_value=job),
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


def test_exhausted_wiki_authoring_marks_the_proposal_revisable() -> None:
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-wiki-1",
        type="author_wiki_edit_proposal_job",
        status=JobStatus.QUEUED,
        payload={
            "args": ["proposal-1", "guild-1"],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=1,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _raise_transient(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("sandbox unavailable")

    with (
        patch("five08.worker.actors.claim_job", return_value=job),
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch(
            "five08.worker.actors.mark_wiki_authoring_retry_exhausted"
        ) as mock_mark_proposal,
        patch.dict(
            actors._HANDLERS,
            {"author_wiki_edit_proposal_job": _raise_transient},
            clear=False,
        ),
    ):
        actors._run_job("job-wiki-1")

    mock_mark_succeeded.assert_not_called()
    mock_mark_proposal.assert_called_once_with("proposal-1", "guild-1")
    mock_mark_dead.assert_called_once()


def test_live_wiki_authoring_lease_retries_without_consuming_an_attempt() -> None:
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-wiki-lease-held",
        type="author_wiki_edit_proposal_job",
        status=JobStatus.QUEUED,
        payload={"args": ["proposal-1", "guild-1"], "kwargs": {}},
        idempotency_key=None,
        attempts=2,
        max_attempts=3,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    def _lease_held(*_args: object, **_kwargs: object) -> None:
        raise WikiAuthoringLeaseHeldError(17.25)

    with (
        patch("five08.worker.actors.claim_job", return_value=job),
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch("five08.worker.actors._mark_exhausted_wiki_authoring") as mock_exhausted,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch.dict(
            actors._HANDLERS,
            {"author_wiki_edit_proposal_job": _lease_held},
            clear=False,
        ),
    ):
        actors._run_job(job.id)

    mock_mark_succeeded.assert_not_called()
    mock_mark_dead.assert_not_called()
    mock_exhausted.assert_not_called()
    mock_schedule_retry.assert_called_once()
    call = mock_schedule_retry.call_args
    assert call.args[0].id == job.id
    assert call.args[1] == job.attempts
    assert call.kwargs["delay_seconds"] == 17.25


def test_exhausted_wiki_authoring_with_missing_token_marks_proposal_revisable(
    monkeypatch,
) -> None:
    """Configuration failure must not strand a queue-dead proposal as queued."""
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="job-wiki-missing-token",
        type="author_wiki_edit_proposal_job",
        status=JobStatus.QUEUED,
        payload={
            "args": ["proposal-missing-token", "guild-1"],
            "kwargs": {},
        },
        idempotency_key=None,
        attempts=0,
        max_attempts=1,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=now,
        updated_at=now,
    )
    worker_settings = WorkerSettings(
        wiki_editing_enabled=True,
        wiki_omp_sandbox_url="http://wiki_omp_sandbox:8080",
        wiki_omp_sandbox_token=None,
    )
    store = Mock()
    monkeypatch.setattr(jobs, "settings", worker_settings)
    monkeypatch.setattr(jobs, "PostgresWikiEditingStore", lambda _settings: store)

    with (
        patch("five08.worker.actors.claim_job", return_value=job),
        patch("five08.worker.actors.mark_job_succeeded") as mock_mark_succeeded,
        patch("five08.worker.actors.mark_job_dead") as mock_mark_dead,
        patch("five08.worker.actors._schedule_retry") as mock_schedule_retry,
        patch.dict(
            actors._HANDLERS,
            {"author_wiki_edit_proposal_job": jobs.author_wiki_edit_proposal_job},
            clear=False,
        ),
    ):
        actors._run_job(job.id)

    mock_mark_succeeded.assert_not_called()
    mock_schedule_retry.assert_not_called()
    store.fail_proposal_if_status.assert_called_once_with(
        "proposal-missing-token",
        organization_id="guild-1",
        failure_code="authoring_retry_exhausted",
        expected_statuses=frozenset({"queued", "authoring"}),
    )
    mock_mark_dead.assert_called_once()


def test_run_job_does_not_invoke_handler_when_another_delivery_claimed_it() -> None:
    """Duplicate deliveries must not execute a handler after a lost claim."""
    handler = Mock()
    running = JobRecord(
        id="job-already-running",
        type="claimable_job",
        status=JobStatus.RUNNING,
        payload={"args": [], "kwargs": {}},
        idempotency_key=None,
        attempts=0,
        max_attempts=3,
        run_after=None,
        locked_at=datetime.now(timezone.utc),
        locked_by="other-worker",
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    with (
        patch("five08.worker.actors.claim_job", return_value=None),
        patch("five08.worker.actors.get_job", return_value=running),
        patch.dict(actors._HANDLERS, {"claimable_job": handler}, clear=False),
    ):
        actors._run_job(running.id)

    handler.assert_not_called()
