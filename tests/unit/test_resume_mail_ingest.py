"""Unit tests for mailbox-driven resume ingestion."""

from email.message import EmailMessage
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.config import settings
from five08.discord_bot.utils.resume_mail_ingest import (
    ResumeAttachment,
    ResumeMailboxProcessor,
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


@pytest.mark.asyncio
async def test_process_message_happy_path_uses_existing_contact() -> None:
    """Authorized senders with resume attachments should process successfully."""
    processor = ResumeMailboxProcessor(settings)
    processor._sender_is_authorized = Mock(return_value=True)
    processor._find_or_create_staging_contact = Mock(return_value={"id": "staging-1"})
    processor._create_contact_for_email = Mock(return_value={"id": "contact-new"})
    processor._process_attachment = AsyncMock(return_value=True)

    result = await processor.process_message(_build_message())

    assert result.skipped_reason is None
    assert result.processed_attachments == 1
    processor._find_or_create_staging_contact.assert_called_once()
    processor._create_contact_for_email.assert_not_called()
    processor._process_attachment.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_message_skips_when_sender_not_authorized() -> None:
    """Unauthorized senders should be rejected before any attachment processing."""
    processor = ResumeMailboxProcessor(settings)
    processor._sender_is_authorized = Mock(return_value=False)
    processor._process_attachment = AsyncMock(return_value=True)
    processor.audit_logger = Mock()

    result = await processor.process_message(_build_message())

    assert result.skipped_reason == "sender_not_authorized"
    assert result.processed_attachments == 0
    processor._process_attachment.assert_not_called()
    processor.audit_logger.log_admin_sso_action.assert_called_once()
    payload = processor.audit_logger.log_admin_sso_action.call_args.kwargs
    assert payload["result"] == "denied"


@pytest.mark.asyncio
async def test_process_message_requires_auth_headers_when_enabled() -> None:
    """Spoof-resistant mode should reject messages without pass auth headers."""
    processor = ResumeMailboxProcessor(settings)
    processor._sender_is_authorized = Mock(return_value=True)

    message = _build_message()
    del message["Authentication-Results"]

    result = await processor.process_message(message)

    assert result.skipped_reason == "sender_authentication_failed"
    processor._sender_is_authorized.assert_not_called()


@pytest.mark.asyncio
async def test_process_message_creates_contact_when_lookup_misses() -> None:
    """A staging contact should be created/resolved once per processed message."""
    processor = ResumeMailboxProcessor(settings)
    processor._sender_is_authorized = Mock(return_value=True)
    processor._find_or_create_staging_contact = Mock(return_value={"id": "staging-1"})
    processor._process_attachment = AsyncMock(return_value=True)

    result = await processor.process_message(_build_message())

    assert result.skipped_reason is None
    assert result.processed_attachments == 1
    processor._find_or_create_staging_contact.assert_called_once()


@pytest.mark.asyncio
async def test_process_attachment_updates_candidate_not_sender() -> None:
    """Attachment processing should resolve candidate email and apply to candidate contact."""
    processor = ResumeMailboxProcessor(settings)
    processor._upload_contact_resume = Mock(
        side_effect=["att-staging", "att-candidate"]
    )
    processor._append_contact_resume = Mock(return_value=True)
    processor._enqueue_resume_extract_job = AsyncMock(
        side_effect=["extract-staging", "extract-candidate"]
    )
    processor._enqueue_resume_apply_job = AsyncMock(return_value="apply-candidate")
    processor._wait_for_worker_job_result = AsyncMock(
        side_effect=[
            {
                "status": "succeeded",
                "result": {
                    "success": True,
                    "extracted_profile": {"email": "candidate@example.com"},
                    "proposed_updates": {},
                },
            },
            {
                "status": "succeeded",
                "result": {
                    "success": True,
                    "proposed_updates": {"phoneNumber": "14155551234"},
                },
            },
            {
                "status": "succeeded",
                "result": {
                    "success": True,
                    "updated_fields": ["phoneNumber"],
                },
            },
        ]
    )
    processor._find_contact_by_email = Mock(return_value=None)
    processor._create_contact_for_email = Mock(return_value={"id": "candidate-1"})

    ok = await processor._process_attachment(
        staging_contact_id="staging-1",
        attachment=ResumeAttachment(filename="resume.pdf", content=b"resume-bytes"),
    )

    assert ok is True
    processor._create_contact_for_email.assert_called_once_with(
        "candidate@example.com", None
    )
    processor._enqueue_resume_apply_job.assert_awaited_once_with(
        contact_id="candidate-1",
        updates={"phoneNumber": "14155551234"},
    )
