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
    TrustedIntroContactProcessor,
    is_contact_intake_message,
    is_forwarded_intro_message,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        contact_email_intake_address="contacts@508.dev",
        email_username="workflows@508.dev",
        email_require_sender_auth_headers=True,
        espo_base_url="https://crm.example.test",
        espo_api_key="test-key",
    )


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


def test_forwarded_intro_requires_an_intro_signal_and_distinct_identity() -> None:
    message = EmailMessage()
    message["From"] = "Michael <michael@508.dev>"
    message["Subject"] = "Fwd: Connecting you two"
    message.set_content(
        "---------- Forwarded message ---------\n"
        "From: Ada Lovelace <ada@example.com>\n"
        "Subject: Introduction\n\n"
        "I wanted to introduce Ada. Her work is at https://example.com/ada.\n"
    )

    assert is_forwarded_intro_message(message) is True

    message.set_content(
        "---------- Forwarded message ---------\n"
        "From: Ada Lovelace <ada@example.com>\n"
        "Subject: Invoice\n\n"
        "Please see the attached invoice.\n"
    )
    message.replace_header("Subject", "Fwd: Invoice")

    assert is_forwarded_intro_message(message) is False


def test_trusted_intro_creates_candidate_and_crm_contact(monkeypatch) -> None:
    message = EmailMessage()
    message["From"] = "Michael <michael@508.dev>"
    message["Subject"] = "Fwd: Introduction"
    message.set_content(
        "---------- Forwarded message ---------\n"
        "From: Ada Lovelace <ada@example.com>\n"
        "Subject: Introduction\n\n"
        "I'd like to introduce Ada: https://example.com/ada\n"
    )
    candidate = _candidate()
    monkeypatch.setattr(
        "five08.worker.contact_email_ingest.upsert_contact_email_candidate",
        lambda *_args: candidate,
    )
    monkeypatch.setattr(
        "five08.worker.contact_email_ingest.get_contact_email_candidate",
        lambda *_args: candidate,
    )
    monkeypatch.setattr(
        "five08.worker.contact_email_ingest.review_contact_email_candidate",
        lambda *_args, **_kwargs: candidate,
    )

    processor = TrustedIntroContactProcessor(_settings())
    monkeypatch.setattr(
        processor.resume_processor,
        "_has_authenticated_sender",
        lambda _message: True,
    )
    monkeypatch.setattr(
        processor.resume_processor,
        "_sender_is_authorized",
        lambda _email: True,
    )
    monkeypatch.setattr(
        processor.resume_processor,
        "_find_contact_by_email",
        lambda _email: None,
    )
    created: dict[str, str] = {}

    def create_contact(email: str, name: str) -> dict[str, str]:
        created["email"] = email
        created["name"] = name
        return {"id": "crm-contact-1"}

    monkeypatch.setattr(
        processor.resume_processor, "_create_contact_for_email", create_contact
    )
    monkeypatch.setattr(processor, "_audit_outcome", lambda **_kwargs: None)

    result = processor.process_message(message)

    assert result.candidate_id == "candidate-1"
    assert result.crm_contact_id == "crm-contact-1"
    assert result.action == "created"
    assert created == {"email": "ada@example.com", "name": "Ada Lovelace"}
