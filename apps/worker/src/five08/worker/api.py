"""FastAPI dashboard + ingest API for enqueuing background jobs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal, cast
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ValidationError
from psycopg import Connection

from five08.audit import (
    ActorProvider,
    AuditEventInput,
    AuditResult,
    AuditSource,
    insert_audit_event,
)
from five08.logging import configure_logging
from five08.queue import (
    EnqueuedJob,
    QueueClient,
    enqueue_job,
    get_job,
    get_postgres_connection,
    get_redis_connection,
    is_postgres_healthy,
)
from five08.worker.auth import (
    AuthSession,
    DiscordAdminVerifier,
    DiscordLinkGrant,
    OIDCProviderClient,
    PendingOIDCState,
    RedisAuthStore,
    build_authorization_url,
    build_redirect_uri,
    extract_groups,
    is_admin_from_groups,
    make_pkce_pair,
    normalize_next_path,
)
from five08.worker.config import settings
from five08.worker.db_migrations import run_job_migrations
from five08.worker.dispatcher import build_queue_client
from five08.worker.jobs import (
    apply_resume_profile_job,
    extract_resume_profile_job,
    process_contact_skills_job,
    process_webhook_event,
    sync_people_from_crm_job,
    sync_person_from_crm_job,
)
from five08.worker.mailbox_resume_ingest import ResumeMailboxProcessor
from five08.worker.models import AuditEventPayload, EspoCRMWebhookPayload

logger = logging.getLogger(__name__)


class ResumeExtractRequest(BaseModel):
    """Request schema for queued resume extraction."""

    contact_id: str
    attachment_id: str
    filename: str


class ResumeApplyRequest(BaseModel):
    """Request schema for queued resume apply updates."""

    contact_id: str
    updates: dict[str, str]
    link_discord: dict[str, str] | None = None


class DiscordLinkCreateRequest(BaseModel):
    """Payload for creating one-time admin deep links from Discord commands."""

    discord_user_id: str
    next_path: str | None = None


def _is_authorized(request: Request) -> bool:
    """Validate shared API secret."""
    if not settings.api_shared_secret:
        logger.error("Rejecting request: API_SHARED_SECRET is not configured")
        return False

    provided_secret = request.headers.get("X-API-Secret", "")
    return secrets.compare_digest(provided_secret, settings.api_shared_secret)


def _extract_idempotency_key(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resume_extract_model_name() -> str:
    if settings.openai_api_key:
        return settings.resolved_resume_ai_model
    return "heuristic"


def _crm_sync_idempotency_key(*, now: datetime) -> str:
    interval_seconds = max(1, settings.crm_sync_interval_seconds)
    bucket = int(now.timestamp()) // interval_seconds
    return f"crm-sync:{bucket}"


async def _enqueue_full_crm_sync_job(queue: QueueClient, *, reason: str) -> EnqueuedJob:
    now = datetime.now(tz=timezone.utc)
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=sync_people_from_crm_job,
        args=(),
        settings=settings,
        idempotency_key=_crm_sync_idempotency_key(now=now),
    )
    logger.info(
        "Enqueued CRM people full-sync job id=%s created=%s reason=%s",
        job.id,
        job.created,
        reason,
    )
    return job


async def _crm_sync_scheduler(app: FastAPI) -> None:
    queue = app.state.queue
    interval_seconds = max(1, settings.crm_sync_interval_seconds)
    while True:
        try:
            await _enqueue_full_crm_sync_job(queue, reason="scheduler")
        except Exception:
            logger.exception("Failed scheduling CRM full-sync job")
        await asyncio.sleep(interval_seconds)


async def _email_resume_scheduler() -> None:
    """Run periodic mailbox polling for resume ingestion."""
    poller = ResumeMailboxProcessor(settings)
    interval_seconds = max(1, settings.check_email_wait) * 60
    while True:
        try:
            processed_count = await asyncio.to_thread(poller.poll_inbox)
            logger.debug(
                "Completed mailbox resume poll processed_attachments=%s",
                processed_count,
            )
        except Exception:
            logger.exception("Failed mailbox resume poll iteration")
        await asyncio.sleep(interval_seconds)


def _check_postgres_connection(connection: Connection) -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


async def _is_postgres_connection_healthy(app: FastAPI) -> bool:
    lock = app.state.postgres_conn_lock
    async with lock:
        connection = app.state.postgres_conn
        healthy = await asyncio.to_thread(_check_postgres_connection, connection)
        if healthy:
            return True

        with contextlib.suppress(Exception):
            await asyncio.to_thread(connection.close)

        try:
            refreshed = await asyncio.to_thread(get_postgres_connection, settings)
        except Exception:
            return False

        app.state.postgres_conn = refreshed
        return await asyncio.to_thread(_check_postgres_connection, refreshed)


def _enqueue_espocrm_batch_sync(queue: QueueClient, event_ids: list[str]) -> None:
    for event_id in event_ids:
        enqueue_job(
            queue=queue,
            fn=process_contact_skills_job,
            args=(event_id,),
            settings=settings,
            idempotency_key=f"espocrm:{event_id}",
        )


async def _enqueue_espocrm_batch(queue: QueueClient, event_ids: list[str]) -> None:
    await asyncio.to_thread(_enqueue_espocrm_batch_sync, queue, event_ids)


def _enqueue_espocrm_people_sync_batch_sync(
    queue: QueueClient, event_ids: list[str], *, bucket: str
) -> None:
    for event_id in event_ids:
        enqueue_job(
            queue=queue,
            fn=sync_person_from_crm_job,
            args=(event_id,),
            settings=settings,
            idempotency_key=f"crm-contact-sync:{event_id}:{bucket}",
        )


async def _enqueue_espocrm_people_sync_batch(
    queue: QueueClient, event_ids: list[str], *, bucket: str
) -> None:
    await asyncio.to_thread(
        _enqueue_espocrm_people_sync_batch_sync, queue, event_ids, bucket=bucket
    )


def _auth_store_from_app(app: FastAPI) -> RedisAuthStore | None:
    store = getattr(app.state, "auth_store", None)
    if isinstance(store, RedisAuthStore):
        return store
    return None


def _oidc_client_from_app(app: FastAPI) -> OIDCProviderClient:
    client = getattr(app.state, "oidc_client", None)
    if isinstance(client, OIDCProviderClient):
        return client
    raise RuntimeError("OIDC client not configured")


def _discord_admin_verifier_from_app(app: FastAPI) -> DiscordAdminVerifier:
    verifier = getattr(app.state, "discord_admin_verifier", None)
    if isinstance(verifier, DiscordAdminVerifier):
        return verifier
    raise RuntimeError("Discord verifier not configured")


def _http_client_from_app(app: FastAPI) -> httpx.AsyncClient:
    client = getattr(app.state, "http_client", None)
    if isinstance(client, httpx.AsyncClient):
        return client
    raise RuntimeError("HTTP client not configured")


async def _current_session(request: Request) -> tuple[str | None, AuthSession | None]:
    store = _auth_store_from_app(request.app)
    if store is None:
        return None, None

    session_id = request.cookies.get(settings.auth_session_cookie_name)
    if not session_id:
        return None, None

    session = await store.get_session(session_id)
    if session is None:
        return session_id, None

    return session_id, session


def _set_session_cookie(
    response: JSONResponse | RedirectResponse, session_id: str
) -> None:
    samesite = cast(
        Literal["lax", "strict", "none"],
        settings.auth_cookie_samesite,
    )
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=session_id,
        max_age=max(1, settings.auth_session_ttl_seconds),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=samesite,
        path="/",
    )


def _clear_session_cookie(response: JSONResponse | RedirectResponse) -> None:
    response.delete_cookie(key=settings.auth_session_cookie_name, path="/")


async def _write_auth_audit_event(
    *,
    action: str,
    result: AuditResult,
    actor_subject: str,
    actor_display_name: str | None = None,
    actor_provider: ActorProvider = ActorProvider.ADMIN_SSO,
    metadata: dict[str, Any] | None = None,
    resource_type: str | None = "auth_session",
    resource_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """Best-effort auth audit write that never breaks request flow."""
    subject = actor_subject.strip()
    if not subject:
        return

    try:
        await asyncio.to_thread(
            insert_audit_event,
            settings,
            AuditEventInput(
                source=AuditSource.ADMIN_DASHBOARD,
                action=action,
                result=result,
                actor_provider=actor_provider,
                actor_subject=subject,
                actor_display_name=actor_display_name,
                resource_type=resource_type,
                resource_id=resource_id,
                correlation_id=correlation_id,
                metadata=metadata or {},
            ),
        )
    except Exception:
        logger.warning(
            "Best-effort auth audit write failed action=%s actor_subject=%s",
            action,
            subject,
            exc_info=True,
        )


async def health_handler(request: Request) -> JSONResponse:
    """Simple health endpoint."""
    redis_conn = request.app.state.redis_conn

    try:
        redis_ok = bool(await asyncio.to_thread(redis_conn.ping))
    except Exception:
        redis_ok = False

    if hasattr(request.app.state, "postgres_conn"):
        postgres_ok = await _is_postgres_connection_healthy(request.app)
    else:
        postgres_ok = await asyncio.to_thread(is_postgres_healthy, settings)

    payload = {
        "status": "healthy" if redis_ok and postgres_ok else "degraded",
        "redis_connected": redis_ok,
        "postgres_connected": postgres_ok,
        "queue_name": settings.redis_queue_name,
    }
    return JSONResponse(payload, status_code=200 if redis_ok and postgres_ok else 503)


async def ingest_handler(request: Request, source: str) -> JSONResponse:
    """Validate and enqueue incoming webhook payloads."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"error": "payload_must_be_object"}, status_code=400)

    queue = request.app.state.queue
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=process_webhook_event,
        args=(source, payload),
        settings=settings,
        idempotency_key=_extract_idempotency_key(payload.get("id")),
    )

    logger.info("Enqueued webhook job %s from source=%s", job.id, source)
    return JSONResponse(
        {
            "status": "queued",
            "job_id": job.id,
            "queue": settings.redis_queue_name,
            "source": source,
        },
        status_code=202,
    )


async def espocrm_webhook_handler(request: Request) -> JSONResponse:
    """Validate EspoCRM webhook payload and enqueue per-contact jobs."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, list):
        return JSONResponse(
            {"error": "payload_must_be_array_of_events"}, status_code=400
        )

    try:
        payload = EspoCRMWebhookPayload.from_list(payload_data)
    except (ValidationError, TypeError) as exc:
        return JSONResponse(
            {"error": "invalid_webhook_event", "detail": str(exc)},
            status_code=400,
        )

    event_ids = [event.id for event in payload.events]
    deduped_event_ids = list(dict.fromkeys(event_ids))
    queue = request.app.state.queue
    try:
        await _enqueue_espocrm_batch(queue, deduped_event_ids)
    except Exception:
        logger.exception(
            "Failed enqueueing EspoCRM webhook events count=%s queue=%s",
            len(deduped_event_ids),
            settings.redis_queue_name,
        )
        return JSONResponse({"error": "enqueue_failed"}, status_code=503)

    logger.info(
        "Enqueued %s EspoCRM webhook events queue=%s",
        len(deduped_event_ids),
        settings.redis_queue_name,
    )
    return JSONResponse(
        {
            "status": "queued",
            "source": "espocrm",
            "events_received": len(deduped_event_ids),
            "events_enqueued": len(deduped_event_ids),
        },
        status_code=202,
    )


async def process_contact_handler(request: Request, contact_id: str) -> JSONResponse:
    """Manual enqueue for one contact."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    normalized_contact_id = contact_id.strip()
    if not normalized_contact_id:
        return JSONResponse({"error": "contact_id_required"}, status_code=400)

    queue = request.app.state.queue
    manual_nonce = datetime.now(tz=timezone.utc).isoformat()
    nonce_suffix = uuid4().hex[:12]
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=process_contact_skills_job,
        args=(normalized_contact_id,),
        settings=settings,
        idempotency_key=f"manual:{normalized_contact_id}:{manual_nonce}:{nonce_suffix}",
    )
    logger.info(
        "Enqueued manual contact job job_id=%s contact_id=%s created=%s",
        job.id,
        normalized_contact_id,
        job.created,
    )
    return JSONResponse(
        {
            "status": "queued",
            "source": "manual",
            "contact_id": normalized_contact_id,
            "job_id": job.id,
        },
        status_code=202,
    )


async def resume_extract_handler(request: Request) -> JSONResponse:
    """Enqueue resume extraction job for one uploaded attachment."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        payload = ResumeExtractRequest.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_resume_extract_payload", "detail": str(exc)},
            status_code=400,
        )

    queue = request.app.state.queue
    model_name = _resume_extract_model_name()
    idempotency_key = (
        f"resume-extract:{payload.contact_id}:{payload.attachment_id}:"
        f"{settings.resume_extractor_version}:{model_name}"
    )
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=extract_resume_profile_job,
        args=(payload.contact_id, payload.attachment_id, payload.filename),
        settings=settings,
        idempotency_key=idempotency_key,
    )
    logger.info(
        "Enqueued resume extract job contact_id=%s attachment_id=%s job_id=%s created=%s",
        payload.contact_id,
        payload.attachment_id,
        job.id,
        job.created,
    )
    return JSONResponse(
        {
            "status": "queued",
            "job_id": job.id,
            "contact_id": payload.contact_id,
            "attachment_id": payload.attachment_id,
            "created": job.created,
        },
        status_code=202,
    )


async def resume_apply_handler(request: Request) -> JSONResponse:
    """Enqueue CRM apply job after user confirmation in Discord."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        payload = ResumeApplyRequest.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_resume_apply_payload", "detail": str(exc)},
            status_code=400,
        )

    queue = request.app.state.queue
    manual_nonce = datetime.now(tz=timezone.utc).isoformat()
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=apply_resume_profile_job,
        args=(payload.contact_id, payload.updates, payload.link_discord),
        settings=settings,
        idempotency_key=f"resume-apply:{payload.contact_id}:{manual_nonce}",
    )
    logger.info(
        "Enqueued resume apply job contact_id=%s job_id=%s created=%s",
        payload.contact_id,
        job.id,
        job.created,
    )
    return JSONResponse(
        {
            "status": "queued",
            "job_id": job.id,
            "contact_id": payload.contact_id,
        },
        status_code=202,
    )


async def job_status_handler(request: Request, job_id: str) -> JSONResponse:
    """Return persisted status and worker result payload for one job."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    normalized_job_id = job_id.strip()
    if not normalized_job_id:
        return JSONResponse({"error": "job_id_required"}, status_code=400)

    job = await asyncio.to_thread(get_job, settings, normalized_job_id)
    if job is None:
        return JSONResponse({"error": "job_not_found"}, status_code=404)

    result: Any = None
    payload = job.payload if isinstance(job.payload, dict) else {}
    if "result" in payload:
        result = payload["result"]

    return JSONResponse(
        {
            "job_id": job.id,
            "type": job.type,
            "status": job.status.value,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "result": result,
        }
    )


async def sync_people_handler(request: Request) -> JSONResponse:
    """Manual enqueue for a full CRM->people cache sync."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    queue = request.app.state.queue
    job = await _enqueue_full_crm_sync_job(queue, reason="manual")
    return JSONResponse(
        {
            "status": "queued",
            "source": "manual",
            "job_id": job.id,
            "created": job.created,
        },
        status_code=202,
    )


async def espocrm_people_sync_webhook_handler(request: Request) -> JSONResponse:
    """Queue per-contact people cache sync jobs from CRM webhook events."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, list):
        return JSONResponse(
            {"error": "payload_must_be_array_of_events"}, status_code=400
        )

    try:
        payload = EspoCRMWebhookPayload.from_list(payload_data)
    except (ValidationError, TypeError) as exc:
        return JSONResponse(
            {"error": "invalid_webhook_event", "detail": str(exc)},
            status_code=400,
        )

    event_ids = [event.id for event in payload.events]
    deduped_event_ids = list(dict.fromkeys(event_ids))
    queue = request.app.state.queue
    bucket = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M")
    try:
        await _enqueue_espocrm_people_sync_batch(
            queue, deduped_event_ids, bucket=bucket
        )
    except Exception:
        logger.exception(
            "Failed enqueueing EspoCRM people-sync events count=%s queue=%s",
            len(deduped_event_ids),
            settings.redis_queue_name,
        )
        return JSONResponse({"error": "enqueue_failed"}, status_code=503)

    return JSONResponse(
        {
            "status": "queued",
            "source": "espocrm_people_sync",
            "events_received": len(deduped_event_ids),
            "events_enqueued": len(deduped_event_ids),
        },
        status_code=202,
    )


async def audit_event_handler(request: Request) -> JSONResponse:
    """Persist one human audit event."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, dict):
        return JSONResponse({"error": "payload_must_be_object"}, status_code=400)

    try:
        payload = AuditEventPayload.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)}, status_code=400
        )

    try:
        created = await asyncio.to_thread(
            insert_audit_event,
            settings,
            AuditEventInput(
                source=AuditSource(payload.source),
                action=payload.action,
                result=AuditResult(payload.result),
                actor_provider=ActorProvider(payload.actor_provider),
                actor_subject=payload.actor_subject,
                resource_type=payload.resource_type,
                resource_id=payload.resource_id,
                actor_display_name=payload.actor_display_name,
                correlation_id=payload.correlation_id,
                metadata=payload.metadata,
                occurred_at=payload.occurred_at,
            ),
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)}, status_code=400
        )

    return JSONResponse(
        {
            "status": "created",
            "event_id": created.id,
            "person_id": created.person_id,
        },
        status_code=201,
    )


async def auth_login_handler(
    request: Request,
    next_path: str | None = Query(default=None, alias="next"),
    discord_link_token: str | None = Query(default=None),
) -> JSONResponse | RedirectResponse:
    """Start OIDC auth-code flow with PKCE and server-side state."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    oidc = _oidc_client_from_app(request.app)
    if not oidc.configured:
        return JSONResponse({"error": "oidc_not_configured"}, status_code=503)

    normalized_next_path = normalize_next_path(
        next_path,
        fallback=normalize_next_path(settings.dashboard_default_path),
    )

    if discord_link_token:
        grant = await store.get_discord_link(discord_link_token)
        if grant is None:
            return JSONResponse({"error": "link_not_found"}, status_code=404)

    code_verifier, code_challenge = make_pkce_pair()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    await store.save_oidc_state(
        state=state,
        payload=PendingOIDCState(
            nonce=nonce,
            code_verifier=code_verifier,
            next_path=normalized_next_path,
            discord_link_token=discord_link_token,
        ),
        ttl_seconds=settings.auth_state_ttl_seconds,
    )

    http_client = _http_client_from_app(request.app)
    metadata = await oidc.get_metadata(http_client)
    redirect_uri = build_redirect_uri(
        settings,
        request_base_url=str(request.base_url),
    )
    authorization_url = build_authorization_url(
        metadata,
        client_id=settings.oidc_client_id,
        redirect_uri=redirect_uri,
        scope=settings.oidc_scope,
        state=state,
        nonce=nonce,
        code_challenge=code_challenge,
    )
    return RedirectResponse(url=authorization_url, status_code=302)


async def auth_callback_handler(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> JSONResponse | RedirectResponse:
    """Handle OIDC callback, create server-side session cookie, and redirect."""
    if not code or not state:
        return JSONResponse({"error": "missing_code_or_state"}, status_code=400)

    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    oidc = _oidc_client_from_app(request.app)
    if not oidc.configured:
        return JSONResponse({"error": "oidc_not_configured"}, status_code=503)

    pending = await store.pop_oidc_state(state)
    if pending is None:
        return JSONResponse({"error": "invalid_state"}, status_code=400)

    http_client = _http_client_from_app(request.app)
    redirect_uri = build_redirect_uri(
        settings,
        request_base_url=str(request.base_url),
    )

    try:
        token_payload = await oidc.exchange_code(
            http_client,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=pending.code_verifier,
        )
    except Exception:
        logger.exception("OIDC token exchange failed")
        return JSONResponse({"error": "oidc_exchange_failed"}, status_code=502)

    id_token = token_payload.get("id_token")
    if not isinstance(id_token, str) or not id_token.strip():
        return JSONResponse({"error": "id_token_missing"}, status_code=400)

    try:
        claims = await oidc.validate_id_token(
            http_client,
            id_token=id_token,
            nonce=pending.nonce,
        )
    except Exception:
        logger.exception("OIDC token validation failed")
        return JSONResponse({"error": "invalid_id_token"}, status_code=401)

    groups = extract_groups(claims, claim_name=settings.oidc_groups_claim)
    is_admin = is_admin_from_groups(
        groups,
        configured_admin_groups=settings.oidc_admin_group_names,
    )

    raw_email = claims.get("email") or claims.get("preferred_username")
    email = str(raw_email).strip().lower() if raw_email else None
    if email == "":
        email = None

    raw_name = claims.get("name") or claims.get("preferred_username")
    display_name = str(raw_name).strip() if raw_name else None
    if display_name == "":
        display_name = None

    audit_actor_subject = (email or str(claims.get("sub", "")).strip()).strip()

    if pending.discord_link_token:
        grant = await store.get_discord_link(pending.discord_link_token)
        if grant is None:
            await _write_auth_audit_event(
                action="auth.login",
                result=AuditResult.DENIED,
                actor_subject=audit_actor_subject,
                actor_display_name=display_name,
                metadata={"reason": "discord_link_not_found"},
                correlation_id=state,
            )
            return JSONResponse({"error": "link_not_found"}, status_code=404)

        if not is_admin:
            await _write_auth_audit_event(
                action="auth.login",
                result=AuditResult.DENIED,
                actor_subject=audit_actor_subject,
                actor_display_name=display_name,
                metadata={"reason": "admin_group_required", "groups": groups},
                correlation_id=state,
            )
            return JSONResponse(
                {"error": "forbidden", "detail": "admin_group_required"},
                status_code=403,
            )

        if not email:
            await _write_auth_audit_event(
                action="auth.login",
                result=AuditResult.DENIED,
                actor_subject=audit_actor_subject,
                actor_display_name=display_name,
                metadata={"reason": "email_claim_required"},
                correlation_id=state,
            )
            return JSONResponse(
                {"error": "forbidden", "detail": "email_claim_required"},
                status_code=403,
            )

        verifier = _discord_admin_verifier_from_app(request.app)
        linked = await verifier.is_admin_email_for_discord_user(
            email=email,
            discord_user_id=grant.discord_user_id,
        )
        if not linked:
            await _write_auth_audit_event(
                action="auth.login",
                result=AuditResult.DENIED,
                actor_subject=audit_actor_subject,
                actor_display_name=display_name,
                metadata={
                    "reason": "oidc_user_not_linked_to_discord_admin",
                    "discord_user_id": grant.discord_user_id,
                },
                correlation_id=state,
            )
            return JSONResponse(
                {
                    "error": "forbidden",
                    "detail": "oidc_user_not_linked_to_discord_admin",
                },
                status_code=403,
            )

        await store.delete_discord_link(pending.discord_link_token)

    now = int(time.time())
    max_session_expiry = now + max(1, settings.auth_session_ttl_seconds)
    raw_exp = claims.get("exp")
    token_expiry = max_session_expiry
    if isinstance(raw_exp, int):
        token_expiry = raw_exp
    expires_at = min(token_expiry, max_session_expiry)

    session_id = secrets.token_urlsafe(32)
    await store.save_session(
        session_id=session_id,
        payload=AuthSession(
            subject=str(claims.get("sub", "")),
            email=email,
            display_name=display_name,
            groups=groups,
            is_admin=is_admin,
            id_token=id_token,
            expires_at=expires_at,
        ),
        ttl_seconds=settings.auth_session_ttl_seconds,
    )

    redirect_to = normalize_next_path(
        pending.next_path,
        fallback=normalize_next_path(settings.dashboard_default_path),
    )
    response = RedirectResponse(url=redirect_to, status_code=302)
    _set_session_cookie(response, session_id)
    await _write_auth_audit_event(
        action="auth.login",
        result=AuditResult.SUCCESS,
        actor_subject=audit_actor_subject,
        actor_display_name=display_name,
        metadata={
            "is_admin": is_admin,
            "groups": groups,
            "via_discord_link": bool(pending.discord_link_token),
        },
        resource_id=session_id,
        correlation_id=state,
    )
    return response


async def auth_me_handler(request: Request) -> JSONResponse:
    """Return current session payload for dashboard clients."""
    _, session = await _current_session(request)
    if session is None:
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        _clear_session_cookie(response)
        return response

    return JSONResponse(
        {
            "subject": session.subject,
            "email": session.email,
            "display_name": session.display_name,
            "groups": session.groups,
            "is_admin": session.is_admin,
            "expires_at": session.expires_at,
        }
    )


async def auth_logout_handler(request: Request) -> JSONResponse:
    """Clear server-side session and auth cookie."""
    session_id, session = await _current_session(request)
    store = _auth_store_from_app(request.app)
    if session_id and store is not None:
        await store.delete_session(session_id)

    if session is not None:
        await _write_auth_audit_event(
            action="auth.logout",
            result=AuditResult.SUCCESS,
            actor_subject=(session.email or session.subject),
            actor_display_name=session.display_name,
            metadata={"is_admin": session.is_admin},
            resource_id=session_id,
        )

    payload: dict[str, Any] = {"status": "logged_out"}
    if session is not None:
        oidc = _oidc_client_from_app(request.app)
        if oidc.configured:
            try:
                metadata = await oidc.get_metadata(_http_client_from_app(request.app))
            except Exception:
                metadata = None
            if (
                metadata is not None
                and metadata.end_session_endpoint
                and session.id_token
            ):
                redirect_base = (
                    settings.dashboard_public_base_url or str(request.base_url)
                ).strip()
                redirect_base = redirect_base.rstrip("/")
                next_path = normalize_next_path(
                    settings.dashboard_default_path,
                    fallback="/",
                )
                params = urlencode(
                    {
                        "id_token_hint": session.id_token,
                        "post_logout_redirect_uri": f"{redirect_base}{next_path}",
                    }
                )
                payload["end_session_url"] = f"{metadata.end_session_endpoint}?{params}"

    response = JSONResponse(payload, status_code=200)
    _clear_session_cookie(response)
    return response


async def auth_discord_link_create_handler(request: Request) -> JSONResponse:
    """Create one-time admin login link for a Discord user."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        payload = DiscordLinkCreateRequest.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)},
            status_code=400,
        )

    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    verifier = _discord_admin_verifier_from_app(request.app)
    http_client = _http_client_from_app(request.app)
    is_admin_user = await verifier.is_admin_discord_user(
        discord_user_id=payload.discord_user_id,
        http_client=http_client,
    )
    if not is_admin_user:
        return JSONResponse(
            {"error": "forbidden", "detail": "discord_user_not_admin"},
            status_code=403,
        )

    token = secrets.token_urlsafe(24)
    next_path = normalize_next_path(
        payload.next_path,
        fallback=normalize_next_path(settings.dashboard_default_path),
    )
    await store.save_discord_link(
        token=token,
        payload=DiscordLinkGrant(
            discord_user_id=payload.discord_user_id,
            next_path=next_path,
        ),
        ttl_seconds=settings.discord_link_ttl_seconds,
    )

    base_url = (settings.dashboard_public_base_url or "").strip().rstrip("/")
    if not base_url:
        base_url = str(request.base_url).strip().rstrip("/")

    return JSONResponse(
        {
            "status": "created",
            "link_url": f"{base_url}/auth/discord/link/{token}",
            "expires_in_seconds": settings.discord_link_ttl_seconds,
        },
        status_code=201,
    )


async def auth_discord_link_redirect_handler(
    request: Request,
    token: str,
) -> JSONResponse | RedirectResponse:
    """Handle one-time Discord deep link and jump into OIDC login flow."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    grant = await store.get_discord_link(token)
    if grant is None:
        return JSONResponse({"error": "link_not_found"}, status_code=404)

    _, session = await _current_session(request)
    if session is not None:
        if not session.is_admin:
            return JSONResponse(
                {"error": "forbidden", "detail": "admin_group_required"},
                status_code=403,
            )

        if not session.email:
            return JSONResponse(
                {"error": "forbidden", "detail": "email_claim_required"},
                status_code=403,
            )

        verifier = _discord_admin_verifier_from_app(request.app)
        linked = await verifier.is_admin_email_for_discord_user(
            email=session.email,
            discord_user_id=grant.discord_user_id,
        )
        if not linked:
            return JSONResponse(
                {
                    "error": "forbidden",
                    "detail": "oidc_user_not_linked_to_discord_admin",
                },
                status_code=403,
            )

        await store.delete_discord_link(token)
        return RedirectResponse(url=grant.next_path, status_code=302)

    login_query = urlencode({"next": grant.next_path, "discord_link_token": token})
    return RedirectResponse(url=f"/auth/login?{login_query}", status_code=302)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> Any:
    await asyncio.to_thread(run_job_migrations)

    redis_conn = get_redis_connection(settings)
    app.state.redis_conn = redis_conn
    app.state.postgres_conn_lock = asyncio.Lock()
    app.state.postgres_conn = await asyncio.to_thread(get_postgres_connection, settings)
    app.state.queue = build_queue_client()
    app.state.auth_store = RedisAuthStore(redis_conn)
    app.state.oidc_client = OIDCProviderClient(settings)
    app.state.discord_admin_verifier = DiscordAdminVerifier(settings)
    app.state.http_client = httpx.AsyncClient(follow_redirects=False)

    if settings.crm_sync_enabled:
        app.state.crm_sync_task = asyncio.create_task(_crm_sync_scheduler(app))
    else:
        logger.info("CRM sync scheduler disabled by config")

    if settings.email_resume_intake_enabled:
        app.state.email_resume_task = asyncio.create_task(_email_resume_scheduler())
    else:
        logger.info("Mailbox resume intake scheduler disabled by config")

    try:
        yield
    finally:
        if hasattr(app.state, "crm_sync_task"):
            task = app.state.crm_sync_task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if hasattr(app.state, "email_resume_task"):
            task = app.state.email_resume_task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if hasattr(app.state, "http_client"):
            await app.state.http_client.aclose()

        if hasattr(app.state, "postgres_conn"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(app.state.postgres_conn.close)

        with contextlib.suppress(Exception):
            redis_conn.close()


def create_app(*, run_lifespan: bool = True) -> FastAPI:
    """Create configured FastAPI app."""
    app = FastAPI(
        title="508 Worker API",
        version="0.1.0",
        lifespan=_lifespan if run_lifespan else None,
    )

    app.state.oidc_client = OIDCProviderClient(settings)
    app.state.discord_admin_verifier = DiscordAdminVerifier(settings)

    app.add_api_route("/", health_handler, methods=["GET"])
    app.add_api_route("/health", health_handler, methods=["GET"])

    app.add_api_route("/jobs/{job_id}", job_status_handler, methods=["GET"])
    app.add_api_route("/jobs/resume-extract", resume_extract_handler, methods=["POST"])
    app.add_api_route("/jobs/resume-apply", resume_apply_handler, methods=["POST"])

    app.add_api_route("/webhooks/espocrm", espocrm_webhook_handler, methods=["POST"])
    app.add_api_route(
        "/webhooks/espocrm/people-sync",
        espocrm_people_sync_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route("/webhooks/{source}", ingest_handler, methods=["POST"])

    app.add_api_route(
        "/process-contact/{contact_id}",
        process_contact_handler,
        methods=["POST"],
    )
    app.add_api_route("/sync/people", sync_people_handler, methods=["POST"])
    app.add_api_route("/audit/events", audit_event_handler, methods=["POST"])

    app.add_api_route(
        "/auth/login", auth_login_handler, methods=["GET"], response_model=None
    )
    app.add_api_route(
        "/auth/callback", auth_callback_handler, methods=["GET"], response_model=None
    )
    app.add_api_route("/auth/me", auth_me_handler, methods=["GET"])
    app.add_api_route("/auth/logout", auth_logout_handler, methods=["POST"])
    app.add_api_route(
        "/auth/discord/links",
        auth_discord_link_create_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/auth/discord/link/{token}",
        auth_discord_link_redirect_handler,
        methods=["GET"],
        response_model=None,
    )

    return app


def run() -> None:
    """Entrypoint for worker API service."""
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(),
        host=settings.webhook_ingest_host,
        port=settings.webhook_ingest_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
