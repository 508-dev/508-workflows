"""Unit tests for the shared agent gateway primitives."""

from __future__ import annotations

from datetime import date

from five08.agent import AgentIdentityContext, AgentOrchestrator, InMemoryTaskStore
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
    results = orchestrator.execute_plan(response.plan, context)

    assert results[0].status == "succeeded"
    assert results[0].result["task_id"] == "TASK-001"
    assert results[0].result["title"] == "update onboarding docs"


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
