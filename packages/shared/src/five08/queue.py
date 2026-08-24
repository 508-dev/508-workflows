"""Shared queue and job persistence helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import uuid4

from psycopg import Connection, connect
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.types.json import Jsonb
from redis import Redis

from five08.settings import SharedSettings

logger = logging.getLogger(__name__)


# An idempotent request can arrive again after the initial database insert but
# before the first broker delivery. Reuse the durable queued-row reservation
# rather than publishing a broker message for every upstream retry.
_DUPLICATE_ENQUEUE_REDELIVERY_BACKOFF_SECONDS = 60.0


def trusted_sql(query: str) -> SQL:
    """Type SQL assembled only from internal fragments plus placeholders.

    Do not pass user input through this helper. Dynamic values must stay in
    psycopg parameter tuples; dynamic identifiers need psycopg.sql composition.
    The runtime value remains a plain str so tests can inspect executed SQL.
    """
    return cast(SQL, query)


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
        id=str(row["id"]),
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
                return str(row["id"]), True

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

    return str(existing["id"]), False


def get_job(settings: SharedSettings, job_id: str) -> JobRecord | None:
    """Load a job by id."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            return _as_record(row)


def redeliver_queued_job(
    queue: QueueClient,
    *,
    settings: SharedSettings,
    job_id: str,
    minimum_age_seconds: float,
) -> bool:
    """Redeliver one queued job after atomically reserving its broker retry.

    A queue adapter is at-least-once, but a persisted queued row does not tell
    us whether its original broker delivery survived.  ``updated_at`` acts as
    a durable redelivery lease here: exactly one dispatcher may advance it
    after the configured backoff window before it publishes another message.
    A process crash after the reservation is safe--a later dispatcher retries
    after the same bounded window.
    """

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        return False
    backoff_seconds = max(1.0, float(minimum_age_seconds))
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE jobs
                SET updated_at = NOW()
                WHERE id = %s
                  AND status = %s
                  AND updated_at <= NOW() - (%s * INTERVAL '1 second')
                RETURNING id, run_after
                """,
                (normalized_job_id, JobStatus.QUEUED.value, backoff_seconds),
            )
            row = cursor.fetchone()
    if row is None:
        return False
    queue.enqueue(str(row["id"]), run_at=row["run_after"])
    return True


def list_jobs(
    settings: SharedSettings,
    *,
    created_after: datetime,
    limit: int,
    status: JobStatus | None = None,
    job_type: str | None = None,
) -> list[JobRecord]:
    """Load recent jobs created after the given UTC datetime."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            conditions: list[str] = ["created_at >= %s"]
            params: list[Any] = [created_after]

            if status is not None:
                conditions.append("status = %s")
                params.append(status.value)
            if job_type is not None:
                conditions.append("type = %s")
                params.append(job_type)

            where_clause = " AND ".join(conditions)
            query = f"""
                SELECT *
                FROM jobs
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s
            """
            cursor.execute(
                trusted_sql(query),
                (*params, limit),
            )
            rows = cursor.fetchall()
            return [_as_record(row) for row in rows]


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
    claim_token: str,
) -> bool:
    """Persist a job transition fenced to one active execution lease."""

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
        return False

    updates.append("updated_at = NOW()")
    params.append(job_id)
    params.append(claim_token)

    query = f"""
        UPDATE jobs
        SET {", ".join(updates)}
        WHERE id = %s
          AND status = 'running'
          AND locked_by = %s;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(trusted_sql(query), params)
            return cursor.rowcount > 0


def claim_job_for_execution(
    settings: SharedSettings,
    job_id: str,
    *,
    worker_name: str,
) -> JobRecord | None:
    """Atomically claim due work or safely recover an expired running lease.

    Queue adapters provide at-least-once delivery, so duplicate broker messages
    are expected. The conditional update is the execution boundary: only the
    caller that transitions the persisted job to ``running`` may invoke its
    side-effectful handler. A unique lease token fences stale workers from
    recording a later success/retry/dead transition after their claim expires.
    Reclaiming a stale running lease consumes one retry attempt; the final
    expired lease becomes terminal rather than looping forever.
    """

    lease_seconds = max(1, int(settings.job_timeout_seconds))
    claim_token = f"{worker_name}:{uuid4()}"
    query = """
        WITH claimed AS (
            UPDATE jobs
            SET status = CASE
                    WHEN status = 'running' AND attempts + 1 >= max_attempts
                        THEN 'dead'
                    ELSE 'running'
                END,
                attempts = CASE
                    WHEN status = 'running' AND attempts < max_attempts
                        THEN attempts + 1
                    ELSE attempts
                END,
                locked_at = CASE
                    WHEN status = 'running' AND attempts + 1 >= max_attempts
                        THEN NULL
                    ELSE NOW()
                END,
                locked_by = CASE
                    WHEN status = 'running' AND attempts + 1 >= max_attempts
                        THEN NULL
                    ELSE %s
                END,
                run_after = NULL,
                last_error = CASE
                    WHEN status = 'running' AND attempts + 1 >= max_attempts
                        THEN 'execution_lease_expired'
                    ELSE NULL
                END,
                updated_at = NOW()
            WHERE id = %s
              AND (
                  (
                      status IN (%s, %s)
                      AND (run_after IS NULL OR run_after <= NOW())
                  )
                  OR (
                      status = 'running'
                      AND (
                          locked_at IS NULL
                          OR locked_at <= NOW() - (%s * INTERVAL '1 second')
                      )
                  )
              )
            RETURNING *
        )
        SELECT * FROM claimed
        WHERE status = 'running';
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    claim_token,
                    job_id,
                    JobStatus.QUEUED.value,
                    JobStatus.FAILED.value,
                    lease_seconds,
                ),
            )
            row = cursor.fetchone()
    return _as_record(row) if row is not None else None


def renew_job_execution_lease(
    settings: SharedSettings,
    job_id: str,
    *,
    claim_token: str,
) -> bool:
    """Extend a running job's lease only while the same worker still owns it."""
    query = """
        UPDATE jobs
        SET locked_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'running'
          AND locked_by = %s;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (job_id, claim_token))
            return cursor.rowcount > 0


def mark_job_succeeded(
    settings: SharedSettings,
    job_id: str,
    *,
    result: Any | None = None,
    base_payload: dict[str, Any] | None = None,
    claim_token: str,
) -> bool:
    """Mark successful completion."""
    payload: Any = _UNSET
    if result is not None:
        merged_payload = dict(base_payload or {})
        merged_payload["result"] = result
        payload = merged_payload

    return _mark_job(
        settings,
        job_id,
        status=JobStatus.SUCCEEDED,
        payload=payload,
        locked_at=None,
        locked_by=None,
        run_after=None,
        last_error=None,
        claim_token=claim_token,
    )


def mark_job_retry(
    settings: SharedSettings,
    job_id: str,
    *,
    attempts: int,
    run_after: datetime,
    last_error: str,
    claim_token: str,
) -> bool:
    """Record a retryable failure using `_mark_job` with `JobStatus.FAILED`.

    This marks a non-terminal failure state while attempts are still below the
    max-attempts threshold. Callers should use this for retry scheduling paths;
    terminal failures should use `mark_job_dead`, which writes `JobStatus.DEAD`.
    """
    return _mark_job(
        settings,
        job_id,
        status=JobStatus.FAILED,
        attempts=attempts,
        run_after=run_after,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
        claim_token=claim_token,
    )


def mark_job_dead(
    settings: SharedSettings,
    job_id: str,
    *,
    attempts: int,
    last_error: str,
    claim_token: str,
) -> bool:
    """Mark a job as permanently dead."""
    return _mark_job(
        settings,
        job_id,
        status=JobStatus.DEAD,
        attempts=attempts,
        run_after=None,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
        claim_token=claim_token,
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
    else:
        # A process can persist the idempotent row and fail before its first
        # broker delivery. The conditional reservation in
        # ``redeliver_queued_job`` both recovers that gap after a bounded
        # backoff and prevents an upstream retry storm from appending a broker
        # message for every duplicate request.
        redeliver_queued_job(
            queue,
            settings=settings,
            job_id=job_id,
            minimum_age_seconds=_DUPLICATE_ENQUEUE_REDELIVERY_BACKOFF_SECONDS,
        )
    return EnqueuedJob(id=job_id, created=created)


def job_is_terminal(status: JobStatus) -> bool:
    """Return true when the job should not be executed again."""
    return status in {JobStatus.SUCCEEDED, JobStatus.DEAD, JobStatus.CANCELED}
