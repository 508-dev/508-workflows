"""PostgreSQL-backed durable memory facts for the agent gateway.

This module deliberately implements only the ``MemoryStore`` contract.  Runtime
wiring remains responsible for selecting it instead of the in-memory store.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.agent.memory import (
    DEFAULT_MEMORY_RETENTION_DAYS,
    MAX_MEMORY_FACTS_PER_LIST,
    assert_visible_org_matches_tenant,
    normalize_memory_time,
    normalize_organization_id,
    validate_memory_value_for_persistence,
)
from five08.agent.models import (
    AgentContextSourceType,
    MemoryFact,
    MemoryScopeType,
    MemoryVerificationStatus,
    MemoryVisibility,
)

ConnectionFactory = Callable[[], Any]
DEFAULT_MEMORY_DATABASE_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_MEMORY_DATABASE_STATEMENT_TIMEOUT_MILLISECONDS = 10_000


class PostgresMemoryStore:
    """Durable ``MemoryStore`` implementation using ``agent_memory_facts``.

    Each method opens a short transaction so writes commit atomically and reads
    do not retain a connection between agent operations. Production connections
    use bounded connect and server-side statement timeouts. ``connection_factory``
    exists for dependency injection and unit tests; production callers normally
    provide ``postgres_url``.
    """

    def __init__(
        self,
        postgres_url: str | None = None,
        *,
        connection_factory: ConnectionFactory | None = None,
        connect_timeout_seconds: int = DEFAULT_MEMORY_DATABASE_CONNECT_TIMEOUT_SECONDS,
        statement_timeout_milliseconds: int = DEFAULT_MEMORY_DATABASE_STATEMENT_TIMEOUT_MILLISECONDS,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if statement_timeout_milliseconds <= 0:
            raise ValueError("statement_timeout_milliseconds must be positive")
        if connection_factory is None:
            normalized_url = (postgres_url or "").strip()
            if not normalized_url:
                raise ValueError(
                    "postgres_url is required when no connection_factory is provided"
                )
            self._connection_factory: ConnectionFactory = lambda: connect(
                normalized_url,
                connect_timeout=connect_timeout_seconds,
                options=f"-c statement_timeout={statement_timeout_milliseconds}",
            )
        else:
            self._connection_factory = connection_factory

    def remember_fact(
        self,
        *,
        organization_id: str,
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
        """Insert one immutable memory fact and return its persisted row."""
        normalized_organization_id = normalize_organization_id(organization_id)
        validate_memory_value_for_persistence(value_json)
        now = normalize_memory_time(None)
        retained_until = expires_at or now + timedelta(
            days=DEFAULT_MEMORY_RETENTION_DAYS
        )
        draft = MemoryFact(
            id=str(uuid4()),
            organization_id=normalized_organization_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key.strip(),
            value_json=value_json,
            visibility=visibility,
            source_type=cast(AgentContextSourceType, source_type),
            source_ref=source_ref,
            source_excerpt_hash=_excerpt_hash(source_excerpt),
            created_by=created_by,
            verification_status=cast(MemoryVerificationStatus, verification_status),
            confidence=confidence,
            expires_at=retained_until,
            created_at=now,
            updated_at=now,
        )
        query = """
            INSERT INTO agent_memory_facts (
                id,
                organization_id,
                scope_type,
                scope_id,
                key,
                value_json,
                visibility,
                source_type,
                source_ref,
                source_excerpt_hash,
                created_by,
                verification_status,
                confidence,
                expires_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            RETURNING
                id,
                organization_id,
                scope_type,
                scope_id,
                key,
                value_json,
                visibility,
                source_type,
                source_ref,
                source_excerpt_hash,
                created_by,
                verification_status,
                confidence,
                expires_at,
                deleted_at,
                created_at,
                updated_at
        """
        with self._connection_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                self._purge_expired_with_cursor(
                    cursor,
                    organization_id=normalized_organization_id,
                    now=now,
                )
                cursor.execute(
                    query,
                    (
                        draft.id,
                        draft.organization_id,
                        draft.scope_type,
                        draft.scope_id,
                        draft.key,
                        Jsonb(draft.value_json),
                        draft.visibility,
                        draft.source_type,
                        draft.source_ref,
                        draft.source_excerpt_hash,
                        draft.created_by,
                        draft.verification_status,
                        draft.confidence,
                        draft.expires_at,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Memory fact insert did not return a row")
        return _memory_fact_from_row(
            row,
            expected_organization_id=normalized_organization_id,
        )

    def list_facts(
        self,
        *,
        organization_id: str,
        scope_type: MemoryScopeType,
        scope_id: str,
        visible_to_user_id: str,
        visible_to_project_id: str | None,
        visible_to_org_id: str | None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryFact]:
        """Return facts visible in the supplied user, project, or org context."""
        normalized_organization_id = normalize_organization_id(organization_id)
        assert_visible_org_matches_tenant(
            visible_to_org_id=visible_to_org_id,
            organization_id=normalized_organization_id,
        )
        comparison_time = normalize_memory_time(now)
        query = """
            WITH newest_facts AS (
                SELECT
                    id,
                    organization_id,
                    scope_type,
                    scope_id,
                    key,
                    value_json,
                    visibility,
                    source_type,
                    source_ref,
                    source_excerpt_hash,
                    created_by,
                    verification_status,
                    confidence,
                    expires_at,
                    deleted_at,
                    created_at,
                    updated_at
                FROM agent_memory_facts
                WHERE organization_id = %s
                  AND scope_type = %s
                  AND scope_id = %s
                  AND (
                      (visibility = 'private' AND scope_type = 'user' AND scope_id = %s)
                      OR (
                          visibility = 'project'
                          AND scope_type = 'project'
                          AND scope_id = %s
                      )
                      OR (visibility = 'org' AND scope_type = 'org' AND scope_id = %s)
                  )
                  AND (%s OR deleted_at IS NULL)
                  AND (expires_at IS NULL OR expires_at > %s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            )
            SELECT *
            FROM newest_facts
            ORDER BY created_at ASC, id ASC
        """
        with self._connection_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    query,
                    (
                        normalized_organization_id,
                        scope_type,
                        scope_id,
                        visible_to_user_id,
                        visible_to_project_id,
                        visible_to_org_id,
                        include_deleted,
                        comparison_time,
                        MAX_MEMORY_FACTS_PER_LIST,
                    ),
                )
                rows = cursor.fetchall()
        facts: list[MemoryFact] = []
        for row in rows:
            fact = _memory_fact_from_row(row)
            # The predicate above is the primary tenant boundary.  Retaining
            # this postcondition protects callers even if a database view or a
            # test double violates that contract.
            if fact.organization_id == normalized_organization_id:
                facts.append(fact)
        return facts[:MAX_MEMORY_FACTS_PER_LIST]

    def forget_fact(
        self,
        *,
        organization_id: str,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        """Immediately remove one fact after atomically checking its manager."""
        normalized_organization_id = normalize_organization_id(organization_id)
        deleted_at = normalize_memory_time(now)
        select_query = """
            SELECT created_by, organization_id
            FROM agent_memory_facts
            WHERE id = %s
              AND organization_id = %s
            FOR UPDATE
        """
        delete_query = """
            DELETE FROM agent_memory_facts
            WHERE id = %s
              AND organization_id = %s
            RETURNING
                id,
                organization_id,
                scope_type,
                scope_id,
                key,
                value_json,
                visibility,
                source_type,
                source_ref,
                source_excerpt_hash,
                created_by,
                verification_status,
                confidence,
                expires_at,
                deleted_at,
                created_at,
                updated_at
        """
        with self._connection_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                self._purge_expired_with_cursor(
                    cursor,
                    organization_id=normalized_organization_id,
                    now=deleted_at,
                )
                cursor.execute(select_query, (fact_id, normalized_organization_id))
                existing = cursor.fetchone()
                if (
                    existing is None
                    or str(existing["organization_id"]) != normalized_organization_id
                ):
                    raise KeyError(f"Memory fact {fact_id} was not found")
                if not actor_is_admin and str(existing["created_by"]) != actor_id:
                    raise PermissionError(
                        "Memory fact can only be deleted by its creator"
                    )
                cursor.execute(
                    delete_query,
                    (
                        fact_id,
                        normalized_organization_id,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Memory fact disappeared before it could be deleted")
        deleted = _memory_fact_from_row(
            row,
            expected_organization_id=normalized_organization_id,
        )
        # The row has been physically removed. Retain the deletion timestamp
        # only in the response object so callers can record an audit event
        # without preserving the fact's value in durable storage.
        return deleted.model_copy(
            update={"deleted_at": deleted_at, "updated_at": deleted_at}
        )

    def purge_expired(
        self,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> int:
        """Physically delete expired or soft-deleted records for one tenant."""
        normalized_organization_id = normalize_organization_id(organization_id)
        comparison_time = normalize_memory_time(now)
        with self._connection_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                return self._purge_expired_with_cursor(
                    cursor,
                    organization_id=normalized_organization_id,
                    now=comparison_time,
                )

    def purge_expired_all_organizations(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Physically delete expired records across tenants for worker maintenance.

        This is intentionally not part of the request-facing ``MemoryStore``
        contract: callers serving a user always operate inside one organization.
        The only intended caller is the trusted, scheduled worker maintenance
        job, which receives no tenant or fact data in return.
        """

        comparison_time = normalize_memory_time(now)
        with self._connection_factory() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    DELETE FROM agent_memory_facts
                    WHERE deleted_at IS NOT NULL
                       OR (expires_at IS NOT NULL AND expires_at <= %s)
                    """,
                    (comparison_time,),
                )
                row_count = getattr(cursor, "rowcount", 0)
        return max(int(row_count or 0), 0)

    @staticmethod
    def _purge_expired_with_cursor(
        cursor: Any,
        *,
        organization_id: str,
        now: datetime,
    ) -> int:
        cursor.execute(
            """
            DELETE FROM agent_memory_facts
            WHERE organization_id = %s
              AND (
                  deleted_at IS NOT NULL
                  OR (expires_at IS NOT NULL AND expires_at <= %s)
              )
            """,
            (organization_id, now),
        )
        row_count = getattr(cursor, "rowcount", 0)
        return max(int(row_count or 0), 0)


def _memory_fact_from_row(
    row: Mapping[str, Any],
    *,
    expected_organization_id: str | None = None,
) -> MemoryFact:
    """Validate a database row before returning it across the store boundary."""
    payload = dict(row)
    payload["id"] = str(payload["id"])
    fact = MemoryFact.model_validate(payload)
    if (
        expected_organization_id is not None
        and fact.organization_id != expected_organization_id
    ):
        raise RuntimeError("Memory fact row violated its organization boundary")
    return fact


def _excerpt_hash(source_excerpt: str | None) -> str | None:
    if source_excerpt is None:
        return None
    return hashlib.sha256(source_excerpt.encode("utf-8")).hexdigest()
