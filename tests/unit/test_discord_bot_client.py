"""Unit tests for the shared Discord bot internal API client."""

from unittest.mock import Mock, patch

import pytest

from five08.clients.discord_bot import (
    DiscordBotAPIError,
    grant_member_role_for_signed_agreement,
)


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
        "POST",
        "http://discord-bot.internal/internal/member-agreements/member-role",
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
