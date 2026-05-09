"""Unit tests for Discord agent cog response handling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from five08.discord_bot.cogs.agent import AgentCog, AgentConfirmationView


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


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
