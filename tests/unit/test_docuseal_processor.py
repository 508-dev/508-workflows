"""Unit tests for Docuseal processor logic."""

import hashlib
from unittest.mock import Mock, patch

from five08.clients.espo import EspoAPIError
from five08.worker.crm.docuseal_processor import DocusealAgreementProcessor


def _expected_masked_email(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]


def test_docuseal_processor_marks_member_agreement_and_flag() -> None:
    """Processor should update agreement date and bool fields on matching contact."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        {"updated": True},
    ]
    expected_email = "member@508.dev"
    expected_masked = _expected_masked_email(expected_email)

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email=expected_email,
            completed_at="2026-02-25T12:00:00Z",
            submission_id=416,
        )

    assert mock_api.request.call_count == 2
    assert mock_api.request.call_args_list[1].args[1] == "Contact/contact-1"
    assert mock_api.request.call_args_list[1].args[2] == {
        "cMemberAgreementSignedAt": "2026-02-25T12:00:00Z",
        "cSignedMemberAgreement": True,
    }
    assert result["success"] is True
    assert result["masked_email"] == expected_masked
    assert result["contact_id"] == "contact-1"
    assert result["submission_id"] == 416
    assert "email" not in result


def test_docuseal_processor_returns_contact_not_found_when_missing_contact() -> None:
    """Processor should return a contact-not-found error without raw email."""
    mock_api = Mock()
    mock_api.request.return_value = {"list": []}

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="missing@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=123,
        )

    assert result["success"] is False
    assert result["error"] == "contact_not_found"
    assert result["masked_email"] == _expected_masked_email("missing@508.dev")
    assert result["masked_email"] != "missing@508.dev"
    assert mock_api.request.call_count == 1


def test_docuseal_processor_returns_error_on_search_failure() -> None:
    """Processor should return failure payload when CRM search request fails."""
    mock_api = Mock()
    mock_api.request.side_effect = EspoAPIError("CRM unavailable")

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="broken@508.dev",
            completed_at="2026-02-25T12:00:00Z",
            submission_id=55,
        )

    assert result["success"] is False
    assert result["error"] == "CRM search failed: CRM unavailable"
    assert result["masked_email"] == _expected_masked_email("broken@508.dev")
    assert result["masked_email"] != "broken@508.dev"
