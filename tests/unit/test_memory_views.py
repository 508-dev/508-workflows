"""Memory controls preserve owner identity and confirmation gates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from five08.discord_bot.utils.memory_views import MemoryFactsView
from five08.discord_bot.cogs.agent import AgentConfirmationView


@pytest.mark.asyncio
async def test_memory_controls_deny_other_users_and_prepare_confirmed_changes():
    cog = SimpleNamespace(
        _guild_role_names=AsyncMock(return_value=["Member"]),
        _post_agent_request=AsyncMock(
            return_value={
                "status": "requires_confirmation",
                "plan": {"plan_id": "plan-1"},
            }
        ),
        _format_agent_response=Mock(return_value="Review replacement"),
    )
    view = MemoryFactsView(
        cog=cog,
        requester_id=123,
        context={"guild_id": "456", "discord_user_id": "123", "roles": ["Admin"]},
        facts=[
            {"id": "fact-12345678", "key": "timezone", "value_json": {"text": "UTC"}}
        ],
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=999),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    assert await view.interaction_check(interaction) is False
    cog._post_agent_request.assert_not_called()
    interaction.user.id = 123
    assert await view.interaction_check(interaction) is True
    await view.propose(interaction, "Forget memory fact fact-12345678")
    assert cog._post_agent_request.await_args.kwargs["context"]["roles"] == ["Member"]
    assert isinstance(
        interaction.followup.send.await_args.kwargs["view"], AgentConfirmationView
    )
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_memory_controls_do_not_prepare_changes_when_roles_cannot_refresh():
    cog = SimpleNamespace(
        _guild_role_names=AsyncMock(return_value=None),
        _post_agent_request=AsyncMock(),
    )
    view = MemoryFactsView(
        cog=cog,
        requester_id=123,
        context={"guild_id": "456", "discord_user_id": "123", "roles": ["Admin"]},
        facts=[
            {"id": "fact-12345678", "key": "timezone", "value_json": {"text": "UTC"}}
        ],
    )
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await view.propose(interaction, "Forget memory fact fact-12345678")

    cog._post_agent_request.assert_not_awaited()
    assert interaction.followup.send.await_args.args[0] == (
        "I couldn't prepare the memory change. Try again."
    )
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
