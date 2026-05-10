"""Unit tests for shared settings validation."""

import pytest
from pydantic import ValidationError

from five08.langfuse import get_langfuse_client
from five08.agent.tools import ToolRuntimeConfig
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


def test_langfuse_base_url_is_shared_configuration() -> None:
    """Shared settings should expose Langfuse endpoint configuration."""
    settings = SharedSettings(langfuse_base_url="https://cloud.langfuse.com")

    assert settings.langfuse_base_url == "https://cloud.langfuse.com"


def test_langfuse_client_is_disabled_without_base_url() -> None:
    """Langfuse should be lazily initialized only when configured."""
    assert get_langfuse_client(SharedSettings()) is None


def test_shared_settings_expose_agent_external_tool_credentials() -> None:
    """Backend agent tools should receive Kimai and Migadu credentials from env."""
    settings = SharedSettings(
        kimai_base_url="https://kimai.example.com",
        kimai_api_token="kimai-token",
        migadu_api_user="migadu-user",
        migadu_api_key="migadu-key",
        migadu_mailbox_domain="mail.example.com",
    )

    runtime_config = ToolRuntimeConfig.from_settings(settings)

    assert runtime_config.kimai_base_url == "https://kimai.example.com"
    assert runtime_config.kimai_api_token == "kimai-token"
    assert runtime_config.migadu_api_user == "migadu-user"
    assert runtime_config.migadu_api_key == "migadu-key"
    assert runtime_config.migadu_mailbox_domain == "mail.example.com"


def test_shared_settings_docuseal_template_id_accepts_numeric_string() -> None:
    """Shared settings should coerce DocuSeal template ids from env-like strings."""
    settings = SharedSettings(docuseal_member_agreement_template_id="1000001")

    assert settings.docuseal_member_agreement_template_id == 1000001


def test_shared_settings_docuseal_template_id_rejects_non_numeric_string() -> None:
    """Shared settings should surface a clear validation error for bad template ids."""
    with pytest.raises(
        ValidationError,
        match="DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID must be an integer",
    ):
        SharedSettings(docuseal_member_agreement_template_id="abc")


def test_local_service_defaults_target_host_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local defaults should work when app services run directly on the host."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("MINIO_ENDPOINT", raising=False)

    settings = SharedSettings()

    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert (
        settings.postgres_url
        == "postgresql://postgres:postgres@127.0.0.1:5432/workflows"
    )
    assert settings.minio_endpoint == "http://127.0.0.1:9000"
