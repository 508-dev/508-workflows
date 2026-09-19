"""Memory fact storage primitives for the agent gateway."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from five08.agent.models import MemoryFact, MemoryScopeType, MemoryVisibility

DEFAULT_MEMORY_RETENTION_DAYS = 365


class MemoryStore(Protocol):
    """Durable fact store interface used by deterministic memory tools."""

    def remember_fact(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        key: str,
        value_json: dict[str, Any],
        visibility: MemoryVisibility,
        source_type: str,
        source_ref: str,
        source_excerpt: str | None,
        created_by: str,
        verification_status: str,
        organization_id: str | None = None,
        confidence: float = 1.0,
        expires_at: datetime | None = None,
        replaces_id: str | None = None,
    ) -> MemoryFact:
        """Persist one memory fact."""

    def list_facts(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        visible_to_user_id: str,
        visible_to_project_id: str | None,
        visible_to_org_id: str | None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryFact]:
        """Return visible non-expired facts by default."""

    def forget_fact(
        self,
        *,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        organization_id: str | None = None,
        project_id: str | None = None,
        actor_can_write_project: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        """Soft-delete one fact the actor may manage."""


class InMemoryMemoryStore:
    """Thread-safe process-local memory store for the MVP and unit tests."""

    def __init__(self, facts: Iterable[MemoryFact] | None = None) -> None:
        self._facts: dict[str, MemoryFact] = {}
        self._lock = threading.RLock()
        for fact in facts or []:
            self._facts[fact.id] = fact

    def remember_fact(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        key: str,
        value_json: dict[str, Any],
        visibility: MemoryVisibility,
        source_type: str,
        source_ref: str,
        source_excerpt: str | None,
        created_by: str,
        verification_status: str,
        organization_id: str | None = None,
        confidence: float = 1.0,
        expires_at: datetime | None = None,
        replaces_id: str | None = None,
    ) -> MemoryFact:
        now = datetime.now(timezone.utc)
        fact = MemoryFact(
            organization_id=organization_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key.strip(),
            value_json=value_json,
            visibility=visibility,
            source_type=source_type,  # type: ignore[arg-type]
            source_ref=source_ref,
            source_excerpt_hash=_excerpt_hash(source_excerpt),
            created_by=created_by,
            verification_status=verification_status,  # type: ignore[arg-type]
            confidence=confidence,
            expires_at=expires_at
            or now + timedelta(days=DEFAULT_MEMORY_RETENTION_DAYS),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            existing = (
                self._facts.get(replaces_id)
                if replaces_id
                else next(
                    (
                        old
                        for old in reversed(list(self._facts.values()))
                        if old.organization_id == organization_id
                        and old.scope_type == scope_type
                        and old.scope_id == scope_id
                        and old.status == "active"
                        and old.deleted_at is None
                        and memory_slot(old.key, old.value_json)
                        == memory_slot(key, value_json)
                    ),
                    None,
                )
            )
            if replaces_id and (
                existing is None
                or existing.scope_type != scope_type
                or existing.scope_id != scope_id
                or existing.organization_id != organization_id
                or existing.created_by != created_by
                or existing.status != "active"
            ):
                raise PermissionError(
                    "Memory to edit is unavailable or not owned by you"
                )
            if existing is not None:
                fact = fact.model_copy(update={"supersedes_id": existing.id})
                self._facts[existing.id] = existing.model_copy(
                    update={"status": "superseded", "updated_at": now}
                )
            self._facts[fact.id] = fact
        return fact

    def list_facts(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        visible_to_user_id: str,
        visible_to_project_id: str | None,
        visible_to_org_id: str | None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryFact]:
        comparison_time = now or datetime.now(timezone.utc)
        with self._lock:
            return [
                fact
                for fact in self._facts.values()
                if fact.scope_type == scope_type
                and fact.scope_id == scope_id
                and _fact_is_visible(
                    fact,
                    user_id=visible_to_user_id,
                    project_id=visible_to_project_id,
                    org_id=visible_to_org_id,
                )
                and (
                    fact.organization_id is None
                    or fact.organization_id == visible_to_org_id
                )
                and (
                    include_deleted
                    or (fact.deleted_at is None and fact.status == "active")
                )
                and not _fact_is_expired(fact, now=comparison_time)
            ]

    def forget_fact(
        self,
        *,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        organization_id: str | None = None,
        project_id: str | None = None,
        actor_can_write_project: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        with self._lock:
            fact = self._facts.get(fact_id)
            if fact is None:
                raise KeyError(f"Memory fact {fact_id} was not found")
            authorize_memory_fact_deletion(
                scope_type=fact.scope_type,
                scope_id=fact.scope_id,
                visibility=fact.visibility,
                fact_organization_id=fact.organization_id,
                actor_id=actor_id,
                actor_is_admin=actor_is_admin,
                organization_id=organization_id,
                project_id=project_id,
                actor_can_write_project=actor_can_write_project,
            )
            deleted_at = now or datetime.now(timezone.utc)
            deleted = fact.model_copy(
                update={
                    "status": "deleted",
                    "deleted_at": deleted_at,
                    "updated_at": deleted_at,
                }
            )
            self._facts[fact_id] = deleted
            return deleted


def authorize_memory_fact_deletion(
    *,
    scope_type: MemoryScopeType,
    scope_id: str,
    visibility: MemoryVisibility,
    fact_organization_id: str | None,
    actor_id: str,
    actor_is_admin: bool,
    organization_id: str | None,
    project_id: str | None,
    actor_can_write_project: bool,
) -> None:
    """Require current authority over the fact's scope before deleting it."""
    if visibility == "private":
        if (
            scope_type != "user"
            or scope_id != actor_id
            or organization_id is None
            or fact_organization_id != organization_id
        ):
            raise PermissionError("Private memory belongs only to its owner")
        return
    if visibility == "project":
        if (
            scope_type != "project"
            or not actor_can_write_project
            or project_id != scope_id
            or organization_id is None
            or fact_organization_id != organization_id
        ):
            raise PermissionError(
                "Project memory deletion requires current project access"
            )
        return
    if visibility == "org":
        if (
            scope_type != "org"
            or not actor_is_admin
            or organization_id != scope_id
            or fact_organization_id != organization_id
        ):
            raise PermissionError(
                "Org memory deletion requires current memory admin access"
            )
        return
    raise PermissionError("Memory fact has an unsupported visibility")


def _fact_is_visible(
    fact: MemoryFact,
    *,
    user_id: str,
    project_id: str | None,
    org_id: str | None,
) -> bool:
    if fact.visibility == "private":
        return fact.scope_type == "user" and fact.scope_id == user_id
    if fact.visibility == "project":
        return fact.scope_type == "project" and fact.scope_id == project_id
    if fact.visibility == "org":
        return fact.scope_type == "org" and fact.scope_id == org_id
    return False


def _fact_is_expired(fact: MemoryFact, *, now: datetime) -> bool:
    if fact.expires_at is None:
        return False
    expires_at = fact.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at.astimezone(timezone.utc) <= now


def _excerpt_hash(source_excerpt: str | None) -> str | None:
    if source_excerpt is None:
        return None
    return hashlib.sha256(source_excerpt.encode("utf-8")).hexdigest()


def memory_slot(key: str, value: dict[str, Any]) -> str:
    """Named facts replace their prior value; independent notes retain separate slots."""
    normalized = key.strip().casefold()
    if normalized == "note":
        normalized += (
            ":"
            + hashlib.sha256(
                json.dumps(value, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
    return "fact:" + normalized
