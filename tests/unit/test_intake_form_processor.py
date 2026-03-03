"""Unit tests for Google Forms intake processor."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from five08.resume_extractor import ResumeExtractedProfile
from five08.worker.crm.intake_form_processor import IntakeFormProcessor


def test_intake_form_processor_creates_prospect_when_not_found() -> None:
    """Form submitter with no CRM match should create a new prospect contact."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {"list": []},
        {"id": "contact-1"},
    ]

    result = processor.process_intake(
        payload={
            "email": "new@example.com",
            "first_name": "New",
            "last_name": "Person",
            "github_username": "https://github.com/newdev",
            "primary_skills_interests": "AI and systems",
            "top_question_about_508": "How does it work?",
            "form_id": "form-1",
        }
    )

    assert result["success"] is True
    assert result["created"] is True
    assert result["contact_id"] == "contact-1"
    assert processor.api.request.call_count == 2
    create_call = processor.api.request.call_args_list[1]
    create_payload = create_call.args[2]
    assert create_payload["cGitHubUsername"] == "newdev"


def test_intake_form_processor_rejects_member_updates() -> None:
    """Existing member contacts should not be updated from intake submissions."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {
        "list": [
            {
                "id": "contact-1",
                "type": "Member",
                "cDiscordRoles": "Member",
            }
        ]
    }

    result = processor.process_intake(
        payload={
            "email": "existing@member.com",
            "first_name": "Current",
            "last_name": "Member",
            "form_id": "form-1",
        }
    )

    assert result["success"] is False
    assert result["error"] == "Contact already exists as member"


def test_intake_form_processor_rejects_member_agreement_signed_updates() -> None:
    """Signed member agreement should block intake updates even without role marker."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {
        "list": [
            {
                "id": "contact-1",
                "type": "Person",
                "cMemberAgreementSignedAt": "2026-02-25T12:00:00Z",
            }
        ]
    }

    result = processor.process_intake(
        payload={
            "email": "existing@member.com",
            "first_name": "Current",
            "last_name": "Member",
            "form_id": "form-1",
        }
    )

    assert result["success"] is False
    assert result["error"] == "Contact already exists as member"


def test_intake_form_processor_rejects_duplicate_contacts() -> None:
    """Multiple CRM matches should fail without mutating any record."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {
        "list": [{"id": "contact-1"}, {"id": "contact-2"}],
    }

    result = processor.process_intake(
        payload={
            "email": "duplicate@example.com",
            "first_name": "Dupe",
            "last_name": "Entry",
            "form_id": "form-1",
        }
    )

    assert result["success"] is False
    assert result["error"] == "Multiple contacts found for email"


def test_build_intake_updates_normalizes_form_skills_to_lowercase() -> None:
    """Skill tags from form labels should be canonicalized to lowercase list values."""
    processor = IntakeFormProcessor()

    updates = processor._build_intake_updates(
        email="new@example.com",
        first_name="New",
        last_name="Person",
        payload={
            "skill_proficiency_next_js": "5",
            "skill_proficiency_project_management": "4",
            "skill_proficiency_ai_ml_engineering": "1",
            "github_username": "person",
        },
        include_email=True,
    )

    assert updates["skills"] == ["ai ml engineering", "next js", "project management"]


def test_build_intake_updates_normalizes_primary_role() -> None:
    """Primary role should normalize to lowercase no-space values for cRoles."""
    processor = IntakeFormProcessor()

    updates = processor._build_intake_updates(
        email="new@example.com",
        first_name="New",
        last_name="Person",
        payload={
            "primary_role": "Developer, Data Scientist, Biz Dev, Staff Engineering"
        },
        include_email=True,
    )

    assert updates["cRoles"] == [
        "developer",
        "data_scientist",
        "biz_dev",
        "staff_engineering",
    ]


def test_build_resume_updates_includes_website_links_as_url_multiple() -> None:
    """Website links extracted from resume should be set to cWebsiteLink as an array."""
    processor = IntakeFormProcessor()
    processor.document_processor = MagicMock()
    processor.resume_extractor = MagicMock()
    processor.document_processor.extract_text.return_value = "resume text"
    processor.resume_extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        website_links=[
            "https://portfolio.example.com",
            "https://blog.example.com/",
            "https://PORTFOLIO.EXAMPLE.COM",
        ],
        address_country=None,
        confidence=0.9,
        source="gpt-4o-mini",
        skills=[],
        skill_attrs={},
    )
    response = Mock()
    response.content = b"resume-bytes"
    response.raise_for_status = Mock()

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
    ):
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://example.com/resume.pdf",
            }
        )

    assert updates["cWebsiteLink"] == [
        "https://portfolio.example.com",
        "https://blog.example.com",
    ]


def test_build_resume_updates_uses_extracted_profile_fields_for_form_fields() -> None:
    processor = IntakeFormProcessor()
    processor.document_processor = Mock()
    processor.resume_extractor = Mock()
    processor.document_processor.extract_text.return_value = "resume text"
    processor.resume_extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        additional_emails=["alt@example.com"],
        availability="10-15 hours/week",
        rate_range="$80 - $120",
        referred_by="Referral Source",
        address_country=None,
        confidence=0.95,
        source="gpt-4o-mini",
        skills=[],
        skill_attrs={},
    )
    response = Mock()
    response.content = b"resume-bytes"
    response.raise_for_status = Mock()

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
    ):
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://example.com/resume.pdf",
            }
        )

    assert updates["cAvailableTimes"] == "10-15 hours/week"
    assert updates["cRateRange"] == "$80 - $120"
    assert updates["cReferredBy"] == "Referral Source"


def test_build_resume_updates_rejects_non_https_resume_url() -> None:
    processor = IntakeFormProcessor()

    with patch("five08.worker.crm.intake_form_processor.requests.get") as mock_get:
        updates = processor._build_resume_updates(
            {
                "resume_url": "http://example.com/resume.pdf",
            }
        )

    assert updates == {}
    mock_get.assert_not_called()


def test_build_resume_updates_rejects_private_ip_resume_url() -> None:
    processor = IntakeFormProcessor()

    with patch("five08.worker.crm.intake_form_processor.requests.get") as mock_get:
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://127.0.0.1/resume.pdf",
            }
        )

    assert updates == {}
    mock_get.assert_not_called()


def test_build_resume_updates_rejects_resume_host_outside_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = IntakeFormProcessor()
    monkeypatch.setattr(
        "five08.worker.crm.intake_form_processor.settings.intake_resume_allowed_hosts",
        "allowed.example",
    )

    with patch("five08.worker.crm.intake_form_processor.requests.get") as mock_get:
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://blocked.example/resume.pdf",
            }
        )

    assert updates == {}
    mock_get.assert_not_called()
