"""Unit tests for Google Forms intake processor."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from five08.resume_extractor import ResumeExtractedProfile
from five08.worker.crm import intake_form_processor as intake_module
from five08.worker.crm.intake_form_processor import (
    IntakeFormProcessor,
    IntakeResumeFile,
)


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


def test_intake_form_processor_lowercases_email_for_lookup_and_persistence() -> None:
    """Intake processing should use lowercase email for CRM lookup and DB joins."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {"list": []},
        {"id": "contact-1"},
    ]

    with patch.object(processor, "_persist_intake_submission") as mock_persist:
        result = processor.process_intake(
            payload={
                "email": " New@Example.COM ",
                "first_name": "New",
                "last_name": "Person",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    lookup_params = processor.api.request.call_args_list[0].args[2]
    create_payload = processor.api.request.call_args_list[1].args[2]
    assert lookup_params["where[0][value]"] == "new@example.com"
    assert create_payload["emailAddress"] == "new@example.com"
    mock_persist.assert_called_once()
    assert mock_persist.call_args.kwargs["email"] == "new@example.com"


def test_intake_form_processor_dry_run_create_does_not_write_crm_or_db() -> None:
    """Dry-run create should search CRM and return planned fields without writes."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {"list": []}

    with patch.object(processor, "_persist_intake_submission") as mock_persist:
        result = processor.process_intake(
            payload={
                "dry_run": True,
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "github_username": "https://github.com/newdev",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["action"] == "create_prospect"
    assert result["created"] is True
    assert result["contact_id"] is None
    assert result["planned_updates"]["emailAddress"] == "new@example.com"
    assert result["planned_updates"]["cGitHubUsername"] == "newdev"
    assert processor.api.request.call_count == 1
    mock_persist.assert_not_called()


def test_intake_form_processor_dry_run_create_reports_resume_upload_plan() -> None:
    """Dry-run create should report that a scanned resume would be uploaded."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {"list": []}
    resume_file = IntakeResumeFile(
        filename="resume.pdf",
        content=b"resume-bytes",
        source_url="https://tally.so/resume.pdf",
    )

    with (
        patch.object(processor, "_prepare_resume_file", return_value=resume_file),
        patch.object(processor, "_build_resume_updates", return_value={}),
        patch.object(processor, "_persist_intake_submission") as mock_persist,
    ):
        result = processor.process_intake(
            payload={
                "dry_run": True,
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "resume_url": "https://tally.so/resume.pdf",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["would_upload_resume"] is True
    assert result["resume_file_name"] == "resume.pdf"
    assert result["resume_file_size_bytes"] == len(b"resume-bytes")
    assert processor.api.request.call_count == 1
    processor.api.upload_file.assert_not_called()
    mock_persist.assert_not_called()


def test_create_prospect_does_not_retry_failed_resume_prepare() -> None:
    """Create flow should not download/scan a failed resume more than once."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {"list": []},
        {"id": "contact-1"},
    ]

    with (
        patch.object(processor, "_prepare_resume_file", return_value=None) as prepare,
        patch.object(processor, "_persist_intake_submission"),
    ):
        result = processor.process_intake(
            payload={
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "resume_url": "https://tally.so/resume.pdf",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    prepare.assert_called_once()


def test_intake_form_processor_dry_run_update_does_not_write_crm_or_db() -> None:
    """Dry-run update should return planned updates without PUT or persistence."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {
        "list": [
            {
                "id": "contact-1",
                "type": "Prospect",
                "firstName": "Existing",
                "lastName": "Person",
            }
        ]
    }

    with patch.object(processor, "_persist_intake_submission") as mock_persist:
        result = processor.process_intake(
            payload={
                "dry_run": "true",
                "email": "existing@example.com",
                "first_name": "Existing",
                "last_name": "Person",
                "github_username": "existingdev",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["action"] == "update_prospect"
    assert result["created"] is False
    assert result["contact_id"] == "contact-1"
    assert result["planned_updates"]["cGitHubUsername"] == "existingdev"
    assert processor.api.request.call_count == 1
    mock_persist.assert_not_called()


def test_update_prospect_does_not_retry_failed_resume_prepare() -> None:
    """Update flow should not download/scan a failed resume more than once."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {"list": [{"id": "contact-1", "type": "Prospect"}]},
        {},
    ]

    with (
        patch.object(processor, "_prepare_resume_file", return_value=None) as prepare,
        patch.object(processor, "_persist_intake_submission"),
    ):
        result = processor.process_intake(
            payload={
                "email": "existing@example.com",
                "first_name": "Existing",
                "last_name": "Person",
                "github_username": "existing-dev",
                "resume_url": "https://tally.so/resume.pdf",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    prepare.assert_called_once()


def test_intake_form_processor_uploads_resume_after_create() -> None:
    """Created prospects should receive the downloaded Tally resume attachment."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {"list": []},
        {"id": "contact-1"},
        {"resumeIds": ["existing-att"]},
        {},
    ]
    processor.api.upload_file.return_value = {"id": "resume-att-1"}
    resume_file = IntakeResumeFile(
        filename="resume.pdf",
        content=b"resume-bytes",
        source_url="https://tally.so/resume.pdf",
    )

    with (
        patch.object(processor, "_prepare_resume_file", return_value=resume_file),
        patch.object(processor, "_build_resume_updates", return_value={}),
        patch.object(processor, "_persist_intake_submission") as mock_persist,
    ):
        result = processor.process_intake(
            payload={
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "resume_url": "https://tally.so/resume.pdf",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    assert result["resume_uploaded"] is True
    assert result["resume_attachment_id"] == "resume-att-1"
    processor.api.upload_file.assert_called_once_with(
        file_content=b"resume-bytes",
        filename="resume.pdf",
        related_type="Contact",
        related_id="contact-1",
        field="resume",
    )
    assert processor.api.request.call_args_list[2].args == ("GET", "Contact/contact-1")
    assert processor.api.request.call_args_list[3].args == (
        "PUT",
        "Contact/contact-1",
        {"resumeIds": ["existing-att", "resume-att-1"]},
    )
    mock_persist.assert_called_once()


def test_intake_form_processor_rejects_tally_when_allowed_forms_unset() -> None:
    """Worker-side Tally intake should fail closed if jobs bypass the webhook."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()

    with patch.object(intake_module.settings, "onboarding_tally_allowed_form_ids", ""):
        result = processor.process_intake(
            payload={
                "source": "tally",
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "form_id": "unexpected-form",
            }
        )

    assert result == {"success": False, "error": "invalid_form_id"}
    processor.api.request.assert_not_called()


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


def test_intake_form_processor_does_not_overwrite_last_name_with_placeholder() -> None:
    """Generated placeholder last names should not be written to matched CRM contacts."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.side_effect = [
        {
            "list": [
                {
                    "id": "contact-1",
                    "type": "Prospect",
                    "firstName": "Existing",
                    "lastName": "Real",
                }
            ]
        },
        {},
    ]

    with patch.object(processor, "_persist_intake_submission") as mock_persist:
        result = processor.process_intake(
            payload={
                "email": "existing@example.com",
                "first_name": "Prince",
                "last_name": "Unknown",
                "last_name_is_placeholder": True,
                "github_username": "prince",
                "form_id": "form-1",
            }
        )

    assert result["success"] is True
    update_payload = processor.api.request.call_args_list[1].args[2]
    assert update_payload["firstName"] == "Prince"
    assert update_payload["cGitHubUsername"] == "prince"
    assert "lastName" not in update_payload
    mock_persist.assert_called_once()


def test_intake_form_processor_persists_placeholder_name_without_crm_create() -> None:
    """New single-token applicants should be queued for review instead of fake CRM create."""
    processor = IntakeFormProcessor()
    processor.api = MagicMock()
    processor.api.request.return_value = {"list": []}

    with patch.object(processor, "_persist_intake_submission") as mock_persist:
        result = processor.process_intake(
            payload={
                "email": "new@example.com",
                "first_name": "Prince",
                "last_name": "Unknown",
                "last_name_is_placeholder": True,
                "form_id": "form-1",
            }
        )

    assert result == {
        "success": True,
        "created": False,
        "contact_id": None,
        "updated_fields": [],
        "pending_review": True,
        "reason": "placeholder_last_name",
    }
    assert processor.api.request.call_count == 1
    mock_persist.assert_called_once_with(
        payload={
            "email": "new@example.com",
            "first_name": "Prince",
            "last_name": "Unknown",
            "last_name_is_placeholder": True,
            "form_id": "form-1",
        },
        contact_id=None,
        email="new@example.com",
    )


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
        "data scientist",
        "biz dev",
        "staff engineering",
    ]


def test_persist_intake_submission_uses_raw_payload_and_nullable_upsert() -> None:
    """Raw submission bodies should be persisted with the nullable-safe upsert target."""
    processor = IntakeFormProcessor()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor
    raw_payload = {"eventId": "evt-1", "data": {"submissionId": "sub-1"}}

    with patch(
        "five08.worker.crm.intake_form_processor.get_postgres_connection",
        return_value=conn,
    ):
        processor._persist_intake_submission(
            payload={
                "source": "tally",
                "email": "new@example.com",
                "form_id": None,
                "submission_id": None,
                "raw_payload": raw_payload,
                "raw_tally_fields": [{"label": "Email", "value": "new@example.com"}],
            },
            contact_id="contact-1",
            email="new@example.com",
        )

    sql, params = cursor.execute.call_args.args
    first_generated_submission_id = params[3]
    assert "(COALESCE(form_id, ''))" in sql
    assert "(COALESCE(submission_id, ''))" in sql
    assert first_generated_submission_id.startswith("generated:")
    assert params[7].obj["email"] == "new@example.com"
    assert params[7].obj["submission_id"] == first_generated_submission_id
    assert "raw_payload" not in params[7].obj
    assert "raw_tally_fields" not in params[7].obj
    assert params[8].obj == raw_payload

    with patch(
        "five08.worker.crm.intake_form_processor.get_postgres_connection",
        return_value=conn,
    ):
        processor._persist_intake_submission(
            payload={
                "source": "tally",
                "email": "new@example.com",
                "form_id": None,
                "submission_id": None,
                "raw_payload": raw_payload,
                "raw_tally_fields": [{"label": "Email", "value": "new@example.com"}],
            },
            contact_id="contact-1",
            email="new@example.com",
        )
    _, retry_params = cursor.execute.call_args.args
    assert retry_params[3] == first_generated_submission_id


def test_persist_intake_submission_raises_for_orphan_persistence_failure() -> None:
    """Orphan intakes have no CRM mutation, so DB persistence failures must retry."""
    processor = IntakeFormProcessor()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.execute.side_effect = RuntimeError("db unavailable")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value = cursor

    with (
        patch(
            "five08.worker.crm.intake_form_processor.get_postgres_connection",
            return_value=conn,
        ),
        pytest.raises(RuntimeError, match="db unavailable"),
    ):
        processor._persist_intake_submission(
            payload={
                "source": "tally",
                "email": "new@example.com",
                "form_id": "form-1",
                "submission_id": "sub-1",
            },
            contact_id=None,
            email="new@example.com",
        )


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
    response.status_code = 200
    response.content = b"resume-bytes"
    response.raise_for_status = Mock()
    response.headers = {}
    response.iter_content = Mock(return_value=[b"resume-bytes"])
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        patch.object(processor, "_scan_resume_content", return_value=True),
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
        address_state="California",
        address_country=None,
        confidence=0.95,
        source="gpt-4o-mini",
        skills=[],
        skill_attrs={},
    )
    response = Mock()
    response.status_code = 200
    response.content = b"resume-bytes"
    response.raise_for_status = Mock()
    response.headers = {}
    response.iter_content = Mock(return_value=[b"resume-bytes"])
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        patch.object(processor, "_scan_resume_content", return_value=True),
    ):
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://example.com/resume.pdf",
            }
        )

    assert updates["cAvailableTimes"] == "10-15 hours/week"
    assert updates["cRateRange"] == "$80 - $120"
    assert updates["cReferredBy"] == "Referral Source"
    assert updates["addressState"] == "California"


def test_build_intake_updates_includes_form_website_and_weekly_hours() -> None:
    processor = IntakeFormProcessor()

    updates = processor._build_intake_updates(
        email="new@example.com",
        first_name="New",
        last_name="Person",
        payload={
            "website_link": "portfolio.example.com",
            "ideal_weekly_hours": "8-10",
        },
        include_email=True,
    )

    assert updates["cWebsiteLink"] == ["https://portfolio.example.com"]
    assert "Ideal weekly hours: 8-10" in updates["description"]


def test_build_resume_updates_skips_parsing_when_scan_fails() -> None:
    processor = IntakeFormProcessor()
    processor.document_processor = Mock()
    response = Mock()
    response.status_code = 200
    response.raise_for_status = Mock()
    response.headers = {}
    response.iter_content = Mock(return_value=[b"resume-bytes"])
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        patch.object(processor, "_scan_resume_content", return_value=False),
    ):
        updates = processor._build_resume_updates(
            {
                "resume_url": "https://example.com/resume.pdf",
            }
        )

    assert updates == {}
    processor.document_processor.extract_text.assert_not_called()


def test_resume_url_log_mask_strips_signed_query_parameters() -> None:
    processor = IntakeFormProcessor()

    masked = processor._mask_resume_url_for_log(
        "https://storage.googleapis.com/tally/resume.pdf?signature=secret&token=hidden"
    )

    assert masked == "https://storage.googleapis.com/tally/resume.pdf"


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


def test_download_resume_content_follows_redirects_within_limit() -> None:
    """Resume download should follow redirects up to the configured limit."""
    processor = IntakeFormProcessor()

    # Mock responses: first redirect, second redirect, then final content
    redirect_1 = Mock()
    redirect_1.status_code = 302
    redirect_1.headers = {"Location": "https://cdn.example.com/resume-v2.pdf"}
    redirect_1.__enter__ = Mock(return_value=redirect_1)
    redirect_1.__exit__ = Mock(return_value=None)

    redirect_2 = Mock()
    redirect_2.status_code = 302
    redirect_2.headers = {"Location": "https://cdn.example.com/resume-final.pdf"}
    redirect_2.__enter__ = Mock(return_value=redirect_2)
    redirect_2.__exit__ = Mock(return_value=None)

    final = Mock()
    final.status_code = 200
    final.raise_for_status = Mock()
    final.iter_content = Mock(return_value=[b"resume", b"data"])
    final.__enter__ = Mock(return_value=final)
    final.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            side_effect=[redirect_1, redirect_2, final],
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
    ):
        content = processor._download_resume_content("https://example.com/resume.pdf")

    assert content == b"resumedata"


def test_download_resume_content_exceeds_max_redirects() -> None:
    """Resume download should fail when exceeding max redirect limit."""
    processor = IntakeFormProcessor()

    # Create redirect responses
    redirect = Mock()
    redirect.status_code = 302
    redirect.headers = {"Location": "https://example.com/redirect-1"}
    redirect.__enter__ = Mock(return_value=redirect)
    redirect.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=redirect,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        pytest.raises(ValueError, match="exceeded max redirect limit"),
    ):
        processor._download_resume_content("https://example.com/resume.pdf")


def test_download_resume_content_enforces_file_size_limit() -> None:
    """Resume download should stop when file exceeds size limit."""
    processor = IntakeFormProcessor()

    # Mock a response that exceeds the size limit
    response = Mock()
    response.status_code = 200
    response.raise_for_status = Mock()
    response.headers = {}
    response.iter_content = Mock(
        return_value=[b"x" * 5 * 1024 * 1024, b"y" * 6 * 1024 * 1024]  # 5MB + 6MB
    )
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        pytest.raises(ValueError, match="exceeds maximum allowed size"),
    ):
        processor._download_resume_content("https://example.com/resume.pdf")


def test_download_resume_content_checks_content_length_header() -> None:
    """Resume download should fail early if Content-Length exceeds limit."""
    processor = IntakeFormProcessor()

    response = Mock()
    response.status_code = 200
    response.headers = {"Content-Length": str(20 * 1024 * 1024)}  # 20MB
    response.raise_for_status = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)

    with (
        patch(
            "five08.worker.crm.intake_form_processor.requests.get",
            return_value=response,
        ),
        patch.object(processor, "_hostname_resolves_publicly", return_value=True),
        pytest.raises(ValueError, match="exceeds maximum allowed size"),
    ):
        processor._download_resume_content("https://example.com/resume.pdf")
