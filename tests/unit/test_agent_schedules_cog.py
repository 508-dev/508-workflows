"""Unit tests for Discord recurring-schedule control messages."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

import five08.discord_bot.cogs.schedules as schedules_module
from five08.discord_bot.cogs.schedules import AgentSchedulesCog


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"status": "queued", "run": {"id": "run-1"}, "job_id": "job-1"},
            "Queued schedule run `run-1` (worker job: job-1).",
        ),
        (
            {
                "status": "already_queued",
                "run": {"id": "run-1", "job_id": "job-1"},
                "job_id": "job-1",
            },
            "Schedule run `run-1` is already queued (worker job: job-1).",
        ),
        (
            {
                "status": "already_requested",
                "run": {"id": "run-1", "status": "succeeded"},
            },
            "A recent schedule run `run-1` already exists (succeeded).",
        ),
    ],
)
def test_manual_schedule_run_message_reflects_the_backend_status(
    payload: dict[str, object],
    expected: str,
) -> None:
    """Duplicate clicks should never be presented as newly queued work."""

    assert AgentSchedulesCog._manual_run_response_message(payload) == expected


def test_schedule_creation_operation_id_is_stable_across_discord_retries() -> None:
    """A lost Discord response does not create a duplicate schedule."""

    first = {
        "context": {
            "discord_user_id": "1001",
            "guild_id": "1000",
            "interaction_id": "2001",
            "roles": ["Admin"],
        },
        "name": " Weekly   operations ",
        "cron_expression": "0  9 * * 1",
        "timezone": "UTC",
        "prompt": "Review operational health.",
        "execution_mode": "agent_loop",
        "channel_id": "3000",
    }
    retried = {
        **first,
        "context": {
            **first["context"],
            "roles": ["Renamed Admin Role"],
        },
        "name": "Weekly operations",
        "cron_expression": "0 9 * * 1",
    }
    recreated = {
        **retried,
        "context": {
            **retried["context"],
            "interaction_id": "2002",
        },
    }

    first_id = AgentSchedulesCog._schedule_creation_operation_id(first)
    retry_id = AgentSchedulesCog._schedule_creation_operation_id(retried)
    recreated_id = AgentSchedulesCog._schedule_creation_operation_id(recreated)

    assert first_id == retry_id
    assert first_id == recreated_id
    assert first_id.startswith("discord-schedule:")
    assert len(first_id) <= 128


def test_schedule_creation_operation_id_changes_with_schedule_definition() -> None:
    """A materially different schedule remains a distinct create operation."""

    payload = {
        "context": {"discord_user_id": "1001", "guild_id": "1000"},
        "name": "Weekly operations",
        "cron_expression": "0 9 * * 1",
        "timezone": "UTC",
        "prompt": "Review operational health.",
        "execution_mode": "agent_loop",
        "channel_id": "3000",
    }

    first_id = AgentSchedulesCog._schedule_creation_operation_id(payload)
    changed_id = AgentSchedulesCog._schedule_creation_operation_id(
        {**payload, "prompt": "Review updated operational health."}
    )

    assert first_id != changed_id


def test_schedule_backend_request_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API secret never follows an attacker-controlled redirect."""

    monkeypatch.setattr(
        schedules_module,
        "settings",
        SimpleNamespace(
            backend_api_base_url="https://api.example",
            api_shared_secret="schedule-secret",
            agent_api_timeout_seconds=8.0,
        ),
    )
    response = SimpleNamespace(status_code=307, json=Mock(return_value={}))

    with patch.object(schedules_module.requests, "post", return_value=response) as post:
        result = AgentSchedulesCog._post_backend_sync("/agent/schedules", {})

    assert result["http_status"] == 307
    assert post.call_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_schedule_control_rejects_an_untrusted_path_identifier() -> None:
    """Control actions never interpolate an unvalidated Discord command value."""

    cog = AgentSchedulesCog.__new__(AgentSchedulesCog)
    cog._context = Mock(return_value={"discord_user_id": "1001"})
    cog._post_backend = AsyncMock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await AgentSchedulesCog.schedule_control_command.callback(
        cog,
        interaction,
        "../../sync/people#",
        SimpleNamespace(value="pause"),
    )

    cog._post_backend.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        "The schedule ID must be a valid UUID.", ephemeral=True
    )


@pytest.mark.asyncio
async def test_schedule_run_rejects_an_untrusted_path_identifier() -> None:
    """Manual runs use the same UUID boundary as lifecycle controls."""

    cog = AgentSchedulesCog.__new__(AgentSchedulesCog)
    cog._context = Mock(return_value={"discord_user_id": "1001"})
    cog._post_backend = AsyncMock()
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await AgentSchedulesCog.schedule_run_command.callback(
        cog,
        interaction,
        "../../sync/people#",
    )

    cog._post_backend.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        "The schedule ID must be a valid UUID.", ephemeral=True
    )
