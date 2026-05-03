"""Unit tests for the shared DocuSeal client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.docuseal import (
    DocusealAPIError,
    create_member_agreement_submission,
    normalize_docuseal_base_url,
)
from five08.tls import default_ca_bundle_path


def test_create_member_agreement_submission_posts_expected_payload() -> None:
    """Shared helper should create a standard member agreement submission."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'[{"id": 1, "submission_id": 4200}]'
    mock_response.json.return_value = [{"id": 1, "submission_id": 4200}]

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
        "https://docuseal.example.com/api/submissions",
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
        verify=default_ca_bundle_path(),
    )


def test_create_member_agreement_submission_normalizes_docuseal_cloud_ui_url() -> None:
    """Cloud UI URLs should be rewritten to the DocuSeal API host."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'[{"id": 1, "submission_id": 4200}]'
    mock_response.json.return_value = [{"id": 1, "submission_id": 4200}]

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ) as mock_request:
        create_member_agreement_submission(
            base_url="https://docuseal.com/",
            api_key="secret",
            template_id=1000001,
            submitter_name="Jane Doe",
            submitter_email="jane@example.com",
        )

    mock_request.assert_called_once()
    assert mock_request.call_args.args[1] == "https://api.docuseal.com/submissions"


def test_create_member_agreement_submission_normalizes_self_hosted_root_url() -> None:
    """Self-hosted root URLs should be rewritten to the `/api` base once."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'[{"id": 1, "submission_id": 4200}]'
    mock_response.json.return_value = [{"id": 1, "submission_id": 4200}]

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = create_member_agreement_submission(
            base_url="https://docuseal.508.dev",
            api_key="secret",
            template_id=1000001,
            submitter_name="Jane Doe",
            submitter_email="jane@example.com",
        )

    assert result == {"id": 4200}
    mock_request.assert_called_once()
    assert mock_request.call_args.args[1] == "https://docuseal.508.dev/api/submissions"


def test_create_member_agreement_submission_normalizes_submitter_list_response() -> (
    None
):
    """Create submission should accept DocuSeal's submitter array response."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = (
        b'[{"id": 11, "submission_id": 4200, "role": "First Party"}]'
    )
    mock_response.json.return_value = [
        {"id": 11, "submission_id": 4200, "role": "First Party"}
    ]

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        result = create_member_agreement_submission(
            base_url="https://docuseal.example.com",
            api_key="secret",
            template_id=1000001,
            submitter_name="Jane Doe",
            submitter_email="jane@example.com",
        )

    assert result == {"id": 4200}


def test_create_member_agreement_submission_raises_on_empty_submitter_list() -> None:
    """Create submission should reject empty submitter-array responses."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b"[]"
    mock_response.json.return_value = []

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(
            DocusealAPIError,
            match="API response did not include any submitters",
        ):
            create_member_agreement_submission(
                base_url="https://docuseal.example.com",
                api_key="secret",
                template_id=1000001,
                submitter_name="Jane Doe",
                submitter_email="jane@example.com",
            )


def test_create_member_agreement_submission_raises_on_non_object_submitter() -> None:
    """Create submission should reject submitter arrays with non-object entries."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'["not-an-object"]'
    mock_response.json.return_value = ["not-an-object"]

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(
            DocusealAPIError,
            match="API response submitter is not a JSON object",
        ):
            create_member_agreement_submission(
                base_url="https://docuseal.example.com",
                api_key="secret",
                template_id=1000001,
                submitter_name="Jane Doe",
                submitter_email="jane@example.com",
            )


def test_create_member_agreement_submission_raises_on_missing_submission_id() -> None:
    """Create submission should reject submitter arrays without a submission id."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'[{"id": 11, "role": "First Party"}]'
    mock_response.json.return_value = [{"id": 11, "role": "First Party"}]

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(
            DocusealAPIError,
            match="API response did not include a submission_id",
        ):
            create_member_agreement_submission(
                base_url="https://docuseal.example.com",
                api_key="secret",
                template_id=1000001,
                submitter_name="Jane Doe",
                submitter_email="jane@example.com",
            )


def test_create_member_agreement_submission_keeps_object_response() -> None:
    """Create submission should still accept object responses if returned."""
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": 4200}'
    mock_response.json.return_value = {"id": 4200}

    with patch(
        "five08.clients.docuseal.requests.request",
        return_value=mock_response,
    ):
        result = create_member_agreement_submission(
            base_url="https://docuseal.example.com",
            api_key="secret",
            template_id=1000001,
            submitter_name="Jane Doe",
            submitter_email="jane@example.com",
        )

    assert result == {"id": 4200}


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


def test_normalize_docuseal_base_url_leaves_self_hosted_url_unchanged() -> None:
    """Self-hosted DocuSeal API URLs should keep their configured base URL."""
    assert (
        normalize_docuseal_base_url("https://docuseal.example.com/api")
        == "https://docuseal.example.com/api"
    )


def test_normalize_docuseal_base_url_adds_api_to_self_hosted_root_url() -> None:
    """Self-hosted root URLs should normalize to the API base URL."""
    assert (
        normalize_docuseal_base_url("https://docuseal.example.com/")
        == "https://docuseal.example.com/api"
    )


def test_normalize_docuseal_base_url_leaves_docuseal_cloud_api_url_unchanged() -> None:
    """DocuSeal Cloud API URLs should not have `/api` appended again."""
    assert (
        normalize_docuseal_base_url("https://api.docuseal.com")
        == "https://api.docuseal.com"
    )


def test_normalize_docuseal_base_url_strips_query_and_fragment() -> None:
    """Normalized base URLs should drop query strings and fragments."""
    assert (
        normalize_docuseal_base_url("https://docuseal.example.com/api?x=1#frag")
        == "https://docuseal.example.com/api"
    )


def test_normalize_docuseal_base_url_rejects_empty_string() -> None:
    """Empty DocuSeal base URLs should fail with a clear validation error."""
    with pytest.raises(
        ValueError,
        match="DOCUSEAL_BASE_URL must be an absolute URL including scheme and host",
    ):
        normalize_docuseal_base_url("")


def test_normalize_docuseal_base_url_rejects_scheme_less_url() -> None:
    """Scheme-less DocuSeal base URLs should fail validation."""
    with pytest.raises(
        ValueError,
        match="DOCUSEAL_BASE_URL must be an absolute URL including scheme and host",
    ):
        normalize_docuseal_base_url("docuseal.508.dev")


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
