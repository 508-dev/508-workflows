"""Unit tests for the CRM SSO provisioning command."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.crm import CRMCog


@pytest.fixture
def mock_interaction() -> AsyncMock:
    interaction = AsyncMock()
    interaction.response = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.user = Mock()
    role = Mock()
    role.name = "Admin"
    interaction.user.roles = [role]
    return interaction


@pytest.fixture
def mock_espo_api() -> Mock:
    with patch("five08.discord_bot.cogs.crm.EspoClient") as mock_client_class:
        mock_api = Mock()
        mock_client_class.return_value = mock_api
        yield mock_api


@pytest.fixture
def cog(mock_espo_api: Mock) -> CRMCog:
    return CRMCog(Mock())


@pytest.mark.asyncio
async def test_create_sso_user_creates_links_and_sends_recovery_email(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": None,
    }
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.return_value = []
    authentik_client.create_user.return_value = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client.resolve_email_stage_id.return_value = "stage-id"
    authentik_client.send_recovery_email.return_value = None

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    mock_interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    authentik_client.create_user.assert_called_once_with(
        username="jane",
        name="Jane Doe",
        email="jane@508.dev",
    )
    authentik_client.resolve_email_stage_id.assert_called_once_with(
        stage_id=None,
        stage_name="default-recovery-email",
    )
    authentik_client.send_recovery_email.assert_called_once_with(
        user_id=42,
        email_stage="stage-id",
    )
    mock_espo_api.request.assert_called_once_with(
        "PUT",
        "Contact/crm-123",
        {"cSsoID": "42"},
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created SSO user" in message
    assert "Recovery email: sent." in message
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_create_sso_user_links_existing_user_without_recovery_email(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": None,
    }
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.return_value = [
        {
            "pk": 42,
            "username": "jane",
            "email": "jane@508.dev",
            "name": "Jane Doe",
            "is_superuser": False,
        }
    ]

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    authentik_client.create_user.assert_not_called()
    authentik_client.send_recovery_email.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Linked the existing SSO user" in message


@pytest.mark.asyncio
async def test_create_sso_user_rejects_superuser_match(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": "42",
    }
    authentik_client = Mock()
    authentik_client.get_user.return_value = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": True,
    }

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    message = mock_interaction.followup.send.call_args.args[0]
    assert "superuser" in message


@pytest.mark.asyncio
async def test_create_sso_user_respects_already_linked_non_superuser(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": "42",
    }
    authentik_client = Mock()
    authentik_client.get_user.return_value = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    authentik_client.get_user.assert_called_once_with(42)
    authentik_client.create_user.assert_not_called()
    authentik_client.send_recovery_email.assert_not_called()
    mock_espo_api.request.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "already linked to the matching SSO user" in message
