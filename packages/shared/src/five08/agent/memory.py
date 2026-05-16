"""Memory fact storage primitives for the agent gateway."""

from __future__ import annotations

import hashlib
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
        confidence: float = 1.0,
        expires_at: datetime | None = None,
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
        confidence: float = 1.0,
        expires_at: datetime | None = None,
    ) -> MemoryFact:
        now = datetime.now(timezone.utc)
        fact = MemoryFact(
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
                and (include_deleted or fact.deleted_at is None)
                and not _fact_is_expired(fact, now=comparison_time)
            ]

    def forget_fact(
        self,
        *,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        with self._lock:
            fact = self._facts.get(fact_id)
            if fact is None:
                raise KeyError(f"Memory fact {fact_id} was not found")
            if not actor_is_admin and fact.created_by != actor_id:
                raise PermissionError("Memory fact can only be deleted by its creator")
            deleted = fact.model_copy(
                update={
                    "deleted_at": now or datetime.now(timezone.utc),
                    "updated_at": now or datetime.now(timezone.utc),
                }
            )
            self._facts[fact_id] = deleted
            return deleted


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
