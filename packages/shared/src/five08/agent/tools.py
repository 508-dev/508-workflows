"""Deterministic tool registry and inline MVP task tools."""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from five08.agent.models import RiskLevel
from five08.clients.docuseal import create_member_agreement_submission
from five08.clients.espo import EspoClient
from five08.clients.github import GitHubClient
from five08.clients.migadu import (
    MigaduClient,
    MigaduMailboxCreateRequest,
    normalize_migadu_mailbox_domain,
)
from five08.crm_contacts import EspoContactRepository


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
        runtime_config: ToolRuntimeConfig | None = None,
    ) -> None:
        self.task_store = task_store or InMemoryTaskStore()
        self.runtime_config = runtime_config or ToolRuntimeConfig()
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
                required_scopes=("mailbox:create",),
                requires_confirmation=True,
                tenant_scoped=True,
                idempotent=False,
                write=True,
            ),
        }

    def get(self, tool_name: str) -> ToolManifest | None:
        return self._manifests.get(tool_name)

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        actor_id: str | None,
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
        raise KeyError(f"Unknown tool {tool_name}")

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
        base_url = _required_config(self.runtime_config.espo_base_url, "ESPO_BASE_URL")
        api_key = _required_config(self.runtime_config.espo_api_key, "ESPO_API_KEY")
        return EspoContactRepository(EspoClient(base_url, api_key))

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

    def _create_migadu_mailbox(self, arguments: dict[str, Any]) -> dict[str, Any]:
        local_part = str(arguments.get("local_part") or "").strip().lower()
        backup_email = str(arguments.get("backup_email") or "").strip()
        name = str(arguments.get("name") or "").strip()
        if not local_part or not backup_email or not name:
            raise ValueError("Mailbox local_part, backup_email, and name are required")
        username = _required_config(
            self.runtime_config.migadu_api_user,
            "MIGADU_API_USER",
        )
        api_key = _required_config(
            self.runtime_config.migadu_api_key,
            "MIGADU_API_KEY",
        )
        client = MigaduClient(
            username=username,
            api_key=api_key,
            domain=normalize_migadu_mailbox_domain(
                self.runtime_config.migadu_mailbox_domain
            ),
        )
        return client.create_mailbox(
            MigaduMailboxCreateRequest(
                local_part=local_part,
                backup_email=backup_email,
                name=name,
            )
        )


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


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
