"""Focused unit tests for Migadu `/create-mailbox` command behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.migadu import CreateMailboxContactSelectView, MigaduCog


@pytest.fixture
def mock_bot() -> Mock:
    """Create a mock bot for testing."""
    bot = Mock()
    bot.get_cog = Mock()
    return bot


@pytest.fixture
def migadu_cog(mock_bot: Mock) -> MigaduCog:
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
    cog.espo_api = Mock()
    return cog


@pytest.fixture
def mock_interaction() -> AsyncMock:
    """Create a mock Discord interaction."""
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.id = 123
    interaction.user.name = "admin"
    admin_role = Mock()
    admin_role.name = "Admin"
    interaction.user.roles = [admin_role]
    return interaction


def test_normalize_mailbox_request_appends_508_domain(migadu_cog: MigaduCog) -> None:
    """Allow the command to accept bare usernames and append the 508 domain."""
    mailbox_email, local_part = migadu_cog._normalize_mailbox_request("Alice")

    assert mailbox_email == "alice@508.dev"
    assert local_part == "alice"


def test_normalize_mailbox_request_rejects_non_508_domain(
    migadu_cog: MigaduCog,
) -> None:
    """Reject explicit mailbox domains other than the configured 508 domain."""
    with pytest.raises(ValueError, match="must be omitted or use the @508.dev domain"):
        migadu_cog._normalize_mailbox_request("alice@gmail.com")


@pytest.mark.asyncio
async def test_create_mailbox_command_success_with_crm_defaults_and_sync(
    migadu_cog: MigaduCog,
    mock_interaction: AsyncMock,
) -> None:
    """Create a mailbox from a CRM match and sync `c508Email` back to the contact."""
    contact = {
        "id": "contact-1",
        "name": "Alice Example",
        "emailAddress": "alice@gmail.com",
        "c508Email": "",
        "cDiscordUsername": "alice",
    }
    migadu_cog._audit_command = Mock()
    migadu_cog._search_contacts_for_mailbox_candidate = AsyncMock(
        return_value=[contact]
    )
    migadu_cog._create_migadu_mailbox = AsyncMock(
        return_value={"address": "alice@508.dev"}
    )

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice",
        search_term="alice@gmail.com",
    )

    migadu_cog._create_migadu_mailbox.assert_awaited_once_with(
        local_part="alice",
        backup_email="alice@gmail.com",
        name="Alice Example",
    )
    migadu_cog.espo_api.update_contact.assert_called_once_with(
        "contact-1",
        {"c508Email": "alice@508.dev"},
    )

    _args, kwargs = mock_interaction.followup.send.call_args
    assert kwargs["embed"].title == "✅ Mailbox Created"


@pytest.mark.asyncio
async def test_create_mailbox_requires_backup_email_without_search_term(
    migadu_cog: MigaduCog,
    mock_interaction: AsyncMock,
) -> None:
    """Require a backup email when no CRM search term is provided."""
    migadu_cog._audit_command = Mock()

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice",
    )

    assert (
        "`backup_email` is required" in mock_interaction.followup.send.call_args[0][0]
    )


@pytest.mark.asyncio
async def test_create_mailbox_shows_contact_selector_for_multiple_matches(
    migadu_cog: MigaduCog,
    mock_interaction: AsyncMock,
) -> None:
    """Prompt the requester to choose a CRM contact when multiple eligible matches exist."""
    migadu_cog._audit_command = Mock()
    migadu_cog._search_contacts_for_mailbox_candidate = AsyncMock(
        return_value=[
            {
                "id": "contact-1",
                "name": "Alice Example",
                "emailAddress": "alice@gmail.com",
                "c508Email": "",
                "cDiscordUsername": "alice",
            },
            {
                "id": "contact-2",
                "name": "Alice Smith",
                "emailAddress": "asmith@gmail.com",
                "c508Email": "",
                "cDiscordUsername": "alice-smith",
            },
        ]
    )

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice",
        search_term="alice",
    )

    args, kwargs = mock_interaction.followup.send.call_args
    assert "Multiple CRM contacts match" in args[0]
    assert isinstance(kwargs["view"], CreateMailboxContactSelectView)


@pytest.mark.asyncio
async def test_create_mailbox_aborts_when_matched_contact_has_508_email(
    migadu_cog: MigaduCog,
    mock_interaction: AsyncMock,
) -> None:
    """Abort before creation when the selected CRM contact already has a 508 mailbox."""
    migadu_cog._audit_command = Mock()
    migadu_cog._search_contacts_for_mailbox_candidate = AsyncMock(
        return_value=[
            {
                "id": "contact-1",
                "name": "Alice Example",
                "emailAddress": "alice@gmail.com",
                "c508Email": "alice@508.dev",
                "cDiscordUsername": "alice",
            }
        ]
    )
    migadu_cog._create_migadu_mailbox = AsyncMock()

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice",
        search_term="alice@gmail.com",
    )

    migadu_cog._create_migadu_mailbox.assert_not_called()
    assert "already has a 508 mailbox" in mock_interaction.followup.send.call_args[0][0]


@pytest.mark.asyncio
async def test_create_mailbox_reports_partial_failure_when_crm_sync_fails(
    migadu_cog: MigaduCog,
    mock_interaction: AsyncMock,
) -> None:
    """Surface CRM sync failures after the mailbox has already been created."""
    migadu_cog._audit_command = Mock()
    migadu_cog._create_migadu_mailbox = AsyncMock(
        return_value={"address": "alice@508.dev"}
    )
    migadu_cog._try_resolve_contact_by_backup_email = Mock(return_value=None)
    migadu_cog._resolve_unique_contact_by_backup_email = Mock(
        side_effect=ValueError(
            "Mailbox was created, but no CRM contact was found for backup email `alice@gmail.com`."
        )
    )

    await migadu_cog.create_mailbox.callback(
        migadu_cog,
        mock_interaction,
        "alice",
        backup_email="alice@gmail.com",
    )

    _args, kwargs = mock_interaction.followup.send.call_args
    assert kwargs["embed"].title == "⚠️ Mailbox Created, CRM Sync Failed"
