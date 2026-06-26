"""Unit tests for Migadu-backed newsletter audience sync."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from five08.clients.migadu import MigaduMailbox
from five08.clients.espo import EspoAPIError
from five08.newsletter_sync import (
    NewsletterSyncProcessor,
    build_newsletter_providers,
    format_newsletter_sync_warning,
    sync_newsletter_contacts,
)


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
    list_lookup_names: list[str] = []
    contact_lookup_emails: list[str] = []

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
        self.contact_lookup_emails.append(email)
        return self.contacts.get(email)

    def add_contact_to_list(self, *, email: str, list_id: int) -> dict[str, Any]:
        self.subscriptions.append({"email": email, "list_id": list_id})
        return {"id": len(self.subscriptions)}

    def find_list_id_by_name(self, name: str) -> int | None:
        self.list_lookup_names.append(name)
        return 4 if name == "508 members" else None


class FakeKeilaClient:
    contacts: dict[str, dict[str, Any]] = {}
    upserts: list[dict[str, Any]] = []
    lookups: list[str] = []

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
        self.lookups.append(email)
        return self.contacts.get(email)

    def upsert_active_contact(
        self,
        *,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        data: dict[str, Any] | None = None,
        existing_contact: dict[str, Any] | None | object = None,
    ) -> dict[str, Any]:
        self.upserts.append(
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "data": data,
                "existing_contact": existing_contact,
            }
        )
        return {"id": str(len(self.upserts))}


class FakeEspoClient:
    contacts: list[dict[str, Any]] = []
    raise_error = False
    calls: list[dict[str, Any]] = []

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
        self.calls.append(params)
        if self.raise_error:
            raise EspoAPIError("CRM unavailable")
        filter_values = {
            str(item.get("value") or "").strip().lower()
            for clause in params.get("where", [])
            if isinstance(clause, dict)
            for item in clause.get("value", [])
            if isinstance(item, dict)
        }
        if not filter_values:
            return {"list": [dict(item) for item in self.contacts]}
        matches: list[dict[str, Any]] = []
        for contact in self.contacts:
            contact_values = {
                str(contact.get("c508Email") or "").strip().lower(),
                str(contact.get("emailAddress") or "").strip().lower(),
            }
            if not any(contact_values):
                matches.append(dict(contact))
                continue
            if filter_values.intersection(value for value in contact_values if value):
                matches.append(dict(contact))
        return {"list": matches}


@pytest.fixture(autouse=True)
def reset_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMigaduClient.mailboxes = []
    FakeBrevoClient.contacts = {}
    FakeBrevoClient.subscriptions = []
    FakeBrevoClient.list_lookup_names = []
    FakeBrevoClient.contact_lookup_emails = []
    FakeKeilaClient.contacts = {}
    FakeKeilaClient.upserts = []
    FakeKeilaClient.lookups = []
    FakeEspoClient.contacts = []
    FakeEspoClient.raise_error = False
    FakeEspoClient.calls = []
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
        "newsletter_sync_excluded_mailboxes": "system",
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


def test_sync_508_members_dry_run_reports_provider_actions_without_writes() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]

    result = NewsletterSyncProcessor(_settings()).sync_508_members(dry_run=True)

    assert result["dry_run"] is True
    assert result["mailboxes_scanned"] == 1
    assert result["contacts_considered"] == 2
    assert sorted(FakeBrevoClient.contact_lookup_emails) == [
        "jane@508.dev",
        "jane@example.com",
    ]
    assert sorted(FakeKeilaClient.lookups) == ["jane@508.dev", "jane@example.com"]
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []
    assert result["providers"]["brevo"]["would_sync"] == 2
    assert result["providers"]["keila"]["would_sync"] == 2


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


def test_sync_508_members_applies_brevo_suppression_to_all_providers() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeBrevoClient.contacts = {"jane@example.com": {"emailBlacklisted": True}}

    result = NewsletterSyncProcessor(_settings()).sync_508_members()

    assert FakeBrevoClient.subscriptions == [{"email": "jane@508.dev", "list_id": 4}]
    assert [item["email"] for item in FakeKeilaClient.upserts] == ["jane@508.dev"]
    assert result["suppressed_contacts_skipped"] == 1
    assert result["providers"]["brevo"]["statuses"] == {
        "synced": 1,
        "skipped_provider_suppressed": 1,
    }
    assert result["providers"]["keila"]["statuses"] == {
        "synced": 1,
        "skipped_provider_suppressed": 1,
    }


def test_sync_508_members_does_not_treat_unreachable_as_suppressed() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email=None,
        )
    ]
    FakeKeilaClient.contacts = {
        "jane@508.dev": {"id": "contact-1", "status": "unreachable"}
    }

    result = NewsletterSyncProcessor(_settings(brevo_api_key=None)).sync_508_members()

    assert result["suppressed_contacts_skipped"] == 0
    assert result["providers"]["keila"]["statuses"] == {"synced": 1}
    assert [item["email"] for item in FakeKeilaClient.upserts] == ["jane@508.dev"]


def test_sync_508_members_allows_sms_only_brevo_blacklist() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email=None,
        )
    ]
    FakeBrevoClient.contacts = {
        "jane@508.dev": {"smsBlacklisted": True, "emailBlacklisted": False}
    }

    result = NewsletterSyncProcessor(_settings(keila_api_key=None)).sync_508_members()

    assert FakeBrevoClient.subscriptions == [{"email": "jane@508.dev", "list_id": 4}]
    assert result["providers"]["brevo"]["statuses"] == {"synced": 1}


def test_sync_508_members_skips_brevo_list_unsubscribed_contacts() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeBrevoClient.contacts = {"jane@example.com": {"listUnsubscribed": ["4"]}}

    result = NewsletterSyncProcessor(_settings()).sync_508_members()

    assert FakeBrevoClient.subscriptions == [{"email": "jane@508.dev", "list_id": 4}]
    assert result["providers"]["brevo"]["statuses"] == {
        "synced": 1,
        "skipped_provider_suppressed": 1,
    }


def test_sync_508_members_caches_brevo_list_lookup_by_name() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]

    result = NewsletterSyncProcessor(
        _settings(
            brevo_508_members_newsletter_list_id=None,
            brevo_508_members_newsletter_list_name="508 members",
            keila_api_key=None,
        )
    ).sync_508_members()

    assert result["providers"]["brevo"]["synced"] == 2
    assert FakeBrevoClient.list_lookup_names == ["508 members"]


def test_sync_508_members_skips_missing_brevo_list_before_contact_lookup() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]

    result = NewsletterSyncProcessor(
        _settings(
            brevo_508_members_newsletter_list_id=None,
            brevo_508_members_newsletter_list_name="Missing list",
            keila_api_key=None,
        )
    ).sync_508_members()

    assert result["providers"]["brevo"]["synced"] == 0
    assert result["providers"]["brevo"]["statuses"] == {"skipped_list_missing": 2}
    assert FakeBrevoClient.list_lookup_names == ["Missing list"]
    assert FakeBrevoClient.contact_lookup_emails == []


def test_sync_508_members_avoids_duplicate_keila_contact_lookups() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]

    result = NewsletterSyncProcessor(_settings(brevo_api_key=None)).sync_508_members()

    assert result["providers"]["keila"]["synced"] == 2
    assert FakeKeilaClient.lookups == [
        "jane@508.dev",
        "jane@508.dev",
        "jane@example.com",
        "jane@example.com",
    ]
    assert [item["existing_contact"] for item in FakeKeilaClient.upserts] == [
        None,
        None,
    ]


def test_sync_508_members_skips_internal_suppression_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]

    def fake_load_suppressions(
        settings: Any, emails: list[str]
    ) -> dict[str, list[Any]]:
        assert sorted(emails) == ["jane@508.dev", "jane@example.com"]
        return {"jane@example.com": [object()]}

    monkeypatch.setattr(
        "five08.newsletter_sync.load_active_newsletter_suppressions_by_email",
        fake_load_suppressions,
    )

    result = NewsletterSyncProcessor(
        _settings(postgres_url="postgresql://example/db")
    ).sync_508_members()

    assert result["suppressed_contacts_skipped"] == 1
    assert FakeBrevoClient.subscriptions == [{"email": "jane@508.dev", "list_id": 4}]
    assert [item["email"] for item in FakeKeilaClient.upserts] == ["jane@508.dev"]
    assert result["providers"]["brevo"]["statuses"] == {
        "synced": 1,
        "skipped_internal_suppression": 1,
    }


def test_sync_508_members_skips_crm_blocked_mailboxes() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeEspoClient.contacts = [
        {"id": "contact-1", "type": "Inactive Member", "c508Email": "jane@508.dev"}
    ]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_blocked_skipped"] == 1
    assert result["contacts_considered"] == 0
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []


def test_sync_508_members_skips_crm_unmatched_mailboxes_when_crm_configured() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="service@508.dev",
            name="Service Account",
            password_recovery_email="ops@example.com",
        )
    ]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_unmatched_skipped"] == 1
    assert result["contacts_considered"] == 0
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []


def test_sync_508_members_syncs_crm_matched_mailboxes_when_crm_configured() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeEspoClient.contacts = [
        {"id": "contact-1", "type": "Member", "c508Email": "jane@508.dev"}
    ]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_unmatched_skipped"] == 0
    assert result["contacts_considered"] == 2
    assert FakeBrevoClient.subscriptions == [
        {"email": "jane@508.dev", "list_id": 4},
        {"email": "jane@example.com", "list_id": 4},
    ]


def test_sync_508_members_batches_crm_lookup_for_multiple_mailboxes() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email=None,
        ),
        MigaduMailbox(
            address="john@508.dev",
            name="John Doe",
            password_recovery_email="john@example.com",
        ),
    ]
    FakeEspoClient.contacts = [
        {"id": "contact-1", "type": "Member", "c508Email": "jane@508.dev"},
        {"id": "contact-2", "type": "Member", "emailAddress": "john@example.com"},
    ]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_unmatched_skipped"] == 0
    assert result["contacts_considered"] == 3
    assert len(FakeEspoClient.calls) == 1
    crm_filters = FakeEspoClient.calls[0]["where"][0]["value"]
    assert {"type": "equals", "attribute": "c508Email", "value": "jane@508.dev"} in (
        crm_filters
    )
    assert {
        "type": "equals",
        "attribute": "emailAddress",
        "value": "john@example.com",
    } in crm_filters


def test_sync_508_members_skips_mailbox_when_any_crm_match_is_blocked() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeEspoClient.contacts = [
        {"id": "contact-1", "type": "Member", "c508Email": "jane@508.dev"},
        {"id": "contact-2", "type": "Inactive Member", "c508Email": "jane@508.dev"},
    ]

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_blocked_skipped"] == 1
    assert result["contacts_considered"] == 0
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []


def test_build_newsletter_providers_uses_default_list_name_when_blank() -> None:
    providers = build_newsletter_providers(
        _settings(
            brevo_508_members_newsletter_list_id=None,
            brevo_508_members_newsletter_list_name="   ",
        )
    )

    assert len(providers) == 2
    assert providers[0].list_name == "508 members"


def test_format_newsletter_sync_warning_reports_suppressed_skips() -> None:
    warning = format_newsletter_sync_warning(
        {
            "providers": {
                "brevo": {
                    "synced": 1,
                    "skipped": 1,
                    "failed": 0,
                    "statuses": {"skipped_provider_suppressed": 1},
                }
            }
        }
    )

    assert warning == "brevo skipped 1 suppressed contact(s)"


def test_format_newsletter_sync_warning_redacts_failure_emails() -> None:
    warning = format_newsletter_sync_warning(
        {
            "providers": {
                "keila": {
                    "failed": 1,
                    "failures": [
                        {
                            "email": "jane@example.com",
                            "error": (
                                "lookup failed for jane@example.com at "
                                "/contacts/jane%40example.com"
                            ),
                        }
                    ],
                }
            }
        }
    )

    assert warning == (
        "keila failed for 1 contact(s): lookup failed for [redacted-email] "
        "at /contacts/[redacted-email]"
    )
    assert "jane@example.com" not in warning
    assert "jane%40example.com" not in warning


def test_sync_newsletter_contacts_uses_first_email_as_default_mailbox_pointer() -> None:
    result = sync_newsletter_contacts(
        _settings(brevo_api_key=None),
        ["jane@508.dev", "jane@example.com"],
        source="test",
    )

    assert result["providers"]["keila"]["synced"] == 2
    assert [item["email"] for item in FakeKeilaClient.upserts] == [
        "jane@508.dev",
        "jane@example.com",
    ]
    assert [item["data"]["mailbox_email"] for item in FakeKeilaClient.upserts] == [
        "jane@508.dev",
        "jane@508.dev",
    ]


def test_sync_508_members_skips_mailbox_when_crm_lookup_fails() -> None:
    FakeMigaduClient.mailboxes = [
        MigaduMailbox(
            address="jane@508.dev",
            name="Jane Doe",
            password_recovery_email="jane@example.com",
        )
    ]
    FakeEspoClient.raise_error = True

    result = NewsletterSyncProcessor(
        _settings(espo_base_url="https://crm.example", espo_api_key="espo-key")
    ).sync_508_members()

    assert result["crm_lookup_failed_skipped"] == 1
    assert result["contacts_considered"] == 0
    assert result["crm_lookup_failures"] == [
        {
            "mailbox": "jane@508.dev",
            "error": "CRM contact lookup failed for jane@508.dev: CRM unavailable",
        }
    ]
    assert FakeBrevoClient.subscriptions == []
    assert FakeKeilaClient.upserts == []
