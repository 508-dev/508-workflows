"""Deterministic tool registry and inline MVP task tools."""

from __future__ import annotations

import itertools
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from five08.agent.memory import InMemoryMemoryStore, MemoryStore
from five08.agent.models import (
    MemoryFact,
    MemoryScopeType,
    MemoryVisibility,
    RiskLevel,
)
from five08.clients.authentik import AuthentikAPIError, AuthentikClient
from five08.clients.docuseal import create_member_agreement_submission
from five08.clients.espo import EspoAPIError, EspoClient
from five08.clients.github import GitHubClient
from five08.clients.migadu import (
    MigaduClient,
    MigaduMailboxCreateRequest,
    normalize_migadu_mailbox_domain,
)
from five08.clients.outline import OutlineClient
from five08.crm_contacts import EspoContactRepository
from five08.newsletter_sync import (
    format_newsletter_sync_warning,
    sync_newsletter_contacts,
)
from five08.redaction import redact_email_addresses

SSO_ID_FIELD = "cSsoID"

_PLANNER_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "task_read.search_tasks": frozenset({"query", "project"}),
    "task_write.create_task": frozenset({"title", "project", "assignee", "due_date"}),
    "task_write.update_task": frozenset(
        {"task_id", "title", "project", "assignee", "due_date", "status"}
    ),
    "github_issue.search_issues": frozenset({"query", "repository", "state", "limit"}),
    "github_issue.create_issue": frozenset({"title", "repository", "body", "labels"}),
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
    "memory_read.get_project_facts": frozenset({"project_id"}),
    "memory_read.search_context": frozenset(),
    "memory_write.remember_fact": frozenset(
        {
            "scope_type",
            "scope_id",
            "key",
            "value_json",
            "visibility",
            "source_type",
            "source_ref",
            "source_excerpt",
            "verification_status",
            "confidence",
        }
    ),
    "memory_write.forget_fact": frozenset({"fact_id", "admin"}),
}


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


class ToolPartialSuccessError(RuntimeError):
    """Raised when an irreversible tool step succeeded before a later failure."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ToolRuntimeConfig:
    """Runtime credentials and defaults for deterministic external tools."""

    github_api_token: str | None = None
    github_default_repo: str | None = None
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
    outline_api_key: str | None = None
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

    @classmethod
    def from_settings(cls, settings: Any) -> "ToolRuntimeConfig":
        """Build tool runtime config from service settings without coupling types."""
        return cls(
            github_api_token=getattr(settings, "github_api_token", None),
            github_default_repo=getattr(settings, "github_default_repo", None),
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
            outline_api_key=getattr(settings, "outline_api_key", None),
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
    ) -> None:
        self.task_store = task_store or InMemoryTaskStore()
        self.memory_store = memory_store or InMemoryMemoryStore()
        self._runtime_config = runtime_config or ToolRuntimeConfig()
        self._runtime_config_factory = runtime_config_factory
        self._manifests = {
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
                required_scopes=("github:issue:read",),
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "github_issue.create_issue": ToolManifest(
                name="github_issue.create_issue",
                risk="medium",
                required_scopes=("github:issue:create",),
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
                tenant_scoped=False,
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
                tenant_scoped=False,
                idempotent=True,
                write=False,
            ),
            "memory_write.remember_fact": ToolManifest(
                name="memory_write.remember_fact",
                risk="medium",
                required_scopes=("memory:write_self",),
                requires_confirmation=True,
                tenant_scoped=False,
                idempotent=False,
                write=True,
            ),
            "memory_write.forget_fact": ToolManifest(
                name="memory_write.forget_fact",
                risk="medium",
                required_scopes=("memory:write_self",),
                requires_confirmation=True,
                tenant_scoped=False,
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
        _validate_planner_argument_value(arguments)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        actor_id: str | None,
        project_id: str | None = None,
        actor_scopes: set[str] | None = None,
    ) -> dict[str, Any]:
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
        if tool_name == "github_issue.search_issues":
            return self._github_client().search_issues(
                repository=self._resolve_repository(arguments),
                query=str(arguments.get("query") or "").strip(),
                state=str(arguments.get("state") or "open").strip().lower(),
                limit=_positive_int(arguments.get("limit"), default=10, maximum=20),
            )
        if tool_name == "github_issue.create_issue":
            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ValueError("GitHub issue title is required")
            return self._github_client().create_issue(
                repository=self._resolve_repository(arguments),
                title=title,
                body=_optional_str(arguments.get("body")),
                labels=_optional_str_list(arguments.get("labels")),
            )
        if tool_name == "crm_read.search_contacts":
            return self._search_crm_contacts(arguments)
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
                actor_scopes=actor_scopes or set(),
            )
        raise KeyError(f"Unknown tool {tool_name}")

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
        facts = self.memory_store.list_facts(
            scope_type="user",
            scope_id=user_id,
            visible_to_user_id=user_id,
            visible_to_project_id=None,
            visible_to_org_id=organization_id,
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
        facts = self.memory_store.list_facts(
            scope_type="project",
            scope_id=project_id,
            visible_to_user_id=actor_id or "",
            visible_to_project_id=project_id,
            visible_to_org_id=organization_id,
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
        scope_type = _memory_scope_type(arguments.get("scope_type"), default="user")
        scope_id = _memory_scope_id(
            arguments.get("scope_id"),
            scope_type=scope_type,
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project_id,
            actor_scopes=actor_scopes,
        )
        if scope_type == "project" and "memory:write_project" not in actor_scopes:
            raise PermissionError("Project memory writes require project memory scope")
        if scope_type == "org" and "memory:admin" not in actor_scopes:
            raise PermissionError("Org memory writes require memory admin scope")
        key = str(arguments.get("key") or "").strip()
        value = arguments.get("value_json")
        if not key:
            raise ValueError("Memory key is required")
        if not isinstance(value, dict) or not value:
            raise ValueError("Memory value_json object is required")
        visibility = _memory_visibility(
            arguments.get("visibility"),
            default="private" if scope_type == "user" else scope_type,
        )
        fact = self.memory_store.remember_fact(
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
        actor_scopes: set[str],
    ) -> dict[str, Any]:
        if actor_id is None:
            raise ValueError("actor_id is required")
        fact_id = str(arguments.get("fact_id") or "").strip()
        if not fact_id:
            raise ValueError("fact_id is required")
        fact = self.memory_store.forget_fact(
            fact_id=fact_id,
            actor_id=actor_id,
            actor_is_admin="memory:admin" in actor_scopes,
        )
        return {"fact": _memory_fact_payload(fact)}

    def _github_client(self) -> GitHubClient:
        token = _required_config(
            self.runtime_config.github_api_token, "GITHUB_API_TOKEN"
        )
        return GitHubClient(token=token)

    def _resolve_repository(self, arguments: dict[str, Any]) -> str:
        repository = _optional_str(arguments.get("repository"))
        if repository is None:
            repository = _optional_str(self.runtime_config.github_default_repo)
        if repository is None:
            raise ValueError("GitHub repository is required")
        normalized_repository = repository.strip().strip("/")
        repository_parts = normalized_repository.split("/")
        if len(repository_parts) != 2 or not all(repository_parts):
            raise ValueError("GitHub repository must be in owner/name form")
        allowed_repositories = {
            repo.lower()
            for repo in _optional_str_list(self.runtime_config.github_allowed_repos)
            or []
        }
        default_repository = _optional_str(self.runtime_config.github_default_repo)
        if default_repository is not None:
            allowed_repositories.add(default_repository.strip().strip("/").lower())
        if not allowed_repositories:
            raise ValueError(
                "GitHub repository is not allowed; configure GITHUB_DEFAULT_REPO "
                "or GITHUB_ALLOWED_REPOS"
            )
        if normalized_repository.lower() not in allowed_repositories:
            raise ValueError(
                "GitHub repository is not allowed by GITHUB_DEFAULT_REPO "
                "or GITHUB_ALLOWED_REPOS"
            )
        return normalized_repository

    def _crm_repository(self) -> EspoContactRepository:
        return EspoContactRepository(self._espo_client())

    def _espo_client(self) -> EspoClient:
        base_url = _required_config(self.runtime_config.espo_base_url, "ESPO_BASE_URL")
        api_key = _required_config(self.runtime_config.espo_api_key, "ESPO_API_KEY")
        return EspoClient(base_url, api_key)

    def _search_crm_contacts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("CRM contact search query is required")
        limit = _positive_int(arguments.get("limit"), default=5, maximum=10)
        contacts = self._crm_repository().search(
            limit=limit,
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
        return {"contacts": [contact.to_dict() for contact in contacts]}

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
                self.runtime_config.outline_api_key,
                "OUTLINE_API_KEY",
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


def _memory_visibility(value: Any, *, default: str) -> MemoryVisibility:
    normalized = str(value or default).strip().lower()
    if normalized not in {"private", "project", "org"}:
        raise ValueError("Memory visibility must be private, project, or org")
    return normalized  # type: ignore[return-value]


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
