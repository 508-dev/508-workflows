"""Unit tests for durable agent memory persistence boundaries."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from psycopg.types.json import Jsonb

from five08.agent.memory import MAX_MEMORY_FACTS_PER_LIST
from five08.agent.postgres_memory import PostgresMemoryStore


class FakeCursor:
    def __init__(
        self,
        *,
        one_rows: list[dict[str, Any] | None] | None = None,
        all_rows: list[dict[str, Any]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.rowcount = rowcount
        self._one_rows = list(one_rows or [])
        self._all_rows = list(all_rows or [])

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self._one_rows.pop(0)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._all_rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_instance = cursor
        self.cursor_kwargs: dict[str, object] | None = None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self, **kwargs: object) -> FakeCursor:
        self.cursor_kwargs = kwargs
        return self.cursor_instance


def _row(
    *,
    fact_id: str = "0e5e5302-8d36-4bc8-954d-68332b36949b",
    organization_id: str = "org-1",
    scope_type: str = "user",
    scope_id: str = "123",
    key: str = "timezone",
    visibility: str = "private",
    created_by: str = "123",
    expires_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    return {
        "id": UUID(fact_id),
        "organization_id": organization_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "key": key,
        "value_json": {"text": "Asia/Taipei"},
        "visibility": visibility,
        "source_type": "request",
        "source_ref": "agent_request",
        "source_excerpt_hash": None,
        "created_by": created_by,
        "verification_status": "user_confirmed",
        "confidence": 1.0,
        "expires_at": expires_at or timestamp + timedelta(days=365),
        "deleted_at": deleted_at,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_remember_fact_inserts_tenant_hashed_provenance_and_default_retention() -> None:
    cursor = FakeCursor(one_rows=[_row()])
    connection = FakeConnection(cursor)
    store = PostgresMemoryStore(connection_factory=lambda: connection)
    before = datetime.now(timezone.utc)

    fact = store.remember_fact(
        organization_id="org-1",
        scope_type="user",
        scope_id="123",
        key=" timezone ",
        value_json={"text": "Asia/Taipei"},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        source_excerpt="My timezone is Asia/Taipei",
        created_by="123",
        verification_status="user_confirmed",
    )

    after = datetime.now(timezone.utc)
    assert fact.id == "0e5e5302-8d36-4bc8-954d-68332b36949b"
    assert fact.organization_id == "org-1"
    assert fact.key == "timezone"
    assert connection.cursor_kwargs is not None
    assert "row_factory" in connection.cursor_kwargs

    purge_query, purge_params = cursor.calls[0]
    assert "DELETE FROM agent_memory_facts" in purge_query
    assert purge_params is not None
    assert purge_params[0] == "org-1"

    query, params = cursor.calls[1]
    assert "INSERT INTO agent_memory_facts" in query
    assert "organization_id" in query
    assert params is not None
    UUID(str(params[0]))
    assert params[1] == "org-1"
    assert params[4] == "timezone"
    assert isinstance(params[5], Jsonb)
    assert params[5].obj == {"text": "Asia/Taipei"}
    assert params[9] == hashlib.sha256(b"My timezone is Asia/Taipei").hexdigest()
    assert before + timedelta(days=364) < params[13] < after + timedelta(days=366)


def test_list_facts_filters_by_tenant_visibility_soft_delete_and_expiry() -> None:
    now = datetime(2026, 7, 28, 9, 30, tzinfo=timezone.utc)
    cursor = FakeCursor(
        all_rows=[
            _row(
                scope_type="project",
                scope_id="project-1",
                key="preference",
                visibility="project",
            )
        ]
    )
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    facts = store.list_facts(
        organization_id="org-1",
        scope_type="project",
        scope_id="project-1",
        visible_to_user_id="123",
        visible_to_project_id="project-1",
        visible_to_org_id="org-1",
        now=now,
    )

    assert [fact.key for fact in facts] == ["preference"]
    query, params = cursor.calls[0]
    assert "WHERE organization_id = %s" in query
    assert "visibility = 'private'" in query
    assert "visibility = 'project'" in query
    assert "visibility = 'org'" in query
    assert "deleted_at IS NULL" in query
    assert "expires_at IS NULL OR expires_at > %s" in query
    assert "ORDER BY created_at ASC, id ASC" in query
    assert "LIMIT %s" in query
    assert params == (
        "org-1",
        "project",
        "project-1",
        "123",
        "project-1",
        "org-1",
        False,
        now,
        MAX_MEMORY_FACTS_PER_LIST,
    )


def test_remember_fact_validates_its_model_before_writing() -> None:
    cursor = FakeCursor(one_rows=[_row()])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(ValidationError, match="source_type"):
        store.remember_fact(
            organization_id="org-1",
            scope_type="user",
            scope_id="123",
            key="timezone",
            value_json={"text": "Asia/Taipei"},
            visibility="private",
            source_type="untrusted_external_type",
            source_ref="agent_request",
            source_excerpt=None,
            created_by="123",
            verification_status="user_confirmed",
        )

    assert cursor.calls == []


@pytest.mark.parametrize(
    "value_json",
    [
        {"api_key": "not-even-a-real-key"},
        {"text": "card number: 4111 1111 1111 1111"},
        {"text": "my password is hunter2"},
        {"text": "SSN 123-45-6789"},
    ],
)
def test_remember_fact_rejects_sensitive_value_before_database_write(
    value_json: dict[str, str],
) -> None:
    cursor = FakeCursor(one_rows=[_row()])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(ValueError, match="secrets, credentials, payment data"):
        store.remember_fact(
            organization_id="org-1",
            scope_type="user",
            scope_id="123",
            key="unsafe",
            value_json=value_json,
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            source_excerpt=None,
            created_by="123",
            verification_status="user_confirmed",
        )

    assert cursor.calls == []


def test_list_facts_can_include_soft_deleted_rows_without_expired_rows() -> None:
    now = datetime(2026, 7, 28, 9, 30, tzinfo=timezone.utc)
    cursor = FakeCursor(
        all_rows=[
            _row(
                deleted_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(days=1),
            )
        ]
    )
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    facts = store.list_facts(
        organization_id="org-1",
        scope_type="user",
        scope_id="123",
        visible_to_user_id="123",
        visible_to_project_id=None,
        visible_to_org_id="org-1",
        include_deleted=True,
        now=now,
    )

    assert facts[0].deleted_at == now - timedelta(minutes=1)
    assert cursor.calls[0][1] is not None
    assert cursor.calls[0][1][6] is True


def test_remember_fact_rejects_a_returned_row_from_another_organization() -> None:
    cursor = FakeCursor(one_rows=[_row(organization_id="org-other")])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(RuntimeError, match="violated its organization boundary"):
        store.remember_fact(
            organization_id="org-1",
            scope_type="user",
            scope_id="123",
            key="timezone",
            value_json={"text": "Asia/Taipei"},
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            source_excerpt=None,
            created_by="123",
            verification_status="user_confirmed",
        )


def test_forget_fact_locks_then_physically_deletes_creator_fact_within_tenant() -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(
        one_rows=[
            {"created_by": "123", "organization_id": "org-1"},
            _row(),
        ]
    )
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    fact = store.forget_fact(
        organization_id="org-1",
        fact_id="0e5e5302-8d36-4bc8-954d-68332b36949b",
        actor_id="123",
        now=now,
    )

    assert fact.deleted_at == now
    select_query, select_params = cursor.calls[1]
    delete_query, delete_params = cursor.calls[2]
    assert "FOR UPDATE" in select_query
    assert select_params == ("0e5e5302-8d36-4bc8-954d-68332b36949b", "org-1")
    assert "DELETE FROM agent_memory_facts" in delete_query
    assert "organization_id = %s" in delete_query
    assert delete_params == (
        "0e5e5302-8d36-4bc8-954d-68332b36949b",
        "org-1",
    )


def test_forget_fact_does_not_update_another_users_fact() -> None:
    cursor = FakeCursor(one_rows=[{"created_by": "456", "organization_id": "org-1"}])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(PermissionError, match="deleted by its creator"):
        store.forget_fact(
            organization_id="org-1",
            fact_id="0e5e5302-8d36-4bc8-954d-68332b36949b",
            actor_id="123",
        )

    assert len(cursor.calls) == 2


def test_forget_fact_hides_another_organizations_fact_from_admin() -> None:
    cursor = FakeCursor(one_rows=[{"created_by": "123", "organization_id": "org-a"}])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(KeyError, match="was not found"):
        store.forget_fact(
            organization_id="org-b",
            fact_id="0e5e5302-8d36-4bc8-954d-68332b36949b",
            actor_id="admin-1",
            actor_is_admin=True,
        )

    select_query, select_params = cursor.calls[1]
    assert "organization_id = %s" in select_query
    assert select_params == ("0e5e5302-8d36-4bc8-954d-68332b36949b", "org-b")
    assert len(cursor.calls) == 2


def test_list_facts_never_returns_another_organizations_row() -> None:
    cursor = FakeCursor(all_rows=[_row(organization_id="org-a")])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    facts = store.list_facts(
        organization_id="org-b",
        scope_type="user",
        scope_id="123",
        visible_to_user_id="123",
        visible_to_project_id=None,
        visible_to_org_id="org-b",
    )

    assert facts == []
    assert cursor.calls[0][1] is not None
    assert cursor.calls[0][1][0] == "org-b"


def test_list_facts_does_not_delete_expired_rows_on_the_read_path() -> None:
    cursor = FakeCursor()
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    assert (
        store.list_facts(
            organization_id="org-1",
            scope_type="user",
            scope_id="123",
            visible_to_user_id="123",
            visible_to_project_id=None,
            visible_to_org_id="org-1",
        )
        == []
    )

    assert len(cursor.calls) == 1
    assert "SELECT" in cursor.calls[0][0]
    assert "DELETE FROM agent_memory_facts" not in cursor.calls[0][0]


def test_list_facts_rejects_a_visible_org_that_does_not_match_the_tenant() -> None:
    cursor = FakeCursor()
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(PermissionError, match="request organization"):
        store.list_facts(
            organization_id="org-a",
            scope_type="user",
            scope_id="123",
            visible_to_user_id="123",
            visible_to_project_id=None,
            visible_to_org_id="org-b",
        )

    assert cursor.calls == []


def test_forget_fact_reports_missing_fact() -> None:
    cursor = FakeCursor(one_rows=[None])
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    with pytest.raises(KeyError, match="was not found"):
        store.forget_fact(
            organization_id="org-1",
            fact_id="0e5e5302-8d36-4bc8-954d-68332b36949b",
            actor_id="123",
        )


def test_purge_expired_is_tenant_scoped() -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(rowcount=3)
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    assert store.purge_expired(organization_id="org-1", now=now) == 3
    query, params = cursor.calls[0]
    assert "DELETE FROM agent_memory_facts" in query
    assert "organization_id = %s" in query
    assert params == ("org-1", now)


def test_global_expiry_cleanup_does_not_accept_or_return_tenant_data() -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    cursor = FakeCursor(rowcount=7)
    store = PostgresMemoryStore(connection_factory=lambda: FakeConnection(cursor))

    assert store.purge_expired_all_organizations(now=now) == 7
    query, params = cursor.calls[0]
    assert "DELETE FROM agent_memory_facts" in query
    assert "organization_id = %s" not in query
    assert "expires_at <= %s" in query
    assert params == (now,)


def test_postgres_url_is_required_without_injected_connection_factory() -> None:
    with pytest.raises(ValueError, match="postgres_url is required"):
        PostgresMemoryStore()


def test_default_postgres_connection_uses_bounded_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = FakeCursor()
    captured: dict[str, object] = {}

    def fake_connect(url: str, **kwargs: object) -> FakeConnection:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeConnection(cursor)

    monkeypatch.setattr("five08.agent.postgres_memory.connect", fake_connect)
    store = PostgresMemoryStore("postgresql://postgres:postgres@db/workflows")

    assert store.purge_expired_all_organizations() == 0
    assert captured == {
        "url": "postgresql://postgres:postgres@db/workflows",
        "kwargs": {
            "connect_timeout": 5,
            "options": "-c statement_timeout=10000",
        },
    }
