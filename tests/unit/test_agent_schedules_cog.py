"""Unit tests for Discord recurring-schedule control messages."""

from __future__ import annotations

import pytest

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
    """A new Discord interaction for the same schedule reuses its durable key."""

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
            "interaction_id": "2002",
            "roles": ["Renamed Admin Role"],
        },
        "name": "Weekly operations",
        "cron_expression": "0 9 * * 1",
    }

    first_id = AgentSchedulesCog._schedule_creation_operation_id(first)
    retry_id = AgentSchedulesCog._schedule_creation_operation_id(retried)

    assert first_id == retry_id
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
