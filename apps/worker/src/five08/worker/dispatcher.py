"""Queue dispatcher adapters for worker jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from five08.queue import QueueClient
from five08.worker.actors import execute_job


class DramatiqQueueClient:
    """Minimal queue adapter that dispatches a job id to Dramatiq."""

    def enqueue(self, job_id: str, *, run_at: datetime | None = None) -> None:
        """Schedule job_id for delivery now or in the future."""
        if run_at is None:
            execute_job.send(job_id)
            return

        delay = run_at - datetime.now(tz=timezone.utc)
        if delay <= timedelta(0):
            execute_job.send(job_id)
            return

        execute_job.send_with_options(
            args=(job_id,), delay=int(delay.total_seconds() * 1000)
        )


def build_queue_client() -> QueueClient:
    """Factory for the default production queue adapter."""
    return DramatiqQueueClient()
