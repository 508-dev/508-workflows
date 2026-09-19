"""Unit tests for shared queue helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

from five08.queue import (
    JobRecord,
    JobStatus,
    _parse_status,
    claim_job,
    enqueue_job,
    get_postgres_connection,
)
from five08.settings import SharedSettings


def test_enqueue_job_persists_and_dispatches_to_queue_client() -> None:
    """Queue helpers should create a persisted job and schedule delivery."""
    queue = Mock()
    settings = SharedSettings(job_max_attempts=5)

    with patch("five08.queue.create_job_record", return_value=("job-1", True)):
        result = enqueue_job(
            queue=queue, fn=lambda value: value, args=("payload",), settings=settings
        )

    queue.enqueue.assert_called_once_with("job-1", run_at=None)
    assert result.id == "job-1"
    assert result.created is True


def test_enqueue_job_redispatches_an_existing_queued_job() -> None:
    """An idempotent retry recovers a job stranded before its Redis handoff."""
    queue = Mock()
    settings = SharedSettings(job_max_attempts=5)
    scheduled_for = datetime(2026, 9, 17, tzinfo=timezone.utc)
    existing = JobRecord(
        id="job-1",
        type="author_wiki_edit_proposal_job",
        status=JobStatus.QUEUED,
        payload={"args": ["proposal-1", "guild-1"], "kwargs": {}},
        idempotency_key="wiki-author:proposal-1",
        attempts=0,
        max_attempts=5,
        run_after=scheduled_for,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=scheduled_for,
        updated_at=scheduled_for,
    )

    with (
        patch("five08.queue.create_job_record", return_value=("job-1", False)),
        patch("five08.queue.get_job", return_value=existing),
    ):
        result = enqueue_job(
            queue=queue,
            fn=lambda proposal_id, organization_id: None,
            args=("proposal-1", "guild-1"),
            settings=settings,
            idempotency_key="wiki-author:proposal-1",
            redispatch_existing_queued=True,
        )

    queue.enqueue.assert_called_once_with("job-1", run_at=scheduled_for)
    assert result.id == "job-1"
    assert result.created is False


def test_enqueue_job_does_not_redispatch_a_nonqueued_existing_job() -> None:
    """Recovery never adds a second delivery once a job has started or retried."""
    queue = Mock()
    settings = SharedSettings(job_max_attempts=5)
    now = datetime(2026, 9, 17, tzinfo=timezone.utc)
    existing = JobRecord(
        id="job-1",
        type="author_wiki_edit_proposal_job",
        status=JobStatus.RUNNING,
        payload={"args": ["proposal-1", "guild-1"], "kwargs": {}},
        idempotency_key="wiki-author:proposal-1",
        attempts=0,
        max_attempts=5,
        run_after=None,
        locked_at=now,
        locked_by="worker-1",
        last_error=None,
        created_at=now,
        updated_at=now,
    )

    with (
        patch("five08.queue.create_job_record", return_value=("job-1", False)),
        patch("five08.queue.get_job", return_value=existing),
    ):
        enqueue_job(
            queue=queue,
            fn=lambda proposal_id, organization_id: None,
            args=("proposal-1", "guild-1"),
            settings=settings,
            idempotency_key="wiki-author:proposal-1",
            redispatch_existing_queued=True,
        )

    queue.enqueue.assert_not_called()


def test_parse_status_handles_unknown_values() -> None:
    """Unknown DB status should fallback to FAILED and emit a warning."""
    assert _parse_status("queued") == JobStatus.QUEUED

    with patch("five08.queue.logger.warning") as mock_warning:
        result = _parse_status("unexpected-status")

    assert result == JobStatus.FAILED
    mock_warning.assert_called_once_with(
        "Unknown job status from DB: %s", "unexpected-status"
    )


def test_postgres_connection_applies_bounded_operation_deadlines() -> None:
    """Optional database deadlines should be passed to libpq explicitly."""
    settings = SharedSettings(postgres_url="postgresql://db.example/workflows")

    with patch("five08.queue.connect") as mock_connect:
        get_postgres_connection(
            settings,
            connect_timeout_seconds=1.9,
            statement_timeout_seconds=2.5,
        )

    mock_connect.assert_called_once_with(
        settings.postgres_url,
        connect_timeout=1,
        options="-c statement_timeout=2500",
    )


def test_claim_job_requires_an_eligible_status_and_returns_the_claimed_row() -> None:
    """A worker can execute only the row returned by its conditional claim."""
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    row = {
        "id": "job-1",
        "type": "author_wiki_edit_proposal_job",
        "status": "running",
        "payload": {"args": [], "kwargs": {}},
        "idempotency_key": None,
        "attempts": 0,
        "max_attempts": 5,
        "run_after": None,
        "locked_at": now,
        "locked_by": "worker-1",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row
    settings = SharedSettings()

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        claimed = claim_job(settings, "job-1", worker_name="worker-1")

    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    query, params = cursor.execute.call_args.args
    assert "UPDATE jobs" in query
    assert "status IN (%s, %s)" in query
    assert "run_after IS NULL OR run_after <= NOW()" in query
    assert params == ("running", "worker-1", "job-1", "queued", "failed")


def test_claim_job_returns_none_when_a_concurrent_delivery_already_claimed_it() -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        claimed = claim_job(SharedSettings(), "job-1", worker_name="worker-1")

    assert claimed is None


def test_claim_job_can_reclaim_only_an_expired_running_job_type() -> None:
    """A bounded worker flow can atomically recover its own stale lock."""
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    row = {
        "id": "job-1",
        "type": "author_wiki_edit_proposal_job",
        "status": "running",
        "payload": {"args": [], "kwargs": {}},
        "idempotency_key": None,
        "attempts": 0,
        "max_attempts": 5,
        "run_after": None,
        "locked_at": now,
        "locked_by": "worker-2",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = row

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        claimed = claim_job(
            SharedSettings(),
            "job-1",
            worker_name="worker-2",
            reclaim_running_job_type="author_wiki_edit_proposal_job",
            reclaim_running_after_seconds=390.0,
        )

    assert claimed is not None
    query, params = cursor.execute.call_args.args
    assert "type = %s" in query
    assert "locked_at <= NOW() - (%s * INTERVAL '1 second')" in query
    assert params == (
        "running",
        "worker-2",
        "job-1",
        "queued",
        "failed",
        "running",
        "author_wiki_edit_proposal_job",
        390.0,
    )
