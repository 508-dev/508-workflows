"""Dramatiq actor definitions and job execution routing."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Final

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
from five08.worker.config import settings
from five08.worker.crm.docuseal_processor import DocusealAgreementNonRetryableError
from five08.worker.jobs import (
    JOB_FUNCTIONS,
    mark_wiki_authoring_retry_exhausted,
)
from five08.wiki_editing.models import WikiAuthoringLeaseHeldError

from five08.logging import configure_observability

logger = logging.getLogger(__name__)
configure_observability(
    settings=settings,
    service_name="worker-actors",
)
_DEFAULT_WEBHOOK_USERNAME: Final[str] = "508 Workflows"
_WEBHOOK_INFO_COLOR: Final[int] = 0x3498DB
_WEBHOOK_WARNING_COLOR: Final[int] = 0xF1C40F
_WEBHOOK_ERROR_COLOR: Final[int] = 0xE74C3C
_WEBHOOK_SUCCESS_COLOR: Final[int] = 0x2ECC71

DRAMATIQ_BROKER = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(DRAMATIQ_BROKER)

_JOB_WEBHOOK_LOGGER = DiscordWebhookLogger(
    webhook_url=settings.discord_logs_webhook_url,
    timeout_seconds=2.0,
    wait_for_response=settings.discord_logs_webhook_wait,
)

_QUEUE_NAME = settings.worker_queue_name
_HANDLERS = JOB_FUNCTIONS
_SYNC_PEOPLE_JOB_NAME: Final[str] = "sync_people_from_crm_job"
_WIKI_AUTHORING_JOB_NAME: Final[str] = "author_wiki_edit_proposal_job"


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


def _should_log_job_event(*, event_type: str, job_type: str) -> bool:
    if job_type != _SYNC_PEOPLE_JOB_NAME:
        return True
    return event_type not in {"started", "retrying", "succeeded"}


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

    _JOB_WEBHOOK_LOGGER.send(
        username=_DEFAULT_WEBHOOK_USERNAME,
        embeds=[
            {
                "title": f"{event_type.upper()} Job",
                "description": f"Worker job lifecycle event for `{job_type}`",
                "color": _job_event_color(event_type=event_type, has_error=bool(error)),
                "fields": _job_event_fields(
                    event_type=event_type,
                    job_id=job_id,
                    job_type=job_type,
                    attempts=attempts,
                    max_attempts=max_attempts,
                    worker_name=worker_name,
                    error=error,
                    result=result,
                ),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            }
        ],
    )


def _job_event_color(*, event_type: str, has_error: bool = False) -> int:
    if event_type.lower() == "succeeded":
        return _WEBHOOK_SUCCESS_COLOR
    if event_type.lower() in {"dead", "failed"} or has_error:
        return _WEBHOOK_ERROR_COLOR
    if event_type.lower() == "retrying":
        return _WEBHOOK_WARNING_COLOR
    return _WEBHOOK_INFO_COLOR


def _job_event_fields(
    *,
    event_type: str,
    job_id: str,
    job_type: str,
    attempts: int,
    max_attempts: int,
    worker_name: str,
    error: str | None,
    result: Any,
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = [
        {"name": "Event", "value": event_type, "inline": True},
        {"name": "Job ID", "value": _truncate(job_id, 64), "inline": True},
        {"name": "Type", "value": _truncate(job_type, 64), "inline": True},
        {
            "name": "Attempt",
            "value": f"{attempts}/{max_attempts}",
            "inline": True,
        },
        {"name": "Worker", "value": _truncate(worker_name, 64), "inline": True},
    ]

    if result is not None:
        fields.append(
            {
                "name": "Result",
                "value": _truncate(_summarize_job_result(result), 1024),
                "inline": False,
            },
        )
    if error:
        fields.append(
            {
                "name": "Error",
                "value": _truncate(error, 1024),
                "inline": False,
            },
        )
    return fields


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


def _schedule_retry(
    job: JobRecord,
    attempts: int,
    *,
    error: str,
    delay_seconds: float | None = None,
) -> None:
    job_id = job.id
    retry_delay_seconds = (
        _compute_retry_delay_seconds(attempts)
        if delay_seconds is None
        else max(1, math.ceil(delay_seconds))
    )
    retry_at = datetime.now(tz=timezone.utc) + timedelta(seconds=retry_delay_seconds)
    mark_job_retry(
        settings,
        job_id,
        attempts=attempts,
        run_after=retry_at,
        last_error=error,
    )
    if _should_log_job_event(event_type="retrying", job_type=job.type):
        _log_job_event(
            event_type="retrying",
            job_id=job_id,
            job_type=job.type,
            attempts=attempts,
            max_attempts=job.max_attempts,
            worker_name=settings.worker_name,
            error=error,
        )
    execute_job.send_with_options(args=(job_id,), delay=retry_delay_seconds * 1000)


def _mark_exhausted_wiki_authoring(job: JobRecord) -> None:
    """Best-effort bridge from generic queue exhaustion to proposal state."""
    if job.type != _WIKI_AUTHORING_JOB_NAME:
        return
    try:
        args, kwargs = _extract_call_args(job)
        if kwargs or len(args) != 2:
            raise ValueError("wiki authoring job payload must have two positional IDs")
        proposal_id, organization_id = args
        if not isinstance(proposal_id, str) or not isinstance(organization_id, str):
            raise ValueError("wiki authoring job IDs must be strings")
        mark_wiki_authoring_retry_exhausted(proposal_id, organization_id)
    except Exception:
        # The original worker job still becomes dead. This callback must not
        # mask its error or make generic queue recovery less reliable.
        logger.exception(
            "Failed to mark exhausted wiki authoring proposal job_id=%s",
            job.id,
        )


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
    if _should_log_job_event(event_type="started", job_type=job.type):
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
        if _should_log_job_event(event_type="succeeded", job_type=job.type):
            _log_job_event(
                event_type="succeeded",
                job_id=job.id,
                job_type=job.type,
                attempts=_job_attempt_display(job.attempts),
                max_attempts=job.max_attempts,
                worker_name=settings.worker_name,
                result=result,
            )
    except WikiAuthoringLeaseHeldError as exc:
        # A separate worker still owns the proposal's durable authoring lease.
        # This is coordination, not a failed authoring attempt: preserve the
        # attempt count and do not turn a live lease into a terminal job.
        error = f"{type(exc).__name__}: {exc}"
        logger.info(
            "Wiki authoring lease held id=%s; retrying after %.3fs",
            job_id,
            exc.retry_after_seconds,
        )
        _schedule_retry(
            job,
            job.attempts,
            error=error,
            delay_seconds=exc.retry_after_seconds,
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
            _mark_exhausted_wiki_authoring(job)
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
