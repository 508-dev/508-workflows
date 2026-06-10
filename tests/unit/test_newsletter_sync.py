"""Unit tests for Migadu-backed newsletter audience sync."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from five08.clients.migadu import MigaduMailbox
from five08.newsletter_sync import NewsletterSyncProcessor


class FakeMigaduClient:
    mailboxes: list[MigaduMailbox] = []

    def __init__(
        self,
        *,
        username: str,
        api_key: str,
        domain: str,
    ) -> None:
        self.username = username
        self.api_key = api_key
        self.domain = domain

    def list_mailboxes(self) -> list[MigaduMailbox]:
        return list(self.mailboxes)


class FakeBrevoClient:
    contacts: dict[str, dict[str, Any]] = {}
    subscriptions: list[dict[str, Any]] = []

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.brevo.com/v3",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def get_contact(self, email: str) -> dict[str, Any] | None:
        return self.contacts.get(email)

    def add_contact_to_list(self, *, email: str, list_id: int) -> dict[str, Any]:
        self.subscriptions.append({"email": email, "list_id": list_id})
        return {"id": len(self.subscriptions)}

    def find_list_id_by_name(self, name: str) -> int | None:
        return 4 if name == "508 members" else None


class FakeKeilaClient:
    contacts: dict[str, dict[str, Any]] = {}
    upserts: list[dict[str, Any]] = []

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://app.keila.io",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def get_contact_by_email(self, email: str) -> dict[str, Any] | None:
        return self.contacts.get(email)

    def upsert_active_contact(
        self,
        *,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.upserts.append(
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "data": data,
            }
        )
        return {"id": str(len(self.upserts))}


class FakeEspoClient:
    contacts: list[dict[str, Any]] = []

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def list_contacts(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"list": [dict(item) for item in self.contacts]}


@pytest.fixture(autouse=True)
def reset_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMigaduClient.mailboxes = []
    FakeBrevoClient.contacts = {}
    FakeBrevoClient.subscriptions = []
    FakeKeilaClient.contacts = {}
    FakeKeilaClient.upserts = []
    FakeEspoClient.contacts = []
    monkeypatch.setattr("five08.newsletter_sync.MigaduClient", FakeMigaduClient)
    monkeypatch.setattr("five08.newsletter_sync.BrevoClient", FakeBrevoClient)
    monkeypatch.setattr("five08.newsletter_sync.KeilaClient", FakeKeilaClient)
    monkeypatch.setattr("five08.newsletter_sync.EspoClient", FakeEspoClient)


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "migadu_api_user": "migadu-user",
        "migadu_api_key": "migadu-key",
        "migadu_mailbox_domain": "508.dev",
        "brevo_api_key": "brevo-key",
        "brevo_508_members_newsletter_list_id": 4,
        "keila_api_key": "keila-key",
        "newsletter_sync_excluded_mailboxes": "system@508.dev",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sync_508_members_adds_mailbox_and_backup_email_to_configured_providers() -> (
    None
):
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        ),
        MigaduMailbox(
            address="system@508.dev",
            name="System",
            password_recovery_email="ops@example.com",
        ),
    ]

    result = NewsletterSyncProcessor(_settings()).sync_508_members()

    assert result["mailboxes_scanned"] == 2
    assert result["system_mailboxes_skipped"] == 1
    assert result["contacts_considered"] == 2
    assert FakeBrevoClient.subscriptions == [
        {"email": "jane@508.dev", "list_id": 4},
        {"email": "jane@example.com", "list_id": 4},
    ]
    assert [item["email"] for item in FakeKeilaClient.upserts] == [
        "jane@508.dev",
        "jane@example.com",
    ]
    assert result["providers"]["brevo"]["synced"] == 2
    assert result["providers"]["keila"]["synced"] == 2


def test_sync_508_members_skips_provider_suppressed_contacts() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeBrevoClient.contacts = {"jane@example.com": {"emailBlacklisted": True}}
    FakeKeilaClient.contacts = {"jane@example.com": {"status": "unsubscribed"}}

    result = NewsletterSyncProcessor(_settings()).sync_508_members()

    assert FakeBrevoClient.subscriptions == [{"email": "jane@508.dev", "list_id": 4}]
    assert [item["email"] for item in FakeKeilaClient.upserts] == ["jane@508.dev"]
    assert result["providers"]["brevo"]["statuses"] == {
        "synced": 1,
        "skipped_provider_suppressed": 1,
    }
    assert result["providers"]["keila"]["statuses"] == {
        "synced": 1,
        "skipped_provider_suppressed": 1,
    }


def test_sync_508_members_skips_crm_blocked_mailboxes() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeEspoClient.contacts = [{"id": "contact-1", "type": "Inactive Member"}]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_blocked_skipped"] == 1
    assert result["contacts_considered"] == 0
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []
