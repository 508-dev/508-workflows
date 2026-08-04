"""Read-only ERP/Billing agent tool boundaries."""

from __future__ import annotations

from typing import Any

import pytest

from five08.agent import (
    AgentIdentityContext,
    AgentModelConfig,
    AgentOrchestrator,
    AgentPlan,
    AgentPlannerResult,
    AgentToolAction,
    ToolRuntimeConfig,
)
from five08.agent import orchestrator as agent_orchestrator
from five08.agent import tools as agent_tools
from five08 import deadlines
from five08.agent.tools import ToolRegistry
from five08.clients.erpnext import ERPNextAPIError, ERPNextClient


class FakeERPNextClient(ERPNextClient):
    """In-memory ERP client that records read-only agent calls."""

    def __init__(
        self,
        *,
        fail_invoice_search: bool = False,
        project_error_status: int | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False
        self.fail_invoice_search = fail_invoice_search
        self.project_error_status = project_error_status

    def close(self) -> None:
        self.closed = True

    def search_invoices(
        self,
        doctype: str,
        query: str = "",
        docstatus: int | None = None,
        limit: int = 10,
        owners: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "search_invoices",
                {"doctype": doctype, "query": query, "limit": limit},
            )
        )
        if self.fail_invoice_search:
            raise ERPNextAPIError("ERPNext URL and internal detail must not escape")
        return [
            {
                "name": "SINV-0001",
                "posting_date": "2026-07-01",
                "docstatus": 1,
                "owner": "private-owner@example.com",
                "items": [{"item_code": "private-item"}],
            }
        ]

    def get_invoice(self, doctype: str, name: str) -> dict[str, Any] | None:
        self.calls.append(("get_invoice", {"doctype": doctype, "name": name}))
        return {
            "name": name,
            "docstatus": 1,
            "posting_date": "2026-07-01",
            "due_date": "2026-08-01",
            "customer": "Acme Customer",
            "supplier": "Hidden supplier for this sales invoice",
            "project": "PROJ-001",
            "currency": "USD",
            "grand_total": 1250.5,
            "rounded_total": 1251,
            "outstanding_amount": 500.25,
            "items": [{"item_code": "private-item"}],
            "payment_schedule": [{"bank_account": "secret-bank"}],
            "bank_account": "secret-bank",
            "address_display": "private address",
        }

    def search_suppliers(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        self.calls.append(("search_suppliers", {"query": query, "limit": limit}))
        return [
            {
                "name": "SUP-001",
                "supplier_name": "Acme Supplier",
                "email_id": "billing@acme.example",
                "disabled": 0,
                "is_frozen": 0,
                "bank_account": "secret-bank",
            }
        ]

    def list_records(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[Any] | None = None,
        or_filters: list[Any] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            (
                "list_records",
                {
                    "doctype": doctype,
                    "fields": fields,
                    "or_filters": or_filters or [],
                    "limit": limit,
                },
            )
        )
        return [
            {
                "name": "PROJ-001",
                "project_name": "Atlas",
                "status": "Open",
                "customer": "Acme Customer",
                "project_type": "External",
                "priority": "Medium",
                "percent_complete": 60,
                "expected_start_date": "2026-01-01",
                "expected_end_date": "2026-12-31",
                "actual_start_date": "2026-01-02",
                "actual_end_date": None,
                "modified": "2026-07-01 10:00:00",
                "users": [{"user": "private-user@example.com"}],
            }
        ]

    def get_project(self, project_id: str) -> dict[str, Any]:
        self.calls.append(("get_project", {"project_id": project_id}))
        if self.project_error_status is not None:
            raise ERPNextAPIError(
                "ERPNext project lookup failed",
                status_code=self.project_error_status,
            )
        return self.list_records(
            "Project",
            fields=[],
            limit=1,
        )[0]


def _context(
    roles: list[str],
    *,
    organization_id: str = "org-508",
) -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id="user-1",
        organization_id=organization_id,
        guild_id=organization_id,
        roles=roles,
    )


def _registry(
    *,
    configured_organization_id: str | None = "org-508",
    fail_invoice_search: bool = False,
    project_error_status: int | None = None,
) -> tuple[ToolRegistry, list[FakeERPNextClient]]:
    clients: list[FakeERPNextClient] = []

    def factory(_config: ToolRuntimeConfig) -> ERPNextClient:
        client = FakeERPNextClient(
            fail_invoice_search=fail_invoice_search,
            project_error_status=project_error_status,
        )
        clients.append(client)
        return client

    return (
        ToolRegistry(
            runtime_config=ToolRuntimeConfig(
                agent_erp_organization_id=configured_organization_id,
            ),
            erpnext_client_factory=factory,
        ),
        clients,
    )


class _OnboardingCursor:
    def __init__(self) -> None:
        self.query = ""
        self.calls: list[tuple[str, object | None]] = []

    def __enter__(self) -> "_OnboardingCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object | None = None) -> None:
        self.query = query
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return [
            {"state": "pending", "count": 3, "stale_count": 1},
            {"state": "onboarded", "count": 2, "stale_count": 0},
        ]


class _OnboardingConnection:
    def __init__(self, cursor: _OnboardingCursor) -> None:
        self.cursor_value = cursor

    def __enter__(self) -> "_OnboardingConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> _OnboardingCursor:
        return self.cursor_value


def test_onboarding_summary_is_aggregate_only_and_schedule_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The onboarding catalog exposes queue health, never a generic DB reader."""

    cursor = _OnboardingCursor()
    connection = _OnboardingConnection(cursor)
    monkeypatch.setattr(agent_tools, "connect", lambda *_args, **_kwargs: connection)
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(postgres_url="postgres://test")
    )

    result = registry.execute(
        "onboarding_read.get_summary",
        {},
        organization_id="org-508",
        actor_id="user-1",
    )

    assert result == {
        "total": 5,
        "by_state": {"pending": 3, "onboarded": 2},
        "stale_count": 1,
    }
    assert "email" not in cursor.query.casefold()
    assert "onboarding_read.get_summary" in registry.schedule_safe_tool_names()
    assert "crm_write.update_contact" not in registry.schedule_safe_tool_names()


def test_scheduled_erp_read_clamps_its_client_timeout_to_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_timeouts: list[float] = []

    def factory(config: ToolRuntimeConfig) -> ERPNextClient:
        captured_timeouts.append(config.erpnext_api_timeout_seconds)
        return FakeERPNextClient()

    monkeypatch.setattr(deadlines, "monotonic", lambda: 100.0)
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(
            agent_erp_organization_id="org-508",
            erpnext_api_timeout_seconds=20.0,
        ),
        erpnext_client_factory=factory,
    )

    registry.execute(
        "billing_read.search_invoices",
        {"invoice_type": "sales", "query": "SINV", "limit": 3},
        organization_id="org-508",
        actor_id="user-1",
        deadline_monotonic=105.0,
    )

    assert captured_timeouts == [5.0]


def test_scheduled_onboarding_summary_clamps_database_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _OnboardingCursor()
    connection = _OnboardingConnection(cursor)
    captured_connect_kwargs: dict[str, object] = {}

    def connect(*_args: object, **kwargs: object) -> _OnboardingConnection:
        captured_connect_kwargs.update(kwargs)
        return connection

    monkeypatch.setattr(agent_tools, "connect", connect)
    monkeypatch.setattr(deadlines, "monotonic", lambda: 100.0)
    registry = ToolRegistry(
        runtime_config=ToolRuntimeConfig(postgres_url="postgres://test")
    )

    registry.execute(
        "onboarding_read.get_summary",
        {},
        organization_id="org-508",
        actor_id="user-1",
        deadline_monotonic=104.5,
    )

    assert captured_connect_kwargs == {
        "connect_timeout": 4,
        "options": "-c statement_timeout=4500",
    }
    assert cursor.calls[0] == (
        "SELECT set_config('statement_timeout', %s, true)",
        ("4500",),
    )


def test_execute_plan_does_not_start_another_tool_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry()
    calls: list[str] = []

    def execute(tool_name: str, *_args: object, **_kwargs: object) -> dict[str, str]:
        calls.append(tool_name)
        return {"tool_name": tool_name}

    monkeypatch.setattr(registry, "execute", execute)
    timestamps = iter([100.0, 105.0])
    monkeypatch.setattr(agent_orchestrator, "monotonic", lambda: next(timestamps))
    plan = AgentPlan(
        plan_id="deadline-plan",
        intent="scheduled_billing_report",
        model_tier="fast",
        model=AgentModelConfig().resolve("fast"),
        actions=[
            AgentToolAction(
                tool_name="billing_read.search_invoices",
                arguments={"invoice_type": "sales", "query": "SINV"},
                summary="Search sales invoices",
            ),
            AgentToolAction(
                tool_name="billing_read.search_invoices",
                arguments={"invoice_type": "sales", "query": "SINV"},
                summary="Search sales invoices again",
            ),
        ],
        human_summary="Search sales invoices twice",
    )

    results = AgentOrchestrator(registry=registry).execute_plan(
        plan,
        _context(["Billing Team"]),
        effective_scopes={"billing:invoice:read"},
        deadline_monotonic=102.0,
    )

    assert calls == ["billing_read.search_invoices"]
    assert [result.status for result in results] == ["succeeded", "failed"]
    assert results[1].error == "Agent execution deadline exceeded before starting tool"


def test_erp_reads_return_whitelisted_summaries_and_close_clients() -> None:
    registry, clients = _registry()

    invoices = registry.execute(
        "billing_read.search_invoices",
        {"invoice_type": "sales", "query": "SINV", "limit": 3},
        organization_id="org-508",
        actor_id="user-1",
    )
    invoice = registry.execute(
        "billing_read.get_invoice_summary",
        {"invoice_type": "sales", "invoice_id": "SINV-0001"},
        organization_id="org-508",
        actor_id="user-1",
    )
    suppliers = registry.execute(
        "billing_read.search_suppliers",
        {"query": "Acme"},
        organization_id="org-508",
        actor_id="user-1",
    )
    projects = registry.execute(
        "erp_read.search_projects",
        {"query": "Atlas", "limit": 2},
        organization_id="org-508",
        actor_id="user-1",
    )
    project = registry.execute(
        "erp_read.get_project_summary",
        {"project_id": "PROJ-001"},
        organization_id="org-508",
        actor_id="user-1",
    )

    assert invoices == {
        "invoice_type": "sales",
        "invoices": [
            {
                "invoice_id": "SINV-0001",
                "posting_date": "2026-07-01",
                "status": "submitted",
            }
        ],
    }
    assert invoice == {
        "invoice": {
            "invoice_id": "SINV-0001",
            "invoice_type": "sales",
            "status": "submitted",
            "posting_date": "2026-07-01",
            "due_date": "2026-08-01",
            "customer": "Acme Customer",
            "supplier": None,
            "project": "PROJ-001",
            "currency": "USD",
            "grand_total": 1250.5,
            "rounded_total": 1251,
            "outstanding_amount": 500.25,
        }
    }
    assert suppliers == {
        "suppliers": [
            {
                "supplier_id": "SUP-001",
                "supplier_name": "Acme Supplier",
                "email": "billing@acme.example",
            }
        ]
    }
    assert projects["projects"][0]["project_id"] == "PROJ-001"
    assert projects["projects"][0]["project_name"] == "Atlas"
    assert "users" not in projects["projects"][0]
    assert project["project"]["project_id"] == "PROJ-001"
    assert "users" not in project["project"]
    assert all(client.closed for client in clients)
    assert clients[0].calls == [
        (
            "search_invoices",
            {"doctype": "Sales Invoice", "query": "SINV", "limit": 3},
        )
    ]
    assert clients[3].calls[0][0] == "list_records"
    assert clients[3].calls[0][1]["doctype"] == "Project"
    assert clients[3].calls[0][1]["limit"] == 2


@pytest.mark.parametrize(
    ("tool_name", "arguments", "match"),
    [
        (
            "billing_read.search_invoices",
            {"invoice_type": "Sales Invoice", "query": "SINV"},
            "invoice_type",
        ),
        (
            "billing_read.search_invoices",
            {"invoice_type": "sales", "query": "SINV", "limit": 11},
            "between 1 and 10",
        ),
        (
            "billing_read.search_invoices",
            {"invoice_type": "sales", "query": "%"},
            "wildcard",
        ),
        (
            "billing_read.search_suppliers",
            {"query": "Acme_"},
            "wildcard",
        ),
        (
            "erp_read.search_projects",
            {"query": "Atlas", "doctype": "User"},
            "Unknown ERP read arguments",
        ),
    ],
)
def test_erp_reads_reject_unbounded_or_undeclared_arguments_before_client(
    tool_name: str,
    arguments: dict[str, object],
    match: str,
) -> None:
    registry, clients = _registry()

    with pytest.raises(ValueError, match=match):
        registry.execute(
            tool_name,
            arguments,
            organization_id="org-508",
            actor_id="user-1",
        )

    assert clients == []


def test_erp_reads_fail_closed_for_missing_or_other_tenant_before_client() -> None:
    registry, clients = _registry()
    arguments = {"invoice_type": "sales", "query": "SINV"}

    with pytest.raises(PermissionError, match="this organization"):
        registry.execute(
            "billing_read.search_invoices",
            arguments,
            organization_id="other-org",
            actor_id="user-1",
        )
    assert clients == []

    unconfigured_registry, unconfigured_clients = _registry(
        configured_organization_id=None
    )
    with pytest.raises(PermissionError, match="not configured"):
        unconfigured_registry.execute(
            "billing_read.search_invoices",
            arguments,
            organization_id="org-508",
            actor_id="user-1",
        )
    assert unconfigured_clients == []


def test_erp_client_errors_are_generic_and_close_the_client() -> None:
    registry, clients = _registry(fail_invoice_search=True)

    with pytest.raises(
        RuntimeError, match="ERP lookup is temporarily unavailable"
    ) as exc:
        registry.execute(
            "billing_read.search_invoices",
            {"invoice_type": "sales", "query": "SINV"},
            organization_id="org-508",
            actor_id="user-1",
        )

    assert "internal detail" not in str(exc.value)
    assert clients[0].closed


def test_erp_project_summary_returns_null_for_a_missing_project() -> None:
    registry, clients = _registry(project_error_status=404)

    result = registry.execute(
        "erp_read.get_project_summary",
        {"project_id": "PROJ-MISSING"},
        organization_id="org-508",
        actor_id="user-1",
    )

    assert result == {"project": None}
    assert clients[0].closed


def test_billing_and_erp_roles_are_limited_to_their_read_only_tools() -> None:
    registry, clients = _registry()
    orchestrator = AgentOrchestrator(registry=registry)

    billing = orchestrator.plan(
        "search Sales Invoice for SINV-0001",
        _context(["Billing Team"]),
    )
    erp_developer = orchestrator.plan(
        "show ERP project PROJ-001",
        _context(["ERP Developer"]),
    )
    billing_denied = orchestrator.plan(
        "show ERP project PROJ-001",
        _context(["Billing Team"]),
    )
    steering_denied = orchestrator.plan(
        "find supplier Acme",
        _context(["Steering Committee"]),
    )

    assert billing.status == "executed"
    assert billing.plan is not None
    assert billing.plan.actions[0].tool_name == "billing_read.search_invoices"
    assert not billing.plan.requires_confirmation
    assert erp_developer.status == "executed"
    assert erp_developer.plan is not None
    assert erp_developer.plan.actions[0].tool_name == "erp_read.get_project_summary"
    assert billing_denied.status == "denied"
    assert "erp:project:read" in billing_denied.message
    assert steering_denied.status == "denied"
    assert "billing:supplier:read" in steering_denied.message
    assert len(clients) == 2


def test_explicit_erp_reads_do_not_reach_the_planner_and_missing_targets_clarify() -> (
    None
):
    class RaisingPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def plan(self, **_kwargs: object) -> AgentPlannerResult | None:
            self.calls += 1
            raise AssertionError("finance request must not reach the planner")

    registry, clients = _registry()
    planner = RaisingPlanner()
    orchestrator = AgentOrchestrator(registry=registry, planner=planner)

    response = orchestrator.plan(
        "show Sales Invoice SINV-0001",
        _context(["Billing Team"]),
    )
    incomplete_invoice = orchestrator.plan(
        "search invoices",
        _context(["Billing Team"]),
    )
    incomplete_project = orchestrator.plan(
        "show ERP project",
        _context(["ERP Developer"]),
    )

    assert response.status == "executed"
    assert planner.calls == 0
    assert len(clients) == 1
    assert incomplete_invoice.status == "needs_clarification"
    assert "Sales or Purchase" in (incomplete_invoice.clarification_question or "")
    assert incomplete_project.status == "needs_clarification"
    assert "project ID" in (incomplete_project.clarification_question or "")


def test_planner_prompt_advertises_only_read_only_erp_tools() -> None:
    from five08.agent.planner import PLANNER_SYSTEM_PROMPT

    assert "billing_read.search_invoices" in PLANNER_SYSTEM_PROMPT
    assert "billing_read.get_invoice_summary" in PLANNER_SYSTEM_PROMPT
    assert "billing_read.search_suppliers" in PLANNER_SYSTEM_PROMPT
    assert "erp_read.search_projects" in PLANNER_SYSTEM_PROMPT
    assert "erp_read.get_project_summary" in PLANNER_SYSTEM_PROMPT
    assert "billing_write" not in PLANNER_SYSTEM_PROMPT


def test_empty_confirmation_scope_override_fails_closed() -> None:
    context = _context(["Steering Committee"])
    plan = AgentPlan(
        plan_id="plan-1",
        intent="search_tasks",
        model_tier="fast",
        model=AgentModelConfig().resolve("fast"),
        actions=[
            AgentToolAction(
                tool_name="task_read.search_tasks",
                arguments={"query": "Atlas", "project": "Atlas"},
                summary="Search Atlas tasks",
            )
        ],
        human_summary="Search Atlas tasks",
    )

    results = AgentOrchestrator().execute_plan(
        plan,
        context,
        effective_scopes=set(),
    )

    assert results[0].status == "denied"
    assert results[0].error == "Missing required scopes: project:read"
