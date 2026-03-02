"""
Focused unit tests for create-mailbox command behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs import crm
from five08.discord_bot.cogs.crm import CRMCog


@pytest.fixture
def mock_bot() -> Mock:
    """Create a mock bot for testing."""
    bot = Mock()
    bot.get_cog = Mock()
    return bot


@pytest.fixture
def mock_espo_api():
    """Create a mock EspoAPI for testing."""
    with patch("five08.discord_bot.cogs.crm.EspoAPI") as mock_api_class:
        mock_api = Mock()
        mock_api_class.return_value = mock_api
        yield mock_api


@pytest.fixture
def crm_cog(mock_bot, mock_espo_api):
    """Create a CRMCog instance for testing."""
    cog = CRMCog(mock_bot)
    cog.espo_api = mock_espo_api
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


def test_normalize_mailbox_request_requires_full_email(crm_cog, monkeypatch):
    """Require fully-qualified email addresses for backup lookup."""
    monkeypatch.setattr(crm.settings, "migadu_mailbox_domain", "508.dev")

    with pytest.raises(ValueError, match="full email address"):
        crm_cog._normalize_mailbox_request("alice")

    with pytest.raises(ValueError, match="missing a domain"):
        crm_cog._normalize_mailbox_request("alice@")


def test_normalize_mailbox_request_rejects_508_email(crm_cog, monkeypatch):
    """Reject @508.dev backup emails."""
    monkeypatch.setattr(crm.settings, "migadu_mailbox_domain", "508.dev")

    with pytest.raises(ValueError, match="cannot be an @508\\.dev email"):
        crm_cog._normalize_mailbox_request("alice@508.dev")


@pytest.mark.asyncio
async def test_normalize_and_create_mailbox_command_success(
    crm_cog, mock_interaction, monkeypatch
):
    """Create the mailbox and update the CRM contact for a single match."""
    monkeypatch.setattr(crm.settings, "migadu_api_user", "migadu-user")
    monkeypatch.setattr(crm.settings, "migadu_api_key", "migadu-key")
    monkeypatch.setattr(crm.settings, "migadu_mailbox_domain", "508.dev")

    crm_cog._audit_command = Mock()
    crm_cog._search_contacts_for_mailbox_command = AsyncMock(
        return_value=[{"id": "contact-1", "name": "Alice"}]
    )
    crm_cog._create_migadu_mailbox = AsyncMock(
        return_value={"address": "alice@508.dev"}
    )

    await crm_cog.create_mailbox.callback(crm_cog, mock_interaction, "alice@gmail.com")

    crm_cog._search_contacts_for_mailbox_command.assert_awaited_once_with(
        backup_email="alice@gmail.com",
        mailbox_email="alice@508.dev",
    )
    crm_cog._create_migadu_mailbox.assert_awaited_once_with(
        local_part="alice", backup_email="alice@gmail.com"
    )
    crm_cog.espo_api.request.assert_called_once_with(
        "PUT",
        "Contact/contact-1",
        {"c508Email": "alice@508.dev"},
    )
    args, kwargs = mock_interaction.followup.send.call_args
    assert kwargs["embed"].title == "✅ Mailbox Created"


@pytest.mark.asyncio
async def test_create_mailbox_command_rejects_multiple_contacts(
    crm_cog, mock_interaction
):
    """Return a helpful message when multiple contacts match."""
    crm_cog._audit_command = Mock()
    crm_cog._search_contacts_for_mailbox_command = AsyncMock(
        return_value=[
            {"id": "contact-1", "name": "Alice", "emailAddress": "alice@gmail.com"},
            {"id": "contact-2", "name": "Alice B", "emailAddress": "alice@another.com"},
        ]
    )
    crm_cog._create_migadu_mailbox = AsyncMock()

    await crm_cog.create_mailbox.callback(
        crm_cog,
        mock_interaction,
        "alice@gmail.com",
    )

    crm_cog._create_migadu_mailbox.assert_not_awaited()
    mock_interaction.followup.send.assert_called_once()
    assert (
        "Multiple contacts match this value"
        in (mock_interaction.followup.send.call_args[0][0])
    )


@pytest.mark.asyncio
async def test_create_mailbox_command_requires_migadu_credentials(
    crm_cog, mock_interaction
):
    """Return a clear error when Migadu credentials are not configured."""
    crm_cog._audit_command = Mock()
    crm_cog._search_contacts_for_mailbox_command = AsyncMock(
        return_value=[{"id": "contact-1", "name": "Alice"}]
    )

    await crm_cog.create_mailbox.callback(
        crm_cog,
        mock_interaction,
        "alice@gmail.com",
    )

    crm_cog.espo_api.request.assert_not_called()
    assert (
        "MIGADU_API_USER is required"
        in (mock_interaction.followup.send.call_args[0][0])
    )
