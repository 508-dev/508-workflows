"""Durable, policy-bounded recurring agent schedule primitives.

Schedules can retain a legacy frozen tool plan or a bounded agent-loop
capability envelope.  The latter stores an objective and an explicit catalog
of read-only tool IDs; it never grants a permission, unlocks a write, or lets a
future tool silently join an existing schedule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from pydantic import BaseModel, Field, field_validator, model_validator
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


AGENT_SCHEDULE_DEFINITION_VERSION = 1
AGENT_SCHEDULE_ALLOWED_TOOL_NAMES = frozenset(
    {
        "github_issue.search_issues",
        "crm_read.search_contacts",
        "billing_read.search_invoices",
        "billing_read.get_invoice_summary",
        "billing_read.search_suppliers",
        "erp_read.search_projects",
        "erp_read.get_project_summary",
        "onboarding_read.get_summary",
        "web_read.search",
        "web_read.extract",
    }
)
MAX_AGENT_SCHEDULE_ACTIONS = 3
MAX_AGENT_SCHEDULE_TOOL_ALLOWLIST = 16
MAX_AGENT_SCHEDULE_PLANNING_STEPS = 3
MAX_AGENT_SCHEDULE_OUTPUT_CHARS = 8_000
MAX_AGENT_SCHEDULE_ERROR_CHARS = 2_000


class AgentScheduleStatus(StrEnum):
    """Lifecycle state for a persisted recurring agent schedule."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class AgentScheduleRunStatus(StrEnum):
    """Lifecycle state for one scheduled or manually requested occurrence."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentScheduleRunTrigger(StrEnum):
    """Source that created one schedule run."""

    SCHEDULE = "schedule"
    MANUAL = "manual"


class AgentScheduleExecutionMode(StrEnum):
    """How a recurring schedule selects its read-only tool calls."""

    FROZEN_ACTIONS = "frozen_actions"
    AGENT_LOOP = "agent_loop"


class AgentScheduleAction(BaseModel):
    """One frozen, read-only tool call permitted for a recurring schedule."""

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def _validate_supported_read_only_action(self) -> "AgentScheduleAction":
        if self.tool_name not in AGENT_SCHEDULE_ALLOWED_TOOL_NAMES:
            raise ValueError("scheduled tool is not supported")
        if self.tool_name == "github_issue.search_issues":
            _validate_github_issue_search_arguments(self.arguments)
        return self


class AgentScheduleDiscordDelivery(BaseModel):
    """Explicit Discord destination for a schedule report."""

    type: Literal["discord_channel"] = "discord_channel"
    guild_id: str
    channel_id: str

    @field_validator("guild_id", "channel_id")
    @classmethod
    def _validate_discord_snowflake(cls, value: str) -> str:
        return _normalize_discord_snowflake(value)


class AgentScheduleDefinition(BaseModel):
    """Task, capability, and delivery policy stored with a schedule."""

    version: int = AGENT_SCHEDULE_DEFINITION_VERSION
    prompt: str = Field(min_length=1, max_length=4_000)
    execution_mode: AgentScheduleExecutionMode = (
        AgentScheduleExecutionMode.FROZEN_ACTIONS
    )
    # Legacy schedules replay this immutable action list. New agent-loop
    # schedules intentionally leave it empty and use ``tool_allowlist``.
    actions: list[AgentScheduleAction] = Field(
        default_factory=list,
        max_length=MAX_AGENT_SCHEDULE_ACTIONS,
    )
    tool_allowlist: list[str] = Field(
        default_factory=list,
        max_length=MAX_AGENT_SCHEDULE_TOOL_ALLOWLIST,
    )
    max_planning_steps: int = Field(
        default=MAX_AGENT_SCHEDULE_PLANNING_STEPS,
        ge=1,
        le=MAX_AGENT_SCHEDULE_PLANNING_STEPS,
    )
    delivery: AgentScheduleDiscordDelivery
    max_runtime_seconds: int = Field(default=120, ge=5, le=300)
    # A model only sees a minimized issue metadata payload when the schedule
    # owner has explicitly classified every source as public. Private data
    # therefore stays on the deterministic report path by default.
    summary_mode: Literal[
        "deterministic",
        "model_for_public_data",
    ] = "deterministic"
    sources_are_public: bool = False

    @field_validator("prompt")
    @classmethod
    def _normalize_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt is required")
        return normalized

    @field_validator("tool_allowlist")
    @classmethod
    def _normalize_tool_allowlist(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw_name in value:
            tool_name = str(raw_name or "").strip()
            if not tool_name:
                raise ValueError("scheduled tool names must not be blank")
            if tool_name not in AGENT_SCHEDULE_ALLOWED_TOOL_NAMES:
                raise ValueError("scheduled tool is not supported")
            if tool_name not in normalized:
                normalized.append(tool_name)
        return normalized

    @model_validator(mode="after")
    def _validate_summary_data_boundary(self) -> "AgentScheduleDefinition":
        if self.version != AGENT_SCHEDULE_DEFINITION_VERSION:
            raise ValueError("unsupported schedule definition version")
        if self.summary_mode == "model_for_public_data" and not self.sources_are_public:
            raise ValueError(
                "model summaries require an explicit public-source classification"
            )
        if self.execution_mode is AgentScheduleExecutionMode.FROZEN_ACTIONS:
            if not self.actions:
                raise ValueError("frozen-action schedules require at least one action")
            if self.tool_allowlist:
                raise ValueError(
                    "frozen-action schedules cannot include a tool allowlist"
                )
            return self
        if self.actions:
            raise ValueError("agent-loop schedules cannot include frozen actions")
        if not self.tool_allowlist:
            raise ValueError("agent-loop schedules require at least one allowed tool")
        # The loop may call a model to select tools, but its observations are
        # always a bounded safe projection. The older public-data switch only
        # applies to the legacy GitHub result summarizer.
        if self.summary_mode != "deterministic" or self.sources_are_public:
            raise ValueError(
                "agent-loop schedules use safe observations and cannot opt into "
                "legacy public-data summaries"
            )
        return self


@dataclass(frozen=True)
class AgentScheduleRecord:
    """Typed persisted recurring schedule."""

    id: str
    organization_id: str
    guild_id: str
    owner_discord_user_id: str
    name: str
    cron_expression: str
    timezone: str
    definition: AgentScheduleDefinition
    allowed_scopes: frozenset[str]
    status: AgentScheduleStatus
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentScheduleRunRecord:
    """Typed persisted execution attempt for one schedule occurrence."""

    id: str
    schedule_id: str
    occurrence_at: datetime
    trigger: AgentScheduleRunTrigger
    status: AgentScheduleRunStatus
    job_id: str | None
    started_at: datetime | None
    finished_at: datetime | None
    output: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def _normalize_discord_snowflake(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or not normalized.isdecimal() or int(normalized) <= 0:
        raise ValueError("Discord IDs must be positive decimal snowflakes")
    return normalized


def _validate_github_issue_search_arguments(arguments: dict[str, Any]) -> None:
    allowed_keys = {"query", "repository", "state", "limit"}
    unknown_keys = set(arguments) - allowed_keys
    if unknown_keys:
        raise ValueError("scheduled GitHub issue search has unknown arguments")

    repository = str(arguments.get("repository") or "").strip()
    if not repository or "/" not in repository or repository.startswith("/"):
        raise ValueError("scheduled GitHub issue search requires owner/repository")
    owner, _, repo = repository.partition("/")
    if not owner.strip() or not repo.strip() or "/" in repo:
        raise ValueError("scheduled GitHub repository must be owner/repository")

    query = str(arguments.get("query") or "").strip()
    if len(query) > 512:
        raise ValueError("scheduled GitHub issue query is too long")

    state = str(arguments.get("state") or "open").strip().casefold()
    if state not in {"open", "closed", "all"}:
        raise ValueError("scheduled GitHub issue state must be open, closed, or all")

    raw_limit = arguments.get("limit", 10)
    if isinstance(raw_limit, bool):
        raise ValueError("scheduled GitHub issue limit must be a number")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("scheduled GitHub issue limit must be a number") from exc
    if limit < 1 or limit > 20:
        raise ValueError("scheduled GitHub issue limit must be between 1 and 20")


def _normalize_schedule_name(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("schedule name is required")
    if len(normalized) > 140:
        raise ValueError("schedule name must be at most 140 characters")
    return normalized


def _normalize_cron_expression(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized.split()) != 5:
        raise ValueError("cron expression must use exactly five fields")
    try:
        valid = croniter.is_valid(normalized)
    except (CroniterBadCronError, ValueError) as exc:
        raise ValueError("invalid cron expression") from exc
    if not valid:
        raise ValueError("invalid cron expression")
    return normalized


def _timezone(value: str) -> ZoneInfo:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("timezone is required")
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc


def next_agent_schedule_occurrence(
    cron_expression: str,
    timezone_name: str,
    *,
    after: datetime,
) -> datetime:
    """Return the next real occurrence in UTC, respecting the IANA timezone."""

    normalized_cron = _normalize_cron_expression(cron_expression)
    zone = _timezone(timezone_name)
    normalized_after = _utc_datetime(after)
    try:
        iterator = croniter(
            normalized_cron,
            normalized_after.astimezone(zone),
            ret_type=datetime,
            day_or=True,
        )
        next_local = iterator.get_next(datetime)
    except (CroniterBadCronError, CroniterBadDateError, ValueError) as exc:
        raise ValueError(
            "cron expression does not produce a future occurrence"
        ) from exc
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=zone)
    return next_local.astimezone(timezone.utc)


def validate_agent_schedule_timing(
    cron_expression: str,
    timezone_name: str,
    *,
    now: datetime,
    minimum_interval_seconds: int,
) -> tuple[str, str, datetime]:
    """Validate a five-field cron schedule and return normalized next run time."""

    normalized_cron = _normalize_cron_expression(cron_expression)
    zone = _timezone(timezone_name)
    normalized_timezone = str(zone)
    first = next_agent_schedule_occurrence(
        normalized_cron,
        normalized_timezone,
        after=now,
    )
    second = next_agent_schedule_occurrence(
        normalized_cron,
        normalized_timezone,
        after=first,
    )
    if (second - first).total_seconds() < max(60, minimum_interval_seconds):
        raise ValueError(
            f"schedule must run no more often than every {minimum_interval_seconds} seconds"
        )
    return normalized_cron, normalized_timezone, first


def create_agent_schedule(
    settings: SharedSettings,
    *,
    organization_id: str,
    guild_id: str,
    owner_discord_user_id: str,
    name: str,
    cron_expression: str,
    timezone_name: str,
    definition: AgentScheduleDefinition,
    allowed_scopes: Iterable[str],
    now: datetime | None = None,
) -> AgentScheduleRecord:
    """Persist one immutable execution envelope and its first due time."""

    normalized_now = _utc_datetime(now or datetime.now(tz=timezone.utc))
    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    normalized_owner_id = _normalize_discord_snowflake(owner_discord_user_id)
    normalized_organization_id = str(organization_id or "").strip()
    if not normalized_organization_id or len(normalized_organization_id) > 128:
        raise ValueError("organization_id is required")
    if definition.delivery.guild_id != normalized_guild_id:
        raise ValueError("schedule delivery must stay inside its configured guild")

    normalized_cron, normalized_timezone, next_run_at = validate_agent_schedule_timing(
        cron_expression,
        timezone_name,
        now=normalized_now,
        minimum_interval_seconds=settings.agent_schedule_min_interval_seconds,
    )
    normalized_scopes = _normalize_scopes(allowed_scopes)
    if not normalized_scopes:
        raise ValueError("schedule requires at least one approved execution scope")

    schedule_id = str(uuid4())
    query = """
        INSERT INTO agent_schedules (
            id,
            organization_id,
            guild_id,
            owner_discord_user_id,
            name,
            cron_expression,
            timezone,
            definition,
            allowed_scopes,
            status,
            next_run_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    schedule_id,
                    normalized_organization_id,
                    normalized_guild_id,
                    normalized_owner_id,
                    _normalize_schedule_name(name),
                    normalized_cron,
                    normalized_timezone,
                    Jsonb(definition.model_dump(mode="json")),
                    Jsonb(sorted(normalized_scopes)),
                    AgentScheduleStatus.ACTIVE.value,
                    next_run_at,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("unable to create agent schedule")
    return _as_schedule_record(row)


def get_agent_schedule(
    settings: SharedSettings,
    *,
    schedule_id: str,
) -> AgentScheduleRecord | None:
    """Load one persisted schedule without changing its lifecycle."""

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM agent_schedules WHERE id = %s", (schedule_id,)
            )
            row = cursor.fetchone()
    return _as_schedule_record(row) if row is not None else None


def list_agent_schedules(
    settings: SharedSettings,
    *,
    guild_id: str,
    limit: int = 100,
    include_archived: bool = False,
) -> list[AgentScheduleRecord]:
    """List a guild's schedules newest first for an admin control surface."""

    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    bounded_limit = max(1, min(int(limit), 500))
    query = """
        SELECT *
        FROM agent_schedules
        WHERE guild_id = %s
          AND (%s OR status <> 'archived')
        ORDER BY created_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query, (normalized_guild_id, include_archived, bounded_limit)
            )
            rows = cursor.fetchall()
    return [_as_schedule_record(row) for row in rows]


def pause_agent_schedule(
    settings: SharedSettings,
    *,
    schedule_id: str,
    guild_id: str,
) -> AgentScheduleRecord | None:
    """Pause a schedule without deleting its frozen definition or history."""

    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    query = """
        UPDATE agent_schedules
        SET status = %s,
            next_run_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND guild_id = %s
          AND status <> 'archived'
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (AgentScheduleStatus.PAUSED.value, schedule_id, normalized_guild_id),
            )
            row = cursor.fetchone()
    return _as_schedule_record(row) if row is not None else None


def resume_agent_schedule(
    settings: SharedSettings,
    *,
    schedule_id: str,
    guild_id: str,
    now: datetime | None = None,
) -> AgentScheduleRecord | None:
    """Resume a schedule from its next future cron occurrence, never backfill."""

    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    normalized_now = _utc_datetime(now or datetime.now(tz=timezone.utc))
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT * FROM agent_schedules
                WHERE id = %s AND guild_id = %s
                FOR UPDATE
                """,
                (schedule_id, normalized_guild_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            schedule = _as_schedule_record(row)
            if schedule.status is AgentScheduleStatus.ARCHIVED:
                return None
            next_run_at = next_agent_schedule_occurrence(
                schedule.cron_expression,
                schedule.timezone,
                after=normalized_now,
            )
            cursor.execute(
                """
                UPDATE agent_schedules
                SET status = %s,
                    next_run_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (AgentScheduleStatus.ACTIVE.value, next_run_at, schedule_id),
            )
            updated = cursor.fetchone()
    return _as_schedule_record(updated) if updated is not None else None


def archive_agent_schedule(
    settings: SharedSettings,
    *,
    schedule_id: str,
    guild_id: str,
) -> AgentScheduleRecord | None:
    """Retire a schedule while retaining an auditable immutable definition."""

    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    query = """
        UPDATE agent_schedules
        SET status = %s,
            next_run_at = NULL,
            updated_at = NOW()
        WHERE id = %s AND guild_id = %s
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (AgentScheduleStatus.ARCHIVED.value, schedule_id, normalized_guild_id),
            )
            row = cursor.fetchone()
    return _as_schedule_record(row) if row is not None else None


def create_manual_agent_schedule_run(
    settings: SharedSettings,
    *,
    schedule_id: str,
    guild_id: str,
    now: datetime | None = None,
) -> AgentScheduleRunRecord | None:
    """Queue a manual one-off run for an active schedule."""

    normalized_guild_id = _normalize_discord_snowflake(guild_id)
    occurrence_at = _utc_datetime(now or datetime.now(tz=timezone.utc))
    run_id = str(uuid4())
    query = """
        INSERT INTO agent_schedule_runs (
            id,
            schedule_id,
            occurrence_at,
            trigger,
            status
        )
        SELECT %s, id, %s, %s, %s
        FROM agent_schedules
        WHERE id = %s
          AND guild_id = %s
          AND status = 'active'
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    run_id,
                    occurrence_at,
                    AgentScheduleRunTrigger.MANUAL.value,
                    AgentScheduleRunStatus.QUEUED.value,
                    schedule_id,
                    normalized_guild_id,
                ),
            )
            row = cursor.fetchone()
    return _as_schedule_run_record(row) if row is not None else None


def create_due_agent_schedule_runs(
    settings: SharedSettings,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> list[AgentScheduleRunRecord]:
    """Atomically claim due schedules, advance them, and create run records.

    PostgreSQL row locks make this safe when multiple API instances have their
    own dispatch loops. Each occurrence is represented before a worker job is
    enqueued, so an enqueue retry cannot create duplicate reports. After a
    restart, each overdue schedule creates at most one catch-up report and is
    then advanced to its next future occurrence; downtime never creates a
    backlog flood.
    """

    normalized_now = _utc_datetime(now or datetime.now(tz=timezone.utc))
    bounded_limit = max(1, min(int(limit), 100))
    created: list[AgentScheduleRunRecord] = []
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM agent_schedules
                WHERE status = 'active'
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= %s
                ORDER BY next_run_at ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (normalized_now, bounded_limit),
            )
            rows = cursor.fetchall()
            for row in rows:
                schedule = _as_schedule_record(row)
                occurrence_at = _utc_datetime(schedule.next_run_at or normalized_now)
                next_run_at = next_agent_schedule_occurrence(
                    schedule.cron_expression,
                    schedule.timezone,
                    after=normalized_now,
                )
                cursor.execute(
                    """
                    UPDATE agent_schedules
                    SET next_run_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (next_run_at, schedule.id),
                )
                cursor.execute(
                    """
                    INSERT INTO agent_schedule_runs (
                        id,
                        schedule_id,
                        occurrence_at,
                        trigger,
                        status
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (schedule_id, occurrence_at) DO NOTHING
                    RETURNING *
                    """,
                    (
                        str(uuid4()),
                        schedule.id,
                        occurrence_at,
                        AgentScheduleRunTrigger.SCHEDULE.value,
                        AgentScheduleRunStatus.QUEUED.value,
                    ),
                )
                run_row = cursor.fetchone()
                if run_row is not None:
                    created.append(_as_schedule_run_record(run_row))
    return created


def list_unenqueued_agent_schedule_runs(
    settings: SharedSettings,
    *,
    limit: int = 100,
) -> list[AgentScheduleRunRecord]:
    """Return queued occurrences that still need a durable worker job."""

    bounded_limit = max(1, min(int(limit), 500))
    query = """
        SELECT *
        FROM agent_schedule_runs
        WHERE status = 'queued'
          AND job_id IS NULL
        ORDER BY created_at ASC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (bounded_limit,))
            rows = cursor.fetchall()
    return [_as_schedule_run_record(row) for row in rows]


def set_agent_schedule_run_job_id(
    settings: SharedSettings,
    *,
    run_id: str,
    job_id: str,
) -> None:
    """Attach the idempotent durable worker job to its schedule occurrence."""

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_schedule_runs
                SET job_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'queued'
                  AND job_id IS NULL
                """,
                (job_id, run_id),
            )


def get_agent_schedule_run(
    settings: SharedSettings,
    *,
    run_id: str,
) -> AgentScheduleRunRecord | None:
    """Load one scheduled occurrence by its durable run identifier."""

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM agent_schedule_runs WHERE id = %s", (run_id,))
            row = cursor.fetchone()
    return _as_schedule_run_record(row) if row is not None else None


def claim_agent_schedule_run(
    settings: SharedSettings,
    *,
    run_id: str,
    reclaim_running_before: datetime | None = None,
) -> AgentScheduleRunRecord | None:
    """Claim a queued/failed occurrence for one worker-backed execution.

    A worker retry may reclaim a failed run. A successfully completed run is
    never re-executed if a transport timeout causes the worker to retry. The
    API may additionally reclaim a run whose lease expired after a process
    crash; callers choose that conservative timestamp explicitly.
    """

    if reclaim_running_before is None:
        query = """
            UPDATE agent_schedule_runs
            SET status = %s,
                started_at = NOW(),
                finished_at = NULL,
                error = NULL,
                updated_at = NOW()
            WHERE id = %s
              AND status IN ('queued', 'failed')
            RETURNING *
        """
        params: tuple[object, ...] = (AgentScheduleRunStatus.RUNNING.value, run_id)
    else:
        query = """
            UPDATE agent_schedule_runs
            SET status = %s,
                started_at = NOW(),
                finished_at = NULL,
                error = NULL,
                updated_at = NOW()
            WHERE id = %s
              AND (
                  status IN ('queued', 'failed')
                  OR (status = 'running' AND started_at <= %s)
              )
            RETURNING *
        """
        params = (
            AgentScheduleRunStatus.RUNNING.value,
            run_id,
            _utc_datetime(reclaim_running_before),
        )
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
    return _as_schedule_run_record(row) if row is not None else None


def complete_agent_schedule_run(
    settings: SharedSettings,
    *,
    run_id: str,
    status: AgentScheduleRunStatus,
    output: str | None = None,
    error: str | None = None,
) -> AgentScheduleRunRecord | None:
    """Persist a terminal run result and record the schedule's last attempt."""

    if status not in {
        AgentScheduleRunStatus.SUCCEEDED,
        AgentScheduleRunStatus.FAILED,
        AgentScheduleRunStatus.SKIPPED,
    }:
        raise ValueError("schedule run completion status must be terminal")

    normalized_output = _bounded_text(output, MAX_AGENT_SCHEDULE_OUTPUT_CHARS)
    normalized_error = _bounded_text(error, MAX_AGENT_SCHEDULE_ERROR_CHARS)
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE agent_schedule_runs
                SET status = %s,
                    output = %s,
                    error = %s,
                    finished_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                  AND status = 'running'
                RETURNING *
                """,
                (status.value, normalized_output, normalized_error, run_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                UPDATE agent_schedules
                SET last_run_at = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (row["finished_at"], row["schedule_id"]),
            )
    return _as_schedule_run_record(row)


def list_agent_schedule_runs(
    settings: SharedSettings,
    *,
    schedule_id: str,
    limit: int = 20,
) -> list[AgentScheduleRunRecord]:
    """List recent execution outcomes for dashboard and Discord inspection."""

    bounded_limit = max(1, min(int(limit), 100))
    query = """
        SELECT *
        FROM agent_schedule_runs
        WHERE schedule_id = %s
        ORDER BY occurrence_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (schedule_id, bounded_limit))
            rows = cursor.fetchall()
    return [_as_schedule_run_record(row) for row in rows]


def _as_schedule_record(row: dict[str, Any]) -> AgentScheduleRecord:
    definition_payload = row.get("definition")
    if not isinstance(definition_payload, dict):
        raise ValueError("agent schedule definition is corrupted")
    return AgentScheduleRecord(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        guild_id=str(row["guild_id"]),
        owner_discord_user_id=str(row["owner_discord_user_id"]),
        name=str(row["name"]),
        cron_expression=str(row["cron_expression"]),
        timezone=str(row["timezone"]),
        definition=AgentScheduleDefinition.model_validate(definition_payload),
        allowed_scopes=frozenset(_normalize_scopes(row.get("allowed_scopes") or [])),
        status=AgentScheduleStatus(str(row["status"])),
        next_run_at=_nullable_utc_datetime(row.get("next_run_at")),
        last_run_at=_nullable_utc_datetime(row.get("last_run_at")),
        created_at=_utc_datetime(row["created_at"]),
        updated_at=_utc_datetime(row["updated_at"]),
    )


def _as_schedule_run_record(row: dict[str, Any]) -> AgentScheduleRunRecord:
    return AgentScheduleRunRecord(
        id=str(row["id"]),
        schedule_id=str(row["schedule_id"]),
        occurrence_at=_utc_datetime(row["occurrence_at"]),
        trigger=AgentScheduleRunTrigger(str(row["trigger"])),
        status=AgentScheduleRunStatus(str(row["status"])),
        job_id=str(row["job_id"]) if row.get("job_id") is not None else None,
        started_at=_nullable_utc_datetime(row.get("started_at")),
        finished_at=_nullable_utc_datetime(row.get("finished_at")),
        output=str(row["output"]) if row.get("output") is not None else None,
        error=str(row["error"]) if row.get("error") is not None else None,
        created_at=_utc_datetime(row["created_at"]),
        updated_at=_utc_datetime(row["updated_at"]),
    )


def _normalize_scopes(values: Iterable[object]) -> set[str]:
    scopes: set[str] = set()
    for value in values:
        scope = str(value or "").strip()
        if scope:
            scopes.add(scope)
    return scopes


def _bounded_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized[:limit]


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _nullable_utc_datetime(value: object) -> datetime | None:
    return _utc_datetime(value) if isinstance(value, datetime) else None
