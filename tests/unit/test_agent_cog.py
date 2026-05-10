"""Unit tests for Discord agent cog response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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


def test_format_agent_response_renders_kimai_results() -> None:
    cog = AgentCog.__new__(AgentCog)

    message = cog._format_agent_response(
        {
            "status": "executed",
            "results": [
                {
                    "tool_name": "kimai_read.project_hours",
                    "status": "succeeded",
                    "result": {
                        "project": {"name": "Atlas"},
                        "total_hours": 12.5,
                        "total_billed": 1250.0,
                        "user_hours": {"Sarah": {"hours": 7.5}},
                    },
                }
            ],
        }
    )

    assert "- kimai_read.project_hours: Atlas 12.5h, $1,250.00" in message
    assert "Sarah: 7.5h" in message


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


def test_extract_mention_request_strips_bot_mentions() -> None:
    assert (
        AgentCog._extract_mention_request(
            "<@123> create a task to update docs <@!123>",
            123,
        )
        == "create a task to update docs"
    )


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
    assert context["roles"] == ["@everyone", "Member"]


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
async def test_agent_mention_posts_agent_response() -> None:
    cog = AgentCog.__new__(AgentCog)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=999))
    cog._post_agent_request = AsyncMock(
        return_value={"status": "executed", "message": "Done"}
    )
    cog._audit_message_safe = Mock()
    cog._format_agent_response = Mock(return_value="Agent status: executed")
    message = SimpleNamespace(
        id=555,
        content="<@999> show tasks for project Atlas",
        author=SimpleNamespace(id=123, bot=False, roles=[]),
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
    message.reply.assert_awaited_once_with(
        "Agent status: executed",
        mention_author=False,
    )


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
