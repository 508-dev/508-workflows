"""Unit tests for jobsctl CLI."""

import json
from datetime import datetime, timezone

from unittest.mock import patch

from five08 import jobcli
import httpx
import pytest


def _json_response(
    *,
    status_code: int,
    payload: object,
    method: str,
    url: str,
) -> httpx.Response:
    request = httpx.Request(method=method, url=url)
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
        request=request,
    )


def test_jobsctl_status_calls_jobs_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_API_BASE_URL", raising=False)
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    with patch("five08.jobcli.httpx.request") as mock_request:
        mock_request.return_value = _json_response(
            status_code=200,
            payload={"status": "succeeded", "job_id": "job-123"},
            method="GET",
            url="http://localhost:8090/jobs/job-123",
        )

        exit_code = jobcli.run(
            [
                "--api-url",
                "http://localhost:8090",
                "--secret",
                "test-secret",
                "status",
                "job-123",
            ]
        )

    assert exit_code == 0
    mock_request.assert_called_once()
    called = mock_request.call_args.kwargs
    assert called["method"] == "GET"
    assert called["url"] == "http://localhost:8090/jobs/job-123"
    assert called["headers"] == {"X-API-Secret": "test-secret"}


def test_jobsctl_rerun_calls_rerun_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_API_BASE_URL", raising=False)
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    with patch("five08.jobcli.httpx.request") as mock_request:
        mock_request.return_value = _json_response(
            status_code=200,
            payload={
                "job_id": "job-new",
                "source_job_id": "job-old",
                "status": "queued",
            },
            method="POST",
            url="http://localhost:8090/jobs/job-old/rerun",
        )

        exit_code = jobcli.run(
            [
                "--api-url",
                "http://localhost:8090",
                "--secret",
                "test-secret",
                "rerun",
                "job-old",
            ]
        )

    assert exit_code == 0
    called = mock_request.call_args.kwargs
    assert called["method"] == "POST"
    assert called["url"] == "http://localhost:8090/jobs/job-old/rerun"


def test_jobsctl_recent_calls_jobs_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKER_API_BASE_URL", raising=False)
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    with patch("five08.jobcli.httpx.request") as mock_request:
        mock_request.return_value = _json_response(
            status_code=200,
            payload=[
                {
                    "job_id": "job-1",
                    "type": "sync_people_from_crm_job",
                    "status": "succeeded",
                    "attempts": 2,
                    "max_attempts": 8,
                    "last_error": None,
                    "created_at": datetime(
                        2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc
                    ).isoformat(),
                    "updated_at": datetime(
                        2026, 2, 26, 12, 0, 5, tzinfo=timezone.utc
                    ).isoformat(),
                }
            ],
            method="GET",
            url="http://localhost:8090/jobs",
        )

        exit_code = jobcli.run(
            [
                "--api-url",
                "http://localhost:8090",
                "--secret",
                "test-secret",
                "recent",
                "--minutes",
                "90",
                "--limit",
                "25",
                "--status",
                "succeeded",
                "--type",
                "sync_people_from_crm_job",
            ]
        )

    assert exit_code == 0
    mock_request.assert_called_once()
    called = mock_request.call_args.kwargs
    assert called["method"] == "GET"
    assert called["url"] == "http://localhost:8090/jobs"
    assert called["params"] == {
        "minutes": 90,
        "limit": 25,
        "status": "succeeded",
        "type": "sync_people_from_crm_job",
    }
    assert called["headers"] == {"X-API-Secret": "test-secret"}


def test_jobsctl_rerun_uses_default_secret_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_SHARED_SECRET", "from-env")
    with patch("five08.jobcli.httpx.request") as mock_request:
        mock_request.return_value = _json_response(
            status_code=200,
            payload={
                "job_id": "job-new",
                "source_job_id": "job-old",
                "status": "queued",
            },
            method="POST",
            url="http://localhost:8090/jobs/job-old/rerun",
        )

        exit_code = jobcli.run(
            ["--api-url", "http://localhost:8090", "rerun", "job-old"]
        )

    assert exit_code == 0
    assert mock_request.call_args.kwargs["headers"]["X-API-Secret"] == "from-env"


def test_jobsctl_status_prints_error_when_api_returns_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("WORKER_API_BASE_URL", raising=False)
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    with patch("five08.jobcli.httpx.request") as mock_request:
        mock_request.return_value = _json_response(
            status_code=404,
            payload={"error": "job_not_found"},
            method="GET",
            url="http://localhost:8090/jobs/job-missing",
        )

        exit_code = jobcli.run(
            [
                "--api-url",
                "http://localhost:8090",
                "--secret",
                "test-secret",
                "status",
                "job-missing",
            ]
        )

    assert exit_code == 1
    assert "API error 404: job_not_found" in capsys.readouterr().err


def test_jobsctl_status_fails_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("WORKER_API_BASE_URL", raising=False)
    monkeypatch.delenv("API_SHARED_SECRET", raising=False)
    exit_code = jobcli.run(["status", "job-123"])

    assert exit_code == 1
    assert "Missing API secret" in capsys.readouterr().err
