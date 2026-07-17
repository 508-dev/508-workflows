"""Unit tests for shared queue helpers."""

from unittest.mock import MagicMock, Mock, patch

from five08.queue import JobStatus, _parse_status, enqueue_job, revive_dead_job
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


def test_parse_status_handles_unknown_values() -> None:
    """Unknown DB status should fallback to FAILED and emit a warning."""
    assert _parse_status("queued") == JobStatus.QUEUED

    with patch("five08.queue.logger.warning") as mock_warning:
        result = _parse_status("unexpected-status")

    assert result == JobStatus.FAILED
    mock_warning.assert_called_once_with(
        "Unknown job status from DB: %s", "unexpected-status"
    )


def test_revive_dead_job_requires_matching_dead_row() -> None:
    """Only a matching dead job can be atomically reset for redelivery."""
    cursor = Mock()
    cursor.fetchone.return_value = {"id": "job-1"}
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("five08.queue.get_postgres_connection", return_value=connection):
        revived = revive_dead_job(
            SharedSettings(),
            "job-1",
            expected_job_type="ingest_erpnext_bank_transaction_job",
        )

    assert revived is True
    query, params = cursor.execute.call_args.args
    assert "status = %s" in query
    assert "attempts = 0" in query
    assert "AND type = %s" in query
    assert params == (
        JobStatus.QUEUED,
        "job-1",
        "ingest_erpnext_bank_transaction_job",
        JobStatus.DEAD,
    )
