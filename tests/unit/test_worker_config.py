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
