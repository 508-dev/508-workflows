"""Shared contact email validation for provider clients."""

from __future__ import annotations

from five08.onboarding_email import validate_plain_email


def normalize_provider_contact_email(value: str, provider_name: str) -> str:
    """Normalize a provider contact email after full-address validation."""
    try:
        return validate_plain_email(value, "contact email").lower()
    except ValueError as exc:
        raise ValueError(
            f"{provider_name} contact email must be a full email address."
        ) from exc
