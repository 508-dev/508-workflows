"""Unit tests for resume profile worker processor."""

import json

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
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

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
    assert result.proposed_updates["skills"] == ["python", "fastapi"]
    assert result.new_skills == ["python", "fastapi"]
    assert isinstance(result.proposed_updates["cSkillAttrs"], str)
    assert json.loads(result.proposed_updates["cSkillAttrs"])["python"]["strength"] == 5
    assert (
        json.loads(result.proposed_updates["cSkillAttrs"])["fastapi"]["strength"] == 4
    )
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


def test_extract_profile_proposal_merges_and_serializes_website_and_skill_attrs() -> (
    None
):
    """Proposal should merge website links and serialize cSkillAttrs as JSON."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cWebsiteLink": ["https://portfolio.example.com"],
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-2"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email="new@example.com",
        github_username=None,
        linkedin_url=None,
        phone=None,
        website_links=["https://portfolio.example.com", "https://www.blog.example.com"],
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["TypeScript", "React Native"],
        skill_attrs={"typescript": {"strength": 4}, "react native": {"strength": 3}},
        confidence=0.8,
        source="gpt-4o-mini",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-2",
        attachment_id="att-2",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["cWebsiteLink"] == [
        "https://portfolio.example.com",
        "https://blog.example.com",
    ]
    assert result.proposed_updates["skills"] == ["typescript", "react native"]
    assert isinstance(result.proposed_updates["cSkillAttrs"], str)
    assert json.loads(result.proposed_updates["cSkillAttrs"]) == {
        "react native": {"strength": 3},
        "typescript": {"strength": 4},
    }


def test_extract_profile_proposal_deduplicates_skills_in_confirmation() -> None:
    """Duplicate extracted or existing skills should not appear in confirmation updates."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "skills": ["Python", "python", "JavaScript", "javascript"],
        "cSkillAttrs": '{"python":{"strength":5},"javascript":{"strength":4}}',
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-3"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=["Python", "node", "Node.js", "python"],
        skill_attrs={"python": {"strength": 5}, "node": {"strength": 2}},
        confidence=0.8,
        source="gpt-4o-mini",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-3",
        attachment_id="att-3",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["skills"] == ["python", "javascript", "node"]
    assert result.new_skills == ["node"]


def test_extract_profile_proposal_includes_seniority_update() -> None:
    """Extracted seniority should map to the CRM cSeniority field."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cSeniority": "junior",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-4"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        seniority_level="Senior",
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=[],
        skill_attrs={},
        confidence=0.8,
        source="gpt-4o-mini",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-4",
        attachment_id="att-4",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["cSeniority"] == "senior"


def test_apply_profile_updates_appends_resume_email_as_primary_emailAddressData() -> (
    None
):
    """Resume email updates should append to emailAddressData instead of overwriting."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.crm.get_contact.return_value = {
        "emailAddressData": [
            {
                "emailAddress": "legacy@example.com",
                "lower": "legacy@example.com",
                "primary": True,
                "optOut": False,
                "invalid": False,
            }
        ]
    }
    result = processor.apply_profile_updates(
        contact_id="contact-3",
        updates={
            "emailAddress": "NewPerson@Example.Com",
            "phoneNumber": "5551112222",
        },
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    payload = processor.crm.update_contact.call_args[0][1]
    assert "emailAddress" not in payload
    email_data = payload["emailAddressData"]
    assert isinstance(email_data, list)
    by_lower = {item["lower"]: item for item in email_data}
    assert by_lower["newperson@example.com"]["primary"] is True
    assert by_lower["legacy@example.com"]["primary"] is False
    assert payload["phoneNumber"] == "5551112222"


def test_extract_profile_proposal_normalizes_existing_skill_punctuation() -> None:
    """Existing punctuation-heavy skills should normalize to search-friendly canonical forms."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

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
    processor.skills_extractor.canonicalize_skill.side_effect = (
        lambda v: str(v).strip().lower()
    )

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
    assert update_payload["skills"] == ["python", "fastapi"]
    assert update_payload["cDiscordUserID"] == "123"
    assert update_payload["cDiscordUsername"] == "member#0001 (ID: 123)"


def test_apply_profile_updates_normalizes_csv_skills_to_array() -> None:
    """Apply should convert comma-separated skills into an array before updating."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-2",
        updates={"skills": "Python, FastAPI, Rust"},
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    update_payload = processor.crm.update_contact.call_args[0][1]
    assert update_payload["skills"] == ["python", "fastapi", "rust"]


def test_apply_profile_updates_serializes_skill_attrs() -> None:
    """Apply should serialize cSkillAttrs payload as compact JSON."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-4",
        updates={
            "cSkillAttrs": {"python": {"strength": 5}, "react": {"strength": 3}},
            "skills": "Python",
        },
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["skills"] == ["python"]
    assert json.loads(payload["cSkillAttrs"]) == {
        "python": {"strength": 5},
        "react": {"strength": 3},
    }


def test_apply_profile_updates_allows_cSeniority_field() -> None:
    """Allowed updates should include cSeniority normalization."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-5",
        updates={"cSeniority": "Senior"},
    )

    assert result.success is True
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["cSeniority"] == "senior"


def test_apply_profile_updates_normalizes_skill_aliases_for_api_payload() -> None:
    """Alias-heavy skills should be normalized into shared canonical forms."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-6",
        updates={
            "skills": ["Node.js", "Node.js", "node"],
            "cSkillAttrs": {"Node.js": {"strength": 4}, "node": {"strength": 5}},
        },
    )

    assert result.success is True
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["skills"] == ["node"]
    assert json.loads(payload["cSkillAttrs"]) == {"node": {"strength": 5}}


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
