"""Unit tests for bot internal automation routes."""

from types import SimpleNamespace
import json
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.utils.internal_api import (
    InternalAPIRoutes,
    MemberAgreementRoleRequest,
)


class TestInternalAPIRoutes:
    """Unit tests for internal bot API route handlers."""

    @pytest.fixture
    def mock_bot(self):
        bot = Mock()
        bot.guilds = [Mock(), Mock()]
        return bot

    @pytest.fixture
    def internal_api_routes(self, mock_bot):
        return InternalAPIRoutes(mock_bot)

    @pytest.mark.asyncio
    async def test_grant_member_role_applies_member_role(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Signed linked users should receive the Member role."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        member_role = Mock()
        member_role.name = "Member"

        bot_top_role = Mock()
        bot_top_role.__gt__ = Mock(return_value=True)

        target_member = Mock()
        target_member.roles = []
        target_member.add_roles = AsyncMock()
        target_member.top_role = Mock()

        guild = Mock()
        guild.id = 123
        guild.roles = [member_role]
        guild.get_member.return_value = target_member
        guild.fetch_member = AsyncMock(return_value=target_member)
        guild.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            top_role=bot_top_role,
        )
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(
            discord_user_id="456",
            contact_id="contact-1",
            submission_id=4200,
        )

        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 200
        assert result["status"] == "applied"
        target_member.add_roles.assert_awaited_once_with(
            member_role,
            reason="Member agreement signed via Docuseal (contact contact-1, submission 4200)",
        )

    @pytest.mark.asyncio
    async def test_grant_member_role_returns_already_present(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Users who already have Member should not be modified."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            "123",
        )

        member_role = Mock()
        member_role.name = "Member"

        target_member = Mock()
        target_member.roles = [member_role]

        guild = Mock()
        guild.id = 123
        guild.roles = [member_role]
        guild.get_member.return_value = target_member
        internal_api_routes.bot.get_guild.return_value = guild

        payload = MemberAgreementRoleRequest(discord_user_id="456")
        result, status_code = await internal_api_routes._grant_member_role(payload)

        assert status_code == 200
        assert result["status"] == "already_present"

    @pytest.mark.asyncio
    async def test_member_agreement_role_handler_rejects_unauthorized(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Internal role grant endpoint should require the shared API secret."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.api_shared_secret",
            "top-secret",
        )
        request = Mock()
        request.headers = {"X-API-Secret": "wrong"}

        response = await internal_api_routes.member_agreement_role_handler(request)

        assert response.status == 401
        assert json.loads(response.body.decode("utf-8")) == {"error": "unauthorized"}

    def test_resolve_target_guild_uses_only_connected_guild_when_unconfigured(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Without a configured guild id, one connected guild is unambiguous."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            None,
        )
        only_guild = Mock()
        internal_api_routes.bot.guilds = [only_guild]

        assert internal_api_routes._resolve_target_guild() is only_guild

    def test_resolve_target_guild_returns_none_when_unconfigured_and_ambiguous(
        self, internal_api_routes, monkeypatch: pytest.MonkeyPatch
    ):
        """Without a configured guild id, multiple connected guilds should fail closed."""
        monkeypatch.setattr(
            "five08.discord_bot.utils.internal_api.settings.discord_server_id",
            None,
        )
        internal_api_routes.bot.guilds = [Mock(), Mock()]

        assert internal_api_routes._resolve_target_guild() is None
