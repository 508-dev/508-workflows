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
