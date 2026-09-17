"""Domain job functions executed by worker actors."""

import base64
import logging
from datetime import datetime, timezone
from email import message_from_bytes
from collections.abc import Callable
from typing import Any, cast
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
from five08.knowledge.store import PostgresKnowledgeStore
from five08.newsletter_sync import NewsletterSyncProcessor
from five08.job_lead_sources import scrape_job_leads
from five08.wiki_editing.models import WikiAuthoringWorkItem, WikiSourceReference
from five08.wiki_editing.omp import OmpWikiAuthoringRunner, WikiAuthoringMaterial
from five08.wiki_editing.service import (
    WikiEditingConfigurationError,
    WikiEditingService,
    build_outline_writer_client,
)
from five08.wiki_editing.store import PostgresWikiEditingStore

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


def _build_wiki_org_knowledge_search(
    store: PostgresKnowledgeStore,
) -> Callable[[str, WikiAuthoringWorkItem], list[WikiAuthoringMaterial]]:
    """Return the narrowly scoped organization-memory reader available to OMP."""

    def search(
        question: str,
        work: WikiAuthoringWorkItem,
    ) -> list[WikiAuthoringMaterial]:
        evidence_items = store.search_evidence(
            question=question,
            organization_id=work.request.organization_id,
            actor_id=work.request.actor_id,
            project_ids=(),
            allow_private=False,
            allow_project=False,
            allow_org=True,
            limit=4,
            semantic_candidate_limit=0,
        )
        return [
            WikiAuthoringMaterial(
                source=WikiSourceReference(
                    source_type="memory_fact",
                    source_ref=evidence.source_ref,
                    source_url=evidence.url,
                    title=evidence.title,
                ),
                text=evidence.excerpt,
                visibility="org",
            )
            for evidence in evidence_items
            if evidence.visibility == "org"
        ][:4]

    return search


def _build_wiki_editing_service() -> WikiEditingService:
    """Construct the worker-only service that owns bounded OMP authoring."""
    launcher_path = settings.resolved_wiki_omp_launcher_path
    if not settings.wiki_authoring_configured or launcher_path is None:
        raise WikiEditingConfigurationError(
            "Wiki authoring worker is not fully configured."
        )

    def outline_client_factory():
        return build_outline_writer_client(settings)

    knowledge_store = PostgresKnowledgeStore(settings)
    authoring_runner = OmpWikiAuthoringRunner(
        omp_executable=str(settings.wiki_omp_command or ""),
        omp_launcher_path=str(launcher_path),
        openrouter_api_key=str(settings.openrouter_api_key or ""),
        model=str(settings.wiki_omp_model or ""),
        thinking=str(settings.wiki_omp_thinking or ""),
        startup_timeout_seconds=cast(
            float,
            settings.wiki_omp_startup_timeout_seconds,
        ),
        authoring_timeout_seconds=cast(
            float,
            settings.wiki_omp_authoring_timeout_seconds,
        ),
        outline_client_factory=outline_client_factory,
        allowed_collection_id=str(settings.wiki_outline_collection_id or ""),
        knowledge_search=_build_wiki_org_knowledge_search(knowledge_store),
    )
    return WikiEditingService(
        settings=settings,
        store=PostgresWikiEditingStore(settings),
        outline_client_factory=outline_client_factory,
        authoring_runner=authoring_runner,
    )


def author_wiki_edit_proposal_job(
    proposal_id: str,
    organization_id: str,
) -> dict[str, Any]:
    """Run the retryable draft-authoring phase; publishing stays user-confirmed."""
    normalized_proposal_id = proposal_id.strip()
    normalized_organization_id = organization_id.strip()
    if not normalized_proposal_id or not normalized_organization_id:
        raise ValueError("Wiki authoring job requires proposal and organization IDs.")

    logger.info(
        "Authoring wiki proposal proposal_id=%s organization_id=%s",
        normalized_proposal_id,
        normalized_organization_id,
    )
    response = _build_wiki_editing_service().author_proposal(
        normalized_proposal_id,
        organization_id=normalized_organization_id,
    )
    return response.model_dump(mode="json")


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
    author_wiki_edit_proposal_job.__name__: author_wiki_edit_proposal_job,
}
