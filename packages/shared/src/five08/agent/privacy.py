"""Small, shared privacy checks for agent model and web boundaries."""

from __future__ import annotations

import re
from urllib.parse import unquote

_EMAIL_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_CONTACT_RECORD_ID_RE = re.compile(
    r"\b(?:crm\s+)?contact\s+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b"
    r"|\bcontact[-_][A-Za-z0-9_-]+\b",
    re.IGNORECASE,
)
_TASK_RECORD_ID_RE = re.compile(r"\bTASK-\d+\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ERP_RECORD_ID_RE = re.compile(
    r"\b(?:ACC-)?(?:SINV|PINV|PROJ)-[A-Za-z0-9_-]+\b"
    r"|\b(?:sales|purchase)\s+invoice\s+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b"
    r"|\b(?:erp(?:next)?\s+)?project\s+[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b",
    re.IGNORECASE,
)
_MAX_PERCENT_DECODE_INPUT_CHARS = 8_192
_MAX_PERCENT_DECODE_PASSES = 4


def percent_decoded_text_candidates(value: str) -> tuple[str, ...] | None:
    """Return bounded, fully decoded text forms, or ``None`` to fail closed.

    Inputs can cross model and external-service boundaries.  Check every
    reversible percent-decoded representation, but keep the work bounded.  An
    overlong or still-nested value is ambiguous, so callers that enforce a
    privacy or sensitive-data boundary must treat ``None`` as unsafe.
    """

    if "%" not in value:
        return (value,)
    if len(value) > _MAX_PERCENT_DECODE_INPUT_CHARS:
        return None

    candidates = [value]
    for _ in range(_MAX_PERCENT_DECODE_PASSES):
        decoded = unquote(candidates[-1])
        if decoded == candidates[-1]:
            return tuple(candidates)
        candidates.append(decoded)

    # Do not allow an attacker to bury a private value under more nesting than
    # this bounded checker permits us to inspect.
    if unquote(candidates[-1]) != candidates[-1]:
        return None
    return tuple(candidates)


def contains_private_agent_identifier(value: object) -> bool:
    """Return whether text contains a record identifier that must stay internal."""

    if not isinstance(value, str):
        return False
    candidates = percent_decoded_text_candidates(value)
    if candidates is None:
        return True
    # Requests and context can carry URL-encoded identifiers. Check every
    # bounded canonical form so reversible encoding cannot bypass the shared
    # model-privacy boundary.
    return any(
        pattern.search(candidate) is not None
        for candidate in candidates
        for pattern in (
            _EMAIL_RE,
            _CONTACT_RECORD_ID_RE,
            _TASK_RECORD_ID_RE,
            _UUID_RE,
            _ERP_RECORD_ID_RE,
        )
    )
