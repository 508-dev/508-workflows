"""Discord evidence access checks run before reading message contents."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from five08.discord_bot.utils.knowledge_context import collect_discord_sources


def setup_source():
    settings = SimpleNamespace(
        knowledge_discord_channel_ids="100",
        discord_server_id="1",
        knowledge_source_timeout_seconds=2,
        knowledge_discord_history_limit=100,
        knowledge_discord_history_days=30,
    )
    member, bot_member = object(), object()
    channel = Mock(spec=discord.TextChannel)
    channel.id, channel.name = 100, "deployments"
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=True, read_message_history=True
    )
    message = SimpleNamespace(
        id=200,
        content="The website deploys using Cloudflare Pages.",
        created_at=datetime.now(timezone.utc),
        jump_url="https://discord.com/channels/1/100/200",
        author=SimpleNamespace(id=50, display_name="Alice", bot=False),
    )

    async def history(**_kwargs):
        yield message

    channel.history = Mock(side_effect=history)
    guild = SimpleNamespace(
        id=1,
        me=bot_member,
        fetch_member=AsyncMock(return_value=member),
        get_channel_or_thread=Mock(return_value=channel),
    )
    bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    return bot, settings, guild, channel, member


@pytest.mark.asyncio
async def test_current_member_permissions_checked_before_selected_channel_history():
    bot, settings, guild, channel, member = setup_source()
    sources, errors = await collect_discord_sources(
        bot, settings, {"guild_id": "1", "discord_user_id": "50"}, "website deploy"
    )
    guild.fetch_member.assert_awaited_once_with(50)
    channel.permissions_for.assert_any_call(member)
    assert sources[0]["source"]["channel_id"] == "100"
    assert len(sources[0]["messages"]) == 1
    assert errors == []
    channel.history.reset_mock()
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=False, read_message_history=True
    )
    sources, _ = await collect_discord_sources(
        bot, settings, {"guild_id": "1", "discord_user_id": "50"}, "website deploy"
    )
    assert sources == []
    channel.history.assert_not_called()


@pytest.mark.asyncio
async def test_empty_allowlist_or_wrong_guild_does_not_read_discord():
    bot, settings, guild, channel, _ = setup_source()
    settings.knowledge_discord_channel_ids = ""
    assert await collect_discord_sources(
        bot, settings, {"guild_id": "1", "discord_user_id": "50"}, "deploy"
    ) == ([], [])
    settings.knowledge_discord_channel_ids = "100"
    assert await collect_discord_sources(
        bot, settings, {"guild_id": "2", "discord_user_id": "50"}, "deploy"
    ) == ([], [])
    guild.fetch_member.assert_not_called()
    channel.history.assert_not_called()


@pytest.mark.asyncio
async def test_member_refresh_failure_does_not_fall_back_to_cached_roles():
    bot, settings, guild, channel, _ = setup_source()
    guild.fetch_member.side_effect = discord.NotFound(
        SimpleNamespace(status=404, reason="gone"), "gone"
    )
    sources, errors = await collect_discord_sources(
        bot, settings, {"guild_id": "1", "discord_user_id": "50"}, "deploy"
    )
    assert sources == []
    assert errors == ["discord_unavailable"]
    channel.history.assert_not_called()
