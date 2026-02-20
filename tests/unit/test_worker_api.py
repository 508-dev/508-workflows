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


@pytest.mark.asyncio
async def test_health_handler_healthy() -> None:
    """Health endpoint should report healthy when Redis pings."""
    app_obj = web.Application()
    app_obj[api.REDIS_CONN_KEY] = _HealthyRedis()
    request = make_mocked_request("GET", "/health", app=app_obj)

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

    response = await api.health_handler(request)
    payload = json.loads(response.text)

    assert response.status == 503
    assert payload["status"] == "degraded"


@pytest.mark.asyncio
async def test_ingest_handler_enqueues_job() -> None:
    """Ingest endpoint should enqueue payload and return job metadata."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/github", app=app_obj, match_info={"source": "github"}
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
async def test_ingest_handler_rejects_non_object_payload() -> None:
    """Ingest endpoint should reject non-object JSON payloads."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request("POST", "/webhooks/default", app=app_obj)
    request.json = AsyncMock(return_value=["not-an-object"])  # type: ignore[method-assign]

    response = await api.ingest_handler(request)
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["error"] == "payload_must_be_object"


@pytest.mark.asyncio
async def test_espocrm_webhook_handler_enqueues_contact_jobs() -> None:
    """EspoCRM webhook should enqueue one job per event."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request("POST", "/webhooks/espocrm", app=app_obj)
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
async def test_espocrm_webhook_handler_rejects_non_list_payload() -> None:
    """EspoCRM webhook should enforce array payload shape."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request("POST", "/webhooks/espocrm", app=app_obj)
    request.json = AsyncMock(return_value={"id": "c-1"})  # type: ignore[method-assign]

    response = await api.espocrm_webhook_handler(request)
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["error"] == "payload_must_be_array_of_events"


@pytest.mark.asyncio
async def test_process_contact_handler_enqueues_single_contact() -> None:
    """Manual contact endpoint should enqueue one contact job."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST",
        "/process-contact/c-123",
        app=app_obj,
        match_info={"contact_id": "c-123"},
    )

    with patch("five08.worker.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-123")
        response = await api.process_contact_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["contact_id"] == "c-123"
    assert payload["job_id"] == "job-123"
