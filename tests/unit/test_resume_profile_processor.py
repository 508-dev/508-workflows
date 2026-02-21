"""Unit tests for resume profile worker processor."""

from unittest.mock import Mock

from five08.worker.crm.resume_profile_processor import ResumeProfileProcessor
from five08.worker.models import ExtractedSkills, ResumeExtractedProfile


def test_extract_profile_proposal_filters_508_email() -> None:
    """Extract proposal should skip @508.dev email updates by policy."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cGitHubUsername": "old-gh",
        "cLinkedInUrl": "https://linkedin.com/in/old",
        "phoneNumber": "1234567890",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email="new@508.dev",
        github_username="new-gh",
        linkedin_url="https://linkedin.com/in/new",
        phone="14155551234",
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["Python", "FastAPI"],
        confidence=0.8,
        source="gpt-4o-mini",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-1",
        attachment_id="att-1",
        filename="resume.pdf",
    )

    assert result.success is True
    assert "emailAddress" not in result.proposed_updates
    assert result.proposed_updates["cGitHubUsername"] == "new-gh"
    assert result.proposed_updates["cLinkedInUrl"] == "https://linkedin.com/in/new"
    assert result.proposed_updates["phoneNumber"] == "14155551234"
    assert result.proposed_updates["skills"] == "Python, FastAPI"
    assert result.new_skills == ["Python", "FastAPI"]
    assert any(item.field == "emailAddress" for item in result.skipped)


def test_apply_profile_updates_adds_discord_and_filters_email() -> None:
    """Apply should include Discord link values and prevent @508.dev email writes."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-1",
        updates={
            "emailAddress": "member@508.dev",
            "cGitHubUsername": "new-gh",
            "phoneNumber": "14155551234",
            "skills": "Python, FastAPI",
        },
        link_discord={"user_id": "123", "username": "member#0001"},
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    update_payload = processor.crm.update_contact.call_args[0][1]
    assert "emailAddress" not in update_payload
    assert update_payload["cGitHubUsername"] == "new-gh"
    assert update_payload["phoneNumber"] == "14155551234"
    assert update_payload["skills"] == "Python, FastAPI"
    assert update_payload["cDiscordUserID"] == "123"
    assert update_payload["cDiscordUsername"] == "member#0001 (ID: 123)"
