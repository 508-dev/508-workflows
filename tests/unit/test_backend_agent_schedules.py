"""Unit tests for backend recurring-agent schedule authorization boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request

from five08.agent import AgentIdentityContext
from five08.agent.schedules import (
    AgentScheduleAction,
    AgentScheduleDefinition,
    AgentScheduleDiscordDelivery,
    AgentScheduleRecord,
    AgentScheduleRunRecord,
    AgentScheduleRunStatus,
    AgentScheduleRunTrigger,
    AgentScheduleStatus,
)
from five08.backend import api


def _definition() -> AgentScheduleDefinition:
    return AgentScheduleDefinition(
        prompt="Group related public GitHub issues.",
        actions=[
            AgentScheduleAction(
                tool_name="github_issue.search_issues",
                arguments={
                    "repository": "508-dev/508-workflows",
                    "query": "label:bug",
                    "state": "open",
                    "limit": 10,
                },
                summary="Search public GitHub issues",
            )
        ],
        delivery=AgentScheduleDiscordDelivery(guild_id="1000", channel_id="2000"),
    )


def _schedule() -> AgentScheduleRecord:
    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    return AgentScheduleRecord(
        id="schedule-1",
        organization_id="1000",
        guild_id="1000",
        owner_discord_user_id="1001",
        name="Weekly GitHub triage",
        cron_expression="0 9 * * 1",
        timezone="UTC",
        definition=_definition(),
        allowed_scopes=frozenset({"agent:schedule:manage", "github:issue:read"}),
        status=AgentScheduleStatus.ACTIVE,
        next_run_at=now,
        last_run_at=None,
        created_at=now,
        updated_at=now,
    )


def _run() -> AgentScheduleRunRecord:
    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    return AgentScheduleRunRecord(
        id="run-1",
        schedule_id="schedule-1",
        occurrence_at=now,
        trigger=AgentScheduleRunTrigger.SCHEDULE,
        status=AgentScheduleRunStatus.QUEUED,
        job_id="job-1",
        started_at=None,
        finished_at=None,
        output=None,
        error=None,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_schedule_creation_uses_refreshed_roles_not_stale_request_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale Admin role supplied by the caller cannot create a schedule."""

    stale_admin = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        roles=["Admin"],
    )
    refreshed_steering_member = stale_admin.model_copy(
        update={"roles": ["Steering Committee"], "role_ids": []}
    )
    payload = SimpleNamespace(
        name="Weekly GitHub triage",
        cron_expression="0 9 * * 1",
        timezone="UTC",
        prompt="Group related public GitHub issues.",
        repository="508-dev/508-workflows",
        query="label:bug",
        state="open",
        limit=10,
        channel_id="2000",
        summary_mode="deterministic",
        sources_are_public=False,
    )

    async def refreshed_context(*_args: object, **_kwargs: object):
        return refreshed_steering_member, None, 200

    create_schedule = Mock()
    monkeypatch.setattr(api.settings, "environment", "test")
    monkeypatch.setattr(api.settings, "agent_allow_role_name_fallback", True)
    monkeypatch.setattr(api, "_fresh_agent_schedule_context", refreshed_context)
    monkeypatch.setattr(api, "create_agent_schedule", create_schedule)

    response, status_code = await api._create_agent_schedule_for_context(
        cast(Request, SimpleNamespace()),
        payload=payload,
        context=stale_admin,
    )

    assert status_code == 403
    assert response["error"] == "schedule_not_authorized"
    assert response["detail"] == "Missing required scopes: agent:schedule:manage"
    create_schedule.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_execution_skips_revoked_owner_before_tools_or_discord(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted read scopes never outlive the schedule owner's live Discord roles."""

    queued_run = _run()
    running_run = replace(
        queued_run,
        status=AgentScheduleRunStatus.RUNNING,
        started_at=queued_run.occurrence_at,
    )
    completed_run = replace(
        running_run,
        status=AgentScheduleRunStatus.SKIPPED,
        finished_at=running_run.occurrence_at,
        error="owner_scopes_no_longer_granted",
    )
    current_member = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        roles=["Member"],
    )

    async def refreshed_context(*_args: object, **_kwargs: object):
        return current_member, None, 200

    get_run = Mock(return_value=queued_run)
    claim_run = Mock(return_value=running_run)
    get_schedule = Mock(return_value=_schedule())
    complete_run = Mock(return_value=completed_run)
    make_plan = Mock(side_effect=AssertionError("tools must not be planned"))
    post_report = AsyncMock(side_effect=AssertionError("Discord must not be called"))
    orchestrator = SimpleNamespace(
        policy=SimpleNamespace(scopes_for_context=Mock(return_value=set()))
    )
    monkeypatch.setattr(api, "get_agent_schedule_run", get_run)
    monkeypatch.setattr(api, "claim_agent_schedule_run", claim_run)
    monkeypatch.setattr(api, "get_agent_schedule", get_schedule)
    monkeypatch.setattr(api, "complete_agent_schedule_run", complete_run)
    monkeypatch.setattr(api, "_fresh_agent_schedule_context", refreshed_context)
    monkeypatch.setattr(api, "_get_agent_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(api, "_agent_schedule_plan", make_plan)
    monkeypatch.setattr(api, "_post_agent_schedule_report_to_bot", post_report)

    response, status_code = await api._execute_agent_schedule_run(
        cast(Request, SimpleNamespace()),
        run_id=queued_run.id,
    )

    assert status_code == 200
    assert response["status"] == "skipped"
    assert response["error"] == "owner_scopes_no_longer_granted"
    assert response["delivery_status"] == "not_posted"
    complete_run.assert_called_once()
    assert complete_run.call_args.kwargs["status"] is AgentScheduleRunStatus.SKIPPED
    assert complete_run.call_args.kwargs["error"] == "owner_scopes_no_longer_granted"
    make_plan.assert_not_called()
    post_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_execution_reclaims_only_a_stale_running_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry can recover a crashed runner after the bounded execution lease."""

    stale_running_run = replace(
        _run(),
        status=AgentScheduleRunStatus.RUNNING,
        started_at=datetime.now(tz=timezone.utc) - timedelta(seconds=301),
    )
    reclaimed_run = replace(
        stale_running_run,
        started_at=datetime.now(tz=timezone.utc),
    )
    completed_run = replace(
        reclaimed_run,
        status=AgentScheduleRunStatus.SKIPPED,
        finished_at=reclaimed_run.started_at,
        error="owner_scopes_no_longer_granted",
    )
    current_member = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        roles=["Member"],
    )

    async def refreshed_context(*_args: object, **_kwargs: object):
        return current_member, None, 200

    claim_run = Mock(return_value=reclaimed_run)
    complete_run = Mock(return_value=completed_run)
    monkeypatch.setattr(
        api,
        "get_agent_schedule_run",
        Mock(return_value=stale_running_run),
    )
    monkeypatch.setattr(api, "claim_agent_schedule_run", claim_run)
    monkeypatch.setattr(api, "get_agent_schedule", Mock(return_value=_schedule()))
    monkeypatch.setattr(api, "complete_agent_schedule_run", complete_run)
    monkeypatch.setattr(api, "_fresh_agent_schedule_context", refreshed_context)
    monkeypatch.setattr(
        api,
        "_get_agent_orchestrator",
        lambda: SimpleNamespace(
            policy=SimpleNamespace(scopes_for_context=lambda _context: set())
        ),
    )

    response, status_code = await api._execute_agent_schedule_run(
        cast(Request, SimpleNamespace()),
        run_id=stale_running_run.id,
    )

    assert status_code == 200
    assert response["status"] == "skipped"
    claim_run.assert_called_once()
    assert (
        claim_run.call_args.kwargs["reclaim_running_before"]
        >= stale_running_run.started_at
    )
