"""Domain job functions executed by worker actors."""

import logging
from datetime import datetime, timezone
from typing import Any

from five08.worker.crm.processor import ContactSkillsProcessor
from five08.worker.models import ExtractedSkills, SkillsExtractionResult

logger = logging.getLogger(__name__)


def process_contact_skills_job(contact_id: str) -> dict[str, Any]:
    """Process one EspoCRM contact and update their skills."""
    logger.info("Processing queued contact skills job contact_id=%s", contact_id)
    try:
        processor = ContactSkillsProcessor()
    except Exception as exc:
        logger.exception(
            "Failed to initialize ContactSkillsProcessor for contact_id=%s error=%s",
            contact_id,
            exc,
        )
        return SkillsExtractionResult(
            contact_id=contact_id,
            extracted_skills=ExtractedSkills(
                skills=[],
                confidence=0.0,
                source="initialization_error",
            ),
            existing_skills=[],
            new_skills=[],
            updated_skills=[],
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        ).model_dump()

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
