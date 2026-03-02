"""Unit tests for Google Forms intake processor."""

from unittest.mock import MagicMock

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
