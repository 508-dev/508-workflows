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


def test_add_contact_to_list_truncates_error_response_body() -> None:
    response = Mock()
    response.status_code = 400
    response.text = f"email=jane@example.com {'x' * 600}"

    with patch("five08.clients.brevo.requests.post", return_value=response):
        with pytest.raises(BrevoAPIError) as exc_info:
            BrevoClient(api_key="brevo-key").add_contact_to_list(
                email="jane@example.com",
                list_id=4,
            )

    message = str(exc_info.value)
    assert "jane@example.com" not in message
    assert "[redacted-email]" in message
    assert len(message) < 600


def test_get_contact_fetches_contact_by_email() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"email": "jane@example.com"}

    with patch("five08.clients.brevo.requests.get", return_value=response) as get:
        result = BrevoClient(api_key="brevo-key").get_contact("Jane@Example.com")

    get.assert_called_once_with(
        "https://api.brevo.com/v3/contacts/jane%40example.com",
        headers={"Accept": "application/json", "api-key": "brevo-key"},
        timeout=20.0,
    )
    assert result == {"email": "jane@example.com"}


def test_get_contact_url_encodes_plus_in_email() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"email": "jane+tag@example.com"}

    with patch("five08.clients.brevo.requests.get", return_value=response) as get:
        result = BrevoClient(api_key="brevo-key").get_contact("Jane+Tag@Example.com")

    get.assert_called_once_with(
        "https://api.brevo.com/v3/contacts/jane%2Btag%40example.com",
        headers={"Accept": "application/json", "api-key": "brevo-key"},
        timeout=20.0,
    )
    assert result == {"email": "jane+tag@example.com"}


def test_get_contact_returns_none_for_missing_contact() -> None:
    response = Mock()
    response.status_code = 404

    with patch("five08.clients.brevo.requests.get", return_value=response):
        result = BrevoClient(api_key="brevo-key").get_contact("jane@example.com")

    assert result is None


@pytest.mark.parametrize("email", ["", "not-an-email"])
def test_get_contact_rejects_invalid_email(email: str) -> None:
    with pytest.raises(ValueError, match="full email address"):
        BrevoClient(api_key="brevo-key").get_contact(email)


def test_get_contact_truncates_error_response_body() -> None:
    response = Mock()
    response.status_code = 500
    response.text = f"email=jane@example.com {'x' * 600}"

    with patch("five08.clients.brevo.requests.get", return_value=response):
        with pytest.raises(BrevoAPIError) as exc_info:
            BrevoClient(api_key="brevo-key").get_contact("jane@example.com")

    message = str(exc_info.value)
    assert "jane@example.com" not in message
    assert "[redacted-email]" in message
    assert len(message) < 600


def test_find_list_id_by_name_gets_matching_list() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "count": 2,
        "lists": [
            {"id": 3, "name": "Other"},
            {"id": 4, "name": "508 members"},
        ],
    }

    with patch("five08.clients.brevo.requests.get", return_value=response) as get:
        list_id = BrevoClient(api_key="brevo-key").find_list_id_by_name("508 Members")

    get.assert_called_once_with(
        "https://api.brevo.com/v3/contacts/lists",
        headers={"Accept": "application/json", "api-key": "brevo-key"},
        params={"limit": 50, "offset": 0, "sort": "asc"},
        timeout=20.0,
    )
    assert list_id == 4


def test_find_list_id_by_name_returns_none_when_missing() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"count": 1, "lists": [{"id": 3, "name": "Other"}]}

    with patch("five08.clients.brevo.requests.get", return_value=response):
        list_id = BrevoClient(api_key="brevo-key").find_list_id_by_name("508 members")

    assert list_id is None


def test_find_list_id_by_name_truncates_error_response_body() -> None:
    response = Mock()
    response.status_code = 503
    response.text = f"email=jane@example.com {'x' * 600}"

    with patch("five08.clients.brevo.requests.get", return_value=response):
        with pytest.raises(BrevoAPIError) as exc_info:
            BrevoClient(api_key="brevo-key").find_list_id_by_name("508 members")

    message = str(exc_info.value)
    assert "jane@example.com" not in message
    assert "[redacted-email]" in message
    assert len(message) < 600
