"""Memory fact storage primitives for the agent gateway."""

from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from five08.agent.models import (
    MemoryFact,
    MemoryScopeType,
    MemoryVisibility,
    validate_memory_value_json,
)

DEFAULT_MEMORY_RETENTION_DAYS = 365
MAX_MEMORY_FACTS_PER_LIST = 50

_SENSITIVE_FIELD_NAME_RE = re.compile(
    r"(?:^|[_\s.-])(?:"
    r"api[_\s.-]?key|"
    r"secret|"
    r"password|passwd|"
    r"credential(?:s)?|"
    r"private[_\s.-]?key|"
    r"(?:access|refresh|auth)[_\s.-]?token|"
    r"cvv|cvc|"
    r"ssn|social[_\s.-]?security(?:[_\s.-]?number)?|"
    r"card(?:[_\s.-]?(?:number|no))?|"
    r"routing(?:[_\s.-]?(?:number|no))?|"
    r"account(?:[_\s.-]?(?:number|no))?|"
    r"iban"
    r")(?:$|[_\s.-])",
    re.IGNORECASE,
)
_HIGH_SIGNAL_INLINE_SECRET_RE = re.compile(
    r"\b(?:api[_\s.-]?key|(?:access|refresh|auth)[_\s.-]?token)"
    r"(?:\s*[:=]\s*|\s+(?:is|equals)\s+|\s+)['\"]?[^\s'\"]{4,}",
    re.IGNORECASE,
)
_NAMED_INLINE_SECRET_RE = re.compile(
    r"\b(?:secret|password|passwd|credential(?:s)?|passphrase|token)"
    # Generic words occur in ordinary requests (for example, "token bucket"
    # or "password reset"). Require an explicit value connector before
    # treating them as secret material.
    r"(?:\s*[:=]\s*|\s+(?:is|equals)\s+)['\"]?[^\s'\"]{4,}",
    re.IGNORECASE,
)
_CONTEXTUAL_INLINE_SECRET_RE = re.compile(
    # Preserve the most common natural requests that actually carry a secret
    # without treating generic documentation terms as a credential.
    r"\b(?:my|our)\s+(?:secret|password|passwd|credential(?:s)?|passphrase|token)"
    r"\s+['\"]?[^\s'\"]{4,}"
    r"|\b(?:use|using|remember|store|set)\s+(?:api[_\s.-]?key|(?:access|refresh|auth)[_\s.-]?token|token)"
    r"\s+['\"]?[^\s'\"]{12,}",
    re.IGNORECASE,
)
_BEARER_TOKEN_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE)
_OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_PAYMENT_API_KEY_RE = re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")
_CONNECTION_URI_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^/\s:@]+:[^@\s/]+@",
    re.IGNORECASE,
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IBAN_CANDIDATE_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_IBAN_COUNTRY_CODES = frozenset(
    {
        "AD",
        "AE",
        "AL",
        "AT",
        "AZ",
        "BA",
        "BE",
        "BG",
        "BH",
        "BI",
        "BR",
        "BY",
        "CH",
        "CR",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DO",
        "EE",
        "EG",
        "ES",
        "FI",
        "FO",
        "FR",
        "GB",
        "GE",
        "GI",
        "GL",
        "GR",
        "GT",
        "HR",
        "HU",
        "IE",
        "IL",
        "IQ",
        "IS",
        "IT",
        "JO",
        "KW",
        "KZ",
        "LB",
        "LC",
        "LI",
        "LT",
        "LU",
        "LV",
        "LY",
        "MC",
        "MD",
        "ME",
        "MK",
        "MN",
        "MR",
        "MT",
        "MU",
        "NI",
        "NL",
        "NO",
        "OM",
        "PK",
        "PL",
        "PS",
        "PT",
        "QA",
        "RO",
        "RS",
        "SA",
        "SC",
        "SE",
        "SI",
        "SK",
        "SM",
        "ST",
        "SV",
        "TL",
        "TN",
        "TR",
        "UA",
        "VA",
        "VG",
        "XK",
    }
)
_PAYMENT_LABEL_RE = re.compile(
    r"\b(?:cvv|cvc|card\s*(?:number|no\.?)|routing\s*(?:number|no\.?)|"
    r"account\s*(?:number|no\.?))\s*[:#=-]?\s*\d{3,}\b",
    re.IGNORECASE,
)
_CARD_NUMBER_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


class MemoryStore(Protocol):
    """Durable fact store interface used by deterministic memory tools."""

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
        """Persist one memory fact."""

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
        """Return visible non-expired facts by default."""

    def forget_fact(
        self,
        *,
        organization_id: str,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        """Immediately remove one fact the actor may manage."""

    def purge_expired(
        self,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> int:
        """Physically remove expired facts for one organization."""


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
        normalized_organization_id = normalize_organization_id(organization_id)
        validate_memory_value_for_persistence(value_json)
        now = normalize_memory_time(None)
        fact = MemoryFact(
            organization_id=normalized_organization_id,
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
            self._purge_expired_locked(
                organization_id=normalized_organization_id,
                now=now,
            )
            self._facts[fact.id] = fact
        return fact

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
        normalized_organization_id = normalize_organization_id(organization_id)
        assert_visible_org_matches_tenant(
            visible_to_org_id=visible_to_org_id,
            organization_id=normalized_organization_id,
        )
        comparison_time = normalize_memory_time(now)
        with self._lock:
            facts = [
                fact
                for fact in self._facts.values()
                if fact.organization_id == normalized_organization_id
                and fact.scope_type == scope_type
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
            return sorted(facts, key=lambda fact: (fact.created_at, fact.id))[
                :MAX_MEMORY_FACTS_PER_LIST
            ]

    def forget_fact(
        self,
        *,
        organization_id: str,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        normalized_organization_id = normalize_organization_id(organization_id)
        comparison_time = normalize_memory_time(now)
        with self._lock:
            self._purge_expired_locked(
                organization_id=normalized_organization_id,
                now=comparison_time,
            )
            fact = self._facts.get(fact_id)
            if fact is None or fact.organization_id != normalized_organization_id:
                raise KeyError(f"Memory fact {fact_id} was not found")
            if not actor_is_admin and fact.created_by != actor_id:
                raise PermissionError("Memory fact can only be deleted by its creator")
            deleted = fact.model_copy(
                update={
                    "deleted_at": comparison_time,
                    "updated_at": comparison_time,
                }
            )
            # Forget means forget. Return deletion metadata to the caller for
            # audit/UI purposes, but do not retain the value in this store for
            # a later purge cycle.
            del self._facts[fact_id]
            return deleted

    def purge_expired(
        self,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> int:
        """Remove expired or soft-deleted records for one tenant only."""
        normalized_organization_id = normalize_organization_id(organization_id)
        comparison_time = normalize_memory_time(now)
        with self._lock:
            return self._purge_expired_locked(
                organization_id=normalized_organization_id,
                now=comparison_time,
            )

    def _purge_expired_locked(self, *, organization_id: str, now: datetime) -> int:
        expired_ids = [
            fact.id
            for fact in self._facts.values()
            if fact.organization_id == organization_id
            and (fact.deleted_at is not None or _fact_is_expired(fact, now=now))
        ]
        for fact_id in expired_ids:
            del self._facts[fact_id]
        return len(expired_ids)


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
    return expires_at.astimezone(timezone.utc) <= normalize_memory_time(now)


def _excerpt_hash(source_excerpt: str | None) -> str | None:
    if source_excerpt is None:
        return None
    return hashlib.sha256(source_excerpt.encode("utf-8")).hexdigest()


def normalize_organization_id(organization_id: str) -> str:
    """Return a non-empty tenant identifier suitable for every store query."""
    if not isinstance(organization_id, str):
        raise ValueError("organization_id is required")
    normalized = organization_id.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("organization_id must be between 1 and 128 characters")
    return normalized


def validate_memory_value_for_persistence(value_json: dict[str, Any]) -> None:
    """Reject oversized, non-JSON, or sensitive values before storing a fact."""
    validate_memory_value_json(value_json)
    if _contains_sensitive_value(value_json):
        raise ValueError(
            "Memory values cannot contain secrets, credentials, payment data, or SSN-like values"
        )


def contains_sensitive_memory_text(value: str) -> bool:
    """Return whether free text contains a credential or regulated identifier.

    This is intentionally reusable before an agent message is sent to a model
    or placed in audit metadata. Durable-memory validation remains the final
    enforcement point for arbitrary structured values.
    """

    return isinstance(value, str) and _contains_sensitive_text(value)


def assert_visible_org_matches_tenant(
    *,
    visible_to_org_id: str | None,
    organization_id: str,
) -> None:
    """Reject a visibility request that crosses the durable-memory tenant."""

    if visible_to_org_id is None:
        return
    if normalize_organization_id(visible_to_org_id) != organization_id:
        raise PermissionError("Memory access is limited to the request organization")


def normalize_memory_time(value: datetime | None) -> datetime:
    """Return a UTC timestamp for memory retention and visibility comparisons."""

    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _contains_sensitive_value(value_json: dict[str, Any]) -> bool:
    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if _SENSITIVE_FIELD_NAME_RE.search(key):
                    return True
                if visit(nested_value):
                    return True
            return False
        if isinstance(value, list):
            return any(visit(nested_value) for nested_value in value)
        if isinstance(value, str):
            return _contains_sensitive_text(value)
        if isinstance(value, int) and not isinstance(value, bool):
            return _is_luhn_valid(str(value))
        return False

    return visit(value_json)


def _contains_sensitive_text(value: str) -> bool:
    if any(
        pattern.search(value)
        for pattern in (
            _HIGH_SIGNAL_INLINE_SECRET_RE,
            _NAMED_INLINE_SECRET_RE,
            _CONTEXTUAL_INLINE_SECRET_RE,
            _BEARER_TOKEN_RE,
            _OPENAI_KEY_RE,
            _GITHUB_TOKEN_RE,
            _AWS_ACCESS_KEY_RE,
            _JWT_RE,
            _PRIVATE_KEY_RE,
            _PAYMENT_API_KEY_RE,
            _CONNECTION_URI_RE,
            _SSN_RE,
            _PAYMENT_LABEL_RE,
        )
    ):
        return True
    if any(
        _is_luhn_valid(_digits_only(candidate.group()))
        for candidate in _CARD_NUMBER_CANDIDATE_RE.finditer(value)
    ):
        return True
    return any(
        _is_valid_iban(candidate.group())
        for candidate in _IBAN_CANDIDATE_RE.finditer(value)
    )


def _is_valid_iban(value: str) -> bool:
    """Return whether a candidate has a supported country code and mod-97 check."""

    normalized = value.upper()
    if not 15 <= len(normalized) <= 34 or normalized[:2] not in _IBAN_COUNTRY_CODES:
        return False
    if not normalized[2:4].isdigit() or not normalized[4:].isalnum():
        return False
    remainder = 0
    for character in normalized[4:] + normalized[:4]:
        if character.isdigit():
            remainder = (remainder * 10 + int(character)) % 97
        else:
            remainder = (remainder * 100 + ord(character) - ord("A") + 10) % 97
    return remainder == 1


def _digits_only(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _is_luhn_valid(value: str) -> bool:
    if not 13 <= len(value) <= 19 or not value.isdigit():
        return False
    total = 0
    for index, character in enumerate(reversed(value)):
        digit = int(character)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
