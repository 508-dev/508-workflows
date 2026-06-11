"""Small redaction helpers for persisted/logged diagnostic strings."""

from __future__ import annotations

import re

EMAIL_ADDRESS_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)


def redact_email_addresses(text: str) -> str:
    """Replace email-like substrings in text with a stable placeholder."""
    return EMAIL_ADDRESS_PATTERN.sub("[redacted-email]", text)
