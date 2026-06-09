"""Unit tests for the shared Brevo API client."""

from unittest.mock import Mock, patch

import pytest
import requests

from five08.clients.brevo import BrevoAPIError, BrevoClient


def test_add_contact_to_list_posts_create_or_update_payload() -> None:
    response = Mock()
    response.status_code = 201
    response.content = b'{"id": 21}'
    response.json.return_value = {"id": 21}

    with patch("five08.clients.brevo.requests.post", return_value=response) as post:
        result = BrevoClient(
            api_key="brevo-key",
            timeout_seconds=7.0,
        ).add_contact_to_list(email="Jane@Example.com", list_id=4)

    post.assert_called_once_with(
        "https://api.brevo.com/v3/contacts",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": "brevo-key",
        },
        json={
            "email": "jane@example.com",
            "listIds": [4],
            "updateEnabled": True,
        },
        timeout=7.0,
    )
    assert result == {"id": 21}


def test_add_contact_to_list_accepts_empty_success_body() -> None:
    response = Mock()
    response.status_code = 204
    response.content = b""

    with patch("five08.clients.brevo.requests.post", return_value=response):
        result = BrevoClient(api_key="brevo-key").add_contact_to_list(
            email="jane@example.com",
            list_id=4,
        )

    assert result == {}


def test_add_contact_to_list_raises_on_request_error() -> None:
    with patch(
        "five08.clients.brevo.requests.post",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(BrevoAPIError, match="request failed"):
            BrevoClient(api_key="brevo-key").add_contact_to_list(
                email="jane@example.com",
                list_id=4,
            )
