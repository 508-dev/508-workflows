"""Unit tests for the shared Keila API client."""

from unittest.mock import Mock, patch

import pytest
import requests

from five08.clients.keila import KeilaAPIError, KeilaClient


def test_get_contact_by_email_fetches_contact() -> None:
    response = Mock()
    response.status_code = 200
    response.content = b'{"data":{"id":"contact-1","email":"jane@example.com"}}'
    response.json.return_value = {
        "data": {"id": "contact-1", "email": "jane@example.com"}
    }

    with patch(
        "five08.clients.keila.requests.request", return_value=response
    ) as request:
        result = KeilaClient(api_key="keila-key").get_contact_by_email(
            "Jane@Example.com"
        )

    request.assert_called_once_with(
        "GET",
        "https://app.keila.io/api/v1/contacts/jane%40example.com",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer keila-key",
        },
        params={"id_type": "email"},
        json=None,
        timeout=20.0,
    )
    assert result == {"id": "contact-1", "email": "jane@example.com"}


def test_get_contact_by_email_url_encodes_plus_in_email() -> None:
    response = Mock()
    response.status_code = 200
    response.content = b'{"data":{"id":"contact-1","email":"jane+tag@example.com"}}'
    response.json.return_value = {
        "data": {"id": "contact-1", "email": "jane+tag@example.com"}
    }

    with patch(
        "five08.clients.keila.requests.request", return_value=response
    ) as request:
        result = KeilaClient(api_key="keila-key").get_contact_by_email(
            "Jane+Tag@Example.com"
        )

    request.assert_called_once_with(
        "GET",
        "https://app.keila.io/api/v1/contacts/jane%2Btag%40example.com",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer keila-key",
        },
        params={"id_type": "email"},
        json=None,
        timeout=20.0,
    )
    assert result == {"id": "contact-1", "email": "jane+tag@example.com"}


def test_get_contact_by_email_returns_none_for_missing_contact() -> None:
    response = Mock()
    response.status_code = 404

    with patch("five08.clients.keila.requests.request", return_value=response):
        result = KeilaClient(api_key="keila-key").get_contact_by_email(
            "jane@example.com"
        )

    assert result is None


def test_upsert_active_contact_creates_missing_contact() -> None:
    missing = Mock()
    missing.status_code = 404
    created = Mock()
    created.status_code = 201
    created.content = b'{"data":{"id":"contact-1"}}'
    created.json.return_value = {"data": {"id": "contact-1"}}

    with patch(
        "five08.clients.keila.requests.request",
        side_effect=[missing, created],
    ) as request:
        result = KeilaClient(api_key="keila-key").upsert_active_contact(
            email="jane@example.com",
            first_name="Jane",
            last_name="Doe",
            data={"audiences": ["508_members"]},
        )

    assert request.call_args_list[1].kwargs["json"] == {
        "data": {
            "email": "jane@example.com",
            "status": "active",
            "data": {"audiences": ["508_members"]},
            "first_name": "Jane",
            "last_name": "Doe",
        }
    }
    assert result == {"id": "contact-1"}


def test_upsert_active_contact_updates_existing_contact_without_status() -> None:
    existing = Mock()
    existing.status_code = 200
    existing.content = b'{"data":{"id":"contact-1","status":"active"}}'
    existing.json.return_value = {"data": {"id": "contact-1", "status": "active"}}
    updated = Mock()
    updated.status_code = 200
    updated.content = b'{"data":{"id":"contact-1"}}'
    updated.json.return_value = {"data": {"id": "contact-1"}}

    with patch(
        "five08.clients.keila.requests.request",
        side_effect=[existing, updated],
    ) as request:
        result = KeilaClient(api_key="keila-key").upsert_active_contact(
            email="jane@example.com",
            data={"audiences": ["508_members"]},
        )

    assert request.call_args_list[1].args == (
        "PATCH",
        "https://app.keila.io/api/v1/contacts/contact-1",
    )
    assert request.call_args_list[1].kwargs["json"] == {
        "data": {
            "email": "jane@example.com",
            "data": {"audiences": ["508_members"]},
        }
    }
    assert result == {"id": "contact-1"}


def test_keila_client_raises_on_request_error() -> None:
    with patch(
        "five08.clients.keila.requests.request",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(KeilaAPIError, match="request failed"):
            KeilaClient(api_key="keila-key").get_contact_by_email("jane@example.com")
