"""Discord UX tests for knowledge capture and questions."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.cogs.agent import AgentCog, KnowledgeCaptureView


class _AsyncTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


def test_knowledge_intent_routing_is_narrow() -> None:
    assert AgentCog._is_knowledge_capture_request("remember this thread") is True
    assert (
        AgentCog._is_knowledge_question("I forgot, does our main website auto deploy?")
        is True
    )
    assert AgentCog._is_knowledge_question("create a task for the website") is False
    assert AgentCog._is_knowledge_question("what tasks are open?") is False


def test_knowledge_answer_format_escapes_mentions_and_renders_citations() -> None:
    rendered = AgentCog._format_knowledge_query_response(
        {
            "status": "answered",
            "answer": "@everyone **deploys** automatically",
            "citations": [
                {
                    "source_type": "memory",
                    "title": "Website *deployment*",
                    "url": "https://discord.example/message/101",
                }
            ],
        }
    )

    assert "@everyone" not in rendered
    assert "\\*\\*deploys\\*\\*" in rendered
    assert "Website \\*deployment\\*" in rendered
    assert "<https://discord.example/message/101>" in rendered


@pytest.mark.asyncio
async def test_ask_command_returns_a_private_grounded_answer() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_knowledge_query = AsyncMock(
        return_value={
            "status": "answered",
            "answer": "It deploys with Cloudflare Pages.",
            "citations": [],
            "public_safe": True,
        }
    )
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=123, roles=[SimpleNamespace(name="Member")]),
        guild_id=456,
        channel_id=789,
        channel=SimpleNamespace(id=789),
        message=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await AgentCog.ask_command.callback(
        cog,
        interaction,
        "Does the website auto deploy?",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    cog._post_knowledge_query.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    assert "Cloudflare Pages" in interaction.followup.send.await_args.args[0]


@pytest.mark.asyncio
async def test_knowledge_confirmation_fails_closed_when_roles_cannot_refresh() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=None))
    view = KnowledgeCaptureView(
        cog=cog,
        requester_id=123,
        draft_id="11111111-1111-1111-1111-111111111111",
        context={
            "discord_user_id": "123",
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
            "roles": ["Admin"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=123, roles=[]),
        guild_id=None,
        channel_id=111,
        channel=SimpleNamespace(id=111),
        message=SimpleNamespace(id=222),
    )

    context = await view._confirmation_context(interaction)

    assert context["guild_id"] == "456"
    assert context["roles"] == []


@pytest.mark.asyncio
async def test_capture_mention_uses_replied_question_and_answer() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._mention_request_timestamps = {}
    cog._post_agent_request = AsyncMock()
    cog._post_knowledge_capture = AsyncMock(
        return_value={
            "status": "requires_confirmation",
            "message": "Review and confirm.",
            "draft_id": "11111111-1111-1111-1111-111111111111",
            "visibility": "org",
            "candidates": [
                {
                    "question": "Does the website auto deploy?",
                    "answer": "Yes, using Cloudflare Pages.",
                }
            ],
        }
    )
    cog._audit_message_safe = Mock()
    now = datetime.now(timezone.utc)
    member_role = SimpleNamespace(name="Member")
    channel = SimpleNamespace(
        id=789,
        name="general",
        typing=Mock(return_value=_AsyncTyping()),
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    guild = SimpleNamespace(id=456, roles=[member_role])
    question = SimpleNamespace(
        id=100,
        content="Does the website auto deploy?",
        author=SimpleNamespace(id=10, name="Caleb", bot=False),
        created_at=now,
        jump_url="https://discord.example/message/100",
        channel=channel,
        reference=None,
    )
    answer = SimpleNamespace(
        id=101,
        content="Yes, using Cloudflare Pages.",
        author=SimpleNamespace(id=11, name="Michael", bot=False),
        created_at=now,
        jump_url="https://discord.example/message/101",
        channel=channel,
        reference=SimpleNamespace(resolved=question, message_id=100),
    )
    author = SimpleNamespace(
        id=123,
        bot=False,
        roles=[member_role],
        send=AsyncMock(),
    )
    trigger = SimpleNamespace(
        id=102,
        content="<@999> remember this answer",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=guild,
        channel=channel,
        reference=SimpleNamespace(resolved=answer, message_id=101),
        jump_url="https://discord.example/message/102",
        reply=AsyncMock(),
    )

    await cog.agent_mention(trigger)

    cog._post_agent_request.assert_not_awaited()
    payload = cog._post_knowledge_capture.await_args.args[0]
    assert [item["message_id"] for item in payload["messages"]] == ["100", "101"]
    assert payload["source"]["source_visibility"] == "org"
    assert isinstance(author.send.await_args.kwargs["view"], KnowledgeCaptureView)
    trigger.reply.assert_awaited_once_with(
        "I sent a capture preview by DM. Nothing is saved until you confirm.",
        mention_author=False,
    )


@pytest.mark.asyncio
async def test_public_safe_knowledge_question_can_reply_in_channel() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._mention_request_timestamps = {}
    cog._post_agent_request = AsyncMock()
    cog._post_knowledge_query = AsyncMock(
        return_value={
            "status": "answered",
            "answer": "Yes, with Cloudflare Pages.",
            "citations": [],
            "public_safe": True,
        }
    )
    cog._send_mention_public_response = AsyncMock()
    cog._audit_message_safe = Mock()
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> I forgot, does our main website auto deploy?",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_knowledge_query.assert_awaited_once()
    cog._post_agent_request.assert_not_awaited()
    cog._send_mention_public_response.assert_awaited_once()
    author.send.assert_not_awaited()
