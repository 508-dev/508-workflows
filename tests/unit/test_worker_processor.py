"""Unit tests for contact skills processor."""

from unittest.mock import Mock

from five08.worker.crm.processor import ContactSkillsProcessor
from five08.worker.models import ExtractedSkills


def test_process_contact_skills_merges_and_updates() -> None:
    """Processor should merge extracted skills with existing and update CRM."""
    processor = ContactSkillsProcessor()

    processor.espocrm_client = Mock()
    processor.document_processor = Mock()
    processor.skills_extractor = Mock()

    processor.espocrm_client.get_contact.return_value = Mock(skills="Python, Redis")
    processor.espocrm_client.get_contact_attachments.return_value = [
        {"id": "a-1", "name": "resume.pdf"}
    ]
    processor.espocrm_client.download_attachment.return_value = b"file-content"
    processor.document_processor.extract_text.return_value = "Python FastAPI Docker"
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["python", "fastapi", "docker"],
        confidence=0.9,
        source="heuristic",
    )
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda value: str(value).strip().lower()
    )
    processor.espocrm_client.update_contact_skills.return_value = True

    result = processor.process_contact_skills("contact-1")

    assert result.success is True
    assert sorted(result.new_skills) == ["docker", "fastapi"]
    assert set(result.updated_skills) == {"python", "redis", "fastapi", "docker"}
