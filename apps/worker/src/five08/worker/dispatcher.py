"""Queue dispatcher adapters for worker jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from five08.queue import QueueClient


class DramatiqQueueClient:
    """Minimal queue adapter that dispatches a job id to Dramatiq."""

    @staticmethod
    def _execute_job_actor() -> Any:
        """Lazy import actor to avoid importing worker side-effects at API startup."""
        from five08.worker.actors import execute_job

        return execute_job

    def enqueue(self, job_id: str, *, run_at: datetime | None = None) -> None:
        """Schedule job_id for delivery now or in the future."""
        actor = self._execute_job_actor()

        if run_at is None:
            actor.send(job_id)
            return

        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        else:
            run_at = run_at.astimezone(timezone.utc)

        delay = run_at - datetime.now(tz=timezone.utc)
        if delay <= timedelta(0):
            actor.send(job_id)
            return

        actor.send_with_options(args=(job_id,), delay=int(delay.total_seconds() * 1000))


def build_queue_client() -> QueueClient:
    """Factory for the default production queue adapter."""
    return DramatiqQueueClient()
