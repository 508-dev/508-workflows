"""Contact skills processing workflow."""

import logging
from typing import Any

from five08.clients.espo import EspoAPI, EspoAPIError
from five08.worker.config import settings
from five08.worker.crm.document_processor import DocumentProcessor
from five08.worker.crm.skills_extractor import SkillsExtractor
from five08.worker.models import ContactData, ExtractedSkills, SkillsExtractionResult

logger = logging.getLogger(__name__)


class EspoCRMClient:
    """Contact-centric Espo helper backed by shared five08 client."""

    def __init__(self) -> None:
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.api = EspoAPI(api_url, settings.espo_api_key)

    def get_contact(self, contact_id: str) -> ContactData:
        try:
            raw = self.api.request("GET", f"Contact/{contact_id}")
            return ContactData.model_validate(raw)
        except EspoAPIError as exc:
            logger.error("Error getting contact %s: %s", contact_id, exc)
            raise ValueError(f"Failed to get contact {contact_id}: {exc}") from exc

    def get_contact_attachments(self, contact_id: str) -> list[dict[str, Any]]:
        try:
            raw = self.api.request("GET", f"Contact/{contact_id}/attachments")
            attachments = raw.get("list", [])
            return attachments if isinstance(attachments, list) else []
        except EspoAPIError as exc:
            logger.error("Error getting contact attachments %s: %s", contact_id, exc)
            return []

    def download_attachment(self, attachment_id: str) -> bytes | None:
        try:
            return self.api.download_file(f"Attachment/{attachment_id}/download")
        except EspoAPIError as exc:
            logger.error("Error downloading attachment %s: %s", attachment_id, exc)
            return None

    def update_contact_skills(self, contact_id: str, skills: list[str]) -> bool:
        try:
            skills_text = ", ".join(skills)
            self.api.request("PATCH", f"Contact/{contact_id}", {"skills": skills_text})
            return True
        except EspoAPIError as exc:
            logger.error("Error updating contact %s skills: %s", contact_id, exc)
            return False


class ContactSkillsProcessor:
    """Process a contact's resume attachments and update EspoCRM skills."""

    def __init__(self) -> None:
        self.espocrm_client = EspoCRMClient()
        self.document_processor = DocumentProcessor()
        self.skills_extractor = SkillsExtractor()

    def process_contact_skills(self, contact_id: str) -> SkillsExtractionResult:
        """Extract and persist new skills for the given contact."""
        try:
            contact = self.espocrm_client.get_contact(contact_id)
            existing_skills = self._parse_existing_skills(contact.skills)

            attachments = self.espocrm_client.get_contact_attachments(contact_id)
            resume_attachments = self._filter_resume_attachments(attachments)

            if not resume_attachments:
                return SkillsExtractionResult(
                    contact_id=contact_id,
                    extracted_skills=ExtractedSkills(
                        skills=[], confidence=0.0, source="no_resume"
                    ),
                    existing_skills=existing_skills,
                    new_skills=[],
                    updated_skills=existing_skills,
                    success=False,
                    error="No resume attachments found",
                )

            extracted_skills, average_confidence = self._extract_from_attachments(
                resume_attachments
            )

            if not extracted_skills:
                return SkillsExtractionResult(
                    contact_id=contact_id,
                    extracted_skills=ExtractedSkills(
                        skills=[], confidence=0.0, source="extraction_failed"
                    ),
                    existing_skills=existing_skills,
                    new_skills=[],
                    updated_skills=existing_skills,
                    success=False,
                    error="Failed to extract skills from attachments",
                )

            extracted = ExtractedSkills(
                skills=extracted_skills,
                confidence=average_confidence,
                source="document_analysis",
            )

            existing_lower = {item.lower() for item in existing_skills}
            new_skills = [
                skill
                for skill in extracted.skills
                if skill.lower() not in existing_lower
            ]
            updated_skills = existing_skills + new_skills

            if new_skills:
                success = self.espocrm_client.update_contact_skills(
                    contact_id=contact_id, skills=updated_skills
                )
            else:
                success = True

            return SkillsExtractionResult(
                contact_id=contact_id,
                extracted_skills=extracted,
                existing_skills=existing_skills,
                new_skills=new_skills,
                updated_skills=updated_skills,
                success=success,
                error=None if success else "Failed to update contact skills",
            )
        except Exception as exc:
            logger.error("Error processing contact %s skills: %s", contact_id, exc)
            return SkillsExtractionResult(
                contact_id=contact_id,
                extracted_skills=ExtractedSkills(
                    skills=[], confidence=0.0, source="error"
                ),
                existing_skills=[],
                new_skills=[],
                updated_skills=[],
                success=False,
                error=str(exc),
            )

    def _extract_from_attachments(
        self, attachments: list[dict[str, Any]]
    ) -> tuple[list[str], float]:
        all_skills: list[str] = []
        confidence_sum = 0.0
        processed_count = 0

        for attachment in attachments[: settings.max_attachments_per_contact]:
            attachment_id = str(attachment.get("id", ""))
            attachment_name = str(attachment.get("name", "unknown"))
            if not attachment_id:
                continue

            try:
                content = self.espocrm_client.download_attachment(attachment_id)
                if not content:
                    continue
                text = self.document_processor.extract_text(content, attachment_name)
                extracted = self.skills_extractor.extract_skills(text)
                all_skills.extend(extracted.skills)
                confidence_sum += extracted.confidence
                processed_count += 1
            except Exception as exc:
                logger.warning(
                    "Skipping attachment id=%s name=%s error=%s",
                    attachment_id,
                    attachment_name,
                    exc,
                )

        unique_skills = sorted(set(all_skills))
        avg_confidence = confidence_sum / processed_count if processed_count else 0.0
        return unique_skills, avg_confidence

    def _parse_existing_skills(self, skills_text: str | None) -> list[str]:
        if not skills_text:
            return []
        parsed = [skill.strip() for skill in skills_text.split(",")]
        return [skill for skill in parsed if skill]

    def _filter_resume_attachments(
        self, attachments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        resume_attachments: list[dict[str, Any]] = []
        resume_keywords = settings.parsed_resume_keywords
        allowed_extensions = {f".{ext}" for ext in settings.allowed_file_extensions}

        for attachment in attachments:
            name = str(attachment.get("name", "")).lower()
            ext = f".{name.split('.')[-1]}" if "." in name else ""
            if ext not in allowed_extensions:
                continue
            if not any(keyword in name for keyword in resume_keywords):
                continue
            resume_attachments.append(attachment)

        return resume_attachments
