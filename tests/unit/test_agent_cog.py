"""Unit tests for Discord agent cog response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from five08.discord_bot.cogs.agent import AgentCog, AgentConfirmationView
from five08.tls import default_ca_bundle_path


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _AsyncTyping:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


def test_format_agent_response_renders_error_payload() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {"error": "plan_expired", "detail": "confirm again"},
    )

    assert "Agent status: error" in message
    assert "Error: plan_expired" in message
    assert "Detail: confirm again" in message


def test_format_agent_response_renders_tool_error() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "failed",
            "message": "One or more actions failed.",
            "results": [
                {
                    "tool_name": "task_write.update_task",
                    "status": "failed",
                    "error": "Task TASK-999 was not found",
                }
            ],
        }
    )

    assert "- task_write.update_task: failed (Task TASK-999 was not found)" in message


def test_format_agent_response_renders_generic_clarification_as_guidance() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "needs_clarification",
            "message": "I could not turn that into a supported task action.",
        }
    )

    assert message == (
        "I could not map that to a supported workflow yet. Ask "
        "`what can you do?` for examples."
    )


def test_format_agent_response_renders_github_issue_results() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "github_issue.search_issues",
                    "status": "succeeded",
                    "result": {
                        "issues": [
                            {
                                "number": 42,
                                "title": "Fix onboarding sync",
                                "html_url": "https://github.example/issues/42",
                            }
                        ]
                    },
                }
            ],
        }
    )

    assert "- github_issue.search_issues: 1 issues" in message
    assert "#42 Fix onboarding sync https://github.example/issues/42" in message


def test_format_agent_response_renders_created_github_issue() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "github_issue.create_issue",
                    "status": "succeeded",
                    "result": {
                        "number": 43,
                        "title": "Fix task sync",
                        "html_url": "https://github.example/issues/43",
                    },
                }
            ],
        }
    )

    assert "- github_issue.create_issue: 1 issues" in message
    assert "#43 Fix task sync https://github.example/issues/43" in message


def test_format_agent_response_renders_contact_results() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "crm_read.search_contacts",
                    "status": "succeeded",
                    "result": {
                        "contacts": [
                            {
                                "id": "contact-1",
                                "name": "Sarah Example",
                                "emailAddress": "sarah@example.com",
                            }
                        ]
                    },
                }
            ],
        }
    )

    assert "- crm_read.search_contacts: 1 contacts" in message
    assert "Sarah Example sarah@example.com contact-1" in message


def test_format_agent_response_renders_memory_facts() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "memory_read.get_user_facts",
                    "status": "succeeded",
                    "result": {
                        "facts": [
                            {
                                "key": "timezone",
                                "value_json": {
                                    "text": "my timezone is Asia/Taipei",
                                },
                            }
                        ]
                    },
                }
            ],
        }
    )

    assert "- memory_read.get_user_facts: 1 remembered facts" in message
    assert "  - timezone: my timezone is Asia/Taipei" in message


def test_format_agent_response_surfaces_sso_recovery_email_warning() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "sso_write.create_user",
                    "status": "succeeded",
                    "result": {
                        "user_id": 42,
                        "recovery_email_error": "stage unavailable",
                    },
                }
            ],
        }
    )

    assert "- sso_write.create_user: succeeded" in message
    assert "Recovery email failed: stage unavailable" in message


def test_format_agent_response_surfaces_nested_sso_recovery_email_warning() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "account_write.create_user_accounts",
                    "status": "succeeded",
                    "result": {
                        "email": "jane@508.dev",
                        "sso": {
                            "user_id": 42,
                            "recovery_email_error": "stage unavailable",
                        },
                    },
                }
            ],
        }
    )

    assert "- account_write.create_user_accounts: succeeded" in message
    assert "Recovery email failed: stage unavailable" in message


def test_audit_result_treats_error_payload_as_error() -> None:
    assert (
        AgentCog._audit_result_for_agent_response(
            {"error": "plan_not_found", "http_status": 404}
        )
        == "error"
    )


def test_audit_result_treats_forbidden_error_payload_as_denied() -> None:
    assert (
        AgentCog._audit_result_for_agent_response(
            {"error": "actor_mismatch", "http_status": 403}
        )
        == "denied"
    )


def test_audit_result_treats_http_error_payload_as_error() -> None:
    assert (
        AgentCog._audit_result_for_agent_response(
            {"detail": "Not Found", "http_status": 404}
        )
        == "error"
    )


def test_audit_result_treats_http_forbidden_payload_as_denied() -> None:
    assert (
        AgentCog._audit_result_for_agent_response(
            {"detail": "Forbidden", "http_status": 403}
        )
        == "denied"
    )


def test_audit_result_treats_clarification_as_success() -> None:
    assert (
        AgentCog._audit_result_for_agent_response(
            {"status": "needs_clarification", "http_status": 422}
        )
        == "success"
    )


def test_audit_result_treats_canceled_as_success() -> None:
    assert (
        AgentCog._audit_result_for_agent_response({"status": "canceled"}) == "success"
    )


@pytest.mark.asyncio
async def test_confirmation_view_disable_stops_listener() -> None:
    view = AgentConfirmationView(
        cog=AgentCog.__new__(AgentCog),
        requester_id=123,
        plan_id="plan-1",
        context={"discord_user_id": "123"},
    )

    view._disable()

    assert view.is_finished()


@pytest.mark.asyncio
async def test_confirmation_transport_failure_keeps_view_retryable() -> None:
    cog = SimpleNamespace(
        _post_agent_confirmation=AsyncMock(side_effect=RuntimeError("timeout")),
        _audit_command_safe=Mock(),
        _format_agent_response=Mock(return_value="Agent status: failed"),
    )
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={"discord_user_id": "123"},
    )
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
        user=SimpleNamespace(id=123),
    )

    await AgentConfirmationView.confirm(view, interaction, None)

    assert not view.is_finished()
    assert all(not item.disabled for item in view.children)
    interaction.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_transport_failure_keeps_view_retryable() -> None:
    cog = SimpleNamespace(
        _post_agent_confirmation=AsyncMock(side_effect=RuntimeError("timeout")),
        _audit_command_safe=Mock(),
        _format_agent_response=Mock(return_value="Agent status: failed"),
    )
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={"discord_user_id": "123"},
    )
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
        user=SimpleNamespace(id=123),
    )

    await AgentConfirmationView.cancel(view, interaction, None)

    assert not view.is_finished()
    assert all(not item.disabled for item in view.children)
    interaction.message.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_uses_fresh_button_context() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_confirmation = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_command_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: executed")
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "message_id": "original-message",
            "roles": ["Member"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=456,
        channel_id=789,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(id=321, edit=AsyncMock()),
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="@everyone"), SimpleNamespace(name="Admin")],
        ),
    )

    await AgentConfirmationView.confirm(view, interaction, None)

    context = cog._post_agent_confirmation.await_args.kwargs["context"]
    assert context["roles"] == ["@everyone", "Admin"]
    assert context["interaction_id"] == "999"
    assert context["channel_id"] == "789"
    assert context["message_id"] == "original-message"


@pytest.mark.asyncio
async def test_cancellation_uses_fresh_button_context() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_confirmation = AsyncMock(
        return_value={"status": "canceled", "message": "Canceled"}
    )
    cog._audit_command_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: canceled")
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "message_id": "original-message",
            "roles": ["Member"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=456,
        channel_id=789,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(id=321, edit=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentConfirmationView.cancel(view, interaction, None)

    context = cog._post_agent_confirmation.await_args.kwargs["context"]
    assert context["roles"] == []
    assert context["interaction_id"] == "999"
    assert context["message_id"] == "original-message"


def test_build_agent_context_separates_interaction_and_message_ids() -> None:
    cog = AgentCog.__new__(AgentCog)
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=123),
        guild_id=456,
        channel_id=789,
        message=SimpleNamespace(id=321),
    )

    context = cog._build_agent_context(interaction)

    assert context["interaction_id"] == "999"
    assert context["message_id"] == "321"


def test_build_agent_context_uses_thread_id_as_parent_message_id() -> None:
    cog = AgentCog.__new__(AgentCog)
    thread = object.__new__(discord.Thread)
    thread.id = 888
    thread.parent_id = 789
    interaction = SimpleNamespace(
        id=999,
        user=SimpleNamespace(id=123),
        guild_id=456,
        channel_id=789,
        channel=thread,
        message=SimpleNamespace(id=321),
    )

    context = cog._build_agent_context(interaction)

    assert context["thread_id"] == "888"
    assert context["parent_message_id"] == "888"


def test_extract_mention_request_strips_bot_mentions() -> None:
    assert (
        AgentCog._extract_mention_request(
            "<@123> create a task to update docs <@!123>",
            123,
        )
        == "create a task to update docs"
    )


def test_agent_command_is_registered_for_bot_dms() -> None:
    contexts = AgentCog.agent_command.allowed_contexts
    installs = AgentCog.agent_command.allowed_installs

    assert contexts is not None
    assert contexts.guild is True
    assert contexts.dm_channel is True
    assert contexts.private_channel is False
    assert installs is not None
    assert installs.guild is True
    assert installs.user is False


def test_build_agent_context_from_message_uses_thread_message_context() -> None:
    cog = AgentCog.__new__(AgentCog)
    message = SimpleNamespace(
        id=555,
        author=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="@everyone"), SimpleNamespace(name="Member")],
        ),
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789),
    )

    context = cog._build_agent_context_from_message(message)

    assert context["discord_user_id"] == "123"
    assert context["guild_id"] == "456"
    assert context["channel_id"] == "789"
    assert context["message_id"] == "555"
    assert context["response_destination_visibility"] == "private"
    assert context["roles"] == ["@everyone", "Member"]


def test_build_agent_context_from_thread_message_uses_thread_id_as_parent_message_id() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    thread = object.__new__(discord.Thread)
    thread.id = 888
    thread.parent_id = 789
    message = SimpleNamespace(
        id=555,
        author=SimpleNamespace(id=123, roles=[]),
        guild=SimpleNamespace(id=456),
        channel=thread,
    )

    context = cog._build_agent_context_from_message(message)

    assert context["thread_id"] == "888"
    assert context["parent_message_id"] == "888"


def test_build_agent_context_from_dm_uses_private_response_visibility() -> None:
    cog = AgentCog.__new__(AgentCog)
    message = SimpleNamespace(
        id=555,
        author=SimpleNamespace(id=123, roles=[]),
        guild=None,
        channel=SimpleNamespace(id=789),
    )

    context = cog._build_agent_context_from_message(message)

    assert context["guild_id"] is None
    assert context["response_destination_visibility"] == "private"


@pytest.mark.asyncio
async def test_agent_command_answers_presence_check_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(cog, interaction, "do you see this")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert "I can see this" in interaction.followup.send.await_args.args[0]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    cog._post_agent_request.assert_not_awaited()
    cog._audit_command_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_command_answers_help_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="Admin")],
        ),
    )

    await AgentCog.agent_command.callback(cog, interaction, "what can you do?")

    response = interaction.followup.send.await_args.args[0]
    assert "I can help with:" in response
    assert "GitHub issues:" in response
    assert "CRM:" in response
    assert "create 508 accounts" in response
    assert "Authentik SSO users" in response
    assert "Outline invites" in response
    assert "`/agent`" not in response
    cog._post_agent_request.assert_not_awaited()
    cog._audit_command_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_command_answers_acknowledgement_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(cog, interaction, "thanks")

    interaction.followup.send.assert_awaited_once_with("Got it.", ephemeral=True)
    cog._post_agent_request.assert_not_awaited()
    cog._audit_command_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_command_routes_unlinked_member_report_to_dedicated_command() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(
        cog,
        interaction,
        "can you look up people with no discord linked but are members",
    )

    response = interaction.followup.send.await_args.args[0]
    assert "/unlinked-discord-users" in response
    assert "dedicated report" in response
    assert "ephemeral" not in response
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    cog._post_agent_request.assert_not_awaited()
    cog._audit_command_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_command_routes_onboarding_people_report_to_dedicated_command() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(
        cog,
        interaction,
        "find me people in the onboarding queue",
    )

    response = interaction.followup.send.await_args.args[0]
    assert "/view-onboarding-queue" in response
    assert "dedicated queue view" in response
    assert "ephemeral" not in response
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    cog._post_agent_request.assert_not_awaited()
    cog._audit_command_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_command_member_info_lookup_reaches_gateway() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._post_agent_request = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        id=999,
        guild_id=456,
        channel_id=789,
        message=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="Admin")],
        ),
    )

    await AgentCog.agent_command.callback(cog, interaction, "Look up info on Caleb")

    cog._post_agent_request.assert_awaited_once()
    assert cog._post_agent_request.await_args.kwargs["message"] == (
        "Look up info on Caleb"
    )
    assert cog._post_agent_request.await_args.kwargs["context"]["roles"] == ["Admin"]
    response = interaction.followup.send.await_args.args[0]
    assert "Agent status: executed" in response
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    cog._audit_command_safe.assert_called_once()


@pytest.mark.asyncio
async def test_agent_command_in_dm_uses_configured_guild_member_context(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(discord_server_id="456"),
    )
    member = SimpleNamespace(
        id=123,
        roles=[SimpleNamespace(name="@everyone"), SimpleNamespace(name="Admin")],
    )
    guild = SimpleNamespace(id=456, get_member=Mock(return_value=member))
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=guild), guilds=[guild])
    cog._post_agent_request = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        id=999,
        guild=None,
        guild_id=None,
        channel_id=789,
        channel=SimpleNamespace(id=789),
        message=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(cog, interaction, "Look up info on Caleb")

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)
    cog.bot.get_guild.assert_called_once_with(456)
    cog._post_agent_request.assert_awaited_once()
    context = cog._post_agent_request.await_args.kwargs["context"]
    assert context["guild_id"] == "456"
    assert context["organization_id"] == "456"
    assert context["channel_id"] == "789"
    assert context["roles"] == ["@everyone", "Admin"]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is False
    cog._audit_command_safe.assert_called_once()


@pytest.mark.asyncio
async def test_agent_command_in_dm_refuses_user_outside_configured_guild(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(discord_server_id="456"),
    )
    guild = SimpleNamespace(
        id=456,
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(return_value=None),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=guild), guilds=[guild])
    cog._post_agent_request = AsyncMock()
    cog._audit_command_safe = Mock()
    interaction = SimpleNamespace(
        id=999,
        guild=None,
        guild_id=None,
        channel_id=789,
        channel=SimpleNamespace(id=789),
        message=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        user=SimpleNamespace(id=123, roles=[]),
    )

    await AgentCog.agent_command.callback(cog, interaction, "Look up info on Caleb")

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)
    cog._post_agent_request.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        "I can only run DM workflows for current members of the configured 508 server.",
        ephemeral=False,
    )
    cog._audit_command_safe.assert_called_once()
    assert cog._audit_command_safe.call_args.kwargs["action"] == "agent.request"
    assert cog._audit_command_safe.call_args.kwargs["result"] == "denied"


@pytest.mark.asyncio
async def test_confirmation_context_in_dm_uses_cached_original_guild_roles() -> None:
    member = SimpleNamespace(roles=[SimpleNamespace(name="Member")])
    guild = SimpleNamespace(get_member=Mock(return_value=member))
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
            "message_id": "555",
            "roles": ["Admin"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=None,
        channel_id=111,
        message=SimpleNamespace(id=222),
        user=SimpleNamespace(id=123),
    )

    context = await view._confirmation_context(interaction)

    assert context["organization_id"] == "456"
    assert context["guild_id"] == "456"
    assert context["channel_id"] == "789"
    assert context["roles"] == ["Member"]
    assert context["interaction_id"] == "999"
    assert context["message_id"] == "555"


@pytest.mark.asyncio
async def test_confirmation_context_in_dm_fetches_uncached_member_roles() -> None:
    member = SimpleNamespace(roles=[SimpleNamespace(name="Member")])
    guild = SimpleNamespace(
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(return_value=member),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
            "message_id": "555",
            "roles": ["Admin"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=None,
        channel_id=111,
        message=SimpleNamespace(id=222),
        user=SimpleNamespace(id=123),
    )

    context = await view._confirmation_context(interaction)

    assert context["organization_id"] == "456"
    assert context["guild_id"] == "456"
    assert context["roles"] == ["Member"]
    assert context["message_id"] == "555"
    guild.fetch_member.assert_awaited_once_with(123)


@pytest.mark.asyncio
async def test_confirmation_context_in_dm_preserves_original_roles_when_guild_missing() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=None))
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
            "message_id": "555",
            "roles": ["Admin", "Member"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=None,
        channel_id=111,
        message=SimpleNamespace(id=222),
        user=SimpleNamespace(id=123),
    )

    context = await view._confirmation_context(interaction)

    assert context["organization_id"] == "456"
    assert context["guild_id"] == "456"
    assert context["roles"] == ["Admin", "Member"]
    assert context["message_id"] == "555"


@pytest.mark.asyncio
async def test_confirmation_context_in_dm_clears_roles_when_member_left_guild() -> None:
    guild = SimpleNamespace(
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(return_value=None),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(get_guild=Mock(return_value=guild))
    view = AgentConfirmationView(
        cog=cog,
        requester_id=123,
        plan_id="plan-1",
        context={
            "discord_user_id": "123",
            "organization_id": "456",
            "guild_id": "456",
            "channel_id": "789",
            "message_id": "555",
            "roles": ["Admin", "Member"],
        },
    )
    interaction = SimpleNamespace(
        id=999,
        guild_id=None,
        channel_id=111,
        message=SimpleNamespace(id=222),
        user=SimpleNamespace(id=123),
    )

    context = await view._confirmation_context(interaction)

    assert context["organization_id"] == "456"
    assert context["guild_id"] == "456"
    assert context["roles"] == []
    assert context["message_id"] == "555"


def test_mention_rate_limit_prunes_expired_user_entries() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog._mention_request_timestamps = {
        111: [1.0],
        222: [2.0],
    }

    with patch("five08.discord_bot.cogs.agent.time.monotonic", return_value=1000.0):
        assert cog._mention_rate_limited(333) is False

    assert 111 not in cog._mention_request_timestamps
    assert 222 not in cog._mention_request_timestamps
    assert cog._mention_request_timestamps[333] == [1000.0]


@pytest.mark.asyncio
async def test_agent_mention_sends_agent_response_by_dm() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_message_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: executed")
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> show tasks for project Atlas",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_awaited_once()
    assert cog._post_agent_request.await_args.kwargs["message"] == (
        "show tasks for project Atlas"
    )
    assert (
        cog._post_agent_request.await_args.kwargs["context"][
            "response_destination_visibility"
        ]
        == "private"
    )
    author.send.assert_awaited_once_with("Agent status: executed")
    message.reply.assert_awaited_once_with(
        "I sent the agent response by DM.",
        mention_author=False,
    )
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_posts_clarification_in_thread() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock(
        return_value={
            "status": "needs_clarification",
            "message": "I could not turn that into a supported task action.",
        }
    )
    cog._audit_message_safe = Mock()
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> frobnicate the dashboard",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    author.send.assert_not_awaited()
    message.create_thread.assert_awaited_once()
    assert message.create_thread.await_args.kwargs["name"] == "Agent response"
    thread.send.assert_awaited_once()
    assert (
        "I could not map that to a supported workflow yet"
        in thread.send.await_args.args[0]
    )
    message.reply.assert_not_awaited()
    cog._audit_message_safe.assert_not_called()


def test_mention_thread_name_does_not_include_request_content() -> None:
    assert (
        AgentCog._mention_thread_name(
            "send member agreement to michael@example.com +1 415 555 1212"
        )
        == "Agent response"
    )


def test_is_agent_thread_requires_bot_owned_thread() -> None:
    bot_owned_thread = object.__new__(discord.Thread)
    bot_owned_thread.owner_id = 999
    user_owned_prefixed_thread = object.__new__(discord.Thread)
    user_owned_prefixed_thread.owner_id = 123
    user_owned_prefixed_thread.name = "agent: renamed by user"

    assert AgentCog._is_agent_thread(bot_owned_thread, 999) is True
    assert AgentCog._is_agent_thread(user_owned_prefixed_thread, 999) is False


@pytest.mark.asyncio
async def test_agent_mention_dms_sensitive_crm_clarification() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock(
        return_value={
            "status": "needs_clarification",
            "message": (
                "I found multiple CRM contacts for Sarah: Sarah Chen "
                "<sarah@example.com> (contact-1); Sarah Jones "
                "<sjones@example.com> (contact-2). Which one should I use?"
            ),
        }
    )
    cog._audit_message_safe = Mock()
    cog._format_agent_response = Mock(
        return_value=(
            "Agent status: needs_clarification\n\n"
            "I found multiple CRM contacts for Sarah: Sarah Chen "
            "<sarah@example.com> (contact-1); Sarah Jones "
            "<sjones@example.com> (contact-2). Which one should I use?"
        )
    )
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> send member agreement to Sarah",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    message.create_thread.assert_not_awaited()
    author.send.assert_awaited_once()
    assert "sarah@example.com" in author.send.await_args.args[0]
    message.reply.assert_awaited_once_with(
        "I sent the agent response by DM.",
        mention_author=False,
    )
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_answers_help_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> what kind of things can you do?",
        author=SimpleNamespace(
            id=123,
            bot=False,
            roles=[SimpleNamespace(name="Admin")],
        ),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    response = thread.send.await_args.args[0]
    assert "I can help with:" in response
    assert "GitHub issues:" in response
    assert "CRM:" in response
    assert "create 508 accounts" in response
    assert "Authentik SSO users" in response
    assert "Outline invites" in response
    assert "Tasks:" not in response
    assert "`/agent`" in response
    assert "/unlinked-discord-users" not in response
    assert "/view-onboarding-queue" not in response
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_help_is_role_aware() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> what can you do?",
        author=SimpleNamespace(
            id=123,
            bot=False,
            roles=[SimpleNamespace(name="Engineer")],
        ),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    response = thread.send.await_args.args[0]
    assert "GitHub issues:" in response
    assert "CRM:" not in response
    assert "mailboxes" not in response
    cog._post_agent_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_mention_answers_presence_check_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> do you see this",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "I can see this" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_answers_presence_typo_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> are you tehre",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "I can see this" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_answers_greeting_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> hello",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "I can see this" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_answers_acknowledgement_without_backend() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> thanks",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once_with("Got it.")
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_thread_reply_continues_without_mention() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._is_agent_thread = Mock(return_value=True)
    cog._mention_response_thread = AsyncMock(
        return_value=SimpleNamespace(send=AsyncMock())
    )
    cog._post_agent_request = AsyncMock(
        return_value={
            "status": "needs_clarification",
            "message": "Which project should I search?",
        }
    )
    cog._audit_message_safe = Mock()
    message = SimpleNamespace(
        id=555,
        content="show tasks",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_awaited_once()
    assert cog._post_agent_request.await_args.kwargs["message"] == "show tasks"
    cog._mention_response_thread.assert_awaited_once()
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_routes_unlinked_member_report_to_ephemeral_command() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> can you look up people with no discord linked but are members",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    author.send.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "/unlinked-discord-users" in thread.send.await_args.args[0]
    assert "ephemeral" in thread.send.await_args.args[0]
    message.reply.assert_not_awaited()
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_routes_onboarding_people_report_to_ephemeral_command() -> (
    None
):
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> find me people in the onboarding queue",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "/view-onboarding-queue" in thread.send.await_args.args[0]
    assert "ephemeral" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_routes_prospect_lookup_to_ephemeral_command() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> I want to find people that are prospects",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "/view-onboarding-queue" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_routes_self_info_lookup_to_search_members() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> look up information on me",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "/search-members query:me show_skills:true" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_routes_member_info_lookup_to_search_members() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    thread = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> look up info on Caleb",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        create_thread=AsyncMock(return_value=thread),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    thread.send.assert_awaited_once()
    assert "/search-members query:Caleb" in thread.send.await_args.args[0]
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_mention_still_audits_errors() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock(
        return_value={"status": "failed", "message": "Backend failed"}
    )
    cog._audit_message_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: failed")
    author = SimpleNamespace(id=123, bot=False, roles=[], send=AsyncMock())
    message = SimpleNamespace(
        id=555,
        content="<@999> show tasks for project Atlas",
        author=author,
        mentions=[SimpleNamespace(id=999)],
        guild=SimpleNamespace(id=456),
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._audit_message_safe.assert_called_once()
    assert cog._audit_message_safe.call_args.kwargs["action"] == "agent.mention"
    assert cog._audit_message_safe.call_args.kwargs["result"] == "error"


@pytest.mark.asyncio
async def test_agent_dm_sends_agent_response_to_current_guild_member(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(discord_server_id="456"),
    )
    member = SimpleNamespace(
        id=123,
        roles=[SimpleNamespace(name="@everyone"), SimpleNamespace(name="Member")],
    )
    guild = SimpleNamespace(
        id=456,
        get_member=Mock(return_value=member),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_guild=Mock(return_value=guild),
        guilds=[guild],
    )
    cog._post_agent_request = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_message_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: executed")
    message = SimpleNamespace(
        id=555,
        content="show tasks for project Atlas",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[],
        guild=None,
        channel=SimpleNamespace(id=789, typing=Mock(return_value=_AsyncTyping())),
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog.bot.get_guild.assert_called_once_with(456)
    cog._post_agent_request.assert_awaited_once()
    assert cog._post_agent_request.await_args.kwargs["message"] == (
        "show tasks for project Atlas"
    )
    context = cog._post_agent_request.await_args.kwargs["context"]
    assert context["guild_id"] == "456"
    assert context["organization_id"] == "456"
    assert context["roles"] == ["@everyone", "Member"]
    message.reply.assert_awaited_once_with(
        "Agent status: executed",
        mention_author=False,
    )
    cog._audit_message_safe.assert_not_called()


@pytest.mark.asyncio
async def test_agent_dm_refuses_user_who_is_not_in_configured_guild(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(discord_server_id="456"),
    )
    guild = SimpleNamespace(
        id=456,
        get_member=Mock(return_value=None),
        fetch_member=AsyncMock(return_value=None),
    )
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(
        user=SimpleNamespace(id=999),
        get_guild=Mock(return_value=guild),
        guilds=[guild],
    )
    cog._post_agent_request = AsyncMock()
    cog._audit_message_safe = Mock()
    message = SimpleNamespace(
        id=555,
        content="show tasks for project Atlas",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
        mentions=[],
        guild=None,
        reply=AsyncMock(),
    )

    await cog.agent_mention(message)

    cog._post_agent_request.assert_not_awaited()
    message.reply.assert_awaited_once_with(
        "I can only run DM workflows for current members of the configured 508 server.",
        mention_author=False,
    )
    cog._audit_message_safe.assert_called_once()
    assert cog._audit_message_safe.call_args.kwargs["action"] == "agent.dm"
    assert cog._audit_message_safe.call_args.kwargs["result"] == "denied"


def test_post_backend_json_returns_structured_failed_response(
    monkeypatch,
) -> None:
    cog = AgentCog.__new__(AgentCog)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(
            backend_api_base_url="http://api.test",
            api_shared_secret="secret",
            agent_api_timeout_seconds=8.0,
        ),
    )

    with patch("five08.discord_bot.cogs.agent.requests.post") as mock_post:
        mock_post.return_value = _FakeResponse(
            500,
            {"status": "failed", "message": "tool failed"},
        )

        payload = cog._post_backend_json("/agent/confirmations/plan-1", {})

    assert payload["status"] == "failed"
    assert payload["http_status"] == 500
    assert mock_post.call_args.kwargs["verify"] == default_ca_bundle_path()


def test_post_backend_json_returns_detail_error_response(
    monkeypatch,
) -> None:
    cog = AgentCog.__new__(AgentCog)
    monkeypatch.setattr(
        "five08.discord_bot.cogs.agent.settings",
        SimpleNamespace(
            backend_api_base_url="http://api.test",
            api_shared_secret="secret",
            agent_api_timeout_seconds=8.0,
        ),
    )

    with patch("five08.discord_bot.cogs.agent.requests.post") as mock_post:
        mock_post.return_value = _FakeResponse(
            500,
            {"detail": "backend unavailable"},
        )

        payload = cog._post_backend_json("/agent/confirmations/plan-1", {})

    assert payload["detail"] == "backend unavailable"
    assert payload["http_status"] == 500
