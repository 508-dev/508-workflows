"""Unit tests for the shared agent gateway primitives."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from five08.agent import (
    AgentIdentityContext,
    AgentModelConfig,
    AgentOrchestrator,
    InMemoryTaskStore,
)
from five08.agent.tools import ToolRegistry


def _context(*, roles: list[str] | None = None) -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id="123",
        organization_id="org-1",
        guild_id="org-1",
        roles=roles or ["Member"],
    )


def test_create_task_requires_confirmation_and_uses_stronger_model() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task for Sarah to update onboarding docs by Friday "
        "and link it to project Atlas.",
        _context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    assert response.plan.model_tier == "strong"
    assert response.plan.model.model == "gpt-5-mini"
    assert response.plan.model.source_tier == "built_in_default"
    action = response.plan.actions[0]
    assert action.tool_name == "task_write.create_task"
    assert action.arguments == {
        "title": "update onboarding docs",
        "assignee": "Sarah",
        "project": "Atlas",
        "due_date": "2026-05-15",
    }


def test_confirmed_plan_executes_inline_against_registry() -> None:
    task_store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(
        registry=ToolRegistry(task_store),
        today=date(2026, 5, 8),
    )
    context = _context()
    response = orchestrator.plan(
        "Create a task for Sarah to update onboarding docs by Friday "
        "and link it to project Atlas.",
        context,
    )

    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, context, confirmed=True)

    assert results[0].status == "succeeded"
    assert results[0].result["task_id"] == "TASK-001"
    assert results[0].result["title"] == "update onboarding docs"


def test_execute_plan_denies_unconfirmed_write_plan() -> None:
    task_store = InMemoryTaskStore()
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))
    context = _context()
    response = orchestrator.plan(
        "Create a task for Sarah to update onboarding docs by Friday",
        context,
    )

    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, context)

    assert results[0].status == "denied"
    assert "requires confirmation" in (results[0].error or "")
    assert task_store.search_tasks(query="", organization_id="org-1")["tasks"] == []


def test_policy_denies_tenant_scoped_tools_without_tenant_context() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))
    context = AgentIdentityContext(discord_user_id="123", roles=["Member"])

    response = orchestrator.plan(
        "Create a task for Sarah to update onboarding docs by Friday.",
        context,
    )

    assert response.status == "denied"
    assert "tenant context" in response.message


def test_search_task_executes_without_confirmation() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date="2026-05-15",
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan("Search tasks for onboarding", _context())

    assert response.status == "executed"
    assert response.results[0].status == "succeeded"
    assert response.results[0].result["tasks"][0]["task_id"] == "TASK-001"


def test_project_only_search_uses_project_as_filter_not_query() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date="2026-05-15",
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan("Show tasks for project Atlas", _context())

    assert response.status == "executed"
    assert response.results[0].result["tasks"][0]["project"] == "Atlas"


def test_create_task_parses_capitalized_for_assignee() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task For Sarah to update onboarding docs by Friday.",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["assignee"] == "Sarah"


def test_create_task_project_clause_stops_before_title() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task for project Atlas to update docs",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["project"] == "Atlas"
    assert response.plan.actions[0].arguments["title"] == "update docs"
    assert "assignee" not in response.plan.actions[0].arguments


def test_invalid_month_date_does_not_crash_planning() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to follow up by February 31 2026",
        _context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    assert "due_date" not in response.plan.actions[0].arguments


def test_update_task_enforces_creator_ownership() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date=None,
        organization_id="org-1",
        created_by="456",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))
    response = orchestrator.plan("Update TASK-001 due tomorrow", _context())

    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, _context(), confirmed=True)

    assert results[0].status == "denied"
    assert "creator" in (results[0].error or "")


def test_tool_registry_rejects_malformed_write_arguments() -> None:
    task_store = InMemoryTaskStore()
    registry = ToolRegistry(task_store)

    with pytest.raises(ValueError, match="title"):
        registry.execute(
            "task_write.create_task",
            {"title": " "},
            organization_id="org-1",
            actor_id="123",
        )

    with pytest.raises(ValueError, match="Task id"):
        registry.execute(
            "task_write.update_task",
            {"task_id": " "},
            organization_id="org-1",
            actor_id="123",
        )

    task_store.create_task(
        title="Existing task",
        project="Atlas",
        assignee=None,
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    with pytest.raises(ValueError, match="At least one"):
        registry.execute(
            "task_write.update_task",
            {"task_id": "TASK-001"},
            organization_id="org-1",
            actor_id="123",
        )


def test_agent_model_config_uses_tier_specific_provider() -> None:
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        openai_base_url=None,
        openai_model="gpt-5-mini",
        agent_fast_model=None,
        agent_fast_base_url=None,
        agent_fast_api_key=None,
        agent_strong_model="accounts/fireworks/models/kimi-k2-instruct",
        agent_strong_base_url="https://api.fireworks.ai/inference/v1",
        agent_strong_api_key="fireworks-key",
        agent_reasoning_model=None,
        agent_reasoning_base_url=None,
        agent_reasoning_api_key=None,
    )

    selection = AgentModelConfig.from_settings(settings).resolve("strong")

    assert selection.model == "accounts/fireworks/models/kimi-k2-instruct"
    assert selection.base_url == "https://api.fireworks.ai/inference/v1"
    assert selection.source_tier == "strong"
    assert selection.fallback_used is False
    assert selection.api_key_configured is True


def test_agent_model_config_falls_back_between_tiers() -> None:
    settings = SimpleNamespace(
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-5-mini",
        agent_fast_model="gpt-5-nano",
        agent_fast_base_url=None,
        agent_fast_api_key=None,
        agent_strong_model=None,
        agent_strong_base_url=None,
        agent_strong_api_key=None,
        agent_reasoning_model=None,
        agent_reasoning_base_url=None,
        agent_reasoning_api_key=None,
    )

    selection = AgentModelConfig.from_settings(settings).resolve("reasoning")

    assert selection.model == "gpt-5-nano"
    assert selection.base_url == "https://api.openai.com/v1"
    assert selection.source_tier == "fast"
    assert selection.fallback_used is True


def test_agent_model_config_falls_back_to_openai_model() -> None:
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-5-mini",
    )

    selection = AgentModelConfig.from_settings(settings).resolve("strong")

    assert selection.model == "gpt-5-mini"
    assert selection.base_url == "https://api.openai.com/v1"
    assert selection.source_tier == "openai_default"
    assert selection.fallback_used is True
