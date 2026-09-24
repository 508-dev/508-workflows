"""Shared queue and job persistence helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
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


def get_postgres_connection(
    settings: SharedSettings,
    *,
    connect_timeout_seconds: float | None = None,
    statement_timeout_seconds: float | None = None,
) -> Connection:
    """Create a PostgreSQL connection with optional operation deadlines."""
    kwargs: dict[str, Any] = {}
    if connect_timeout_seconds is not None:
        kwargs["connect_timeout"] = max(
            1,
            int(float(connect_timeout_seconds)),
        )
    if statement_timeout_seconds is not None:
        statement_timeout_ms = max(1, int(float(statement_timeout_seconds) * 1000))
        kwargs["options"] = f"-c statement_timeout={statement_timeout_ms}"
    return connect(settings.postgres_url, **kwargs)


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
    expected_locked_at: datetime | None = None,
    expected_locked_by: str | None = None,
) -> bool:
    if (expected_locked_at is None) != (expected_locked_by is None):
        raise ValueError("Claim fencing requires both lock timestamp and owner.")
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
    claim_clause = ""
    if expected_locked_at is not None and expected_locked_by is not None:
        claim_clause = """
          AND status = %s
          AND locked_at = %s
          AND locked_by = %s
        """
        params.extend(
            (
                JobStatus.RUNNING.value,
                expected_locked_at,
                expected_locked_by,
            )
        )

    query = f"""
        UPDATE jobs
        SET {", ".join(updates)}
        WHERE id = %s
        {claim_clause};
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(trusted_sql(query), params)
            return cursor.rowcount == 1


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


def _claim_fence(claim: JobRecord) -> tuple[datetime, str]:
    """Return the immutable identity of an active durable claim."""
    if claim.status != JobStatus.RUNNING:
        raise ValueError("Job claim must be running before it can be resolved.")
    if claim.locked_at is None or claim.locked_by is None:
        raise ValueError("Job claim is missing its lock identity.")
    return claim.locked_at, claim.locked_by


def claim_job(
    settings: SharedSettings,
    job_id: str,
    *,
    worker_name: str,
    reclaim_running_job_type: str | None = None,
    reclaim_running_after_seconds: float | None = None,
) -> JobRecord | None:
    """Atomically claim an eligible job for one worker.

    Initial deliveries are ``queued``. Retry deliveries remain ``failed`` until
    their durable ``run_after`` time, so both states can be claimed exactly
    once. A caller may additionally reclaim one job type after its bounded
    running lease expires. A missing row means another worker has already
    claimed the job, it is terminal, or its retry delay or lease has not
    elapsed yet. Reclaiming an expired lease consumes an attempt; an exhausted
    reclaim is returned as ``dead`` so the caller can perform type-specific
    terminal cleanup without executing the handler again.
    """
    if (reclaim_running_job_type is None) != (reclaim_running_after_seconds is None):
        raise ValueError("Running-job reclaim requires both a job type and timeout.")
    if reclaim_running_after_seconds is not None and reclaim_running_after_seconds <= 0:
        raise ValueError("Running-job reclaim timeout must be positive.")
    # Replicas commonly share ``worker_name``. Add a per-claim nonce so the
    # persisted owner is also an unambiguous fencing token.
    claim_owner = f"{worker_name}:{uuid4()}"

    eligible_clause = """
          status IN (%s, %s)
          AND (run_after IS NULL OR run_after <= NOW())
    """
    eligibility_params: tuple[Any, ...] = (
        JobStatus.QUEUED.value,
        JobStatus.FAILED.value,
    )
    if reclaim_running_job_type is not None:
        eligible_clause = f"""
          (({eligible_clause})
           OR (status = %s
               AND type = %s
               AND locked_at IS NOT NULL
               AND locked_at <= NOW() - (%s * INTERVAL '1 second')))
        """
        eligibility_params += (
            JobStatus.RUNNING.value,
            reclaim_running_job_type,
            reclaim_running_after_seconds,
        )

    query = f"""
        WITH eligible AS (
            SELECT
                id,
                status = %s AS reclaiming,
                status = %s AND attempts + 1 >= max_attempts AS exhausted
            FROM jobs
            WHERE id = %s
              AND ({eligible_clause})
            FOR UPDATE
        )
        UPDATE jobs AS jobs
        SET
            status = CASE
                WHEN eligible.exhausted THEN %s
                ELSE %s
            END,
            attempts = CASE
                WHEN eligible.reclaiming THEN jobs.attempts + 1
                ELSE jobs.attempts
            END,
            locked_at = CASE WHEN eligible.exhausted THEN NULL ELSE NOW() END,
            locked_by = CASE WHEN eligible.exhausted THEN NULL ELSE %s END,
            run_after = NULL,
            last_error = CASE
                WHEN eligible.exhausted THEN %s
                ELSE NULL
            END,
            updated_at = NOW()
        FROM eligible
        WHERE jobs.id = eligible.id
        RETURNING jobs.*;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    JobStatus.RUNNING.value,
                    JobStatus.RUNNING.value,
                    job_id,
                    *eligibility_params,
                    JobStatus.DEAD.value,
                    JobStatus.RUNNING.value,
                    claim_owner,
                    "Worker lease expired before completion.",
                ),
            )
            row = cursor.fetchone()
    return _as_record(row) if row is not None else None


def mark_job_succeeded(
    settings: SharedSettings,
    claim: JobRecord,
    *,
    result: Any | None = None,
    base_payload: dict[str, Any] | None = None,
) -> bool:
    """Mark successful completion."""
    locked_at, locked_by = _claim_fence(claim)
    payload: Any = _UNSET
    if result is not None:
        merged_payload = dict(base_payload or {})
        merged_payload["result"] = result
        payload = merged_payload

    return _mark_job(
        settings,
        claim.id,
        status=JobStatus.SUCCEEDED,
        payload=payload,
        locked_at=None,
        locked_by=None,
        run_after=None,
        last_error=None,
        expected_locked_at=locked_at,
        expected_locked_by=locked_by,
    )


def mark_job_retry(
    settings: SharedSettings,
    claim: JobRecord,
    *,
    attempts: int,
    run_after: datetime,
    last_error: str,
) -> bool:
    """Record a retryable failure using `_mark_job` with `JobStatus.FAILED`.

    This marks a non-terminal failure state while attempts are still below the
    max-attempts threshold. Callers should use this for retry scheduling paths;
    terminal failures should use `mark_job_dead`, which writes `JobStatus.DEAD`.
    """
    locked_at, locked_by = _claim_fence(claim)
    return _mark_job(
        settings,
        claim.id,
        status=JobStatus.FAILED,
        attempts=attempts,
        run_after=run_after,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
        expected_locked_at=locked_at,
        expected_locked_by=locked_by,
    )


def mark_job_dead(
    settings: SharedSettings,
    claim: JobRecord,
    *,
    attempts: int,
    last_error: str,
) -> bool:
    """Mark a job as permanently dead."""
    locked_at, locked_by = _claim_fence(claim)
    return _mark_job(
        settings,
        claim.id,
        status=JobStatus.DEAD,
        attempts=attempts,
        run_after=None,
        last_error=last_error,
        locked_at=None,
        locked_by=None,
        expected_locked_at=locked_at,
        expected_locked_by=locked_by,
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
    redispatch_existing_queued: bool = False,
) -> EnqueuedJob:
    """Create a job record and hand it to the configured queue adapter.

    ``redispatch_existing_queued`` is an opt-in recovery path for callers
    whose durable work is still queued after an interrupted handoff to Redis.
    It deliberately does not redispatch running, retrying, or terminal jobs.
    """
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
    elif redispatch_existing_queued:
        existing = get_job(settings, job_id)
        if existing is not None and existing.status == JobStatus.QUEUED:
            # Preserve the durable schedule rather than trusting a retry's
            # call-site value, which may no longer describe this job.
            queue.enqueue(job_id, run_at=existing.run_after)
    return EnqueuedJob(id=job_id, created=created)


def job_is_terminal(status: JobStatus) -> bool:
    """Return true when the job should not be executed again."""
    return status in {JobStatus.SUCCEEDED, JobStatus.DEAD, JobStatus.CANCELED}
