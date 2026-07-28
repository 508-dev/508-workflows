"""Unit tests for shared settings validation."""

import sys
from types import SimpleNamespace

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


def test_non_local_settings_do_not_eagerly_reject_missing_runtime_dependencies() -> (
    None
):
    """Non-local settings should construct so health/runtime checks can report issues."""
    settings = SharedSettings(
        environment="production",
        minio_root_password=" ",
    )

    assert settings.environment == "production"
    assert (
        settings.postgres_url
        == "postgresql://postgres:postgres@127.0.0.1:5432/workflows"
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


def test_langfuse_client_uses_configured_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured Langfuse endpoints should construct the lazy client."""

    calls: dict[str, object] = {}

    class FakeLangfuse:
        def __init__(self, **kwargs: object) -> None:
            calls.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(Langfuse=FakeLangfuse),
    )

    client = get_langfuse_client(
        SharedSettings(langfuse_base_url=" https://cloud.langfuse.com ")
    )

    assert isinstance(client, FakeLangfuse)
    assert calls == {"base_url": "https://cloud.langfuse.com"}


def test_shared_settings_expose_agent_external_tool_credentials() -> None:
    """Backend agent tools should receive external tool credentials from env."""
    settings = SharedSettings(
        github_app_client_id="Iv1.client-id",
        github_app_installation_id="456",
        github_app_private_key="private-key",
        github_member_extra_repos="508-dev/member-work",
        github_steering_all_installed_repos=False,
        github_steering_extra_repos="508-dev/infra",
        github_allowed_repos="508-dev/508-workflows,508-dev/infra",
        migadu_api_user="migadu-user",
        migadu_api_key="migadu-key",
        migadu_mailbox_domain="mail.example.com",
        brevo_api_key="brevo-key",
        brevo_508_members_newsletter_list_id=4,
        brevo_508_members_newsletter_list_name="508 members",
        keila_api_key="keila-key",
        keila_api_base_url="https://keila.example",
        agent_web_search_provider_order="searxng,brave,firecrawl",
        agent_web_search_timeout_seconds=12.0,
        agent_web_default_result_limit=4,
        agent_planning_max_steps=4,
        erpnext_base_url="https://erp.example.test",
        erpnext_api_key="erp-key",
        agent_erp_organization_id="org-erp",
        searxng_base_url="http://searxng:8080",
        brave_search_api_key="brave-key",
        firecrawl_api_key="firecrawl-key",
        postgres_url="postgresql://postgres:postgres@db.example/workflows",
    )

    runtime_config = ToolRuntimeConfig.from_settings(settings)

    assert runtime_config.github_default_repo == "508-dev/todos"
    assert runtime_config.github_organization == "508-dev"
    assert runtime_config.github_app_client_id == "Iv1.client-id"
    assert runtime_config.github_app_installation_id == "456"
    assert runtime_config.github_app_private_key == "private-key"
    assert runtime_config.github_member_extra_repos == "508-dev/member-work"
    assert runtime_config.github_steering_all_installed_repos is False
    assert runtime_config.github_steering_extra_repos == "508-dev/infra"
    assert runtime_config.github_allowed_repos == "508-dev/508-workflows,508-dev/infra"
    assert runtime_config.migadu_api_user == "migadu-user"
    assert runtime_config.migadu_api_key == "migadu-key"
    assert runtime_config.migadu_mailbox_domain == "mail.example.com"
    assert runtime_config.brevo_api_key == "brevo-key"
    assert runtime_config.brevo_508_members_newsletter_list_id == 4
    assert runtime_config.brevo_508_members_newsletter_list_name == "508 members"
    assert runtime_config.keila_api_key == "keila-key"
    assert runtime_config.keila_api_base_url == "https://keila.example"
    assert runtime_config.agent_web_search_provider_order == "searxng,brave,firecrawl"
    assert runtime_config.agent_web_search_timeout_seconds == 12.0
    assert runtime_config.agent_web_default_result_limit == 4
    assert runtime_config.erpnext_base_url == "https://erp.example.test"
    assert runtime_config.erpnext_api_key == "erp-key"
    assert runtime_config.agent_erp_organization_id == "org-erp"
    assert runtime_config.searxng_base_url == "http://searxng:8080"
    assert runtime_config.brave_search_api_key == "brave-key"
    assert runtime_config.firecrawl_api_key == "firecrawl-key"
    assert (
        runtime_config.postgres_url
        == "postgresql://postgres:postgres@db.example/workflows"
    )


def test_shared_settings_accepts_legacy_github_app_id_alias() -> None:
    settings = SharedSettings(**{"GITHUB_APP_ID": "123"})

    assert settings.github_app_client_id == "123"


def test_shared_settings_accept_newsletter_sync_env_aliases() -> None:
    settings = SharedSettings(
        **{
            "MIGADU_USER": "michael@508.dev",
            "MIGADU_DOMAIN": "508.dev",
            "KEILA_BASE_URL": "https://keila.508.dev/",
        }
    )

    assert settings.migadu_api_user == "michael@508.dev"
    assert settings.migadu_mailbox_domain == "508.dev"
    assert settings.keila_api_base_url == "https://keila.508.dev/"


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


def test_shared_settings_brevo_members_list_id_accepts_blank_string_as_none() -> None:
    """Blank Brevo list IDs from env should leave list-name lookup enabled."""
    settings = SharedSettings(brevo_508_members_newsletter_list_id=" ")

    assert settings.brevo_508_members_newsletter_list_id is None


def test_shared_settings_brevo_members_list_id_accepts_numeric_string() -> None:
    """Numeric Brevo list IDs from env should coerce to integers."""
    settings = SharedSettings(brevo_508_members_newsletter_list_id="4")

    assert settings.brevo_508_members_newsletter_list_id == 4


def test_shared_settings_newsletter_sync_interval_requires_one_minute() -> None:
    """Newsletter scheduler intervals should match dashboard runtime-config limits."""
    with pytest.raises(ValidationError):
        SharedSettings(newsletter_sync_interval_seconds=59)

    settings = SharedSettings(newsletter_sync_interval_seconds=60)

    assert settings.newsletter_sync_interval_seconds == 60


def test_shared_settings_newsletter_sync_defaults_disabled() -> None:
    settings = SharedSettings()

    assert settings.newsletter_sync_enabled is False


def test_shared_settings_agent_memory_cleanup_requires_at_least_one_hour() -> None:
    with pytest.raises(ValidationError):
        SharedSettings(agent_memory_cleanup_interval_seconds=3_599)

    settings = SharedSettings(agent_memory_cleanup_interval_seconds=3_600)

    assert settings.agent_memory_cleanup_enabled is True
    assert settings.agent_memory_cleanup_interval_seconds == 3_600


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


def test_shared_settings_accept_web_service_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Web/API service settings should use general web env names."""
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "18090")
    monkeypatch.delenv("WEBHOOK_INGEST_HOST", raising=False)
    monkeypatch.delenv("WEBHOOK_INGEST_PORT", raising=False)

    settings = SharedSettings()

    assert settings.web_host == "127.0.0.1"
    assert settings.web_port == 18090


def test_shared_settings_accept_legacy_webhook_ingest_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy webhook ingest env names should remain usable during migration."""
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.delenv("WEB_PORT", raising=False)
    monkeypatch.setenv("WEBHOOK_INGEST_HOST", "127.0.0.2")
    monkeypatch.setenv("WEBHOOK_INGEST_PORT", "18091")

    settings = SharedSettings()

    assert settings.web_host == "127.0.0.2"
    assert settings.web_port == 18091
