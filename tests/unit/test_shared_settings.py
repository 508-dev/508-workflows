"""Unit tests for shared settings validation."""

import pytest
from pydantic import ValidationError

from five08.settings import SharedSettings


def test_non_local_settings_accept_explicit_values() -> None:
    """Non-local settings should validate when values are provided directly."""
    settings = SharedSettings(
        environment="production",
        postgres_url="postgresql://user:pass@db.example.com:5432/workflows",
        minio_root_password="secret",
    )

    assert settings.environment == "production"


def test_non_local_settings_require_non_empty_secrets() -> None:
    """Non-local settings should reject empty runtime secret values."""
    with pytest.raises(ValidationError, match="MINIO_ROOT_PASSWORD must be set"):
        SharedSettings(
            environment="production",
            postgres_url="postgresql://user:pass@db.example.com:5432/workflows",
            minio_root_password=" ",
        )


def test_sentry_environment_and_sampling_are_not_env_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Low-value Sentry config should stay fixed even if legacy env vars are set."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("SENTRY_RELEASE", "v1.2.3")
    monkeypatch.setenv("SENTRY_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SENTRY_PROFILES_SAMPLE_RATE", "0.75")

    settings = SharedSettings(
        postgres_url="postgresql://user:pass@db.example.com:5432/workflows",
        minio_root_password="secret",
    )

    assert settings.sentry_environment_name == "production"
    assert settings.sentry_release is None
    assert settings.sentry_sample_rate == 1.0
    assert settings.sentry_traces_sample_rate == 0.0
    assert settings.sentry_profiles_sample_rate == 0.0


def test_shared_settings_docuseal_template_id_accepts_numeric_string() -> None:
    """Shared settings should coerce DocuSeal template ids from env-like strings."""
    settings = SharedSettings(docuseal_member_agreement_template_id="1000001")

    assert settings.docuseal_member_agreement_template_id == 1000001
