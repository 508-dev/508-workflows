"""Unit tests for shared queue helpers."""

from unittest.mock import Mock, patch

from five08.queue import enqueue_job
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
