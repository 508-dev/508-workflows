"""Storage-contract tests for durable knowledge memory."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from five08.knowledge import store as knowledge_store
from five08.knowledge.store import PostgresKnowledgeStore


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []
        self.row: dict[str, Any] | None = None
        self.rowcount = 0

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
            "supersedes_id": None,
            "created_at": params[13],
            "updated_at": params[14],
        }

    def fetchall(self) -> list[dict[str, Any]]:
        return []

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

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


def test_postgres_memory_cleanup_targets_the_unified_memory_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    connection.cursor_instance.rowcount = 3
    monkeypatch.setattr(
        knowledge_store,
        "get_postgres_connection",
        lambda _settings, **_kwargs: connection,
    )
    now = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)

    purged = PostgresKnowledgeStore(SimpleNamespace()).purge_expired_all_organizations(
        now=now
    )

    query, params = connection.cursor_instance.calls[-1]
    assert "DELETE FROM memory_facts" in query
    assert "status = 'deleted'" in query
    assert params == (now,)
    assert purged == 3
