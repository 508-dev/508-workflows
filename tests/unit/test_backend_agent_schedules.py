"""Unit tests for backend recurring-agent schedule authorization boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request

from five08.agent import (
    AgentExecutionResult,
    AgentIdentityContext,
    AgentModelConfig,
    AgentOrchestrator,
    AgentPlan,
    AgentPlannerResult,
    AgentToolAction,
    PlannerDraft,
    PlannerDraftAction,
    PolicyEngine,
    ToolRegistry,
)
from five08.agent.schedules import (
    AgentScheduleAction,
    AgentScheduleDefinition,
    AgentScheduleDiscordDelivery,
    AgentScheduleExecutionMode,
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


def _agent_loop_schedule(*, tool_allowlist: list[str]) -> AgentScheduleRecord:
    schedule = _schedule()
    return replace(
        schedule,
        definition=AgentScheduleDefinition(
            prompt="Inspect the CRM pipeline and summarize what needs attention.",
            execution_mode=AgentScheduleExecutionMode.AGENT_LOOP,
            tool_allowlist=tool_allowlist,
            delivery=AgentScheduleDiscordDelivery(guild_id="1000", channel_id="2000"),
        ),
        allowed_scopes=frozenset({"agent:schedule:manage", "crm:contact:read"}),
    )


def test_agent_loop_creation_persists_an_exact_default_tool_catalog() -> None:
    """The generic creation surface captures tools now, rather than later."""

    definition = api._agent_schedule_definition_from_fields(
        SimpleNamespace(
            prompt="Review ERP, CRM, and onboarding health.",
            execution_mode="agent_loop",
            tool_allowlist=[],
            channel_id="2000",
        ),
        guild_id="1000",
    )

    assert definition.execution_mode is AgentScheduleExecutionMode.AGENT_LOOP
    assert definition.actions == []
    assert "onboarding_read.get_summary" in definition.tool_allowlist
    assert "crm_write.update_contact" not in definition.tool_allowlist


class _LoopPlanner:
    def __init__(self, first_draft: PlannerDraft, final_draft: PlannerDraft) -> None:
        self.first_draft = first_draft
        self.final_draft = final_draft
        self.observations: list[dict[str, str]] = []
        self.model_config = AgentModelConfig()

    def plan(self, **_kwargs: object) -> AgentPlannerResult:
        return AgentPlannerResult(
            draft=self.first_draft,
            model=self.model_config.resolve("fast"),
            latency_ms=1,
        )

    def plan_with_observations(
        self,
        *,
        tool_observations: list[dict[str, str]],
        **_kwargs: object,
    ) -> AgentPlannerResult:
        self.observations = tool_observations
        return AgentPlannerResult(
            draft=self.final_draft,
            model=self.model_config.resolve("fast"),
            latency_ms=1,
        )


class _LoopOrchestrator:
    def __init__(self, planner: _LoopPlanner) -> None:
        self.registry = ToolRegistry()
        self.policy = PolicyEngine()
        self.model_config = AgentModelConfig()
        self.planner = planner
        self.plans = []

    def execute_plan(
        self, plan: object, *_args: object, **_kwargs: object
    ) -> list[AgentExecutionResult]:
        self.plans.append(plan)
        return [
            AgentExecutionResult(
                tool_name="crm_read.search_contacts",
                status="succeeded",
                result={
                    "contacts": [
                        {
                            "id": "contact-123",
                            "name": "Private Person",
                            "emailAddress": "private@example.com",
                            "phoneNumber": "+1-555-0100",
                        },
                        {
                            "id": "contact-456",
                            "name": "Another Private Person",
                            "emailAddress": "another@example.com",
                        },
                    ]
                },
            )
        ]


def test_agent_loop_uses_safe_aggregate_observations_for_private_tools() -> None:
    """Raw CRM records never return to the planner on the second loop step."""

    planner = _LoopPlanner(
        PlannerDraft(
            status="planned",
            actions=[
                PlannerDraftAction(
                    tool_name="crm_read.search_contacts",
                    arguments={"query": "onboarding", "limit": 5},
                    summary="Inspect CRM onboarding contacts",
                )
            ],
        ),
        PlannerDraft(
            status="answer",
            answer="The CRM search found two matching contacts.",
        ),
    )
    orchestrator = _LoopOrchestrator(planner)
    schedule = _agent_loop_schedule(tool_allowlist=["crm_read.search_contacts"])
    context = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        roles=["Admin"],
    )

    outcome = api._run_agent_schedule_loop(
        orchestrator=cast(AgentOrchestrator, orchestrator),
        schedule=schedule,
        run=_run(),
        context=context,
        effective_scopes=orchestrator.policy.scopes_for_context(context),
        deadline_monotonic=1_000_000_000_000.0,
    )

    assert outcome.error is None
    assert outcome.answer == "The CRM search found two matching contacts."
    assert len(orchestrator.plans) == 1
    observation = json.loads(planner.observations[0]["data_json"])
    assert observation == {"matching_contact_count": 2}
    assert "private@example.com" not in planner.observations[0]["data_json"]
    assert "Private Person" not in planner.observations[0]["data_json"]


def test_agent_loop_rejects_a_write_before_any_tool_executes() -> None:
    """The model cannot turn a scheduled report into a CRM mutation."""

    planner = _LoopPlanner(
        PlannerDraft(
            status="planned",
            actions=[
                PlannerDraftAction(
                    tool_name="crm_write.update_contact",
                    arguments={
                        "contact_id": "contact-123",
                        "updates": {"cOnboardingState": "approved"},
                    },
                    summary="Update onboarding state",
                )
            ],
        ),
        PlannerDraft(status="answer", answer="unused"),
    )
    orchestrator = _LoopOrchestrator(planner)
    schedule = _agent_loop_schedule(tool_allowlist=["crm_read.search_contacts"])
    context = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        roles=["Admin"],
    )

    outcome = api._run_agent_schedule_loop(
        orchestrator=cast(AgentOrchestrator, orchestrator),
        schedule=schedule,
        run=_run(),
        context=context,
        effective_scopes=orchestrator.policy.scopes_for_context(context),
        deadline_monotonic=1_000_000_000_000.0,
    )

    assert outcome.error == "scheduled_planner_proposed_unallowed_tool"
    assert outcome.results == []
    assert orchestrator.plans == []


@pytest.mark.asyncio
async def test_confirmed_agent_schedule_creation_binds_current_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent cannot select a channel or loosen the generic schedule mode."""

    captured: dict[str, object] = {}

    async def create_schedule(
        _request: Request,
        *,
        payload: object,
        context: AgentIdentityContext,
    ) -> tuple[dict[str, object], int]:
        captured["payload"] = payload
        captured["context"] = context
        return {
            "status": "created",
            "schedule": {
                "id": "schedule-2",
                "next_run_at": "2026-08-03T00:00:00+00:00",
            },
        }, 201

    monkeypatch.setattr(api, "_create_agent_schedule_for_context", create_schedule)
    plan = AgentPlan(
        plan_id="schedule-plan-1",
        intent="create_agent_schedule",
        planner="live_model",
        model_tier="fast",
        model=AgentModelConfig().resolve("fast"),
        actions=[
            AgentToolAction(
                tool_name="agent_schedule.create",
                arguments={
                    "name": "Weekly onboarding health",
                    "cron_expression": "0 9 * * 1",
                    "timezone": "Asia/Tokyo",
                    "prompt": "Inspect onboarding health and report blockers.",
                },
                summary="Create recurring report",
                requires_confirmation=True,
            )
        ],
        human_summary="Create recurring report",
        requires_confirmation=True,
    )
    context = AgentIdentityContext(
        discord_user_id="1001",
        organization_id="1000",
        guild_id="1000",
        channel_id="2000",
        roles=["Admin"],
    )

    response = await api._execute_confirmed_agent_schedule_creation_plan(
        cast(Request, SimpleNamespace()),
        plan=plan,
        context=context,
    )

    assert response.status == "executed"
    assert response.results[0].result == {
        "schedule_id": "schedule-2",
        "next_run_at": "2026-08-03T00:00:00+00:00",
        "channel_id": "2000",
    }
    fields = captured["payload"]
    assert getattr(fields, "channel_id") == "2000"
    assert getattr(fields, "execution_mode") == "agent_loop"
    assert getattr(fields, "tool_allowlist") == []
    assert captured["context"] == context


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
