"""Collect bounded Discord evidence after checking the requester's current access."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import discord


async def collect_thread_context(message: discord.Message) -> list[dict[str, Any]]:
    """Collect recent task discussion only from the current accessible thread."""
    channel = message.channel
    if not isinstance(channel, discord.Thread) or not isinstance(
        message.author, discord.Member
    ):
        return []
    permissions = channel.permissions_for(message.author)
    if not permissions.view_channel or not permissions.read_message_history:
        return []
    snippets = []
    try:
        async with asyncio.timeout(2):
            async for previous in channel.history(
                limit=20, before=message, oldest_first=False
            ):
                if not previous.content.strip():
                    continue
                snippets.append(
                    {
                        "source_type": "discord_message",
                        "source_ref": previous.jump_url,
                        "label": f"Thread message from {previous.author.id}",
                        "text": previous.content[:2048],
                        "channel_id": str(channel.id),
                        "thread_id": str(channel.id),
                        "message_id": str(previous.id),
                        "author_id": str(previous.author.id),
                        "created_at": previous.created_at.isoformat(),
                    }
                )
    except (TimeoutError, discord.HTTPException):
        return []
    return snippets


async def collect_discord_sources(
    bot: Any, settings: Any, context: dict[str, Any], question: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read only allowlisted locations, never arbitrary model-selected channels."""
    channel_ids = [
        value.strip()
        for value in settings.knowledge_discord_channel_ids.split(",")
        if value.strip().isdigit()
    ][:8]
    if not channel_ids or context.get("guild_id") != str(settings.discord_server_id):
        return [], []
    guild = bot.get_guild(int(context["guild_id"]))
    if guild is None:
        return [], ["discord_unavailable"]
    sources: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        async with asyncio.timeout(settings.knowledge_source_timeout_seconds):
            # Refresh the member instead of relying on stale cached role membership.
            member = await guild.fetch_member(int(context["discord_user_id"]))
            for channel_id in dict.fromkeys(channel_ids):
                channel = guild.get_channel_or_thread(int(channel_id))
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    continue
                # Private-thread membership is a separate ACL; do not infer it from
                # parent channel permissions.
                if isinstance(channel, discord.Thread) and channel.is_private():
                    continue
                if guild.me is None:
                    continue
                permissions = [
                    channel.permissions_for(actor) for actor in (member, guild.me)
                ]
                if not all(
                    p.view_channel and p.read_message_history for p in permissions
                ):
                    continue
                messages = []
                try:
                    async for message in channel.history(
                        limit=settings.knowledge_discord_history_limit,
                        after=datetime.now(timezone.utc)
                        - timedelta(days=settings.knowledge_discord_history_days),
                        oldest_first=False,
                    ):
                        if message.author.bot or not message.content.strip():
                            continue
                        messages.append(
                            {
                                "message_id": str(message.id),
                                "author_id": str(message.author.id),
                                "author_name": message.author.display_name[:128],
                                "content": message.content[:2000],
                                "created_at": message.created_at.isoformat(),
                                "jump_url": message.jump_url,
                            }
                        )
                except discord.HTTPException:
                    errors = ["discord_unavailable"]
                    continue
                if messages:
                    sources.append(
                        {
                            "source": {
                                "source_type": "discord_thread"
                                if isinstance(channel, discord.Thread)
                                else "discord_message",
                                "source_ref": f"https://discord.com/channels/{guild.id}/{channel.id}",
                                "title": channel.name[:256],
                                "guild_id": str(guild.id),
                                "channel_id": str(channel.id),
                                "thread_id": str(channel.id)
                                if isinstance(channel, discord.Thread)
                                else None,
                                "source_visibility": "private",
                            },
                            "messages": messages,
                        }
                    )
    except TimeoutError:
        errors = ["discord_timeout"]
    except discord.HTTPException:
        errors = ["discord_unavailable"]
    # Select relevant messages before transport, keeping a hard total text bound.
    # The backend independently ranks and bounds the gateway-provided evidence.
    from five08.knowledge.store import _fuzzy_relevance, _search_tokens

    tokens = _search_tokens(question)
    ranked = sorted(
        [(batch, message) for batch in sources for message in batch["messages"]],
        key=lambda item: max(
            len(tokens & _search_tokens(item[1]["content"])) / max(len(tokens), 1),
            _fuzzy_relevance(question, item[1]["content"]),
        ),
        reverse=True,
    )
    selected: dict[str, dict[str, Any]] = {}
    remaining = 20_000
    for batch, message in ranked:
        if remaining <= 0:
            break
        message["content"] = message["content"][:remaining]
        remaining -= len(message["content"])
        channel_id = batch["source"]["channel_id"]
        selected.setdefault(channel_id, {"source": batch["source"], "messages": []})[
            "messages"
        ].append(message)
    return list(selected.values()), errors
