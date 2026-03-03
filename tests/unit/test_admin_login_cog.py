"""Unit tests for the admin login Discord cog."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.admin_login import AdminLoginCog


@pytest.fixture
def mock_interaction() -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    interaction.user.id = 123456789
    role = Mock()
    role.name = "Admin"
    interaction.user.roles = [role]
    return interaction


@pytest.fixture
def cog() -> AdminLoginCog:
    return AdminLoginCog(Mock())


@pytest.mark.asyncio
async def test_login_command_returns_link(
    cog: AdminLoginCog, mock_interaction: AsyncMock
) -> None:
    with (
        patch.object(
            cog,
            "_create_login_link",
            new=AsyncMock(
                return_value=("https://dash.508.dev/auth/discord/link/token", 600)
            ),
        ),
        patch.object(cog, "_audit") as mock_audit,
    ):
        await cog.login.callback(cog, mock_interaction)

    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    mock_interaction.followup.send.assert_awaited_once()
    sent_message = mock_interaction.followup.send.call_args.args[0]
    assert "https://dash.508.dev/auth/discord/link/token" in sent_message
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_login_command_denied_when_user_not_admin(
    cog: AdminLoginCog, mock_interaction: AsyncMock
) -> None:
    with (
        patch.object(
            cog,
            "_create_login_link",
            new=AsyncMock(side_effect=PermissionError("discord_user_not_admin")),
        ),
        patch.object(cog, "_audit"),
    ):
        await cog.login.callback(cog, mock_interaction)

    sent_message = mock_interaction.followup.send.call_args.args[0]
    assert "not allowed" in sent_message


@pytest.mark.asyncio
async def test_login_command_handles_missing_secret(
    cog: AdminLoginCog, mock_interaction: AsyncMock
) -> None:
    with (
        patch.object(
            cog,
            "_create_login_link",
            new=AsyncMock(side_effect=ValueError("missing secret")),
        ),
        patch.object(cog, "_audit"),
    ):
        await cog.login.callback(cog, mock_interaction)

    sent_message = mock_interaction.followup.send.call_args.args[0]
    assert "not configured" in sent_message
