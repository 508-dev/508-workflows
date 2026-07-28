"""Unit tests for tenant-isolated in-memory agent facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from five08.agent.memory import (
    MAX_MEMORY_FACTS_PER_LIST,
    InMemoryMemoryStore,
)
from five08.agent.models import MemoryFact


def _fact(
    *,
    fact_id: str,
    organization_id: str = "org-1",
    scope_id: str = "user-1",
    key: str = "timezone",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> MemoryFact:
    timestamp = created_at or datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    return MemoryFact(
        id=fact_id,
        organization_id=organization_id,
        scope_type="user",
        scope_id=scope_id,
        key=key,
        value_json={"text": key},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        created_by=scope_id,
        verification_status="user_confirmed",
        created_at=timestamp,
        updated_at=timestamp,
        expires_at=expires_at,
        deleted_at=deleted_at,
    )


def _list_user_facts(
    store: InMemoryMemoryStore,
    *,
    organization_id: str = "org-1",
    now: datetime | None = None,
) -> list[MemoryFact]:
    return store.list_facts(
        organization_id=organization_id,
        scope_type="user",
        scope_id="user-1",
        visible_to_user_id="user-1",
        visible_to_project_id=None,
        visible_to_org_id=organization_id,
        now=now,
    )


def test_memory_fact_requires_an_organization_id() -> None:
    with pytest.raises(ValidationError, match="organization_id"):
        MemoryFact(
            scope_type="user",
            scope_id="user-1",
            key="timezone",
            value_json={"text": "Asia/Taipei"},
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            created_by="user-1",
        )


def test_in_memory_list_and_admin_delete_cannot_cross_organizations() -> None:
    org_a_fact = _fact(fact_id="fact-org-a", organization_id="org-a")
    org_b_fact = _fact(fact_id="fact-org-b", organization_id="org-b")
    store = InMemoryMemoryStore([org_a_fact, org_b_fact])

    assert _list_user_facts(store, organization_id="org-b") == [org_b_fact]

    with pytest.raises(KeyError, match="was not found"):
        store.forget_fact(
            organization_id="org-b",
            fact_id=org_a_fact.id,
            actor_id="admin-1",
            actor_is_admin=True,
        )

    assert _list_user_facts(store, organization_id="org-a") == [org_a_fact]


@pytest.mark.parametrize(
    "value_json",
    [
        {"password": "plain-text-password"},
        {"text": "Bearer abcdefghijklmnopqrstuv"},
        {"text": "my password is hunter2"},
        {"text": "secret: abcdefgh"},
        {"text": "api key abcdefghijklmnop"},
        {"text": "use token qwertyuiopasdfgh"},
        {"text": "SSN: 123-45-6789"},
        {"text": "4111 1111 1111 1111"},
        {"text": 4111111111111111},
        {"text": "-----BEGIN PRIVATE KEY-----"},
    ],
)
def test_in_memory_rejects_sensitive_values_before_storing(
    value_json: dict[str, object],
) -> None:
    store = InMemoryMemoryStore()

    with pytest.raises(ValueError, match="secrets, credentials, payment data"):
        store.remember_fact(
            organization_id="org-1",
            scope_type="user",
            scope_id="user-1",
            key="unsafe",
            value_json=value_json,
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            source_excerpt=None,
            created_by="user-1",
            verification_status="user_confirmed",
        )

    assert _list_user_facts(store) == []


@pytest.mark.parametrize(
    "text",
    [
        "password reset instructions",
        "token bucket limit",
        "the secret sauce doc",
    ],
)
def test_in_memory_accepts_benign_mentions_of_secret_like_words(text: str) -> None:
    store = InMemoryMemoryStore()

    fact = store.remember_fact(
        organization_id="org-1",
        scope_type="user",
        scope_id="user-1",
        key="safe",
        value_json={"text": text},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
    )

    assert _list_user_facts(store) == [fact]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("IBAN: GB82WEST12345698765432", True),
        ("iban de89370400440532013000", True),
        ("reference DE89370400440532013001", False),
        ("SKU12ABCDEFGHIJKL", False),
    ],
)
def test_in_memory_iban_detection_requires_a_valid_checksum(
    text: str,
    expected: bool,
) -> None:
    store = InMemoryMemoryStore()

    if expected:
        with pytest.raises(ValueError, match="payment data"):
            store.remember_fact(
                organization_id="org-1",
                scope_type="user",
                scope_id="user-1",
                key="unsafe",
                value_json={"text": text},
                visibility="private",
                source_type="request",
                source_ref="agent_request",
                source_excerpt=None,
                created_by="user-1",
                verification_status="user_confirmed",
            )
        return

    fact = store.remember_fact(
        organization_id="org-1",
        scope_type="user",
        scope_id="user-1",
        key="safe",
        value_json={"text": text},
        visibility="private",
        source_type="request",
        source_ref="agent_request",
        source_excerpt=None,
        created_by="user-1",
        verification_status="user_confirmed",
    )
    assert _list_user_facts(store) == [fact]


def test_in_memory_rejects_overlong_value_before_storing() -> None:
    store = InMemoryMemoryStore()

    with pytest.raises(ValueError, match="overlong string"):
        store.remember_fact(
            organization_id="org-1",
            scope_type="user",
            scope_id="user-1",
            key="unsafe",
            value_json={"text": "x" * 2_049},
            visibility="private",
            source_type="request",
            source_ref="agent_request",
            source_excerpt=None,
            created_by="user-1",
            verification_status="user_confirmed",
        )

    assert _list_user_facts(store) == []


def test_in_memory_list_has_a_deterministic_bounded_result_set() -> None:
    start = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    facts = [
        _fact(
            fact_id=f"fact-{index:03d}",
            key=f"key-{index:03d}",
            created_at=start + timedelta(seconds=index),
        )
        for index in range(MAX_MEMORY_FACTS_PER_LIST + 1)
    ]
    store = InMemoryMemoryStore(reversed(facts))

    listed = _list_user_facts(store, now=start)

    assert len(listed) == MAX_MEMORY_FACTS_PER_LIST
    assert [fact.id for fact in listed] == [fact.id for fact in facts[:-1]]


def test_in_memory_purge_is_tenant_scoped_and_removes_expired_or_deleted_rows() -> None:
    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    expired_org_a = _fact(
        fact_id="expired-org-a",
        organization_id="org-a",
        expires_at=now - timedelta(seconds=1),
    )
    deleted_org_a = _fact(
        fact_id="deleted-org-a",
        organization_id="org-a",
        deleted_at=now - timedelta(seconds=1),
    )
    expired_org_b = _fact(
        fact_id="expired-org-b",
        organization_id="org-b",
        expires_at=now - timedelta(seconds=1),
    )
    store = InMemoryMemoryStore([expired_org_a, deleted_org_a, expired_org_b])

    assert store.purge_expired(organization_id="org-a", now=now) == 2
    assert _list_user_facts(store, organization_id="org-a", now=now) == []
    assert store.purge_expired(organization_id="org-b", now=now) == 1


def test_forget_returns_deletion_metadata_and_removes_the_fact_immediately() -> None:
    now = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    fact = _fact(fact_id="soft-delete-me", created_at=now)
    store = InMemoryMemoryStore([fact])

    deleted = store.forget_fact(
        organization_id="org-1",
        fact_id=fact.id,
        actor_id="user-1",
        now=now,
    )

    assert deleted.id == fact.id
    assert deleted.deleted_at == now
    assert _list_user_facts(store, now=now) == []
