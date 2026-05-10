"""Unit tests for the shared Outline API client."""

from unittest.mock import Mock, patch

import pytest
import requests

from five08.clients.outline import (
    OutlineAPIError,
    OutlineClient,
    normalize_outline_api_base_url,
)


def test_normalize_outline_api_base_url_accepts_root_url() -> None:
    assert (
        normalize_outline_api_base_url("https://outline.example.com/")
        == "https://outline.example.com/api"
    )


def test_normalize_outline_api_base_url_accepts_api_url() -> None:
    assert (
        normalize_outline_api_base_url("https://outline.example.com/api/")
        == "https://outline.example.com/api"
    )


def test_invite_user_posts_outline_rpc_payload() -> None:
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "ok": True,
        "data": {
            "sent": [{"email": "jane@508.dev", "name": "Jane Doe"}],
            "users": [],
        },
    }

    with patch("five08.clients.outline.requests.post", return_value=response) as post:
        result = OutlineClient(
            api_key="outline-key",
            base_url="https://outline.example.com/",
            timeout_seconds=7.0,
        ).invite_user(email="jane@508.dev", name="Jane Doe")

    post.assert_called_once_with(
        "https://outline.example.com/api/users.invite",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer outline-key",
            "Content-Type": "application/json",
        },
        json={
            "invites": [{"email": "jane@508.dev", "name": "Jane Doe"}],
            "suppressEmail": False,
        },
        timeout=7.0,
    )
    assert result["ok"] is True


def test_invite_user_raises_on_http_error() -> None:
    response = Mock()
    response.status_code = 403
    response.text = "Forbidden"

    with patch("five08.clients.outline.requests.post", return_value=response):
        with pytest.raises(OutlineAPIError, match="status=403"):
            OutlineClient(api_key="outline-key").invite_user(email="jane@508.dev")


def test_invite_user_raises_on_request_error() -> None:
    with patch(
        "five08.clients.outline.requests.post",
        side_effect=requests.Timeout("timed out"),
    ):
        with pytest.raises(OutlineAPIError, match="request failed"):
            OutlineClient(api_key="outline-key").invite_user(email="jane@508.dev")
