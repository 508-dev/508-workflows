"""Unit tests for worker settings email intake validation."""

import pytest
from pydantic import ValidationError

from five08.worker.config import WorkerSettings


def test_email_intake_requires_mailbox_credentials() -> None:
    with pytest.raises(ValidationError, match="EMAIL_PASSWORD must be set"):
        WorkerSettings(
            espo_base_url="https://crm.test.com",
            espo_api_key="test-key",
            email_resume_intake_enabled=True,
            email_username="workflows@508.dev",
            email_password=" ",
            imap_server="imap.test.com",
        )


def test_email_intake_requires_mailbox_username() -> None:
    with pytest.raises(ValidationError, match="EMAIL_USERNAME must be set"):
        WorkerSettings(
            espo_base_url="https://crm.test.com",
            espo_api_key="test-key",
            email_resume_intake_enabled=True,
            email_username=" ",
            email_password="password",
            imap_server="imap.test.com",
        )


def test_email_intake_requires_imap_server() -> None:
    with pytest.raises(ValidationError, match="IMAP_SERVER must be set"):
        WorkerSettings(
            espo_base_url="https://crm.test.com",
            espo_api_key="test-key",
            email_resume_intake_enabled=True,
            email_username="workflows@508.dev",
            email_password="password",
            imap_server=" ",
        )


def test_email_intake_disabled_bypasses_validation() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        email_resume_intake_enabled=False,
        email_username=" ",
        email_password=" ",
        imap_server=" ",
    )

    assert settings.email_resume_intake_enabled is False


def test_email_intake_validation_passes_with_required_fields() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        email_resume_intake_enabled=True,
        email_username="workflows@508.dev",
        email_password="password",
        imap_server="imap.test.com",
    )

    assert settings.email_resume_intake_enabled is True


def test_docuseal_template_id_normalizes_blank_string_to_none() -> None:
    """Docuseal template filter should treat empty string as unset."""
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        docuseal_member_agreement_template_id="",
    )

    assert settings.docuseal_member_agreement_template_id is None


def test_docuseal_template_id_accepts_numeric_string() -> None:
    """Docuseal template filter should coerce numeric strings to int."""
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        docuseal_member_agreement_template_id="68",
    )

    assert settings.docuseal_member_agreement_template_id == 68


def test_discord_bot_internal_base_url_defaults_to_host_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCORD_BOT_INTERNAL_BASE_URL", raising=False)

    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.discord_bot_internal_base_url == "http://127.0.0.1:3000"


def test_google_forms_allowed_form_ids_parses_as_set() -> None:
    """Allowed form IDs should be parsed into a normalized set."""
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        google_forms_allowed_form_ids="form-1, form-2,,  form-3 ",
    )

    assert settings.google_forms_allowed_form_ids_set == {"form-1", "form-2", "form-3"}


def test_oidc_admin_groups_default_matches_authentik_admins() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.oidc_admin_group_names == {"authentik admins"}


def test_discord_admin_roles_default_is_admin_owner() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.discord_admin_role_names == {"admin", "owner"}


def test_resume_model_defaults_to_gpt_4_1_mini() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.resolved_resume_ai_model == "gpt-4.1-mini"


def test_resume_provider_uses_specific_credentials_when_configured() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        resume_ai_api_key="resume-key",
        resume_ai_base_url="https://openrouter.ai/api/v1",
        resume_ai_model="gpt-4.1-mini",
    )

    assert settings.resolved_resume_ai_api_key == "resume-key"
    assert settings.resolved_resume_ai_base_url == "https://openrouter.ai/api/v1"
    assert settings.resolved_resume_ai_model == "openai/gpt-4.1-mini"


def test_resume_provider_falls_back_to_openai_when_specific_key_missing() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        resume_ai_base_url="https://openrouter.ai/api/v1",
        resume_ai_model="gpt-4.1-mini",
    )

    assert settings.resolved_resume_ai_api_key == "openai-key"
    assert settings.resolved_resume_ai_base_url == "https://api.openai.com/v1"
    assert settings.resolved_resume_ai_model == "gpt-4.1-mini"


def test_resume_provider_attempts_include_direct_fallbacks_for_bifrost() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        openai_api_key="bifrost-key",
        openai_base_url="https://bifrost.508.dev/openai",
        resume_ai_model="openrouter/openai/gpt-4.1-mini",
        openrouter_api_key="openrouter-key",
        openai_api_key_direct="openai-direct-key",
        agent_fallback_model="gpt-4.1-mini",
    )

    attempts = settings.resolved_resume_ai_provider_attempts

    assert [attempt.label for attempt in attempts] == [
        "primary",
        "openrouter-direct",
        "openai-direct",
    ]
    assert attempts[0].model == "openrouter/openai/gpt-4.1-mini"
    assert attempts[1].model == "openai/gpt-4.1-mini"
    assert attempts[1].base_url == "https://openrouter.ai/api/v1"
    assert attempts[2].model == "gpt-4.1-mini"
    assert attempts[2].base_url == "https://api.openai.com/v1"


def test_intake_resume_fetch_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            espo_base_url="https://crm.test.com",
            espo_api_key="test-key",
            intake_resume_fetch_timeout_seconds=0,
        )


def test_intake_resume_max_redirects_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(
            espo_base_url="https://crm.test.com",
            espo_api_key="test-key",
            intake_resume_max_redirects=-1,
        )


def test_intake_resume_allowed_hostnames_normalizes_dots_and_empties() -> None:
    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        intake_resume_allowed_hosts=" .Example.com., ., sub.example.com., , ",
    )

    assert settings.intake_resume_allowed_hostnames == {
        "example.com",
        "sub.example.com",
    }


def test_fixed_worker_defaults_ignore_legacy_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRM_LINKEDIN_FIELD", "linkedinCustom")
    monkeypatch.setenv("CRM_INTAKE_COMPLETED_FIELD", "cCompleted")
    monkeypatch.setenv("RESUME_KEYWORDS", "portfolio")
    monkeypatch.setenv("OIDC_HTTP_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OIDC_JWKS_CACHE_SECONDS", "60")
    monkeypatch.setenv("AUTH_STATE_TTL_SECONDS", "42")
    monkeypatch.setenv("AUTH_SESSION_TTL_SECONDS", "120")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")

    settings = WorkerSettings(
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.crm_intake_completed_field == ""
    assert settings.parsed_resume_keywords == {"resume", "cv", "curriculum"}
    assert settings.oidc_http_timeout_seconds == 8.0
    assert settings.oidc_jwks_cache_seconds == 300
    assert settings.auth_state_ttl_seconds == 600
    assert settings.auth_session_ttl_seconds == 28800
    assert settings.auth_cookie_samesite == "lax"


def test_auth_cookie_secure_is_false_for_local_even_if_legacy_env_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

    settings = WorkerSettings(
        environment="local",
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
    )

    assert settings.auth_cookie_secure is False


def test_auth_cookie_secure_is_true_for_non_local_even_if_legacy_env_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    settings = WorkerSettings(
        environment="production",
        espo_base_url="https://crm.test.com",
        espo_api_key="test-key",
        minio_root_password="secret",
    )

    assert settings.auth_cookie_secure is True
