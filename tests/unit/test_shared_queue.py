"""Unit tests for shared queue helpers."""

from unittest.mock import Mock

from five08.queue import enqueue_job
from five08.settings import SharedSettings


def test_enqueue_job_applies_shared_timeouts() -> None:
    """Shared queue helper should pass timeout and TTL from settings."""
    queue = Mock()
    queue.enqueue.return_value = Mock(id="job-1")
    settings = SharedSettings(job_timeout_seconds=123, job_result_ttl_seconds=456)

    enqueue_job(
        queue=queue, fn=lambda value: value, args=("payload",), settings=settings
    )

    queue.enqueue.assert_called_once()
    _, args, kwargs = queue.enqueue.mock_calls[0]
    assert args[1] == "payload"
    assert kwargs["job_timeout"] == 123
    assert kwargs["result_ttl"] == 456
