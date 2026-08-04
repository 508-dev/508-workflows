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
