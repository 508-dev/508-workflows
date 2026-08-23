"""Tests for recipient-guarded contact candidate extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

from five08.contact_email_candidates import (
    ContactEmailCandidate,
    ContactEmailCandidateStatus,
)
from five08.worker.contact_email_ingest import (
    ContactEmailCandidateProcessor,
    is_contact_intake_message,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(contact_email_intake_address="contacts@508.dev")


def _candidate() -> ContactEmailCandidate:
    return ContactEmailCandidate(
        id="candidate-1",
        status=ContactEmailCandidateStatus.PENDING,
        message_id="<candidate@example.test>",
        delivered_to="contacts@508.dev",
        forwarded_by_name="Reviewer",
        forwarded_by_email="reviewer@508.dev",
        proposed_name="Ada Lovelace",
        proposed_email="ada@example.com",
        subject="Introduction",
        body_text="",
        links=[],
        extraction_method="inline_forward",
        crm_contact_id=None,
        reviewed_by=None,
        reviewed_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_contact_intake_requires_a_delivery_header() -> None:
    message = EmailMessage()
    message["To"] = "contacts@508.dev"
    message.set_content("Not enough to prove alias delivery.")

    assert is_contact_intake_message(message, _settings()) is False

    message["Delivered-To"] = "contacts@508.dev"

    assert is_contact_intake_message(message, _settings()) is True


def test_processor_extracts_inline_forward_identity_and_links(monkeypatch) -> None:
    message = EmailMessage()
    message["Message-ID"] = "<forwarded-contact@example.test>"
    message["From"] = "Reviewer <reviewer@508.dev>"
    message["Delivered-To"] = "<contacts@508.dev>"
    message.set_content(
        "---------- Forwarded message ---------\n"
        "From: Ada Lovelace <ada@example.com>\n"
        "Subject: Introduction\n\n"
        "See https://example.com/ada and https://www.linkedin.com/in/ada.\n"
    )
    captured = {}

    def store_candidate(_settings, candidate):
        captured["candidate"] = candidate
        return _candidate()

    monkeypatch.setattr(
        "five08.worker.contact_email_ingest.upsert_contact_email_candidate",
        store_candidate,
    )

    result = ContactEmailCandidateProcessor(_settings()).process_message(message)

    assert result.candidate_id == "candidate-1"
    candidate = captured["candidate"]
    assert candidate.proposed_name == "Ada Lovelace"
    assert candidate.proposed_email == "ada@example.com"
    assert candidate.extraction_method == "inline_forward"
    assert candidate.links == [
        "https://example.com/ada",
        "https://www.linkedin.com/in/ada",
    ]
