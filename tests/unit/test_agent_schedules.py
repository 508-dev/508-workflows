"""Unit tests for frozen recurring agent schedule primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from five08.agent import schedules
from five08.agent.schedules import (
    AgentScheduleAction,
    AgentScheduleDefinition,
    AgentScheduleDiscordDelivery,
    AgentScheduleExecutionMode,
    AgentScheduleProposal,
    AgentScheduleRunStatus,
    claim_agent_schedule_run,
    create_manual_agent_schedule_run,
    create_due_agent_schedule_runs,
    get_agent_schedule,
    get_agent_schedule_run,
    validate_agent_schedule_timing,
)
from five08.settings import SharedSettings


def _delivery() -> AgentScheduleDiscordDelivery:
    return AgentScheduleDiscordDelivery(guild_id="1000", channel_id="2000")


def _github_action(**arguments: object) -> AgentScheduleAction:
    return AgentScheduleAction(
        tool_name="github_issue.search_issues",
        arguments={
            "repository": "508-dev/508-workflows",
            "query": "label:bug",
            "state": "open",
            "limit": 10,
            **arguments,
        },
        summary="Search public GitHub issues",
    )


def test_schedule_definition_rejects_model_summary_without_public_classification() -> (
    None
):
    """A saved prompt cannot send observations to a model by default."""

    with pytest.raises(ValidationError, match="public-source classification"):
        AgentScheduleDefinition(
            prompt="Group related issues and recommend priorities.",
            actions=[_github_action()],
            delivery=_delivery(),
            summary_mode="model_for_public_data",
            sources_are_public=False,
        )


def test_schedule_action_rejects_tool_or_argument_expansion() -> None:
    """The persisted envelope only permits the explicitly supported read."""

    with pytest.raises(ValidationError, match="scheduled tool is not supported"):
        AgentScheduleAction(
            tool_name="github_issue.create_issue",
            arguments={"repository": "508-dev/508-workflows", "title": "write"},
            summary="Create a GitHub issue",
        )

    with pytest.raises(ValidationError, match="unknown arguments"):
        _github_action(assignee="someone")


def test_agent_loop_definition_requires_a_saved_read_only_catalog() -> None:
    """A generic objective cannot acquire tools dynamically at run time."""

    definition = AgentScheduleDefinition(
        prompt="Inspect onboarding health and report blockers.",
        execution_mode=AgentScheduleExecutionMode.AGENT_LOOP,
        tool_allowlist=["onboarding_read.get_summary", "erp_read.search_projects"],
        delivery=_delivery(),
    )

    assert definition.actions == []
    assert definition.tool_allowlist == [
        "onboarding_read.get_summary",
        "erp_read.search_projects",
    ]
    with pytest.raises(ValidationError, match="at least one allowed tool"):
        AgentScheduleDefinition(
            prompt="Inspect onboarding health.",
            execution_mode=AgentScheduleExecutionMode.AGENT_LOOP,
            delivery=_delivery(),
        )
    with pytest.raises(ValidationError, match="cannot include frozen actions"):
        AgentScheduleDefinition(
            prompt="Inspect onboarding health.",
            execution_mode=AgentScheduleExecutionMode.AGENT_LOOP,
            actions=[_github_action()],
            tool_allowlist=["onboarding_read.get_summary"],
            delivery=_delivery(),
        )


def test_agent_schedule_proposal_excludes_delivery_and_capability_controls() -> None:
    """A model can describe a report, but cannot choose its authority boundary."""

    proposal = AgentScheduleProposal.model_validate(
        {
            "name": "  Weekly onboarding health  ",
            "cron_expression": " 0 9 * * 1 ",
            "timezone": "Asia/Tokyo",
            "prompt": "  Inspect onboarding health and report blockers.  ",
        }
    )

    assert proposal.model_dump() == {
        "name": "Weekly onboarding health",
        "cron_expression": "0 9 * * 1",
        "timezone": "Asia/Tokyo",
        "prompt": "Inspect onboarding health and report blockers.",
    }
    with pytest.raises(ValidationError, match="cron expression"):
        AgentScheduleProposal.model_validate(
            {
                "name": "Hourly report",
                "cron_expression": "not cron",
                "timezone": "UTC",
                "prompt": "Inspect onboarding health.",
            }
        )


def test_schedule_timing_enforces_minimum_cadence_and_preserves_timezone() -> None:
    """Five-field cron schedules are bounded and resolved to UTC correctly."""

    now = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="no more often than every 300 seconds"):
        validate_agent_schedule_timing(
            "* * * * *",
            "UTC",
            now=now,
            minimum_interval_seconds=300,
        )

    expression, timezone_name, next_run_at = validate_agent_schedule_timing(
        "0 9 * * 1",
        "Asia/Tokyo",
        now=now,
        minimum_interval_seconds=300,
    )

    assert expression == "0 9 * * 1"
    assert timezone_name == "Asia/Tokyo"
    assert next_run_at == datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)


def test_schedule_timing_rejects_a_later_tight_cron_cluster() -> None:
    """Validation must not accept a sparse first gap then an hourly cluster."""

    with pytest.raises(ValueError, match="no more often than every 7200 seconds"):
        validate_agent_schedule_timing(
            "0 0,1 * * *",
            "UTC",
            now=datetime(2026, 7, 28, 0, 30, tzinfo=timezone.utc),
            minimum_interval_seconds=7_200,
        )


def test_schedule_timing_reports_the_effective_minimum_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured value below one minute must not leak into the error text."""

    now = datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc)
    occurrences = iter(
        [
            now.replace(minute=1),
            now.replace(minute=1, second=30),
        ]
    )
    monkeypatch.setattr(
        schedules,
        "next_agent_schedule_occurrence",
        lambda *_args, **_kwargs: next(occurrences),
    )

    with pytest.raises(ValueError, match="no more often than every 60 seconds"):
        validate_agent_schedule_timing(
            "* * * * *",
            "UTC",
            now=now,
            minimum_interval_seconds=10,
        )


def test_schedule_ids_are_rejected_before_opening_a_database_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed path IDs must be ordinary misses instead of PostgreSQL errors."""

    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    assert get_agent_schedule(SharedSettings(), schedule_id="not-a-uuid") is None
    assert get_agent_schedule_run(SharedSettings(), run_id="not-a-uuid") is None
    assert (
        create_manual_agent_schedule_run(
            SharedSettings(),
            schedule_id="not-a-uuid",
            guild_id="1000",
        )
        is None
    )


class _FakeScheduleCursor:
    def __init__(
        self,
        *,
        schedule_row: dict[str, Any],
        run_row: dict[str, Any],
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self._schedule_row = schedule_row
        self._run_row = run_row
        self._current_query = ""

    def __enter__(self) -> "_FakeScheduleCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.calls.append((query, params))
        self._current_query = query

    def fetchall(self) -> list[dict[str, Any]]:
        if "FROM agent_schedules" in self._current_query:
            return [self._schedule_row]
        return []

    def fetchone(self) -> dict[str, Any] | None:
        if "INSERT INTO agent_schedule_runs" in self._current_query:
            return self._run_row
        if "UPDATE agent_schedule_runs" in self._current_query:
            return self._run_row
        return None


class _FakeScheduleConnection:
    def __init__(self, cursor: _FakeScheduleCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_FakeScheduleConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> _FakeScheduleCursor:
        return self._cursor


class _ManualRunCursor:
    def __init__(self, rows: list[dict[str, Any] | None]) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self._rows = rows

    def __enter__(self) -> "_ManualRunCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows.pop(0)


class _ManualRunConnection:
    def __init__(self, cursor: _ManualRunCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "_ManualRunConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> _ManualRunCursor:
        return self._cursor


def test_manual_run_requests_coalesce_within_the_schedule_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeat request returns the prior run without inserting a second one."""

    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    schedule_id = "00000000-0000-0000-0000-000000000010"
    existing_run = {
        "id": "00000000-0000-0000-0000-000000000011",
        "schedule_id": schedule_id,
        "occurrence_at": now,
        "trigger": "manual",
        "status": "queued",
        "job_id": None,
        "started_at": None,
        "finished_at": None,
        "output": None,
        "error": None,
        "delivery_status": "pending",
        "delivery_message_id": None,
        "delivery_claimed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    cursor = _ManualRunCursor([{"id": schedule_id}, existing_run])
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _ManualRunConnection(cursor),
    )

    result = create_manual_agent_schedule_run(
        SharedSettings(),
        schedule_id=schedule_id,
        guild_id="1000",
        now=now,
    )

    assert result is not None
    assert result.created is False
    assert result.run.id == existing_run["id"]
    assert not any(
        "INSERT INTO agent_schedule_runs" in query for query, _ in cursor.calls
    )


def test_due_schedule_creates_one_catch_up_then_advances_past_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A multi-day outage must not enqueue one report for every missed day."""

    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    overdue = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    definition = AgentScheduleDefinition(
        prompt="Report the open GitHub issues.",
        actions=[_github_action()],
        delivery=_delivery(),
    ).model_dump(mode="json")
    schedule_row = {
        "id": "schedule-1",
        "organization_id": "1000",
        "guild_id": "1000",
        "owner_discord_user_id": "1001",
        "name": "Daily GitHub report",
        "cron_expression": "0 9 * * *",
        "timezone": "UTC",
        "definition": definition,
        "allowed_scopes": ["agent:schedule:manage", "github:issue:read"],
        "status": "active",
        "next_run_at": overdue,
        "last_run_at": None,
        "created_at": overdue,
        "updated_at": overdue,
    }
    run_row = {
        "id": "run-1",
        "schedule_id": "schedule-1",
        "occurrence_at": overdue,
        "trigger": "schedule",
        "status": "queued",
        "job_id": None,
        "started_at": None,
        "finished_at": None,
        "output": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    cursor = _FakeScheduleCursor(schedule_row=schedule_row, run_row=run_row)
    connection = _FakeScheduleConnection(cursor)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: connection,
    )

    runs = create_due_agent_schedule_runs(SharedSettings(), now=now)

    assert [run.id for run in runs] == ["run-1"]
    update_params = next(
        params for query, params in cursor.calls if "UPDATE agent_schedules" in query
    )
    assert update_params == (
        datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc),
        "schedule-1",
    )


def test_claim_can_recover_a_running_schedule_after_its_lease_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_before = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    run_id = "00000000-0000-0000-0000-000000000001"
    run_row = {
        "id": run_id,
        "schedule_id": "schedule-1",
        "occurrence_at": stale_before,
        "trigger": "schedule",
        "status": "running",
        "job_id": "job-1",
        "started_at": stale_before,
        "finished_at": None,
        "output": None,
        "error": None,
        "created_at": stale_before,
        "updated_at": stale_before,
    }
    cursor = _FakeScheduleCursor(schedule_row={}, run_row=run_row)
    connection = _FakeScheduleConnection(cursor)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: connection,
    )

    run = claim_agent_schedule_run(
        SharedSettings(),
        run_id=run_id,
        reclaim_running_before=stale_before,
    )

    assert run is not None
    assert run.status is AgentScheduleRunStatus.RUNNING
    query, params = cursor.calls[0]
    assert "status = 'running' AND started_at <= %s" in query
    assert params == (AgentScheduleRunStatus.RUNNING.value, run_id, stale_before)
