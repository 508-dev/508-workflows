"""Small, shared privacy checks for agent model and web boundaries."""

from __future__ import annotations

import re

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


def contains_private_agent_identifier(value: object) -> bool:
    """Return whether text contains a record identifier that must stay internal."""

    if not isinstance(value, str):
        return False
    return any(
        pattern.search(value) is not None
        for pattern in (
            _EMAIL_RE,
            _CONTACT_RECORD_ID_RE,
            _TASK_RECORD_ID_RE,
            _UUID_RE,
            _ERP_RECORD_ID_RE,
        )
    )
