"""508 member newsletter audience synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from five08.clients.brevo import BrevoClient
from five08.clients.espo import EspoAPIError, EspoClient
from five08.clients.keila import KeilaClient
from five08.clients.migadu import MigaduClient, MigaduMailbox

CRM_BLOCKED_TYPES = {"inactive member", "rejected", "blocked"}
CRM_BLOCKED_ONBOARDING_STATES = {"rejected", "waitlist"}
PROVIDER_SUPPRESSED_STATUSES = {"unsubscribed", "unreachable", "blocked"}


class CRMContactLookupError(RuntimeError):
    """Raised when CRM block-state lookup fails during member sync."""


@dataclass(frozen=True, slots=True)
class NewsletterContact:
    """One email address derived from one Migadu mailbox."""

    email: str
    mailbox_email: str
    name: str
    source: str


class NewsletterProvider(Protocol):
    """Provider interface for additive newsletter subscription sync."""

    name: str

    def ensure_contact(self, contact: NewsletterContact) -> str:
        """Ensure contact exists, returning added/updated/already/skipped."""


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

    def _list_id(self) -> int | None:
        if not self._list_id_lookup_completed:
            self._resolved_list_id = self.client.find_list_id_by_name(self.list_name)
            self._list_id_lookup_completed = True
        return self._resolved_list_id

    def ensure_contact(self, contact: NewsletterContact) -> str:
        list_id = self._list_id()
        if list_id is None:
            return "skipped_list_missing"

        existing = self.client.get_contact(contact.email)
        if existing is not None and (
            bool(existing.get("emailBlacklisted"))
            or str(existing.get("status") or "").strip().casefold()
            in PROVIDER_SUPPRESSED_STATUSES
        ):
            return "skipped_provider_suppressed"

        if existing is not None:
            if _contains_list_id(existing.get("listUnsubscribed"), list_id):
                return "skipped_provider_suppressed"
        self.client.add_contact_to_list(email=contact.email, list_id=list_id)
        return "synced"


class KeilaNewsletterProvider:
    """Keila implementation using project contacts and contact data tags."""

    name = "keila"

    def __init__(self, client: KeilaClient) -> None:
        self.client = client

    def ensure_contact(self, contact: NewsletterContact) -> str:
        existing = self.client.get_contact_by_email(contact.email)
        if existing is not None:
            status = str(existing.get("status") or "").strip().casefold()
            if status in PROVIDER_SUPPRESSED_STATUSES:
                return "skipped_provider_suppressed"

        first_name, last_name = _split_name(contact.name)
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

    def sync_508_members(self) -> dict[str, Any]:
        providers = build_newsletter_providers(self.settings)
        result: dict[str, Any] = {
            "mailboxes_scanned": 0,
            "system_mailboxes_skipped": 0,
            "crm_blocked_skipped": 0,
            "crm_unmatched_skipped": 0,
            "crm_lookup_failed_skipped": 0,
            "contacts_considered": 0,
            "providers": {
                provider.name: {"synced": 0, "skipped": 0, "failed": 0}
                for provider in providers
            },
        }
        if not providers:
            result["warning"] = "no_newsletter_providers_configured"
            return result

        crm_lookup_enabled = self._crm_lookup_enabled()
        for mailbox in self._migadu_client().list_mailboxes():
            result["mailboxes_scanned"] += 1
            if _is_mailbox_excluded(mailbox.address, self.excluded_mailboxes):
                result["system_mailboxes_skipped"] += 1
                continue

            try:
                crm_contacts = self._list_crm_contacts(mailbox)
            except CRMContactLookupError as exc:
                result["crm_lookup_failed_skipped"] += 1
                failures = result.setdefault("crm_lookup_failures", [])
                if isinstance(failures, list) and len(failures) < 20:
                    failures.append({"mailbox": mailbox.address, "error": str(exc)})
                continue
            if crm_lookup_enabled and not crm_contacts:
                result["crm_unmatched_skipped"] += 1
                continue
            if any(_is_crm_blocked(contact) for contact in crm_contacts):
                result["crm_blocked_skipped"] += 1
                continue

            for contact in _normalized_emails_for_mailbox(mailbox):
                result["contacts_considered"] += 1
                for provider in providers:
                    provider_result = result["providers"][provider.name]
                    try:
                        status = provider.ensure_contact(contact)
                    except Exception as exc:
                        provider_result["failed"] += 1
                        failures = provider_result.setdefault("failures", [])
                        if isinstance(failures, list) and len(failures) < 20:
                            failures.append({"email": contact.email, "error": str(exc)})
                        continue
                    if status == "synced":
                        provider_result["synced"] += 1
                    else:
                        provider_result["skipped"] += 1
                    statuses = provider_result.setdefault("statuses", {})
                    if isinstance(statuses, dict):
                        statuses[status] = int(statuses.get(status, 0)) + 1
        return result

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
    for email in emails:
        normalized_email = email.strip().lower()
        if not normalized_email or normalized_email in seen:
            continue
        seen.add(normalized_email)
        if not default_mailbox_email:
            default_mailbox_email = normalized_email
        result["contacts_considered"] += 1
        contact = NewsletterContact(
            email=normalized_email,
            mailbox_email=default_mailbox_email,
            name=name,
            source=source,
        )
        for provider in providers:
            provider_result = result["providers"][provider.name]
            try:
                status = provider.ensure_contact(contact)
            except Exception as exc:
                provider_result["failed"] += 1
                failures = provider_result.setdefault("failures", [])
                if isinstance(failures, list) and len(failures) < 20:
                    failures.append({"email": normalized_email, "error": str(exc)})
                continue
            if status == "synced":
                provider_result["synced"] += 1
            else:
                provider_result["skipped"] += 1
            statuses = provider_result.setdefault("statuses", {})
            if isinstance(statuses, dict):
                statuses[status] = int(statuses.get(status, 0)) + 1
    return result


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
                f"{item.get('email')}: {item.get('error')}"
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

    return "; ".join(messages) if messages else None
