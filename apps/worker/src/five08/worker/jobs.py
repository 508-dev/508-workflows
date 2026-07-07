"""Domain job functions executed by worker actors."""

import base64
import logging
from datetime import datetime, timezone
from email import message_from_bytes
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from five08.redaction import (
    EMAIL_ADDRESS_PATTERN,
    PERCENT_ENCODED_EMAIL_ADDRESS_PATTERN,
)
from five08.worker.config import settings
from five08.worker.crm.docuseal_processor import DocusealAgreementProcessor
from five08.worker.crm.intake_form_processor import IntakeFormProcessor
from five08.worker.crm.people_sync import PeopleSyncProcessor
from five08.worker.crm.processor import ContactSkillsProcessor
from five08.worker.crm.resume_profile_processor import ResumeProfileProcessor
from five08.worker.erpnext_project_sync import ERPNextProjectSyncProcessor
from five08.worker.mailbox_resume_ingest import ResumeMailboxProcessor
from five08.worker.masking import mask_email
from five08.newsletter_sync import NewsletterSyncProcessor
from five08.job_lead_sources import scrape_job_leads

logger = logging.getLogger(__name__)


DOCUSEAL_COMPLETED_AT_UTC_FORMAT = "%Y-%m-%d %H:%M:%S"


def process_contact_skills_job(contact_id: str) -> dict[str, Any]:
    """Process one EspoCRM contact and update their skills."""
    logger.info("Processing queued contact skills job contact_id=%s", contact_id)
    processor = ContactSkillsProcessor()
    result = processor.process_contact_skills(contact_id)
    return result.model_dump()


def process_webhook_event(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process a generic webhook payload and return normalized metadata."""
    event_id = str(payload.get("id", "unknown"))
    received_at = datetime.now(timezone.utc).isoformat()
    logger.info("Processing webhook source=%s event_id=%s", source, event_id)
    return {
        "source": source,
        "event_id": event_id,
        "received_at": received_at,
        "payload_keys": sorted(payload.keys()),
    }


def extract_resume_profile_job(
    contact_id: str,
    attachment_id: str,
    filename: str,
) -> dict[str, Any]:
    """Extract profile updates from an uploaded resume attachment."""
    logger.info(
        "Processing resume extract job contact_id=%s attachment_id=%s",
        contact_id,
        attachment_id,
    )
    processor = ResumeProfileProcessor()
    result = processor.extract_profile_proposal(
        contact_id=contact_id,
        attachment_id=attachment_id,
        filename=filename,
    )
    return result.model_dump()


def apply_resume_profile_job(
    contact_id: str,
    updates: dict[str, Any],
    link_discord: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Apply confirmed CRM profile updates after bot-side confirmation."""
    logger.info("Processing resume apply job contact_id=%s", contact_id)
    processor = ResumeProfileProcessor()
    result = processor.apply_profile_updates(
        contact_id=contact_id,
        updates=updates,
        link_discord=link_discord,
    )
    return result.model_dump()


def process_docuseal_agreement_job(
    email: str,
    completed_at: str,
    submission_id: int,
) -> dict[str, Any]:
    """Mark a CRM contact as having signed the member agreement via Docuseal.

    Job input contract:
    - completed_at is a UTC string, formatted as ``YYYY-MM-DD HH:mm:ss``.
    - Keep it string-based to match JSON job payload serialization constraints.
    """
    logger.info(
        "Processing Docuseal agreement job masked_email=%s submission_id=%s",
        mask_email(email),
        submission_id,
    )
    processor = DocusealAgreementProcessor()
    return processor.process_agreement(email, completed_at, submission_id)


def process_intake_form_job(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Process a Google Forms member intake submission against CRM."""
    email = str(payload.get("email", ""))
    logger.info("Processing intake form job masked_email=%s", mask_email(email))
    processor = IntakeFormProcessor()
    return processor.process_intake(payload=payload)


def process_mailbox_message_job(raw_message_b64: str) -> dict[str, Any]:
    """Process one queued mailbox message."""
    try:
        raw_message = base64.b64decode(raw_message_b64.encode("ascii"), validate=True)
    except Exception as exc:
        logger.warning(
            "Skipping mailbox message job due to invalid payload: %s",
            exc,
        )
        return {
            "sender_email": None,
            "sender_name": None,
            "processed_attachments": 0,
            "skipped_reason": "invalid_message_payload",
        }

    try:
        message = message_from_bytes(raw_message)
        processor = ResumeMailboxProcessor(settings)
        result = processor.process_message(message)
        return result.__dict__
    except Exception as exc:
        logger.warning("Failed processing queued mailbox message: %s", exc)
        return {
            "sender_email": None,
            "sender_name": None,
            "processed_attachments": 0,
            "skipped_reason": "message_processing_error",
        }


def sync_people_from_crm_job() -> dict[str, Any]:
    """Sync a full contacts page-set from CRM into the local people cache."""
    logger.info("Processing CRM people full-sync job")
    processor = PeopleSyncProcessor()
    result = processor.sync_all_contacts()
    return result


def sync_person_from_crm_job(contact_id: str) -> dict[str, Any]:
    """Sync one CRM contact into the local people cache."""
    logger.info("Processing CRM people sync job contact_id=%s", contact_id)
    processor = PeopleSyncProcessor()
    result = processor.sync_contact(contact_id)
    return result


def sync_projects_from_erpnext_job() -> dict[str, Any]:
    """Sync open ERPNext projects into the local project cache."""
    logger.info("Processing ERPNext project sync job")
    processor = ERPNextProjectSyncProcessor()
    return processor.sync_open_projects()


def _mask_newsletter_sync_result(result: dict[str, Any]) -> dict[str, Any]:
    """Mask email addresses before newsletter sync results are persisted."""
    crm_failures = result.get("crm_lookup_failures")
    if isinstance(crm_failures, list):
        for failure in crm_failures:
            if not isinstance(failure, dict):
                continue
            if failure.get("mailbox"):
                failure["mailbox"] = mask_email(str(failure["mailbox"]))
            if failure.get("error"):
                failure["error"] = _mask_emails_in_text(str(failure["error"]))

    providers = result.get("providers")
    if isinstance(providers, dict):
        for provider_result in providers.values():
            if not isinstance(provider_result, dict):
                continue
            failures = provider_result.get("failures")
            if not isinstance(failures, list):
                continue
            for failure in failures:
                if not isinstance(failure, dict):
                    continue
                if failure.get("email"):
                    failure["email"] = mask_email(str(failure["email"]))
                if failure.get("error"):
                    failure["error"] = _mask_emails_in_text(str(failure["error"]))
    return result


def _mask_emails_in_text(text: str) -> str:
    """Mask email-like substrings embedded in free-form error text."""
    text = PERCENT_ENCODED_EMAIL_ADDRESS_PATTERN.sub(
        lambda match: mask_email(unquote(match.group(0))),
        text,
    )
    return EMAIL_ADDRESS_PATTERN.sub(lambda match: mask_email(match.group(0)), text)


def sync_508_members_newsletters_job() -> dict[str, Any]:
    """Sync Migadu member emails into configured newsletter providers."""
    logger.info("Processing 508 members newsletter sync job")
    processor = NewsletterSyncProcessor(settings)
    return _mask_newsletter_sync_result(processor.sync_508_members())


def scrape_job_leads_job(
    source: str = "hackernews_who_is_hiring",
    story_id: int | None = None,
) -> dict[str, Any]:
    """Scrape external job lead sources into the review queue.

    This job intentionally does not publish to Discord. Publishing requires a
    separate approval action in the bot/dashboard layer.
    """
    logger.info("Scraping job leads source=%s story_id=%s", source, story_id)
    return scrape_job_leads(settings, source=source, story_id=story_id)


JOB_FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    process_webhook_event.__name__: process_webhook_event,
    process_contact_skills_job.__name__: process_contact_skills_job,
    extract_resume_profile_job.__name__: extract_resume_profile_job,
    apply_resume_profile_job.__name__: apply_resume_profile_job,
    process_intake_form_job.__name__: process_intake_form_job,
    process_mailbox_message_job.__name__: process_mailbox_message_job,
    sync_people_from_crm_job.__name__: sync_people_from_crm_job,
    sync_person_from_crm_job.__name__: sync_person_from_crm_job,
    sync_projects_from_erpnext_job.__name__: sync_projects_from_erpnext_job,
    sync_508_members_newsletters_job.__name__: sync_508_members_newsletters_job,
    process_docuseal_agreement_job.__name__: process_docuseal_agreement_job,
    scrape_job_leads_job.__name__: scrape_job_leads_job,
}
