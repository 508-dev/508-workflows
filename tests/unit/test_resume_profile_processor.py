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
    processor._record_processing_run = Mock()

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cGitHubUsername": "old-gh",
        "cLinkedInUrl": "https://linkedin.com/in/old",
        "phoneNumber": "1234567890",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-1"
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
        skill_attrs={
            "python": {"strength": 5},
            "fastapi": {"strength": 4},
        },
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
    assert result.proposed_updates["skills"] == ["Python", "FastAPI"]
    assert result.new_skills == ["Python", "FastAPI"]
    assert any(item.field == "emailAddress" for item in result.skipped)
    processor.crm.update_contact.assert_called_once()
    update_contact_payload = processor.crm.update_contact.call_args.args[1]
    assert "cResumeLastProcessed" in update_contact_payload
    assert isinstance(update_contact_payload["cResumeLastProcessed"], str)
    processor._record_processing_run.assert_called_once()
    record_kwargs = processor._record_processing_run.call_args.kwargs
    assert record_kwargs["status"] == "succeeded"
    assert record_kwargs["contact_id"] == "contact-1"
    assert record_kwargs["attachment_id"] == "att-1"


def test_extract_profile_proposal_normalizes_existing_skill_punctuation() -> None:
    """Existing punctuation-heavy skills should normalize to search-friendly canonical forms."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "skills": "Node.js, A/B Testing",
        "cSkillAttrs": '{"node.js":{"strength":4},"a/b testing":{"strength":2}}',
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-10"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["node", "ab testing", "product management"],
        skill_attrs={
            "node": {"strength": 5},
            "ab testing": {"strength": 3},
            "product management": {"strength": 4},
        },
        confidence=0.8,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: {
        "node.js": "node",
        "a/b testing": "ab testing",
        "node": "node",
        "ab testing": "ab testing",
    }.get(str(v).strip().lower(), str(v).strip().lower())

    result = processor.extract_profile_proposal(
        contact_id="contact-10",
        attachment_id="att-10",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["skills"] == [
        "node",
        "ab testing",
        "product management",
    ]


def test_extract_profile_proposal_with_strength_change_only_no_skill_proposal() -> None:
    """Strength-only changes to existing skills should not be shown as editable updates."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "skills": ["Python"],
        "cSkillAttrs": '{"python":{"strength":5}}',
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-20"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["python"],
        skill_attrs={"python": {"strength": 3}},
        confidence=0.8,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-20",
        attachment_id="att-20",
        filename="resume.pdf",
    )

    assert result.success is True
    assert "skills" not in result.proposed_updates
    assert not any(item.field == "skills" for item in result.proposed_changes)


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
            "skills": ["Python", "FastAPI"],
        },
        link_discord={"user_id": "123", "username": "member#0001"},
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    update_payload = processor.crm.update_contact.call_args[0][1]
    assert "emailAddress" not in update_payload
    assert update_payload["cGitHubUsername"] == "new-gh"
    assert update_payload["phoneNumber"] == "14155551234"
    assert update_payload["skills"] == ["Python", "FastAPI"]
    assert update_payload["cDiscordUserID"] == "123"
    assert update_payload["cDiscordUsername"] == "member#0001 (ID: 123)"


def test_apply_profile_updates_normalizes_csv_skills_string_to_array() -> None:
    """Apply should normalize comma-separated skill strings into array payloads."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-2",
        updates={
            "skills": "Python, FastAPI, React, ,JavaScript",
        },
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    update_payload = processor.crm.update_contact.call_args.args[1]
    assert update_payload["skills"] == ["Python", "FastAPI", "React", "JavaScript"]


def test_extract_profile_proposal_records_failed_run() -> None:
    """Failed extraction should still be written to the processing ledger."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()

    processor.crm.get_contact.return_value = {"emailAddress": "member@example.com"}
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.get_content_hash.return_value = "hash-2"
    processor.document_processor.extract_text.side_effect = ValueError("parse failed")

    result = processor.extract_profile_proposal(
        contact_id="contact-2",
        attachment_id="att-2",
        filename="broken.pdf",
    )

    assert result.success is False
    processor._record_processing_run.assert_called_once()
    record_kwargs = processor._record_processing_run.call_args.kwargs
    assert record_kwargs["status"] == "failed"
    assert record_kwargs["contact_id"] == "contact-2"
    assert record_kwargs["attachment_id"] == "att-2"
    assert record_kwargs["content_hash"] == "hash-2"
