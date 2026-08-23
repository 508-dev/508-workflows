"""Recipient-guarded ingestion of contact candidates from forwarded mail."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from email.utils import parseaddr

from five08.contact_email_candidates import (
    ContactEmailCandidateInput,
    upsert_contact_email_candidate,
)
from five08.worker.config import WorkerSettings

_EMAIL_IN_HEADER = re.compile(
    r"(?i)([a-z0-9.!#$%&'*+/=?^_{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+)"
)
_FORWARDED_HEADER = re.compile(r"(?im)^From:\s*(.+?)\s*$")
_FORWARDED_SUBJECT = re.compile(r"(?im)^Subject:\s*(.+?)\s*$")
_LINK = re.compile(r"https?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_MAX_BODY_CHARS = 20_000
_MAX_LINKS = 25


@dataclass(frozen=True)
class ContactEmailIngestResult:
    """Metadata returned by one contact-intake worker job."""

    candidate_id: str | None
    proposed_email: str | None
    skipped_reason: str | None = None


def configured_contact_intake_address(settings: WorkerSettings) -> str:
    """Return the normalized alias that is allowed to create candidates."""
    value = str(getattr(settings, "contact_email_intake_address", "") or "").strip()
    return value.casefold() or "contacts@508.dev"


def is_contact_intake_message(message: Message, settings: WorkerSettings) -> bool:
    """Require a delivery header proving the message arrived through the alias."""
    target = configured_contact_intake_address(settings)
    for header_name in ("Delivered-To", "X-Original-To"):
        for raw_value in message.get_all(header_name, []):
            addresses = {
                match.casefold() for match in _EMAIL_IN_HEADER.findall(str(raw_value))
            }
            if target in addresses:
                return True
    return False


class ContactEmailCandidateProcessor:
    """Create a review candidate without mutating EspoCRM."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings

    def process_message(self, message: Message) -> ContactEmailIngestResult:
        """Persist a candidate only for mail delivered to the configured alias."""
        if not is_contact_intake_message(message, self.settings):
            return ContactEmailIngestResult(
                candidate_id=None,
                proposed_email=None,
                skipped_reason="recipient_not_contact_intake",
            )

        forwarded_by_name, forwarded_by_email = _sender_identity(message)
        source_message, extraction_method = _source_message(message)
        proposed_name, proposed_email = _source_identity(
            source_message,
            fallback_body=_message_text(message),
        )
        body_text = _message_text(source_message)
        candidate = upsert_contact_email_candidate(
            self.settings,
            ContactEmailCandidateInput(
                message_id=_message_id(message),
                delivered_to=configured_contact_intake_address(self.settings),
                forwarded_by_name=forwarded_by_name,
                forwarded_by_email=forwarded_by_email,
                proposed_name=proposed_name,
                proposed_email=proposed_email,
                subject=_subject(source_message, body_text),
                body_text=body_text[:_MAX_BODY_CHARS],
                links=_links(body_text),
                extraction_method=extraction_method,
            ),
        )
        return ContactEmailIngestResult(
            candidate_id=candidate.id,
            proposed_email=candidate.proposed_email,
        )


def _message_id(message: Message) -> str:
    value = str(message.get("Message-ID", "")).strip()
    if value:
        return value
    return f"sha256:{hashlib.sha256(message.as_bytes()).hexdigest()}"


def _sender_identity(message: Message) -> tuple[str | None, str | None]:
    name, email_address = parseaddr(str(message.get("From", "")).strip())
    normalized_email = email_address.strip().casefold() or None
    return name.strip() or None, normalized_email


def _source_message(message: Message) -> tuple[Message, str]:
    for part in message.walk():
        if part.get_content_type() != "message/rfc822":
            continue
        payload = part.get_payload()
        if isinstance(payload, list) and payload and isinstance(payload[0], Message):
            return payload[0], "attached_forward"
        decoded = part.get_payload(decode=True)
        if isinstance(decoded, bytes):
            return message_from_bytes(decoded), "attached_forward"
    body = _message_text(message)
    if _FORWARDED_HEADER.search(body):
        return message, "inline_forward"
    return message, "direct"


def _source_identity(
    message: Message, *, fallback_body: str
) -> tuple[str | None, str | None]:
    name, email_address = _sender_identity(message)
    if email_address and not _FORWARDED_HEADER.search(fallback_body):
        return name, email_address

    match = _FORWARDED_HEADER.search(fallback_body)
    if match:
        forwarded_name, forwarded_email = parseaddr(match.group(1).strip())
        if forwarded_email.strip():
            return forwarded_name.strip() or None, forwarded_email.strip().casefold()
    return name, email_address


def _subject(message: Message, body_text: str) -> str | None:
    subject = str(message.get("Subject", "")).strip()
    if subject:
        return subject
    match = _FORWARDED_SUBJECT.search(body_text)
    return match.group(1).strip() if match else None


def _message_text(message: Message) -> str:
    values: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/plain":
            continue
        if str(part.get("Content-Disposition", "")).lower().startswith("attachment"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        values.append(
            payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        )
    if values:
        return "\n".join(values).strip()
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(
            message.get_content_charset() or "utf-8", errors="replace"
        ).strip()
    return ""


def _links(body_text: str) -> list[str]:
    links: list[str] = []
    for raw_link in _LINK.findall(body_text):
        link = raw_link.rstrip(".,;:!?")
        if link and link not in links:
            links.append(link)
        if len(links) >= _MAX_LINKS:
            break
    return links
