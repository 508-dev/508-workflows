"""Unit tests for Docuseal processor logic."""

import pytest
from unittest.mock import Mock, patch

from five08.clients.espo import EspoAPIError
from five08.worker.crm.docuseal_processor import (
    DocusealAgreementNonRetryableError,
    DocusealAgreementProcessingError,
    DocusealAgreementProcessor,
)
from five08.worker.masking import mask_email


def test_docuseal_processor_marks_member_agreement_signed_timestamp() -> None:
    """Processor should update the member agreement signed-at timestamp."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        {"updated": True},
    ]
    expected_email = "member@508.dev"
    expected_masked = mask_email(expected_email)

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
        "cMemberAgreementSignedAt": "2026-02-25 12:00:00",
    }
    assert result["success"] is True
    assert result["masked_email"] == expected_masked
    assert result["contact_id"] == "contact-1"
    assert result["submission_id"] == 416
    assert result["completed_at"] == "2026-02-25 12:00:00"
    assert "email" not in result


def test_docuseal_processor_normalizes_completed_at_to_utc_timestamp() -> None:
    """Processor should convert a UTC-offset timestamp before writing CRM."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        {"updated": True},
    ]

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        result = processor.process_agreement(
            email="member@508.dev",
            completed_at="2026-03-02T10:02:30.572+02:00",
            submission_id=416,
        )

    assert mock_api.request.call_args_list[1].args[2] == {
        "cMemberAgreementSignedAt": "2026-03-02 08:02:30",
    }
    assert result["completed_at"] == "2026-03-02 08:02:30"


def test_docuseal_processor_raises_on_invalid_completed_at() -> None:
    """Processor should raise so the job runner can mark the job non-retryable/dead."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
    ]

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementNonRetryableError) as exc_info:
            processor.process_agreement(
                email="member@508.dev",
                completed_at="not-a-date",
                submission_id=416,
            )

    assert "invalid_completed_at for contact_id=contact-1" in str(exc_info.value)
    assert mock_api.request.call_count == 1


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
    assert result["masked_email"] == mask_email("missing@508.dev")
    assert result["masked_email"] != "missing@508.dev"
    assert mock_api.request.call_count == 1


def test_docuseal_processor_raises_on_search_failure() -> None:
    """Processor should raise when CRM search fails to trigger job retries."""
    mock_api = Mock()
    mock_api.request.side_effect = EspoAPIError("CRM unavailable")

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementProcessingError) as exc_info:
            processor.process_agreement(
                email="broken@508.dev",
                completed_at="2026-02-25T12:00:00Z",
                submission_id=55,
            )

    assert (
        str(exc_info.value)
        == f"CRM search failed for masked_email={mask_email('broken@508.dev')}: "
        "CRM unavailable"
    )


def test_docuseal_processor_raises_on_update_failure() -> None:
    """Processor should raise when CRM update fails to trigger job retries."""
    mock_api = Mock()
    mock_api.request.side_effect = [
        {"list": [{"id": "contact-1"}]},
        EspoAPIError("write failed"),
    ]

    with patch("five08.worker.crm.docuseal_processor.EspoAPI", return_value=mock_api):
        processor = DocusealAgreementProcessor()
        with pytest.raises(DocusealAgreementProcessingError) as exc_info:
            processor.process_agreement(
                email="member@508.dev",
                completed_at="2026-02-25T12:00:00Z",
                submission_id=9001,
            )

    assert (
        str(exc_info.value)
        == "CRM update failed for contact_id=contact-1: write failed"
    )
