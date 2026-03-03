"""Unit tests for backend dashboard/ingest API."""

import re
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock, patch

from five08.backend import api
from five08.worker.masking import mask_email


class _HealthyRedis:
    def ping(self) -> bool:
        return True


class _FailingRedis:
    def ping(self) -> bool:
        raise RuntimeError("redis unavailable")


class _FakeAuthStore:
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


def test_auth_login_returns_503_when_store_not_ready(client: TestClient) -> None:
    response = client.get("/auth/login")
    assert response.status_code == 503
    assert response.json()["error"] == "auth_not_ready"


def test_auth_me_requires_session(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_auth_discord_link_create_forbidden_for_non_admin(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    fake_store = _FakeAuthStore()
    fake_verifier = Mock()
    fake_verifier.is_admin_discord_user = AsyncMock(return_value=False)

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
    assert response.json()["detail"] == "discord_user_not_admin"


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
    fake_verifier.is_admin_discord_user = AsyncMock(return_value=True)

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

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_group_required"
    audit_payload = mock_insert.call_args.args[1]
    assert audit_payload.action == "auth.login"
    assert audit_payload.result == api.AuditResult.DENIED
    assert audit_payload.actor_subject == "member@508.dev"


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
        patch("five08.backend.api.insert_audit_event"),
    ):
        response = client.get(
            "/auth/callback?code=code-1&state=state-1",
            follow_redirects=False,
        )

    assert response.status_code == 302
    saved_session = store.save_session.call_args.kwargs["payload"]
    assert saved_session.is_admin is True
    store.delete_discord_link.assert_awaited_once_with("link-1")


def test_auth_discord_link_redirect_skips_session_identity_checks_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
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
    store.delete_discord_link = AsyncMock()

    session = api.AuthSession(
        subject="authentik-user-5",
        email=None,
        display_name="Bootstrap User",
        groups=["Member"],
        is_admin=False,
        id_token="id-token-5",
        expires_at=4_102_444_800,
    )

    with (
        patch("five08.backend.api._auth_store_from_app", return_value=store),
        patch(
            "five08.backend.api._current_session", return_value=("session-5", session)
        ),
    ):
        response = client.get("/auth/discord/link/link-1", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"
    store.delete_discord_link.assert_awaited_once_with("link-1")


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
