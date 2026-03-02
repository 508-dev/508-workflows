"""Dramatiq actor definitions and job execution routing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from five08.discord_webhook import DiscordWebhookLogger

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
from five08.worker.crm.docuseal_processor import DocusealAgreementNonRetryableError
from five08.worker.jobs import (
    apply_resume_profile_job,
    extract_resume_profile_job,
    process_mailbox_message_job,
    process_contact_skills_job,
    process_docuseal_agreement_job,
    process_webhook_event,
    sync_people_from_crm_job,
    sync_person_from_crm_job,
)

from five08.logging import configure_observability

logger = logging.getLogger(__name__)
configure_observability(
    settings=settings,
    service_name="worker-actors",
)

DRAMATIQ_BROKER = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(DRAMATIQ_BROKER)

_JOB_WEBHOOK_LOGGER = DiscordWebhookLogger(
    webhook_url=settings.discord_logs_webhook_url,
    timeout_seconds=2.0,
    wait_for_response=settings.discord_logs_webhook_wait,
)

_QUEUE_NAMES = parse_queue_names(settings.worker_queue_names)
_QUEUE_NAME = _QUEUE_NAMES[0] if _QUEUE_NAMES else settings.redis_queue_name

_HANDLERS: dict[str, Any] = {
    process_webhook_event.__name__: process_webhook_event,
    process_contact_skills_job.__name__: process_contact_skills_job,
    extract_resume_profile_job.__name__: extract_resume_profile_job,
    apply_resume_profile_job.__name__: apply_resume_profile_job,
    process_mailbox_message_job.__name__: process_mailbox_message_job,
    sync_people_from_crm_job.__name__: sync_people_from_crm_job,
    sync_person_from_crm_job.__name__: sync_person_from_crm_job,
    process_docuseal_agreement_job.__name__: process_docuseal_agreement_job,
}


def _job_attempt_display(attempts: int) -> int:
    return max(1, attempts + 1)


def _truncate(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."


def _summarize_job_result(result: Any) -> str:
    if isinstance(result, dict):
        keys = ",".join(sorted(map(str, result.keys())))
        if not keys:
            return "dict(result)"
        return f"dict(keys={_truncate(keys, 120)})"
    text = str(result)
    return _truncate(text, 120)


def _log_job_event(
    *,
    event_type: str,
    job_id: str,
    job_type: str,
    attempts: int,
    max_attempts: int,
    worker_name: str,
    error: str | None = None,
    result: Any = None,
) -> None:
    if not _JOB_WEBHOOK_LOGGER.enabled:
        return

    parts = [
        f"{event_type.upper()} job",
        f"type={job_type}",
        f"id={job_id}",
        f"attempt={attempts}/{max_attempts}",
        f"worker={worker_name}",
    ]

    if result is not None:
        parts.append(f"result={_summarize_job_result(result)}")
    if error:
        parts.append(f"error={_truncate(error, 160)}")

    _JOB_WEBHOOK_LOGGER.send(content=" | ".join(parts))


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


def _schedule_retry(job: JobRecord, attempts: int, *, error: str) -> None:
    job_id = job.id
    delay_seconds = _compute_retry_delay_seconds(attempts)
    retry_at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay_seconds)
    mark_job_retry(
        settings,
        job_id,
        attempts=attempts,
        run_after=retry_at,
        last_error=error,
    )
    _log_job_event(
        event_type="retrying",
        job_id=job_id,
        job_type=job.type,
        attempts=attempts,
        max_attempts=job.max_attempts,
        worker_name=settings.worker_name,
        error=error,
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
        error = f"Unknown job type: {job.type}"
        logger.error("Marking job dead id=%s error=%s", job_id, error)
        mark_job_dead(settings, job_id, attempts=job.attempts, last_error=error)
        _log_job_event(
            event_type="dead",
            job_id=job.id,
            job_type=job.type,
            attempts=_job_attempt_display(job.attempts),
            max_attempts=job.max_attempts,
            worker_name=settings.worker_name,
            error=error,
        )
        return

    mark_job_running(settings, job_id, worker_name=settings.worker_name)
    _log_job_event(
        event_type="started",
        job_id=job.id,
        job_type=job.type,
        attempts=_job_attempt_display(job.attempts),
        max_attempts=job.max_attempts,
        worker_name=settings.worker_name,
    )

    try:
        args, kwargs = _extract_call_args(job)
        result = handler(*args, **kwargs)
        mark_job_succeeded(
            settings,
            job_id,
            result=result,
            base_payload=job.payload,
        )
        logger.info("Completed job_id=%s type=%s", job_id, job.type)
        _log_job_event(
            event_type="succeeded",
            job_id=job.id,
            job_type=job.type,
            attempts=_job_attempt_display(job.attempts),
            max_attempts=job.max_attempts,
            worker_name=settings.worker_name,
            result=result,
        )
    except DocusealAgreementNonRetryableError as exc:
        next_attempt = job.attempts + 1
        error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Job failed non-retryable id=%s attempt=%s error=%s",
            job_id,
            next_attempt,
            error,
        )
        mark_job_dead(
            settings,
            job_id,
            attempts=next_attempt,
            last_error=error,
        )
        _log_job_event(
            event_type="dead",
            job_id=job.id,
            job_type=job.type,
            attempts=next_attempt,
            max_attempts=job.max_attempts,
            worker_name=settings.worker_name,
            error=error,
        )
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
            _log_job_event(
                event_type="dead",
                job_id=job.id,
                job_type=job.type,
                attempts=next_attempt,
                max_attempts=job.max_attempts,
                worker_name=settings.worker_name,
                error=error,
            )
            return
        _schedule_retry(job, next_attempt, error=error)
        # _schedule_retry logs the retry event. Keep this exception path focused
        # on state transition.


@dramatiq.actor(queue_name=_QUEUE_NAME, max_retries=0)
def execute_job(job_id: str) -> None:
    """Entry-point actor for all worker jobs."""
    _run_job(job_id)
