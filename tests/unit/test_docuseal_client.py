"""Unit tests for the shared DocuSeal client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.docuseal import DocusealAPIError, create_member_agreement_submission


def test_create_member_agreement_submission_posts_expected_payload() -> None:
    """Shared helper should create a standard member agreement submission."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": 4200}'
    mock_response.json.return_value = {"id": 4200}

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = create_member_agreement_submission(
            base_url="https://docuseal.example.com/",
            api_key="secret",
            template_id=1000001,
            submitter_name="Jane Doe",
            submitter_email="jane@example.com",
        )

    assert result == {"id": 4200}
    mock_request.assert_called_once_with(
        "POST",
        "https://docuseal.example.com/submissions",
        headers={
            "Content-Type": "application/json",
            "X-Auth-Token": "secret",
        },
        json={
            "template_id": 1000001,
            "send_email": True,
            "submitters": [
                {
                    "name": "Jane Doe",
                    "role": "First Party",
                    "email": "jane@example.com",
                }
            ],
        },
        timeout=20.0,
    )


def test_create_member_agreement_submission_raises_on_api_error() -> None:
    """Non-2xx responses should raise a DocuSeal API error."""
    mock_response = Mock()
    mock_response.status_code = 422
    mock_response.text = "template is invalid"
    mock_response.content = b"template is invalid"

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(DocusealAPIError, match="status code is 422"):
            create_member_agreement_submission(
                base_url="https://docuseal.example.com",
                api_key="secret",
                template_id=1000001,
                submitter_name="Jane Doe",
                submitter_email="jane@example.com",
            )


def test_create_member_agreement_submission_raises_on_invalid_json_body() -> None:
    """2xx responses with invalid JSON should still raise DocusealAPIError."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = "not-json"
    mock_response.content = b"not-json"
    mock_response.json.side_effect = ValueError("bad json")

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(
            DocusealAPIError,
            match="Failed to decode JSON response \\(status 200\\)",
        ):
            create_member_agreement_submission(
                base_url="https://docuseal.example.com",
                api_key="secret",
                template_id=1000001,
                submitter_name="Jane Doe",
                submitter_email="jane@example.com",
            )
