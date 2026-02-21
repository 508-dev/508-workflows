"""Domain job functions executed by worker actors."""

import logging
from datetime import datetime, timezone
from typing import Any

from five08.worker.crm.people_sync import PeopleSyncProcessor
from five08.worker.crm.processor import ContactSkillsProcessor

logger = logging.getLogger(__name__)


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
