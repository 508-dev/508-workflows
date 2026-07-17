"""Unit tests for the shared Discord bot internal API client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.discord_bot import (
    DiscordBotAPIError,
    DiscordBotClient,
    grant_member_role_for_signed_agreement,
)
from five08.tls import default_ca_bundle_path


def test_grant_member_role_posts_expected_payload() -> None:
    """Shared helper should call the bot endpoint with the signing metadata."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b'{"status": "applied"}'
    mock_response.json.return_value = {"status": "applied"}

    with patch(
        "five08.clients.discord_bot.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = grant_member_role_for_signed_agreement(
            base_url="http://discord-bot.internal/",
            api_secret="secret",
            discord_user_id="12345",
            contact_id="contact-1",
            contact_name="Jane Doe",
            submission_id=4200,
            completed_at="2026-03-25 12:00:00",
        )

    assert result == {"status": "applied"}
    mock_request.assert_called_once_with(
        method="POST",
        url="http://discord-bot.internal/internal/member-agreements/member-role",
        headers={
            "Content-Type": "application/json",
            "X-API-Secret": "secret",
        },
        json={
            "discord_user_id": "12345",
            "contact_id": "contact-1",
            "contact_name": "Jane Doe",
            "submission_id": 4200,
            "completed_at": "2026-03-25 12:00:00",
        },
        timeout=10.0,
        verify=default_ca_bundle_path(),
    )


def test_grant_member_role_raises_on_api_error() -> None:
    """Non-2xx responses should raise a bot API error."""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = "missing_manage_roles_permission"
    mock_response.content = b"missing_manage_roles_permission"

    with patch(
        "five08.clients.discord_bot.requests.request",
        return_value=mock_response,
    ):
        with pytest.raises(DiscordBotAPIError, match="status code is 403"):
            grant_member_role_for_signed_agreement(
                base_url="http://discord-bot.internal",
                api_secret="secret",
                discord_user_id="12345",
            )


def test_request_omits_json_argument_when_payload_is_none() -> None:
    """Raw client requests should not send a JSON null body by default."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"{}"
    mock_response.json.return_value = {}

    with patch(
        "five08.clients.discord_bot.requests.request",
        return_value=mock_response,
    ) as mock_request:
        client = DiscordBotClient("http://discord-bot.internal", "secret")
        result = client.request("GET", "/health")

    assert result == {}
    mock_request.assert_called_once_with(
        method="GET",
        url="http://discord-bot.internal/health",
        headers={
            "Content-Type": "application/json",
            "X-API-Secret": "secret",
        },
        timeout=10.0,
        verify=default_ca_bundle_path(),
    )


def test_post_project_payment_notification_posts_stable_outbox_identity() -> None:
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b'{"status": "sent", "message_id": "123"}'
    mock_response.json.return_value = {"status": "sent", "message_id": "123"}

    with patch(
        "five08.clients.discord_bot.requests.request",
        return_value=mock_response,
    ) as mock_request:
        result = DiscordBotClient(
            "http://discord-bot.internal", "secret"
        ).post_project_payment_notification(
            notification_id="00000000-0000-0000-0000-000000000001",
            lease_token="00000000-0000-0000-0000-000000000002",
        )

    assert result == {"status": "sent", "message_id": "123"}
    mock_request.assert_called_once_with(
        method="POST",
        url="http://discord-bot.internal/internal/project-payments/notify",
        headers={
            "Content-Type": "application/json",
            "X-API-Secret": "secret",
        },
        json={
            "notification_id": "00000000-0000-0000-0000-000000000001",
            "lease_token": "00000000-0000-0000-0000-000000000002",
        },
        timeout=10.0,
        verify=default_ca_bundle_path(),
    )
