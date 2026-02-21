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
    monkeypatch.setattr(api.settings, "webhook_shared_secret", "test-secret")
    return {"X-Webhook-Secret": "test-secret"}


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
    """EspoCRM webhook should enqueue before responding."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/espocrm", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(return_value=[{"id": "c-1"}, {"id": "c-2"}])  # type: ignore[method-assign]

    with patch("five08.worker.api._enqueue_espocrm_batch", new_callable=AsyncMock):
        response = await api.espocrm_webhook_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["events_received"] == 2
    assert payload["events_enqueued"] == 2


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
async def test_sync_people_handler_enqueues_full_sync(
    auth_headers: dict[str, str],
) -> None:
    """Manual people-sync endpoint should enqueue one full sync job."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/sync/people", app=app_obj, headers=auth_headers
    )

    with patch("five08.worker.api._enqueue_full_crm_sync_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-sync", created=True)
        response = await api.sync_people_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["job_id"] == "job-sync"
    assert payload["created"] is True


@pytest.mark.asyncio
async def test_espocrm_people_sync_webhook_handler_enqueues_contact_jobs(
    auth_headers: dict[str, str],
) -> None:
    """People sync webhook should enqueue before responding."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST",
        "/webhooks/espocrm/people-sync",
        app=app_obj,
        headers=auth_headers,
    )
    request.json = AsyncMock(return_value=[{"id": "c-1"}, {"id": "c-2"}])  # type: ignore[method-assign]

    with patch(
        "five08.worker.api._enqueue_espocrm_people_sync_batch",
        new_callable=AsyncMock,
    ):
        response = await api.espocrm_people_sync_webhook_handler(request)

    payload = json.loads(response.text)
    assert response.status == 202
    assert payload["events_received"] == 2
    assert payload["events_enqueued"] == 2


@pytest.mark.asyncio
async def test_espocrm_webhook_handler_returns_503_on_enqueue_failure(
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should fail when enqueue persistence fails."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST", "/webhooks/espocrm", app=app_obj, headers=auth_headers
    )
    request.json = AsyncMock(return_value=[{"id": "c-1"}])  # type: ignore[method-assign]

    with patch(
        "five08.worker.api._enqueue_espocrm_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await api.espocrm_webhook_handler(request)

    payload = json.loads(response.text)
    assert response.status == 503
    assert payload["error"] == "enqueue_failed"


@pytest.mark.asyncio
async def test_espocrm_people_sync_webhook_handler_returns_503_on_enqueue_failure(
    auth_headers: dict[str, str],
) -> None:
    """People sync webhook should fail when enqueue persistence fails."""
    app_obj = web.Application()
    app_obj[api.QUEUE_KEY] = Mock()
    request = make_mocked_request(
        "POST",
        "/webhooks/espocrm/people-sync",
        app=app_obj,
        headers=auth_headers,
    )
    request.json = AsyncMock(return_value=[{"id": "c-1"}])  # type: ignore[method-assign]

    with patch(
        "five08.worker.api._enqueue_espocrm_people_sync_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await api.espocrm_people_sync_webhook_handler(request)

    payload = json.loads(response.text)
    assert response.status == 503
    assert payload["error"] == "enqueue_failed"


@pytest.mark.asyncio
async def test_audit_event_handler_persists_human_event(
    auth_headers: dict[str, str],
) -> None:
    """Audit events endpoint should persist one validated event."""
    app_obj = web.Application()
    request = make_mocked_request(
        "POST",
        "/audit/events",
        app=app_obj,
        headers=auth_headers,
    )
    request.json = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "source": "discord",
            "action": "crm.search",
            "result": "success",
            "actor_provider": "discord",
            "actor_subject": "12345",
            "actor_display_name": "johnny",
            "metadata": {"query": "python"},
        }
    )

    with patch("five08.worker.api.insert_audit_event") as mock_insert:
        mock_insert.return_value = Mock(id="evt-1", person_id="person-1")
        response = await api.audit_event_handler(request)

    payload = json.loads(response.text)
    assert response.status == 201
    assert payload["event_id"] == "evt-1"
    assert payload["person_id"] == "person-1"
