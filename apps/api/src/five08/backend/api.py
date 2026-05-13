"""FastAPI dashboard + ingest API for enqueuing background jobs."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
import time
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError
from psycopg import Connection
from psycopg.rows import dict_row

from five08.audit import (
    ActorProvider,
    AuditEventInput,
    AuditResult,
    AuditSource,
    insert_audit_event,
)
from five08.clients.espo import EspoAPIError, EspoClient
from five08.logging import configure_observability
from five08.queue import (
    EnqueuedJob,
    QueueClient,
    JobStatus,
    list_jobs,
    enqueue_job,
    get_job,
    get_postgres_connection,
    get_redis_connection,
    is_postgres_healthy,
)
from five08.backend.auth import (
    AuthSession,
    DASHBOARD_ADMIN_PERMISSIONS,
    DASHBOARD_PERMISSION_AUDIT_READ,
    DASHBOARD_PERMISSION_JOBS_READ,
    DASHBOARD_PERMISSION_JOBS_WRITE,
    DASHBOARD_PERMISSION_ONBOARDING_READ,
    DASHBOARD_PERMISSION_ONBOARDING_WRITE,
    DASHBOARD_PERMISSION_PEOPLE_READ,
    DASHBOARD_PERMISSION_PEOPLE_SYNC,
    DASHBOARD_SENSITIVE_PERMISSIONS,
    DiscordAdminVerifier,
    DiscordLinkGrant,
    OIDCProviderClient,
    PendingOIDCState,
    RedisAuthStore,
    build_authorization_url,
    build_redirect_uri,
    dashboard_permissions_for_roles,
    extract_groups,
    has_dashboard_discord_role,
    is_admin_from_groups,
    make_pkce_pair,
    normalize_next_path,
)
from five08.backend.dashboard import dashboard_html, login_required_html
from five08.worker.config import settings
from five08.worker.db_migrations import run_job_migrations
from five08.worker.dispatcher import build_queue_client
from five08.worker.masking import mask_email
from five08.worker.jobs import (
    JOB_FUNCTIONS,
)
from five08.worker.mailbox_resume_ingest import ResumeMailboxProcessor
from five08.worker.models import (
    AuditEventPayload,
    DocusealWebhookPayload,
    EspoCRMWebhookPayload,
    GoogleFormsIntakePayload,
)

logger = logging.getLogger(__name__)
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ResumeExtractRequest(BaseModel):
    """Request schema for queued resume extraction."""

    contact_id: str
    attachment_id: str
    filename: str
    refresh_token: str | None = None


class ResumeApplyRequest(BaseModel):
    """Request schema for queued resume apply updates."""

    contact_id: str
    updates: dict[str, Any]
    link_discord: dict[str, str] | None = None


class DiscordLinkCreateRequest(BaseModel):
    """Payload for creating one-time admin deep links from Discord commands."""

    discord_user_id: str
    next_path: str | None = None


class DashboardAssignOnboarderRequest(BaseModel):
    """Payload for assigning an onboarder from the dashboard."""

    onboarder: str


@dataclass(frozen=True)
class JobsQueryFilters:
    """Normalized query filters for job-list endpoints."""

    created_after: datetime
    status: JobStatus | None
    job_type: str | None


_JOB_FUNCTIONS = JOB_FUNCTIONS
_ONBOARDING_STATUS_FIELD = "cOnboardingState"
_ONBOARDER_FIELD = "cOnboarder"
_SENSITIVE_PAYLOAD_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "refresh_token",
    "secret",
    "token",
)
_ONBOARDER_USERNAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")


class DashboardOnboarderAssignmentError(Exception):
    """Expected dashboard onboarder assignment validation error."""

    def __init__(self, error: str, *, status_code: int = 400) -> None:
        super().__init__(error)
        self.error = error
        self.status_code = status_code


# Backward-compatible direct handler exports expected by existing call sites/tests.
process_webhook_event = JOB_FUNCTIONS["process_webhook_event"]
process_contact_skills_job = JOB_FUNCTIONS["process_contact_skills_job"]
extract_resume_profile_job = JOB_FUNCTIONS["extract_resume_profile_job"]
apply_resume_profile_job = JOB_FUNCTIONS["apply_resume_profile_job"]
process_intake_form_job = JOB_FUNCTIONS["process_intake_form_job"]
process_mailbox_message_job = JOB_FUNCTIONS["process_mailbox_message_job"]
sync_people_from_crm_job = JOB_FUNCTIONS["sync_people_from_crm_job"]
sync_person_from_crm_job = JOB_FUNCTIONS["sync_person_from_crm_job"]
process_docuseal_agreement_job = JOB_FUNCTIONS["process_docuseal_agreement_job"]


def _is_authorized(request: Request) -> bool:
    """Validate shared API secret."""
    if not settings.api_shared_secret:
        logger.error("Rejecting request: API_SHARED_SECRET is not configured")
        return False

    provided_secret = request.headers.get("X-API-Secret", "")
    if secrets.compare_digest(provided_secret, settings.api_shared_secret):
        return True
    logger.warning("Rejecting request: invalid X-API-Secret")
    return False


def _encode_ulid_base32(value: int, length: int) -> str:
    encoded = ["0"] * length
    for index in range(length - 1, -1, -1):
        encoded[index] = _ULID_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(encoded)


def _generate_ulid() -> str:
    """Generate a sortable ULID string without external dependencies."""
    timestamp_ms = int(time.time() * 1000)
    random_value = int.from_bytes(os.urandom(10), "big")
    return f"{_encode_ulid_base32(timestamp_ms, 10)}{_encode_ulid_base32(random_value, 16)}"


def _extract_idempotency_key(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resume_extract_model_name() -> str:
    if settings.openai_api_key:
        return settings.resolved_resume_ai_model
    return "heuristic"


def _coerce_docuseal_completed_at_to_utc(value: str) -> str:
    """Normalize Docuseal completion timestamps for queue/job payload contract."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_value = parsed.astimezone(timezone.utc)
    return utc_value.strftime("%Y-%m-%d %H:%M:%S")


def _crm_sync_idempotency_key(*, now: datetime) -> str:
    interval_seconds = max(1, settings.crm_sync_interval_seconds)
    bucket = int(now.timestamp()) // interval_seconds
    return f"crm-sync:{bucket}"


def _normalize_google_forms_input(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _google_forms_intake_idempotency_key(
    *,
    email: str,
    submission_id: str | None,
    submitted_at: str | None,
    payload: dict[str, Any],
) -> str:
    token = _normalize_google_forms_input(submission_id) or ""
    if not token:
        token = _normalize_google_forms_input(submitted_at) or ""
    if not token:
        normalized_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        token = hashlib.sha256(normalized_payload.encode("utf-8")).hexdigest()
    return f"intake:{email}:{token}"


def _validate_google_forms_submission(
    payload: GoogleFormsIntakePayload,
) -> JSONResponse | None:
    allowed_form_ids = settings.google_forms_allowed_form_ids_set
    if not allowed_form_ids:
        return None

    form_id = (payload.form_id or "").strip()
    if form_id and form_id in allowed_form_ids:
        return None

    return JSONResponse({"error": "invalid_form_id"}, status_code=403)


async def _enqueue_full_crm_sync_job(queue: QueueClient, *, reason: str) -> EnqueuedJob:
    now = datetime.now(tz=timezone.utc)
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=JOB_FUNCTIONS["sync_people_from_crm_job"],
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
    queue = build_queue_client()
    interval_seconds = max(1, settings.check_email_wait) * 60
    while True:
        try:
            messages = await asyncio.to_thread(poller.poll_unprocessed_messages)
            enqueued = 0
            for message in messages:
                idempotency_key = (
                    message.message_id if message.message_id else message.message_num
                )
                job = await asyncio.to_thread(
                    enqueue_job,
                    queue=queue,
                    fn=JOB_FUNCTIONS["process_mailbox_message_job"],
                    args=(message.raw_message_b64,),
                    settings=settings,
                    idempotency_key=f"mailbox-inbox:{idempotency_key}",
                )
                if job.created:
                    enqueued += 1
            logger.debug(
                "Completed mailbox resume poll discovered_messages=%s queued_jobs=%s",
                len(messages),
                enqueued,
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
            fn=JOB_FUNCTIONS["process_contact_skills_job"],
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
            fn=JOB_FUNCTIONS["sync_person_from_crm_job"],
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


def _has_sso_validated_session(session: AuthSession) -> bool:
    return bool(session.id_token.strip()) or _dashboard_dev_sensitive_access_enabled()


def _dashboard_dev_sensitive_access_enabled() -> bool:
    return settings.environment.strip().lower() in {
        "local",
        "dev",
        "development",
        "test",
    }


def _dashboard_permissions_for_identity(
    raw_roles: object,
    *,
    is_admin: bool,
    id_token: str,
    actor_provider: ActorProvider = ActorProvider.ADMIN_SSO,
) -> list[str]:
    if actor_provider == ActorProvider.DISCORD:
        permissions = set(
            dashboard_permissions_for_roles(
                raw_roles,
                is_admin=is_admin,
                admin_role_names=settings.discord_admin_role_names,
            )
        )
    else:
        permissions = set(DASHBOARD_ADMIN_PERMISSIONS if is_admin else ())
    if not id_token.strip() and not _dashboard_dev_sensitive_access_enabled():
        permissions -= DASHBOARD_SENSITIVE_PERMISSIONS
    return sorted(permissions)


def _session_dashboard_permissions(session: AuthSession) -> set[str]:
    actor_provider = _session_actor_provider(session)
    if session.permissions:
        permissions = set(session.permissions)
        if actor_provider != ActorProvider.DISCORD and not session.is_admin:
            permissions = set()
    else:
        permissions = set(
            _dashboard_permissions_for_identity(
                session.groups,
                is_admin=session.is_admin,
                id_token=session.id_token,
                actor_provider=actor_provider,
            )
        )
    if not _has_sso_validated_session(session):
        permissions -= DASHBOARD_SENSITIVE_PERMISSIONS
    return permissions


def _crm_web_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.lower().endswith("/api/v1"):
        normalized = normalized[: -len("/api/v1")].rstrip("/")
    return normalized


def _crm_base_url() -> str:
    return _crm_web_base_url(settings.espo_base_url)


def _session_has_dashboard_permission(
    session: AuthSession,
    required_permission: str,
) -> bool:
    return required_permission in _session_dashboard_permissions(session)


async def _dashboard_session_or_error(
    request: Request,
    *,
    required_permission: str = DASHBOARD_PERMISSION_PEOPLE_READ,
) -> tuple[AuthSession | None, JSONResponse | None]:
    session_id, session = await _current_session(request)
    if session is None:
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        if session_id is not None:
            _clear_session_cookie(response)
        return None, response
    if not _session_has_dashboard_permission(session, required_permission):
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return session, None


def _origin_from_url(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _request_origin(request: Request) -> str | None:
    return _origin_from_url(str(request.base_url))


def _dashboard_same_origin_post_or_error(request: Request) -> JSONResponse | None:
    expected_origin = _request_origin(request)
    if expected_origin is None:
        return JSONResponse({"error": "invalid_request_origin"}, status_code=403)

    origin = request.headers.get("origin")
    if origin is not None and _origin_from_url(origin) != expected_origin:
        return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    referer = request.headers.get("referer")
    if origin is None and referer is not None:
        if _origin_from_url(referer) != expected_origin:
            return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    return None


def _session_payload(session: AuthSession) -> dict[str, Any]:
    return {
        "subject": session.subject,
        "email": session.email,
        "display_name": session.display_name,
        "groups": session.groups,
        "is_admin": session.is_admin,
        "permissions": sorted(_session_dashboard_permissions(session)),
        "expires_at": session.expires_at,
        "actor_provider": session.actor_provider,
        "crm_contact_id": session.crm_contact_id,
        "crm_base_url": _crm_base_url(),
    }


def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = key_text.lower()
            if any(
                marker in normalized_key for marker in _SENSITIVE_PAYLOAD_KEY_MARKERS
            ):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = _redact_sensitive_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


_DASHBOARD_REDACT_POSITIONAL_ARGS_JOB_TYPES = {
    "process_mailbox_message_job",
}


def _redact_dashboard_job_payload(job_type: str, payload: dict[str, Any]) -> Any:
    redacted = _redact_sensitive_payload(payload)
    if (
        job_type in _DASHBOARD_REDACT_POSITIONAL_ARGS_JOB_TYPES
        and isinstance(redacted, dict)
        and isinstance(redacted.get("args"), list)
    ):
        redacted["args"] = ["[redacted]"] * len(redacted["args"])
    return redacted


def _redact_dashboard_idempotency_key(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value)
    if key.startswith("resume-extract:"):
        parts = key.split(":")
        if len(parts) > 5:
            return ":".join(parts[:5] + ["[redacted]"])
    return key


def _datetime_or_none(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


_ONBOARDING_STATUS_LABELS = {
    "pending": "Needs review",
    "selected": "Assigned to onboarder",
    "reachingout": "Reaching out",
    "awaitingcontribution": "Awaiting contribution",
    "onboarded": "Onboarded",
    "waitlist": "Waitlist",
    "rejected": "Rejected",
}


_DASHBOARD_PEOPLE_SEARCH_SQL = """
(
    coalesce(crm_contact_id, '') || ' ' ||
    coalesce(name, '') || ' ' ||
    coalesce(email, '') || ' ' ||
    coalesce(email_508, '') || ' ' ||
    coalesce(discord_user_id, '') || ' ' ||
    coalesce(discord_username, '') || ' ' ||
    coalesce(github_username, '') || ' ' ||
    coalesce(contact_type, '') || ' ' ||
    coalesce(address_country, '') || ' ' ||
    coalesce(address_city, '') || ' ' ||
    coalesce(address_state, '') || ' ' ||
    coalesce(seniority, '') || ' ' ||
    coalesce(latest_resume_name, '')
)
"""

_DASHBOARD_ONBOARDING_SEARCH_SQL = """
(
    coalesce(name, '') || ' ' ||
    coalesce(email, '') || ' ' ||
    coalesce(email_508, '') || ' ' ||
    coalesce(discord_user_id, '') || ' ' ||
    coalesce(discord_username, '') || ' ' ||
    coalesce(onboarder, '') || ' ' ||
    coalesce(onboarding_state, '')
)
"""

_ONBOARDING_STATE_NORMALIZED_SQL = """
replace(
    replace(
        replace(lower(btrim(onboarding_state)), '_', ''),
        '-',
        ''
    ),
    ' ',
    ''
)
"""


def _normalize_onboarding_state_key(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .casefold()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


def _onboarding_status_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "No status"
    normalized = _normalize_onboarding_state_key(raw)
    if normalized in _ONBOARDING_STATUS_LABELS:
        return _ONBOARDING_STATUS_LABELS[normalized]
    return raw.replace("_", " ").replace("-", " ").title()


def _dashboard_job_payload(job: Any) -> dict[str, Any]:
    payload = job.payload if isinstance(job.payload, dict) else {}
    result = payload.get("result")
    return {
        "job_id": job.id,
        "type": job.type,
        "status": job.status.value,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "run_after": _datetime_or_none(job.run_after),
        "locked_at": _datetime_or_none(job.locked_at),
        "locked_by": job.locked_by,
        "last_error": job.last_error,
        "idempotency_key": _redact_dashboard_idempotency_key(job.idempotency_key),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "payload": _redact_dashboard_job_payload(job.type, payload),
        "result": _redact_sensitive_payload(result),
    }


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip()
        return bool(normalized) and normalized.lower() not in {"no discord", "none"}
    return bool(value)


def _normalize_508_username(value: str | None) -> str | None:
    """Normalize a dashboard onboarder value into a 508 username."""
    if not value:
        return None

    normalized = value.strip().lstrip("@").strip()
    normalized = " ".join(normalized.split())
    if not normalized or normalized.startswith("<@"):
        return None

    if "@" in normalized:
        username, _, _domain = normalized.partition("@")
        if not username:
            return None
        normalized = username

    normalized = normalized.casefold()
    if not normalized or any(
        char not in _ONBOARDER_USERNAME_CHARS for char in normalized
    ):
        return None
    return normalized


def _limit_dashboard_count(value: int) -> int:
    return max(1, min(value, 100))


def _normalize_jobs_query_filters(
    *,
    minutes: int,
    status: str | None,
    job_type: str | None,
) -> tuple[JobsQueryFilters | None, JSONResponse | None]:
    job_status: JobStatus | None = None
    if status is not None:
        try:
            job_status = JobStatus(status)
        except ValueError:
            return None, JSONResponse(
                {"error": "invalid_status", "status": status},
                status_code=400,
            )

    normalized_job_type = job_type.strip() if job_type is not None else None
    if normalized_job_type == "":
        normalized_job_type = None

    return JobsQueryFilters(
        created_after=datetime.now(tz=timezone.utc) - timedelta(minutes=minutes),
        status=job_status,
        job_type=normalized_job_type,
    ), None


def _query_dashboard_people(
    *,
    normalized_query: str,
    limit: int,
    sync_status: str | None,
    is_member: bool | None,
    discord: str | None,
    email_508: str | None,
    resume: str | None,
    skills: str | None,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    conditions: list[str] = []
    if normalized_query:
        like_query = f"%{normalized_query}%"
        params.append(like_query)
        conditions.append(f"{_DASHBOARD_PEOPLE_SEARCH_SQL} ILIKE %s")

    if sync_status is not None:
        conditions.append("sync_status = %s")
        params.append(sync_status)
    if is_member is not None:
        conditions.append("is_member = %s")
        params.append(is_member)
    if discord == "linked":
        conditions.append(
            """
            discord_user_id IS NOT NULL
            AND btrim(discord_user_id) <> ''
            AND lower(btrim(discord_user_id)) NOT IN ('no discord', 'none')
        """
        )
    elif discord == "missing":
        conditions.append(
            """
            (
                discord_user_id IS NULL
                OR btrim(discord_user_id) = ''
                OR lower(btrim(discord_user_id)) IN ('no discord', 'none')
            )
        """
        )
    if email_508 == "present":
        conditions.append("email_508 IS NOT NULL AND btrim(email_508) <> ''")
    elif email_508 == "missing":
        conditions.append("(email_508 IS NULL OR btrim(email_508) = '')")
    if resume == "present":
        conditions.append(
            """
            (
            (
                latest_resume_id IS NOT NULL
                AND btrim(latest_resume_id) <> ''
            )
            OR (
                latest_resume_name IS NOT NULL
                AND btrim(latest_resume_name) <> ''
            )
            )
        """
        )
    elif resume == "missing":
        conditions.append(
            """
            (
                latest_resume_id IS NULL
                OR btrim(latest_resume_id) = ''
            )
            AND (
                latest_resume_name IS NULL
                OR btrim(latest_resume_name) = ''
            )
        """
        )
    if skills == "present":
        conditions.append("COALESCE(cardinality(skills), 0) > 0")
    elif skills == "missing":
        conditions.append("COALESCE(cardinality(skills), 0) = 0")

    params.append(limit)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""
        SELECT
            id::text,
            crm_contact_id,
            name,
            email,
            email_508,
            discord_user_id,
            discord_username,
            discord_roles,
            github_username,
            contact_type,
            is_member,
            address_country,
            address_city,
            address_state,
            timezone,
            seniority,
            linkedin,
            skills,
            latest_resume_id,
            latest_resume_name,
            onboarding_state,
            onboarder,
            onboarding_updated_at,
            sync_status,
            created_at,
            updated_at
        FROM people
        {where_clause}
        ORDER BY updated_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    return _shape_dashboard_people_rows(rows)


def _shape_dashboard_people_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    for row in rows:
        roles = row.get("discord_roles") or []
        skills = row.get("skills") or []
        person = dict(row)
        person["created_at"] = _datetime_or_none(row.get("created_at"))
        person["updated_at"] = _datetime_or_none(row.get("updated_at"))
        person["onboarding_updated_at"] = _datetime_or_none(
            row.get("onboarding_updated_at")
        )
        person["onboarding_status_label"] = _onboarding_status_label(
            row.get("onboarding_state")
        )
        person["profile_status"] = {
            "crm_active": row.get("sync_status") == "active",
            "is_member": bool(row.get("is_member")),
            "discord_linked": _is_present(row.get("discord_user_id")),
            "email_508": _is_present(row.get("email_508")),
            "latest_resume": _is_present(row.get("latest_resume_id"))
            or _is_present(row.get("latest_resume_name")),
            "roles_count": len(roles) if isinstance(roles, list) else 0,
            "skills_count": len(skills) if isinstance(skills, list) else 0,
        }
        people.append(person)
    return people


def _list_dashboard_onboarding(
    *,
    query: str | None,
    limit: int,
    onboarding_state: str | None,
    onboarder: str | None,
    discord: str | None,
    email_508: str | None,
    resume: str | None,
    skills: str | None,
) -> list[dict[str, Any]]:
    normalized_query = (query or "").strip()
    limit = _limit_dashboard_count(limit)
    params: list[Any] = []
    conditions: list[str] = [
        "sync_status = 'active'",
        "is_member = false",
        "contact_type ILIKE %s",
        f"""
        (
            onboarding_state IS NULL
            OR {_ONBOARDING_STATE_NORMALIZED_SQL} NOT IN ('onboarded', 'waitlist', 'rejected')
        )
        """,
    ]
    params.append("%prospect%")

    if normalized_query:
        like_query = f"%{normalized_query}%"
        params.append(like_query)
        conditions.append(f"{_DASHBOARD_ONBOARDING_SEARCH_SQL} ILIKE %s")
    if onboarding_state is not None:
        conditions.append(f"{_ONBOARDING_STATE_NORMALIZED_SQL} = %s")
        params.append(onboarding_state)
    if onboarder:
        conditions.append("onboarder ILIKE %s")
        params.append(f"%{onboarder}%")
    if discord == "linked":
        conditions.append(
            """
            discord_user_id IS NOT NULL
            AND btrim(discord_user_id) <> ''
            AND lower(btrim(discord_user_id)) NOT IN ('no discord', 'none')
        """
        )
    elif discord == "missing":
        conditions.append(
            """
            (
                discord_user_id IS NULL
                OR btrim(discord_user_id) = ''
                OR lower(btrim(discord_user_id)) IN ('no discord', 'none')
            )
        """
        )
    if email_508 == "present":
        conditions.append("email_508 IS NOT NULL AND btrim(email_508) <> ''")
    elif email_508 == "missing":
        conditions.append("(email_508 IS NULL OR btrim(email_508) = '')")
    if resume == "present":
        conditions.append(
            """
            (
            (
                latest_resume_id IS NOT NULL
                AND btrim(latest_resume_id) <> ''
            )
            OR (
                latest_resume_name IS NOT NULL
                AND btrim(latest_resume_name) <> ''
            )
            )
        """
        )
    elif resume == "missing":
        conditions.append(
            """
            (
                latest_resume_id IS NULL
                OR btrim(latest_resume_id) = ''
            )
            AND (
                latest_resume_name IS NULL
                OR btrim(latest_resume_name) = ''
            )
        """
        )
    if skills == "present":
        conditions.append("COALESCE(cardinality(skills), 0) > 0")
    elif skills == "missing":
        conditions.append("COALESCE(cardinality(skills), 0) = 0")

    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT
            id::text,
            crm_contact_id,
            name,
            email,
            email_508,
            discord_user_id,
            discord_username,
            discord_roles,
            github_username,
            contact_type,
            is_member,
            address_country,
            address_city,
            address_state,
            timezone,
            seniority,
            linkedin,
            skills,
            latest_resume_id,
            latest_resume_name,
            onboarding_state,
            onboarder,
            onboarding_updated_at,
            sync_status,
            created_at,
            updated_at
        FROM people
        WHERE {where_clause}
        ORDER BY
            CASE WHEN COALESCE({_ONBOARDING_STATE_NORMALIZED_SQL}, '') = 'pending'
                THEN 1 ELSE 0 END,
            onboarding_updated_at DESC NULLS LAST,
            name ASC NULLS LAST
        LIMIT %s
    """
    params.append(limit)
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

    return _shape_dashboard_people_rows(rows)


def _is_dashboard_onboarding_contact_eligible(contact_id: str) -> bool:
    sql = f"""
        SELECT 1
        FROM people
        WHERE
            crm_contact_id = %s
            AND sync_status = 'active'
            AND is_member = false
            AND contact_type ILIKE %s
            AND (
                onboarding_state IS NULL
                OR {_ONBOARDING_STATE_NORMALIZED_SQL}
                    NOT IN ('onboarded', 'waitlist', 'rejected')
            )
        LIMIT 1
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, (contact_id, "%prospect%"))
            return cursor.fetchone() is not None


def _list_dashboard_audit_events(limit: int) -> list[dict[str, Any]]:
    limit = _limit_dashboard_count(limit)
    sql = """
        SELECT
            id::text,
            occurred_at,
            source,
            action,
            resource_type,
            resource_id,
            result,
            actor_provider,
            actor_subject,
            actor_display_name,
            person_id::text,
            correlation_id,
            metadata,
            created_at
        FROM audit_events
        ORDER BY occurred_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, (limit,))
            rows = cursor.fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        event["occurred_at"] = _datetime_or_none(row.get("occurred_at"))
        event["created_at"] = _datetime_or_none(row.get("created_at"))
        event["metadata"] = row.get("metadata") or {}
        events.append(event)
    return events


def _assign_dashboard_onboarder_in_crm(
    *,
    contact_id: str,
    onboarder: str,
) -> dict[str, Any]:
    normalized_contact_id = contact_id.strip()
    if not normalized_contact_id:
        raise DashboardOnboarderAssignmentError("contact_id_required")

    onboarder_username = _normalize_508_username(onboarder)
    if onboarder_username is None:
        raise DashboardOnboarderAssignmentError("invalid_onboarder")

    if not _is_dashboard_onboarding_contact_eligible(normalized_contact_id):
        raise DashboardOnboarderAssignmentError(
            "contact_not_onboarding_eligible",
            status_code=403,
        )

    client = EspoClient(settings.espo_base_url, settings.espo_api_key)
    full_contact = client.request("GET", f"Contact/{normalized_contact_id}")
    if _ONBOARDER_FIELD not in full_contact:
        raise DashboardOnboarderAssignmentError(
            "missing_onboarder_field",
            status_code=422,
        )

    current_state = str(full_contact.get(_ONBOARDING_STATUS_FIELD) or "").strip()
    normalized_state = current_state.casefold()
    update_payload: dict[str, str] = {_ONBOARDER_FIELD: onboarder_username}
    state_updated = False
    if normalized_state == "pending":
        update_payload[_ONBOARDING_STATUS_FIELD] = "selected"
        state_updated = True

    client.request("PUT", f"Contact/{normalized_contact_id}", update_payload)
    resulting_state = "selected" if state_updated else current_state
    return {
        "status": "updated",
        "contact_id": normalized_contact_id,
        "contact_name": full_contact.get("name") or "CRM contact",
        "onboarder": onboarder_username,
        "previous_state": normalized_state or None,
        "onboarding_state": resulting_state or None,
        "state_updated": state_updated,
    }


def _session_actor_provider(session: AuthSession) -> ActorProvider:
    raw_provider = session.actor_provider.strip().lower()
    if raw_provider == ActorProvider.DISCORD.value:
        return ActorProvider.DISCORD
    return ActorProvider.ADMIN_SSO


def _set_session_cookie(response: Response, session_id: str) -> None:
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


def _clear_session_cookie(response: Response) -> None:
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


def _session_audit_actor(session: AuthSession) -> tuple[ActorProvider, str]:
    actor_provider = _session_actor_provider(session)
    actor_subject = session.email or session.subject
    if actor_provider == ActorProvider.DISCORD:
        actor_subject = session.subject
    return actor_provider, actor_subject


async def _audit_dashboard_job_rerun(
    session: AuthSession,
    payload: dict[str, Any],
) -> None:
    if payload.get("status") != "queued":
        return

    job_type = payload.get("type")
    if not isinstance(job_type, str):
        return

    job_id = payload.get("job_id")
    source_job_id = payload.get("source_job_id")
    if not isinstance(job_id, str) or not isinstance(source_job_id, str):
        return

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="worker.job_rerun",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="worker_job",
        resource_id=job_id,
        metadata={
            "source": "dashboard",
            "source_job_id": source_job_id,
            "job_type": job_type,
            "queue": settings.redis_queue_name,
        },
    )


async def _audit_dashboard_assign_onboarder(
    session: AuthSession,
    *,
    result: AuditResult,
    contact_id: str,
    onboarder: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    audit_metadata = {
        "source": "dashboard",
        "onboarder": onboarder,
    }
    audit_metadata.update(metadata or {})
    await _write_auth_audit_event(
        action="crm.assign_onboarder",
        result=result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="crm_contact",
        resource_id=contact_id,
        metadata=audit_metadata,
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
        fn=JOB_FUNCTIONS["process_webhook_event"],
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
        fn=JOB_FUNCTIONS["process_contact_skills_job"],
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
    if payload.refresh_token:
        idempotency_key = f"{idempotency_key}:{payload.refresh_token}"
    job = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=JOB_FUNCTIONS["extract_resume_profile_job"],
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
        fn=JOB_FUNCTIONS["apply_resume_profile_job"],
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


async def jobs_handler(
    request: Request,
    minutes: int = Query(default=60, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None, alias="type"),
) -> JSONResponse:
    """Return jobs created within the last N minutes."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    filters, error_response = _normalize_jobs_query_filters(
        minutes=minutes,
        status=status,
        job_type=job_type,
    )
    if error_response is not None:
        return error_response
    assert filters is not None

    recent_jobs = await asyncio.to_thread(
        list_jobs,
        settings,
        created_after=filters.created_after,
        limit=limit,
        status=filters.status,
        job_type=filters.job_type,
    )

    payload = [
        {
            "job_id": job.id,
            "type": job.type,
            "status": job.status.value,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }
        for job in recent_jobs
    ]
    return JSONResponse(payload)


async def _rerun_job(job_id: str, queue: QueueClient) -> tuple[dict[str, Any], int]:
    """Create a duplicate queued job from an existing persisted job."""
    normalized_job_id = job_id.strip()
    if not normalized_job_id:
        return {"error": "job_id_required"}, 400

    source_job = await asyncio.to_thread(get_job, settings, normalized_job_id)
    if source_job is None:
        return {"error": "job_not_found"}, 404

    fn = JOB_FUNCTIONS.get(source_job.type)
    if fn is None:
        return {
            "error": "unsupported_job_type",
            "job_type": source_job.type,
        }, 400

    raw_payload = source_job.payload
    if not isinstance(raw_payload, dict):
        return {"error": "invalid_job_payload"}, 400
    if "args" not in raw_payload or "kwargs" not in raw_payload:
        return {"error": "invalid_job_payload"}, 400

    raw_args = raw_payload["args"]
    raw_kwargs = raw_payload["kwargs"]
    if not isinstance(raw_args, list) or not isinstance(raw_kwargs, dict):
        return {"error": "invalid_job_payload"}, 400

    rerun_idempotency_key = f"manual-rerun:{source_job.id}:{_generate_ulid()}"

    try:
        rerun_job: EnqueuedJob = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=fn,
            args=tuple(raw_args),
            kwargs=raw_kwargs,
            settings=settings,
            idempotency_key=rerun_idempotency_key,
            max_attempts=source_job.max_attempts,
        )
    except Exception:
        logger.exception(
            "Failed rerunning job source_job_id=%s type=%s",
            source_job.id,
            source_job.type,
        )
        return {"error": "enqueue_failed"}, 503

    return {
        "status": "queued",
        "source_job_id": source_job.id,
        "job_id": rerun_job.id,
        "type": source_job.type,
        "created": rerun_job.created,
    }, 202


async def rerun_job_handler(request: Request, job_id: str) -> JSONResponse:
    """Create and enqueue a new job using a prior job's original call payload."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    queue = request.app.state.queue
    payload, status_code = await _rerun_job(job_id, queue)
    return JSONResponse(payload, status_code=status_code)


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


async def dashboard_handler(
    request: Request,
    view: str | None = None,
) -> HTMLResponse | RedirectResponse:
    """Serve the operations dashboard for authenticated operator sessions."""
    session_id, session = await _current_session(request)
    if session is None:
        oidc = _oidc_client_from_app(request.app)
        response = HTMLResponse(
            login_required_html(oidc_configured=oidc.configured),
            status_code=401,
        )
        if session_id is not None:
            _clear_session_cookie(response)
        return response
    if not _session_has_dashboard_permission(session, DASHBOARD_PERMISSION_PEOPLE_READ):
        return HTMLResponse("Forbidden", status_code=403)
    return HTMLResponse(dashboard_html(), status_code=200)


async def dashboard_me_handler(request: Request) -> JSONResponse:
    """Return the dashboard session identity."""
    session, error_response = await _dashboard_session_or_error(request)
    if error_response is not None:
        return error_response
    assert session is not None
    return JSONResponse(_session_payload(session))


async def dashboard_jobs_handler(
    request: Request,
    minutes: int = Query(default=60, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None),
    job_type: str | None = Query(default=None, alias="type"),
) -> JSONResponse:
    """Return recent jobs for an authenticated dashboard session."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_JOBS_READ,
    )
    if error_response is not None:
        return error_response

    filters, error_response = _normalize_jobs_query_filters(
        minutes=minutes,
        status=status,
        job_type=job_type,
    )
    if error_response is not None:
        return error_response
    assert filters is not None

    recent_jobs = await asyncio.to_thread(
        list_jobs,
        settings,
        created_after=filters.created_after,
        limit=limit,
        status=filters.status,
        job_type=filters.job_type,
    )

    return JSONResponse(
        [
            {
                "job_id": job.id,
                "type": job.type,
                "status": job.status.value,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "last_error": job.last_error,
                "created_at": job.created_at.isoformat(),
                "updated_at": job.updated_at.isoformat(),
            }
            for job in recent_jobs
        ]
    )


async def dashboard_job_detail_handler(
    request: Request,
    job_id: str,
) -> JSONResponse:
    """Return one job's dashboard detail payload for an authenticated admin."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_JOBS_READ,
    )
    if error_response is not None:
        return error_response

    normalized_job_id = job_id.strip()
    if not normalized_job_id:
        return JSONResponse({"error": "job_id_required"}, status_code=400)

    job = await asyncio.to_thread(get_job, settings, normalized_job_id)
    if job is None:
        return JSONResponse({"error": "job_not_found"}, status_code=404)

    return JSONResponse(_dashboard_job_payload(job))


async def dashboard_people_handler(
    request: Request,
    query: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    sync_status: str | None = Query(default=None),
    is_member: bool | None = Query(default=None),
    discord: str | None = Query(default=None),
    email_508: str | None = Query(default=None),
    resume: str | None = Query(default=None),
    skills: str | None = Query(default=None),
) -> JSONResponse:
    """Return CRM people-cache rows for dashboard lookup/onboarding views."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_READ,
    )
    if error_response is not None:
        return error_response

    if sync_status not in {None, "active", "missing_in_crm", "conflict"}:
        return JSONResponse({"error": "invalid_sync_status"}, status_code=400)
    if discord not in {None, "linked", "missing"}:
        return JSONResponse({"error": "invalid_discord_filter"}, status_code=400)
    if email_508 not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_email_508_filter"}, status_code=400)
    if resume not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_resume_filter"}, status_code=400)
    if skills not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_skills_filter"}, status_code=400)

    people = await asyncio.to_thread(
        _query_dashboard_people,
        normalized_query=(query or "").strip(),
        limit=_limit_dashboard_count(limit),
        sync_status=sync_status,
        is_member=is_member,
        discord=discord,
        email_508=email_508,
        resume=resume,
        skills=skills,
    )
    return JSONResponse(people)


async def dashboard_onboarding_handler(
    request: Request,
    query: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    onboarding_state: str | None = Query(default=None),
    onboarder: str | None = Query(default=None),
    discord: str | None = Query(default=None),
    email_508: str | None = Query(default=None),
    resume: str | None = Query(default=None),
    skills: str | None = Query(default=None),
) -> JSONResponse:
    """Return prospect onboarding queue rows from the CRM people cache."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_ONBOARDING_READ,
    )
    if error_response is not None:
        return error_response

    normalized_state = (
        _normalize_onboarding_state_key(onboarding_state) if onboarding_state else None
    )
    normalized_onboarder = onboarder.strip() if onboarder else None
    if normalized_state == "":
        normalized_state = None
    if normalized_onboarder == "":
        normalized_onboarder = None
    if discord not in {None, "linked", "missing"}:
        return JSONResponse({"error": "invalid_discord_filter"}, status_code=400)
    if email_508 not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_email_508_filter"}, status_code=400)
    if resume not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_resume_filter"}, status_code=400)
    if skills not in {None, "present", "missing"}:
        return JSONResponse({"error": "invalid_skills_filter"}, status_code=400)

    people = await asyncio.to_thread(
        _list_dashboard_onboarding,
        query=query,
        limit=limit,
        onboarding_state=normalized_state,
        onboarder=normalized_onboarder,
        discord=discord,
        email_508=email_508,
        resume=resume,
        skills=skills,
    )
    return JSONResponse(people)


async def dashboard_assign_onboarder_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Assign an onboarder to one CRM contact from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_ONBOARDING_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
        payload = DashboardAssignOnboarderRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        result = await asyncio.to_thread(
            _assign_dashboard_onboarder_in_crm,
            contact_id=contact_id,
            onboarder=payload.onboarder,
        )
    except DashboardOnboarderAssignmentError as exc:
        await _audit_dashboard_assign_onboarder(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            onboarder=_normalize_508_username(payload.onboarder),
            metadata={"reason": exc.error},
        )
        return JSONResponse({"error": exc.error}, status_code=exc.status_code)
    except EspoAPIError as exc:
        logger.error(
            "CRM onboarder assignment failed contact_id=%s error=%s",
            contact_id,
            exc,
        )
        await _audit_dashboard_assign_onboarder(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            onboarder=_normalize_508_username(payload.onboarder),
            metadata={"reason": "crm_update_failed", "error": str(exc)},
        )
        return JSONResponse(
            {"error": "crm_update_failed", "detail": str(exc)},
            status_code=502,
        )

    sync_job_id: str | None = None
    try:
        sync_job = await asyncio.to_thread(
            enqueue_job,
            queue=request.app.state.queue,
            fn=JOB_FUNCTIONS["sync_person_from_crm_job"],
            args=(result["contact_id"],),
            settings=settings,
            idempotency_key=f"dashboard-onboarder-sync:{result['contact_id']}:{_generate_ulid()}",
        )
        sync_job_id = sync_job.id
    except Exception:
        logger.warning(
            "Failed enqueueing post-assignment people sync contact_id=%s",
            result["contact_id"],
            exc_info=True,
        )

    await _audit_dashboard_assign_onboarder(
        session,
        result=AuditResult.SUCCESS,
        contact_id=result["contact_id"],
        onboarder=result["onboarder"],
        metadata={
            "contact_name": result["contact_name"],
            "previous_state": result["previous_state"],
            "state_updated": result["state_updated"],
            "sync_job_id": sync_job_id,
        },
    )
    result["sync_job_id"] = sync_job_id
    return JSONResponse(result, status_code=200)


async def dashboard_audit_events_handler(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
) -> JSONResponse:
    """Return recent human audit events for the dashboard."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_AUDIT_READ,
    )
    if error_response is not None:
        return error_response

    events = await asyncio.to_thread(_list_dashboard_audit_events, limit)
    return JSONResponse(events)


async def dashboard_rerun_job_handler(
    request: Request,
    job_id: str,
) -> JSONResponse:
    """Rerun one job from the authenticated dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_JOBS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    payload, status_code = await _rerun_job(job_id, request.app.state.queue)
    if status_code == 202:
        await _audit_dashboard_job_rerun(session, payload)
    return JSONResponse(payload, status_code=status_code)


async def dashboard_sync_people_handler(request: Request) -> JSONResponse:
    """Queue a people-cache sync from the authenticated dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    job = await _enqueue_full_crm_sync_job(request.app.state.queue, reason="dashboard")
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="crm.people_sync",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="crm_people_sync",
        resource_id=job.id,
        metadata={
            "source": "dashboard",
            "queue": settings.redis_queue_name,
        },
    )
    return JSONResponse(
        {
            "status": "queued",
            "source": "dashboard",
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


async def docuseal_webhook_handler(request: Request) -> JSONResponse:
    """Process a Docuseal form.completed webhook and enqueue agreement job.

    Job payload contract for the queue is:
    completed_at = "YYYY-MM-DD HH:mm:ss" in UTC.
    """
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, dict):
        return JSONResponse({"error": "payload_must_be_object"}, status_code=400)

    try:
        payload = DocusealWebhookPayload.model_validate(payload_data)
    except (ValidationError, TypeError) as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)},
            status_code=400,
        )

    if payload.event_type != "form.completed":
        return JSONResponse(
            {
                "status": "ignored",
                "reason": f"unhandled event_type: {payload.event_type}",
            },
            status_code=200,
        )

    submitter = payload.data
    submission_id = (
        submitter.submission_id if submitter.submission_id is not None else submitter.id
    )

    template_filter_id = settings.docuseal_member_agreement_template_id
    if template_filter_id is None:
        logger.info("Ignoring Docuseal agreement webhook: template filter is unset")
        return JSONResponse(
            {
                "status": "ignored",
                "reason": "template_filter_not_configured",
            },
            status_code=200,
        )

    template_id = submitter.template.id if submitter.template else None
    if template_id != template_filter_id:
        logger.info(
            "Ignoring Docuseal agreement webhook for unmatched template_id=%s"
            " expected=%s submission_id=%s",
            template_id,
            template_filter_id,
            submission_id,
        )
        return JSONResponse(
            {
                "status": "ignored",
                "reason": "template_mismatch",
                "submission_id": submission_id,
            },
            status_code=200,
        )

    email = (submitter.email or "").strip()

    completed_at = submitter.completed_at or payload.timestamp
    if isinstance(completed_at, str):
        completed_at = completed_at.strip()
    if not isinstance(completed_at, str) or not completed_at:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    try:
        completed_at = _coerce_docuseal_completed_at_to_utc(completed_at)
    except ValueError:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    if not email:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    masked_email = mask_email(email)

    queue = request.app.state.queue
    try:
        job: EnqueuedJob = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=JOB_FUNCTIONS["process_docuseal_agreement_job"],
            args=(email, completed_at, submission_id),
            settings=settings,
            idempotency_key=f"docuseal-agreement:{submission_id}",
        )
    except Exception:
        logger.exception(
            "Failed enqueueing Docuseal agreement job masked_email=%s submission_id=%s",
            masked_email,
            submission_id,
        )
        return JSONResponse({"error": "enqueue_failed"}, status_code=503)

    logger.info(
        "Enqueued Docuseal agreement job job_id=%s masked_email=%s",
        job.id,
        masked_email,
    )
    return JSONResponse(
        {
            "status": "queued",
            "source": "docuseal",
            "job_id": job.id,
            "masked_email": masked_email,
            "submission_id": submission_id,
        },
        status_code=202,
    )


async def google_forms_intake_webhook_handler(request: Request) -> JSONResponse:
    """Validate a Google Forms intake submission and enqueue a processing job."""
    if not _is_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        payload = GoogleFormsIntakePayload.model_validate(payload_data)
    except (ValidationError, TypeError) as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)},
            status_code=400,
        )

    form_validation_error = _validate_google_forms_submission(payload)
    if form_validation_error is not None:
        return form_validation_error

    email = (payload.email or "").strip().lower()
    first_name = (payload.first_name or "").strip()
    last_name = (payload.last_name or "").strip()
    if not email or not first_name or not last_name:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_payload = payload.model_dump(exclude_none=True)
    normalized_payload["email"] = email
    normalized_payload["first_name"] = first_name
    normalized_payload["last_name"] = last_name

    idempotency_key = _google_forms_intake_idempotency_key(
        email=email,
        submission_id=payload.submission_id,
        submitted_at=payload.submitted_at,
        payload=normalized_payload,
    )

    queue = request.app.state.queue
    try:
        job = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=JOB_FUNCTIONS["process_intake_form_job"],
            args=(normalized_payload,),
            settings=settings,
            idempotency_key=idempotency_key,
        )
    except Exception:
        logger.exception(
            "Failed enqueueing intake form job masked_email=%s",
            mask_email(email),
        )
        return JSONResponse({"error": "enqueue_failed"}, status_code=503)

    return JSONResponse(
        {
            "status": "queued",
            "source": "google_forms",
            "job_id": job.id,
            "email": email,
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

    oidc_subject = str(claims.get("sub", ""))
    audit_actor_subject = (email or oidc_subject.strip()).strip()
    audit_actor_display_name = display_name
    audit_actor_provider = ActorProvider.ADMIN_SSO
    enforce_discord_link_identity_checks = (
        settings.discord_link_require_oidc_identity_checks
    )
    session_subject = oidc_subject
    session_email = email
    session_display_name = display_name
    session_groups = groups
    session_actor_provider = ActorProvider.ADMIN_SSO
    crm_contact_id: str | None = None

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

        if enforce_discord_link_identity_checks:
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
            linked = await verifier.is_dashboard_email_for_discord_user(
                email=email,
                discord_user_id=grant.discord_user_id,
                http_client=http_client,
            )
            if not linked:
                await _write_auth_audit_event(
                    action="auth.login",
                    result=AuditResult.DENIED,
                    actor_subject=audit_actor_subject,
                    actor_display_name=display_name,
                    metadata={
                        "reason": "oidc_user_not_linked_to_dashboard_user",
                        "discord_user_id": grant.discord_user_id,
                    },
                    correlation_id=state,
                )
                return JSONResponse(
                    {
                        "error": "forbidden",
                        "detail": "oidc_user_not_linked_to_discord_dashboard_user",
                    },
                    status_code=403,
                )
            identity = await verifier.resolve_dashboard_identity(
                discord_user_id=grant.discord_user_id,
                http_client=http_client,
            )
            if identity is None:
                await _write_auth_audit_event(
                    action="auth.login",
                    result=AuditResult.DENIED,
                    actor_subject=audit_actor_subject,
                    actor_display_name=display_name,
                    metadata={
                        "reason": "discord_user_not_dashboard_allowed",
                        "discord_user_id": grant.discord_user_id,
                    },
                    correlation_id=state,
                )
                return JSONResponse(
                    {"error": "forbidden", "detail": "discord_user_not_allowed"},
                    status_code=403,
                )
            crm_contact_id = identity.crm_contact_id
            session_subject = grant.discord_user_id
            session_email = identity.email or email
            session_display_name = identity.display_name or display_name
            session_groups = identity.discord_roles
            is_admin = has_dashboard_discord_role(
                identity.discord_roles,
                "Admin",
                admin_role_names=settings.discord_admin_role_names,
            )
            session_actor_provider = ActorProvider.DISCORD
            audit_actor_subject = grant.discord_user_id
            audit_actor_display_name = session_display_name
            audit_actor_provider = ActorProvider.DISCORD
        else:
            # Discord deep links are already restricted to dashboard-capable users.
            # In bootstrap mode, skip OIDC group/email-link checks for this path.
            verifier = _discord_admin_verifier_from_app(request.app)
            identity = await verifier.resolve_dashboard_identity(
                discord_user_id=grant.discord_user_id,
                http_client=http_client,
            )
            if identity is None:
                await _write_auth_audit_event(
                    action="auth.login",
                    result=AuditResult.DENIED,
                    actor_subject=audit_actor_subject,
                    actor_display_name=display_name,
                    metadata={
                        "reason": "discord_user_not_dashboard_allowed",
                        "discord_user_id": grant.discord_user_id,
                    },
                    correlation_id=state,
                )
                return JSONResponse(
                    {"error": "forbidden", "detail": "discord_user_not_allowed"},
                    status_code=403,
                )
            crm_contact_id = identity.crm_contact_id
            session_subject = grant.discord_user_id
            session_email = identity.email or email
            session_display_name = identity.display_name or display_name
            session_groups = identity.discord_roles
            session_actor_provider = ActorProvider.DISCORD
            is_admin = has_dashboard_discord_role(
                identity.discord_roles,
                "Admin",
                admin_role_names=settings.discord_admin_role_names,
            )
            audit_actor_subject = grant.discord_user_id
            audit_actor_display_name = session_display_name
            audit_actor_provider = ActorProvider.DISCORD

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
            subject=session_subject,
            email=session_email,
            display_name=session_display_name,
            groups=session_groups,
            is_admin=is_admin,
            id_token=id_token,
            expires_at=expires_at,
            actor_provider=session_actor_provider.value,
            crm_contact_id=crm_contact_id,
            permissions=_dashboard_permissions_for_identity(
                session_groups,
                is_admin=is_admin,
                id_token=id_token,
                actor_provider=session_actor_provider,
            ),
        ),
        ttl_seconds=settings.auth_session_ttl_seconds,
    )

    redirect_to = normalize_next_path(
        pending.next_path,
        fallback=normalize_next_path(settings.dashboard_default_path),
    )
    response = RedirectResponse(url=redirect_to, status_code=302)
    _set_session_cookie(response, session_id)
    login_audit_metadata: dict[str, Any] = {
        "is_admin": is_admin,
        "groups": session_groups,
        "via_discord_link": bool(pending.discord_link_token),
    }
    if pending.discord_link_token:
        login_audit_metadata["discord_link_identity_checks_enforced"] = (
            enforce_discord_link_identity_checks
        )

    await _write_auth_audit_event(
        action="auth.login",
        result=AuditResult.SUCCESS,
        actor_subject=audit_actor_subject,
        actor_display_name=audit_actor_display_name,
        actor_provider=audit_actor_provider,
        metadata=login_audit_metadata,
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

    return JSONResponse(_session_payload(session))


async def auth_logout_handler(request: Request) -> JSONResponse:
    """Clear server-side session and auth cookie."""
    session_id, session = await _current_session(request)
    store = _auth_store_from_app(request.app)
    if session_id and store is not None:
        await store.delete_session(session_id)

    if session is not None:
        actor_provider = _session_actor_provider(session)
        actor_subject = session.email or session.subject
        if actor_provider == ActorProvider.DISCORD:
            actor_subject = session.subject
        await _write_auth_audit_event(
            action="auth.logout",
            result=AuditResult.SUCCESS,
            actor_subject=actor_subject,
            actor_display_name=session.display_name,
            actor_provider=actor_provider,
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
    """Create one-time operations dashboard login link for a Discord user."""
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
    is_dashboard_user = await verifier.is_dashboard_discord_user(
        discord_user_id=payload.discord_user_id,
        http_client=http_client,
    )
    if not is_dashboard_user:
        return JSONResponse(
            {"error": "forbidden", "detail": "discord_user_not_allowed"},
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
    """Handle one-time Discord deep link and create or resume an admin session."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    grant = await store.get_discord_link(token)
    if grant is None:
        return JSONResponse({"error": "link_not_found"}, status_code=404)

    session_id, session = await _current_session(request)
    if not settings.discord_link_require_oidc_identity_checks:
        verifier = _discord_admin_verifier_from_app(request.app)
        http_client = _http_client_from_app(request.app)
        identity = await verifier.resolve_dashboard_identity(
            discord_user_id=grant.discord_user_id,
            http_client=http_client,
        )
        if identity is None:
            return JSONResponse(
                {"error": "forbidden", "detail": "discord_user_not_allowed"},
                status_code=403,
            )

        session_id = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + max(1, settings.auth_session_ttl_seconds)
        is_admin = has_dashboard_discord_role(
            identity.discord_roles,
            "Admin",
            admin_role_names=settings.discord_admin_role_names,
        )
        await store.save_session(
            session_id=session_id,
            payload=AuthSession(
                subject=grant.discord_user_id,
                email=identity.email,
                display_name=identity.display_name,
                groups=identity.discord_roles,
                is_admin=is_admin,
                id_token="",
                expires_at=expires_at,
                actor_provider=ActorProvider.DISCORD.value,
                crm_contact_id=identity.crm_contact_id,
                permissions=_dashboard_permissions_for_identity(
                    identity.discord_roles,
                    is_admin=is_admin,
                    id_token="",
                    actor_provider=ActorProvider.DISCORD,
                ),
            ),
            ttl_seconds=settings.auth_session_ttl_seconds,
        )
        await store.delete_discord_link(token)

        response = RedirectResponse(url=grant.next_path, status_code=302)
        _set_session_cookie(response, session_id)
        await _write_auth_audit_event(
            action="auth.login",
            result=AuditResult.SUCCESS,
            actor_subject=grant.discord_user_id,
            actor_display_name=identity.display_name,
            actor_provider=ActorProvider.DISCORD,
            metadata={
                "is_admin": is_admin,
                "groups": identity.discord_roles,
                "permissions": _dashboard_permissions_for_identity(
                    identity.discord_roles,
                    is_admin=is_admin,
                    id_token="",
                    actor_provider=ActorProvider.DISCORD,
                ),
                "via_discord_link": True,
                "discord_link_identity_checks_enforced": False,
            },
            resource_id=session_id,
            correlation_id=token,
        )
        return response

    if session is not None:
        if not session.email:
            return JSONResponse(
                {"error": "forbidden", "detail": "email_claim_required"},
                status_code=403,
            )

        verifier = _discord_admin_verifier_from_app(request.app)
        http_client = _http_client_from_app(request.app)
        linked = await verifier.is_dashboard_email_for_discord_user(
            email=session.email,
            discord_user_id=grant.discord_user_id,
            http_client=http_client,
        )
        if not linked:
            return JSONResponse(
                {
                    "error": "forbidden",
                    "detail": "oidc_user_not_linked_to_discord_dashboard_user",
                },
                status_code=403,
            )

        identity = await verifier.resolve_dashboard_identity(
            discord_user_id=grant.discord_user_id,
            http_client=http_client,
        )
        if identity is None:
            return JSONResponse(
                {"error": "forbidden", "detail": "discord_user_not_allowed"},
                status_code=403,
            )

        assert session_id is not None
        is_admin = has_dashboard_discord_role(
            identity.discord_roles,
            "Admin",
            admin_role_names=settings.discord_admin_role_names,
        )
        await store.save_session(
            session_id=session_id,
            payload=AuthSession(
                subject=grant.discord_user_id,
                email=identity.email,
                display_name=identity.display_name,
                groups=identity.discord_roles,
                is_admin=is_admin,
                id_token=session.id_token,
                expires_at=session.expires_at,
                actor_provider=ActorProvider.DISCORD.value,
                crm_contact_id=identity.crm_contact_id,
                permissions=_dashboard_permissions_for_identity(
                    identity.discord_roles,
                    is_admin=is_admin,
                    id_token=session.id_token,
                    actor_provider=ActorProvider.DISCORD,
                ),
            ),
            ttl_seconds=settings.auth_session_ttl_seconds,
        )
        await store.delete_discord_link(token)
        await _write_auth_audit_event(
            action="auth.login",
            result=AuditResult.SUCCESS,
            actor_subject=grant.discord_user_id,
            actor_display_name=identity.display_name,
            actor_provider=ActorProvider.DISCORD,
            metadata={
                "is_admin": is_admin,
                "groups": identity.discord_roles,
                "permissions": _dashboard_permissions_for_identity(
                    identity.discord_roles,
                    is_admin=is_admin,
                    id_token=session.id_token,
                    actor_provider=ActorProvider.DISCORD,
                ),
                "via_discord_link": True,
                "discord_link_identity_checks_enforced": True,
                "upgraded_existing_session": True,
            },
            resource_id=session_id,
            correlation_id=token,
        )
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
        title="508 Backend API",
        version="0.1.0",
        lifespan=_lifespan if run_lifespan else None,
    )

    app.state.oidc_client = OIDCProviderClient(settings)
    app.state.discord_admin_verifier = DiscordAdminVerifier(settings)

    app.add_api_route("/", health_handler, methods=["GET"])
    app.add_api_route("/health", health_handler, methods=["GET"])

    app.add_api_route(
        "/dashboard",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route(
        "/dashboard/{view}",
        dashboard_handler,
        methods=["GET"],
        response_model=None,
    )
    app.add_api_route("/dashboard/api/me", dashboard_me_handler, methods=["GET"])
    app.add_api_route("/dashboard/api/jobs", dashboard_jobs_handler, methods=["GET"])
    app.add_api_route(
        "/dashboard/api/jobs/{job_id}",
        dashboard_job_detail_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/jobs/{job_id}/rerun",
        dashboard_rerun_job_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/people",
        dashboard_people_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding",
        dashboard_onboarding_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/onboarding/{contact_id}/onboarder",
        dashboard_assign_onboarder_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/dashboard/api/audit-events",
        dashboard_audit_events_handler,
        methods=["GET"],
    )
    app.add_api_route(
        "/dashboard/api/sync/people",
        dashboard_sync_people_handler,
        methods=["POST"],
    )

    app.add_api_route("/jobs", jobs_handler, methods=["GET"])
    app.add_api_route("/jobs/{job_id}", job_status_handler, methods=["GET"])
    app.add_api_route("/jobs/{job_id}/rerun", rerun_job_handler, methods=["POST"])
    app.add_api_route("/jobs/resume-extract", resume_extract_handler, methods=["POST"])
    app.add_api_route("/jobs/resume-apply", resume_apply_handler, methods=["POST"])

    app.add_api_route("/webhooks/espocrm", espocrm_webhook_handler, methods=["POST"])
    app.add_api_route(
        "/webhooks/espocrm/people-sync",
        espocrm_people_sync_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/docuseal",
        docuseal_webhook_handler,
        methods=["POST"],
    )
    app.add_api_route(
        "/webhooks/google-forms",
        google_forms_intake_webhook_handler,
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
    """Entrypoint for backend API service."""
    configure_observability(
        settings=settings,
        service_name="backend-api",
        include_fastapi=True,
    )
    uvicorn.run(
        create_app(),
        host=settings.webhook_ingest_host,
        port=settings.webhook_ingest_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
