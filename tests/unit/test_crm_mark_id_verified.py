"""Tests for the mark-id-verified command."""

from datetime import date

from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.crm import (
    CRMCog,
    ID_VERIFIED_FIELD,
    ID_VERIFIED_AT_FIELD,
    ID_VERIFIED_BY_FIELD,
    MarkIdVerifiedSelectionView,
)


class TestMarkIdVerifiedCommand:
    """Unit tests for mark-id-verified flow."""

    @pytest.fixture
    def mock_bot(self):
        bot = Mock()
        bot.get_cog = Mock()
        return bot

    @pytest.fixture
    def mock_espo_api(self):
        with patch("five08.discord_bot.cogs.crm.EspoAPI") as mock_api_class:
            mock_api = Mock()
            mock_api_class.return_value = mock_api
            yield mock_api

    @pytest.fixture
    def crm_cog(self, mock_bot, mock_espo_api):
        cog = CRMCog(mock_bot)
        cog.espo_api = mock_espo_api
        return cog

    @pytest.fixture
    def mock_interaction(self):
        interaction = Mock()
        interaction.response = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.guild = None
        interaction.user = Mock()
        interaction.user.name = "admin_user"
        interaction.user.id = 123
        return interaction

    @pytest.fixture
    def admin_member(self):
        member = Mock()
        member.id = 999
        member.name = "Caleb Rogers"
        return member

    @pytest.mark.asyncio
    async def test_parse_verified_at_variants(self, crm_cog):
        assert await crm_cog._parse_verified_at(None) == date.today().isoformat()
        assert await crm_cog._parse_verified_at("March 5, 2026") == "2026-03-05"
        assert await crm_cog._parse_verified_at("4/5/2026") == "2026-05-04"

    @pytest.mark.asyncio
    async def test_parse_verified_at_invalid_format(self, crm_cog):
        with pytest.raises(ValueError, match="Invalid verified_at format"):
            await crm_cog._parse_verified_at("not-a-date")

    @pytest.mark.asyncio
    async def test_resolve_verified_by_from_discord_mention(
        self,
        crm_cog,
        mock_interaction,
        admin_member,
    ):
        mock_interaction.guild = Mock()
        mock_interaction.guild.get_member.return_value = admin_member
        crm_cog._find_contact_by_discord_id = AsyncMock(
            return_value={"c508Email": "caleb@508.dev", "id": "c1"}
        )

        resolved = await crm_cog._resolve_verified_by(mock_interaction, "<@999>")

        assert resolved == "caleb"

    @pytest.mark.asyncio
    async def test_resolve_verified_by_from_invoker_via_discord_id(
        self,
        crm_cog,
        mock_interaction,
    ):
        mock_interaction.user.id = 123
        crm_cog._find_contact_by_discord_id = AsyncMock(
            return_value={"c508Email": "admin_user@508.dev", "id": "admin-contact"}
        )

        resolved = await crm_cog._resolve_verified_by(mock_interaction, "")

        assert resolved == "admin_user"
        crm_cog._find_contact_by_discord_id.assert_awaited_once_with("123")

    @pytest.mark.asyncio
    async def test_resolve_verified_by_from_invoker_via_discord_username_fallback(
        self,
        crm_cog,
        mock_interaction,
    ):
        mock_interaction.user.name = "Admin User"
        crm_cog._find_contact_by_discord_id = AsyncMock(return_value=None)
        crm_cog._find_contact_by_discord_username = AsyncMock(
            return_value={"c508Email": "admin_user@508.dev", "id": "admin-contact"}
        )

        resolved = await crm_cog._resolve_verified_by(mock_interaction, "")

        assert resolved == "admin_user"
        crm_cog._find_contact_by_discord_username.assert_awaited_once_with("admin user")

    @pytest.mark.asyncio
    async def test_mark_id_verified_single_contact_updates_id_fields(
        self,
        crm_cog,
        mock_interaction,
    ):
        contact = {
            "id": "contact-123",
            "name": "Caleb",
            "c508Email": "caleb@508.dev",
        }
        crm_cog._search_contacts_for_mark_id_verification = AsyncMock(
            return_value=[contact]
        )
        crm_cog.espo_api.request.return_value = {"id": "contact-123"}

        await crm_cog.mark_id_verified.callback(
            crm_cog,
            mock_interaction,
            "caleb",
            "caleb",
            "2026-02-26",
        )

        crm_cog.espo_api.request.assert_called_once_with(
            "PUT",
            "Contact/contact-123",
            {
                ID_VERIFIED_FIELD: True,
                ID_VERIFIED_AT_FIELD: "2026-02-26",
                ID_VERIFIED_BY_FIELD: "caleb",
            },
        )
        args, kwargs = mock_interaction.followup.send.call_args
        assert "embed" in kwargs
        assert "ID Verified" in kwargs["embed"].title

    @pytest.mark.asyncio
    async def test_mark_id_verified_multiple_contacts_shows_selector(
        self,
        crm_cog,
        mock_interaction,
    ):
        crm_cog._search_contacts_for_mark_id_verification = AsyncMock(
            return_value=[
                {"id": "c1", "name": "Caleb", "c508Email": "caleb@508.dev"},
                {"id": "c2", "name": "Caleb B", "c508Email": "calebb@508.dev"},
            ]
        )

        await crm_cog.mark_id_verified.callback(
            crm_cog,
            mock_interaction,
            "caleb",
            "Caleb",
            "2026-02-26",
        )

        crm_cog.espo_api.request.assert_not_called()
        args, kwargs = mock_interaction.followup.send.call_args
        assert "view" in kwargs
        assert isinstance(kwargs["view"], MarkIdVerifiedSelectionView)
        assert kwargs["embed"].title == "🔍 Multiple Contacts Found"

    @pytest.mark.asyncio
    async def test_mark_id_verified_invalid_date_sends_message(
        self,
        crm_cog,
        mock_interaction,
    ):
        await crm_cog.mark_id_verified.callback(
            crm_cog,
            mock_interaction,
            "caleb",
            verified_by="caleb",
            verified_at="bogus-date",
        )

        assert mock_interaction.followup.send.call_args.args[0].startswith("❌ Invalid")
        crm_cog.espo_api.request.assert_not_called()
