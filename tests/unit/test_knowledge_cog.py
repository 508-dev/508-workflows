"""Discord UX tests for knowledge capture and questions."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.cogs.agent import (
    AgentCog,
    AgentConfirmationView,
    KnowledgeCaptureDynamicButton,
    KnowledgeCaptureView,
    settings,
    setup as setup_agent_cog,
)


class _AsyncTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


def test_knowledge_intent_routing_is_narrow() -> None:
    assert AgentCog._is_knowledge_capture_request("remember this thread") is True
    assert AgentCog._is_knowledge_capture_request("remeber this thred") is True
    assert AgentCog._is_knowledge_capture_request("save this answr") is True
    assert (
        AgentCog._is_knowledge_question("I forgot, does our main website auto deploy?")
        is True
    )
    assert AgentCog._is_knowledge_question("create a task for the website") is False
    assert AgentCog._is_knowledge_question("what tasks are open?") is False
    assert AgentCog._is_knowledge_question("Can you list GitHub repositories?") is False
    assert (
        AgentCog._is_knowledge_capture_request("Do you remember this thread?") is False
    )
    assert AgentCog._is_knowledge_question("Do you remember this thread?") is True
    assert AgentCog._is_knowledge_question("What do you remember about me?") is False
    assert (
        AgentCog._is_knowledge_question("Could you remember that my timezone is UTC?")
        is False
    )
    assert (
        AgentCog._is_knowledge_capture_request(
            "suggest facts worth saving from this thread"
        )
        is True
    )


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


def test_long_knowledge_answer_keeps_at_least_one_citation() -> None:
    rendered = AgentCog._format_knowledge_query_response(
        {
            "status": "answered",
            "answer": "A" * 4000,
            "citations": [
                {
                    "source_type": "outline",
                    "title": "Deployment runbook",
                    "url": "https://outline.example/doc/1",
                }
            ],
        }
    )

    assert len(rendered) <= 1900
    assert "Sources:" in rendered
    assert "Deployment runbook" in rendered


def test_capture_preview_parts_include_every_candidate_before_confirmation() -> None:
    response = {
        "status": "requires_confirmation",
        "visibility": "org",
        "candidates": [
            {
                "question": f"Question {index}",
                "answer": "A" * 1400 + f" answer-tail-{index}",
            }
            for index in range(1, 4)
        ],
    }

    parts = AgentCog._format_knowledge_capture_preview_parts(response)
    rendered = "\n".join(parts)

    assert all(len(part) <= 1900 for part in parts)
    assert all(f"answer-tail-{index}" in rendered for index in range(1, 4))
    assert rendered.endswith(
        "Choose **Remember** to save exactly this preview, or cancel."
    )


def test_member_specific_view_overwrite_prevents_public_knowledge_reply() -> None:
    member_role = SimpleNamespace(id=1, name="Member", managed=False)
    guest = Mock()
    guest.id = 999
    channel = SimpleNamespace(
        overwrites={guest: SimpleNamespace(view_channel=True)},
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(roles=[member_role]),
        channel=channel,
    )

    assert AgentCog._discord_destination_is_org_only(message) is False


def test_bot_member_view_overwrite_does_not_make_channel_guest_visible() -> None:
    member_role = SimpleNamespace(id=1, name="Member", managed=False)
    bot_member = Mock()
    bot_member.id = 999
    channel = SimpleNamespace(
        overwrites={bot_member: SimpleNamespace(view_channel=True)},
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(roles=[member_role], me=bot_member),
        channel=channel,
    )

    assert AgentCog._discord_destination_is_org_only(message) is True


def test_managed_role_visibility_prevents_public_knowledge_reply() -> None:
    member_role = SimpleNamespace(id=1, name="Member", managed=False)
    booster_role = SimpleNamespace(id=2, name="Server Booster", managed=True)
    channel = SimpleNamespace(
        overwrites={},
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    message = SimpleNamespace(
        guild=SimpleNamespace(roles=[member_role, booster_role]),
        channel=channel,
    )

    assert AgentCog._discord_destination_is_org_only(message) is False


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
async def test_disabled_knowledge_does_not_collect_discord_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect_sources = AsyncMock()
    monkeypatch.setattr(settings, "knowledge_enabled", False)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.collect_discord_sources",
        collect_sources,
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace()
    cog._post_backend_json = Mock(
        return_value={
            "status": "denied",
            "answer": "Organizational knowledge answers are disabled.",
        }
    )

    response = await cog._post_knowledge_query(
        question="What happened?",
        context={"discord_user_id": "123"},
    )

    assert response["status"] == "denied"
    collect_sources.assert_not_awaited()
    assert "discord_sources" not in cog._post_backend_json.call_args.args[1]


@pytest.mark.asyncio
async def test_knowledge_confirmation_fails_closed_when_roles_cannot_refresh() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=None))
    cog._post_knowledge_confirmation = AsyncMock()
    cog._audit_command_safe = Mock()
    cog._format_knowledge_capture_response = Mock(return_value="Capture failed")
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
        message=SimpleNamespace(id=222, edit=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await view._finish(interaction, confirm=True)

    cog._post_knowledge_confirmation.assert_not_awaited()
    assert not view.is_finished()
    assert all(
        isinstance(item, KnowledgeCaptureDynamicButton) and not item.item.disabled
        for item in view.children
    )


@pytest.mark.asyncio
async def test_dynamic_capture_button_rehydrates_after_restart() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._build_agent_context = Mock(
        return_value={"discord_user_id": "123", "organization_id": None}
    )
    cog._guild_role_names = AsyncMock(return_value=["Member"])
    cog._post_knowledge_confirmation = AsyncMock(
        return_value={
            "status": "saved",
            "message": "Saved 1 remembered answer.",
            "http_status": 200,
        }
    )
    cog._audit_command_safe = Mock()
    cog._format_knowledge_capture_response = Mock(return_value="Saved")
    interaction = SimpleNamespace(
        id=999,
        client=SimpleNamespace(get_cog=Mock(return_value=cog)),
        user=SimpleNamespace(id=123, roles=[]),
        guild_id=None,
        channel_id=111,
        channel=SimpleNamespace(id=111),
        message=SimpleNamespace(id=222, edit=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    original = KnowledgeCaptureDynamicButton(
        action="c",
        draft_id="11111111-1111-1111-1111-111111111111",
        guild_id="456",
        channel_id="789",
    )
    match = original.template.fullmatch(original.custom_id)
    assert match is not None
    restored = await KnowledgeCaptureDynamicButton.from_custom_id(
        interaction,
        original.item,
        match,
    )

    await restored.callback(interaction)

    confirmation = cog._post_knowledge_confirmation.await_args.kwargs
    assert confirmation["draft_id"] == "11111111-1111-1111-1111-111111111111"
    assert confirmation["context"]["guild_id"] == "456"
    assert confirmation["context"]["roles"] == ["Member"]
    assert confirmation["confirm"] is True
    edited_view = interaction.message.edit.await_args.kwargs["view"]
    assert all(
        isinstance(item, KnowledgeCaptureDynamicButton) and item.item.disabled
        for item in edited_view.children
    )


@pytest.mark.asyncio
async def test_agent_setup_registers_restart_safe_capture_buttons() -> None:
    bot = SimpleNamespace(add_dynamic_items=Mock(), add_cog=AsyncMock())

    await setup_agent_cog(bot)

    bot.add_dynamic_items.assert_called_once_with(KnowledgeCaptureDynamicButton)
    bot.add_cog.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_view_uses_only_dynamic_items_to_avoid_double_dispatch() -> None:
    view = KnowledgeCaptureView(
        cog=AgentCog.__new__(AgentCog),
        requester_id=123,
        draft_id="11111111-1111-1111-1111-111111111111",
        context={"guild_id": "456", "channel_id": "789"},
    )

    assert len(view.children) == 2
    assert all(
        isinstance(item, KnowledgeCaptureDynamicButton) for item in view.children
    )
    view.stop()


@pytest.mark.asyncio
async def test_thread_capture_requires_requester_history_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyThread:
        def __init__(self) -> None:
            self.permissions_for = Mock(
                return_value=SimpleNamespace(
                    view_channel=True,
                    read_message_history=False,
                )
            )
            self.history = Mock()

    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.discord.Thread",
        DummyThread,
    )
    cog = AgentCog.__new__(AgentCog)
    channel = DummyThread()
    trigger = SimpleNamespace(channel=channel, author=SimpleNamespace(id=123))

    messages = await cog._knowledge_capture_messages(trigger)

    assert messages == []
    channel.permissions_for.assert_called_once_with(trigger.author)
    channel.history.assert_not_called()


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
        permissions_for=Mock(
            return_value=SimpleNamespace(
                view_channel=True,
                read_message_history=True,
            )
        ),
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
    member_role = SimpleNamespace(name="Member", managed=False)
    channel = SimpleNamespace(
        id=789,
        typing=Mock(return_value=_AsyncTyping()),
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> I forgot, does our main website auto deploy?",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456, roles=[member_role]),
        channel=channel,
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_knowledge_query.assert_awaited_once()
    cog._post_agent_request.assert_not_awaited()
    cog._send_mention_public_response.assert_awaited_once()
    author.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_safe_answer_is_dmed_when_guests_can_view_channel() -> None:
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
    member_role = SimpleNamespace(name="Member", managed=False)
    guest_role = SimpleNamespace(name="Guest", managed=False)
    channel = SimpleNamespace(
        id=789,
        typing=Mock(return_value=_AsyncTyping()),
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=True)),
    )
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> I forgot, does our main website auto deploy?",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456, roles=[member_role, guest_role]),
        channel=channel,
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._send_mention_public_response.assert_not_awaited()
    author.send.assert_awaited_once()
    message.reply.assert_awaited_once_with(
        "I sent the knowledge answer by DM.",
        mention_author=False,
    )


@pytest.mark.asyncio
async def test_knowledge_view_uses_draft_ttl_without_changing_agent_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "knowledge_capture_draft_ttl_seconds",
        777,
    )
    cog = AgentCog.__new__(AgentCog)

    knowledge_view = KnowledgeCaptureView(
        cog=cog,
        requester_id=123,
        draft_id="11111111-1111-1111-1111-111111111111",
        context={},
    )
    agent_view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="11111111-1111-1111-1111-111111111111",
        context={},
    )

    assert knowledge_view.timeout == 777
    assert agent_view.timeout == 600
    knowledge_view.stop()
    agent_view.stop()


@pytest.mark.asyncio
async def test_knowledge_confirmation_keeps_controls_after_backend_outage() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._build_agent_context = Mock(
        return_value={"discord_user_id": "123", "organization_id": None}
    )
    cog._guild_role_names = AsyncMock(return_value=["Project Manager"])
    cog._post_knowledge_confirmation = AsyncMock(
        return_value={
            "status": "failed",
            "message": "The knowledge service could not be reached. Try again.",
            "http_status": 500,
        }
    )
    cog._audit_command_safe = Mock()
    cog._format_knowledge_capture_response = Mock(return_value="Try again")
    view = KnowledgeCaptureView(
        cog=cog,
        requester_id=123,
        draft_id="11111111-1111-1111-1111-111111111111",
        context={
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
        },
    )
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=123, roles=[]),
        guild_id=None,
        channel_id=111,
        channel=SimpleNamespace(id=111),
        message=SimpleNamespace(id=222, edit=AsyncMock()),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await view._finish(interaction, confirm=True)

    cog._post_knowledge_confirmation.assert_awaited_once()
    assert not view.is_finished()
    assert all(
        isinstance(item, KnowledgeCaptureDynamicButton) and not item.item.disabled
        for item in view.children
    )
    interaction.message.edit.assert_not_awaited()
