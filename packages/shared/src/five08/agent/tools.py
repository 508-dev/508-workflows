"""Deterministic tool registry and inline MVP task tools."""

from __future__ import annotations

import itertools
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from psycopg import connect
from psycopg.rows import dict_row

from five08.agent.memory import (
    InMemoryMemoryStore,
    MemoryStore,
    validate_memory_value_for_persistence,
)
from five08.agent.models import (
    MemoryFact,
    MemoryScopeType,
    MemoryVisibility,
    RiskLevel,
    validate_memory_fact_key,
)
from five08.agent.privacy import contains_private_agent_identifier
from five08.agent.schedules import AgentScheduleProposal
from five08.agent.web import (
    BraveWebSearch,
    FirecrawlWebResearch,
    SearxngWebSearch,
    WebResearchClient,
    WebResearchValidationError,
    WebSearchProvider,
    validate_public_https_url,
    validate_public_https_url_shape,
    validate_web_search_limit,
    validate_web_search_query,
)
from five08.clients.authentik import AuthentikAPIError, AuthentikClient
from five08.clients.docuseal import create_member_agreement_submission
from five08.clients.espo import EspoAPIError, EspoClient
from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.clients.github import GitHubAppTokenProvider, GitHubClient
from five08.clients.migadu import (
    MigaduClient,
    MigaduMailboxCreateRequest,
    normalize_migadu_mailbox_domain,
)
from five08.clients.outline import OutlineClient
from five08.crm_contacts import EspoContactRepository
from five08.deadlines import DeadlineExceeded, clamp_timeout_seconds
from five08.newsletter_sync import (
    format_newsletter_sync_warning,
    sync_newsletter_contacts,
)
from five08.redaction import redact_email_addresses

SSO_ID_FIELD = "cSsoID"
logger = logging.getLogger(__name__)

_PLANNER_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "agent_schedule.create": frozenset(
        {"name", "cron_expression", "timezone", "prompt"}
    ),
    "task_read.search_tasks": frozenset({"query", "project"}),
    "task_write.create_task": frozenset({"title", "project", "assignee", "due_date"}),
    "task_write.update_task": frozenset(
        {"task_id", "title", "project", "assignee", "due_date", "status"}
    ),
    "github_issue.search_issues": frozenset({"query", "repository", "state", "limit"}),
    "github_issue.get_issue": frozenset({"repository", "issue_number"}),
    "github_issue.create_issue": frozenset({"title", "repository", "body", "labels"}),
    "github_issue.update_issue": frozenset(
        {"repository", "issue_number", "title", "body", "state", "state_reason"}
    ),
    "github_issue.comment_on_issue": frozenset({"repository", "issue_number", "body"}),
    "github_repository.list_repositories": frozenset(),
    "github_project.list_projects": frozenset({"organization", "limit"}),
    "github_project.get_project": frozenset({"organization", "project_number"}),
    "github_project.list_project_fields": frozenset(
        {"organization", "project_number", "limit"}
    ),
    "github_project.list_project_items": frozenset(
        {"organization", "project_number", "limit"}
    ),
    "github_project.add_issue_to_project": frozenset(
        {"organization", "project_number", "repository", "issue_number"}
    ),
    "github_project.update_project_item": frozenset(
        {"organization", "project_number", "item_id", "fields"}
    ),
    "crm_read.search_contacts": frozenset({"query", "limit"}),
    "crm_write.update_contact": frozenset({"contact_id", "updates"}),
    "docuseal_write.create_member_agreement_submission": frozenset(
        {"submitter_email", "submitter_name", "send_email"}
    ),
    "mail_write.create_mailbox": frozenset(
        {"local_part", "backup_email", "name", "contact_id", "contact_query"}
    ),
    "sso_write.create_user": frozenset({"contact_id", "contact_query"}),
    "outline_write.invite_user": frozenset({"email", "contact_id", "contact_query"}),
    "account_write.create_user_accounts": frozenset(
        {"contact_id", "contact_query", "mailbox_username"}
    ),
    "memory_read.get_user_facts": frozenset({"user_id"}),
    "memory_read.get_project_facts": frozenset(),
    "memory_read.search_context": frozenset(),
    "memory_write.remember_fact": frozenset(
        {
            "scope_type",
            "key",
            "value_json",
            "visibility",
        }
    ),
    "memory_write.forget_fact": frozenset({"fact_id", "admin"}),
    "billing_read.search_invoices": frozenset({"invoice_type", "query", "limit"}),
    "billing_read.get_invoice_summary": frozenset({"invoice_type", "invoice_id"}),
    "billing_read.search_suppliers": frozenset({"query", "limit"}),
    "erp_read.search_projects": frozenset({"query", "limit"}),
    "erp_read.get_project_summary": frozenset({"project_id"}),
    "onboarding_read.get_summary": frozenset(),
    "web_read.search": frozenset({"query", "limit"}),
    "web_read.extract": frozenset({"url"}),
}

_ERP_INVOICE_DOCTYPES = {
    "sales": "Sales Invoice",
    "purchase": "Purchase Invoice",
}
_ERP_READ_TOOL_NAMES = frozenset(
    {
        "billing_read.search_invoices",
        "billing_read.get_invoice_summary",
        "billing_read.search_suppliers",
        "erp_read.search_projects",
        "erp_read.get_project_summary",
    }
)
_ERP_PROJECT_FIELDS = [
    "name",
    "project_name",
    "status",
    "customer",
    "project_type",
    "priority",
    "percent_complete",
    "expected_start_date",
    "expected_end_date",
    "actual_start_date",
    "actual_end_date",
    "modified",
]


@dataclass(frozen=True)
class ToolManifest:
    """Static metadata used by policy before a tool can execute."""

    name: str
    risk: RiskLevel
    required_scopes: tuple[str, ...] = ()
    requires_confirmation: bool = False
    tenant_scoped: bool = True
    idempotent: bool = False
    write: bool = False
    # Recurring schedules may only expose tools that opt into this narrower
    # capability surface.  Adding a normal read tool never silently gives
    # existing schedules a new data source.
    schedule_safe: bool = False


class ToolPartialSuccessError(RuntimeError):
    """Raised when an irreversible tool step succeeded before a later failure."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ToolRuntimeConfig:
    """Runtime credentials and defaults for deterministic external tools."""

    github_default_repo: str = "508-dev/todos"
    github_organization: str = "508-dev"
    github_member_extra_repos: str = ""
    github_steering_all_installed_repos: bool = True
    github_steering_extra_repos: str = ""
    github_app_client_id: str | None = None
    github_app_installation_id: str | None = None
    github_app_private_key: str | None = None
    github_api_token: str | None = None
    github_allowed_repos: str = ""
    espo_base_url: str | None = None
    espo_api_key: str | None = None
    docuseal_base_url: str | None = None
    docuseal_api_key: str | None = None
    docuseal_member_agreement_template_id: int | None = None
    migadu_api_user: str | None = None
    migadu_api_key: str | None = None
    migadu_mailbox_domain: str = "508.dev"
    authentik_api_base_url: str | None = None
    authentik_api_token: str | None = None
    authentik_api_timeout_seconds: float = 20.0
    authentik_recovery_email_stage_id: str | None = None
    authentik_recovery_email_stage_name: str = "default-recovery-email"
    outline_base_url: str = "https://app.getoutline.com"
    outline_admin_api_key: str | None = None
    outline_api_timeout_seconds: float = 20.0
    brevo_api_key: str | None = None
    brevo_api_base_url: str = "https://api.brevo.com/v3"
    brevo_api_timeout_seconds: float = 20.0
    brevo_508_members_newsletter_list_id: int | None = None
    brevo_508_members_newsletter_list_name: str = "508 members"
    keila_api_key: str | None = None
    keila_api_base_url: str = "https://app.keila.io"
    keila_api_timeout_seconds: float = 20.0
    postgres_url: str | None = None
    erpnext_base_url: str | None = None
    erpnext_api_key: str | None = None
    erpnext_api_timeout_seconds: float = 20.0
    agent_erp_organization_id: str | None = None
    agent_web_search_provider_order: str = "searxng,brave,firecrawl"
    agent_web_search_timeout_seconds: float = 5.0
    agent_web_default_result_limit: int = 5
    searxng_base_url: str | None = None
    searxng_search_language: str | None = None
    brave_search_api_key: str | None = None
    brave_search_base_url: str = "https://api.search.brave.com"
    brave_search_country: str | None = None
    brave_search_language: str | None = None
    firecrawl_api_key: str | None = None
    firecrawl_base_url: str = "https://api.firecrawl.dev"

    @classmethod
    def from_settings(cls, settings: Any) -> "ToolRuntimeConfig":
        """Build tool runtime config from service settings without coupling types."""
        return cls(
            github_default_repo=getattr(
                settings, "github_default_repo", "508-dev/todos"
            ),
            github_organization=getattr(settings, "github_organization", "508-dev"),
            github_member_extra_repos=getattr(
                settings, "github_member_extra_repos", ""
            ),
            github_steering_all_installed_repos=bool(
                getattr(settings, "github_steering_all_installed_repos", True)
            ),
            github_steering_extra_repos=getattr(
                settings, "github_steering_extra_repos", ""
            ),
            github_app_client_id=getattr(settings, "github_app_client_id", None),
            github_app_installation_id=getattr(
                settings, "github_app_installation_id", None
            ),
            github_app_private_key=getattr(settings, "github_app_private_key", None),
            github_api_token=getattr(settings, "github_api_token", None),
            github_allowed_repos=getattr(settings, "github_allowed_repos", ""),
            espo_base_url=getattr(settings, "espo_base_url", None),
            espo_api_key=getattr(settings, "espo_api_key", None),
            docuseal_base_url=getattr(settings, "docuseal_base_url", None),
            docuseal_api_key=getattr(settings, "docuseal_api_key", None),
            docuseal_member_agreement_template_id=getattr(
                settings,
                "docuseal_member_agreement_template_id",
                None,
            ),
            migadu_api_user=getattr(settings, "migadu_api_user", None),
            migadu_api_key=getattr(settings, "migadu_api_key", None),
            migadu_mailbox_domain=getattr(
                settings,
                "migadu_mailbox_domain",
                "508.dev",
            ),
            authentik_api_base_url=getattr(settings, "authentik_api_base_url", None),
            authentik_api_token=getattr(settings, "authentik_api_token", None),
            authentik_api_timeout_seconds=getattr(
                settings,
                "authentik_api_timeout_seconds",
                20.0,
            ),
            authentik_recovery_email_stage_id=getattr(
                settings,
                "authentik_recovery_email_stage_id",
                None,
            ),
            authentik_recovery_email_stage_name=getattr(
                settings,
                "authentik_recovery_email_stage_name",
                "default-recovery-email",
            ),
            outline_base_url=getattr(
                settings,
                "outline_base_url",
                "https://app.getoutline.com",
            ),
            outline_admin_api_key=(
                getattr(settings, "outline_admin_api_key", None)
                or getattr(settings, "outline_api_key", None)
            ),
            outline_api_timeout_seconds=getattr(
                settings,
                "outline_api_timeout_seconds",
                20.0,
            ),
            brevo_api_key=getattr(settings, "brevo_api_key", None),
            brevo_api_base_url=getattr(
                settings,
                "brevo_api_base_url",
                "https://api.brevo.com/v3",
            ),
            brevo_api_timeout_seconds=getattr(
                settings,
                "brevo_api_timeout_seconds",
                20.0,
            ),
            brevo_508_members_newsletter_list_id=getattr(
                settings, "brevo_508_members_newsletter_list_id", None
            ),
            brevo_508_members_newsletter_list_name=getattr(
                settings, "brevo_508_members_newsletter_list_name", "508 members"
            ),
            keila_api_key=getattr(settings, "keila_api_key", None),
            keila_api_base_url=getattr(
                settings, "keila_api_base_url", "https://app.keila.io"
            ),
            keila_api_timeout_seconds=getattr(
                settings, "keila_api_timeout_seconds", 20.0
            ),
            postgres_url=getattr(settings, "postgres_url", None),
            erpnext_base_url=getattr(settings, "erpnext_base_url", None),
            erpnext_api_key=getattr(settings, "erpnext_api_key", None),
            erpnext_api_timeout_seconds=getattr(
                settings,
                "erpnext_api_timeout_seconds",
                20.0,
            ),
            agent_erp_organization_id=getattr(
                settings,
                "agent_erp_organization_id",
                None,
            ),
            agent_web_search_provider_order=getattr(
                settings,
                "agent_web_search_provider_order",
                "searxng,brave,firecrawl",
            ),
            agent_web_search_timeout_seconds=getattr(
                settings,
                "agent_web_search_timeout_seconds",
                5.0,
            ),
            agent_web_default_result_limit=getattr(
                settings,
                "agent_web_default_result_limit",
                5,
            ),
            searxng_base_url=getattr(settings, "searxng_base_url", None),
            searxng_search_language=getattr(
                settings,
                "searxng_search_language",
                None,
            ),
            brave_search_api_key=getattr(settings, "brave_search_api_key", None),
            brave_search_base_url=getattr(
                settings,
                "brave_search_base_url",
                "https://api.search.brave.com",
            ),
            brave_search_country=getattr(settings, "brave_search_country", None),
            brave_search_language=getattr(settings, "brave_search_language", None),
            firecrawl_api_key=getattr(settings, "firecrawl_api_key", None),
            firecrawl_base_url=getattr(
                settings,
                "firecrawl_base_url",
                "https://api.firecrawl.dev",
            ),
        )


@dataclass
class TaskRecord:
    """Small in-memory task record for the agent gateway MVP."""

    task_id: str
    title: str
    project: str | None
    assignee: str | None
    due_date: str | None
    status: str = "open"
    organization_id: str | None = None
    created_by: str | None = None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "project": self.project,
            "assignee": self.assignee,
            "due_date": self.due_date,
            "status": self.status,
            "organization_id": self.organization_id,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
        }


class InMemoryTaskStore:
    """Process-local MVP task store for synchronous agent command execution."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        *,
        title: str,
        project: str | None,
        assignee: str | None,
        due_date: str | None,
        organization_id: str | None,
        created_by: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            task_id = f"TASK-{next(self._counter):03d}"
            task = TaskRecord(
                task_id=task_id,
                title=title,
                project=project,
                assignee=assignee,
                due_date=due_date,
                organization_id=organization_id,
                created_by=created_by,
            )
            self._tasks[task_id] = task
            return task.to_payload()

    def update_task(
        self,
        *,
        task_id: str,
        organization_id: str | None,
        actor_id: str | None,
        can_update_any: bool = False,
        title: str | None = None,
        project: str | None = None,
        assignee: str | None = None,
        due_date: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task {task_id} was not found")
            if task.organization_id and organization_id != task.organization_id:
                raise PermissionError("Task belongs to a different organization")
            if task.created_by and actor_id != task.created_by and not can_update_any:
                raise PermissionError("Task can only be updated by its creator")
            if title is not None:
                task.title = title
            if project is not None:
                task.project = project
            if assignee is not None:
                task.assignee = assignee
            if due_date is not None:
                task.due_date = due_date
            if status is not None:
                task.status = status
            task.updated_at = datetime.now(timezone.utc).isoformat()
            return task.to_payload()

    def search_tasks(
        self,
        *,
        query: str,
        organization_id: str | None,
        project: str | None = None,
    ) -> dict[str, Any]:
        if not project:
            return {"tasks": []}
        normalized_query = query.strip().casefold()
        normalized_project = project.strip().casefold() if project else None
        matches: list[dict[str, Any]] = []
        with self._lock:
            for task in self._tasks.values():
                if task.organization_id and organization_id != task.organization_id:
                    continue
                if (
                    normalized_project
                    and (task.project or "").casefold() != normalized_project
                ):
                    continue
                searchable = " ".join(
                    value
                    for value in [
                        task.task_id,
                        task.title,
                        task.project or "",
                        task.assignee or "",
                        task.status,
                    ]
                    if value
                ).casefold()
                if not normalized_query or normalized_query in searchable:
                    matches.append(task.to_payload())
        return {"tasks": matches}


class ToolRegistry:
    """Registry that exposes only explicitly declared tools."""

    def __init__(
        self,
        task_store: InMemoryTaskStore | None = None,
        *,
        memory_store: MemoryStore | None = None,
        runtime_config: ToolRuntimeConfig | None = None,
        runtime_config_factory: Callable[[], ToolRuntimeConfig] | None = None,
        web_client: WebResearchClient | None = None,
        web_client_factory: Callable[[ToolRuntimeConfig], WebResearchClient]
        | None = None,
        erpnext_client_factory: Callable[[ToolRuntimeConfig], ERPNextClient]
        | None = None,
    ) -> None:
        self.task_store = task_store or InMemoryTaskStore()
        self.memory_store = memory_store or InMemoryMemoryStore()
        self._runtime_config = runtime_config or ToolRuntimeConfig()
        self._runtime_config_factory = runtime_config_factory
        self._web_client = web_client
        self._web_client_factory = web_client_factory
        self._erpnext_client_factory = erpnext_client_factory
        self._github_app_provider: GitHubAppTokenProvider | None = None
        self._github_app_provider_config: tuple[str, str, str] | None = None
        self._github_installation_repository_cache: (
            tuple[datetime, tuple[str, str], frozenset[str]] | None
        ) = None
        self._manifests = {
            # This action is intentionally API-owned. The registry gives the
            # planner a typed, policy-controlled proposal surface, while the
            # backend binds the current Discord channel and persists the
            # schedule only after confirmation and a fresh member snapshot.
            "agent_schedule.create": ToolManifest(
                name="agent_schedule.create",
                risk="high",
                required_scopes=("agent:schedule:manage",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "task_read.search_tasks": ToolManifest(
                name="task_read.search_tasks",
                risk="low",
                required_scopes=("project:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
            ),
            "task_write.create_task": ToolManifest(
                name="task_write.create_task",
                risk="medium",
                required_scopes=("task:create",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "task_write.update_task": ToolManifest(
                name="task_write.update_task",
                risk="medium",
                required_scopes=("task:update_own",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "github_issue.search_issues": ToolManifest(
                name="github_issue.search_issues",
                risk="low",
                required_scopes=(),
                tenant_scoped=False,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "github_issue.get_issue": ToolManifest(
                name="github_issue.get_issue",
                risk="low",
                required_scopes=(),
                tenant_scoped=False,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "github_issue.create_issue": ToolManifest(
                name="github_issue.create_issue",
                risk="medium",
                required_scopes=(),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "github_issue.update_issue": ToolManifest(
                name="github_issue.update_issue",
                risk="medium",
                required_scopes=(),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "github_issue.comment_on_issue": ToolManifest(
                name="github_issue.comment_on_issue",
                risk="medium",
                required_scopes=(),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "github_repository.list_repositories": ToolManifest(
                name="github_repository.list_repositories",
                risk="low",
                required_scopes=("github:repository:all:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_project.list_projects": ToolManifest(
                name="github_project.list_projects",
                risk="low",
                required_scopes=("github:project:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_project.get_project": ToolManifest(
                name="github_project.get_project",
                risk="low",
                required_scopes=("github:project:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_project.list_project_fields": ToolManifest(
                name="github_project.list_project_fields",
                risk="low",
                required_scopes=("github:project:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_project.list_project_items": ToolManifest(
                name="github_project.list_project_items",
                risk="low",
                required_scopes=("github:project:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_project.add_issue_to_project": ToolManifest(
                name="github_project.add_issue_to_project",
                risk="medium",
                required_scopes=("github:project:write",),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "github_project.update_project_item": ToolManifest(
                name="github_project.update_project_item",
                risk="medium",
                required_scopes=("github:project:write",),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "crm_read.search_contacts": ToolManifest(
                name="crm_read.search_contacts",
                risk="low",
                required_scopes=("crm:contact:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "billing_read.search_invoices": ToolManifest(
                name="billing_read.search_invoices",
                risk="low",
                required_scopes=("billing:invoice:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "billing_read.get_invoice_summary": ToolManifest(
                name="billing_read.get_invoice_summary",
                risk="low",
                required_scopes=("billing:invoice:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "billing_read.search_suppliers": ToolManifest(
                name="billing_read.search_suppliers",
                risk="low",
                required_scopes=("billing:supplier:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "erp_read.search_projects": ToolManifest(
                name="erp_read.search_projects",
                risk="low",
                required_scopes=("erp:project:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "erp_read.get_project_summary": ToolManifest(
                name="erp_read.get_project_summary",
                risk="low",
                required_scopes=("erp:project:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "onboarding_read.get_summary": ToolManifest(
                name="onboarding_read.get_summary",
                risk="low",
                required_scopes=("crm:contact:read",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "crm_write.update_contact": ToolManifest(
                name="crm_write.update_contact",
                risk="high",
                required_scopes=("crm:contact:update",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "docuseal_write.create_member_agreement_submission": ToolManifest(
                name="docuseal_write.create_member_agreement_submission",
                risk="high",
                required_scopes=("docuseal:submission:create",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "mail_write.create_mailbox": ToolManifest(
                name="mail_write.create_mailbox",
                risk="high",
                required_scopes=("mailbox:create", "integration:manage"),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "sso_write.create_user": ToolManifest(
                name="sso_write.create_user",
                risk="high",
                required_scopes=(
                    "user:manage",
                    "crm:contact:read",
                    "crm:contact:update",
                ),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "outline_write.invite_user": ToolManifest(
                name="outline_write.invite_user",
                risk="high",
                required_scopes=("integration:manage", "crm:contact:read"),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "account_write.create_user_accounts": ToolManifest(
                name="account_write.create_user_accounts",
                risk="high",
                required_scopes=(
                    "mailbox:create",
                    "user:manage",
                    "integration:manage",
                    "crm:contact:read",
                    "crm:contact:update",
                ),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "memory_read.get_user_facts": ToolManifest(
                name="memory_read.get_user_facts",
                risk="low",
                required_scopes=("memory:read_self",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
            ),
            "memory_read.get_project_facts": ToolManifest(
                name="memory_read.get_project_facts",
                risk="low",
                required_scopes=("memory:read_project",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
            ),
            "memory_read.search_context": ToolManifest(
                name="memory_read.search_context",
                risk="low",
                required_scopes=("context:read_current_thread",),
                tenant_scoped=True,
                idempotent=True,
                write=False,
            ),
            "web_read.search": ToolManifest(
                name="web_read.search",
                risk="low",
                required_scopes=("web:research",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "web_read.extract": ToolManifest(
                name="web_read.extract",
                risk="low",
                required_scopes=("web:research",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
                schedule_safe=True,
            ),
            "memory_write.remember_fact": ToolManifest(
                name="memory_write.remember_fact",
                risk="medium",
                required_scopes=("memory:write_self",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
            "memory_write.forget_fact": ToolManifest(
                name="memory_write.forget_fact",
                risk="medium",
                required_scopes=("memory:write_self",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
        }

    @property
    def runtime_config(self) -> ToolRuntimeConfig:
        """Return the current external-tool runtime config."""
        if self._runtime_config_factory is not None:
            return self._runtime_config_factory()
        return self._runtime_config

    def get(self, tool_name: str) -> ToolManifest | None:
        return self._manifests.get(tool_name)

    def schedule_safe_tool_names(self) -> frozenset[str]:
        """Return the explicitly approved read-only schedule tool catalog."""

        return frozenset(
            name
            for name, manifest in self._manifests.items()
            if manifest.schedule_safe
            and manifest.idempotent
            and not manifest.write
            and not manifest.requires_confirmation
        )

    def validate_planner_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Apply a deterministic shape boundary to model-proposed arguments.

        Individual tool handlers remain responsible for their business validation.
        This gate prevents a model from smuggling undeclared control fields to a
        handler before policy and confirmation are evaluated.
        """

        if tool_name not in self._manifests:
            raise ValueError("unknown_tool")
        allowed_arguments = _PLANNER_TOOL_ARGUMENTS.get(tool_name)
        if allowed_arguments is None:
            raise ValueError("planner_tool_not_supported")
        unknown_arguments = set(arguments) - allowed_arguments
        if unknown_arguments:
            raise ValueError("unknown_arguments")
        if tool_name == "crm_write.update_contact":
            updates = arguments.get("updates")
            if not isinstance(updates, dict) or set(updates) != {"cOnboardingState"}:
                raise ValueError("unsupported_crm_update_fields")
        if tool_name == "crm_read.search_contacts":
            _validate_crm_contact_search_arguments(arguments)
        if tool_name == "memory_write.remember_fact":
            scope_type = _memory_scope_type(arguments.get("scope_type"), default="user")
            _memory_visibility_for_scope(
                arguments.get("visibility"),
                scope_type=scope_type,
            )
            _validate_memory_write_arguments(arguments)
        if tool_name == "agent_schedule.create":
            try:
                AgentScheduleProposal.model_validate(arguments)
            except ValueError as exc:
                raise ValueError("invalid_agent_schedule_proposal") from exc
        if tool_name in _ERP_READ_TOOL_NAMES:
            _validate_erp_read_arguments(tool_name, arguments)
        if tool_name == "web_read.search":
            _validate_planner_web_search_arguments(arguments)
        if tool_name == "web_read.extract":
            url = str(arguments.get("url") or "").strip()
            if contains_private_agent_identifier(url):
                raise PermissionError(
                    "Public web extraction URLs cannot contain internal record identifiers"
                )
            try:
                validate_public_https_url_shape(url)
            except WebResearchValidationError as exc:
                raise ValueError(str(exc)) from exc
        _validate_planner_argument_value(arguments)

    def normalize_action_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Freeze built-in GitHub defaults into a proposed action.

        Defaults are resolved before authorization and confirmation so the
        repository or organization a user is approving can never change between
        planning and execution.
        """

        normalized = dict(arguments)
        if tool_name.startswith("github_issue.") or tool_name == (
            "github_project.add_issue_to_project"
        ):
            if _optional_str(normalized.get("repository")) is None:
                default_repository = _optional_str(
                    self.runtime_config.github_default_repo
                )
                if default_repository is not None:
                    normalized["repository"] = default_repository
        if tool_name.startswith("github_project."):
            if _optional_str(normalized.get("organization")) is None:
                organization = _optional_str(self.runtime_config.github_organization)
                if organization is not None:
                    normalized["organization"] = organization
        return normalized

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        actor_id: str | None,
        project_id: str | None = None,
        actor_scopes: set[str] | None = None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        if tool_name == "agent_schedule.create":
            # The FastAPI confirmation handler owns this durable write so it
            # can refresh Discord roles, bind the current channel, and audit
            # the persisted schedule. Do not let a generic registry caller
            # bypass those boundaries.
            raise PermissionError("Agent schedule creation must use confirmation")
        if tool_name == "task_read.search_tasks":
            return self.task_store.search_tasks(
                query=str(arguments.get("query") or ""),
                organization_id=organization_id,
                project=_optional_str(arguments.get("project")),
            )
        if tool_name == "task_write.create_task":
            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ValueError("Task title is required")
            return self.task_store.create_task(
                title=title,
                project=_optional_str(arguments.get("project")),
                assignee=_optional_str(arguments.get("assignee")),
                due_date=_optional_date(arguments.get("due_date")),
                organization_id=organization_id,
                created_by=actor_id,
            )
        if tool_name == "task_write.update_task":
            task_id = str(arguments.get("task_id") or "").strip().upper()
            if not task_id:
                raise ValueError("Task id is required")
            updates = {
                "title": _optional_str(arguments.get("title")),
                "project": _optional_str(arguments.get("project")),
                "assignee": _optional_str(arguments.get("assignee")),
                "due_date": _optional_date(arguments.get("due_date")),
                "status": _optional_str(arguments.get("status")),
            }
            if not any(value is not None for value in updates.values()):
                raise ValueError("At least one task field must be provided")
            return self.task_store.update_task(
                task_id=task_id,
                organization_id=organization_id,
                actor_id=actor_id,
                can_update_any="task:update" in (actor_scopes or set()),
                title=updates["title"],
                project=updates["project"],
                assignee=updates["assignee"],
                due_date=updates["due_date"],
                status=updates["status"],
            )
        github_scopes = actor_scopes or set()
        if tool_name == "github_issue.search_issues":
            return self._github_client(
                deadline_monotonic=deadline_monotonic
            ).search_issues(
                repository=self._resolve_repository(
                    arguments,
                    actor_scopes=github_scopes,
                    deadline_monotonic=deadline_monotonic,
                ),
                query=str(arguments.get("query") or "").strip(),
                state=str(arguments.get("state") or "open").strip().lower(),
                limit=_positive_int(arguments.get("limit"), default=10, maximum=20),
            )
        if tool_name == "github_issue.get_issue":
            return self._github_client(deadline_monotonic=deadline_monotonic).get_issue(
                repository=self._resolve_repository(
                    arguments,
                    actor_scopes=github_scopes,
                    deadline_monotonic=deadline_monotonic,
                ),
                issue_number=_required_positive_int(
                    arguments.get("issue_number"),
                    field_name="GitHub issue number",
                ),
            )
        if tool_name == "github_issue.create_issue":
            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ValueError("GitHub issue title is required")
            return self._github_client().create_issue(
                repository=self._resolve_repository(
                    arguments,
                    actor_scopes=github_scopes,
                ),
                title=title,
                body=_optional_str(arguments.get("body")),
                labels=_optional_str_list(arguments.get("labels")),
            )
        if tool_name == "github_issue.update_issue":
            title = _optional_str(arguments.get("title"))
            state = _optional_str(arguments.get("state"))
            state_reason = _optional_str(arguments.get("state_reason"))
            if title is None and "body" not in arguments and state is None:
                raise ValueError("At least one GitHub issue update is required")
            update_kwargs: dict[str, Any] = {
                "repository": self._resolve_repository(
                    arguments,
                    actor_scopes=github_scopes,
                ),
                "issue_number": _required_positive_int(
                    arguments.get("issue_number"),
                    field_name="GitHub issue number",
                ),
                "title": title,
                "state": state,
                "state_reason": state_reason,
            }
            if "body" in arguments:
                update_kwargs["body"] = _optional_str(arguments.get("body"))
            return self._github_client().update_issue(**update_kwargs)
        if tool_name == "github_issue.comment_on_issue":
            body = _optional_str(arguments.get("body"))
            if body is None:
                raise ValueError("GitHub comment body is required")
            return self._github_client().add_issue_comment(
                repository=self._resolve_repository(
                    arguments,
                    actor_scopes=github_scopes,
                ),
                issue_number=_required_positive_int(
                    arguments.get("issue_number"),
                    field_name="GitHub issue number",
                ),
                body=body,
            )
        if tool_name == "github_repository.list_repositories":
            _require_any_scope(
                github_scopes,
                {"github:repository:all:read", "github:repository:all:write"},
                message="Listing GitHub repositories requires Steering Committee access",
            )
            return self._github_client().list_installation_repositories()
        if tool_name == "github_project.list_projects":
            _require_any_scope(
                github_scopes,
                {"github:project:read", "github:project:write"},
                message="GitHub Projects access requires Steering Committee access",
            )
            return self._github_client().list_organization_projects(
                organization=self._resolve_github_organization(arguments),
                limit=_positive_int(arguments.get("limit"), default=20, maximum=100),
            )
        if tool_name == "github_project.get_project":
            _require_any_scope(
                github_scopes,
                {"github:project:read", "github:project:write"},
                message="GitHub Projects access requires Steering Committee access",
            )
            return self._github_client().get_organization_project(
                organization=self._resolve_github_organization(arguments),
                project_number=_required_positive_int(
                    arguments.get("project_number"),
                    field_name="GitHub project number",
                ),
            )
        if tool_name == "github_project.list_project_fields":
            _require_any_scope(
                github_scopes,
                {"github:project:read", "github:project:write"},
                message="GitHub Projects access requires Steering Committee access",
            )
            return self._github_client().list_organization_project_fields(
                organization=self._resolve_github_organization(arguments),
                project_number=_required_positive_int(
                    arguments.get("project_number"),
                    field_name="GitHub project number",
                ),
                limit=_positive_int(arguments.get("limit"), default=100, maximum=100),
            )
        if tool_name == "github_project.list_project_items":
            _require_any_scope(
                github_scopes,
                {"github:project:read", "github:project:write"},
                message="GitHub Projects access requires Steering Committee access",
            )
            return self._github_client().list_organization_project_items(
                organization=self._resolve_github_organization(arguments),
                project_number=_required_positive_int(
                    arguments.get("project_number"),
                    field_name="GitHub project number",
                ),
                limit=_positive_int(arguments.get("limit"), default=20, maximum=100),
            )
        if tool_name == "github_project.add_issue_to_project":
            _require_any_scope(
                github_scopes,
                {"github:project:write"},
                message="Writing GitHub Projects requires Steering Committee access",
            )
            repository = self._resolve_repository(arguments, actor_scopes=github_scopes)
            issue = self._github_client().get_issue(
                repository=repository,
                issue_number=_required_positive_int(
                    arguments.get("issue_number"),
                    field_name="GitHub issue number",
                ),
            )
            issue_id = _required_positive_int(
                issue.get("id"),
                field_name="GitHub issue id",
            )
            return self._github_client().add_organization_project_item(
                organization=self._resolve_github_organization(arguments),
                project_number=_required_positive_int(
                    arguments.get("project_number"),
                    field_name="GitHub project number",
                ),
                content_type="Issue",
                content_id=issue_id,
            )
        if tool_name == "github_project.update_project_item":
            _require_any_scope(
                github_scopes,
                {"github:project:write"},
                message="Writing GitHub Projects requires Steering Committee access",
            )
            return self._github_client().update_organization_project_item(
                organization=self._resolve_github_organization(arguments),
                project_number=_required_positive_int(
                    arguments.get("project_number"),
                    field_name="GitHub project number",
                ),
                item_id=_required_positive_int(
                    arguments.get("item_id"),
                    field_name="GitHub project item id",
                ),
                fields=_github_project_fields(arguments.get("fields")),
            )
        if tool_name == "crm_read.search_contacts":
            return self._search_crm_contacts(
                arguments,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "billing_read.search_invoices":
            return self._search_erp_invoices(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "billing_read.get_invoice_summary":
            return self._get_erp_invoice_summary(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "billing_read.search_suppliers":
            return self._search_erp_suppliers(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "erp_read.search_projects":
            return self._search_erp_projects(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "erp_read.get_project_summary":
            return self._get_erp_project_summary(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "onboarding_read.get_summary":
            return self._get_onboarding_summary(
                arguments,
                organization_id=organization_id,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "crm_write.update_contact":
            return self._update_crm_contact(arguments)
        if tool_name == "docuseal_write.create_member_agreement_submission":
            return self._create_docuseal_member_agreement(arguments)
        if tool_name == "mail_write.create_mailbox":
            return self._create_migadu_mailbox(arguments)
        if tool_name == "sso_write.create_user":
            return self._create_sso_user(arguments)
        if tool_name == "outline_write.invite_user":
            return self._invite_outline_user(arguments)
        if tool_name == "account_write.create_user_accounts":
            return self._create_user_accounts(arguments)
        if tool_name == "memory_read.get_user_facts":
            return self._get_user_memory_facts(
                arguments,
                actor_id=actor_id,
                organization_id=organization_id,
                actor_scopes=actor_scopes or set(),
            )
        if tool_name == "memory_read.get_project_facts":
            return self._get_project_memory_facts(
                arguments,
                actor_id=actor_id,
                organization_id=organization_id,
                project_id=project_id,
                actor_scopes=actor_scopes or set(),
            )
        if tool_name == "memory_read.search_context":
            return {"snippets": []}
        if tool_name == "web_read.search":
            return self._search_public_web(
                arguments,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "web_read.extract":
            return self._extract_public_web(
                arguments,
                deadline_monotonic=deadline_monotonic,
            )
        if tool_name == "memory_write.remember_fact":
            return self._remember_memory_fact(
                arguments,
                actor_id=actor_id,
                organization_id=organization_id,
                project_id=project_id,
                actor_scopes=actor_scopes or set(),
            )
        if tool_name == "memory_write.forget_fact":
            return self._forget_memory_fact(
                arguments,
                actor_id=actor_id,
                organization_id=organization_id,
                actor_scopes=actor_scopes or set(),
            )
        raise KeyError(f"Unknown tool {tool_name}")

    def _erpnext_client(
        self,
        *,
        deadline_monotonic: float | None = None,
    ) -> ERPNextClient:
        config = self.runtime_config
        timeout_seconds = clamp_timeout_seconds(
            max(1.0, float(config.erpnext_api_timeout_seconds)),
            deadline_monotonic=deadline_monotonic,
        )
        if self._erpnext_client_factory is not None:
            return self._erpnext_client_factory(
                replace(config, erpnext_api_timeout_seconds=timeout_seconds)
            )
        base_url = _optional_str(config.erpnext_base_url)
        api_key = _optional_str(config.erpnext_api_key)
        if base_url is None or api_key is None:
            raise RuntimeError("ERP lookup is not configured")
        return ERPNextClient(
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            deadline_monotonic=deadline_monotonic,
        )

    def _require_erp_tenant(self, organization_id: str | None) -> str:
        """Bind ERP agent access to its explicitly configured Discord tenant."""

        tenant_id = _required_organization_id(organization_id)
        configured_tenant_id = _optional_str(
            self.runtime_config.agent_erp_organization_id
        )
        if configured_tenant_id is None:
            raise PermissionError(
                "ERP agent access is not configured for a Discord organization"
            )
        if tenant_id != configured_tenant_id:
            raise PermissionError(
                "ERP agent access is not configured for this organization"
            )
        return tenant_id

    def _search_erp_invoices(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        _validate_erp_read_arguments("billing_read.search_invoices", arguments)
        self._require_erp_tenant(organization_id)
        invoice_type = _erp_invoice_type(arguments["invoice_type"])
        query = _erp_search_query(arguments["query"], field_name="Invoice query")
        limit = _erp_result_limit(arguments.get("limit"), default=5)
        client = self._erpnext_client(deadline_monotonic=deadline_monotonic)
        try:
            try:
                rows = client.search_invoices(
                    _ERP_INVOICE_DOCTYPES[invoice_type],
                    query=query,
                    # Keep one extra row only to distinguish an exact count
                    # from a truncated result set in scheduled reports.
                    limit=limit + 1,
                )
            except ERPNextAPIError:
                logger.warning("ERP invoice search failed", exc_info=True)
                raise RuntimeError("ERP lookup is temporarily unavailable") from None
        finally:
            client.close()
        has_more = len(rows) > limit
        result: dict[str, Any] = {
            "invoice_type": invoice_type,
            "invoices": [_erp_invoice_search_payload(row) for row in rows[:limit]],
        }
        if has_more:
            result["has_more"] = True
        return result

    def _get_erp_invoice_summary(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        _validate_erp_read_arguments("billing_read.get_invoice_summary", arguments)
        self._require_erp_tenant(organization_id)
        invoice_type = _erp_invoice_type(arguments["invoice_type"])
        invoice_id = _erp_required_text(
            arguments["invoice_id"],
            field_name="Invoice id",
            maximum=140,
        )
        client = self._erpnext_client(deadline_monotonic=deadline_monotonic)
        try:
            try:
                row = client.get_invoice(
                    _ERP_INVOICE_DOCTYPES[invoice_type],
                    invoice_id,
                )
            except ERPNextAPIError:
                logger.warning("ERP invoice summary lookup failed", exc_info=True)
                raise RuntimeError("ERP lookup is temporarily unavailable") from None
        finally:
            client.close()
        return {
            "invoice": (
                _erp_invoice_summary_payload(row, invoice_type=invoice_type)
                if row is not None
                else None
            )
        }

    def _search_erp_suppliers(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        _validate_erp_read_arguments("billing_read.search_suppliers", arguments)
        self._require_erp_tenant(organization_id)
        query = _erp_search_query(arguments["query"], field_name="Supplier query")
        limit = _erp_result_limit(arguments.get("limit"), default=5)
        client = self._erpnext_client(deadline_monotonic=deadline_monotonic)
        try:
            try:
                rows = client.search_suppliers(query, limit=limit + 1)
            except ERPNextAPIError:
                logger.warning("ERP supplier search failed", exc_info=True)
                raise RuntimeError("ERP lookup is temporarily unavailable") from None
        finally:
            client.close()
        has_more = len(rows) > limit
        result: dict[str, Any] = {
            "suppliers": [_erp_supplier_payload(row) for row in rows[:limit]],
        }
        if has_more:
            result["has_more"] = True
        return result

    def _search_erp_projects(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        _validate_erp_read_arguments("erp_read.search_projects", arguments)
        self._require_erp_tenant(organization_id)
        query = _erp_search_query(
            arguments["query"],
            field_name="ERP project query",
        )
        limit = _erp_result_limit(arguments.get("limit"), default=5)
        client = self._erpnext_client(deadline_monotonic=deadline_monotonic)
        try:
            try:
                rows = client.list_records(
                    "Project",
                    fields=_ERP_PROJECT_FIELDS,
                    or_filters=[
                        ["Project", "name", "like", f"%{query}%"],
                        ["Project", "project_name", "like", f"%{query}%"],
                    ],
                    limit=limit + 1,
                )
            except ERPNextAPIError:
                logger.warning("ERP project search failed", exc_info=True)
                raise RuntimeError("ERP lookup is temporarily unavailable") from None
        finally:
            client.close()
        has_more = len(rows) > limit
        result: dict[str, Any] = {
            "projects": [_erp_project_summary_payload(row) for row in rows[:limit]],
        }
        if has_more:
            result["has_more"] = True
        return result

    def _get_erp_project_summary(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        _validate_erp_read_arguments("erp_read.get_project_summary", arguments)
        self._require_erp_tenant(organization_id)
        project_id = _erp_required_text(
            arguments["project_id"],
            field_name="ERP project id",
            maximum=140,
        )
        client = self._erpnext_client(deadline_monotonic=deadline_monotonic)
        try:
            try:
                row = client.get_project(project_id)
            except ERPNextAPIError as exc:
                if exc.status_code == 404:
                    row = None
                else:
                    logger.warning("ERP project summary lookup failed", exc_info=True)
                    raise RuntimeError(
                        "ERP lookup is temporarily unavailable"
                    ) from None
        finally:
            client.close()
        return {
            "project": _erp_project_summary_payload(row) if row is not None else None
        }

    def _get_onboarding_summary(
        self,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """Return a non-identifying summary of the local onboarding queue.

        This intentionally is not a generic database tool: schedules can ask
        about onboarding health, but they cannot enumerate people, emails, or
        intake payloads into a Discord channel or model prompt.
        """

        if arguments:
            raise ValueError("Onboarding summary does not accept arguments")
        _required_organization_id(organization_id)
        postgres_url = _required_config(
            self.runtime_config.postgres_url,
            "POSTGRES_URL",
        )
        query = """
            SELECT
                COALESCE(
                    NULLIF(
                        replace(
                            replace(lower(btrim(onboarding_state)), '_', ''),
                            '-',
                            ''
                        ),
                        ''
                    ),
                    'notstarted'
                ) AS state,
                count(*)::int AS count,
                count(*) FILTER (
                    WHERE onboarding_updated_at < NOW() - INTERVAL '14 days'
                )::int AS stale_count
            FROM people
            WHERE COALESCE(is_member, false) = false
              AND (sync_status = 'active' OR sync_status IS NULL)
              AND (
                    contact_type ILIKE '%prospect%'
                    OR onboarding_state IS NOT NULL
                  )
            GROUP BY 1
            ORDER BY 1
        """
        connect_timeout_seconds = 5
        statement_timeout_milliseconds = 10_000
        if deadline_monotonic is not None:
            remaining_connect_timeout = clamp_timeout_seconds(
                float(connect_timeout_seconds),
                deadline_monotonic=deadline_monotonic,
            )
            # libpq accepts whole seconds for connect_timeout. Do not round up
            # and start a connection that can outlive the schedule deadline.
            connect_timeout_seconds = int(remaining_connect_timeout)
            if connect_timeout_seconds < 1:
                raise DeadlineExceeded(
                    "Onboarding database connection deadline exceeded"
                )
            statement_timeout_milliseconds = max(
                1,
                int(
                    clamp_timeout_seconds(
                        statement_timeout_milliseconds / 1_000,
                        deadline_monotonic=deadline_monotonic,
                    )
                    * 1_000
                ),
            )
        try:
            with connect(
                postgres_url,
                connect_timeout=connect_timeout_seconds,
                options=f"-c statement_timeout={statement_timeout_milliseconds}",
            ) as conn:
                with conn.cursor(row_factory=dict_row) as cursor:
                    if deadline_monotonic is not None:
                        # The connect phase can consume part of the budget, so
                        # re-clamp the statement timeout immediately before the
                        # database query begins.
                        remaining_statement_timeout_milliseconds = max(
                            1,
                            int(
                                clamp_timeout_seconds(
                                    statement_timeout_milliseconds / 1_000,
                                    deadline_monotonic=deadline_monotonic,
                                )
                                * 1_000
                            ),
                        )
                        cursor.execute(
                            "SELECT set_config('statement_timeout', %s, true)",
                            (str(remaining_statement_timeout_milliseconds),),
                        )
                    cursor.execute(query)
                    rows = cursor.fetchall()
        except DeadlineExceeded:
            raise
        except Exception:
            logger.warning("Onboarding summary lookup failed", exc_info=True)
            raise RuntimeError(
                "Onboarding summary is temporarily unavailable"
            ) from None

        by_state: dict[str, int] = {}
        stale_count = 0
        for row in rows:
            state = _onboarding_state_label(row.get("state"))
            count = _nonnegative_int(row.get("count"))
            by_state[state] = count
            stale_count += _nonnegative_int(row.get("stale_count"))
        return {
            "total": sum(by_state.values()),
            "by_state": by_state,
            "stale_count": stale_count,
        }

    def _web_research_client(self) -> WebResearchClient:
        if self._web_client is not None:
            return self._web_client
        config = self.runtime_config
        if self._web_client_factory is not None:
            return self._web_client_factory(config)
        return _web_research_client_from_config(config)

    def _search_public_web(
        self,
        arguments: dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        _validate_public_web_query(query)
        limit = _positive_int(
            arguments.get("limit"),
            default=_positive_int(
                self.runtime_config.agent_web_default_result_limit,
                default=5,
                maximum=10,
            ),
            maximum=10,
        )
        client = self._web_research_client()
        if deadline_monotonic is not None and isinstance(client, WebResearchClient):
            response = client.search(
                query,
                limit=limit,
                deadline_monotonic=deadline_monotonic,
            )
        else:
            response = client.search(query, limit=limit)
        return {
            "provider": response.provider,
            "results": [
                {
                    "provider": item.provider,
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                    "published_at": item.published_at,
                    "source": item.source,
                }
                for item in response.results
            ],
        }

    def _extract_public_web(
        self,
        arguments: dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ValueError("Public web URL is required")
        if contains_private_agent_identifier(url):
            raise PermissionError(
                "Public web extraction URLs cannot contain internal record identifiers"
            )
        try:
            url = validate_public_https_url(url)
        except WebResearchValidationError as exc:
            raise ValueError(str(exc)) from exc
        client = self._web_research_client()
        if deadline_monotonic is not None and isinstance(client, WebResearchClient):
            result = client.extract(url, deadline_monotonic=deadline_monotonic)
        else:
            result = client.extract(url)
        return {
            "provider": result.provider,
            "url": result.url,
            "title": result.title,
            "content": result.content,
            "metadata": result.metadata,
        }

    def _get_user_memory_facts(
        self,
        arguments: dict[str, Any],
        *,
        actor_id: str | None,
        organization_id: str | None,
        actor_scopes: set[str],
    ) -> dict[str, Any]:
        user_id = _optional_str(arguments.get("user_id")) or actor_id
        if user_id is None:
            raise ValueError("user_id is required")
        if user_id != actor_id and "memory:admin" not in actor_scopes:
            raise PermissionError("Cannot read another user's private memory")
        tenant_id = _required_organization_id(organization_id)
        facts = self.memory_store.list_facts(
            organization_id=tenant_id,
            scope_type="user",
            scope_id=user_id,
            visible_to_user_id=user_id,
            visible_to_project_id=None,
            visible_to_org_id=tenant_id,
        )
        return {"facts": [_memory_fact_payload(fact) for fact in facts]}

    def _get_project_memory_facts(
        self,
        arguments: dict[str, Any],
        *,
        actor_id: str | None,
        organization_id: str | None,
        project_id: str | None,
        actor_scopes: set[str],
    ) -> dict[str, Any]:
        requested_project_id = _optional_str(arguments.get("project_id"))
        project_id = _trusted_project_scope_id(
            requested_project_id,
            project_id=project_id,
        )
        tenant_id = _required_organization_id(organization_id)
        facts = self.memory_store.list_facts(
            organization_id=tenant_id,
            scope_type="project",
            scope_id=project_id,
            visible_to_user_id=actor_id or "",
            visible_to_project_id=project_id,
            visible_to_org_id=tenant_id,
        )
        return {"facts": [_memory_fact_payload(fact) for fact in facts]}

    def _remember_memory_fact(
        self,
        arguments: dict[str, Any],
        *,
        actor_id: str | None,
        organization_id: str | None,
        project_id: str | None,
        actor_scopes: set[str],
    ) -> dict[str, Any]:
        if actor_id is None:
            raise ValueError("actor_id is required")
        tenant_id = _required_organization_id(organization_id)
        scope_type = _memory_scope_type(arguments.get("scope_type"), default="user")
        scope_id = _memory_scope_id(
            arguments.get("scope_id"),
            scope_type=scope_type,
            actor_id=actor_id,
            organization_id=tenant_id,
            project_id=project_id,
            actor_scopes=actor_scopes,
        )
        if scope_type == "project" and "memory:write_project" not in actor_scopes:
            raise PermissionError("Project memory writes require project memory scope")
        if scope_type == "org" and "memory:admin" not in actor_scopes:
            raise PermissionError("Org memory writes require memory admin scope")
        if scope_type == "org":
            raise ValueError(
                "Org memory writes are disabled until scoped org memory reads are available"
            )
        key = validate_memory_fact_key(arguments.get("key"))
        value = arguments.get("value_json")
        if not isinstance(value, dict) or not value:
            raise ValueError("Memory value_json object is required")
        validate_memory_value_for_persistence(value)
        visibility = _memory_visibility_for_scope(
            arguments.get("visibility"),
            scope_type=scope_type,
        )
        fact = self.memory_store.remember_fact(
            organization_id=tenant_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key,
            value_json=value,
            visibility=visibility,
            source_type=str(arguments.get("source_type") or "request"),
            source_ref=str(arguments.get("source_ref") or "agent_request"),
            source_excerpt=_optional_str(arguments.get("source_excerpt")),
            created_by=actor_id,
            verification_status=str(
                arguments.get("verification_status") or "user_confirmed"
            ),
            confidence=float(arguments.get("confidence") or 1.0),
        )
        return {"fact": _memory_fact_payload(fact)}

    def _forget_memory_fact(
        self,
        arguments: dict[str, Any],
        *,
        actor_id: str | None,
        organization_id: str | None,
        actor_scopes: set[str],
    ) -> dict[str, Any]:
        if actor_id is None:
            raise ValueError("actor_id is required")
        fact_id = str(arguments.get("fact_id") or "").strip()
        if not fact_id:
            raise ValueError("fact_id is required")
        tenant_id = _required_organization_id(organization_id)
        fact = self.memory_store.forget_fact(
            organization_id=tenant_id,
            fact_id=fact_id,
            actor_id=actor_id,
            actor_is_admin="memory:admin" in actor_scopes,
        )
        return {"fact": _memory_fact_payload(fact)}

    def _github_client(
        self,
        *,
        deadline_monotonic: float | None = None,
    ) -> GitHubClient:
        config = self.runtime_config
        app_values = (
            _optional_str(config.github_app_client_id),
            _optional_str(config.github_app_installation_id),
            _optional_str(config.github_app_private_key),
        )
        if any(app_values):
            if not all(app_values):
                raise RuntimeError(
                    "GITHUB_APP_CLIENT_ID, GITHUB_APP_INSTALLATION_ID, and "
                    "GITHUB_APP_PRIVATE_KEY must be configured together"
                )
            client_id, installation_id, private_key = app_values
            assert client_id is not None
            assert installation_id is not None
            assert private_key is not None
            provider_config = (client_id, installation_id, private_key)
            if (
                self._github_app_provider is None
                or self._github_app_provider_config != provider_config
            ):
                self._github_app_provider = GitHubAppTokenProvider(
                    client_id=client_id,
                    installation_id=installation_id,
                    private_key=private_key.replace("\\n", "\n"),
                )
                self._github_app_provider_config = provider_config
                self._github_installation_repository_cache = None
            return GitHubClient(
                token_provider=self._github_app_provider,
                deadline_monotonic=deadline_monotonic,
            )

        token = _required_config(config.github_api_token, "GITHUB_API_TOKEN")
        return GitHubClient(
            token=token,
            deadline_monotonic=deadline_monotonic,
        )

    def _resolve_repository(
        self,
        arguments: dict[str, Any],
        *,
        actor_scopes: set[str],
        deadline_monotonic: float | None = None,
    ) -> str:
        repository = _optional_str(arguments.get("repository"))
        if repository is None:
            repository = _optional_str(self.runtime_config.github_default_repo)
        if repository is None:
            raise ValueError("GitHub repository is required")
        normalized_repository = repository.strip().strip("/")
        repository_parts = normalized_repository.split("/")
        if len(repository_parts) != 2 or not all(repository_parts):
            raise ValueError("GitHub repository must be in owner/name form")

        config = self.runtime_config
        normalized_key = normalized_repository.casefold()
        member_repositories = _github_repository_set(
            config.github_default_repo,
            config.github_member_extra_repos,
        )
        legacy_repositories = _github_repository_set(
            config.github_default_repo,
            config.github_allowed_repos,
        )
        steering_repositories = _github_repository_set(
            config.github_default_repo,
            config.github_steering_extra_repos,
        )
        github_app_is_set = self._github_app_is_set(config)

        if {
            "github:repository:member:read",
            "github:repository:member:write",
        } & actor_scopes:
            if normalized_key in member_repositories:
                return normalized_repository

        if (
            not github_app_is_set
            and {
                "github:repository:configured:read",
                "github:repository:configured:write",
                # Legacy scopes are retained while installations transition from an
                # API token to the GitHub App.
                "github:issue:read",
                "github:issue:create",
            }
            & actor_scopes
        ):
            if normalized_key in legacy_repositories:
                return normalized_repository

        if {
            "github:repository:all:read",
            "github:repository:all:write",
        } & actor_scopes:
            if config.github_steering_all_installed_repos and github_app_is_set:
                if normalized_key in self._installed_github_repository_names(
                    config,
                    deadline_monotonic=deadline_monotonic,
                ):
                    return normalized_repository
                raise ValueError(
                    "GitHub repository is not selected for this GitHub App installation"
                )
            if normalized_key in steering_repositories:
                return normalized_repository

        if (
            not github_app_is_set
            and {
                "github:repository:configured:read",
                "github:repository:configured:write",
                "github:issue:read",
                "github:issue:create",
            }
            & actor_scopes
        ):
            raise ValueError(
                "GitHub repository is not allowed by GITHUB_DEFAULT_REPO "
                "or GITHUB_ALLOWED_REPOS"
            )
        raise PermissionError(
            "GitHub repository is not allowed for this Discord role and configuration"
        )

    def _resolve_github_organization(self, arguments: dict[str, Any]) -> str:
        configured_organization = _required_config(
            self.runtime_config.github_organization,
            "GITHUB_ORGANIZATION",
        )
        requested_organization = _optional_str(arguments.get("organization"))
        organization = requested_organization or configured_organization
        normalized = organization.strip().strip("/")
        if not re.fullmatch(r"[A-Za-z0-9-]+", normalized):
            raise ValueError("GitHub organization must be a valid organization name")
        if normalized.casefold() != configured_organization.strip().casefold():
            raise ValueError(
                "GitHub organization is not allowed by GITHUB_ORGANIZATION"
            )
        return normalized

    @staticmethod
    def _github_app_is_set(config: ToolRuntimeConfig) -> bool:
        return all(
            _optional_str(value) is not None
            for value in (
                config.github_app_client_id,
                config.github_app_installation_id,
                config.github_app_private_key,
            )
        )

    def _installed_github_repository_names(
        self,
        config: ToolRuntimeConfig,
        *,
        deadline_monotonic: float | None = None,
    ) -> frozenset[str]:
        """Return GitHub App-selected repositories with a short local cache."""

        client_id = _required_config(
            config.github_app_client_id,
            "GITHUB_APP_CLIENT_ID",
        )
        installation_id = _required_config(
            config.github_app_installation_id,
            "GITHUB_APP_INSTALLATION_ID",
        )
        cache_key = (client_id, installation_id)
        now = datetime.now(timezone.utc)
        cached = self._github_installation_repository_cache
        if (
            cached is not None
            and cached[1] == cache_key
            and cached[0] > now - timedelta(minutes=5)
        ):
            return cached[2]

        payload = self._github_client(
            deadline_monotonic=deadline_monotonic
        ).list_installation_repositories()
        names = frozenset(
            full_name.casefold()
            for repository in payload.get("repositories", [])
            if isinstance(repository, dict)
            and (full_name := _optional_str(repository.get("full_name"))) is not None
        )
        self._github_installation_repository_cache = (
            now,
            cache_key,
            names,
        )
        return names

    def _crm_repository(
        self,
        *,
        deadline_monotonic: float | None = None,
    ) -> EspoContactRepository:
        return EspoContactRepository(
            self._espo_client(deadline_monotonic=deadline_monotonic)
        )

    def _espo_client(
        self,
        *,
        deadline_monotonic: float | None = None,
    ) -> EspoClient:
        base_url = _required_config(self.runtime_config.espo_base_url, "ESPO_BASE_URL")
        api_key = _required_config(self.runtime_config.espo_api_key, "ESPO_API_KEY")
        return EspoClient(
            base_url,
            api_key,
            deadline_monotonic=deadline_monotonic,
        )

    def _search_crm_contacts(
        self,
        arguments: dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("CRM contact search query is required")
        limit = _positive_int(arguments.get("limit"), default=5, maximum=10)
        contacts = self._crm_repository(deadline_monotonic=deadline_monotonic).search(
            # The extra row is not returned to the caller; it only preserves
            # whether a capped aggregate is an exact count.
            limit=limit + 1,
            select=[
                "id",
                "name",
                "emailAddress",
                "phoneNumber",
                "cRoles",
                "cOnboardingState",
            ],
            name__contains=query,
        )
        has_more = len(contacts) > limit
        result: dict[str, Any] = {
            "contacts": [contact.to_dict() for contact in contacts[:limit]],
        }
        if has_more:
            result["has_more"] = True
        return result

    def _update_crm_contact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        contact_id = str(arguments.get("contact_id") or "").strip()
        updates = arguments.get("updates")
        if not contact_id:
            raise ValueError("CRM contact id is required")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("CRM contact updates are required")
        repository = self._crm_repository()
        contact = repository.get(contact_id)
        contact.set(**updates)
        return {"contact": contact.save().to_dict()}

    def _create_docuseal_member_agreement(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        base_url = _required_config(
            self.runtime_config.docuseal_base_url,
            "DOCUSEAL_BASE_URL",
        )
        api_key = _required_config(
            self.runtime_config.docuseal_api_key,
            "DOCUSEAL_API_KEY",
        )
        template_id = self.runtime_config.docuseal_member_agreement_template_id
        if template_id is None:
            raise ValueError("DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID is required")
        email = str(arguments.get("submitter_email") or "").strip()
        if not email:
            raise ValueError("Submitter email is required")
        return create_member_agreement_submission(
            base_url=base_url,
            api_key=api_key,
            template_id=template_id,
            submitter_name=_optional_str(arguments.get("submitter_name")),
            submitter_email=email,
            send_email=bool(arguments.get("send_email", True)),
        )

    def _authentik_client(self) -> AuthentikClient:
        return AuthentikClient(
            base_url=_required_config(
                self.runtime_config.authentik_api_base_url,
                "AUTHENTIK_API_BASE_URL",
            ),
            api_token=_required_config(
                self.runtime_config.authentik_api_token,
                "AUTHENTIK_API_TOKEN",
            ),
            timeout_seconds=max(
                1.0,
                float(self.runtime_config.authentik_api_timeout_seconds),
            ),
        )

    def _outline_client(self) -> OutlineClient:
        return OutlineClient(
            api_key=_required_config(
                self.runtime_config.outline_admin_api_key,
                "OUTLINE_ADMIN_API_KEY",
            ),
            base_url=self.runtime_config.outline_base_url
            or "https://app.getoutline.com",
            timeout_seconds=max(
                1.0, float(self.runtime_config.outline_api_timeout_seconds)
            ),
        )

    def _resolve_account_contact(
        self,
        arguments: dict[str, Any],
        *,
        select: str = f"id,name,emailAddress,c508Email,{SSO_ID_FIELD}",
    ) -> dict[str, Any]:
        client = self._espo_client()
        contact_id = _optional_str(arguments.get("contact_id"))
        if contact_id is not None:
            return client.get_contact(contact_id)

        query = _optional_str(arguments.get("contact_query")) or _optional_str(
            arguments.get("search_term")
        )
        if query is None:
            raise ValueError("CRM contact_id or contact_query is required")

        contacts = self._search_contacts_for_lookup(
            client,
            query,
            max_size=2,
            select=select,
        )
        if not contacts:
            raise ValueError(f"No CRM contact found for: {query}")
        if len(contacts) > 1:
            raise ValueError(
                "Multiple CRM contacts matched. Use a CRM contact ID to disambiguate."
            )
        return contacts[0]

    def _search_contacts_for_lookup(
        self,
        client: EspoClient,
        search_term: str,
        *,
        max_size: int,
        select: str,
    ) -> list[dict[str, Any]]:
        normalized = search_term.strip()
        if not normalized:
            return []
        if _looks_like_crm_contact_id(normalized):
            try:
                contact = client.get_contact(normalized)
            except EspoAPIError:
                contact = {}
            if contact.get("id"):
                return [contact]

        filters = self._contact_search_filters(normalized)
        if not filters:
            return []
        response = client.list_contacts(
            {
                "where": [{"type": "or", "value": filters}],
                "maxSize": max_size,
                "select": select,
            }
        )
        contacts = response.get("list")
        if not isinstance(contacts, list):
            return []
        deduped: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for contact in contacts:
            if not isinstance(contact, dict):
                continue
            contact_id = str(contact.get("id") or "")
            if contact_id in seen_ids:
                continue
            seen_ids.add(contact_id)
            deduped.append(contact)
        return deduped

    def _contact_search_filters(self, search_term: str) -> list[dict[str, Any]]:
        normalized = search_term.strip()
        if not normalized:
            return []
        if "@" in normalized:
            email_query = normalized.lower()
            local_part, _, domain = email_query.partition("@")
            configured_domain = normalize_migadu_mailbox_domain(
                self.runtime_config.migadu_mailbox_domain
            )
            domain_aliases = {configured_domain}
            if "." in configured_domain:
                domain_aliases.add(configured_domain.split(".", 1)[0])
            email = (
                f"{local_part}@{configured_domain}"
                if local_part and domain.casefold() in domain_aliases
                else email_query
            )
            return [
                {"type": "equals", "attribute": "emailAddress", "value": email},
                {"type": "equals", "attribute": "c508Email", "value": email},
            ]

        filters: list[dict[str, Any]] = [
            {"type": "contains", "attribute": "name", "value": normalized}
        ]
        name_parts = [part for part in re.split(r"\s+", normalized) if part]
        if len(name_parts) >= 2:
            filters.append(
                {
                    "type": "and",
                    "value": [
                        {
                            "type": "contains",
                            "attribute": "firstName",
                            "value": name_parts[0],
                        },
                        {
                            "type": "contains",
                            "attribute": "lastName",
                            "value": name_parts[-1],
                        },
                    ],
                }
            )
        if " " not in normalized:
            filters.append(
                {
                    "type": "equals",
                    "attribute": "c508Email",
                    "value": (
                        f"{normalized}@"
                        f"{normalize_migadu_mailbox_domain(self.runtime_config.migadu_mailbox_domain)}"
                    ),
                }
            )
        return filters

    def _create_sso_user(self, arguments: dict[str, Any]) -> dict[str, Any]:
        contact = self._resolve_account_contact(arguments)
        return self._create_sso_user_for_contact(contact)

    def _create_sso_user_for_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        contact_id = _required_text(
            contact.get("id"), "Selected contact is missing a CRM ID"
        )
        contact_name = _optional_str(contact.get("name")) or "Unknown"
        username, email = self._resolve_sso_identity_for_contact(contact)
        client = self._authentik_client()

        user_id: int
        created = False
        crm_updated = False
        recovered_existing_after_create_error = False
        recovery_email_error: str | None = None
        linked_sso_id = self._crm_sso_id(contact)

        if linked_sso_id is not None:
            user = client.get_user(linked_sso_id)
            user_id = self._validate_authentik_user_for_contact(
                user,
                expected_username=username,
                expected_email=email,
            )
        else:
            matches = client.find_users_by_username_or_email(
                username=username,
                email=email,
            )
            if len(matches) > 1:
                raise ValueError(
                    "Multiple Authentik users matched this CRM contact. "
                    "Resolve the duplicate manually before linking."
                )
            if matches:
                user_id = self._validate_authentik_user_for_contact(
                    matches[0],
                    expected_username=username,
                    expected_email=email,
                )
            else:
                (
                    recovery_email_stage_id,
                    recovery_email_error,
                ) = self._resolve_recovery_email_stage_id(client)
                try:
                    user = client.create_user(
                        username=username,
                        name=contact_name,
                        email=email,
                    )
                    created = True
                except AuthentikAPIError as exc:
                    reconciled_matches = client.find_users_by_username_or_email(
                        username=username,
                        email=email,
                    )
                    if len(reconciled_matches) > 1:
                        raise ValueError(
                            "Multiple Authentik users matched this CRM contact after "
                            "the create attempt. Resolve the duplicate manually "
                            "before linking."
                        ) from exc
                    if not reconciled_matches:
                        raise
                    user = reconciled_matches[0]
                    recovered_existing_after_create_error = True
                try:
                    user_id = self._validate_authentik_user_for_contact(
                        user,
                        expected_username=username,
                        expected_email=email,
                    )
                except ValueError as exc:
                    if created or recovered_existing_after_create_error:
                        result = self._sso_result(
                            contact_id=contact_id,
                            contact_name=contact_name,
                            username=username,
                            email=email,
                            user_id=_maybe_authentik_user_pk(user),
                            created=created,
                            crm_updated=False,
                            recovery_email_error=None,
                            recovered_existing_after_create_error=(
                                recovered_existing_after_create_error
                            ),
                            partial_success=(
                                "sso_created_validation_failed"
                                if created
                                else "sso_reconciled_validation_failed"
                            ),
                            error=_short_error(exc),
                        )
                        raise ToolPartialSuccessError(
                            "SSO user is ready, but validation failed.",
                            result,
                        ) from exc
                    raise
                if created and recovery_email_stage_id is not None:
                    try:
                        client.send_recovery_email(
                            user_id=user_id,
                            email_stage=recovery_email_stage_id,
                        )
                    except AuthentikAPIError as exc:
                        recovery_email_error = _short_error(exc)

            try:
                self._espo_client().update_contact(
                    contact_id, {SSO_ID_FIELD: str(user_id)}
                )
            except EspoAPIError as exc:
                result = self._sso_result(
                    contact_id=contact_id,
                    contact_name=contact_name,
                    username=username,
                    email=email,
                    user_id=user_id,
                    created=created,
                    crm_updated=False,
                    recovery_email_error=recovery_email_error,
                    recovered_existing_after_create_error=(
                        recovered_existing_after_create_error
                    ),
                    partial_success="sso_user_ready_crm_update_failed",
                    error=_short_error(exc),
                )
                raise ToolPartialSuccessError(
                    "SSO user is ready, but updating CRM cSsoID failed.",
                    result,
                ) from exc
            crm_updated = True

        return self._sso_result(
            contact_id=contact_id,
            contact_name=contact_name,
            username=username,
            email=email,
            user_id=user_id,
            created=created,
            crm_updated=crm_updated,
            recovery_email_error=recovery_email_error,
            recovered_existing_after_create_error=recovered_existing_after_create_error,
        )

    @staticmethod
    def _sso_result(
        *,
        contact_id: str,
        contact_name: str,
        username: str,
        email: str,
        user_id: int | None,
        created: bool,
        crm_updated: bool,
        recovery_email_error: str | None,
        recovered_existing_after_create_error: bool = False,
        partial_success: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "username": username,
            "email": email,
            "user_id": user_id,
            "created": created,
            "crm_updated": crm_updated,
            "recovered_existing_after_create_error": recovered_existing_after_create_error,
            "recovery_email_error": recovery_email_error,
        }
        if partial_success is not None:
            result["partial_success"] = partial_success
        if error is not None:
            result["error"] = error
        return result

    def _resolve_sso_identity_for_contact(
        self,
        contact: dict[str, Any],
    ) -> tuple[str, str]:
        email = self._contact_508_email(contact)
        if email is None:
            configured_domain = normalize_migadu_mailbox_domain(
                self.runtime_config.migadu_mailbox_domain
            )
            raise ValueError(
                f"Selected contact does not have a @{configured_domain} email in CRM."
            )
        username = email.split("@", 1)[0].strip().lower()
        if not username:
            raise ValueError("Unable to derive a 508 username from the CRM email.")
        return username, email

    def _contact_508_email(self, contact: dict[str, Any]) -> str | None:
        for field_name in ("c508Email", "emailAddress"):
            email = self._normalize_508_email(contact.get(field_name))
            if email is not None:
                return email
        return None

    def _normalize_508_email(self, value: Any) -> str | None:
        configured_domain = normalize_migadu_mailbox_domain(
            self.runtime_config.migadu_mailbox_domain
        )
        try:
            email = _normalize_full_email(value, field_label="508 email")
        except ValueError:
            return None
        _local_part, _sep, domain = email.partition("@")
        if domain != configured_domain:
            return None
        return email

    def _crm_sso_id(self, contact: dict[str, Any]) -> int | None:
        raw_value = _optional_str(contact.get(SSO_ID_FIELD))
        if raw_value is None:
            return None
        if raw_value.isdigit():
            return int(raw_value)
        raise ValueError(f"{SSO_ID_FIELD} must be a numeric Authentik user id.")

    def _validate_authentik_user_for_contact(
        self,
        user: dict[str, Any],
        *,
        expected_username: str,
        expected_email: str,
    ) -> int:
        if bool(user.get("is_superuser")):
            raise ValueError("Refusing to use an Authentik superuser for this command.")
        actual_username = _optional_str(user.get("username"))
        if actual_username != expected_username:
            raise ValueError(
                "Matched Authentik username does not match the CRM-derived 508 username."
            )
        actual_email = self._normalize_508_email(user.get("email"))
        if actual_email != expected_email:
            configured_domain = normalize_migadu_mailbox_domain(
                self.runtime_config.migadu_mailbox_domain
            )
            raise ValueError(
                "Matched Authentik email does not match the CRM-derived "
                f"@{configured_domain} email."
            )
        return _authentik_user_pk(user)

    def _resolve_recovery_email_stage_id(
        self, client: AuthentikClient
    ) -> tuple[str | None, str | None]:
        try:
            stage_id = client.resolve_email_stage_id(
                stage_id=_optional_str(
                    self.runtime_config.authentik_recovery_email_stage_id
                ),
                stage_name=(
                    _optional_str(
                        self.runtime_config.authentik_recovery_email_stage_name
                    )
                    or "default-recovery-email"
                ),
            )
        except AuthentikAPIError as exc:
            return None, _short_error(exc)
        return stage_id, None

    def _invite_outline_user(self, arguments: dict[str, Any]) -> dict[str, Any]:
        direct_email = _optional_str(arguments.get("email"))
        if direct_email is not None:
            email = _normalize_full_email(
                direct_email, field_label="Outline invite email"
            )
            name = _optional_str(arguments.get("name")) or email.partition("@")[0]
            self._outline_client().invite_user(email=email, name=name, role="member")
            return {"email": email, "name": name, "direct_email": True}

        contact = self._resolve_account_contact(
            arguments,
            select="id,name,emailAddress,c508Email",
        )
        return self._invite_outline_user_for_contact(contact)

    def _invite_outline_user_for_contact(
        self, contact: dict[str, Any]
    ) -> dict[str, Any]:
        contact_id = _required_text(
            contact.get("id"), "Selected contact is missing a CRM ID"
        )
        contact_name = _optional_str(contact.get("name")) or "Unknown"
        email = self._contact_508_email(contact) or _optional_str(
            contact.get("emailAddress")
        )
        normalized_email = _normalize_full_email(
            email,
            field_label="Outline invite email",
        )
        self._outline_client().invite_user(
            email=normalized_email,
            name=contact_name,
            role="member",
        )
        return {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "email": normalized_email,
            "direct_email": False,
        }

    def _create_user_accounts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        contact = self._resolve_account_contact(arguments)
        mailbox_username = _required_text(
            arguments.get("mailbox_username"),
            "mailbox_username is required",
        )
        self._preflight_user_accounts(
            contact=contact, mailbox_username=mailbox_username
        )
        contact_id = _required_text(
            contact.get("id"),
            "Selected contact is missing a CRM ID",
        )
        contact_name = _optional_str(contact.get("name")) or "Unknown"
        mailbox: dict[str, Any] | None = None
        sso: dict[str, Any] | None = None
        outline: dict[str, Any] | None = None

        try:
            mailbox = self._create_migadu_mailbox_for_contact(
                contact=contact,
                mailbox_username=mailbox_username,
            )
            sso = self._create_sso_user_for_contact(contact)
            outline = self._invite_outline_user_for_contact(contact)
        except ToolPartialSuccessError as exc:
            partial_result = exc.result
            if mailbox is None and _is_mailbox_result(partial_result):
                mailbox = partial_result
            elif sso is None and _is_sso_result(partial_result):
                sso = partial_result
            raise ToolPartialSuccessError(
                "User account provisioning partially completed.",
                self._user_accounts_result(
                    contact_id=contact_id,
                    contact_name=contact_name,
                    email=self._user_accounts_email(mailbox=mailbox, contact=contact),
                    mailbox=mailbox,
                    sso=sso,
                    outline=outline,
                    partial_success="user_accounts_partial",
                    error=str(exc),
                ),
            ) from exc
        except Exception as exc:
            if mailbox is None and sso is None and outline is None:
                raise
            raise ToolPartialSuccessError(
                "User account provisioning partially completed.",
                self._user_accounts_result(
                    contact_id=contact_id,
                    contact_name=contact_name,
                    email=self._user_accounts_email(mailbox=mailbox, contact=contact),
                    mailbox=mailbox,
                    sso=sso,
                    outline=outline,
                    partial_success="user_accounts_partial",
                    error=_short_error(exc),
                ),
            ) from exc

        return self._user_accounts_result(
            contact_id=contact_id,
            contact_name=contact_name,
            email=self._user_accounts_email(mailbox=mailbox, contact=contact),
            mailbox=mailbox,
            sso=sso,
            outline=outline,
        )

    def _preflight_user_accounts(
        self,
        *,
        contact: dict[str, Any],
        mailbox_username: str,
    ) -> None:
        authentik_client = self._authentik_client()
        self._outline_client()
        if self._crm_sso_id(contact) is None:
            self._resolve_recovery_email_stage_id(authentik_client)
        target_email, _local_part = self._normalize_mailbox_username(mailbox_username)
        existing_email = self._normalize_508_email(contact.get("c508Email"))
        if existing_email is None:
            self._migadu_client()
        elif existing_email != target_email:
            raise ValueError(
                f"CRM contact already has a different 508 email: {existing_email}."
            )

    @staticmethod
    def _user_accounts_result(
        *,
        contact_id: str,
        contact_name: str,
        email: str | None,
        mailbox: dict[str, Any] | None,
        sso: dict[str, Any] | None,
        outline: dict[str, Any] | None,
        partial_success: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "email": email,
            "mailbox": mailbox,
            "sso": sso,
            "outline": outline,
        }
        if partial_success is not None:
            result["partial_success"] = partial_success
        if error is not None:
            result["error"] = error
        return result

    def _user_accounts_email(
        self,
        *,
        mailbox: dict[str, Any] | None,
        contact: dict[str, Any],
    ) -> str | None:
        if mailbox is not None:
            email = _optional_str(mailbox.get("email"))
            if email is not None:
                return email
        return self._contact_508_email(contact)

    def _migadu_client(self) -> MigaduClient:
        username = _required_config(
            self.runtime_config.migadu_api_user,
            "MIGADU_API_USER",
        )
        api_key = _required_config(
            self.runtime_config.migadu_api_key,
            "MIGADU_API_KEY",
        )
        return MigaduClient(
            username=username,
            api_key=api_key,
            domain=normalize_migadu_mailbox_domain(
                self.runtime_config.migadu_mailbox_domain
            ),
        )

    def _add_emails_to_newsletter(self, emails: list[str]) -> str | None:
        try:
            result = sync_newsletter_contacts(
                self.runtime_config,
                emails,
                source="agent_account_creation",
            )
        except Exception as exc:
            text = " ".join(
                redact_email_addresses(f"Newsletter sync failed: {exc}").split()
            ).strip()
            return f"{text[:197]}..." if len(text) > 200 else text
        warning = format_newsletter_sync_warning(result)
        if not warning:
            return None
        text = " ".join(warning.split()).strip()
        return f"{text[:197]}..." if len(text) > 200 else text

    def _create_migadu_mailbox(
        self,
        arguments: dict[str, Any],
        *,
        subscribe_newsletter: bool = True,
    ) -> dict[str, Any]:
        local_part = str(arguments.get("local_part") or "").strip().lower()
        backup_email = _normalize_full_email(
            arguments.get("backup_email"),
            field_label="Mailbox backup_email",
        )
        name = str(arguments.get("name") or "").strip()
        if not local_part or not backup_email or not name:
            raise ValueError("Mailbox local_part, backup_email, and name are required")
        mailbox = self._migadu_client().create_mailbox(
            MigaduMailboxCreateRequest(
                local_part=local_part,
                backup_email=backup_email,
                name=name,
            )
        )
        if subscribe_newsletter:
            mailbox_email = str(mailbox.get("address") or "").strip().lower()
            if not mailbox_email:
                domain = normalize_migadu_mailbox_domain(
                    self.runtime_config.migadu_mailbox_domain
                )
                mailbox_email = f"{local_part}@{domain}"
            mailbox["newsletter_error"] = self._add_emails_to_newsletter(
                [mailbox_email, backup_email]
            )
            mailbox["newsletter_subscribed"] = mailbox["newsletter_error"] is None
        return mailbox

    def _create_migadu_mailbox_for_contact(
        self,
        *,
        contact: dict[str, Any],
        mailbox_username: str,
    ) -> dict[str, Any]:
        contact_id = _required_text(
            contact.get("id"), "Selected contact is missing a CRM ID"
        )
        target_email, local_part = self._normalize_mailbox_username(mailbox_username)
        existing_email = self._normalize_508_email(contact.get("c508Email"))
        if existing_email is not None:
            if existing_email != target_email:
                raise ValueError(
                    f"CRM contact already has a different 508 email: {existing_email}."
                )
            return self._mailbox_result(
                email=existing_email,
                created=False,
                crm_updated=False,
                backup_email="",
                newsletter_error=self._add_emails_to_newsletter(
                    [existing_email, _optional_str(contact.get("emailAddress")) or ""]
                ),
            )

        backup_email = _normalize_full_email(
            contact.get("emailAddress"),
            field_label="CRM primary email",
        )
        contact_name = _optional_str(contact.get("name")) or local_part
        mailbox = self._create_migadu_mailbox(
            {
                "local_part": local_part,
                "backup_email": backup_email,
                "name": contact_name,
            },
            subscribe_newsletter=False,
        )
        created_address = str(mailbox.get("address") or target_email).strip().lower()
        if created_address != target_email:
            message = (
                "Migadu created a mailbox but returned a different address "
                f"{created_address} than requested {target_email}."
            )
            result = self._mailbox_result(
                email=created_address,
                created=True,
                crm_updated=False,
                backup_email=backup_email,
                partial_success="mailbox_created_address_mismatch",
                error=message,
            )
            raise ToolPartialSuccessError(message, result)

        newsletter_error = self._add_emails_to_newsletter([target_email, backup_email])

        try:
            self._espo_client().update_contact(contact_id, {"c508Email": target_email})
        except EspoAPIError as exc:
            result = self._mailbox_result(
                email=target_email,
                created=True,
                crm_updated=False,
                backup_email=backup_email,
                partial_success="mailbox_created_crm_update_failed",
                error=_short_error(exc),
                newsletter_error=newsletter_error,
            )
            raise ToolPartialSuccessError(
                "Mailbox was created, but updating CRM c508Email failed.",
                result,
            ) from exc
        contact["c508Email"] = target_email
        return self._mailbox_result(
            email=target_email,
            created=True,
            crm_updated=True,
            backup_email=backup_email,
            newsletter_error=newsletter_error,
        )

    @staticmethod
    def _mailbox_result(
        *,
        email: str,
        created: bool,
        crm_updated: bool,
        backup_email: str,
        partial_success: str | None = None,
        error: str | None = None,
        newsletter_error: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "email": email,
            "created": created,
            "crm_updated": crm_updated,
            "backup_email": backup_email,
            "newsletter_subscribed": newsletter_error is None,
            "newsletter_error": newsletter_error,
        }
        if partial_success is not None:
            result["partial_success"] = partial_success
        if error is not None:
            result["error"] = error
        return result

    def _normalize_mailbox_username(self, mailbox_username: str) -> tuple[str, str]:
        normalized = mailbox_username.strip().lower()
        if not normalized:
            raise ValueError("Please provide a mailbox username like jane.")
        if " " in normalized:
            raise ValueError("Mailbox username cannot include spaces.")
        configured_domain = normalize_migadu_mailbox_domain(
            self.runtime_config.migadu_mailbox_domain
        )
        if "@" not in normalized:
            local_part = normalized
        else:
            if normalized.count("@") != 1:
                raise ValueError("Mailbox username must be in the format name@domain.")
            local_part, username_domain = normalized.split("@", 1)
            if username_domain != configured_domain:
                raise ValueError(
                    f"Mailbox username must be omitted or use the @{configured_domain} domain."
                )
        if not local_part:
            raise ValueError("Mailbox username is missing a local part.")
        if not _is_valid_email_local_part(local_part):
            raise ValueError("Mailbox username contains invalid characters.")
        return f"{local_part}@{configured_domain}", local_part


_WEB_QUERY_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_WEB_QUERY_ACCESS_TOKEN_RE = re.compile(
    r"\b(?:sk|pk|rk|ghp|github_pat|xox[baprs])-?[A-Za-z0-9_-]{16,}\b",
    re.IGNORECASE,
)
_WEB_QUERY_PAYMENT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_WEB_QUERY_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _web_research_client_from_config(config: ToolRuntimeConfig) -> WebResearchClient:
    """Build only explicitly configured public-web providers.

    Search-provider order is an administrator-controlled fallback order.  The
    Firecrawl extractor is made available whenever its credentials are present,
    even if it is not selected as a search fallback.
    """

    provider_order = _web_provider_order(config.agent_web_search_provider_order)
    timeout_seconds = float(config.agent_web_search_timeout_seconds)
    firecrawl: FirecrawlWebResearch | None = None
    if _optional_str(config.firecrawl_api_key) is not None:
        firecrawl = FirecrawlWebResearch(
            api_key=config.firecrawl_api_key or "",
            base_url=config.firecrawl_base_url,
            timeout_seconds=timeout_seconds,
        )

    search_providers: list[WebSearchProvider] = []
    for provider_name in provider_order:
        if provider_name == "searxng":
            base_url = _optional_str(config.searxng_base_url)
            if base_url is not None:
                search_providers.append(
                    SearxngWebSearch(
                        base_url=base_url,
                        timeout_seconds=timeout_seconds,
                        language=config.searxng_search_language,
                    )
                )
        elif provider_name == "brave":
            if _optional_str(config.brave_search_api_key) is not None:
                search_providers.append(
                    BraveWebSearch(
                        api_key=config.brave_search_api_key or "",
                        base_url=config.brave_search_base_url,
                        timeout_seconds=timeout_seconds,
                        country=config.brave_search_country,
                        search_language=config.brave_search_language,
                    )
                )
        elif provider_name == "firecrawl" and firecrawl is not None:
            search_providers.append(firecrawl)

    if not search_providers and firecrawl is None:
        raise RuntimeError(
            "No public web search provider is configured. Set SEARXNG_BASE_URL, "
            "BRAVE_SEARCH_API_KEY, or FIRECRAWL_API_KEY."
        )
    return WebResearchClient(
        search_providers=search_providers,
        extract_providers=(firecrawl,) if firecrawl is not None else (),
    )


def _web_provider_order(value: object) -> tuple[str, ...]:
    raw_values = str(value or "").split(",")
    names = tuple(item.strip().casefold() for item in raw_values if item.strip())
    if not names:
        raise ValueError("AGENT_WEB_SEARCH_PROVIDER_ORDER must list a provider")
    supported = {"searxng", "brave", "firecrawl"}
    unsupported = [name for name in names if name not in supported]
    if unsupported:
        raise ValueError(
            "AGENT_WEB_SEARCH_PROVIDER_ORDER contains unsupported provider: "
            f"{unsupported[0]}"
        )
    if len(set(names)) != len(names):
        raise ValueError("AGENT_WEB_SEARCH_PROVIDER_ORDER cannot repeat a provider")
    return names


def _validate_public_web_query(query: str) -> None:
    """Reject clear private-data markers before any outbound web request."""

    if not query:
        raise ValueError("Public web search query is required")
    if _WEB_QUERY_EMAIL_RE.search(query):
        raise PermissionError(
            "Public web search queries cannot contain email addresses"
        )
    if _WEB_QUERY_ACCESS_TOKEN_RE.search(query):
        raise PermissionError("Public web search queries cannot contain access tokens")
    if _WEB_QUERY_PAYMENT_CARD_RE.search(query):
        raise PermissionError("Public web search queries cannot contain payment cards")
    if _WEB_QUERY_UUID_RE.search(query):
        raise PermissionError("Public web search queries cannot contain internal IDs")
    if contains_private_agent_identifier(query):
        raise PermissionError(
            "Public web search queries cannot contain internal record identifiers"
        )


def _validate_memory_write_arguments(arguments: dict[str, Any]) -> None:
    """Reject values a memory store would deterministically refuse on confirm."""

    validate_memory_fact_key(arguments.get("key"))
    value_json = arguments.get("value_json")
    if not isinstance(value_json, dict) or not value_json:
        raise ValueError("Memory value_json object is required")
    validate_memory_value_for_persistence(value_json)


def _validate_planner_web_search_arguments(arguments: dict[str, Any]) -> None:
    """Apply web-provider bounds before a model proposal becomes a schedule."""

    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("Web search query must be text")
    try:
        normalized_query = validate_web_search_query(query)
        _validate_public_web_query(normalized_query)
        if "limit" in arguments:
            validate_web_search_limit(arguments["limit"])
    except WebResearchValidationError as exc:
        raise ValueError(str(exc)) from exc


def _validate_erp_read_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Validate the narrow ERP read contracts before a client is created."""

    allowed = _PLANNER_TOOL_ARGUMENTS[tool_name]
    unknown_arguments = set(arguments) - allowed
    if unknown_arguments:
        raise ValueError("Unknown ERP read arguments")

    if tool_name == "billing_read.search_invoices":
        _erp_invoice_type(arguments.get("invoice_type"))
        _erp_search_query(arguments.get("query"), field_name="Invoice query")
        _erp_result_limit(arguments.get("limit"), default=5)
        return
    if tool_name == "billing_read.get_invoice_summary":
        _erp_invoice_type(arguments.get("invoice_type"))
        _erp_required_text(
            arguments.get("invoice_id"),
            field_name="Invoice id",
            maximum=140,
        )
        return
    if tool_name == "billing_read.search_suppliers":
        _erp_search_query(arguments.get("query"), field_name="Supplier query")
        _erp_result_limit(arguments.get("limit"), default=5)
        return
    if tool_name == "erp_read.search_projects":
        _erp_search_query(arguments.get("query"), field_name="ERP project query")
        _erp_result_limit(arguments.get("limit"), default=5)
        return
    if tool_name == "erp_read.get_project_summary":
        _erp_required_text(
            arguments.get("project_id"),
            field_name="ERP project id",
            maximum=140,
        )
        return
    raise ValueError("Unsupported ERP read tool")


def _erp_invoice_type(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("invoice_type must be sales or purchase")
    normalized = value.strip().casefold()
    if normalized not in _ERP_INVOICE_DOCTYPES:
        raise ValueError("invoice_type must be sales or purchase")
    return normalized


def _erp_required_text(
    value: Any,
    *,
    field_name: str,
    maximum: int = 160,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is required")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} is too long")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{field_name} contains unsupported control characters")
    return normalized


def _erp_search_query(value: Any, *, field_name: str) -> str:
    normalized = _erp_required_text(value, field_name=field_name)
    if "%" in normalized or "_" in normalized:
        raise ValueError(f"{field_name} cannot contain wildcard characters")
    return normalized


def _erp_result_limit(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer between 1 and 10")
    if not 1 <= value <= 10:
        raise ValueError("limit must be between 1 and 10")
    return value


def _erp_invoice_search_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_id": _erp_payload_text(row.get("name"), maximum=140),
        "posting_date": _erp_payload_text(row.get("posting_date"), maximum=32),
        "status": _erp_document_status(row.get("docstatus")),
    }


def _erp_invoice_summary_payload(
    row: dict[str, Any],
    *,
    invoice_type: str,
) -> dict[str, Any]:
    return {
        "invoice_id": _erp_payload_text(row.get("name"), maximum=140),
        "invoice_type": invoice_type,
        "status": _erp_document_status(row.get("docstatus")),
        "posting_date": _erp_payload_text(row.get("posting_date"), maximum=32),
        "due_date": _erp_payload_text(row.get("due_date"), maximum=32),
        "customer": (
            _erp_payload_text(row.get("customer"), maximum=256)
            if invoice_type == "sales"
            else None
        ),
        "supplier": (
            _erp_payload_text(row.get("supplier"), maximum=256)
            if invoice_type == "purchase"
            else None
        ),
        "project": _erp_payload_text(row.get("project"), maximum=140),
        "currency": _erp_payload_text(row.get("currency"), maximum=16),
        "grand_total": _erp_payload_amount(row.get("grand_total")),
        "rounded_total": _erp_payload_amount(row.get("rounded_total")),
        "outstanding_amount": _erp_payload_amount(row.get("outstanding_amount")),
    }


def _erp_supplier_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "supplier_id": _erp_payload_text(row.get("name"), maximum=140),
        "supplier_name": _erp_payload_text(row.get("supplier_name"), maximum=256),
        "email": _erp_payload_text(row.get("email_id"), maximum=320),
    }


def _erp_project_summary_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": _erp_payload_text(row.get("name"), maximum=140),
        "project_name": _erp_payload_text(row.get("project_name"), maximum=256),
        "status": _erp_payload_text(row.get("status"), maximum=64),
        "customer": _erp_payload_text(row.get("customer"), maximum=256),
        "project_type": _erp_payload_text(row.get("project_type"), maximum=128),
        "priority": _erp_payload_text(row.get("priority"), maximum=64),
        "percent_complete": _erp_payload_amount(row.get("percent_complete")),
        "expected_start_date": _erp_payload_text(
            row.get("expected_start_date"),
            maximum=32,
        ),
        "expected_end_date": _erp_payload_text(
            row.get("expected_end_date"),
            maximum=32,
        ),
        "actual_start_date": _erp_payload_text(
            row.get("actual_start_date"),
            maximum=32,
        ),
        "actual_end_date": _erp_payload_text(
            row.get("actual_end_date"),
            maximum=32,
        ),
        "modified": _erp_payload_text(row.get("modified"), maximum=64),
    }


def _erp_document_status(value: Any) -> str:
    if value in (0, "0"):
        return "draft"
    if value in (1, "1"):
        return "submitted"
    if value in (2, "2"):
        return "cancelled"
    return "unknown"


def _erp_payload_text(value: Any, *, maximum: int) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    normalized = " ".join(str(value).split())
    return normalized[:maximum] or None


def _erp_payload_amount(value: Any) -> int | float | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        return normalized[:64] or None
    return None


def _onboarding_state_label(value: Any) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:64] or "notstarted"


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _required_text(value: Any, message: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(message)
    return normalized


def _normalize_full_email(value: Any, *, field_label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError(f"{field_label} is required.")
    if re.search(r"\s", normalized):
        raise ValueError(f"{field_label} must be a full email address.")
    local_part, sep, domain = normalized.partition("@")
    if sep != "@" or not local_part or not domain or "@" in domain:
        raise ValueError(f"{field_label} must be a full email address.")
    if not _is_valid_email_local_part(local_part):
        raise ValueError(f"{field_label} must be a full email address.")
    if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", domain):
        raise ValueError(f"{field_label} must be a full email address.")
    return normalized


def _is_valid_email_local_part(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._%+-]+", value))


def _maybe_authentik_user_pk(user: dict[str, Any]) -> int | None:
    try:
        return _authentik_user_pk(user)
    except ValueError:
        return None


def _authentik_user_pk(user: dict[str, Any]) -> int:
    raw_value = user.get("pk")
    if isinstance(raw_value, int) and not isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str) and raw_value.strip().isdigit():
        return int(raw_value.strip())
    raise ValueError("Authentik response did not include a numeric user id.")


def _short_error(exc: Exception) -> str:
    text = " ".join(str(exc).split()).strip()
    if len(text) > 200:
        return text[:200].rstrip() + "..."
    return text


def _is_mailbox_result(value: dict[str, Any]) -> bool:
    return (
        _optional_str(value.get("email")) is not None
        and "backup_email" in value
        and "crm_updated" in value
    )


def _is_sso_result(value: dict[str, Any]) -> bool:
    return (
        _optional_str(value.get("username")) is not None
        and _optional_str(value.get("email")) is not None
        and "crm_updated" in value
    )


def _looks_like_crm_contact_id(value: str) -> bool:
    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        value,
        re.IGNORECASE,
    ):
        return True
    if value.casefold().startswith("contact-"):
        return True
    return bool(
        re.fullmatch(r"[A-Za-z0-9_-]{8,}", value) and any(ch.isdigit() for ch in value)
    )


def _optional_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return date.fromisoformat(stripped).isoformat()
        except ValueError as exc:
            raise ValueError("Expected date value as valid ISO date") from exc
    return None


def _optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        return None
    normalized = [item for item in items if item]
    return normalized or None


def _memory_fact_payload(fact: MemoryFact) -> dict[str, Any]:
    return fact.model_dump(mode="json", exclude={"source_excerpt_hash"})


def _memory_scope_type(value: Any, *, default: MemoryScopeType) -> MemoryScopeType:
    normalized = str(value or default).strip().lower()
    if normalized not in {"user", "project", "org"}:
        raise ValueError("Memory scope_type must be user, project, or org")
    return normalized  # type: ignore[return-value]


def _required_organization_id(value: str | None) -> str:
    normalized = _optional_str(value)
    if normalized is None:
        raise ValueError("organization_id is required for tenant-scoped operations")
    return normalized


def _memory_visibility(value: Any, *, default: str) -> MemoryVisibility:
    normalized = str(value or default).strip().lower()
    if normalized not in {"private", "project", "org"}:
        raise ValueError("Memory visibility must be private, project, or org")
    return normalized  # type: ignore[return-value]


def _memory_visibility_for_scope(
    value: Any,
    *,
    scope_type: MemoryScopeType,
) -> MemoryVisibility:
    expected_visibility: dict[MemoryScopeType, MemoryVisibility] = {
        "user": "private",
        "project": "project",
        "org": "org",
    }
    expected = expected_visibility[scope_type]
    visibility = _memory_visibility(value, default=expected)
    if visibility != expected:
        raise ValueError("memory_visibility_must_match_scope")
    return visibility


def _memory_scope_id(
    value: Any,
    *,
    scope_type: MemoryScopeType,
    actor_id: str,
    organization_id: str | None,
    project_id: str | None,
    actor_scopes: set[str],
) -> str:
    scope_id = _optional_str(value)
    if scope_type == "user":
        if scope_id is not None and scope_id != actor_id:
            if "memory:admin" not in actor_scopes:
                raise PermissionError("User memory writes are limited to the actor")
            return scope_id
        return actor_id
    if scope_type == "project":
        return _trusted_project_scope_id(scope_id, project_id=project_id)
    if scope_type == "org" and organization_id is not None:
        if scope_id is not None and scope_id != organization_id:
            raise PermissionError("Org memory writes must use the request organization")
        return organization_id
    raise ValueError(f"scope_id is required for {scope_type} memory")


def _trusted_project_scope_id(value: str | None, *, project_id: str | None) -> str:
    if project_id is None:
        raise ValueError("project_id is required in trusted context")
    if value is not None and value != project_id:
        raise PermissionError("Project memory access is limited to the actor project")
    return project_id


def _required_config(value: str | None, name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise RuntimeError(f"{name} is required for this tool")
    return normalized


def _positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), maximum)


def _validate_crm_contact_search_arguments(arguments: dict[str, Any]) -> None:
    """Reject unusable model-planned CRM searches before schedule execution."""

    _required_text(arguments.get("query"), "CRM contact search query is required")
    limit = arguments.get("limit")
    if limit is None:
        return
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("CRM contact search limit must be an integer between 1 and 10")
    if not 1 <= limit <= 10:
        raise ValueError("CRM contact search limit must be between 1 and 10")


def _required_positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _github_repository_set(*values: str | None) -> set[str]:
    repositories: set[str] = set()
    for value in values:
        for repository in _optional_str_list(value) or []:
            normalized = repository.strip().strip("/")
            if normalized:
                repositories.add(normalized.casefold())
    return repositories


def _require_any_scope(
    actor_scopes: set[str],
    allowed_scopes: set[str],
    *,
    message: str,
) -> None:
    if not allowed_scopes & actor_scopes:
        raise PermissionError(message)


def _github_project_fields(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("GitHub project fields must be a non-empty list")
    normalized_fields: list[dict[str, Any]] = []
    for field_update in value:
        if not isinstance(field_update, dict) or set(field_update) - {"id", "value"}:
            raise ValueError("Each GitHub project field must contain id and value")
        field_id = _required_positive_int(
            field_update.get("id"),
            field_name="GitHub project field id",
        )
        value_to_set = field_update.get("value")
        if value_to_set is not None and not isinstance(
            value_to_set,
            str | int | float | bool,
        ):
            raise ValueError("GitHub project field value must be a scalar or null")
        normalized_fields.append({"id": field_id, "value": value_to_set})
    return normalized_fields


def _validate_planner_argument_value(value: Any, *, depth: int = 0) -> None:
    """Bound JSON-like model output before it reaches an integration client."""

    if depth > 4:
        raise ValueError("argument_nesting_too_deep")
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if len(value) > 8_000:
            raise ValueError("argument_string_too_long")
        return
    if isinstance(value, list):
        if len(value) > 50:
            raise ValueError("argument_list_too_long")
        for item in value:
            _validate_planner_argument_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 50 or not all(isinstance(key, str) for key in value):
            raise ValueError("invalid_argument_object")
        for item in value.values():
            _validate_planner_argument_value(item, depth=depth + 1)
        return
    raise ValueError("invalid_argument_type")
