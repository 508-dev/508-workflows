"""Shared queue and job persistence helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from psycopg import Connection, connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from redis import Redis

from five08.settings import SharedSettings

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Persistent job state values used across queue adapters."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"
    CANCELED = "canceled"


@dataclass(frozen=True)
class JobRecord:
    """Row-shape view of a persisted job."""

    id: str
    type: str
    status: JobStatus
    payload: dict[str, Any]
    idempotency_key: str | None
    attempts: int
    max_attempts: int
    run_after: datetime | None
    locked_at: datetime | None
    locked_by: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class EnqueuedJob:
    """Result for `enqueue_job` calls."""

    id: str
    created: bool


class QueueClient(Protocol):
    """Small framework-agnostic delivery interface."""

    def enqueue(self, job_id: str, *, run_at: datetime | None = None) -> None:
        """Schedule job_id with optional delivery time."""


def get_redis_connection(settings: SharedSettings) -> Redis:
    """Create a Redis connection from shared settings."""
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_socket_connect_timeout,
        socket_timeout=settings.redis_socket_timeout,
    )


def get_postgres_connection(settings: SharedSettings) -> Connection:
    """Create a PostgreSQL connection from shared settings."""
    return connect(settings.postgres_url)


def is_postgres_healthy(settings: SharedSettings) -> bool:
    """Return whether Postgres is reachable and queryable."""
    try:
        with get_postgres_connection(settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


def parse_queue_names(raw_queue_names: str) -> list[str]:
    """Normalize comma-separated queue names."""
    names = [name.strip() for name in raw_queue_names.split(",")]
    return [name for name in names if name]


def _parse_status(value: str) -> JobStatus:
    """Cast DB status text into `JobStatus`."""
    try:
        return JobStatus(value)
    except ValueError:
        logger.warning("Unknown job status from DB: %s", value)
        return JobStatus.FAILED


_UNSET = object()


def _as_record(row: dict[str, Any]) -> JobRecord:
    """Build a typed job record from a DB row."""
    return JobRecord(
        id=row["id"],
        type=row["type"],
        status=_parse_status(row["status"]),
        payload=row["payload"] or {},
        idempotency_key=row["idempotency_key"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        run_after=row["run_after"],
        locked_at=row["locked_at"],
        locked_by=row["locked_by"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_job_record(
    *,
    settings: SharedSettings,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    run_after: datetime | None = None,
) -> tuple[str, bool]:
    """Create or reuse an idempotent job row and return (job_id, was_created)."""
    job_id = str(uuid4())
    max_attempts = max_attempts or settings.job_max_attempts
    query = """
        INSERT INTO jobs (
            id,
            type,
            status,
            payload,
            idempotency_key,
            attempts,
            max_attempts,
            run_after
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id;
    """

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    job_id,
                    job_type,
                    JobStatus.QUEUED,
                    Jsonb(payload),
                    idempotency_key,
                    0,
                    max_attempts,
                    run_after,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                return row["id"], True

            if idempotency_key is None:
                raise RuntimeError("Unable to create job row without idempotency key.")

            cursor.execute(
                """
                SELECT id
                FROM jobs
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            )
            existing = cursor.fetchone()

    if existing is None:
        raise RuntimeError("Unable to load existing job for duplicate idempotency key.")

    return existing["id"], False


def get_job(settings: SharedSettings, job_id: str) -> JobRecord | None:
    """Load a job by id."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return _as_record(row)


def _mark_job(
    settings: SharedSettings,
    job_id: str,
    *,
    status: JobStatus | None = None,
    attempts: int | None = None,
    payload: Any = _UNSET,
    locked_at: Any = _UNSET,
    locked_by: Any = _UNSET,
    run_after: Any = _UNSET,
    last_error: Any = _UNSET,
) -> None:
    updates: list[str] = []
    params: list[Any] = []

    if status is not None:
        updates.append("status = %s")
        params.append(status.value)
    if attempts is not None:
        updates.append("attempts = %s")
        params.append(attempts)
    if payload is not _UNSET:
        updates.append("payload = %s")
        params.append(Jsonb(payload))
    if locked_at is not _UNSET:
        updates.append("locked_at = %s")
        params.append(locked_at)
    if locked_by is not _UNSET:
        updates.append("locked_by = %s")
        params.append(locked_by)
    if run_after is not _UNSET:
        updates.append("run_after = %s")
        params.append(run_after)
    if last_error is not _UNSET:
        updates.append("last_error = %s")
        params.append(last_error)
    if not updates:
        return

    updates.append("updated_at = NOW()")
    params.append(job_id)

    query = f"""
        UPDATE jobs
        SET {", ".join(updates)}
        WHERE id = %s;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)


def mark_job_running(
    settings: SharedSettings, job_id: str, *, worker_name: str
) -> None:
    """Mark a job as actively executing."""
    _mark_job(
        settings,
        job_id,
        status=JobStatus.RUNNING,
        locked_at=datetime.now(tz=timezone.utc),
        locked_by=worker_name,
        run_after=None,
        last_error=None,
    )


def mark_job_succeeded(
    settings: SharedSettings,
    job_id: str,
    *,
    result: Any | None = None,
    base_payload: dict[str, Any] | None = None,
) -> None:
    """Mark successful completion."""
    payload: Any = _UNSET
    if result is not None:
        merged_payload = dict(base_payload or {})
        merged_payload["result"] = result
        payload = merged_payload

    _mark_job(
        settings,
        job_id,
        status=JobStatus.SUCCEEDED,
        payload=payload,
        locked_at=None,
        locked_by=None,
        run_after=None,
        last_error=None,
    )


def mark_job_retry(
    settings: SharedSettings,
    job_id: str,
    *,
    attempts: int,
    run_after: datetime,
    last_error: str,
) -> None:
    """Keep record of a failed retryable attempt."""
    _mark_job(
        settings,
        job_id,
        status=JobStatus.FAILED,
        attempts=attempts,
        run_after=run_after,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
    )


def mark_job_dead(
    settings: SharedSettings,
    job_id: str,
    *,
    attempts: int,
    last_error: str,
) -> None:
    """Mark a job as permanently dead."""
    _mark_job(
        settings,
        job_id,
        status=JobStatus.DEAD,
        attempts=attempts,
        run_after=None,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
    )


def enqueue_job(
    queue: QueueClient,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    settings: SharedSettings,
    *,
    kwargs: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    run_after: datetime | None = None,
) -> EnqueuedJob:
    """Create a job record and hand it to the configured queue adapter."""
    payload = {"args": list(args), "kwargs": kwargs or {}}
    job_type = fn.__name__
    job_id, created = create_job_record(
        settings=settings,
        job_type=job_type,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        run_after=run_after,
    )
    if created:
        queue.enqueue(job_id, run_at=run_after)
    return EnqueuedJob(id=job_id, created=created)


def job_is_terminal(status: JobStatus) -> bool:
    """Return true when the job should not be executed again."""
    return status in {JobStatus.SUCCEEDED, JobStatus.DEAD, JobStatus.CANCELED}
