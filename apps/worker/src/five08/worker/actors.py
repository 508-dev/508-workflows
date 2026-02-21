"""Dramatiq actor definitions and job execution routing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from five08.queue import (
    JobRecord,
    JobStatus,
    get_job,
    job_is_terminal,
    mark_job_dead,
    mark_job_retry,
    mark_job_running,
    mark_job_succeeded,
)
from five08.queue import parse_queue_names
from five08.worker.config import settings
from five08.worker.jobs import process_contact_skills_job, process_webhook_event

from five08.logging import configure_logging

logger = logging.getLogger(__name__)
configure_logging(settings.log_level)

DRAMATIQ_BROKER = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(DRAMATIQ_BROKER)

_QUEUE_NAMES = parse_queue_names(settings.worker_queue_names)
_QUEUE_NAME = _QUEUE_NAMES[0] if _QUEUE_NAMES else settings.redis_queue_name

_HANDLERS: dict[str, Any] = {
    process_webhook_event.__name__: process_webhook_event,
    process_contact_skills_job.__name__: process_contact_skills_job,
}


def _extract_call_args(job: JobRecord) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Convert stored payload back into call-site args."""
    raw_args = job.payload.get("args", [])
    raw_kwargs = job.payload.get("kwargs", {})
    if not isinstance(raw_args, list):
        raise TypeError("Job payload args must be a list.")
    if not isinstance(raw_kwargs, dict):
        raise TypeError("Job payload kwargs must be a dict.")
    return tuple(raw_args), raw_kwargs


def _compute_retry_delay_seconds(attempt: int) -> int:
    base = max(1, settings.job_retry_base_seconds)
    capped = settings.job_retry_max_seconds
    return min(base * (2 ** max(attempt - 1, 0)), capped)


def _schedule_retry(job_id: str, attempts: int, *, error: str) -> None:
    delay_seconds = _compute_retry_delay_seconds(attempts)
    retry_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay_seconds)
    mark_job_retry(
        settings,
        job_id,
        attempts=attempts,
        run_after=retry_at,
        last_error=error,
    )
    execute_job.send_with_options(args=(job_id,), delay=delay_seconds * 1000)


def _run_job(job_id: str) -> None:
    job = get_job(settings, job_id)
    if job is None:
        logger.warning("Skipping job_id=%s (not found)", job_id)
        return
    if job_is_terminal(job.status):
        logger.info("Skipping job_id=%s already terminal (%s)", job_id, job.status)
        return
    if job.status == JobStatus.RUNNING and job.locked_by != settings.worker_name:
        logger.warning(
            "Skipping job_id=%s locked by worker=%s",
            job_id,
            job.locked_by,
        )
        return

    handler = _HANDLERS.get(job.type)
    if handler is None:
        raise ValueError(f"Unknown job type: {job.type}")

    mark_job_running(settings, job_id, worker_name=settings.worker_name)

    try:
        args, kwargs = _extract_call_args(job)
        handler(*args, **kwargs)
        mark_job_succeeded(settings, job_id)
        logger.info("Completed job_id=%s type=%s", job_id, job.type)
    except Exception as exc:
        next_attempt = job.attempts + 1
        error = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "Job failed id=%s attempt=%s error=%s", job_id, next_attempt, error
        )

        if next_attempt >= job.max_attempts:
            mark_job_dead(
                settings,
                job_id,
                attempts=next_attempt,
                last_error=error,
            )
            return
        _schedule_retry(job_id, next_attempt, error=error)


@dramatiq.actor(queue_name=_QUEUE_NAME, max_retries=0)
def execute_job(job_id: str) -> None:
    """Entry-point actor for all worker jobs."""
    _run_job(job_id)
