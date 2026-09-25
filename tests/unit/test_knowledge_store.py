"""Storage-contract tests for durable knowledge memory."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from five08.knowledge import store as knowledge_store
from five08.knowledge.store import InMemoryKnowledgeStore, PostgresKnowledgeStore


class _FakeCursor:
    def __init__(self, selected_rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.row: dict[str, Any] | None = None
        self.selected_rows = selected_rows or []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.calls.append((query, params))
        if "INSERT INTO memory_facts" not in query or params is None:
            return
        self.row = {
            "id": params[0],
            "organization_id": params[1],
            "scope_type": params[2],
            "scope_id": params[3],
            "kind": "fact",
            "key": params[4],
            "answer": params[5],
            "value_json": {"text": "Asia/Tokyo"},
            "visibility": params[7],
            "created_by": params[8],
            "verification_status": params[9],
            "confidence": params[10],
            "status": "active",
            "review_after": None,
            "expires_at": params[11],
            "deleted_at": None,
            "supersedes_id": params[15],
            "created_at": params[13],
            "updated_at": params[14],
        }

    def fetchall(self) -> list[dict[str, Any]]:
        return self.selected_rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor | None = None) -> None:
        self.cursor_instance = cursor or _FakeCursor()

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self, **_kwargs: object) -> _FakeCursor:
        return self.cursor_instance


def test_postgres_memory_adapter_preserves_retention_and_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        knowledge_store,
        "get_postgres_connection",
        lambda _settings, **_kwargs: connection,
    )
    before = datetime.now(timezone.utc)

    fact = PostgresKnowledgeStore(SimpleNamespace()).remember_fact(
        scope_type="user",
        scope_id="user-1",
        key="timezone",
        value_json={"text": "Asia/Tokyo"},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        source_excerpt="My timezone is Asia/Tokyo",
        created_by="user-1",
        verification_status="user_confirmed",
        organization_id="org-1",
    )

    insert_query, insert_params = next(
        call
        for call in connection.cursor_instance.calls
        if "INSERT INTO memory_facts" in call[0]
    )
    assert "verification_status" in insert_query
    assert "expires_at" in insert_query
    assert "normalized_verification" not in insert_query
    assert "effective_expires_at" not in insert_query
    assert insert_params is not None
    assert insert_params[11] > before
    assert fact.expires_at == insert_params[11]
    assert fact.source_excerpt_hash is not None


def test_postgres_memory_adapter_rejects_oversized_key_before_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        knowledge_store,
        "get_postgres_connection",
        lambda _settings, **_kwargs: connection,
    )

    with pytest.raises(ValueError, match="at most 128"):
        PostgresKnowledgeStore(SimpleNamespace()).remember_fact(
            scope_type="user",
            scope_id="user-1",
            key="x" * 129,
            value_json={"text": "value"},
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            source_excerpt="value",
            created_by="user-1",
            verification_status="user_confirmed",
            organization_id="org-1",
        )

    assert connection.cursor_instance.calls == []


def test_in_memory_edit_merges_an_occupied_destination_slot() -> None:
    store = InMemoryKnowledgeStore()
    first = store.remember_fact(
        scope_type="user",
        scope_id="user-1",
        key="timezone",
        value_json={"text": "Asia/Tokyo"},
        visibility="private",
        source_type="request",
        source_ref="first",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
        organization_id="org-1",
    )
    destination = store.remember_fact(
        scope_type="user",
        scope_id="user-1",
        key="location",
        value_json={"text": "Tokyo"},
        visibility="private",
        source_type="request",
        source_ref="destination",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
        organization_id="org-1",
    )

    edited = store.remember_fact(
        scope_type="user",
        scope_id="user-1",
        key="location",
        value_json={"text": "Tokyo"},
        visibility="private",
        source_type="request",
        source_ref="edit",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
        organization_id="org-1",
        replaces_id=first.id,
    )

    active = store.list_facts(
        scope_type="user",
        scope_id="user-1",
        visible_to_user_id="user-1",
        visible_to_project_id=None,
        visible_to_org_id="org-1",
    )
    assert edited.supersedes_id == first.id
    assert [fact.id for fact in active] == [edited.id]
    assert destination.id != edited.id


def test_postgres_edit_selects_replaced_and_destination_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replaced_id = "11111111-1111-1111-1111-111111111111"
    destination_id = "22222222-2222-2222-2222-222222222222"
    cursor = _FakeCursor(
        selected_rows=[
            {"id": replaced_id, "created_by": "user-1"},
            {"id": destination_id, "created_by": "user-1"},
        ]
    )
    connection = _FakeConnection(cursor)
    monkeypatch.setattr(
        knowledge_store,
        "get_postgres_connection",
        lambda _settings, **_kwargs: connection,
    )
    fact = PostgresKnowledgeStore(SimpleNamespace()).remember_fact(
        scope_type="user",
        scope_id="user-1",
        key="location",
        value_json={"text": "Tokyo"},
        visibility="private",
        source_type="request",
        source_ref="edit",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
        organization_id="org-1",
        replaces_id=replaced_id,
    )

    select_query, select_params = next(
        call for call in cursor.calls if "SELECT * FROM memory_facts" in call[0]
    )
    assert "%s::uuid IS NULL AND kind = 'fact'" not in select_query
    assert select_params is not None
    assert select_params[3:5] == (replaced_id, replaced_id)
    update_params = next(
        params
        for query, params in cursor.calls
        if "UPDATE memory_facts SET status = 'superseded'" in query
    )
    assert update_params is not None
    assert update_params[1] == [replaced_id, destination_id]
    assert fact.supersedes_id == replaced_id


def test_postgres_semantic_fallback_honors_smaller_candidate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(
        knowledge_store,
        "get_postgres_connection",
        lambda _settings, **_kwargs: connection,
    )

    evidence = PostgresKnowledgeStore(SimpleNamespace()).search_evidence(
        question="wesbite depoly",
        organization_id="org-1",
        actor_id="user-1",
        allow_private=False,
        allow_project=False,
        allow_org=True,
        limit=8,
        semantic_candidate_limit=4,
    )

    fallback_query, fallback_params = next(
        call
        for call in connection.cursor_instance.calls
        if "0.0::float AS relevance" in call[0]
    )
    assert fallback_query
    assert fallback_params is not None
    assert fallback_params[-1] == 4
    assert evidence == []
