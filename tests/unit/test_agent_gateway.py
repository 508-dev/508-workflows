"""Unit tests for the shared agent gateway primitives."""

from __future__ import annotations

from datetime import date, timezone
from types import SimpleNamespace

import pytest

from five08.agent import (
    AgentIdentityContext,
    AgentModelConfig,
    AgentOrchestrator,
    AgentToolAction,
    InMemoryTaskStore,
    PolicyEngine,
    ToolManifest,
    ToolRuntimeConfig,
)
from five08.agent.tools import ToolRegistry


def _context(
    *,
    roles: list[str] | None = None,
    internal_user_id: str | None = None,
) -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id="123",
        internal_user_id=internal_user_id,
        organization_id="org-1",
        guild_id="org-1",
        roles=roles if roles is not None else ["Member"],
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
    assert response.plan.expires_at is not None
    assert response.plan.expires_at.tzinfo == timezone.utc
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
    assert response.plan is not None
    assert response.plan.requires_confirmation is False
    assert response.plan.actions[0].requires_confirmation is False


def test_policy_requires_canonical_organization_for_tenant_tools() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))
    context = AgentIdentityContext(
        discord_user_id="123",
        guild_id="org-1",
        roles=["Member"],
    )

    response = orchestrator.plan("Show tasks for project Atlas", context)

    assert response.status == "denied"
    assert "tenant context" in response.message


def test_policy_denies_task_tools_without_member_role() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))
    context = AgentIdentityContext(
        discord_user_id="123",
        organization_id="org-1",
        guild_id="org-1",
        roles=["@everyone"],
    )

    response = orchestrator.plan("Show tasks for project Atlas", context)

    assert response.status == "denied"
    assert "Missing required scopes" in response.message


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

    response = orchestrator.plan(
        "Search tasks for project Atlas matching onboarding", _context()
    )

    assert response.status == "executed"
    assert response.results[0].status == "succeeded"
    assert response.results[0].result["tasks"][0]["task_id"] == "TASK-001"


def test_create_task_title_with_search_phrase_routes_to_create() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to show task counts on the dashboard",
        _context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "task_write.create_task"
    assert action.arguments["title"] == "show task counts on the dashboard"


def test_bare_task_list_requires_project_filter() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date="2026-05-15",
        organization_id="org-1",
        created_by="123",
    )
    task_store.create_task(
        title="Review launch notes",
        project="Atlas",
        assignee=None,
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan("Show tasks", _context())

    assert response.status == "needs_clarification"
    assert response.clarification_question == "Which project should I search?"


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


def test_project_search_keeps_trailing_matching_query() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date="2026-05-15",
        organization_id="org-1",
        created_by="123",
    )
    task_store.create_task(
        title="Update onboarding docs",
        project="Beta",
        assignee="Sarah",
        due_date="2026-05-15",
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan(
        "Show tasks for project Atlas matching onboarding",
        _context(),
    )

    assert response.status == "executed"
    assert response.results[0].result["tasks"][0]["project"] == "Atlas"
    assert len(response.results[0].result["tasks"]) == 1


def test_project_search_about_project_plan_keeps_project_as_query_text() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update project plan",
        project="Atlas",
        assignee="Sarah",
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    task_store.create_task(
        title="Review onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan(
        "Show tasks for project Atlas about project plan", _context()
    )

    assert response.status == "executed"
    assert len(response.results[0].result["tasks"]) == 1
    assert response.results[0].result["tasks"][0]["title"] == "Update project plan"


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


def test_create_task_in_project_clause_stops_before_title() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task in project Atlas to update docs",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["project"] == "Atlas"
    assert response.plan.actions[0].arguments["title"] == "update docs"


def test_create_task_assignment_clause_stops_before_title() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to update docs and assign it to Sarah by Friday",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == "update docs"
    assert response.plan.actions[0].arguments["assignee"] == "Sarah"


def test_create_task_title_due_word_without_date_stays_in_title() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to prepare due diligence report",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == (
        "prepare due diligence report"
    )
    assert "due_date" not in response.plan.actions[0].arguments


def test_create_task_title_weekday_does_not_become_due_date_without_cue() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to draft the Monday newsletter",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == "draft the Monday newsletter"
    assert "due_date" not in response.plan.actions[0].arguments


def test_create_task_title_iso_date_does_not_become_due_date_without_cue() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to draft 2026-05-09 launch notes",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == (
        "draft 2026-05-09 launch notes"
    )
    assert "due_date" not in response.plan.actions[0].arguments


def test_create_task_title_project_word_does_not_become_project() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to update project plan",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == "update project plan"
    assert "project" not in response.plan.actions[0].arguments


def test_update_assignment_parses_task_id_before_to() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan("Assign TASK-001 to Sarah", _context())

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["assignee"] == "Sarah"


def test_update_status_to_done_parses_done_status() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan("Update TASK-001 status to done", _context())

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["status"] == "done"


def test_close_task_id_parses_done_status() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan("Close TASK-001", _context())

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["status"] == "done"


def test_complete_task_id_routes_to_done_status_update() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan("Complete TASK-001", _context())

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["status"] == "done"


def test_mark_task_id_as_done_routes_to_status_update() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan("Mark TASK-001 as done", _context())

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["status"] == "done"


def test_rename_task_id_routes_to_title_update() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Rename TASK-001 to refresh onboarding docs",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["title"] == "refresh onboarding docs"


def test_update_title_parses_title_update() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Update TASK-001 title to refresh onboarding docs",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].tool_name == "task_write.update_task"
    assert response.plan.actions[0].arguments["task_id"] == "TASK-001"
    assert response.plan.actions[0].arguments["title"] == "refresh onboarding docs"


def test_update_title_with_status_word_does_not_change_status() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Update TASK-001 title to completed checklist",
        _context(),
    )

    assert response.plan is not None
    assert response.plan.actions[0].arguments["title"] == "completed checklist"
    assert "status" not in response.plan.actions[0].arguments


def test_invalid_month_date_does_not_crash_planning() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to follow up by February 31 2026",
        _context(),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    assert "due_date" not in response.plan.actions[0].arguments


def test_invalid_iso_date_does_not_freeze_malformed_due_date() -> None:
    orchestrator = AgentOrchestrator(today=date(2026, 5, 8))

    response = orchestrator.plan(
        "Create a task to follow up due 2026-02-31",
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


def test_admin_can_update_task_created_by_someone_else() -> None:
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
    response = orchestrator.plan(
        "Update TASK-001 due tomorrow",
        _context(roles=["Admin"]),
    )

    assert response.plan is not None
    results = orchestrator.execute_plan(
        response.plan,
        _context(roles=["Admin"]),
        confirmed=True,
    )

    assert results[0].status == "succeeded"
    assert results[0].result["due_date"] is not None


def test_linked_user_can_update_task_created_before_linking() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee="Sarah",
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))
    context = _context(internal_user_id="internal-123")

    response = orchestrator.plan("Update TASK-001 due tomorrow", context)

    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, context, confirmed=True)

    assert results[0].status == "succeeded"
    assert results[0].result["due_date"] is not None


def test_engineer_can_draft_github_issue_write_with_confirmation() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create GitHub issue in repo 508-dev/508-workflows titled Fix onboarding sync",
        _context(roles=["Engineer"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "github_issue.create_issue"
    assert action.arguments == {
        "title": "Fix onboarding sync",
        "repository": "508-dev/508-workflows",
    }


def test_github_issue_create_title_can_include_search_word() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create a GitHub issue to improve search UI",
        _context(roles=["Engineer"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "github_issue.create_issue"
    assert action.arguments["title"] == "improve search UI"


def test_github_issue_create_title_can_include_task_word() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create a GitHub issue to show task counts on the dashboard",
        _context(roles=["Engineer"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "github_issue.create_issue"
    assert action.arguments["title"] == "show task counts on the dashboard"


def test_github_issue_create_strips_trailing_repository_clause() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create a GitHub issue to improve UI in repo 508-dev/508-workflows",
        _context(roles=["Engineer"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "github_issue.create_issue"
    assert action.arguments == {
        "title": "improve UI",
        "repository": "508-dev/508-workflows",
    }


def test_github_issue_search_strips_trailing_repository_clause() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Search GitHub issues matching onboarding in repo 508-dev/508-workflows",
        _context(roles=["Engineer"]),
    )

    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "github_issue.search_issues"
    assert action.arguments == {
        "query": "onboarding",
        "repository": "508-dev/508-workflows",
        "state": "open",
    }


def test_member_cannot_create_github_issue() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create GitHub issue in repo 508-dev/508-workflows titled Fix onboarding sync",
        _context(roles=["Member"]),
    )

    assert response.status == "denied"
    assert "github:issue:create" in response.message


def test_kimai_month_query_sets_begin_and_end_bounds() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Kimai hours for project Atlas in 2026-05",
        _context(roles=["Admin"]),
    )

    assert response.status == "failed"
    action = response.plan.actions[0]
    assert action.tool_name == "kimai_read.project_hours"
    assert action.arguments["project"] == "Atlas"
    assert action.arguments["begin"] == "2026-05-01"
    assert action.arguments["end"] == "2026-05-31T23:59:59"
    assert response.results[0].status == "failed"
    assert "KIMAI_BASE_URL" in response.results[0].error


def test_kimai_december_month_query_rolls_end_to_year_boundary() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Kimai hours for project Atlas in 2026-12",
        _context(roles=["Admin"]),
    )

    action = response.plan.actions[0]
    assert action.arguments["begin"] == "2026-12-01"
    assert action.arguments["end"] == "2026-12-31T23:59:59"


def test_kimai_invalid_month_returns_clarification() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Kimai hours for project Atlas in 2026-13",
        _context(roles=["Admin"]),
    )

    assert response.status == "needs_clarification"
    assert response.plan is None
    assert response.results == []


def test_admin_can_draft_docuseal_member_agreement_with_confirmation() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Send member agreement to Sarah Example sarah@example.com",
        _context(roles=["Admin"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "docuseal_write.create_member_agreement_submission"
    assert action.arguments == {
        "submitter_email": "sarah@example.com",
        "submitter_name": "Sarah Example",
        "send_email": True,
    }


def test_admin_can_search_crm_contacts() -> None:
    captured: dict[str, object] = {}

    class FakeContact:
        def to_dict(self) -> dict[str, object]:
            return {"id": "1", "name": "Sarah Example"}

    class FakeRepository:
        def search(self, **kwargs: object) -> list[FakeContact]:
            captured.update(kwargs)
            return [FakeContact()]

    class FakeRegistry(ToolRegistry):
        def _crm_repository(self) -> FakeRepository:
            return FakeRepository()

    orchestrator = AgentOrchestrator(
        registry=FakeRegistry(
            runtime_config=ToolRuntimeConfig(
                espo_base_url="https://crm.example.test",
                espo_api_key="key",
            ),
        )
    )

    response = orchestrator.plan(
        "Find contact Sarah Example", _context(roles=["Admin"])
    )

    assert response.status == "executed"
    assert captured["name__contains"] == "Sarah Example"
    assert "name" not in captured
    assert response.results[0].result["contacts"][0]["name"] == "Sarah Example"


def test_admin_can_plan_crm_contact_onboarding_update() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Update CRM contact contact-123 onboarding state to approved",
        _context(roles=["Admin"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "crm_write.update_contact"
    assert action.arguments == {
        "contact_id": "contact-123",
        "updates": {"cOnboardingState": "approved"},
    }


def test_execute_plan_formats_key_errors_without_repr_quotes() -> None:
    orchestrator = AgentOrchestrator()
    context = _context(roles=["Project Manager"])

    response = orchestrator.plan("Close TASK-999", context)

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, context, confirmed=True)
    assert results[0].status == "failed"
    assert results[0].error == "Task TASK-999 was not found"


def test_mailbox_create_uses_explicit_backup_email_not_mailbox_address() -> None:
    orchestrator = AgentOrchestrator()

    response = orchestrator.plan(
        "Create mailbox john@508.dev named John with backup john@gmail.com",
        _context(roles=["Admin"]),
    )

    assert response.status == "requires_confirmation"
    assert response.plan is not None
    action = response.plan.actions[0]
    assert action.tool_name == "mail_write.create_mailbox"
    assert action.arguments == {
        "local_part": "john",
        "backup_email": "john@gmail.com",
        "name": "John",
    }


def test_github_issue_create_uses_runtime_configured_default_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_create_issue(
        self: object,
        *,
        repository: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, object]:
        calls.update(
            {"repository": repository, "title": title, "body": body, "labels": labels}
        )
        return {"number": 42, "html_url": "https://github.example/issue/42"}

    monkeypatch.setattr(
        "five08.clients.github.GitHubClient.create_issue",
        fake_create_issue,
    )
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(
            github_api_token="token",
            github_default_repo="508-dev/508-workflows",
        )
    )

    result = registry.execute(
        "github_issue.create_issue",
        {"title": "Fix onboarding sync"},
        organization_id="org-1",
        actor_id="123",
        actor_scopes={"github:issue:create"},
    )

    assert result["number"] == 42
    assert calls["repository"] == "508-dev/508-workflows"


def test_github_issue_repository_must_be_owner_name() -> None:
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(github_api_token="token"),
    )

    with pytest.raises(ValueError, match="owner/name"):
        registry.execute(
            "github_issue.search_issues",
            {"query": "onboarding", "repository": "508-dev/508-workflows/extra"},
            organization_id="org-1",
            actor_id="123",
            actor_scopes={"github:issue:read"},
        )


def test_github_issue_repository_must_be_default_or_allowed() -> None:
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(
            github_api_token="token",
            github_default_repo="508-dev/508-workflows",
        ),
    )

    with pytest.raises(ValueError, match="not allowed"):
        registry.execute(
            "github_issue.search_issues",
            {"query": "onboarding", "repository": "other-owner/other-repo"},
            organization_id="org-1",
            actor_id="123",
            actor_scopes={"github:issue:read"},
        )


def test_github_issue_repository_allows_configured_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_search_issues(
        self: object,
        *,
        repository: str,
        query: str,
        state: str = "open",
        limit: int = 10,
    ) -> dict[str, object]:
        calls.update(
            {"repository": repository, "query": query, "state": state, "limit": limit}
        )
        return {"issues": []}

    monkeypatch.setattr(
        "five08.clients.github.GitHubClient.search_issues",
        fake_search_issues,
    )
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(
            github_api_token="token",
            github_default_repo="508-dev/508-workflows",
            github_allowed_repos="other-owner/other-repo",
        ),
    )

    result = registry.execute(
        "github_issue.search_issues",
        {"query": "onboarding", "repository": "other-owner/other-repo"},
        organization_id="org-1",
        actor_id="123",
        actor_scopes={"github:issue:read"},
    )

    assert result == {"issues": []}
    assert calls["repository"] == "other-owner/other-repo"


def test_member_cannot_assign_task_without_assign_scope() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee=None,
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))

    response = orchestrator.plan("Assign TASK-001 to Sarah", _context())

    assert response.status == "denied"
    assert "task:assign" in response.message
    assert response.plan is not None
    assert response.plan.actions[0].required_scopes == [
        "task:update_own",
        "task:assign",
    ]


def test_project_manager_can_assign_task() -> None:
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Update onboarding docs",
        project="Atlas",
        assignee=None,
        due_date=None,
        organization_id="org-1",
        created_by="123",
    )
    orchestrator = AgentOrchestrator(registry=ToolRegistry(task_store))
    context = _context(roles=["Project Manager"])

    response = orchestrator.plan("Assign TASK-001 to Sarah", context)

    assert response.plan is not None
    results = orchestrator.execute_plan(response.plan, context, confirmed=True)

    assert results[0].status == "succeeded"
    assert results[0].result["assignee"] == "Sarah"


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

    with pytest.raises(ValueError, match="valid ISO date"):
        registry.execute(
            "task_write.create_task",
            {"title": "Follow up", "due_date": "2026-02-31"},
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


def test_policy_ignores_client_supplied_scopes() -> None:
    policy = PolicyEngine()
    manifest = ToolManifest(
        name="deploy.request",
        risk="high",
        required_scopes=("deploy:request",),
        tenant_scoped=False,
    )
    action = AgentToolAction(
        tool_name="deploy.request",
        required_scopes=["deploy:request"],
        summary="Request deploy",
    )
    context = AgentIdentityContext(
        discord_user_id="123",
        roles=["Member"],
        scopes=["deploy:request"],
    )

    decision = policy.authorize(context=context, manifest=manifest, action=action)

    assert not decision.allowed
    assert "deploy:request" in decision.reason


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


def test_agent_model_config_requires_tier_key_for_external_provider() -> None:
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-5-mini",
        agent_fast_model=None,
        agent_fast_base_url=None,
        agent_fast_api_key=None,
        agent_strong_model="accounts/fireworks/models/kimi-k2-instruct",
        agent_strong_base_url="https://api.fireworks.ai/inference/v1",
        agent_strong_api_key=None,
        agent_reasoning_model=None,
        agent_reasoning_base_url=None,
        agent_reasoning_api_key=None,
    )

    selection = AgentModelConfig.from_settings(settings).resolve("strong")

    assert selection.base_url == "https://api.fireworks.ai/inference/v1"
    assert selection.api_key_configured is False


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


def test_agent_model_config_rejects_disallowed_base_url() -> None:
    settings = SimpleNamespace(
        openai_api_key="openai-key",
        openai_base_url="https://metadata.google.internal",
        openai_model="gpt-5-mini",
    )

    with pytest.raises(ValueError, match="Disallowed agent model base_url"):
        AgentModelConfig.from_settings(settings)
