"""Unit tests for shared queue helpers."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

from five08.queue import (
    JobRecord,
    JobStatus,
    _parse_status,
    claim_job_for_execution,
    enqueue_job,
    mark_job_dead,
    mark_job_retry,
    mark_job_succeeded,
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

    with (
        patch("five08.queue.get_postgres_connection", return_value=connection),
        patch("five08.queue.uuid4", return_value="claim-1"),
    ):
        claimed = claim_job_for_execution(
            settings,
            "job-1",
            worker_name="worker-1",
        )

    assert claimed is None
    query, parameters = cursor.execute.call_args.args
    assert "UPDATE jobs" in query
    assert "status IN" in query
    assert "locked_at <= NOW() - (%s * INTERVAL '1 second')" in query
    assert "RETURNING *" in query
    assert parameters == (
        "running",
        "worker-1:claim-1",
        "job-1",
        "queued",
        "failed",
        "running",
        600,
    )


def test_claim_job_for_execution_reclaims_an_expired_running_lease() -> None:
    """A redelivery may replace an expired lease with a fresh owner token."""

    now = datetime.now(tz=timezone.utc)
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": "job-1",
        "type": "example",
        "status": "running",
        "payload": {},
        "idempotency_key": None,
        "attempts": 1,
        "max_attempts": 5,
        "run_after": None,
        "locked_at": now,
        "locked_by": "worker-2:fresh-claim",
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )
    settings = SharedSettings(job_max_attempts=5, job_timeout_seconds=30)

    with (
        patch("five08.queue.get_postgres_connection", return_value=connection),
        patch("five08.queue.uuid4", return_value="fresh-claim"),
    ):
        claimed = claim_job_for_execution(
            settings,
            "job-1",
            worker_name="worker-2",
        )

    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert claimed.locked_by == "worker-2:fresh-claim"
    query, parameters = cursor.execute.call_args.args
    assert "status = %s" in query
    assert "locked_at IS NULL" in query
    assert "locked_at <= NOW() - (%s * INTERVAL '1 second')" in query
    assert parameters[-2:] == ("running", 30)


def test_stale_owner_cannot_mark_job_succeeded() -> None:
    """A completed old execution cannot overwrite a newer claim."""

    cursor = MagicMock()
    cursor.rowcount = 0
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        transitioned = mark_job_succeeded(
            SharedSettings(),
            "job-1",
            result={"ok": True},
            base_payload={},
            claim_token="worker-1:expired-claim",
        )

    assert transitioned is False
    query, parameters = cursor.execute.call_args.args
    assert "AND status = 'running'" in query
    assert "AND locked_by = %s" in query
    assert parameters[-2:] == ["job-1", "worker-1:expired-claim"]


def test_stale_owner_cannot_schedule_a_retry_or_mark_a_job_dead() -> None:
    """A replaced lease cannot overwrite newer state with retry or dead."""

    cursor = MagicMock()
    cursor.rowcount = 0
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )
    settings = SharedSettings()
    token = "worker-1:expired-claim"

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        retried = mark_job_retry(
            settings,
            "job-1",
            attempts=2,
            run_after=datetime.now(tz=timezone.utc),
            last_error="transient failure",
            claim_token=token,
        )
        dead = mark_job_dead(
            settings,
            "job-1",
            attempts=2,
            last_error="terminal failure",
            claim_token=token,
        )

    assert retried is False
    assert dead is False
    assert cursor.execute.call_count == 2
    for call in cursor.execute.call_args_list:
        query, parameters = call.args
        assert "AND status = 'running'" in query
        assert "AND locked_by = %s" in query
        assert parameters[-2:] == ["job-1", token]


def test_parse_status_handles_unknown_values() -> None:
    """Unknown DB status should fallback to FAILED and emit a warning."""
    assert _parse_status("queued") == JobStatus.QUEUED

    with patch("five08.queue.logger.warning") as mock_warning:
        result = _parse_status("unexpected-status")

    assert result == JobStatus.FAILED
    mock_warning.assert_called_once_with(
        "Unknown job status from DB: %s", "unexpected-status"
    )
