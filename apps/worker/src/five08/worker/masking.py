"""PII masking helpers used across worker modules."""


def mask_email(email: str) -> str:
    """Return a deterministic redacted email representation for logs and responses."""
    local, at, domain = email.partition("@")
    if not at:
        return "***"

    masked_local = (local[:1] if local else "*") + "***"

    if not domain:
        return f"{masked_local}@****..."

    return f"{masked_local}@{domain[:1]}****..."
