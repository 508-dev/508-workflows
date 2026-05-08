"""Deterministic tool registry and inline MVP task tools."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from five08.agent.models import RiskLevel


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
    """Small inline task store for synchronous agent command execution."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._tasks: dict[str, TaskRecord] = {}

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
        title: str | None = None,
        project: str | None = None,
        assignee: str | None = None,
        due_date: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} was not found")
        if task.organization_id and organization_id != task.organization_id:
            raise PermissionError("Task belongs to a different organization")
        if task.created_by and actor_id != task.created_by:
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
        normalized_query = query.strip().casefold()
        normalized_project = project.strip().casefold() if project else None
        matches: list[dict[str, Any]] = []
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

    def __init__(self, task_store: InMemoryTaskStore | None = None) -> None:
        self.task_store = task_store or InMemoryTaskStore()
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
    ) -> dict[str, Any]:
        if tool_name == "task_read.search_tasks":
            return self.task_store.search_tasks(
                query=str(arguments.get("query") or ""),
                organization_id=organization_id,
                project=_optional_str(arguments.get("project")),
            )
        if tool_name == "task_write.create_task":
            return self.task_store.create_task(
                title=str(arguments.get("title") or "").strip(),
                project=_optional_str(arguments.get("project")),
                assignee=_optional_str(arguments.get("assignee")),
                due_date=_optional_date(arguments.get("due_date")),
                organization_id=organization_id,
                created_by=actor_id,
            )
        if tool_name == "task_write.update_task":
            return self.task_store.update_task(
                task_id=str(arguments.get("task_id") or "").strip().upper(),
                organization_id=organization_id,
                actor_id=actor_id,
                title=_optional_str(arguments.get("title")),
                project=_optional_str(arguments.get("project")),
                assignee=_optional_str(arguments.get("assignee")),
                due_date=_optional_date(arguments.get("due_date")),
                status=_optional_str(arguments.get("status")),
            )
        raise KeyError(f"Unknown tool {tool_name}")


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
        return stripped or None
    return None
