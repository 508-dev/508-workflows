"""508 member newsletter audience synchronization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from typing import Any, Iterable, Protocol

from five08.clients.brevo import BrevoClient
from five08.clients.espo import EspoAPIError, EspoClient
from five08.clients.keila import KeilaClient
from five08.clients.migadu import MigaduClient, MigaduMailbox
from five08.newsletter_suppressions import (
    NewsletterSuppressionRecord,
    deactivate_newsletter_suppression,
    load_active_newsletter_suppressions_by_email,
    upsert_newsletter_suppression,
)
from five08.redaction import redact_email_addresses

CRM_BLOCKED_TYPES = {"inactive member", "rejected", "blocked"}
CRM_BLOCKED_ONBOARDING_STATES = {"rejected", "waitlist"}
PROVIDER_SUPPRESSED_STATUSES = {"unsubscribed", "blocked"}
CRM_LOOKUP_BATCH_SIZE = 10
CRM_LOOKUP_BATCH_MAX_SIZE = 200
DRY_RUN_PROVIDER_PREVIEW_MAX_WORKERS = 8


class CRMContactLookupError(RuntimeError):
    """Raised when CRM block-state lookup fails during member sync."""


@dataclass(frozen=True, slots=True)
class NewsletterContact:
    """One email address derived from one Migadu mailbox."""

    email: str
    mailbox_email: str
    name: str
    source: str


@dataclass(frozen=True, slots=True)
class NewsletterSuppressionSignal:
    """One provider-observed suppression for one email address."""

    email: str
    source_provider: str
    reason: str
    metadata: dict[str, Any]


class NewsletterProvider(Protocol):
    """Provider interface for additive newsletter subscription sync."""

    name: str

    def ensure_contact(self, contact: NewsletterContact) -> str:
        """Ensure contact exists, returning a sync status key."""

    def preview_contact(self, contact: NewsletterContact) -> str:
        """Return the sync status key that would be produced without writing."""

    def get_suppression(
        self, contact: NewsletterContact
    ) -> NewsletterSuppressionSignal | None:
        """Return provider suppression state without changing the contact."""


def _split_name(full_name: str) -> tuple[str | None, str | None]:
    normalized = full_name.strip()
    if not normalized:
        return None, None
    parts = normalized.rsplit(" ", 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _normalized_emails_for_mailbox(mailbox: MigaduMailbox) -> list[NewsletterContact]:
    contacts = [
        NewsletterContact(
            email=mailbox.address,
            mailbox_email=mailbox.address,
            name=mailbox.name,
            source="migadu_mailbox",
        )
    ]
    if mailbox.password_recovery_email:
        contacts.append(
            NewsletterContact(
                email=mailbox.password_recovery_email,
                mailbox_email=mailbox.address,
                name=mailbox.name,
                source="migadu_password_recovery_email",
            )
        )
    deduped: list[NewsletterContact] = []
    seen: set[str] = set()
    for contact in contacts:
        if contact.email in seen:
            continue
        seen.add(contact.email)
        deduped.append(contact)
    return deduped


def _normalized_csv_set(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def _mailbox_local_part(email: str) -> str:
    return email.split("@", 1)[0].strip().lower()


def _is_mailbox_excluded(email: str, excluded_mailboxes: set[str]) -> bool:
    normalized_email = email.strip().lower()
    if normalized_email in excluded_mailboxes:
        return True
    local_part = _mailbox_local_part(normalized_email)
    return bool(local_part and local_part in excluded_mailboxes)


def _is_crm_blocked(contact: dict[str, Any] | None) -> bool:
    if contact is None:
        return False
    contact_type = str(contact.get("type") or "").strip().casefold()
    onboarding = str(contact.get("cOnboardingState") or "").strip().casefold()
    return (
        contact_type in CRM_BLOCKED_TYPES or onboarding in CRM_BLOCKED_ONBOARDING_STATES
    )


def _contains_list_id(value: object, list_id: int) -> bool:
    if not isinstance(value, list):
        return False
    return any(str(item).strip() == str(list_id) for item in value)


def _joined_reason(reasons: list[str]) -> str:
    return "+".join(sorted(set(reasons)))


def _provider_suppression_sources(
    suppressions: list[NewsletterSuppressionSignal],
) -> set[str]:
    return {suppression.source_provider for suppression in suppressions}


def _reconcile_registry_suppressions(
    settings: Any,
    *,
    contact: NewsletterContact,
    active_suppressions: list[NewsletterSuppressionRecord],
    provider_suppressions: list[NewsletterSuppressionSignal],
    checked_provider_names: set[str],
    result: dict[str, Any],
) -> list[NewsletterSuppressionRecord]:
    """Clear stale provider-observed suppressions after a clean provider check."""
    if not active_suppressions:
        return []
    if not bool(str(getattr(settings, "postgres_url", "") or "").strip()):
        return active_suppressions

    provider_sources = _provider_suppression_sources(provider_suppressions)
    remaining: list[NewsletterSuppressionRecord] = []
    for record in active_suppressions:
        if record.source_provider == "manual":
            remaining.append(record)
            continue
        if record.source_provider not in checked_provider_names:
            remaining.append(record)
            continue
        if record.source_provider in provider_sources:
            remaining.append(record)
            continue
        try:
            changed = deactivate_newsletter_suppression(
                settings,
                email=contact.email,
                source_provider=record.source_provider,
            )
        except Exception as exc:
            result["suppression_registry_warning"] = redact_email_addresses(str(exc))
            remaining.append(record)
            continue
        if changed:
            result["suppression_registry_deactivated"] = (
                int(result.get("suppression_registry_deactivated") or 0) + 1
            )
    return remaining


def _chunks[T](items: list[T], size: int) -> Iterable[list[T]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _mailbox_crm_lookup_values(mailbox: MigaduMailbox) -> set[str]:
    values = {mailbox.address.strip().lower()}
    if mailbox.password_recovery_email:
        values.add(mailbox.password_recovery_email.strip().lower())
    return {value for value in values if value}


def _crm_filters_for_mailboxes(mailboxes: list[MigaduMailbox]) -> list[dict[str, str]]:
    filters: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for mailbox in mailboxes:
        for attribute, value in (
            ("c508Email", mailbox.address),
            ("emailAddress", mailbox.address),
        ):
            normalized = value.strip().lower()
            key = (attribute, normalized)
            if normalized and key not in seen:
                filters.append(
                    {"type": "equals", "attribute": attribute, "value": normalized}
                )
                seen.add(key)
        if mailbox.password_recovery_email:
            normalized = mailbox.password_recovery_email.strip().lower()
            key = ("emailAddress", normalized)
            if normalized and key not in seen:
                filters.append(
                    {
                        "type": "equals",
                        "attribute": "emailAddress",
                        "value": normalized,
                    }
                )
                seen.add(key)
    return filters


def _contact_matches_mailbox(contact: dict[str, Any], mailbox: MigaduMailbox) -> bool:
    mailbox_values = _mailbox_crm_lookup_values(mailbox)
    contact_values = {
        str(contact.get("c508Email") or "").strip().lower(),
        str(contact.get("emailAddress") or "").strip().lower(),
    }
    return bool(mailbox_values.intersection(value for value in contact_values if value))


class BrevoNewsletterProvider:
    """Brevo implementation for the 508 members newsletter list."""

    name = "brevo"

    def __init__(
        self,
        client: BrevoClient,
        *,
        list_id: int | None,
        list_name: str,
    ) -> None:
        self.client = client
        self.list_id = list_id
        self.list_name = list_name
        self._list_id_lookup_completed = list_id is not None
        self._resolved_list_id = list_id
        self._list_id_lookup_lock = Lock()
        self._contact_lookup_lock = Lock()
        self._contact_cache: dict[str, dict[str, Any] | None] = {}

    def _list_id(self) -> int | None:
        if not self._list_id_lookup_completed:
            with self._list_id_lookup_lock:
                if not self._list_id_lookup_completed:
                    self._resolved_list_id = self.client.find_list_id_by_name(
                        self.list_name
                    )
                    self._list_id_lookup_completed = True
        return self._resolved_list_id

    def ensure_contact(self, contact: NewsletterContact) -> str:
        return self._sync_contact(contact, write=True)

    def preview_contact(self, contact: NewsletterContact) -> str:
        return self._sync_contact(contact, write=False)

    def get_suppression(
        self, contact: NewsletterContact
    ) -> NewsletterSuppressionSignal | None:
        list_id = self._list_id()
        if list_id is None:
            return None
        existing = self._get_existing_contact(contact.email)
        return self._suppression_from_existing(contact, existing, list_id)

    def _get_existing_contact(self, email: str) -> dict[str, Any] | None:
        normalized_email = email.strip().lower()
        with self._contact_lookup_lock:
            if normalized_email in self._contact_cache:
                return self._contact_cache[normalized_email]
        existing = self.client.get_contact(normalized_email)
        with self._contact_lookup_lock:
            self._contact_cache[normalized_email] = existing
        return existing

    def _suppression_from_existing(
        self,
        contact: NewsletterContact,
        existing: dict[str, Any] | None,
        list_id: int,
    ) -> NewsletterSuppressionSignal | None:
        if existing is None:
            return None
        reasons: list[str] = []
        if bool(existing.get("emailBlacklisted")):
            reasons.append("email_blacklisted")
        status = str(existing.get("status") or "").strip().casefold()
        if status in PROVIDER_SUPPRESSED_STATUSES:
            reasons.append(f"status_{status}")
        if _contains_list_id(existing.get("listUnsubscribed"), list_id):
            reasons.append("list_unsubscribed")
        if not reasons:
            return None
        return NewsletterSuppressionSignal(
            email=contact.email,
            source_provider=self.name,
            reason=_joined_reason(reasons),
            metadata={"list_id": list_id, "reasons": reasons},
        )

    def _sync_contact(self, contact: NewsletterContact, *, write: bool) -> str:
        list_id = self._list_id()
        if list_id is None:
            return "skipped_list_missing"

        existing = self._get_existing_contact(contact.email)
        if self._suppression_from_existing(contact, existing, list_id) is not None:
            return "skipped_provider_suppressed"
        if not write:
            return "would_sync"
        self.client.add_contact_to_list(email=contact.email, list_id=list_id)
        return "synced"


class KeilaNewsletterProvider:
    """Keila implementation using project contacts and contact data tags."""

    name = "keila"

    def __init__(self, client: KeilaClient) -> None:
        self.client = client
        self._contact_lookup_lock = Lock()
        self._contact_cache: dict[str, dict[str, Any] | None] = {}

    def ensure_contact(self, contact: NewsletterContact) -> str:
        return self._sync_contact(contact, write=True)

    def preview_contact(self, contact: NewsletterContact) -> str:
        return self._sync_contact(contact, write=False)

    def get_suppression(
        self, contact: NewsletterContact
    ) -> NewsletterSuppressionSignal | None:
        existing = self._get_existing_contact(contact.email)
        return self._suppression_from_existing(contact, existing)

    def _get_existing_contact(self, email: str) -> dict[str, Any] | None:
        normalized_email = email.strip().lower()
        with self._contact_lookup_lock:
            if normalized_email in self._contact_cache:
                return self._contact_cache[normalized_email]
        existing = self.client.get_contact_by_email(normalized_email)
        with self._contact_lookup_lock:
            self._contact_cache[normalized_email] = existing
        return existing

    def _suppression_from_existing(
        self,
        contact: NewsletterContact,
        existing: dict[str, Any] | None,
    ) -> NewsletterSuppressionSignal | None:
        if existing is None:
            return None
        status = str(existing.get("status") or "").strip().casefold()
        if status not in PROVIDER_SUPPRESSED_STATUSES:
            return None
        return NewsletterSuppressionSignal(
            email=contact.email,
            source_provider=self.name,
            reason=f"status_{status}",
            metadata={"status": status},
        )

    def _sync_contact(self, contact: NewsletterContact, *, write: bool) -> str:
        existing = self._get_existing_contact(contact.email)
        if self._suppression_from_existing(contact, existing) is not None:
            return "skipped_provider_suppressed"

        first_name, last_name = _split_name(contact.name)
        if not write:
            return "would_sync"
        self.client.upsert_active_contact(
            email=contact.email,
            first_name=first_name,
            last_name=last_name,
            data={
                "audiences": ["508_members"],
                "source": contact.source,
                "mailbox_email": contact.mailbox_email,
            },
            existing_contact=existing,
        )
        return "synced"


class NewsletterSyncProcessor:
    """Synchronize Migadu member mailboxes into configured newsletter providers."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.excluded_mailboxes = _normalized_csv_set(
            settings.newsletter_sync_excluded_mailboxes
        )

    def sync_508_members(self, *, dry_run: bool = False) -> dict[str, Any]:
        providers = build_newsletter_providers(self.settings)
        synced_key = "would_sync" if dry_run else "synced"
        result: dict[str, Any] = {
            "mailboxes_scanned": 0,
            "system_mailboxes_skipped": 0,
            "crm_blocked_skipped": 0,
            "crm_unmatched_skipped": 0,
            "crm_lookup_failed_skipped": 0,
            "contacts_considered": 0,
            "suppressed_contacts_skipped": 0,
            "suppression_lookup_failed_skipped": 0,
            "providers": {
                provider.name: {synced_key: 0, "skipped": 0, "failed": 0}
                for provider in providers
            },
        }
        if dry_run:
            result["dry_run"] = True
        if not providers:
            result["warning"] = "no_newsletter_providers_configured"
            return result

        crm_lookup_enabled = self._crm_lookup_enabled()
        eligible_mailboxes: list[MigaduMailbox] = []
        for mailbox in self._migadu_client().list_mailboxes():
            result["mailboxes_scanned"] += 1
            if _is_mailbox_excluded(mailbox.address, self.excluded_mailboxes):
                result["system_mailboxes_skipped"] += 1
                continue
            eligible_mailboxes.append(mailbox)

        crm_contacts_by_mailbox, crm_lookup_failures = self._list_crm_contacts_batch(
            eligible_mailboxes
        )
        sync_contacts: list[NewsletterContact] = []
        for mailbox in eligible_mailboxes:
            mailbox_key = mailbox.address.strip().lower()
            crm_lookup_error = crm_lookup_failures.get(mailbox_key)
            if crm_lookup_error is not None:
                result["crm_lookup_failed_skipped"] += 1
                failures = result.setdefault("crm_lookup_failures", [])
                if isinstance(failures, list) and len(failures) < 20:
                    failures.append(
                        {"mailbox": mailbox.address, "error": str(crm_lookup_error)}
                    )
                continue
            crm_contacts = crm_contacts_by_mailbox.get(mailbox_key, [])
            if crm_lookup_enabled and not crm_contacts:
                result["crm_unmatched_skipped"] += 1
                continue
            if any(_is_crm_blocked(contact) for contact in crm_contacts):
                result["crm_blocked_skipped"] += 1
                continue

            for contact in _normalized_emails_for_mailbox(mailbox):
                result["contacts_considered"] += 1
                sync_contacts.append(contact)
        if dry_run and sync_contacts:
            self._preview_provider_contacts(
                contacts=sync_contacts,
                providers=providers,
                result=result,
                synced_key=synced_key,
            )
            return result

        registry_suppressions = self._load_registry_suppressions(sync_contacts, result)
        for contact in sync_contacts:
            active_suppressions = list(
                registry_suppressions.get(contact.email.strip().lower(), [])
            )
            (
                provider_suppressions,
                failed_provider_names,
                checked_provider_names,
            ) = self._provider_suppressions(
                contact,
                providers,
                result,
                synced_key,
            )
            if failed_provider_names:
                result["suppression_lookup_failed_skipped"] += 1
                for provider in providers:
                    if provider.name in failed_provider_names:
                        continue
                    self._apply_provider_status(
                        result,
                        provider.name,
                        contact.email,
                        synced_key,
                        status="skipped_suppression_lookup_failed",
                    )
                continue
            self._record_provider_suppressions(provider_suppressions, result)
            active_suppressions = _reconcile_registry_suppressions(
                self.settings,
                contact=contact,
                active_suppressions=active_suppressions,
                provider_suppressions=provider_suppressions,
                checked_provider_names=checked_provider_names,
                result=result,
            )
            if active_suppressions or provider_suppressions:
                result["suppressed_contacts_skipped"] += 1
                status = (
                    "skipped_provider_suppressed"
                    if provider_suppressions
                    else "skipped_internal_suppression"
                )
                for provider in providers:
                    self._apply_provider_status(
                        result,
                        provider.name,
                        contact.email,
                        synced_key,
                        status=status,
                    )
                continue
            for provider in providers:
                try:
                    status = provider.ensure_contact(contact)
                except Exception as exc:
                    self._apply_provider_status(
                        result,
                        provider.name,
                        contact.email,
                        synced_key,
                        error=exc,
                    )
                    continue
                self._apply_provider_status(
                    result,
                    provider.name,
                    contact.email,
                    synced_key,
                    status=status,
                )
        return result

    def _suppression_registry_enabled(self) -> bool:
        return bool(str(getattr(self.settings, "postgres_url", "") or "").strip())

    def _load_registry_suppressions(
        self,
        contacts: list[NewsletterContact],
        result: dict[str, Any],
    ) -> dict[str, list[NewsletterSuppressionRecord]]:
        if not self._suppression_registry_enabled() or not contacts:
            return {}
        try:
            return load_active_newsletter_suppressions_by_email(
                self.settings,
                [contact.email for contact in contacts],
            )
        except Exception as exc:
            result["suppression_registry_warning"] = redact_email_addresses(str(exc))
            return {}

    def _provider_suppressions(
        self,
        contact: NewsletterContact,
        providers: list[NewsletterProvider],
        result: dict[str, Any],
        synced_key: str,
    ) -> tuple[list[NewsletterSuppressionSignal], set[str], set[str]]:
        suppressions: list[NewsletterSuppressionSignal] = []
        failed_provider_names: set[str] = set()
        checked_provider_names: set[str] = set()
        for provider in providers:
            try:
                suppression = provider.get_suppression(contact)
            except Exception as exc:
                failed_provider_names.add(provider.name)
                self._apply_provider_status(
                    result,
                    provider.name,
                    contact.email,
                    synced_key,
                    error=exc,
                )
                continue
            checked_provider_names.add(provider.name)
            if suppression is not None:
                suppressions.append(suppression)
        return suppressions, failed_provider_names, checked_provider_names

    def _record_provider_suppressions(
        self,
        suppressions: list[NewsletterSuppressionSignal],
        result: dict[str, Any],
    ) -> None:
        if not suppressions or not self._suppression_registry_enabled():
            return
        recorded = 0
        failed = 0
        for suppression in suppressions:
            try:
                upsert_newsletter_suppression(
                    self.settings,
                    email=suppression.email,
                    source_provider=suppression.source_provider,
                    reason=suppression.reason,
                    metadata=suppression.metadata,
                )
            except Exception as exc:
                failed += 1
                result["suppression_registry_warning"] = redact_email_addresses(
                    str(exc)
                )
                continue
            recorded += 1
        result["suppression_registry_recorded"] = (
            int(result.get("suppression_registry_recorded") or 0) + recorded
        )
        if failed:
            result["suppression_registry_failed"] = (
                int(result.get("suppression_registry_failed") or 0) + failed
            )

    def _apply_provider_status(
        self,
        result: dict[str, Any],
        provider_name: str,
        email: str,
        synced_key: str,
        status: str | None = None,
        error: Exception | None = None,
    ) -> None:
        provider_result = result["providers"][provider_name]
        if error is not None:
            provider_result["failed"] += 1
            failures = provider_result.setdefault("failures", [])
            if isinstance(failures, list) and len(failures) < 20:
                failures.append({"email": email, "error": str(error)})
            return
        if status in {"synced", "would_sync"}:
            provider_result[synced_key] += 1
        else:
            provider_result["skipped"] += 1
        statuses = provider_result.setdefault("statuses", {})
        if isinstance(statuses, dict) and status is not None:
            statuses[status] = int(statuses.get(status, 0)) + 1

    def _preview_provider_contacts(
        self,
        *,
        contacts: list[NewsletterContact],
        providers: list[NewsletterProvider],
        result: dict[str, Any],
        synced_key: str,
    ) -> None:
        work_count = len(contacts) * len(providers)
        max_workers = max(1, min(DRY_RUN_PROVIDER_PREVIEW_MAX_WORKERS, work_count))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(provider.preview_contact, contact): (
                    provider.name,
                    contact.email,
                )
                for contact in contacts
                for provider in providers
            }
            for future in as_completed(futures):
                provider_name, email = futures[future]
                try:
                    status = future.result()
                except Exception as exc:
                    self._apply_provider_status(
                        result,
                        provider_name,
                        email,
                        synced_key,
                        error=exc,
                    )
                    continue
                self._apply_provider_status(
                    result,
                    provider_name,
                    email,
                    synced_key,
                    status=status,
                )

    def _migadu_client(self) -> MigaduClient:
        return MigaduClient(
            username=_required(self.settings.migadu_api_user, "MIGADU_API_USER"),
            api_key=_required(self.settings.migadu_api_key, "MIGADU_API_KEY"),
            domain=self.settings.migadu_mailbox_domain,
        )

    def _crm_client(self) -> EspoClient | None:
        base_url = getattr(self.settings, "espo_base_url", None)
        api_key = getattr(self.settings, "espo_api_key", None)
        if not base_url or not api_key:
            return None
        return EspoClient(base_url, api_key)

    def _crm_lookup_enabled(self) -> bool:
        return self._crm_client() is not None

    def _list_crm_contacts(self, mailbox: MigaduMailbox) -> list[dict[str, Any]]:
        client = self._crm_client()
        if client is None:
            return []
        return self._list_crm_contacts_for_mailbox(client, mailbox)

    def _list_crm_contacts_for_mailbox(
        self,
        client: EspoClient,
        mailbox: MigaduMailbox,
    ) -> list[dict[str, Any]]:
        filters: list[dict[str, Any]] = [
            {"type": "equals", "attribute": "c508Email", "value": mailbox.address},
            {"type": "equals", "attribute": "emailAddress", "value": mailbox.address},
        ]
        if mailbox.password_recovery_email:
            filters.append(
                {
                    "type": "equals",
                    "attribute": "emailAddress",
                    "value": mailbox.password_recovery_email,
                }
            )
        try:
            response = client.list_contacts(
                {
                    "where": [{"type": "or", "value": filters}],
                    "maxSize": 20,
                    "select": "id,name,emailAddress,c508Email,type,cOnboardingState",
                }
            )
        except EspoAPIError as exc:
            raise CRMContactLookupError(
                f"CRM contact lookup failed for {mailbox.address}: {exc}"
            ) from exc
        contacts = response.get("list", [])
        if not isinstance(contacts, list):
            return []
        return [contact for contact in contacts if isinstance(contact, dict)]

    def _list_crm_contacts_batch(
        self,
        mailboxes: list[MigaduMailbox],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, CRMContactLookupError]]:
        client = self._crm_client()
        if client is None or not mailboxes:
            return {}, {}

        contacts_by_mailbox: dict[str, list[dict[str, Any]]] = {
            mailbox.address.strip().lower(): [] for mailbox in mailboxes
        }
        failures: dict[str, CRMContactLookupError] = {}
        for chunk in _chunks(mailboxes, CRM_LOOKUP_BATCH_SIZE):
            try:
                contacts = self._list_crm_contacts_for_mailboxes(client, chunk)
            except CRMContactLookupError:
                for mailbox in chunk:
                    mailbox_key = mailbox.address.strip().lower()
                    try:
                        contacts_by_mailbox[mailbox_key] = (
                            self._list_crm_contacts_for_mailbox(client, mailbox)
                        )
                    except CRMContactLookupError as exc:
                        failures[mailbox_key] = exc
                continue
            for mailbox in chunk:
                mailbox_key = mailbox.address.strip().lower()
                contacts_by_mailbox[mailbox_key] = [
                    contact
                    for contact in contacts
                    if _contact_matches_mailbox(contact, mailbox)
                ]
        return contacts_by_mailbox, failures

    def _list_crm_contacts_for_mailboxes(
        self,
        client: EspoClient,
        mailboxes: list[MigaduMailbox],
    ) -> list[dict[str, Any]]:
        filters = _crm_filters_for_mailboxes(mailboxes)
        if not filters:
            return []
        try:
            response = client.list_contacts(
                {
                    "where": [{"type": "or", "value": filters}],
                    "maxSize": CRM_LOOKUP_BATCH_MAX_SIZE,
                    "select": "id,name,emailAddress,c508Email,type,cOnboardingState",
                }
            )
        except EspoAPIError as exc:
            raise CRMContactLookupError(
                f"CRM contact batch lookup failed: {exc}"
            ) from exc
        contacts = response.get("list", [])
        if not isinstance(contacts, list):
            return []
        return [contact for contact in contacts if isinstance(contact, dict)]


def _required(value: str | None, name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required.")
    return normalized


def build_newsletter_providers(settings: Any) -> list[NewsletterProvider]:
    """Build configured newsletter providers from shared-like settings."""
    providers: list[NewsletterProvider] = []
    brevo_api_key = str(getattr(settings, "brevo_api_key", "") or "").strip()
    if brevo_api_key:
        list_name = (
            str(
                getattr(
                    settings, "brevo_508_members_newsletter_list_name", "508 members"
                )
                or ""
            ).strip()
            or "508 members"
        )
        providers.append(
            BrevoNewsletterProvider(
                BrevoClient(
                    api_key=brevo_api_key,
                    base_url=getattr(
                        settings, "brevo_api_base_url", "https://api.brevo.com/v3"
                    ),
                    timeout_seconds=getattr(
                        settings, "brevo_api_timeout_seconds", 20.0
                    ),
                ),
                list_id=getattr(settings, "brevo_508_members_newsletter_list_id", None),
                list_name=list_name,
            )
        )

    keila_api_key = str(getattr(settings, "keila_api_key", "") or "").strip()
    if keila_api_key:
        providers.append(
            KeilaNewsletterProvider(
                KeilaClient(
                    api_key=keila_api_key,
                    base_url=getattr(
                        settings, "keila_api_base_url", "https://app.keila.io"
                    ),
                    timeout_seconds=getattr(
                        settings, "keila_api_timeout_seconds", 20.0
                    ),
                )
            )
        )
    return providers


def sync_newsletter_contacts(
    settings: Any,
    emails: Iterable[str],
    *,
    name: str = "",
    mailbox_email: str | None = None,
    source: str = "account_creation",
) -> dict[str, Any]:
    """Best-effort additive sync for known member emails at creation time."""
    providers = build_newsletter_providers(settings)
    result: dict[str, Any] = {
        "contacts_considered": 0,
        "suppression_lookup_failed_skipped": 0,
        "providers": {
            provider.name: {"synced": 0, "skipped": 0, "failed": 0}
            for provider in providers
        },
    }
    if not providers:
        result["warning"] = "no_newsletter_providers_configured"
        return result

    seen: set[str] = set()
    default_mailbox_email = (mailbox_email or "").strip().lower()
    contacts: list[NewsletterContact] = []
    for email in emails:
        normalized_email = email.strip().lower()
        if not normalized_email or normalized_email in seen:
            continue
        seen.add(normalized_email)
        if not default_mailbox_email:
            default_mailbox_email = normalized_email
        result["contacts_considered"] += 1
        contacts.append(
            NewsletterContact(
                email=normalized_email,
                mailbox_email=default_mailbox_email,
                name=name,
                source=source,
            )
        )

    registry_suppressions: dict[str, list[NewsletterSuppressionRecord]] = {}
    if bool(str(getattr(settings, "postgres_url", "") or "").strip()) and contacts:
        try:
            registry_suppressions = load_active_newsletter_suppressions_by_email(
                settings,
                [contact.email for contact in contacts],
            )
        except Exception as exc:
            result["suppression_registry_warning"] = redact_email_addresses(str(exc))

    for contact in contacts:
        provider_suppressions: list[NewsletterSuppressionSignal] = []
        failed_provider_names: set[str] = set()
        checked_provider_names: set[str] = set()
        for provider in providers:
            provider_result = result["providers"][provider.name]
            try:
                suppression = provider.get_suppression(contact)
            except Exception as exc:
                failed_provider_names.add(provider.name)
                provider_result["failed"] += 1
                failures = provider_result.setdefault("failures", [])
                if isinstance(failures, list) and len(failures) < 20:
                    failures.append({"email": contact.email, "error": str(exc)})
                continue
            checked_provider_names.add(provider.name)
            if suppression is not None:
                provider_suppressions.append(suppression)
        if failed_provider_names:
            result["suppression_lookup_failed_skipped"] = (
                int(result.get("suppression_lookup_failed_skipped") or 0) + 1
            )
            for provider in providers:
                if provider.name in failed_provider_names:
                    continue
                _apply_direct_provider_status(
                    result,
                    provider.name,
                    contact.email,
                    status="skipped_suppression_lookup_failed",
                )
            continue
        if provider_suppressions:
            for suppression in provider_suppressions:
                if bool(str(getattr(settings, "postgres_url", "") or "").strip()):
                    try:
                        upsert_newsletter_suppression(
                            settings,
                            email=suppression.email,
                            source_provider=suppression.source_provider,
                            reason=suppression.reason,
                            metadata=suppression.metadata,
                        )
                    except Exception as exc:
                        result["suppression_registry_warning"] = redact_email_addresses(
                            str(exc)
                        )

        active_suppressions = registry_suppressions.get(
            contact.email.strip().lower(), []
        )
        active_suppressions = _reconcile_registry_suppressions(
            settings,
            contact=contact,
            active_suppressions=list(active_suppressions),
            provider_suppressions=provider_suppressions,
            checked_provider_names=checked_provider_names,
            result=result,
        )
        if provider_suppressions or active_suppressions:
            result["suppressed_contacts_skipped"] = (
                int(result.get("suppressed_contacts_skipped") or 0) + 1
            )
            status = (
                "skipped_provider_suppressed"
                if provider_suppressions
                else "skipped_internal_suppression"
            )
            for provider in providers:
                _apply_direct_provider_status(
                    result,
                    provider.name,
                    contact.email,
                    status=status,
                )
            continue

        for provider in providers:
            try:
                status = provider.ensure_contact(contact)
            except Exception as exc:
                _apply_direct_provider_status(
                    result,
                    provider.name,
                    contact.email,
                    error=exc,
                )
                continue
            _apply_direct_provider_status(
                result,
                provider.name,
                contact.email,
                status=status,
            )
    return result


def _apply_direct_provider_status(
    result: dict[str, Any],
    provider_name: str,
    email: str,
    *,
    status: str | None = None,
    error: Exception | None = None,
) -> None:
    provider_result = result["providers"][provider_name]
    if error is not None:
        provider_result["failed"] += 1
        failures = provider_result.setdefault("failures", [])
        if isinstance(failures, list) and len(failures) < 20:
            failures.append({"email": email, "error": str(error)})
        return
    if status == "synced":
        provider_result["synced"] += 1
    else:
        provider_result["skipped"] += 1
    statuses = provider_result.setdefault("statuses", {})
    if isinstance(statuses, dict) and status is not None:
        statuses[status] = int(statuses.get(status, 0)) + 1


def format_newsletter_sync_warning(result: dict[str, Any]) -> str | None:
    """Format direct-provisioning sync failures for user-visible warnings."""
    if result.get("warning") == "no_newsletter_providers_configured":
        return "No newsletter providers are configured."

    messages: list[str] = []
    providers = result.get("providers", {})
    if not isinstance(providers, dict):
        return None

    for provider_name, provider_result in providers.items():
        if not isinstance(provider_result, dict):
            continue
        failed = int(provider_result.get("failed") or 0)
        failures = provider_result.get("failures")
        if failed and isinstance(failures, list) and failures:
            detail = "; ".join(
                redact_email_addresses(str(item.get("error") or "unknown error"))
                for item in failures[:3]
                if isinstance(item, dict)
            )
            messages.append(f"{provider_name} failed for {failed} contact(s): {detail}")
        elif failed:
            messages.append(f"{provider_name} failed for {failed} contact(s)")

        statuses = provider_result.get("statuses")
        if isinstance(statuses, dict):
            if statuses.get("skipped_list_missing"):
                messages.append(f"{provider_name} list was not found")
            suppressed = int(statuses.get("skipped_provider_suppressed") or 0)
            if suppressed:
                messages.append(
                    f"{provider_name} skipped {suppressed} suppressed contact(s)"
                )
            lookup_failed = int(statuses.get("skipped_suppression_lookup_failed") or 0)
            if lookup_failed:
                messages.append(
                    f"{provider_name} skipped {lookup_failed} contact(s) "
                    "because suppression lookup failed"
                )

    return "; ".join(messages) if messages else None
