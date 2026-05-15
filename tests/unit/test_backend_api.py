"""Unit tests for backend dashboard/ingest API."""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from five08.agent import (
    AgentExecutionResult,
    AgentIdentityContext,
    AgentOrchestrator,
    InMemoryTaskStore,
    ToolRegistry,
)
from five08.backend import api
from five08.worker.masking import mask_email


class _HealthyRedis:
    def ping(self) -> bool:
        return True


class _FailingRedis:
    def ping(self) -> bool:
        raise RuntimeError("redis unavailable")


class _FakeAuthStore(api.RedisAuthStore):
    def __init__(self) -> None:
        self.saved_links: dict[str, object] = {}

    async def save_discord_link(
        self,
        *,
        token: str,
        payload: object,
        ttl_seconds: int,
    ) -> None:
        self.saved_links[token] = payload

    async def get_discord_link(self, token: str) -> object | None:
        return self.saved_links.get(token)

    async def delete_discord_link(self, token: str) -> None:
        self.saved_links.pop(token, None)

    async def get_session(self, session_id: str) -> object | None:
        return None

    async def delete_session(self, session_id: str) -> None:
        return None

    async def save_oidc_state(
        self, *, state: str, payload: object, ttl_seconds: int
    ) -> None:
        return None

    async def pop_oidc_state(self, state: str) -> object | None:
        return None


@pytest.fixture
def auth_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Configure API secret and return matching auth headers."""
    monkeypatch.setattr(api.settings, "api_shared_secret", "test-secret")
    monkeypatch.setattr(api, "_AGENT_REQUEST_TIMESTAMPS", {})
    return {"X-API-Secret": "test-secret"}


@pytest.fixture
def app() -> api.FastAPI:
    app_obj = api.create_app(run_lifespan=False)
    app_obj.state.queue = Mock()
    app_obj.state.redis_conn = _HealthyRedis()
    return app_obj


@pytest.fixture
def client(app: api.FastAPI) -> TestClient:
    return TestClient(app)


def test_health_handler_healthy(client: TestClient) -> None:
    """Health endpoint should report healthy when Redis pings."""
    with patch("five08.backend.api.is_postgres_healthy", return_value=True):
        response = client.get("/health")

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "healthy"


def test_health_handler_degraded(app: api.FastAPI) -> None:
    """Health endpoint should report degraded when Redis fails."""
    app.state.redis_conn = _FailingRedis()
    client = TestClient(app)
    with patch("five08.backend.api.is_postgres_healthy", return_value=True):
        response = client.get("/health")

    payload = response.json()
    assert response.status_code == 503
    assert payload["status"] == "degraded"


def test_ingest_handler_enqueues_job(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Ingest endpoint should enqueue payload and return job metadata."""
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-123")
        response = client.post(
            "/webhooks/github",
            json={"id": "evt-1"},
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["job_id"] == "job-123"
    assert payload["source"] == "github"


def test_ingest_handler_rejects_non_object_payload(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Ingest endpoint should reject non-object JSON payloads."""
    response = client.post(
        "/webhooks/default",
        json=["not-an-object"],
        headers=auth_headers,
    )

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "payload_must_be_object"


def test_espocrm_webhook_handler_enqueues_contact_jobs(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should enqueue before responding."""
    with patch("five08.backend.api._enqueue_espocrm_batch", new_callable=AsyncMock):
        response = client.post(
            "/webhooks/espocrm",
            json=[{"id": "c-1"}, {"id": "c-2"}],
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["events_received"] == 2
    assert payload["events_enqueued"] == 2


def test_espocrm_webhook_handler_rejects_non_list_payload(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should enforce array payload shape."""
    response = client.post(
        "/webhooks/espocrm",
        json={"id": "c-1"},
        headers=auth_headers,
    )

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "payload_must_be_array_of_events"


def test_process_contact_handler_enqueues_single_contact(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Manual contact endpoint should enqueue one contact job."""
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-123")
        response = client.post("/process-contact/c-123", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 202
    assert payload["contact_id"] == "c-123"
    assert payload["job_id"] == "job-123"


def test_resume_extract_handler_enqueues_job(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Resume extract endpoint should enqueue extraction job."""
    monkeypatch.setattr(api.settings, "resume_extractor_version", "v7")
    monkeypatch.setattr(api.settings, "openai_api_key", "key")
    monkeypatch.setattr(api.settings, "openai_base_url", None)
    monkeypatch.setattr(api.settings, "resume_ai_model", "gpt-test")

    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-extract", created=True)
        response = client.post(
            "/jobs/resume-extract",
            json={
                "contact_id": "c-1",
                "attachment_id": "a-1",
                "filename": "resume.pdf",
            },
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["job_id"] == "job-extract"
    assert payload["contact_id"] == "c-1"
    assert payload["attachment_id"] == "a-1"
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["idempotency_key"] == "resume-extract:c-1:a-1:v7:gpt-test"


def test_resume_extract_handler_appends_refresh_token_to_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Explicit refresh tokens should force a new resume extract job key."""
    monkeypatch.setattr(api.settings, "resume_extractor_version", "v7")
    monkeypatch.setattr(api.settings, "openai_api_key", "key")
    monkeypatch.setattr(api.settings, "openai_base_url", None)
    monkeypatch.setattr(api.settings, "resume_ai_model", "gpt-test")

    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-extract", created=True)
        response = client.post(
            "/jobs/resume-extract",
            json={
                "contact_id": "c-1",
                "attachment_id": "a-1",
                "filename": "resume.pdf",
                "refresh_token": "refresh-123",
            },
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["job_id"] == "job-extract"
    call_kwargs = mock_enqueue.call_args.kwargs
    assert (
        call_kwargs["idempotency_key"]
        == "resume-extract:c-1:a-1:v7:gpt-test:refresh-123"
    )


def test_resume_extract_handler_idempotency_key_uses_fallback_provider(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Fallback-only resume LLM providers should affect the job idempotency key."""
    monkeypatch.setattr(api.settings, "resume_extractor_version", "v7")
    monkeypatch.setattr(api.settings, "openai_api_key", None)
    monkeypatch.setattr(api.settings, "openai_base_url", None)
    monkeypatch.setattr(api.settings, "openai_direct_api_key", None)
    monkeypatch.setattr(api.settings, "fireworks_api_key", None)
    monkeypatch.setattr(api.settings, "openrouter_api_key", "openrouter-key")
    monkeypatch.setattr(api.settings, "resume_ai_api_key", None)
    monkeypatch.setattr(api.settings, "resume_ai_base_url", None)
    monkeypatch.setattr(api.settings, "resume_ai_model", "gpt-4.1-mini")

    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-extract", created=True)
        response = client.post(
            "/jobs/resume-extract",
            json={
                "contact_id": "c-1",
                "attachment_id": "a-1",
                "filename": "resume.pdf",
            },
            headers=auth_headers,
        )

    assert response.status_code == 202
    call_kwargs = mock_enqueue.call_args.kwargs
    assert (
        call_kwargs["idempotency_key"]
        == "resume-extract:c-1:a-1:v7:openrouter-direct/openai/gpt-4.1-mini"
    )


def test_resume_apply_handler_enqueues_job(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Resume apply endpoint should enqueue apply job."""
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-apply", created=True)
        response = client.post(
            "/jobs/resume-apply",
            json={
                "contact_id": "c-1",
                "updates": {"emailAddress": "dev@example.com"},
                "link_discord": {"user_id": "123", "username": "dev#1111"},
            },
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["job_id"] == "job-apply"
    assert payload["contact_id"] == "c-1"


def test_job_status_handler_returns_result(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Job status endpoint should expose persisted result payload."""
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

    with patch("five08.backend.api.get_job", return_value=mock_job):
        response = client.get("/jobs/job-123", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 200
    assert payload["job_id"] == "job-123"
    assert payload["status"] == "succeeded"
    assert payload["result"] == {"success": True}


def test_jobs_handler_returns_recent_jobs(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Jobs list endpoint should return created jobs sorted by API-reported query order."""
    mock_job = Mock(
        id="job-2",
        type="sync_people_from_crm_job",
        status=Mock(value="queued"),
        attempts=1,
        max_attempts=8,
        last_error=None,
        created_at=datetime(2026, 2, 26, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 26, 12, 1, 0, tzinfo=timezone.utc),
    )
    mock_job2 = Mock(
        id="job-1",
        type="extract_resume_profile_job",
        status=Mock(value="succeeded"),
        attempts=2,
        max_attempts=8,
        last_error="boom",
        created_at=datetime(2026, 2, 26, 13, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 26, 13, 5, 0, tzinfo=timezone.utc),
    )

    with patch(
        "five08.backend.api.list_jobs",
        return_value=[mock_job2, mock_job],
    ) as mock_list_jobs:
        response = client.get(
            "/jobs?minutes=15&limit=2&status=queued&type=sync_people_from_crm_job",
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload == [
        {
            "job_id": "job-1",
            "type": "extract_resume_profile_job",
            "status": "succeeded",
            "attempts": 2,
            "max_attempts": 8,
            "last_error": "boom",
            "created_at": "2026-02-26T13:00:00+00:00",
            "updated_at": "2026-02-26T13:05:00+00:00",
        },
        {
            "job_id": "job-2",
            "type": "sync_people_from_crm_job",
            "status": "queued",
            "attempts": 1,
            "max_attempts": 8,
            "last_error": None,
            "created_at": "2026-02-26T12:00:00+00:00",
            "updated_at": "2026-02-26T12:01:00+00:00",
        },
    ]

    mock_list_jobs.assert_called_once()
    called_kwargs = mock_list_jobs.call_args.kwargs
    assert called_kwargs["status"].value == "queued"
    assert called_kwargs["job_type"] == "sync_people_from_crm_job"
    assert called_kwargs["limit"] == 2
    assert called_kwargs["created_after"].tzinfo == timezone.utc


def test_jobs_handler_rejects_invalid_status(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Jobs list endpoint should reject unknown status filters."""
    response = client.get("/jobs?minutes=15&status=not-a-status", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "invalid_status"
    assert payload["status"] == "not-a-status"


def test_rerun_job_handler_enqueues_new_job(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Rerun endpoint should enqueue a fresh job from existing call payload."""
    source_job = Mock(
        id="job-old-1",
        type="process_docuseal_agreement_job",
        max_attempts=8,
        payload={
            "args": ["member@508.dev", "2026-02-25 12:00:00", 55],
            "kwargs": {},
            "result": {"success": False},
        },
    )

    with (
        patch("five08.backend.api.get_job", return_value=source_job),
        patch("five08.backend.api.enqueue_job") as mock_enqueue,
    ):
        mock_enqueue.return_value = Mock(id="job-new-1", created=True)
        response = client.post("/jobs/job-old-1/rerun", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["source_job_id"] == "job-old-1"
    assert payload["job_id"] == "job-new-1"
    assert payload["type"] == "process_docuseal_agreement_job"
    assert payload["created"] is True

    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["fn"] is api.process_docuseal_agreement_job
    assert call_kwargs["args"] == (
        "member@508.dev",
        "2026-02-25 12:00:00",
        55,
    )
    assert call_kwargs["kwargs"] == {}
    assert call_kwargs["max_attempts"] == 8
    prefix = "manual-rerun:job-old-1:"
    assert call_kwargs["idempotency_key"].startswith(prefix)
    suffix = call_kwargs["idempotency_key"][len(prefix) :]
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", suffix)


def test_rerun_job_handler_returns_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Rerun endpoint should 404 when source job does not exist."""
    with patch("five08.backend.api.get_job", return_value=None):
        response = client.post("/jobs/missing/rerun", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 404
    assert payload["error"] == "job_not_found"


def test_rerun_job_handler_rejects_unknown_job_type(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Rerun endpoint should reject unknown persisted job types."""
    source_job = Mock(
        id="job-old-2",
        type="some_unknown_type",
        max_attempts=8,
        payload={"args": [], "kwargs": {}},
    )
    with patch("five08.backend.api.get_job", return_value=source_job):
        response = client.post("/jobs/job-old-2/rerun", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "unsupported_job_type"
    assert payload["job_type"] == "some_unknown_type"


def test_rerun_job_handler_rejects_invalid_payload_shape(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Rerun endpoint should reject source jobs with malformed call payload."""
    source_job = Mock(
        id="job-old-3",
        type="sync_people_from_crm_job",
        max_attempts=8,
        payload={"args": "not-a-list", "kwargs": {}},
    )
    with patch("five08.backend.api.get_job", return_value=source_job):
        response = client.post("/jobs/job-old-3/rerun", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 400
    assert payload["error"] == "invalid_job_payload"


def test_rerun_job_handler_returns_503_on_enqueue_failure(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Rerun endpoint should fail with 503 when enqueueing fails."""
    source_job = Mock(
        id="job-old-4",
        type="sync_people_from_crm_job",
        max_attempts=8,
        payload={"args": [], "kwargs": {}},
    )
    with (
        patch("five08.backend.api.get_job", return_value=source_job),
        patch("five08.backend.api.enqueue_job", side_effect=RuntimeError("boom")),
    ):
        response = client.post("/jobs/job-old-4/rerun", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"] == "enqueue_failed"


def test_resume_extract_model_name_uses_heuristic_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model identity should be heuristic when OpenAI key is absent."""
    monkeypatch.setattr(api.settings, "openai_api_key", None)
    monkeypatch.setattr(api.settings, "resume_ai_model", "gpt-test")

    assert api._resume_extract_model_name() == "heuristic"


def test_resume_extract_model_name_prefixes_openrouter_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenRouter base URL should map plain resume model to openai/<model>."""
    monkeypatch.setattr(api.settings, "openai_api_key", "key")
    monkeypatch.setattr(api.settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(api.settings, "resume_ai_model", "gpt-4o-mini")

    assert api._resume_extract_model_name() == "openai/gpt-4o-mini"


def test_sync_people_handler_enqueues_full_sync(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Manual people-sync endpoint should enqueue one full sync job."""
    with patch(
        "five08.backend.api._enqueue_full_crm_sync_job", new_callable=AsyncMock
    ) as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-sync", created=True)
        response = client.post("/sync/people", headers=auth_headers)

    payload = response.json()
    assert response.status_code == 202
    assert payload["job_id"] == "job-sync"
    assert payload["created"] is True


def test_espocrm_people_sync_webhook_handler_enqueues_contact_jobs(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """People sync webhook should enqueue before responding."""
    with patch(
        "five08.backend.api._enqueue_espocrm_people_sync_batch",
        new_callable=AsyncMock,
    ):
        response = client.post(
            "/webhooks/espocrm/people-sync",
            json=[{"id": "c-1"}, {"id": "c-2"}],
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["events_received"] == 2
    assert payload["events_enqueued"] == 2


def test_espocrm_webhook_handler_returns_503_on_enqueue_failure(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """EspoCRM webhook should fail when enqueue persistence fails."""
    with patch(
        "five08.backend.api._enqueue_espocrm_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = client.post(
            "/webhooks/espocrm",
            json=[{"id": "c-1"}],
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"] == "enqueue_failed"


def test_espocrm_people_sync_webhook_handler_returns_503_on_enqueue_failure(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """People sync webhook should fail when enqueue persistence fails."""
    with patch(
        "five08.backend.api._enqueue_espocrm_people_sync_batch",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = client.post(
            "/webhooks/espocrm/people-sync",
            json=[{"id": "c-1"}],
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 503
    assert payload["error"] == "enqueue_failed"


def test_audit_event_handler_persists_human_event(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Audit events endpoint should persist one validated event."""
    with patch("five08.backend.api.insert_audit_event") as mock_insert:
        mock_insert.return_value = Mock(id="evt-1", person_id="person-1")
        response = client.post(
            "/audit/events",
            json={
                "source": "discord",
                "action": "crm.search",
                "result": "success",
                "actor_provider": "discord",
                "actor_subject": "12345",
                "actor_display_name": "johnny",
                "metadata": {"query": "python"},
            },
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 201
    assert payload["event_id"] == "evt-1"
    assert payload["person_id"] == "person-1"


def test_agent_request_for_write_returns_confirmation_plan(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent writes should freeze a plan instead of mutating immediately."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch(
        "five08.backend.api._write_agent_audit_event", new_callable=AsyncMock
    ) as mock_audit:
        response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "interaction_id": "interaction-1",
                    "message_id": "message-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        audit_kwargs = mock_audit.call_args.kwargs

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "requires_confirmation"
    assert payload["plan"]["actions"][0]["tool_name"] == "task_write.create_task"
    assert payload["plan"]["plan_id"] in api._PENDING_AGENT_PLANS
    assert audit_kwargs["context"].interaction_id == "interaction-1"
    assert audit_kwargs["metadata"]["message"] == (
        "Create a task for Sarah to update onboarding docs by Friday"
    )


def test_agent_request_rejects_oversized_message(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/agent/requests",
        json={
            "message": "x" * 4097,
            "context": {
                "discord_user_id": "123",
                "organization_id": "org-1",
                "guild_id": "org-1",
                "roles": ["Member"],
            },
        },
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_agent_request_audits_unsupported_message_with_sanitized_shape(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_AGENT_ORCHESTRATOR", AgentOrchestrator())

    with patch(
        "five08.backend.api._write_agent_audit_event", new_callable=AsyncMock
    ) as mock_audit:
        response = client.post(
            "/agent/requests",
            json={
                "message": (
                    "frobnicate member agreement to Michael Wu at "
                    "michael@example.com +1 415 555 1212"
                ),
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Admin"],
                },
            },
            headers=auth_headers,
        )
        audit_kwargs = mock_audit.call_args.kwargs

    assert response.status_code == 422
    metadata = audit_kwargs["metadata"]
    assert metadata["reason"] == "unsupported_agent_request"
    assert metadata["improvement_log"] is True
    assert metadata["message_sanitized"] == ("frobnicate member agreement to [person]")
    assert "message" not in metadata
    assert "Michael Wu" not in str(metadata)
    assert "michael@example.com" not in str(metadata)
    assert "415" not in str(metadata)


def test_agent_request_rate_limits_per_user(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_AGENT_REQUEST_RATE_LIMIT_MAX_REQUESTS", 1)

    request_body = {
        "message": "nonsense",
        "context": {
            "discord_user_id": "123",
            "organization_id": "org-1",
            "guild_id": "org-1",
            "roles": ["Member"],
        },
    }

    with patch("five08.backend.api.insert_audit_event"):
        first_response = client.post(
            "/agent/requests",
            json=request_body,
            headers=auth_headers,
        )
        second_response = client.post(
            "/agent/requests",
            json=request_body,
            headers=auth_headers,
        )

    assert first_response.status_code == 422
    assert second_response.status_code == 429
    assert second_response.json()["status"] == "denied"


def test_agent_request_rejects_when_pending_plan_capacity_is_full(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pending confirmation plans should be bounded under request bursts."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})
    monkeypatch.setattr(api, "_MAX_PENDING_AGENT_PLANS", 1)

    request_body = {
        "message": "Create a task for Sarah to update onboarding docs by Friday",
        "context": {
            "discord_user_id": "123",
            "organization_id": "org-1",
            "guild_id": "org-1",
            "roles": ["Member"],
        },
    }

    with patch("five08.backend.api.insert_audit_event"):
        first_response = client.post(
            "/agent/requests",
            json=request_body,
            headers=auth_headers,
        )
        second_response = client.post(
            "/agent/requests",
            json=request_body,
            headers=auth_headers,
        )

    assert first_response.status_code == 202
    assert second_response.status_code == 503
    assert second_response.json()["status"] == "failed"
    assert second_response.json()["plan"] is None
    assert "capacity is full" in second_response.json()["message"]


def test_pending_agent_plan_lock_is_created_per_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pending-plan locks should be bound to the active request loop, not import time."""
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS_LOCK", None)
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS_LOCK_LOOP", None)

    async def get_lock() -> asyncio.Lock:
        return api._pending_agent_plans_lock()

    first_lock = asyncio.run(get_lock())
    second_lock = asyncio.run(get_lock())

    assert first_lock is not second_lock


@pytest.mark.asyncio
async def test_agent_audit_scheduler_keeps_strong_task_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    context = AgentIdentityContext(discord_user_id="123")

    async def wait_for_gate(**kwargs: object) -> None:
        await gate.wait()

    monkeypatch.setattr(api, "_write_agent_audit_event", wait_for_gate)
    monkeypatch.setattr(api, "_AGENT_AUDIT_TASKS", set())

    api._schedule_agent_audit_event(
        context=context,
        action="agent.request",
        result=api.AuditResult.SUCCESS,
        plan=None,
    )

    assert len(api._AGENT_AUDIT_TASKS) == 1
    task = next(iter(api._AGENT_AUDIT_TASKS))
    assert not task.done()

    gate.set()
    await task
    await asyncio.sleep(0)

    assert api._AGENT_AUDIT_TASKS == set()


@pytest.mark.asyncio
async def test_agent_confirmation_claim_pops_before_expired_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claiming a plan should not race itself if cleanup sees it as expired."""
    plan_response = AgentOrchestrator(today=datetime.now(timezone.utc).date()).plan(
        "Create a task for Sarah to update onboarding docs by Friday",
        AgentIdentityContext(
            discord_user_id="123",
            organization_id="org-1",
            guild_id="org-1",
            roles=["Member"],
        ),
    )
    assert plan_response.plan is not None
    original_context = AgentIdentityContext(
        discord_user_id="123",
        organization_id="org-1",
        guild_id="org-1",
        roles=["Member"],
    )
    monkeypatch.setattr(
        api,
        "_PENDING_AGENT_PLANS",
        {plan_response.plan.plan_id: (plan_response.plan, original_context)},
    )

    def cleanup_removes_plan(*, now: datetime | None = None) -> None:
        api._PENDING_AGENT_PLANS.pop(plan_response.plan.plan_id, None)

    monkeypatch.setattr(
        api, "_cleanup_expired_pending_agent_plans", cleanup_removes_plan
    )

    claim_status, pending = await api._claim_pending_agent_plan(
        plan_response.plan.plan_id,
        discord_user_id="123",
    )

    assert claim_status == "claimed"
    assert pending is not None
    assert pending[0].plan_id == plan_response.plan.plan_id


def test_agent_confirmation_executes_frozen_plan_inline(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmed agent writes should execute inline and return the result."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event"):
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        confirm_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )

    payload = confirm_response.json()
    assert confirm_response.status_code == 200
    assert payload["status"] == "executed"
    assert payload["results"][0]["result"]["task_id"] == "TASK-001"
    assert plan_id not in api._PENDING_AGENT_PLANS


def test_agent_confirmation_cancel_returns_canceled_status(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User cancellation should not be reported as policy denial."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event") as mock_insert:
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        cancel_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": False,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )

    payload = cancel_response.json()
    assert cancel_response.status_code == 200
    assert payload["status"] == "canceled"
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.result == api.AuditResult.SUCCESS
    assert audit_payload.metadata["status"] == "canceled"
    assert plan_id not in api._PENDING_AGENT_PLANS


def test_agent_confirmation_uses_original_context_for_execution(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmation should ignore spoofed roles/scopes/context from the client."""
    task_store = InMemoryTaskStore()
    task_store.create_task(
        title="Existing task",
        project="Atlas",
        assignee="Sarah",
        due_date=None,
        organization_id="org-1",
        created_by="456",
    )
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event"):
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Update TASK-001 due tomorrow",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        confirm_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "internal_user_id": "456",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member", "Admin"],
                    "scopes": ["task:update_own"],
                },
            },
            headers=auth_headers,
        )

    payload = confirm_response.json()
    assert confirm_response.status_code == 403
    assert payload["status"] == "denied"
    assert "creator" in payload["results"][0]["error"]


def test_agent_confirmation_uses_fresh_non_escalating_roles(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmation should reflect role revocation without accepting escalation."""
    captured: dict[str, object] = {}
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    class CapturingOrchestrator:
        def execute_plan(
            self,
            plan: object,
            context: AgentIdentityContext,
            *,
            confirmed: bool = False,
            effective_scopes: set[str] | None = None,
        ) -> list[AgentExecutionResult]:
            captured["context"] = context
            captured["effective_scopes"] = effective_scopes
            return [
                AgentExecutionResult(
                    tool_name="task_write.create_task",
                    status="succeeded",
                    result={"task_id": "TASK-001"},
                )
            ]

    with patch("five08.backend.api.insert_audit_event"):
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "internal_user_id": "internal-123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Admin"],
                    "scopes": ["deploy:request"],
                },
            },
            headers=auth_headers,
        )
        monkeypatch.setattr(api, "_AGENT_ORCHESTRATOR", CapturingOrchestrator())
        plan_id = plan_response.json()["plan"]["plan_id"]
        confirm_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "internal_user_id": "attacker-internal-id",
                    "organization_id": "other-org",
                    "guild_id": "other-guild",
                    "roles": ["Member"],
                    "scopes": ["deploy:request"],
                },
            },
            headers=auth_headers,
        )

    assert confirm_response.status_code == 200
    context = captured["context"]
    assert isinstance(context, AgentIdentityContext)
    assert context.internal_user_id == "internal-123"
    assert context.organization_id == "org-1"
    assert context.guild_id == "org-1"
    assert context.roles == ["Member"]
    assert context.scopes == []
    assert captured["effective_scopes"] == {
        "project:read",
        "task:create",
        "task:update_own",
    }


def test_agent_confirmation_executes_with_confirm_time_member_role(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained member role should keep member-scoped writes executable."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event"):
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Admin", "Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        confirm_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )

    assert confirm_response.status_code == 200
    assert confirm_response.json()["status"] == "executed"
    assert confirm_response.json()["results"][0]["result"]["task_id"] == "TASK-001"


def test_agent_confirmation_claims_plan_once(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed plan should be consumed so repeated confirms cannot double-run."""
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event"):
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        first_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        second_response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 404
    assert second_response.json()["error"] == "plan_not_found"


def test_agent_confirmation_not_found_is_audited(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event") as mock_insert:
        response = client.post(
            "/agent/confirmations/missing-plan",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "interaction_id": "interaction-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )

    assert response.status_code == 404
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "agent.confirmation"
    assert audit_payload.result == api.AuditResult.DENIED
    assert audit_payload.correlation_id == "interaction-1"
    assert audit_payload.metadata["reason"] == "plan_not_found"
    assert audit_payload.metadata["plan_id"] == "missing-plan"


def test_agent_confirmation_expired_plan_is_audited(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_store = InMemoryTaskStore()
    monkeypatch.setattr(
        api,
        "_AGENT_ORCHESTRATOR",
        AgentOrchestrator(registry=ToolRegistry(task_store)),
    )
    monkeypatch.setattr(api, "_PENDING_AGENT_PLANS", {})

    with patch("five08.backend.api.insert_audit_event") as mock_insert:
        plan_response = client.post(
            "/agent/requests",
            json={
                "message": "Create a task for Sarah to update onboarding docs by Friday",
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                    "interaction_id": "interaction-1",
                    "roles": ["Member"],
                },
            },
            headers=auth_headers,
        )
        plan_id = plan_response.json()["plan"]["plan_id"]
        plan, context = api._PENDING_AGENT_PLANS[plan_id]
        api._PENDING_AGENT_PLANS[plan_id] = (
            plan.model_copy(
                update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
            ),
            context,
        )
        response = client.post(
            f"/agent/confirmations/{plan_id}",
            json={
                "confirm": True,
                "context": {
                    "discord_user_id": "123",
                    "organization_id": "org-1",
                    "guild_id": "org-1",
                },
            },
            headers=auth_headers,
        )

    assert response.status_code == 410
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "agent.confirmation"
    assert audit_payload.result == api.AuditResult.DENIED
    assert audit_payload.resource_id == plan_id
    assert audit_payload.correlation_id == "interaction-1"
    assert audit_payload.metadata["reason"] == "plan_expired"


def test_auth_login_returns_503_when_store_not_ready(client: TestClient) -> None:
    response = client.get("/auth/login")
    assert response.status_code == 503
    assert response.json()["error"] == "auth_not_ready"


def test_auth_login_shows_recovery_page_when_oidc_not_configured(
    app: api.FastAPI,
) -> None:
    app.state.auth_store = _FakeAuthStore()
    client = TestClient(app)

    response = client.get("/auth/login", headers={"Accept": "text/html"})

    assert response.status_code == 503
    assert "SSO is not configured" in response.text
    assert "DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=false" in response.text


def test_auth_login_returns_json_when_oidc_not_configured_for_json_client(
    app: api.FastAPI,
) -> None:
    app.state.auth_store = _FakeAuthStore()
    client = TestClient(app)

    response = client.get("/auth/login", headers={"Accept": "application/json"})

    assert response.status_code == 503
    assert response.json()["error"] == "oidc_not_configured"


def test_auth_me_requires_session(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_dashboard_shows_login_recovery_for_unauthenticated_client(
    client: TestClient,
) -> None:
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 401
    assert "/dashboard-login" in response.text


def test_dashboard_clears_stale_session_cookie(client: TestClient) -> None:
    client.cookies.set(api.settings.auth_session_cookie_name, "stale-session")

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("stale-session", None),
    ):
        response = client.get("/dashboard", follow_redirects=False)

    assert response.status_code == 401
    assert "/dashboard-login" in response.text
    set_cookie = response.headers["set-cookie"]
    assert f"{api.settings.auth_session_cookie_name}=" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_dashboard_forbids_non_admin_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="member-1",
        email="member@508.dev",
        display_name="Member User",
        groups=["Member"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard")

    assert response.status_code == 403


def test_dashboard_renders_for_steering_committee_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="steering-1",
        email="steering@508.dev",
        display_name="Steering User",
        groups=["Steering Committee"],
        is_admin=False,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/people")

    assert response.status_code == 200
    assert "508 Operations Dashboard" in response.text


def test_dashboard_renders_for_admin_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["discord_admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
        crm_contact_id="contact-123",
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/people")

    assert response.status_code == 200
    assert "508 Operations Dashboard" in response.text
    assert "/dashboard/api/me" in response.text
    assert "/dashboard/people" in response.text
    assert "/dashboard/agent" in response.text


def test_dashboard_me_returns_crm_linked_admin_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["discord_admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
        crm_contact_id="contact-123",
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 200
    assert response.json()["crm_contact_id"] == "contact-123"
    assert response.json()["actor_provider"] == api.ActorProvider.DISCORD.value
    assert response.json()["crm_base_url"] == api._crm_base_url()
    assert "jobs:write" in response.json()["permissions"]


def test_dashboard_me_normalizes_crm_api_base_url(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "espo_base_url", "https://crm.example/api/v1/")
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 200
    assert response.json()["crm_base_url"] == "https://crm.example"


def test_dashboard_me_limits_sensitive_permissions_without_sso(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "environment", "production")
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["Admin"],
        is_admin=True,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 200
    assert "people:read" in response.json()["permissions"]
    assert "onboarding:write" in response.json()["permissions"]
    assert "jobs:write" not in response.json()["permissions"]


def test_dashboard_me_allows_sensitive_permissions_without_sso_in_dev(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "environment", "development")
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["Admin"],
        is_admin=True,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 200
    assert "jobs:write" in response.json()["permissions"]
    assert "audit:read" in response.json()["permissions"]


def test_dashboard_me_does_not_trust_literal_oidc_admin_group(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="oidc-user-1",
        email="user@508.dev",
        display_name="OIDC User",
        groups=["Admin"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_dashboard_me_does_not_trust_stale_oidc_permissions(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="oidc-user-1",
        email="user@508.dev",
        display_name="OIDC User",
        groups=["Admin"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        permissions=["people:read", "jobs:write"],
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_dashboard_me_honors_configured_discord_admin_role(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "discord_admin_roles", "Operations")
    session = api.AuthSession(
        subject="123456789",
        email="ops@508.dev",
        display_name="Ops User",
        groups=["Operations"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 200
    assert "jobs:write" in response.json()["permissions"]


def test_dashboard_me_clears_stale_session_cookie(client: TestClient) -> None:
    client.cookies.set(api.settings.auth_session_cookie_name, "stale-session")

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("stale-session", None),
    ):
        response = client.get("/dashboard/api/me")

    assert response.status_code == 401
    set_cookie = response.headers["set-cookie"]
    assert f"{api.settings.auth_session_cookie_name}=" in set_cookie
    assert "Max-Age=0" in set_cookie


def test_dashboard_jobs_requires_admin_session(client: TestClient) -> None:
    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=(None, None),
    ):
        response = client.get("/dashboard/api/jobs")

    assert response.status_code == 401


def test_dashboard_jobs_forbids_non_admin_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="member-1",
        email="member@508.dev",
        display_name="Member User",
        groups=["Member"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/jobs")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_dashboard_jobs_forbids_steering_committee_session(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="steering-1",
        email="steering@508.dev",
        display_name="Steering User",
        groups=["Steering Committee"],
        is_admin=False,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/jobs")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_dashboard_jobs_forbids_discord_admin_without_sso(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "environment", "production")
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["Admin"],
        is_admin=True,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/jobs")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_dashboard_jobs_returns_filtered_job_payload(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    created_at = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 2, 25, 12, 5, 0, tzinfo=timezone.utc)
    job = Mock(
        id="job-failed-1",
        type="sync_people_from_crm_job",
        status=api.JobStatus.FAILED,
        attempts=2,
        max_attempts=5,
        last_error="crm timeout",
        created_at=created_at,
        updated_at=updated_at,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.list_jobs", return_value=[job]) as mock_list_jobs,
    ):
        response = client.get(
            "/dashboard/api/jobs"
            "?minutes=15&limit=2&status=failed&type=sync_people_from_crm_job"
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "job_id": "job-failed-1",
            "type": "sync_people_from_crm_job",
            "status": "failed",
            "attempts": 2,
            "max_attempts": 5,
            "last_error": "crm timeout",
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
        }
    ]
    called_kwargs = mock_list_jobs.call_args.kwargs
    assert called_kwargs["limit"] == 2
    assert called_kwargs["status"] == api.JobStatus.FAILED
    assert called_kwargs["job_type"] == "sync_people_from_crm_job"
    assert called_kwargs["created_after"].tzinfo == timezone.utc


def test_dashboard_jobs_rejects_invalid_status(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.list_jobs") as mock_list_jobs,
    ):
        response = client.get("/dashboard/api/jobs?status=not-a-status")

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_status",
        "status": "not-a-status",
    }
    mock_list_jobs.assert_not_called()


def test_dashboard_jobs_ignores_empty_type_filter(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.list_jobs", return_value=[]) as mock_list_jobs,
    ):
        response = client.get("/dashboard/api/jobs?type=")

    assert response.status_code == 200
    assert mock_list_jobs.call_args.kwargs["job_type"] is None


def test_dashboard_job_detail_returns_redacted_payload(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    created_at = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 2, 25, 12, 5, 0, tzinfo=timezone.utc)
    job = Mock(
        id="job-1",
        type="extract_resume_profile_job",
        status=api.JobStatus.SUCCEEDED,
        payload={
            "args": ["contact-1"],
            "kwargs": {"refresh_token": "secret-refresh-token"},
            "result": {"status": "ok", "api_key": "secret-key"},
        },
        idempotency_key="resume-extract:contact-1:attachment-1:v1:gpt-test:secret-refresh-token",
        attempts=1,
        max_attempts=5,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=created_at,
        updated_at=updated_at,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.get_job", return_value=job),
    ):
        response = client.get("/dashboard/api/jobs/job-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == "job-1"
    assert (
        payload["idempotency_key"]
        == "resume-extract:contact-1:attachment-1:v1:gpt-test:[redacted]"
    )
    assert payload["payload"]["kwargs"]["refresh_token"] == "[redacted]"
    assert payload["result"]["api_key"] == "[redacted]"
    assert "secret-refresh-token" not in response.text


def test_dashboard_job_detail_redacts_sensitive_positional_args(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    created_at = datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc)
    updated_at = datetime(2026, 2, 25, 12, 5, 0, tzinfo=timezone.utc)
    job = Mock(
        id="job-mailbox",
        type="process_mailbox_message_job",
        status=api.JobStatus.QUEUED,
        payload={
            "args": ["raw-message-b64-with-email-and-resume"],
            "kwargs": {},
            "result": None,
        },
        idempotency_key="mailbox-inbox:message-1",
        attempts=0,
        max_attempts=5,
        run_after=None,
        locked_at=None,
        locked_by=None,
        last_error=None,
        created_at=created_at,
        updated_at=updated_at,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.get_job", return_value=job),
    ):
        response = client.get("/dashboard/api/jobs/job-mailbox")

    assert response.status_code == 200
    payload = response.json()
    assert payload["payload"]["args"] == ["[redacted]"]
    assert "raw-message-b64-with-email-and-resume" not in response.text


def test_dashboard_people_returns_lookup_payload(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    people = [
        {
            "crm_contact_id": "contact-123",
            "name": "Alice Prospect",
            "email": "alice@example.com",
            "email_508": "alice@508.dev",
            "discord_user_id": "123456789",
            "discord_username": "alice",
            "sync_status": "active",
            "profile_status": {
                "crm_active": True,
                "is_member": True,
                "discord_linked": True,
                "email_508": True,
                "latest_resume": True,
                "roles_count": 2,
                "skills_count": 4,
            },
            "updated_at": "2026-02-25T12:05:00+00:00",
        }
    ]

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._query_dashboard_people", return_value=people
        ) as mock_people,
    ):
        response = client.get(
            "/dashboard/api/people"
            "?query=alice&limit=10&sync_status=active&is_member=true"
            "&discord=linked&email_508=present&resume=present&skills=present"
        )

    assert response.status_code == 200
    assert response.json() == people
    mock_people.assert_called_once_with(
        normalized_query="alice",
        limit=10,
        sync_status="active",
        is_member=True,
        discord="linked",
        email_508="present",
        resume="present",
        skills="present",
    )


def test_dashboard_onboarding_returns_filtered_queue(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    queue = [
        {
            "crm_contact_id": "contact-prospect-1",
            "name": "Bea Prospect",
            "contact_type": "Prospect",
            "onboarding_state": "selected",
            "onboarder": "michael",
            "profile_status": {"skills_count": 0},
        }
    ]

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._list_dashboard_onboarding", return_value=queue
        ) as mock_onboarding,
    ):
        response = client.get(
            "/dashboard/api/onboarding"
            "?query=bea&limit=10&onboarding_state=Awaiting%20Contribution"
            "&onboarder=michael"
            "&skills=missing"
        )

    assert response.status_code == 200
    assert response.json() == queue
    mock_onboarding.assert_called_once_with(
        query="bea",
        limit=10,
        onboarding_state="awaitingcontribution",
        onboarder="michael",
        discord=None,
        email_508=None,
        resume=None,
        skills="missing",
    )


def test_dashboard_assign_onboarder_updates_crm_and_audits(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["discord_admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )
    espo_client = Mock()
    espo_client.request.side_effect = [
        {
            "id": "contact-prospect-1",
            "name": "Bea Prospect",
            "cOnboarder": "none",
            "cOnboardingState": "pending",
        },
        {"id": "contact-prospect-1"},
    ]

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._is_dashboard_onboarding_contact_eligible",
            return_value=True,
        ),
        patch("five08.backend.api.EspoClient", return_value=espo_client),
        patch(
            "five08.backend.api.enqueue_job", return_value=Mock(id="sync-job-1")
        ) as mock_enqueue,
        patch(
            "five08.backend.api._write_auth_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        response = client.post(
            "/dashboard/api/onboarding/contact-prospect-1/onboarder",
            json={"onboarder": "Jane@508.dev"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contact_id"] == "contact-prospect-1"
    assert payload["contact_name"] == "Bea Prospect"
    assert payload["onboarder"] == "jane"
    assert payload["previous_state"] == "pending"
    assert payload["onboarding_state"] == "selected"
    assert payload["state_updated"] is True
    assert payload["sync_job_id"] == "sync-job-1"
    assert espo_client.request.call_args_list[0].args == (
        "GET",
        "Contact/contact-prospect-1",
    )
    assert espo_client.request.call_args_list[1].args == (
        "PUT",
        "Contact/contact-prospect-1",
        {"cOnboarder": "jane", "cOnboardingState": "selected"},
    )
    assert mock_enqueue.call_args.kwargs["args"] == ("contact-prospect-1",)
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["action"] == "crm.assign_onboarder"
    assert audit_kwargs["result"] == api.AuditResult.SUCCESS
    assert audit_kwargs["actor_provider"] == api.ActorProvider.DISCORD
    assert audit_kwargs["actor_subject"] == "123456789"
    assert audit_kwargs["resource_id"] == "contact-prospect-1"
    assert audit_kwargs["metadata"]["onboarder"] == "jane"


def test_dashboard_assign_onboarder_rejects_ineligible_contact(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="steering-1",
        email="steering@508.dev",
        display_name="Steering User",
        groups=["Steering Committee"],
        is_admin=False,
        id_token="",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._is_dashboard_onboarding_contact_eligible",
            return_value=False,
        ),
        patch("five08.backend.api.EspoClient") as mock_espo_client,
        patch(
            "five08.backend.api._write_auth_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        response = client.post(
            "/dashboard/api/onboarding/contact-member-1/onboarder",
            json={"onboarder": "jane"},
        )

    assert response.status_code == 403
    assert response.json() == {"error": "contact_not_onboarding_eligible"}
    mock_espo_client.assert_not_called()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["action"] == "crm.assign_onboarder"
    assert audit_kwargs["result"] == api.AuditResult.ERROR
    assert audit_kwargs["metadata"]["reason"] == "contact_not_onboarding_eligible"


def test_dashboard_assign_onboarder_rejects_invalid_onboarder(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api.EspoClient") as mock_espo_client,
        patch(
            "five08.backend.api._write_auth_audit_event",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        response = client.post(
            "/dashboard/api/onboarding/contact-prospect-1/onboarder",
            json={"onboarder": "<@123456789>"},
        )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_onboarder"}
    mock_espo_client.assert_not_called()
    audit_kwargs = mock_audit.call_args.kwargs
    assert audit_kwargs["action"] == "crm.assign_onboarder"
    assert audit_kwargs["result"] == api.AuditResult.ERROR
    assert audit_kwargs["metadata"]["reason"] == "invalid_onboarder"


def test_dashboard_audit_events_returns_recent_events(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    events = [
        {
            "id": "event-1",
            "occurred_at": "2026-02-25T12:05:00+00:00",
            "source": "admin_dashboard",
            "action": "worker.job_rerun",
            "resource_type": "worker_job",
            "resource_id": "job-new-1",
            "result": "success",
            "actor_provider": "discord",
            "actor_subject": "123456789",
            "actor_display_name": "Discord Admin",
            "metadata": {"job_type": "sync_people_from_crm_job"},
        }
    ]

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._list_dashboard_audit_events",
            return_value=events,
        ) as mock_events,
    ):
        response = client.get("/dashboard/api/audit-events?limit=10")

    assert response.status_code == 200
    assert response.json() == events
    mock_events.assert_called_once_with(10)


def test_dashboard_agent_report_returns_admin_only_metrics(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    report = {
        "summary": {
            "total": 1,
            "handled": 0,
            "requires_confirmation": 0,
            "needs_clarification": 1,
            "unsupported": 1,
            "denied_or_failed": 0,
        },
        "status_counts": {"needs_clarification": 1},
        "intent_counts": {"unknown": 1},
        "planner_counts": {"unknown": 1},
        "recent_unsupported": [
            {
                "occurred_at": "2026-02-25T12:05:00+00:00",
                "actor": "Discord Admin",
                "message_sanitized": "are you there",
                "result": "success",
                "correlation_id": "message-1",
            }
        ],
    }

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._dashboard_agent_request_report",
            return_value=report,
        ) as mock_report,
    ):
        response = client.get("/dashboard/api/agent?limit=25")

    assert response.status_code == 200
    assert response.json() == report
    mock_report.assert_called_once_with(25)


def test_dashboard_agent_report_requires_audit_permission(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="steering-1",
        email="steering@508.dev",
        display_name="Steering User",
        groups=["Steering Committee"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
    )

    with patch(
        "five08.backend.api._current_session",
        new_callable=AsyncMock,
        return_value=("session-1", session),
    ):
        response = client.get("/dashboard/api/agent")

    assert response.status_code == 403


def test_dashboard_agent_report_shapes_only_sanitized_unsupported_messages() -> None:
    report = api._shape_dashboard_agent_request_report(
        [
            {
                "id": "event-1",
                "occurred_at": datetime(2026, 2, 25, 12, 5, tzinfo=timezone.utc),
                "result": "success",
                "actor_provider": "discord",
                "actor_subject": "123",
                "actor_display_name": "Discord Admin",
                "correlation_id": "message-1",
                "metadata": {
                    "status": "needs_clarification",
                    "intent": None,
                    "planner": None,
                    "requires_confirmation": False,
                    "reason": "unsupported_agent_request",
                    "improvement_log": True,
                    "message_sanitized": "look up info on [person]",
                    "message": "look up info on Michael Wu",
                },
            },
            {
                "id": "event-2",
                "occurred_at": datetime(2026, 2, 25, 12, 6, tzinfo=timezone.utc),
                "result": "success",
                "actor_provider": "discord",
                "actor_subject": "123",
                "actor_display_name": "Discord Admin",
                "metadata": {
                    "status": "executed",
                    "intent": "crm_search",
                    "planner": "heuristic",
                    "message": "find member Michael Wu",
                },
            },
        ]
    )

    assert report["summary"]["total"] == 2
    assert report["summary"]["handled"] == 1
    assert report["summary"]["unsupported"] == 1
    assert report["status_counts"] == {"needs_clarification": 1, "executed": 1}
    unsupported = report["recent_unsupported"]
    assert unsupported == [
        {
            "id": "event-1",
            "occurred_at": "2026-02-25T12:05:00+00:00",
            "actor": "Discord Admin",
            "message_sanitized": "look up info on [person]",
            "result": "success",
            "correlation_id": "message-1",
        }
    ]
    assert "Michael Wu" not in str(report)


def test_dashboard_rerun_crm_job_audits_discord_session(
    client: TestClient,
) -> None:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["discord_admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
        crm_contact_id="contact-123",
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._rerun_job",
            new_callable=AsyncMock,
            return_value=(
                {
                    "status": "queued",
                    "source_job_id": "job-old-1",
                    "job_id": "job-new-1",
                    "type": "sync_people_from_crm_job",
                    "created": True,
                },
                202,
            ),
        ),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.post("/dashboard/api/jobs/job-old-1/rerun")

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-new-1"
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.source == api.AuditSource.ADMIN_DASHBOARD
    assert audit_payload.action == "worker.job_rerun"
    assert audit_payload.result == api.AuditResult.SUCCESS
    assert audit_payload.actor_provider == api.ActorProvider.DISCORD
    assert audit_payload.actor_subject == "123456789"
    assert audit_payload.actor_display_name == "Discord Admin"
    assert audit_payload.resource_type == "worker_job"
    assert audit_payload.resource_id == "job-new-1"
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["source"] == "dashboard"
    assert audit_payload.metadata["source_job_id"] == "job-old-1"
    assert audit_payload.metadata["job_type"] == "sync_people_from_crm_job"


def test_dashboard_rerun_rejects_cross_origin_post(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch("five08.backend.api._rerun_job", new_callable=AsyncMock) as mock_rerun,
    ):
        response = client.post(
            "/dashboard/api/jobs/job-old-1/rerun",
            headers={"Origin": "https://evil.example.invalid"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "csrf_check_failed"
    mock_rerun.assert_not_called()


def test_dashboard_sync_people_audits_discord_session(client: TestClient) -> None:
    session = api.AuthSession(
        subject="123456789",
        email="admin@508.dev",
        display_name="Discord Admin",
        groups=["discord_admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
        actor_provider=api.ActorProvider.DISCORD.value,
        crm_contact_id="contact-123",
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._enqueue_full_crm_sync_job",
            new_callable=AsyncMock,
            return_value=Mock(id="job-sync-1", created=True),
        ),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.post("/dashboard/api/sync/people")

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-sync-1"
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.source == api.AuditSource.ADMIN_DASHBOARD
    assert audit_payload.action == "crm.people_sync"
    assert audit_payload.result == api.AuditResult.SUCCESS
    assert audit_payload.actor_provider == api.ActorProvider.DISCORD
    assert audit_payload.actor_subject == "123456789"
    assert audit_payload.actor_display_name == "Discord Admin"
    assert audit_payload.resource_type == "crm_people_sync"
    assert audit_payload.resource_id == "job-sync-1"
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["source"] == "dashboard"


def test_dashboard_sync_people_rejects_cross_origin_post(client: TestClient) -> None:
    session = api.AuthSession(
        subject="admin-1",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admins"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._enqueue_full_crm_sync_job",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        response = client.post(
            "/dashboard/api/sync/people",
            headers={"Origin": "https://evil.example.invalid"},
        )

    assert response.status_code == 403
    assert response.json()["error"] == "csrf_check_failed"
    mock_enqueue.assert_not_called()


def test_auth_discord_link_create_forbidden_for_non_admin(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    fake_store = _FakeAuthStore()
    fake_verifier = Mock()
    fake_verifier.is_dashboard_discord_user = AsyncMock(return_value=False)

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=fake_store),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=fake_verifier,
        ),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
    ):
        response = client.post(
            "/auth/discord/links",
            json={"discord_user_id": "123456"},
            headers=auth_headers,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "discord_user_not_allowed"


def test_auth_discord_link_create_returns_url_for_admin(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    monkeypatch.setattr(
        api.settings, "dashboard_public_base_url", "https://dash.508.dev"
    )
    fake_store = _FakeAuthStore()
    fake_verifier = Mock()
    fake_verifier.is_dashboard_discord_user = AsyncMock(return_value=True)

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=fake_store),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=fake_verifier,
        ),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
    ):
        response = client.post(
            "/auth/discord/links",
            json={"discord_user_id": "123456", "next_path": "/jobs/abc"},
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 201
    assert payload["status"] == "created"
    assert payload["link_url"].startswith("https://dash.508.dev/auth/discord/link/")


def test_auth_callback_success_writes_login_audit(client: TestClient) -> None:
    store = Mock()
    store.pop_oidc_state = AsyncMock(
        return_value=api.PendingOIDCState(
            nonce="nonce-1",
            code_verifier="verifier-1",
            next_path="/dashboard",
            discord_link_token=None,
        )
    )
    store.save_session = AsyncMock()

    oidc = Mock()
    oidc.configured = True
    oidc.exchange_code = AsyncMock(return_value={"id_token": "id-token-1"})
    oidc.validate_id_token = AsyncMock(
        return_value={
            "sub": "authentik-user-1",
            "email": "Admin@508.dev",
            "name": "Admin User",
            "groups": ["Admin"],
            "exp": 4_102_444_800,
        }
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch("five08.backend.api._oidc_client_from_app", return_value=oidc),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get(
            "/auth/callback?code=code-1&state=state-1",
            follow_redirects=False,
        )

    assert response.status_code == 302
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "auth.login"
    assert audit_payload.result == api.AuditResult.SUCCESS
    assert audit_payload.source == api.AuditSource.ADMIN_DASHBOARD
    assert audit_payload.actor_provider == api.ActorProvider.ADMIN_SSO
    assert audit_payload.actor_subject == "admin@508.dev"
    assert audit_payload.metadata is not None
    assert "discord_link_identity_checks_enforced" not in audit_payload.metadata


def test_auth_callback_denied_writes_login_audit(client: TestClient) -> None:
    store = Mock()
    store.pop_oidc_state = AsyncMock(
        return_value=api.PendingOIDCState(
            nonce="nonce-1",
            code_verifier="verifier-1",
            next_path="/dashboard",
            discord_link_token="link-1",
        )
    )
    store.get_discord_link = AsyncMock(
        return_value=api.DiscordLinkGrant(
            discord_user_id="123456789",
            next_path="/dashboard",
        )
    )

    oidc = Mock()
    oidc.configured = True
    oidc.exchange_code = AsyncMock(return_value={"id_token": "id-token-1"})
    oidc.validate_id_token = AsyncMock(
        return_value={
            "sub": "authentik-user-2",
            "email": "member@508.dev",
            "name": "Member User",
            "groups": ["Member"],
            "exp": 4_102_444_800,
        }
    )
    verifier = Mock()
    verifier.is_dashboard_email_for_discord_user = AsyncMock(return_value=False)

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch("five08.backend.api._oidc_client_from_app", return_value=oidc),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=verifier,
        ),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get(
            "/auth/callback?code=code-1&state=state-1",
            follow_redirects=False,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "oidc_user_not_linked_to_discord_dashboard_user"
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "auth.login"
    assert audit_payload.result == api.AuditResult.DENIED
    assert audit_payload.actor_subject == "member@508.dev"


def test_auth_callback_discord_link_uses_discord_session_after_oidc_checks(
    client: TestClient,
) -> None:
    store = Mock()
    store.pop_oidc_state = AsyncMock(
        return_value=api.PendingOIDCState(
            nonce="nonce-1",
            code_verifier="verifier-1",
            next_path="/dashboard",
            discord_link_token="link-1",
        )
    )
    store.get_discord_link = AsyncMock(
        return_value=api.DiscordLinkGrant(
            discord_user_id="123456789",
            next_path="/dashboard",
        )
    )
    store.delete_discord_link = AsyncMock()
    store.save_session = AsyncMock()
    verifier = Mock()
    verifier.is_dashboard_email_for_discord_user = AsyncMock(return_value=True)
    verifier.resolve_dashboard_identity = AsyncMock(
        return_value=Mock(
            discord_user_id="123456789",
            crm_contact_id="contact-123",
            email="admin@508.dev",
            display_name="Discord Admin",
            discord_roles=["Admin"],
        )
    )

    oidc = Mock()
    oidc.configured = True
    oidc.exchange_code = AsyncMock(return_value={"id_token": "id-token-1"})
    oidc.validate_id_token = AsyncMock(
        return_value={
            "sub": "authentik-user-3",
            "email": "Admin@508.dev",
            "name": "OIDC Admin",
            "groups": ["Member"],
            "exp": 4_102_444_800,
        }
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch("five08.backend.api._oidc_client_from_app", return_value=oidc),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=verifier,
        ),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get(
            "/auth/callback?code=code-1&state=state-1",
            follow_redirects=False,
        )

    assert response.status_code == 302
    saved_session = store.save_session.call_args.kwargs["payload"]
    assert saved_session.subject == "123456789"
    assert saved_session.actor_provider == api.ActorProvider.DISCORD.value
    assert saved_session.crm_contact_id == "contact-123"
    assert saved_session.email == "admin@508.dev"
    assert saved_session.display_name == "Discord Admin"
    assert saved_session.id_token == "id-token-1"
    assert saved_session.groups == ["Admin"]
    assert saved_session.is_admin is True
    assert "people:read" in saved_session.permissions
    assert "onboarding:write" in saved_session.permissions
    assert "jobs:write" in saved_session.permissions
    store.delete_discord_link.assert_awaited_once_with("link-1")
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.actor_provider == api.ActorProvider.DISCORD
    assert audit_payload.actor_subject == "123456789"
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["discord_link_identity_checks_enforced"] is True


def test_auth_callback_discord_link_can_skip_oidc_identity_checks(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(
        api.settings, "discord_link_require_oidc_identity_checks", False
    )
    store = Mock()
    store.pop_oidc_state = AsyncMock(
        return_value=api.PendingOIDCState(
            nonce="nonce-1",
            code_verifier="verifier-1",
            next_path="/dashboard",
            discord_link_token="link-1",
        )
    )
    store.get_discord_link = AsyncMock(
        return_value=api.DiscordLinkGrant(
            discord_user_id="123456789",
            next_path="/dashboard",
        )
    )
    store.delete_discord_link = AsyncMock()
    store.save_session = AsyncMock()
    verifier = Mock()
    verifier.resolve_dashboard_identity = AsyncMock(
        return_value=Mock(
            discord_user_id="123456789",
            crm_contact_id="contact-123",
            email="steering@508.dev",
            display_name="Steering User",
            discord_roles=["Steering Committee"],
        )
    )

    oidc = Mock()
    oidc.configured = True
    oidc.exchange_code = AsyncMock(return_value={"id_token": "id-token-1"})
    oidc.validate_id_token = AsyncMock(
        return_value={
            "sub": "authentik-user-4",
            "name": "Bootstrap User",
            "groups": ["not-admin-yet"],
            "exp": 4_102_444_800,
        }
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch("five08.backend.api._oidc_client_from_app", return_value=oidc),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=verifier,
        ),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get(
            "/auth/callback?code=code-1&state=state-1",
            follow_redirects=False,
        )

    assert response.status_code == 302
    saved_session = store.save_session.call_args.kwargs["payload"]
    assert saved_session.is_admin is False
    assert saved_session.groups == ["Steering Committee"]
    assert "onboarding:write" in saved_session.permissions
    assert "jobs:write" not in saved_session.permissions
    store.delete_discord_link.assert_awaited_once_with("link-1")
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["via_discord_link"] is True
    assert audit_payload.metadata["discord_link_identity_checks_enforced"] is False


def test_auth_discord_link_redirect_creates_discord_session_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(api.settings, "environment", "production")
    monkeypatch.setattr(
        api.settings, "discord_link_require_oidc_identity_checks", False
    )
    store = Mock()
    store.get_discord_link = AsyncMock(
        return_value=api.DiscordLinkGrant(
            discord_user_id="123456789",
            next_path="/dashboard",
        )
    )
    store.save_session = AsyncMock()
    store.delete_discord_link = AsyncMock()
    verifier = Mock()
    verifier.resolve_dashboard_identity = AsyncMock(
        return_value=Mock(
            discord_user_id="123456789",
            crm_contact_id="contact-123",
            email="admin@508.dev",
            display_name="Discord Admin",
            discord_roles=["Admin"],
        )
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=verifier,
        ),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get("/auth/discord/link/link-1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    store.delete_discord_link.assert_awaited_once_with("link-1")
    saved_session = store.save_session.call_args.kwargs["payload"]
    assert saved_session.subject == "123456789"
    assert saved_session.actor_provider == api.ActorProvider.DISCORD.value
    assert saved_session.crm_contact_id == "contact-123"
    assert saved_session.email == "admin@508.dev"
    assert saved_session.is_admin is True
    assert "people:read" in saved_session.permissions
    assert "onboarding:write" in saved_session.permissions
    assert "jobs:write" not in saved_session.permissions
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.actor_provider == api.ActorProvider.DISCORD
    assert audit_payload.actor_subject == "123456789"
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["discord_link_identity_checks_enforced"] is False


def test_auth_discord_link_redirect_upgrades_existing_oidc_session(
    client: TestClient,
) -> None:
    store = Mock()
    store.get_discord_link = AsyncMock(
        return_value=api.DiscordLinkGrant(
            discord_user_id="123456789",
            next_path="/dashboard",
        )
    )
    store.save_session = AsyncMock()
    store.delete_discord_link = AsyncMock()
    session = api.AuthSession(
        subject="authentik-user-1",
        email="steering@508.dev",
        display_name="OIDC Steering",
        groups=["Member"],
        is_admin=False,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )
    verifier = Mock()
    verifier.is_dashboard_email_for_discord_user = AsyncMock(return_value=True)
    verifier.resolve_dashboard_identity = AsyncMock(
        return_value=Mock(
            discord_user_id="123456789",
            crm_contact_id="contact-123",
            email="steering@508.dev",
            display_name="Discord Steering",
            discord_roles=["Steering Committee"],
        )
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch(
            "five08.backend.api._current_session",
            new_callable=AsyncMock,
            return_value=("session-1", session),
        ),
        patch(
            "five08.backend.api._discord_admin_verifier_from_app",
            return_value=verifier,
        ),
        patch("five08.backend.api._http_client_from_app", return_value=Mock()),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.get("/auth/discord/link/link-1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    store.save_session.assert_awaited_once()
    saved_session = store.save_session.call_args.kwargs["payload"]
    assert store.save_session.call_args.kwargs["session_id"] == "session-1"
    assert saved_session.subject == "123456789"
    assert saved_session.actor_provider == api.ActorProvider.DISCORD.value
    assert saved_session.crm_contact_id == "contact-123"
    assert saved_session.id_token == "id-token-1"
    assert saved_session.is_admin is False
    assert "onboarding:write" in saved_session.permissions
    assert "jobs:write" not in saved_session.permissions
    store.delete_discord_link.assert_awaited_once_with("link-1")
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.actor_provider == api.ActorProvider.DISCORD
    assert audit_payload.actor_subject == "123456789"
    assert audit_payload.metadata is not None
    assert audit_payload.metadata["upgraded_existing_session"] is True


def test_auth_logout_writes_logout_audit(client: TestClient) -> None:
    store = Mock()
    store.delete_session = AsyncMock()
    session = api.AuthSession(
        subject="authentik-user-3",
        email="admin@508.dev",
        display_name="Admin User",
        groups=["Admin"],
        is_admin=True,
        id_token="id-token-1",
        expires_at=4_102_444_800,
    )

    with (
        patch(
            "five08.backend.api._current_session", return_value=("session-1", session)
        ),
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch("five08.backend.api.insert_audit_event") as mock_insert,
    ):
        response = client.post("/auth/logout")

    assert response.status_code == 200
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "auth.logout"
    assert audit_payload.result == api.AuditResult.SUCCESS
    assert audit_payload.actor_subject == "admin@508.dev"


# -- Docuseal webhook tests --------------------------------------------------

_DOCUSEAL_PAYLOAD = {
    "event_type": "form.completed",
    "timestamp": "2026-02-25T12:00:00Z",
    "data": {
        "id": 42,
        "submission_id": 4200,
        "email": "member@508.dev",
        "status": "completed",
        "completed_at": "2026-02-25T12:00:00Z",
        "name": "Jane Doe",
        "template": {"id": 68},
    },
}


_GOOGLE_FORMS_INTAKE_PAYLOAD = {
    "email": "member@example.com",
    "first_name": "Jane",
    "last_name": "Doe",
    "phone": "+15551234567",
    "discord_username": "janedoe",
    "linkedin_url": "https://linkedin.com/in/member",
    "github_username": "janedoe-github",
    "submission_id": "sub-42",
    "submitted_at": "2026-02-25T12:00:00Z",
}


def test_docuseal_webhook_rejects_unauthorized(client: TestClient) -> None:
    """Docuseal webhook should reject requests without valid auth."""
    response = client.post("/webhooks/docuseal", json=_DOCUSEAL_PAYLOAD)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_docuseal_webhook_enqueues_agreement_job(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid form.completed payload should enqueue agreement job."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-ds-1")
        response = client.post(
            "/webhooks/docuseal",
            json=_DOCUSEAL_PAYLOAD,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["source"] == "docuseal"
    assert payload["job_id"] == "job-ds-1"
    assert payload["masked_email"] == mask_email("member@508.dev")
    assert payload["submission_id"] == 4200

    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["args"] == ("member@508.dev", "2026-02-25 12:00:00", 4200)
    assert call_kwargs["args"][1] == "2026-02-25 12:00:00"
    assert call_kwargs["idempotency_key"] == "docuseal-agreement:4200"


def test_docuseal_webhook_converts_completed_at_to_utc_payload_contract(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docuseal timestamps should be serialized as UTC string contract payload args."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "data": {
            **_DOCUSEAL_PAYLOAD["data"],
            "completed_at": "2026-03-02T10:02:30.572+02:00",
        },
        "timestamp": "2026-03-02T10:02:30.572+02:00",
    }
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-ds-utc")
        response = client.post(
            "/webhooks/docuseal",
            json=payload,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["job_id"] == "job-ds-utc"
    assert payload["submission_id"] == 4200

    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["args"][1] == "2026-03-02 08:02:30"


def test_docuseal_webhook_ignored_when_template_filter_unset(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docuseal webhook should be ignored when template filter is unset."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        None,
    )
    with (
        patch("five08.backend.api.enqueue_job") as mock_enqueue,
        patch("five08.backend.api.logger.info") as mock_info,
    ):
        response = client.post(
            "/webhooks/docuseal",
            json=_DOCUSEAL_PAYLOAD,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ignored"
    assert payload["reason"] == "template_filter_not_configured"
    mock_enqueue.assert_not_called()
    assert mock_info.call_args.args[0].startswith(
        "Ignoring Docuseal agreement webhook: template filter is unset"
    )


def test_docuseal_webhook_rejects_invalid_payload(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Malformed payload should return 400."""
    response = client.post(
        "/webhooks/docuseal",
        json={"bad": "data"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


@pytest.mark.parametrize("email", ["", "  "])
def test_docuseal_webhook_rejects_blank_email(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    email: str,
) -> None:
    """Blank submitter email should be rejected."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "data": {**_DOCUSEAL_PAYLOAD["data"], "email": email},
    }
    response = client.post(
        "/webhooks/docuseal",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


@pytest.mark.parametrize("timestamp", ["", "   "])
def test_docuseal_webhook_rejects_blank_timestamp(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    timestamp: str,
) -> None:
    """Blank submitter completion time should be rejected."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "timestamp": timestamp,
        "data": {**_DOCUSEAL_PAYLOAD["data"], "completed_at": ""},
    }
    response = client.post(
        "/webhooks/docuseal",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_docuseal_webhook_ignores_unmatched_template(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhooks for non-target templates should be ignored when template filter is set."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        100,
    )
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "data": {
            **_DOCUSEAL_PAYLOAD["data"],
            "template": {"id": 101},
        },
    }
    response = client.post(
        "/webhooks/docuseal",
        json=payload,
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "template_mismatch"


def test_docuseal_webhook_processes_matching_template(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching template webhooks should still enqueue agreement jobs."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-ds-2")
        response = client.post(
            "/webhooks/docuseal",
            json=_DOCUSEAL_PAYLOAD,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["source"] == "docuseal"
    assert payload["job_id"] == "job-ds-2"
    assert payload["masked_email"] == mask_email("member@508.dev")
    assert payload["submission_id"] == 4200
    assert mock_enqueue.call_args.kwargs["idempotency_key"] == "docuseal-agreement:4200"


def test_docuseal_webhook_ignores_when_template_id_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Template-less payloads should be ignored when filter is configured."""
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "data": {
            **_DOCUSEAL_PAYLOAD["data"],
            "template": None,
        },
    }
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        response = client.post(
            "/webhooks/docuseal",
            json=payload,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ignored"
    assert payload["reason"] == "template_mismatch"
    mock_enqueue.assert_not_called()


def test_docuseal_webhook_uses_submitter_id_when_submission_id_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhooks without submission_id should fallback to submitter id for idempotency."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    payload = {
        **_DOCUSEAL_PAYLOAD,
        "data": {
            "id": 42,
            "email": "member@508.dev",
            "status": "completed",
            "completed_at": "2026-02-25T12:00:00Z",
            "template": {"id": 68},
        },
    }
    with patch("five08.backend.api.enqueue_job") as mock_enqueue:
        mock_enqueue.return_value = Mock(id="job-ds-4")
        response = client.post(
            "/webhooks/docuseal",
            json=payload,
            headers=auth_headers,
        )

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["source"] == "docuseal"
    assert payload["job_id"] == "job-ds-4"
    assert payload["masked_email"] == mask_email("member@508.dev")
    assert payload["submission_id"] == 42

    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["idempotency_key"] == "docuseal-agreement:42"


def test_docuseal_webhook_ignores_non_completed_event(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Non form.completed events should be acknowledged but ignored."""
    payload = {
        "event_type": "form.viewed",
        "timestamp": "2026-02-25T12:00:00Z",
        "data": {
            "id": 42,
            "email": "member@508.dev",
            "status": "pending",
        },
    }
    response = client.post(
        "/webhooks/docuseal",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_docuseal_webhook_returns_503_on_enqueue_failure(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enqueue failure should return 503."""
    monkeypatch.setattr(
        api.settings,
        "docuseal_member_agreement_template_id",
        68,
    )
    with patch(
        "five08.backend.api.enqueue_job",
        side_effect=RuntimeError("queue down"),
    ):
        response = client.post(
            "/webhooks/docuseal",
            json=_DOCUSEAL_PAYLOAD,
            headers=auth_headers,
        )
    assert response.status_code == 503
    assert response.json()["error"] == "enqueue_failed"


# --- Google Forms intake webhook ---


def test_google_forms_intake_rejects_unauthorized(client: TestClient) -> None:
    """Google Forms webhook should reject requests without auth."""
    response = client.post("/webhooks/google-forms", json={"email": "a@b.com"})
    assert response.status_code == 401


def test_google_forms_intake_enqueues_job(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Google Forms webhook should enqueue intake job and return 202."""
    with patch.object(api.settings, "google_forms_allowed_form_ids", "form-1,form-2"):
        with patch("five08.backend.api.enqueue_job") as mock_enqueue:
            mock_enqueue.return_value = Mock(id="job-intake-1")
            response = client.post(
                "/webhooks/google-forms",
                json={
                    **_GOOGLE_FORMS_INTAKE_PAYLOAD,
                    "email": "  member@example.com  ",
                    "first_name": "  Jane  ",
                    "last_name": "  Doe  ",
                    "form_id": "form-1",
                },
                headers=auth_headers,
            )

    payload = response.json()
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["source"] == "google_forms"
    assert payload["job_id"] == "job-intake-1"
    assert payload["email"] == "member@example.com"

    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["idempotency_key"] == "intake:member@example.com:sub-42"
    assert call_kwargs["args"][0]["email"] == "member@example.com"
    assert call_kwargs["args"][0]["first_name"] == "Jane"
    assert call_kwargs["args"][0]["last_name"] == "Doe"


def test_google_forms_intake_rejects_unapproved_form_id(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Unapproved Google Forms IDs should be rejected."""
    with patch.object(api.settings, "google_forms_allowed_form_ids", "form-1,form-2"):
        with patch("five08.backend.api.enqueue_job"):
            response = client.post(
                "/webhooks/google-forms",
                json={**_GOOGLE_FORMS_INTAKE_PAYLOAD, "form_id": "legacy-form"},
                headers=auth_headers,
            )

    assert response.status_code == 403
    assert response.json()["error"] == "invalid_form_id"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", ""),
        ("first_name", ""),
        ("last_name", ""),
        ("email", "   "),
        ("first_name", "   "),
        ("last_name", "   "),
    ],
)
def test_google_forms_intake_rejects_blank_required_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    field: str,
    value: str,
) -> None:
    """Blank required fields should be rejected after normalization."""
    payload = dict(_GOOGLE_FORMS_INTAKE_PAYLOAD)
    payload[field] = value

    response = client.post(
        "/webhooks/google-forms",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_google_forms_intake_idempotency_uses_submission_payload_fingerprint_when_submission_id_missing(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Repeated payloads without submission_id should share a stable idempotency key."""
    payload = {
        "email": "  member@example.com  ",
        "first_name": " Jane ",
        "last_name": " Doe ",
        "phone": " +15551234567 ",
        "submission_id": None,
        "submitted_at": None,
        "form_id": "form-1",
    }
    normalized_payload = api.GoogleFormsIntakePayload.model_validate(
        payload
    ).model_dump(exclude_none=True)
    expected_idempotency_key = api._google_forms_intake_idempotency_key(
        email="member@example.com",
        submission_id=None,
        submitted_at=None,
        payload=normalized_payload,
    )

    with patch.object(api.settings, "google_forms_allowed_form_ids", "form-1,form-2"):
        with patch("five08.backend.api.enqueue_job") as mock_enqueue:
            mock_enqueue.return_value = Mock(id="job-intake-1")
            response_one = client.post(
                "/webhooks/google-forms",
                json=payload,
                headers=auth_headers,
            )
            response_two = client.post(
                "/webhooks/google-forms",
                json=payload,
                headers=auth_headers,
            )

    assert response_one.status_code == 202
    assert response_one.json()["job_id"] == "job-intake-1"
    assert response_two.status_code == 202
    assert response_two.json()["job_id"] == "job-intake-1"

    call_kwargs_one = mock_enqueue.call_args_list[0].kwargs
    call_kwargs_two = mock_enqueue.call_args_list[1].kwargs
    assert call_kwargs_one["idempotency_key"] == expected_idempotency_key
    assert call_kwargs_two["idempotency_key"] == expected_idempotency_key
    assert call_kwargs_one["args"][0]["email"] == "member@example.com"
    assert call_kwargs_one["args"][0]["first_name"] == "Jane"
    assert call_kwargs_one["args"][0]["last_name"] == "Doe"


def test_google_forms_intake_rejects_invalid_payload(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Google Forms webhook should return 400 for invalid payloads."""
    response = client.post(
        "/webhooks/google-forms",
        json={"not_a_valid": "payload"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_payload"


def test_google_forms_intake_returns_503_on_enqueue_failure(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Google Forms webhook should return 503 when enqueue fails."""
    with patch("five08.backend.api.enqueue_job", side_effect=RuntimeError("boom")):
        response = client.post(
            "/webhooks/google-forms",
            json={
                "email": "fail@example.com",
                "first_name": "Test",
                "last_name": "User",
            },
            headers=auth_headers,
        )
    assert response.status_code == 503
    assert response.json()["error"] == "enqueue_failed"
