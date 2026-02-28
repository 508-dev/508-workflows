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
