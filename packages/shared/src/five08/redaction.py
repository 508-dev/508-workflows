"""Small redaction helpers for persisted/logged diagnostic strings."""

from __future__ import annotations

import re

EMAIL_ADDRESS_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"
)
PERCENT_ENCODED_EMAIL_ADDRESS_PATTERN = re.compile(
    r"(?<![\w.%+-])[\w.+%-]+%40[\w.-]+\.[A-Za-z]{2,}(?![\w.-])",
    re.IGNORECASE,
)


def redact_email_addresses(text: str) -> str:
    """Replace email-like substrings in text with a stable placeholder."""
    text = PERCENT_ENCODED_EMAIL_ADDRESS_PATTERN.sub("[redacted-email]", text)
    return EMAIL_ADDRESS_PATTERN.sub("[redacted-email]", text)
