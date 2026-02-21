"""Unit tests for worker-side mailbox resume ingestion."""

from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import Mock

from five08.worker.mailbox_resume_ingest import ResumeAttachment, ResumeMailboxProcessor


class _FakeProfile:
    def __init__(self, email: str | None) -> None:
        self._email = email

    def model_dump(self) -> dict[str, str | None]:
        return {"email": self._email}


def _build_settings() -> SimpleNamespace:
    return SimpleNamespace(
        espo_base_url="https://crm.test.com",
        espo_api_key="test_key",
        email_username="workflows@508.dev",
        email_password="test_password",
        imap_server="imap.test.com",
        email_resume_allowed_extensions="pdf,doc,docx",
        email_resume_max_file_size_mb=10,
        email_require_sender_auth_headers=True,
    )


def _build_message(*, include_attachment: bool = True) -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Admin User <admin@508.dev>"
    message["Subject"] = "Resume upload"
    message["Authentication-Results"] = "mx.example; dkim=pass; spf=pass; dmarc=pass"
    message.set_content("Please process this resume.")

    if include_attachment:
        message.add_attachment(
            b"resume-bytes",
            maintype="application",
            subtype="pdf",
            filename="resume.pdf",
        )

    return message


def test_process_message_happy_path() -> None:
    processor = ResumeMailboxProcessor(_build_settings())
    processor._audit_mailbox_outcome = Mock()
    processor._sender_is_authorized = Mock(return_value=True)
    processor._find_or_create_staging_contact = Mock(return_value={"id": "staging-1"})
    processor._process_attachment = Mock(return_value=True)

    result = processor.process_message(_build_message())

    assert result.skipped_reason is None
    assert result.processed_attachments == 1
    processor._find_or_create_staging_contact.assert_called_once()
    processor._process_attachment.assert_called_once()


def test_process_message_denies_unauthorized_sender() -> None:
    processor = ResumeMailboxProcessor(_build_settings())
    processor._audit_mailbox_outcome = Mock()
    processor._sender_is_authorized = Mock(return_value=False)

    result = processor.process_message(_build_message())

    assert result.skipped_reason == "sender_not_authorized"
    assert result.processed_attachments == 0


def test_process_attachment_updates_candidate_contact() -> None:
    processor = ResumeMailboxProcessor(_build_settings())
    processor._upload_contact_resume = Mock(
        side_effect=["att-staging", "att-candidate"]
    )
    processor._append_contact_resume = Mock(return_value=True)
    processor._find_contact_by_email = Mock(return_value=None)
    processor._create_contact_for_email = Mock(return_value={"id": "candidate-1"})

    staging_extract = SimpleNamespace(
        success=True,
        extracted_profile=_FakeProfile("candidate@example.com"),
        proposed_updates={},
    )
    candidate_extract = SimpleNamespace(
        success=True,
        extracted_profile=_FakeProfile(None),
        proposed_updates={"phoneNumber": "14155551234"},
    )
    apply_result = SimpleNamespace(success=True)

    processor.resume_processor = Mock()
    processor.resume_processor.extract_profile_proposal.side_effect = [
        staging_extract,
        candidate_extract,
    ]
    processor.resume_processor.apply_profile_updates.return_value = apply_result

    ok = processor._process_attachment(
        staging_contact_id="staging-1",
        attachment=ResumeAttachment(filename="resume.pdf", content=b"resume-bytes"),
    )

    assert ok is True
    processor._create_contact_for_email.assert_called_once_with(
        "candidate@example.com", None
    )
    processor.resume_processor.apply_profile_updates.assert_called_once_with(
        contact_id="candidate-1",
        updates={"phoneNumber": "14155551234"},
        link_discord=None,
    )
