"""Webhook ingest API for enqueuing background jobs."""

import asyncio
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from aiohttp import web
from pydantic import BaseModel, ValidationError
from redis import Redis

from five08.logging import configure_logging
from five08.queue import (
    EnqueuedJob,
    QueueClient,
    enqueue_job,
    get_job,
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
)
from five08.worker.models import EspoCRMWebhookPayload

logger = logging.getLogger(__name__)
REDIS_CONN_KEY = web.AppKey("redis_conn", Redis)
QUEUE_KEY = web.AppKey("queue", QueueClient)


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


async def health_handler(request: web.Request) -> web.Response:
    """Simple health endpoint."""
    redis_conn = request.app[REDIS_CONN_KEY]

    try:
        redis_ok = bool(await asyncio.to_thread(redis_conn.ping))
    except Exception:
        redis_ok = False
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

    queue = request.app[QUEUE_KEY]
    jobs: list[dict[str, str]] = []
    for event in payload.events:
        job = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=process_contact_skills_job,
            args=(event.id,),
            settings=settings,
            idempotency_key=f"espocrm:{event.id}",
        )
        jobs.append({"contact_id": event.id, "job_id": job.id})

    logger.info(
        "Enqueued %s EspoCRM contact jobs for queue=%s",
        len(jobs),
        settings.redis_queue_name,
    )
    return web.json_response(
        {
            "status": "queued",
            "source": "espocrm",
            "jobs": jobs,
            "events_processed": len(jobs),
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
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=process_contact_skills_job,
        args=(contact_id,),
        settings=settings,
        idempotency_key=f"manual:{contact_id}:{manual_nonce}",
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
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=extract_resume_profile_job,
        args=(payload.contact_id, payload.attachment_id, payload.filename),
        settings=settings,
        idempotency_key=f"resume-extract:{payload.contact_id}:{payload.attachment_id}",
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


async def on_startup(app: web.Application) -> None:
    """Initialize queue dependencies."""
    await asyncio.to_thread(run_job_migrations)
    redis_conn = get_redis_connection(settings)
    app[REDIS_CONN_KEY] = redis_conn
    app[QUEUE_KEY] = build_queue_client()


async def on_cleanup(app: web.Application) -> None:
    """Close Redis connection cleanly."""
    redis_conn = app[REDIS_CONN_KEY]
    redis_conn.close()


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
    app.router.add_post("/webhooks/{source}", ingest_handler)
    app.router.add_post("/process-contact/{contact_id}", process_contact_handler)
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
