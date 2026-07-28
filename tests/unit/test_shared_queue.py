"""Unit tests for shared queue helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

from five08.queue import (
    JobRecord,
    JobStatus,
    _parse_status,
    claim_job_for_execution,
    enqueue_job,
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


def test_enqueue_job_redelivers_an_existing_queued_job() -> None:
    """An interrupted first broker handoff remains recoverable."""

    queue = Mock()
    settings = SharedSettings(job_max_attempts=5)
    existing = JobRecord(
        id="job-1",
        type="example",
        status=JobStatus.QUEUED,
        payload={},
        idempotency_key="example:1",
        attempts=0,
        max_attempts=5,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )

    with (
        patch("five08.queue.create_job_record", return_value=("job-1", False)),
        patch("five08.queue.get_job", return_value=existing),
    ):
        result = enqueue_job(
            queue=queue, fn=lambda value: value, args=("payload",), settings=settings
        )

    queue.enqueue.assert_called_once_with("job-1", run_at=None)
    assert result.id == "job-1"
    assert result.created is False


def test_claim_job_for_execution_uses_one_conditional_update() -> None:
    """Duplicate deliveries race on one SQL state transition, not a read."""

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )
    settings = SharedSettings(job_max_attempts=5)

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        claimed = claim_job_for_execution(
            settings,
            "job-1",
            worker_name="worker-1",
        )

    assert claimed is None
    query, parameters = cursor.execute.call_args.args
    assert "UPDATE jobs" in query
    assert "status IN" in query
    assert "RETURNING *" in query
    assert parameters == ("running", "worker-1", "job-1", "queued", "failed")


def test_parse_status_handles_unknown_values() -> None:
    """Unknown DB status should fallback to FAILED and emit a warning."""
    assert _parse_status("queued") == JobStatus.QUEUED

    with patch("five08.queue.logger.warning") as mock_warning:
        result = _parse_status("unexpected-status")

    assert result == JobStatus.FAILED
    mock_warning.assert_called_once_with(
        "Unknown job status from DB: %s", "unexpected-status"
    )
