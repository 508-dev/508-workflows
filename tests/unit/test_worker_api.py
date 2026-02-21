"""Unit tests for worker ingest API."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from five08.worker import api


class _HealthyRedis:
    def ping(self) -> bool:
        return True


class _FailingRedis:
    def ping(self) -> bool:
        raise RuntimeError("redis unavailable")


@pytest.fixture
def auth_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Configure webhook secret and return matching auth headers."""
    monkeypatch.setattr(api.settings, "api_shared_secret", "test-secret")
    return {"X-API-Secret": "test-secret"}


@pytest.mark.asyncio
async def test_health_handler_healthy() -> None:
    """Health endpoint should report healthy when Redis pings."""
    app_obj = web.Application()
    app_obj[api.REDIS_CONN_KEY] = _HealthyRedis()
    request = make_mocked_request("GET", "/health", app=app_obj)

    with patch("five08.worker.api.is_postgres_healthy", return_value=True):
        response = await api.health_handler(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_handler_degraded() -> None:
    """Health endpoint should report degraded when Redis fails."""
    app_obj = web.Application()
    app_obj[api.REDIS_CONN_KEY] = _FailingRedis()
    request = make_mocked_request("GET", "/health", app=app_obj)

    with patch("five08.worker.api.is_postgres_healthy", return_value=True):
        response = await api.health_handler(request)
    payload = json.loads(response.text)

    assert response.status == 503
    assert payload["status"] == "degraded"


@pytest.mark.asyncio
async def test_ingest_handler_enqueues_job(auth_headers: dict[str, str]) -> None:
    """Ingest endpoint should enqueue payload and return job metadata."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST",
        "/webhooks/github",
        app=app_obj,
        match_info={"source": "github"},
        headers=auth_headers,
    )
    request.json = AsyncMock(return_value={"id": "evt-1"})  # type: ignore[method-assign]

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-123")
        response = await api.ingest_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["job_id"] == "job-123"
    assert payload["source"] == "github"


@pytest.mark.asyncio
async def test_ingest_handler_rejects_non_object_payload(
    auth_headers: dict[str, str],
) -> None:
    """Ingest endpoint should reject non-object JSON payloads."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/default", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(return_value=["not-an-object"])  # type: ignore[method-assign]

    response = await api.ingest_handler(request)
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["error"] == "payload_must_be_object"


@pytest.mark.asyncio
async def test_espocrm_webhook_handler_enqueues_contact_jobs(
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should enqueue one job per event."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/espocrm", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(return_value=[{"id": "c-1"}, {"id": "c-2"}])  # type: ignore[method-assign]

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.side_effect = [Mock(id="job-1"), Mock(id="job-2")]
        response = await api.espocrm_webhook_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["events_processed"] == 2
    assert payload["jobs"] == [
        {"contact_id": "c-1", "job_id": "job-1"},
        {"contact_id": "c-2", "job_id": "job-2"},
    ]


@pytest.mark.asyncio
async def test_espocrm_webhook_handler_rejects_non_list_payload(
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should enforce array payload shape."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/espocrm", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(return_value={"id": "c-1"})  # type: ignore[method-assign]

    response = await api.espocrm_webhook_handler(request)
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["error"] == "payload_must_be_array_of_events"


@pytest.mark.asyncio
async def test_process_contact_handler_enqueues_single_contact(
    auth_headers: dict[str, str],
) -> None:
    """Manual contact endpoint should enqueue one contact job."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST",
        "/process-contact/c-123",
        app=app_obj,
        match_info={"contact_id": "c-123"},
        headers=auth_headers,
    )

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-123")
        response = await api.process_contact_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["contact_id"] == "c-123"
    assert payload["job_id"] == "job-123"


@pytest.mark.asyncio
async def test_resume_extract_handler_enqueues_job(
    auth_headers: dict[str, str],
) -> None:
    """Resume extract endpoint should enqueue extraction job."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/jobs/resume-extract", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(
        return_value={
            "contact_id": "c-1",
            "attachment_id": "a-1",
            "filename": "resume.pdf",
        }
    )  # type: ignore[method-assign]

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-extract", created=True)
        response = await api.resume_extract_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["job_id"] == "job-extract"
    assert payload["contact_id"] == "c-1"
    assert payload["attachment_id"] == "a-1"


@pytest.mark.asyncio
async def test_resume_apply_handler_enqueues_job(
    auth_headers: dict[str, str],
) -> None:
    """Resume apply endpoint should enqueue apply job."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/jobs/resume-apply", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(
        return_value={
            "contact_id": "c-1",
            "updates": {"emailAddress": "dev@example.com"},
            "link_discord": {"user_id": "123", "username": "dev#1111"},
        }
    )  # type: ignore[method-assign]

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-apply", created=True)
        response = await api.resume_apply_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["job_id"] == "job-apply"
    assert payload["contact_id"] == "c-1"


@pytest.mark.asyncio
async def test_job_status_handler_returns_result(
    auth_headers: dict[str, str],
) -> None:
    """Job status endpoint should expose persisted result payload."""
    app_obj = web.Application()
    request = make_mocked_request(
        "GET",
        "/jobs/job-123",
        app=app_obj,
        headers=auth_headers,
        match_info={"job_id": "job-123"},
    )

    mock_status = Mock()
    mock_status.value = "succeeded"
    mock_job = Mock(
        id="job-123",
        type="extract_resume_profile_job",
        status=mock_status,
        attempts=1,
        max_attempts=8,
        last_error=None,
        payload={"result": {"success": True}},
    )

    with patch("five08.worker.api.get_job", return_value=mock_job):
        response = await api.job_status_handler(request)

    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["job_id"] == "job-123"
    assert payload["status"] == "succeeded"
    assert payload["result"] == {"success": True}
