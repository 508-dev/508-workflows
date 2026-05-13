"""Unit tests for the CRM SSO provisioning command."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.crm import (
    CRMCog,
    CreateSSOUserSelectionView,
    CreateUserAccountsSelectionView,
    OutlineInviteSelectionView,
    SSOProvisioningPartialError,
)
from five08.clients.authentik import AuthentikAPIError
from five08.clients.espo import EspoAPIError
from five08.clients.outline import OutlineAPIError


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
    followup_kwargs = mock_interaction.followup.send.call_args.kwargs
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created SSO user" in message
    assert "Recovery email: sent." in message
    assert followup_kwargs["ephemeral"] is True
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs["metadata"]["freshly_created"] is True


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
    mock_espo_api.request.assert_called_once_with(
        "PUT",
        "Contact/crm-123",
        {"cSsoID": "42"},
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Linked the existing SSO user" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True


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
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True


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
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_create_sso_user_rejects_mismatched_email_style_username(
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
        "username": "jane@contractor.com",
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

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Matched Authentik username does not match" in message


@pytest.mark.asyncio
async def test_create_sso_user_reports_partial_success_when_crm_update_fails(
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
    mock_espo_api.request.side_effect = EspoAPIError("crm update failed")

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created the SSO user, but failed to update CRM" in message
    assert "SSO user ID: `42`" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_user_id"] == 42
    assert audit_metadata["partial_success"] == "sso_created_crm_update_failed"


@pytest.mark.asyncio
async def test_create_sso_user_reports_partial_success_when_local_validation_fails(
    cog: CRMCog, mock_interaction: AsyncMock
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
        "username": "other-user",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client.resolve_email_stage_id.return_value = "stage-id"

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created the SSO user, but failed to validate" in message
    assert "SSO user ID: `42`" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_user_id"] == 42
    assert audit_metadata["partial_success"] == "sso_created_validation_failed"


@pytest.mark.asyncio
async def test_create_sso_user_reconciles_user_after_create_error(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": None,
    }
    reconciled_user = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.side_effect = [
        [],
        [reconciled_user],
    ]
    authentik_client.create_user.side_effect = AuthentikAPIError(
        "Authentik request failed with status 405: Method Not Allowed"
    )
    authentik_client.resolve_email_stage_id.return_value = "stage-id"
    authentik_client.send_recovery_email.return_value = None
    authentik_client.status_code = 405

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

    authentik_client.send_recovery_email.assert_not_called()
    mock_espo_api.request.assert_called_once_with(
        "PUT",
        "Contact/crm-123",
        {"cSsoID": "42"},
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Linked the existing SSO user" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert mock_audit.call_args.kwargs["metadata"]["freshly_created"] is False
    mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_create_sso_user_reports_partial_success_when_reconciled_crm_update_fails(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "c508Email": "jane@508.dev",
        "cSsoID": None,
    }
    reconciled_user = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.side_effect = [
        [],
        [reconciled_user],
    ]
    authentik_client.create_user.side_effect = AuthentikAPIError(
        "Authentik request failed with status 405: Method Not Allowed"
    )
    authentik_client.resolve_email_stage_id.return_value = "stage-id"
    authentik_client.status_code = 405
    mock_espo_api.request.side_effect = EspoAPIError("crm update failed")

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="jane")

    authentik_client.send_recovery_email.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Recovered the SSO user after the create request failed" in message
    assert "SSO user ID: `42`" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_user_id"] == 42
    assert audit_metadata["partial_success"] == "sso_reconciled_crm_update_failed"


@pytest.mark.asyncio
async def test_create_sso_user_shows_selection_view_for_multiple_contacts(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contacts = [
        {
            "id": "crm-123",
            "name": "Jane Doe",
            "c508Email": "jane@508.dev",
        },
        {
            "id": "crm-456",
            "name": "John Doe",
            "c508Email": "john@508.dev",
        },
    ]

    with patch.object(
        cog,
        "_search_contacts_for_lookup",
        new=AsyncMock(return_value=contacts),
    ):
        sent_message = Mock()
        mock_interaction.followup.send = AsyncMock(return_value=sent_message)
        await cog.create_sso_user.callback(cog, mock_interaction, search_term="doe")

    mock_interaction.followup.send.assert_awaited_once()
    kwargs = mock_interaction.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    view = kwargs["view"]
    assert isinstance(view, CreateSSOUserSelectionView)
    labels = [item.label for item in view.children if hasattr(item, "label")]
    assert labels == ["Jane Doe", "John Doe"]


@pytest.mark.asyncio
async def test_create_user_accounts_creates_mailbox_sso_and_outline_invite(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@508.dev"}
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
    outline_client = Mock()
    outline_client.invite_user.return_value = {
        "ok": True,
        "data": {"sent": [{"email": "jane@508.dev"}], "users": []},
    }

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    migadu_client.create_mailbox.assert_called_once()
    mailbox_request = migadu_client.create_mailbox.call_args.args[0]
    assert mailbox_request.local_part == "jane"
    assert mailbox_request.backup_email == "jane.personal@example.com"
    assert mailbox_request.name == "Jane Doe"
    authentik_client.create_user.assert_called_once_with(
        username="jane",
        name="Jane Doe",
        email="jane@508.dev",
    )
    outline_client.invite_user.assert_called_once_with(
        email="jane@508.dev",
        name="Jane Doe",
        role="member",
    )
    assert mock_espo_api.request.call_args_list[0].args == (
        "PUT",
        "Contact/crm-123",
        {"c508Email": "jane@508.dev"},
    )
    assert mock_espo_api.request.call_args_list[1].args == (
        "PUT",
        "Contact/crm-123",
        {"cSsoID": "42"},
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "User accounts are ready" in message
    assert "Email: `jane@508.dev`" in message
    assert "Outline invite: sent." in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert mock_audit.call_args.kwargs["metadata"]["outline_invited"] is True


@pytest.mark.asyncio
async def test_create_user_accounts_uses_configured_mailbox_domain_for_sso(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@example.org"}
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.return_value = []
    authentik_client.create_user.return_value = {
        "pk": 42,
        "username": "jane",
        "email": "jane@example.org",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client.resolve_email_stage_id.return_value = "stage-id"
    authentik_client.send_recovery_email.return_value = None
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch(
            "five08.discord_bot.cogs.crm.settings.migadu_mailbox_domain", "example.org"
        ),
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    authentik_client.create_user.assert_called_once_with(
        username="jane",
        name="Jane Doe",
        email="jane@example.org",
    )
    outline_client.invite_user.assert_called_once_with(
        email="jane@example.org",
        name="Jane Doe",
        role="member",
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Email: `jane@example.org`" in message


def test_contact_lookup_bare_username_uses_configured_mailbox_domain(
    cog: CRMCog,
) -> None:
    with patch(
        "five08.discord_bot.cogs.crm.settings.migadu_mailbox_domain", "example.org"
    ):
        filters = cog._build_contact_search_filters("jane")

    assert {
        "type": "equals",
        "attribute": "c508Email",
        "value": "jane@example.org",
    } in filters


@pytest.mark.asyncio
async def test_create_user_accounts_reuses_existing_mailbox(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
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
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client") as migadu_client,
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane@508.dev",
        )

    migadu_client.assert_not_called()
    mock_espo_api.request.assert_not_called()
    outline_client.invite_user.assert_called_once_with(
        email="jane@508.dev",
        name="Jane Doe",
        role="member",
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Mailbox: already existed/reused." in message
    assert "SSO: already existed/reused" in message


@pytest.mark.asyncio
async def test_create_user_accounts_reuses_existing_mailbox_without_backup_email(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "",
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
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client") as migadu_client,
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane@508.dev",
        )

    migadu_client.assert_not_called()
    mock_espo_api.request.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "User accounts are ready" in message


@pytest.mark.asyncio
async def test_create_user_accounts_primary_508_email_does_not_skip_mailbox_creation(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane@508.dev",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@508.dev"}
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
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe"),
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    migadu_client.create_mailbox.assert_called_once()
    assert mock_espo_api.request.call_args_list[0].args == (
        "PUT",
        "Contact/crm-123",
        {"c508Email": "jane@508.dev"},
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Mailbox: created." in message


@pytest.mark.asyncio
async def test_create_user_accounts_validates_outline_before_mailbox_creation(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_authentik_client", return_value=Mock()),
        patch.object(
            cog,
            "_outline_client",
            side_effect=ValueError("OUTLINE_API_KEY is not configured."),
        ),
        patch.object(cog, "_migadu_client") as migadu_client,
        patch.object(cog, "_audit_command_safe"),
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    migadu_client.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "OUTLINE_API_KEY is not configured" in message


@pytest.mark.asyncio
async def test_create_user_accounts_reports_partial_success_when_mailbox_crm_sync_fails(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@508.dev"}
    mock_espo_api.request.side_effect = EspoAPIError("crm update failed")

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client") as authentik_factory,
        patch.object(cog, "_outline_client") as outline_factory,
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    authentik_factory.return_value.find_users_by_username_or_email.assert_not_called()
    authentik_factory.return_value.create_user.assert_not_called()
    outline_factory.return_value.invite_user.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created the mailbox, but failed to update CRM" in message
    assert "Email: `jane@508.dev`" in message
    assert "SSO provisioning and Outline invite were not started" in message
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_success"] == "mailbox_created_crm_update_failed"


@pytest.mark.asyncio
async def test_create_user_accounts_rejects_migadu_address_mismatch(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "other@508.dev"}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client") as authentik_factory,
        patch.object(cog, "_outline_client") as outline_factory,
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    mock_espo_api.request.assert_not_called()
    authentik_factory.return_value.find_users_by_username_or_email.assert_not_called()
    authentik_factory.return_value.create_user.assert_not_called()
    outline_factory.return_value.invite_user.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "returned a different address" in message
    assert "Created mailbox: `other@508.dev`" in message
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_success"] == "mailbox_created_address_mismatch"


@pytest.mark.asyncio
async def test_create_user_accounts_reports_outline_invite_failure(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@508.dev"}
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
    outline_client = Mock()
    outline_client.invite_user.side_effect = OutlineAPIError("outline unavailable")

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        mock_espo_api.request.return_value = {"id": "crm-123"}
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Mailbox and SSO are ready, but the Outline invite failed" in message
    assert "outline unavailable" in message
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["stage"] == "outline"
    assert "outline unavailable" in audit_metadata["error"]


@pytest.mark.asyncio
async def test_create_user_accounts_reports_reconciled_sso_crm_partial_success(
    cog: CRMCog, mock_interaction: AsyncMock, mock_espo_api: Mock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }
    migadu_client = Mock()
    migadu_client.create_mailbox.return_value = {"address": "jane@508.dev"}
    reconciled_user = {
        "pk": 42,
        "username": "jane",
        "email": "jane@508.dev",
        "name": "Jane Doe",
        "is_superuser": False,
    }
    authentik_client = Mock()
    authentik_client.find_users_by_username_or_email.side_effect = [
        [],
        [reconciled_user],
    ]
    authentik_client.create_user.side_effect = AuthentikAPIError(
        "Authentik request failed with status 405: Method Not Allowed"
    )
    authentik_client.resolve_email_stage_id.return_value = "stage-id"
    authentik_client.status_code = 405
    outline_client = Mock()
    mock_espo_api.request.side_effect = [
        {"id": "crm-123"},
        EspoAPIError("crm sso update failed"),
    ]

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_migadu_client", return_value=migadu_client),
        patch.object(cog, "_authentik_client", return_value=authentik_client),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    outline_client.invite_user.assert_not_called()
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Created the mailbox and started SSO provisioning" in message
    assert "SSO user ID: `42`" in message
    assert "Outline invite was not sent" in message
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["partial_user_id"] == 42
    assert audit_metadata["partial_success"] == "sso_reconciled_crm_update_failed"


@pytest.mark.asyncio
async def test_create_user_accounts_partial_sso_without_user_id_omits_none_line(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "",
        "cSsoID": None,
    }

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(
            cog,
            "_execute_user_accounts_provisioning",
            new=AsyncMock(
                side_effect=SSOProvisioningPartialError(
                    "validation failed",
                    partial_user_id=None,
                    partial_success="sso_created_validation_failed",
                ),
            ),
        ),
    ):
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "SSO user ID: `None`" not in message
    assert "validation failed" in message


@pytest.mark.asyncio
async def test_create_user_accounts_shows_selection_view_for_multiple_contacts(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contacts = [
        {
            "id": "crm-123",
            "name": "Jane Doe",
            "emailAddress": "jane@example.com",
        },
        {
            "id": "crm-456",
            "name": "Jane Smith",
            "emailAddress": "smith@example.com",
        },
    ]

    with patch.object(
        cog,
        "_search_contacts_for_lookup",
        new=AsyncMock(return_value=contacts),
    ):
        sent_message = Mock()
        mock_interaction.followup.send = AsyncMock(return_value=sent_message)
        await cog.create_user_accounts.callback(
            cog,
            mock_interaction,
            search_term="jane",
            mailbox_username="jane",
        )

    kwargs = mock_interaction.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    view = kwargs["view"]
    assert isinstance(view, CreateUserAccountsSelectionView)
    labels = [item.label for item in view.children if hasattr(item, "label")]
    assert labels == ["Jane Doe", "Jane Smith"]


@pytest.mark.asyncio
async def test_invite_outline_user_invites_contact_508_email(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "jane@508.dev",
    }
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.invite_outline_user.callback(
            cog,
            mock_interaction,
            search_term="jane",
        )

    outline_client.invite_user.assert_called_once_with(
        email="jane@508.dev",
        name="Jane Doe",
        role="member",
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Outline invite sent" in message
    assert "Email: `jane@508.dev`" in message
    assert mock_interaction.followup.send.call_args.kwargs["ephemeral"] is True
    assert mock_audit.call_args.kwargs["metadata"]["contact_id"] == "crm-123"


@pytest.mark.asyncio
async def test_invite_outline_user_reports_outline_api_error(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contact = {
        "id": "crm-123",
        "name": "Jane Doe",
        "emailAddress": "jane.personal@example.com",
        "c508Email": "jane@508.dev",
    }
    outline_client = Mock()
    outline_client.invite_user.side_effect = OutlineAPIError("outline unavailable")

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[contact]),
        ),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.invite_outline_user.callback(
            cog,
            mock_interaction,
            search_term="jane",
        )

    message = mock_interaction.followup.send.call_args.args[0]
    assert "Outline invite failed" in message
    assert "outline unavailable" in message
    audit_metadata = mock_audit.call_args.kwargs["metadata"]
    assert audit_metadata["search_term"] == "jane"
    assert audit_metadata["error"] == "outline unavailable"


@pytest.mark.asyncio
async def test_invite_outline_user_invites_direct_email_when_no_contact_matches(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    outline_client = Mock()
    outline_client.invite_user.return_value = {"ok": True}

    with (
        patch.object(
            cog,
            "_search_contacts_for_lookup",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(cog, "_outline_client", return_value=outline_client),
        patch.object(cog, "_audit_command_safe") as mock_audit,
    ):
        await cog.invite_outline_user.callback(
            cog,
            mock_interaction,
            search_term="person@example.com",
        )

    outline_client.invite_user.assert_called_once_with(
        email="person@example.com",
        name="person",
        role="member",
    )
    message = mock_interaction.followup.send.call_args.args[0]
    assert "Email: `person@example.com`" in message
    assert mock_audit.call_args.kwargs["metadata"]["direct_email"] is True


@pytest.mark.asyncio
async def test_invite_outline_user_shows_selection_view_for_multiple_contacts(
    cog: CRMCog, mock_interaction: AsyncMock
) -> None:
    contacts = [
        {
            "id": "crm-123",
            "name": "Jane Doe",
            "emailAddress": "jane@example.com",
            "c508Email": "jane@508.dev",
        },
        {
            "id": "crm-456",
            "name": "Jane Smith",
            "emailAddress": "smith@example.com",
            "c508Email": "smith@508.dev",
        },
    ]

    with patch.object(
        cog,
        "_search_contacts_for_lookup",
        new=AsyncMock(return_value=contacts),
    ):
        sent_message = Mock()
        mock_interaction.followup.send = AsyncMock(return_value=sent_message)
        await cog.invite_outline_user.callback(
            cog,
            mock_interaction,
            search_term="jane",
        )

    kwargs = mock_interaction.followup.send.call_args.kwargs
    assert kwargs["ephemeral"] is True
    view = kwargs["view"]
    assert isinstance(view, OutlineInviteSelectionView)
    labels = [item.label for item in view.children if hasattr(item, "label")]
    assert labels == ["Jane Doe", "Jane Smith"]
