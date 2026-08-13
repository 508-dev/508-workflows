"""Unit tests for frozen recurring agent schedule primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from five08.agent import schedules
from five08.agent.schedules import (
    AgentScheduleAction,
    AgentScheduleDefinition,
    AgentScheduleDiscordDelivery,
    AgentScheduleExecutionMode,
    AgentScheduleProposal,
    AgentScheduleRunDeliveryStatus,
    AgentScheduleRunStatus,
    claim_agent_schedule_run,
    claim_agent_schedule_run_delivery,
    complete_agent_schedule_run,
    create_manual_agent_schedule_run,
    create_due_agent_schedule_runs,
    fail_agent_schedule_run,
    get_agent_schedule,
    get_agent_schedule_run,
    list_agent_schedule_runs_needing_queue_reconciliation,
    list_stale_agent_schedule_run_delivery_claims,
    mark_agent_schedule_run_delivery_posted,
    mark_agent_schedule_run_delivery_unknown,
    release_agent_schedule_run_delivery_claim,
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


def _legacy_agent_loop_definition_payload() -> dict[str, Any]:
    """Represent a row written before identifier lookups left model catalogs."""

    return {
        "version": 1,
        "prompt": "Inspect the billing queue and report blockers.",
        "execution_mode": "agent_loop",
        "actions": [],
        "tool_allowlist": ["billing_read.get_invoice_summary"],
        "max_planning_steps": 3,
        "delivery": {
            "type": "discord_channel",
            "guild_id": "1000",
            "channel_id": "2000",
        },
        "max_runtime_seconds": 120,
        "summary_mode": "deterministic",
        "sources_are_public": False,
    }


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


@pytest.mark.parametrize(
    "query",
    ["repo:other/private", "OR -repo:other/private", "(repo:other/private)"],
)
def test_schedule_action_rejects_repository_query_qualifiers(query: str) -> None:
    """A frozen repository cannot be broadened by an issue-search qualifier."""

    with pytest.raises(ValidationError, match="cannot override its repository"):
        _github_action(query=query)


def test_schedule_action_defaults_to_an_explicit_bounded_issue_state() -> None:
    """Persisted actions keep the safe default instead of an unrestricted state."""

    action = _github_action(state="")

    assert action.arguments["state"] == "open"
    with pytest.raises(ValidationError, match="open or closed"):
        _github_action(state="all")


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
    with pytest.raises(ValidationError, match="identifier lookup tools"):
        AgentScheduleDefinition(
            prompt="Inspect invoice status.",
            execution_mode=AgentScheduleExecutionMode.AGENT_LOOP,
            tool_allowlist=["billing_read.get_invoice_summary"],
            delivery=_delivery(),
        )


def test_frozen_schedule_can_retain_an_explicit_identifier_lookup() -> None:
    """The model-only catalog restriction does not alter deterministic plans."""

    definition = AgentScheduleDefinition(
        prompt="Report the saved invoice summary.",
        actions=[
            AgentScheduleAction(
                tool_name="billing_read.get_invoice_summary",
                arguments={"invoice_type": "sales", "invoice_id": "SINV-0001"},
                summary="Read the saved sales invoice",
            )
        ],
        delivery=_delivery(),
    )

    assert definition.actions[0].tool_name == "billing_read.get_invoice_summary"


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
        if "FROM agent_schedule_runs AS runs" in self._current_query:
            return [self._run_row]
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


def test_due_dispatch_loads_legacy_identifier_catalog_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A historic schedule stays readable until execution rejects its catalog."""

    now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    overdue = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    schedule_row = {
        "id": "schedule-1",
        "organization_id": "1000",
        "guild_id": "1000",
        "owner_discord_user_id": "1001",
        "name": "Legacy invoice report",
        "cron_expression": "0 9 * * *",
        "timezone": "UTC",
        "definition": _legacy_agent_loop_definition_payload(),
        "allowed_scopes": ["agent:schedule:manage", "billing:invoice:read"],
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
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _FakeScheduleConnection(cursor),
    )

    runs = create_due_agent_schedule_runs(SharedSettings(), now=now)

    assert [run.id for run in runs] == ["run-1"]
    loaded = schedules._as_schedule_record(schedule_row)  # noqa: SLF001
    assert loaded.definition.tool_allowlist == ["billing_read.get_invoice_summary"]


def test_legacy_catalog_cannot_be_written_as_a_new_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compatibility deserialization never grants a path to copy it forward."""

    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    row = {
        "id": "schedule-1",
        "organization_id": "1000",
        "guild_id": "1000",
        "owner_discord_user_id": "1001",
        "name": "Legacy invoice report",
        "cron_expression": "0 9 * * *",
        "timezone": "UTC",
        "definition": _legacy_agent_loop_definition_payload(),
        "allowed_scopes": ["agent:schedule:manage", "billing:invoice:read"],
        "status": "active",
        "next_run_at": now,
        "last_run_at": None,
        "created_at": now,
        "updated_at": now,
    }
    legacy_definition = schedules._as_schedule_record(row).definition  # noqa: SLF001
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    with pytest.raises(ValidationError, match="identifier lookup tools"):
        schedules.create_agent_schedule(
            SharedSettings(),
            organization_id="1000",
            guild_id="1000",
            owner_discord_user_id="1001",
            name="Copied legacy schedule",
            cron_expression="0 9 * * *",
            timezone_name="UTC",
            definition=legacy_definition,
            allowed_scopes={"agent:schedule:manage", "billing:invoice:read"},
            now=now,
        )


def test_queue_reconciliation_includes_an_attached_queued_worker_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable queued job remains eligible for safe broker redelivery."""

    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    run_row = {
        "id": "run-1",
        "schedule_id": "schedule-1",
        "occurrence_at": now,
        "trigger": "schedule",
        "status": "queued",
        "job_id": "job-1",
        "started_at": None,
        "finished_at": None,
        "output": None,
        "error": None,
        "delivery_status": "pending",
        "delivery_message_id": None,
        "delivery_claimed_at": None,
        "execution_token": None,
        "created_at": now,
        "updated_at": now,
        "worker_job_status": "queued",
        "worker_job_last_error": None,
    }
    cursor = _FakeScheduleCursor(schedule_row={}, run_row=run_row)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _FakeScheduleConnection(cursor),
    )

    reconciliations = list_agent_schedule_runs_needing_queue_reconciliation(
        SharedSettings(),
        limit=25,
    )

    assert len(reconciliations) == 1
    assert reconciliations[0].run.id == "run-1"
    assert reconciliations[0].job_status == "queued"
    query, params = cursor.calls[0]
    assert "runs.status = 'queued' AND jobs.status = 'queued'" in query
    assert params == (25,)


def test_claim_can_recover_a_running_schedule_after_its_lease_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_before = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    run_id = "00000000-0000-0000-0000-000000000001"
    execution_token = "00000000-0000-0000-0000-000000000002"
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
        "execution_token": execution_token,
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
    monkeypatch.setattr(schedules, "uuid4", lambda: execution_token)

    run = claim_agent_schedule_run(
        SharedSettings(),
        run_id=run_id,
        reclaim_running_before=stale_before,
    )

    assert run is not None
    assert run.status is AgentScheduleRunStatus.RUNNING
    query, params = cursor.calls[0]
    assert "status = 'running'" in query
    assert "started_at <= %s" in query
    assert "execution_token = %s" in query
    assert "delivery_status <> %s" in query
    assert params == (
        AgentScheduleRunStatus.RUNNING.value,
        execution_token,
        run_id,
        stale_before,
        AgentScheduleRunDeliveryStatus.CLAIMED.value,
    )


def test_claim_does_not_reclaim_a_run_with_an_in_flight_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale run remains operator-visible while Discord delivery is claimed."""

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: connection,
    )

    claimed = claim_agent_schedule_run(
        SharedSettings(),
        run_id="00000000-0000-0000-0000-000000000001",
        reclaim_running_before=datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc),
    )

    assert claimed is None
    query, params = cursor.execute.call_args.args
    assert "delivery_status <> %s" in query
    assert params[-1] == AgentScheduleRunDeliveryStatus.CLAIMED.value


def test_pre_send_delivery_failure_releases_only_the_claimed_running_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known pre-send bot response makes one durable retry safe."""

    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    run_id = "00000000-0000-0000-0000-000000000001"
    execution_token = "00000000-0000-0000-0000-000000000002"
    run_row = {
        "id": run_id,
        "schedule_id": "schedule-1",
        "occurrence_at": now,
        "trigger": "schedule",
        "status": "running",
        "job_id": "job-1",
        "started_at": now,
        "finished_at": None,
        "output": None,
        "error": None,
        "delivery_status": "pending",
        "delivery_message_id": None,
        "delivery_claimed_at": None,
        "execution_token": execution_token,
        "created_at": now,
        "updated_at": now,
    }
    cursor = _FakeScheduleCursor(schedule_row={}, run_row=run_row)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _FakeScheduleConnection(cursor),
    )

    released = release_agent_schedule_run_delivery_claim(
        SharedSettings(),
        run_id=run_id,
        execution_token=execution_token,
    )

    assert released is not None
    assert released.delivery_status is AgentScheduleRunDeliveryStatus.PENDING
    query, params = cursor.calls[0]
    assert "status = 'running'" in query
    assert "execution_token = %s" in query
    assert "delivery_status = %s" in query
    assert params == (
        AgentScheduleRunDeliveryStatus.PENDING.value,
        run_id,
        execution_token,
        AgentScheduleRunDeliveryStatus.CLAIMED.value,
    )


def test_stale_delivery_claims_are_listed_and_manually_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can inspect stale claims without the dispatcher retrying them."""

    claimed_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    claimed_before = datetime(2026, 7, 28, 9, 5, tzinfo=timezone.utc)
    run_id = "00000000-0000-0000-0000-000000000001"
    execution_token = "00000000-0000-0000-0000-000000000002"
    run_row = {
        "id": run_id,
        "schedule_id": "schedule-1",
        "occurrence_at": claimed_at,
        "trigger": "schedule",
        "status": "running",
        "job_id": "job-1",
        "started_at": claimed_at,
        "finished_at": None,
        "output": None,
        "error": None,
        "delivery_status": "claimed",
        "delivery_message_id": None,
        "delivery_claimed_at": claimed_at,
        "execution_token": execution_token,
        "created_at": claimed_at,
        "updated_at": claimed_at,
    }
    cursor = _FakeScheduleCursor(schedule_row={}, run_row=run_row)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _FakeScheduleConnection(cursor),
    )

    claims = list_stale_agent_schedule_run_delivery_claims(
        SharedSettings(),
        guild_id="1000",
        claimed_before=claimed_before,
        limit=25,
    )

    assert [claim.id for claim in claims] == [run_id]
    query, params = cursor.calls[0]
    assert "runs.delivery_claimed_at <= %s" in query
    assert params == (
        "1000",
        AgentScheduleRunDeliveryStatus.CLAIMED.value,
        claimed_before,
        25,
    )

    cursor = _FakeScheduleCursor(schedule_row={}, run_row=run_row)
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: _FakeScheduleConnection(cursor),
    )
    resolved = mark_agent_schedule_run_delivery_unknown(
        SharedSettings(),
        run_id=run_id,
        execution_token=execution_token,
        claimed_before=claimed_before,
    )

    assert resolved is not None
    query, params = cursor.calls[0]
    assert "delivery_claimed_at <= %s" in query
    assert params == (
        AgentScheduleRunDeliveryStatus.UNKNOWN.value,
        run_id,
        execution_token,
        AgentScheduleRunDeliveryStatus.CLAIMED.value,
        claimed_before,
    )


@pytest.mark.parametrize(
    ("transition", "kwargs"),
    [
        (
            complete_agent_schedule_run,
            {"status": AgentScheduleRunStatus.FAILED, "error": "late result"},
        ),
        (fail_agent_schedule_run, {"error": "late dispatch failure"}),
        (claim_agent_schedule_run_delivery, {}),
        (release_agent_schedule_run_delivery_claim, {}),
        (mark_agent_schedule_run_delivery_posted, {"message_id": "message-1"}),
        (mark_agent_schedule_run_delivery_unknown, {}),
    ],
)
def test_stale_execution_token_cannot_change_a_reclaimed_run(
    monkeypatch: pytest.MonkeyPatch,
    transition: Any,
    kwargs: dict[str, Any],
) -> None:
    """Every active-run transition is fenced to its claiming execution."""

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = (
        cursor
    )
    monkeypatch.setattr(
        schedules,
        "get_postgres_connection",
        lambda _settings: connection,
    )
    token = "00000000-0000-0000-0000-000000000099"

    changed = transition(
        SharedSettings(),
        run_id="00000000-0000-0000-0000-000000000001",
        execution_token=token,
        **kwargs,
    )

    assert changed is None
    query, params = cursor.execute.call_args.args
    assert "execution_token = %s" in query
    assert token in params
