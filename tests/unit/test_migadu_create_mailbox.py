"""
Focused unit tests for Migadu /create-mailbox command behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.migadu import MigaduCog


@pytest.fixture
def mock_bot() -> Mock:
    """Create a mock bot for testing."""
    bot = Mock()
    bot.get_cog = Mock()
    return bot


@pytest.fixture
def migadu_cog(mock_bot):
    """Create a MigaduCog instance for testing."""
    with patch("five08.discord_bot.cogs.migadu.settings") as mock_settings:
        mock_settings.espo_api_key = "token"
        mock_settings.espo_base_url = "https://crm.example.com"
        mock_settings.audit_api_base_url = "https://audit.example.com"
        mock_settings.api_shared_secret = "secret"
        mock_settings.audit_api_timeout_seconds = 5.0
        mock_settings.discord_logs_webhook_url = None
        mock_settings.discord_logs_webhook_wait = False
        mock_settings.migadu_api_user = "migadu-user"
        mock_settings.migadu_api_key = "migadu-key"
        mock_settings.migadu_mailbox_domain = "508.dev"
        cog = MigaduCog(mock_bot)
    return cog


@pytest.fixture
def mock_interaction() -> Mock:
    """Create a mock Discord interaction."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.name = "admin"
    admin_role = Mock()
    admin_role.name = "Admin"
    interaction.user.roles = [admin_role]
    return interaction


def test_normalize_mailbox_request_requires_508_domain(migadu_cog):
    """Require mailbox usernames to be in the configured 508 domain."""
    with pytest.raises(ValueError, match="must be in the @508.dev domain"):
        migadu_cog._normalize_mailbox_request("alice@gmail.com", "alice@gmail.com")


@pytest.mark.asyncio
async def test_create_mailbox_command_success(
    migadu_cog, mock_interaction, monkeypatch
):
    """Create a mailbox directly without any CRM lookup."""
    monkeypatch.setattr(
        "five08.discord_bot.cogs.migadu.settings.migadu_api_user", "migadu-user"
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.migadu.settings.migadu_api_key", "migadu-key"
    )
    monkeypatch.setattr(
        "five08.discord_bot.cogs.migadu.settings.migadu_mailbox_domain", "508.dev"
    )

    migadu_cog._audit_command = Mock()
    migadu_cog._create_migadu_mailbox = AsyncMock(
        return_value={"address": "alice@508.dev"}
    )

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice@508.dev",
        "alice@gmail.com",
    )

    migadu_cog._create_migadu_mailbox.assert_awaited_once_with(
        local_part="alice", backup_email="alice@gmail.com"
    )
    args, kwargs = mock_interaction.followup.send.call_args
    assert kwargs["embed"].title == "✅ Mailbox Created"


@pytest.mark.asyncio
async def test_create_mailbox_command_requires_migadu_credentials(
    mock_bot, mock_interaction, monkeypatch
):
    """Return a clear error when Migadu credentials are missing."""
    monkeypatch.setattr("five08.discord_bot.cogs.migadu.settings.migadu_api_user", "")
    monkeypatch.setattr("five08.discord_bot.cogs.migadu.settings.migadu_api_key", "")
    monkeypatch.setattr(
        "five08.discord_bot.cogs.migadu.settings.migadu_mailbox_domain", "508.dev"
    )
    migadu_cog = MigaduCog(mock_bot)

    migadu_cog._audit_command = Mock()

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice@508.dev",
        "alice@gmail.com",
    )

    assert (
        "MIGADU_API_USER is required to create Migadu mailboxes."
        in (mock_interaction.followup.send.call_args[0][0])
    )
