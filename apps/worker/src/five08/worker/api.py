"""Webhook ingest API for enqueuing background jobs."""

import asyncio
import contextlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from aiohttp import web
from pydantic import BaseModel, ValidationError
from psycopg import Connection
from redis import Redis

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
from five08.worker.models import AuditEventPayload, EspoCRMWebhookPayload

logger = logging.getLogger(__name__)
REDIS_CONN_KEY = web.AppKey("redis_conn", Redis)
QUEUE_KEY = web.AppKey("queue", QueueClient)
CRM_SYNC_TASK_KEY = web.AppKey("crm_sync_task", asyncio.Task)
POSTGRES_CONN_KEY = web.AppKey("postgres_conn", Connection)
POSTGRES_CONN_LOCK_KEY = web.AppKey("postgres_conn_lock", asyncio.Lock)


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


def _is_authorized(request: web.Request) -> bool:
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
    if settings.openai_api_key and settings.openai_model:
        return settings.openai_model
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


async def _crm_sync_scheduler(app: web.Application) -> None:
    queue = app[QUEUE_KEY]
    interval_seconds = max(1, settings.crm_sync_interval_seconds)
    while True:
        try:
            await _enqueue_full_crm_sync_job(queue, reason="scheduler")
        except Exception:
            logger.exception("Failed scheduling CRM full-sync job")
        await asyncio.sleep(interval_seconds)


def _check_postgres_connection(connection: Connection) -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except Exception:
        return False


async def _is_postgres_connection_healthy(app: web.Application) -> bool:
    lock = app[POSTGRES_CONN_LOCK_KEY]
    async with lock:
        connection = app[POSTGRES_CONN_KEY]
        healthy = await asyncio.to_thread(_check_postgres_connection, connection)
        if healthy:
            return True

        with contextlib.suppress(Exception):
            await asyncio.to_thread(connection.close)

        try:
            refreshed = await asyncio.to_thread(get_postgres_connection, settings)
        except Exception:
            return False

        app[POSTGRES_CONN_KEY] = refreshed
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


async def health_handler(request: web.Request) -> web.Response:
    """Simple health endpoint."""
    redis_conn = request.app[REDIS_CONN_KEY]

    try:
        redis_ok = bool(await asyncio.to_thread(redis_conn.ping))
    except Exception:
        redis_ok = False
    if POSTGRES_CONN_KEY in request.app:
        postgres_ok = await _is_postgres_connection_healthy(request.app)
    else:
        postgres_ok = await asyncio.to_thread(is_postgres_healthy, settings)

    return web.json_response(
        {
            "status": "healthy" if redis_ok and postgres_ok else "degraded",
            "redis_connected": redis_ok,
            "postgres_connected": postgres_ok,
            "queue_name": settings.redis_queue_name,
        },
        status=200 if redis_ok and postgres_ok else 503,
    )


async def ingest_handler(request: web.Request) -> web.Response:
    """Validate and enqueue incoming webhook payloads."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "payload_must_be_object"}, status=400)

    source = request.match_info.get("source", "default")
    queue = request.app[QUEUE_KEY]
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=process_webhook_event,
        args=(source, payload),
        settings=settings,
        idempotency_key=_extract_idempotency_key(payload.get("id")),
    )

    logger.info("Enqueued webhook job %s from source=%s", job.id, source)
    return web.json_response(
        {
            "status": "queued",
            "job_id": job.id,
            "queue": settings.redis_queue_name,
            "source": source,
        },
        status=202,
    )


async def espocrm_webhook_handler(request: web.Request) -> web.Response:
    """Validate EspoCRM webhook payload and enqueue per-contact jobs."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload_data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload_data, list):
        return web.json_response(
            {"error": "payload_must_be_array_of_events"}, status=400
        )

    try:
        payload = EspoCRMWebhookPayload.from_list(payload_data)
    except (ValidationError, TypeError) as exc:
        return web.json_response(
            {"error": "invalid_webhook_event", "detail": str(exc)}, status=400
        )

    event_ids = [event.id for event in payload.events]
    deduped_event_ids = list(dict.fromkeys(event_ids))
    queue = request.app[QUEUE_KEY]
    try:
        await _enqueue_espocrm_batch(queue, deduped_event_ids)
    except Exception:
        logger.exception(
            "Failed enqueueing EspoCRM webhook events count=%s queue=%s",
            len(deduped_event_ids),
            settings.redis_queue_name,
        )
        return web.json_response({"error": "enqueue_failed"}, status=503)

    logger.info(
        "Enqueued %s EspoCRM webhook events queue=%s",
        len(deduped_event_ids),
        settings.redis_queue_name,
    )
    return web.json_response(
        {
            "status": "queued",
            "source": "espocrm",
            "events_received": len(deduped_event_ids),
            "events_enqueued": len(deduped_event_ids),
        },
        status=202,
    )


async def process_contact_handler(request: web.Request) -> web.Response:
    """Manual enqueue for one contact."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    contact_id = request.match_info.get("contact_id", "").strip()
    if not contact_id:
        return web.json_response({"error": "contact_id_required"}, status=400)

    queue = request.app[QUEUE_KEY]
    manual_nonce = datetime.now(tz=timezone.utc).isoformat()
    nonce_suffix = uuid4().hex[:12]
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=process_contact_skills_job,
        args=(contact_id,),
        settings=settings,
        idempotency_key=f"manual:{contact_id}:{manual_nonce}:{nonce_suffix}",
    )
    logger.info(
        "Enqueued manual contact job job_id=%s contact_id=%s created=%s",
        job.id,
        contact_id,
        job.created,
    )
    return web.json_response(
        {
            "status": "queued",
            "source": "manual",
            "contact_id": contact_id,
            "job_id": job.id,
        },
        status=202,
    )


async def resume_extract_handler(request: web.Request) -> web.Response:
    """Enqueue resume extraction job for one uploaded attachment."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload_data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    try:
        payload = ResumeExtractRequest.model_validate(payload_data)
    except ValidationError as exc:
        return web.json_response(
            {"error": "invalid_resume_extract_payload", "detail": str(exc)}, status=400
        )

    queue = request.app[QUEUE_KEY]
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
    return web.json_response(
        {
            "status": "queued",
            "job_id": job.id,
            "contact_id": payload.contact_id,
            "attachment_id": payload.attachment_id,
            "created": job.created,
        },
        status=202,
    )


async def resume_apply_handler(request: web.Request) -> web.Response:
    """Enqueue CRM apply job after user confirmation in Discord."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload_data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    try:
        payload = ResumeApplyRequest.model_validate(payload_data)
    except ValidationError as exc:
        return web.json_response(
            {"error": "invalid_resume_apply_payload", "detail": str(exc)}, status=400
        )

    queue = request.app[QUEUE_KEY]
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
    return web.json_response(
        {
            "status": "queued",
            "job_id": job.id,
            "contact_id": payload.contact_id,
        },
        status=202,
    )


async def job_status_handler(request: web.Request) -> web.Response:
    """Return persisted status and worker result payload for one job."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    job_id = request.match_info.get("job_id", "").strip()
    if not job_id:
        return web.json_response({"error": "job_id_required"}, status=400)

    job = await asyncio.to_thread(get_job, settings, job_id)
    if job is None:
        return web.json_response({"error": "job_not_found"}, status=404)

    result: Any = None
    payload = job.payload if isinstance(job.payload, dict) else {}
    if "result" in payload:
        result = payload["result"]

    return web.json_response(
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


async def sync_people_handler(request: web.Request) -> web.Response:
    """Manual enqueue for a full CRM->people cache sync."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    queue = request.app[QUEUE_KEY]
    job = await _enqueue_full_crm_sync_job(queue, reason="manual")
    return web.json_response(
        {
            "status": "queued",
            "source": "manual",
            "job_id": job.id,
            "created": job.created,
        },
        status=202,
    )


async def espocrm_people_sync_webhook_handler(request: web.Request) -> web.Response:
    """Queue per-contact people cache sync jobs from CRM webhook events."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload_data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload_data, list):
        return web.json_response(
            {"error": "payload_must_be_array_of_events"}, status=400
        )

    try:
        payload = EspoCRMWebhookPayload.from_list(payload_data)
    except (ValidationError, TypeError) as exc:
        return web.json_response(
            {"error": "invalid_webhook_event", "detail": str(exc)}, status=400
        )

    event_ids = [event.id for event in payload.events]
    deduped_event_ids = list(dict.fromkeys(event_ids))
    queue = request.app[QUEUE_KEY]
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
        return web.json_response({"error": "enqueue_failed"}, status=503)

    return web.json_response(
        {
            "status": "queued",
            "source": "espocrm_people_sync",
            "events_received": len(deduped_event_ids),
            "events_enqueued": len(deduped_event_ids),
        },
        status=202,
    )


async def audit_event_handler(request: web.Request) -> web.Response:
    """Persist one human audit event."""
    if not _is_authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload_data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload_data, dict):
        return web.json_response({"error": "payload_must_be_object"}, status=400)

    try:
        payload = AuditEventPayload.model_validate(payload_data)
    except ValidationError as exc:
        return web.json_response(
            {"error": "invalid_payload", "detail": str(exc)}, status=400
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
        return web.json_response(
            {"error": "invalid_payload", "detail": str(exc)}, status=400
        )

    return web.json_response(
        {
            "status": "created",
            "event_id": created.id,
            "person_id": created.person_id,
        },
        status=201,
    )


async def on_startup(app: web.Application) -> None:
    """Initialize queue dependencies."""
    await asyncio.to_thread(run_job_migrations)
    redis_conn = get_redis_connection(settings)
    app[REDIS_CONN_KEY] = redis_conn
    app[POSTGRES_CONN_LOCK_KEY] = asyncio.Lock()
    app[POSTGRES_CONN_KEY] = await asyncio.to_thread(get_postgres_connection, settings)
    app[QUEUE_KEY] = build_queue_client()
    if settings.crm_sync_enabled:
        app[CRM_SYNC_TASK_KEY] = asyncio.create_task(_crm_sync_scheduler(app))
    else:
        logger.info("CRM sync scheduler disabled by config")


async def on_cleanup(app: web.Application) -> None:
    """Close Redis connection cleanly."""
    redis_conn = app[REDIS_CONN_KEY]
    redis_conn.close()
    if POSTGRES_CONN_KEY in app:
        await asyncio.to_thread(app[POSTGRES_CONN_KEY].close)
    if CRM_SYNC_TASK_KEY in app:
        task = app[CRM_SYNC_TASK_KEY]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> web.Application:
    """Create configured aiohttp app."""
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/jobs/{job_id}", job_status_handler)
    app.router.add_post("/jobs/resume-extract", resume_extract_handler)
    app.router.add_post("/jobs/resume-apply", resume_apply_handler)
    app.router.add_post("/webhooks/espocrm", espocrm_webhook_handler)
    app.router.add_post(
        "/webhooks/espocrm/people-sync", espocrm_people_sync_webhook_handler
    )
    app.router.add_post("/webhooks/{source}", ingest_handler)
    app.router.add_post("/process-contact/{contact_id}", process_contact_handler)
    app.router.add_post("/sync/people", sync_people_handler)
    app.router.add_post("/audit/events", audit_event_handler)
    return app


def run() -> None:
    """Entrypoint for worker API service."""
    configure_logging(settings.log_level)
    web.run_app(
        create_app(),
        host=settings.webhook_ingest_host,
        port=settings.webhook_ingest_port,
    )


if __name__ == "__main__":
    run()
