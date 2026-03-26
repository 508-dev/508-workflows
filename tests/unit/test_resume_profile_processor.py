"""Unit tests for resume profile worker processor."""

import ipaddress
import json
from datetime import datetime
from types import SimpleNamespace

from unittest.mock import MagicMock, Mock, patch

from curl_cffi import CurlOpt

from five08.clients.espo import EspoAPIError
from five08.resume_profile_processor import ResumeProcessorConfig
from five08.worker.crm.resume_profile_processor import ResumeProfileProcessor
from five08.worker.models import ExtractedSkills, ResumeExtractedProfile


def test_resume_processor_config_filters_unsupported_extensions() -> None:
    """Shared config should clamp settings to the supported resume formats."""
    config = ResumeProcessorConfig.from_settings(
        SimpleNamespace(
            espo_base_url="https://crm.example.com",
            espo_api_key="secret",
            allowed_file_types="pdf,docx,txt",
            max_file_size_mb=12,
        )
    )

    assert config.allowed_file_extensions == {"pdf", "docx"}
    assert config.allowed_attachment_suffixes == frozenset({".pdf", ".docx"})
    assert config.allowed_file_extensions_label == "PDF or DOCX"
    assert config.max_file_size_bytes == 12 * 1024 * 1024


def test_extract_profile_proposal_filters_508_email() -> None:
    """Extract proposal should skip @508.dev email updates by policy."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cGitHubUsername": "old-gh",
        "cLinkedIn": "https://linkedin.com/in/old",
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
    assert result.proposed_updates["cLinkedIn"] == "https://linkedin.com/in/new"
    assert result.proposed_updates["phoneNumber"] == "14155551234"
    assert result.proposed_updates["skills"] == ["python", "fastapi"]
    assert result.new_skills == ["python", "fastapi"]
    assert isinstance(result.proposed_updates["cSkillAttrs"], str)
    assert json.loads(result.proposed_updates["cSkillAttrs"])["python"]["strength"] == 5
    assert (
        json.loads(result.proposed_updates["cSkillAttrs"])["fastapi"]["strength"] == 4
    )
    assert any(item.field == "emailAddress" for item in result.skipped)
    processor.crm.update_contact.assert_not_called()
    processor._record_processing_run.assert_called_once()
    record_kwargs = processor._record_processing_run.call_args.kwargs
    assert record_kwargs["status"] == "succeeded"
    assert record_kwargs["contact_id"] == "contact-1"
    assert record_kwargs["attachment_id"] == "att-1"


def test_extract_profile_proposal_includes_additional_emails() -> None:
    """Additional extracted emails should be shown in updates and proposed changes."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {"emailAddress": "member@example.com"}
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-additional"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email="lead@example.com",
        additional_emails=["lead2@example.com", "lead3@example.com"],
        github_username=None,
        linkedin_url=None,
        phone=None,
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
        contact_id="contact-extra",
        attachment_id="att-extra",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["additional_emails"] == [
        "lead2@example.com",
        "lead3@example.com",
    ]
    assert any(
        getattr(change, "field", None) == "additional_emails"
        for change in result.proposed_changes
    )


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
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
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


def test_extract_profile_proposal_merges_and_serializes_social_links() -> None:
    """Social links should be merged and persisted to cSocialLinks without duplication."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cWebsiteLink": ["https://portfolio.example.com"],
        "cSocialLinks": ["https://x.com/old"],
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-social"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email="new@example.com",
        github_username=None,
        linkedin_url=None,
        phone=None,
        website_links=["https://portfolio.example.com", "https://blog.example.com"],
        social_links=["https://x.com/new", "https://instagram.com/example"],
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
        contact_id="contact-social",
        attachment_id="att-social",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["cSocialLinks"] == [
        "https://x.com/old",
        "https://x.com/new",
        "https://instagram.com/example",
    ]


def test_extract_profile_proposal_fetches_crm_website_and_github_sources() -> None:
    """Existing CRM website and GitHub profile text should be passed into extraction."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor._fetch_external_profile_source_text = Mock(
        side_effect=lambda url: {
            "https://portfolio.example.com": "Portfolio content",
            "https://github.com/octocat": "GitHub profile content",
        }[url]
    )
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cWebsiteLink": ["https://portfolio.example.com"],
        "cGitHubUsername": "octocat",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-external-crm"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username="octocat",
        linkedin_url=None,
        phone=None,
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
        contact_id="contact-external-crm",
        attachment_id="att-external-crm",
        filename="resume.pdf",
    )

    assert result.success is True
    assert processor.extractor.extract.call_count == 1
    extract_call = processor.extractor.extract.call_args
    assert extract_call.args == ("resume text",)
    assert extract_call.kwargs["extra_sources"] == {
        "personal_website": (
            "Source: Personal Website\n"
            "URL: https://portfolio.example.com\n"
            "Content:\n"
            "Portfolio content"
        ),
        "github_profile": (
            "Source: GitHub Profile\n"
            "URL: https://github.com/octocat\n"
            "Content:\n"
            "GitHub profile content"
        ),
    }
    assert [item.status for item in result.source_enrichments] == ["used", "used"]
    assert [item.origin for item in result.source_enrichments] == ["crm", "crm"]


def test_extract_profile_proposal_reruns_with_inferred_github_only() -> None:
    """Inferred GitHub should rerun automatically while websites wait for confirmation."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor._fetch_external_profile_source_text = Mock(
        side_effect=lambda url: {
            "https://blog.example.com": "Blog content",
            "https://github.com/octocat": "GitHub profile content",
        }[url]
    )
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-external-rerun"
    processor.extractor.extract.side_effect = [
        ResumeExtractedProfile(
            email=None,
            github_username="octocat",
            linkedin_url=None,
            phone=None,
            website_links=["https://blog.example.com"],
            confidence=0.7,
            source="gpt-4o-mini",
        ),
        ResumeExtractedProfile(
            email=None,
            github_username="octocat",
            linkedin_url=None,
            phone=None,
            website_links=["https://blog.example.com"],
            description="Enriched profile",
            confidence=0.9,
            source="gpt-4o-mini",
        ),
    ]
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=[],
        skill_attrs={},
        confidence=0.8,
        source="gpt-4o-mini",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-external-rerun",
        attachment_id="att-external-rerun",
        filename="resume.pdf",
    )

    assert result.success is True
    assert processor.extractor.extract.call_count == 2
    first_call = processor.extractor.extract.call_args_list[0]
    second_call = processor.extractor.extract.call_args_list[1]
    assert first_call.args == ("resume text",)
    assert first_call.kwargs["extra_sources"] is None
    assert second_call.args == ("resume text",)
    assert second_call.kwargs["extra_sources"] == {
        "github_profile": (
            "Source: GitHub Profile\n"
            "URL: https://github.com/octocat\n"
            "Content:\n"
            "GitHub profile content"
        ),
    }
    assert result.extracted_profile.description == "Enriched profile"
    assert [item.status for item in result.source_enrichments] == [
        "confirmation_needed",
        "used",
    ]
    assert [item.origin for item in result.source_enrichments] == [
        "resume_inference",
        "resume_inference",
    ]


def test_extract_profile_proposal_reruns_with_confirmed_personal_website() -> None:
    """Confirmed inferred personal websites should be fetched on a rerun."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor._fetch_external_profile_source_text = Mock(return_value="Blog content")
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = (
        "hash-external-confirmed"
    )
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        website_links=["https://blog.example.com"],
        description="Confirmed website enrichment",
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
        contact_id="contact-external-confirmed",
        attachment_id="att-external-confirmed",
        filename="resume.pdf",
        confirmed_personal_websites=["https://blog.example.com"],
    )

    assert result.success is True
    assert processor.extractor.extract.call_count == 1
    first_call = processor.extractor.extract.call_args_list[0]
    assert first_call.kwargs["extra_sources"] == {
        "personal_website": (
            "Source: Personal Website\n"
            "URL: https://blog.example.com\n"
            "Content:\n"
            "Blog content"
        )
    }
    assert result.extracted_profile.description == "Confirmed website enrichment"
    assert [item.status for item in result.source_enrichments] == ["used"]
    assert [item.origin for item in result.source_enrichments] == [
        "resume_confirmation"
    ]


def test_extract_text_from_html_reads_meta_description_regardless_of_order() -> None:
    """Meta description extraction should not depend on HTML attribute order."""
    processor = ResumeProfileProcessor()

    rendered = processor._extract_text_from_html(
        """
        <html>
          <head>
            <title>Portfolio</title>
            <meta content="Builder of useful things" name="description">
          </head>
          <body><main>Body copy</main></body>
        </html>
        """
    )

    assert "Title: Portfolio" in rendered
    assert "Description: Builder of useful things" in rendered
    assert "Body copy" in rendered


def test_fetch_external_profile_source_text_pins_resolved_public_ips() -> None:
    """External website fetches should connect only to the validated public IPs."""
    processor = ResumeProfileProcessor()
    resolved_ip = ipaddress.ip_address("93.184.216.34")
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.headers = {"Content-Type": "text/plain"}
    response.iter_content.return_value = [b"Profile body"]
    response.raise_for_status = Mock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = response

    with (
        patch.object(
            processor,
            "_resolve_public_profile_request_target",
            return_value=("example.com", 443, [resolved_ip], False),
        ),
        patch(
            "five08.resume_profile_processor.curl_requests.Session",
            return_value=session,
        ) as session_cls,
    ):
        text = processor._fetch_external_profile_source_text("https://example.com")

    assert text == "Profile body"
    session_cls.assert_called_once()
    assert session_cls.call_args.kwargs["curl_options"] == {
        CurlOpt.RESOLVE: ["example.com:443:93.184.216.34"]
    }
    session.get.assert_called_once()


def test_extract_profile_proposal_deduplicates_existing_and_extracted_websites_by_scheme() -> (
    None
):
    """Existing and extracted links should merge by website identity, not scheme."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cWebsiteLink": ["http://bit.ly/charleschen-portfolio"],
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-scheme"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        website_links=["https://bit.ly/charleschen-portfolio"],
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
        contact_id="contact-scheme",
        attachment_id="att-scheme",
        filename="resume.pdf",
    )

    assert result.success is True
    assert "cWebsiteLink" not in result.proposed_updates


def test_extract_profile_proposal_deduplicates_skills_in_confirmation() -> None:
    """Duplicate extracted or existing skills should not appear in confirmation updates."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
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
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {"emailAddress": "member@example.com"}
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


def test_extract_profile_proposal_preserves_existing_seniority() -> None:
    """Existing non-unknown seniority should not be overwritten."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "cSeniority": "Senior",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-6"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        seniority_level="Junior",
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
        contact_id="contact-7",
        attachment_id="att-7",
        filename="resume.pdf",
    )

    assert result.success is True
    assert "cSeniority" not in result.proposed_updates
    assert any(
        item.field == "cSeniority" and item.value == "junior" for item in result.skipped
    )


def test_extract_profile_proposal_maps_principal_to_staff() -> None:
    """Principal titles should normalize to staff seniority."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {"emailAddress": "member@example.com"}
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-9"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        seniority_level="Principal Engineer",
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
        contact_id="contact-9",
        attachment_id="att-9",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["cSeniority"] == "staff"


def test_extract_profile_proposal_normalizes_unknown_seniority_to_unknown() -> None:
    """Unknown extracted seniority values should normalize to unknown."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
    )

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-5"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        seniority_level="guru",
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
        contact_id="contact-8",
        attachment_id="att-8",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["cSeniority"] == "unknown"


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


def test_apply_profile_updates_appends_additional_emails_without_replacing_primary() -> (
    None
):
    """Additional emails should be merged into emailAddressData and stay non-primary."""
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
        contact_id="contact-4",
        updates={
            "emailAddress": "NewPerson@Example.Com",
            "additional_emails": [
                "Extra@Example.Com",
                "legacy@example.com",
                "SECONDARY@Example.Com",
            ],
        },
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    payload = processor.crm.update_contact.call_args[0][1]
    email_data = payload["emailAddressData"]
    by_lower = {item["lower"]: item for item in email_data}
    assert by_lower["legacy@example.com"]["primary"] is False
    assert by_lower["newperson@example.com"]["primary"] is True
    assert by_lower["extra@example.com"]["primary"] is False
    assert by_lower["secondary@example.com"]["primary"] is False


def test_apply_profile_updates_preserves_primary_when_only_additional_emails() -> None:
    """Existing primary email should remain when only additional emails are merged."""
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
            },
            {
                "emailAddress": "alias@example.com",
                "lower": "alias@example.com",
                "primary": False,
                "optOut": False,
                "invalid": False,
            },
        ]
    }
    result = processor.apply_profile_updates(
        contact_id="contact-5",
        updates={
            "additional_emails": [
                "Alias@Example.Com",
                "extra@Example.com",
            ],
        },
    )

    assert result.success is True
    processor.crm.update_contact.assert_called_once()
    payload = processor.crm.update_contact.call_args[0][1]
    email_data = payload["emailAddressData"]
    by_lower = {item["lower"]: item for item in email_data}
    assert by_lower["legacy@example.com"]["primary"] is True
    assert by_lower["alias@example.com"]["primary"] is False
    assert by_lower["extra@example.com"]["primary"] is False


def test_extract_profile_proposal_normalizes_existing_skill_punctuation() -> None:
    """Existing punctuation-heavy skills should normalize to search-friendly canonical forms."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
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
    processor.skills_extractor.canonicalize_skill.side_effect = lambda v: (
        str(v).strip().lower()
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


def test_extract_profile_proposal_fills_missing_strengths_for_merged_skills() -> None:
    """Merged cSkillAttrs should include defaults for skills missing explicit strengths."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.extractor = Mock()
    processor.skills_extractor = Mock()
    processor.document_processor = Mock()
    processor._record_processing_run = Mock()

    processor.crm.get_contact.return_value = {
        "emailAddress": "member@example.com",
        "skills": ["qt", "redis"],
        "cSkillAttrs": '{"qt":{"strength":3},"redis":{"strength":3}}',
    }
    processor.crm.download_attachment.return_value = b"resume-bytes"
    processor.document_processor.extract_text.return_value = "resume text"
    processor.document_processor.get_content_hash.return_value = "hash-21"
    processor.extractor.extract.return_value = ResumeExtractedProfile(
        email=None,
        github_username=None,
        linkedin_url=None,
        phone=None,
        skills=["python", "javascript", "qt", "redis"],
        skill_attrs={"python": 5},
        confidence=0.9,
        source="gpt-4o-mini",
    )
    processor.skills_extractor.extract_skills.return_value = ExtractedSkills(
        skills=[],
        skill_attrs={},
        confidence=0.8,
        source="heuristic",
    )

    result = processor.extract_profile_proposal(
        contact_id="contact-21",
        attachment_id="att-21",
        filename="resume.pdf",
    )

    assert result.success is True
    assert result.proposed_updates["skills"] == ["qt", "redis", "python", "javascript"]
    attrs = json.loads(result.proposed_updates["cSkillAttrs"])
    assert attrs["qt"]["strength"] == 3
    assert attrs["redis"]["strength"] == 3
    assert attrs["python"]["strength"] == 5
    assert attrs["javascript"]["strength"] == 3


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
    assert result.updated_values["cGitHubUsername"] == "new-gh"
    assert result.updated_values["skills"] == ["python", "fastapi"]
    assert result.updated_values["cDiscordUserID"] == "123"
    processor.crm.update_contact.assert_called_once()
    update_payload = processor.crm.update_contact.call_args[0][1]
    assert "emailAddress" not in update_payload
    assert update_payload["cGitHubUsername"] == "new-gh"
    assert update_payload["phoneNumber"] == "14155551234"
    assert update_payload["skills"] == ["python", "fastapi"]
    assert update_payload["cDiscordUserID"] == "123"
    assert update_payload["cDiscordUsername"] == "member#0001"
    assert "cResumeLastProcessed" in update_payload
    assert isinstance(update_payload["cResumeLastProcessed"], str)
    assert (
        datetime.strptime(update_payload["cResumeLastProcessed"], "%Y-%m-%d %H:%M:%S")
        is not None
    )


def test_apply_profile_updates_normalizes_csv_skills_to_array() -> None:
    """Apply should convert comma-separated skills into a deduplicated array."""
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
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["skills"] == ["python"]
    assert json.loads(payload["cSkillAttrs"]) == {
        "python": {"strength": 5},
        "react": {"strength": 3},
    }


def test_apply_profile_updates_accepts_double_encoded_skill_attrs() -> None:
    """Apply should parse double-encoded cSkillAttrs payloads instead of dropping them."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    raw_attrs = {"python": {"strength": 5}, "react": {"strength": 4}}
    result = processor.apply_profile_updates(
        contact_id="contact-21",
        updates={
            "skills": ["Python", "React"],
            "cSkillAttrs": json.dumps(json.dumps(raw_attrs)),
        },
    )

    assert result.success is True
    payload = processor.crm.update_contact.call_args[0][1]
    assert json.loads(payload["cSkillAttrs"]) == raw_attrs


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


def test_apply_profile_updates_normalizes_unknown_seniority_to_unknown() -> None:
    """Unknown seniority values should be normalized to the canonical unknown bucket."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-7",
        updates={"cSeniority": "distinguished"},
    )

    assert result.success is True
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["cSeniority"] == "unknown"


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


def test_apply_profile_updates_accepts_link_only_updates() -> None:
    """Link-only submissions should still persist Discord linkage."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()

    result = processor.apply_profile_updates(
        contact_id="contact-link-only",
        updates={},
        link_discord={"user_id": "123", "username": "member#0001"},
    )

    assert result.success is True
    assert result.link_discord_applied is True
    assert result.updated_fields == ["cDiscordUserID", "cDiscordUsername"]
    payload = processor.crm.update_contact.call_args[0][1]
    assert payload["cDiscordUserID"] == "123"
    assert payload["cDiscordUsername"] == "member#0001"


def test_apply_profile_updates_returns_warning_for_partial_success() -> None:
    """Fallback field updates should surface partial failures without dropping successes."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor._verify_updated_fields = Mock(return_value=["cGitHubUsername"])
    processor.crm.update_contact.side_effect = [
        EspoAPIError("batch failed"),
        None,
        EspoAPIError("phone rejected"),
        None,
    ]

    result = processor.apply_profile_updates(
        contact_id="contact-partial",
        updates={
            "cGitHubUsername": "new-gh",
            "phoneNumber": "14155551234",
        },
    )

    assert result.success is True
    assert result.updated_fields == ["cGitHubUsername"]
    assert result.updated_values == {"cGitHubUsername": "new-gh"}
    assert result.warning == "phoneNumber: phone rejected"
    assert result.error is None
    timestamp_call = processor.crm.update_contact.call_args_list[-1]
    assert timestamp_call.args[0] == "contact-partial"
    assert set(timestamp_call.args[1].keys()) == {"cResumeLastProcessed"}
    assert (
        datetime.strptime(
            timestamp_call.args[1]["cResumeLastProcessed"], "%Y-%m-%d %H:%M:%S"
        )
        is not None
    )


def test_apply_profile_updates_does_not_report_failed_fields_as_updated() -> None:
    """Failed writes should not be echoed back as updated fields or values."""
    processor = ResumeProfileProcessor()
    processor.crm = Mock()
    processor.crm.update_contact.side_effect = [
        EspoAPIError("batch failed"),
        EspoAPIError("github rejected"),
        EspoAPIError("phone rejected"),
    ]

    result = processor.apply_profile_updates(
        contact_id="contact-failed",
        updates={
            "cGitHubUsername": "new-gh",
            "phoneNumber": "14155551234",
        },
    )

    assert result.success is False
    assert result.updated_fields == []
    assert result.updated_values == {}
    assert (
        result.error == "cGitHubUsername: github rejected; phoneNumber: phone rejected"
    )


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
