"""Unit tests for the worker-to-API recurring-schedule handoff."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from five08.worker import jobs


def test_run_agent_schedule_job_delegates_with_the_existing_api_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker never holds agent credentials or bypasses the API policy loop."""

    monkeypatch.setattr(jobs.settings, "agent_schedule_api_base_url", "http://api")
    monkeypatch.setattr(jobs.settings, "api_shared_secret", "shared-secret")
    response = SimpleNamespace(
        status_code=200,
        json=Mock(
            return_value={
                "status": "succeeded",
                "schedule_id": "schedule-1",
                "delivery_status": "posted",
            }
        ),
    )

    with patch("five08.worker.jobs.requests.post", return_value=response) as post:
        result = jobs.run_agent_schedule_job("run-1")

    assert result == {
        "run_id": "run-1",
        "status": "succeeded",
        "schedule_id": "schedule-1",
        "delivery_status": "posted",
    }
    assert post.call_args.args[0] == "http://api/internal/agent-schedules/runs/run-1"
    assert post.call_args.kwargs["headers"] == {"X-API-Secret": "shared-secret"}


def test_run_agent_schedule_job_marks_policy_rejections_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoked owner or invalid stored run should not consume retry attempts."""

    monkeypatch.setattr(jobs.settings, "agent_schedule_api_base_url", "http://api")
    monkeypatch.setattr(jobs.settings, "api_shared_secret", "shared-secret")
    response = SimpleNamespace(
        status_code=403, json=Mock(return_value={"error": "denied"})
    )

    with (
        patch("five08.worker.jobs.requests.post", return_value=response),
        pytest.raises(jobs.AgentScheduleRunNonRetryableError, match="403:denied"),
    ):
        jobs.run_agent_schedule_job("run-1")


def test_expired_agent_memory_cleanup_uses_only_the_worker_postgres_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMemoryStore:
        def __init__(self, postgres_url: str) -> None:
            assert postgres_url == "postgresql://worker-db"

        def purge_expired_all_organizations(self) -> int:
            return 4

    monkeypatch.setattr(jobs.settings, "postgres_url", "postgresql://worker-db")
    monkeypatch.setattr(jobs, "PostgresMemoryStore", FakeMemoryStore)

    assert jobs.purge_expired_agent_memory_facts_job() == {"purged_count": 4}
