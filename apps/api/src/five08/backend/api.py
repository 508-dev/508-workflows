"""FastAPI dashboard + ingest API for enqueuing background jobs."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import sys
import json
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast
from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from psycopg import Connection
from psycopg.rows import dict_row

from five08.audit import (
    ActorProvider,
    AuditEventInput,
    AuditResult,
    AuditSource,
    insert_audit_event,
)
from five08.agent import (
    AgentIdentityContext,
    AgentModelConfig,
    AgentOrchestrator,
    AgentPlan,
    AgentRequest,
    AgentResponse,
    InMemoryTaskStore,
    OpenAICompatibleAgentPlanner,
    OpenAICompatibleIntentNormalizer,
    PolicyEngine,
    PostgresMemoryStore,
    ToolRegistry,
    ToolRuntimeConfig,
)
from five08.agent.memory import contains_sensitive_memory_text
from five08.clients.espo import EspoAPIError, EspoClient
from five08.logging import configure_observability
from five08.job_leads import (
    clear_job_lead_staging_cleanup_required,
    job_lead_display_payload,
    JobLeadStatus,
    list_job_leads,
    review_job_lead,
)
from five08.job_channels import (
    list_registered_job_post_channel_configs,
    register_job_post_channel,
    unregister_job_post_channel,
)
from five08.queue import (
    EnqueuedJob,
    JobRecord,
    QueueClient,
    JobStatus,
    list_jobs,
    enqueue_job,
    get_job,
    get_postgres_connection,
    get_redis_connection,
    is_postgres_healthy,
    trusted_sql,
)
from five08.backend.auth import (
    AuthSession,
    DASHBOARD_ADMIN_PERMISSIONS,
    DASHBOARD_PERMISSION_AUDIT_READ,
    DASHBOARD_PERMISSION_CONFIGURATION_READ,
    DASHBOARD_PERMISSION_CONFIGURATION_WRITE,
    DASHBOARD_PERMISSION_GIGS_READ,
    DASHBOARD_PERMISSION_GIGS_WRITE,
    DASHBOARD_PERMISSION_JOBS_READ,
    DASHBOARD_PERMISSION_JOBS_WRITE,
    DASHBOARD_PERMISSION_ONBOARDING_READ,
    DASHBOARD_PERMISSION_ONBOARDING_WRITE,
    DASHBOARD_PERMISSION_PEOPLE_READ,
    DASHBOARD_PERMISSION_PEOPLE_SYNC,
    DASHBOARD_PERMISSION_PROJECTS_READ,
    DASHBOARD_PERMISSION_PROJECTS_SYNC,
    DASHBOARD_PERMISSION_PROJECTS_SYNC_DRY_RUN,
    DASHBOARD_PERMISSION_PROJECTS_WRITE,
    DASHBOARD_PERMISSION_JOBS_WRITE_DRY_RUN,
    DASHBOARD_PERMISSION_PEOPLE_SYNC_DRY_RUN,
    DASHBOARD_SENSITIVE_PERMISSIONS,
    DASHBOARD_WORKFLOWS_ENGINEER_SENSITIVE_PERMISSIONS,
    ConsumedDiscordLinkGrant,
    DiscordAdminVerifier,
    DiscordAdminIdentity,
    DiscordLinkGrant,
    OIDCProviderClient,
    PendingOIDCState,
    RedisAuthStore,
    build_authorization_url,
    build_redirect_uri,
    dashboard_permissions_for_roles,
    extract_groups,
    has_dashboard_discord_role,
    has_workflows_engineer_role,
    is_admin_from_groups,
    make_pkce_pair,
    normalize_next_path,
)
from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.backend.routes import BackendRouteSurface, register_routes
from five08.backend.schemas import (
    AgentConfirmationRequest,
    DashboardAssignOnboarderRequest,
    DashboardBulkProjectUpdateRequest,
    DashboardConfigurationUpdateRequest,
    DashboardEngineerSetupRequest,
    DashboardGigApplicationCreateRequest,
    DashboardGigApplicationStatusRequest,
    DashboardGigStatusRequest,
    DashboardJobChannelUpdateRequest,
    DashboardJobLeadPostRequest,
    DashboardJobLeadReviewRequest,
    DashboardJobLeadStagingRecoveryClearRequest,
    DashboardJobLeadSyncRequest,
    DashboardOnboardingEmailDraftRequest,
    DashboardOnboardingEmailSendRequest,
    DashboardOnboardingStatusRequest,
    DashboardOnboardingVolunteerRequest,
    DashboardProjectCreateRequest,
    DashboardProjectHistoricalMemberRemoveRequest,
    DashboardProjectHistoricalMemberRequest,
    DashboardProjectStatusRequest,
    DashboardProjectUserRemoveRequest,
    DashboardProjectUserRequest,
    DashboardProjectWikiMatchRequest,
    DiscordLinkCreateRequest,
    ResumeApplyRequest,
    ResumeExtractRequest,
)
from five08.backend.dashboard import (
    dashboard_assets_dir as dashboard_assets_dir,
    dashboard_html,
    discord_link_continue_html,
    discord_link_unavailable_html,
    login_required_html,
    oidc_not_configured_html,
)
from five08.engagements import (
    EngagementApplicationStatus,
    EngagementStatus,
    add_crm_application_to_engagement,
    list_dashboard_engagements,
    list_dashboard_notifications,
    normalize_engagement_status,
    update_engagement_application_status,
    update_engagement_status,
    viewer_can_update_engagement,
)
from five08.engineer_onboarding import (
    ActivityCostRequest,
    EngineerOnboardingDuplicateNameError,
    EngineerOnboardingError,
    EngineerSetupRequest,
    add_engineer_to_project,
    setup_engineer,
)
from five08.onboarding_email import (
    OnboardingEmailRequest,
    OnboardingEmailSmtpConfig,
    build_onboarding_email,
    build_onboarding_email_message,
    markdown_body_to_html,
    markdown_body_to_text,
    onboarding_email_smtp_ready,
    send_onboarding_email_message,
    validate_plain_email,
)
from five08.onboarding import (
    VolunteerAvailability,
    list_onboarding_volunteers,
    mark_onboarder_assigned,
    suggested_onboarders,
    upsert_onboarding_volunteer,
)
from five08.newsletter_sync import NewsletterSyncProcessor
from five08.newsletter_suppressions import list_newsletter_suppressions
from five08.projects import (
    DEFAULT_WIKI_PROJECT_DOC_ID,
    PROJECT_ROSTER_KIND_HISTORICAL,
    PROJECT_SOURCE_MANUAL,
    PROJECT_WIKI_MATCH_CONFIRMED,
    PROJECT_WIKI_MATCH_NO_ROW,
    add_project_roster_member,
    erpnext_project_to_input,
    fetch_outline_document,
    list_dashboard_projects,
    parse_project_wiki_tables,
    project_cache_summary,
    remove_project_roster_member,
    set_project_wiki_match,
    upsert_project,
    wiki_project_match_preview,
    wiki_row_by_key,
)
from five08.runtime_config import (
    delete_runtime_config_value,
    list_runtime_config,
    runtime_config_definition_for_key,
    set_runtime_config_value,
)
from five08.redaction import redact_email_addresses
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
    TallyWebhookField,
    TallyWebhookPayload,
)

logger = logging.getLogger(__name__)
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PROJECT_ROSTER_USER_CANDIDATE_CACHE_TTL_SECONDS = 60.0
_DISCORD_LINK_REPLAY_TTL_SECONDS = 10
_PROJECT_ROSTER_USER_CANDIDATE_CACHE_MAX_SIZE = 128
_PROJECT_ROSTER_USER_CANDIDATE_CACHE_LOCK = threading.RLock()
_PROJECT_ROSTER_USER_CANDIDATE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_JOB_LEAD_SCRAPE_JOB_TYPE = "scrape_job_leads_job"
_JOB_LEAD_SCRAPE_STATUS_LOOKBACK_DAYS = 366


@dataclass(frozen=True)
class JobsQueryFilters:
    """Normalized query filters for job-list endpoints."""

    created_after: datetime
    status: JobStatus | None
    job_type: str | None


_JOB_FUNCTIONS = JOB_FUNCTIONS
_ONBOARDING_STATUS_FIELD = "cOnboardingState"
_ONBOARDER_FIELD = "cOnboarder"
_GENERIC_UNSUPPORTED_AGENT_MESSAGE = "I could not map that to a supported workflow."
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


class DashboardOnboardingEmailError(Exception):
    """Expected dashboard onboarding email validation/delivery error."""

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
sync_projects_from_erpnext_job = JOB_FUNCTIONS["sync_projects_from_erpnext_job"]
process_docuseal_agreement_job = JOB_FUNCTIONS["process_docuseal_agreement_job"]

TALLY_INTAKE_FIELD_LABEL_MAP = {
    "full name": "name",
    "full name (in english)": "name",
    "email address": "email",
    "email": "email",
    "discord username": "discord_username",
    "linkedin profile link": "linkedin_url",
    "github profile link": "github_username",
    "website link": "website_link",
    "resume / cv (this is the file upload)": "resume_url",
    "resume / cv": "resume_url",
    "current country": "address_country",
    "primary role": "primary_role",
    "seniority level": "seniority_level",
    "name in your native language": "native_name",
    "if you joined 508.dev, how many working hours per week would be ideal from co-op projects": "ideal_weekly_hours",
    "what hourly rate range (in usd) do you normally charge for work": "rate_range",
    "how did you hear about 508.dev": "referred_by",
    "what's your interest in 508.dev / what is a top question you have about the co-op": "top_question_about_508",
    "what would be some good times in the following weeks to have a chat with a member (according to your timezone)": "chat_availability",
    "beyond your resume / linkedin, what would you say your primary skills and interests are": "primary_skills_interests",
}

# Process-local MVP agent tools stay synchronous for Discord button UX. Both the
# task store and pending plans are non-durable; production task workflows should
# swap this registry for a persistent task service before multi-worker use.
_AGENT_TASK_STORE = InMemoryTaskStore()
_AGENT_ORCHESTRATOR: AgentOrchestrator | None = None
_AGENT_ORCHESTRATOR_LOCK = threading.RLock()
_PENDING_AGENT_PLANS: dict[str, tuple[AgentPlan, AgentIdentityContext]] = {}
_PENDING_AGENT_PLANS_LOCK: asyncio.Lock | None = None
_PENDING_AGENT_PLANS_LOCK_LOOP: asyncio.AbstractEventLoop | None = None
_MAX_PENDING_AGENT_PLANS = 1000
_MAX_PENDING_AGENT_PLANS_PER_ACTOR = 25
_AGENT_REQUEST_RATE_LIMIT_WINDOW_SECONDS = 60.0
_AGENT_REQUEST_RATE_LIMIT_MAX_REQUESTS = 10
_AGENT_REQUEST_TIMESTAMPS: dict[str, list[float]] = {}
_AGENT_REQUEST_RATE_LIMIT_LOCK = threading.RLock()
_AGENT_AUDIT_TASKS: set[asyncio.Task[None]] = set()
# `asyncio.wait_for` cannot stop a synchronous DNS/HTTP call running in a
# worker thread. Keep the number of such requests bounded while the API has
# already returned its caller-visible timeout response.
_AGENT_REQUEST_PLAN_BULKHEAD = threading.BoundedSemaphore(value=4)


class AgentRequestPlanCapacityError(RuntimeError):
    """Raised when timed-out synchronous agent work has filled the bulkhead."""


def _get_agent_orchestrator() -> AgentOrchestrator:
    """Lazily construct the agent orchestrator so config errors isolate /agent."""
    global _AGENT_ORCHESTRATOR
    if _AGENT_ORCHESTRATOR is not None:
        return _AGENT_ORCHESTRATOR
    with _AGENT_ORCHESTRATOR_LOCK:
        if _AGENT_ORCHESTRATOR is None:
            _AGENT_ORCHESTRATOR = AgentOrchestrator(
                registry=ToolRegistry(
                    _AGENT_TASK_STORE,
                    memory_store=PostgresMemoryStore(
                        connection_factory=lambda: get_postgres_connection(settings)
                    ),
                    runtime_config_factory=lambda: ToolRuntimeConfig.from_settings(
                        settings
                    ),
                ),
                model_config=AgentModelConfig.from_settings(settings),
                planner=OpenAICompatibleAgentPlanner.from_settings(settings),
                intent_normalizer=OpenAICompatibleIntentNormalizer.from_settings(
                    settings
                ),
                policy=PolicyEngine.from_settings(settings),
                max_planning_steps=settings.agent_planning_max_steps,
                max_public_web_seconds=settings.agent_public_web_deadline_seconds,
            )
    return _AGENT_ORCHESTRATOR


def _run_agent_plan(
    orchestrator: AgentOrchestrator,
    message: str,
    context: AgentIdentityContext,
) -> AgentResponse:
    """Run one sync planner call without allowing stalled workers to multiply."""

    if not _AGENT_REQUEST_PLAN_BULKHEAD.acquire(blocking=False):
        raise AgentRequestPlanCapacityError("agent planner capacity is busy")
    try:
        return orchestrator.plan(message, context)
    finally:
        _AGENT_REQUEST_PLAN_BULKHEAD.release()


def _is_authorized_with_secret(
    request: Request,
    *,
    configured_secret: str | None,
    setting_name: str,
) -> bool:
    """Validate an X-API-Secret header against one configured secret."""
    secret = (configured_secret or "").strip()
    if not secret:
        logger.error("Rejecting request: %s is not configured", setting_name)
        return False

    provided_secret = request.headers.get("X-API-Secret", "")
    if secrets.compare_digest(provided_secret, secret):
        return True
    logger.warning("Rejecting request: invalid X-API-Secret for %s", setting_name)
    return False


def _is_authorized(request: Request) -> bool:
    """Validate the internal shared API secret."""
    return _is_authorized_with_secret(
        request,
        configured_secret=settings.api_shared_secret,
        setting_name="API_SHARED_SECRET",
    )


def _is_agent_authorized(request: Request) -> bool:
    """Validate the dedicated credential for role-bearing agent requests.

    Agent callers supply a Discord role context, so sharing this credential
    with broader internal API clients would let those clients claim a role they
    do not hold. Local/dev/test retain the existing API-secret fallback to make
    single-process development practical; every other environment fails closed.
    """

    agent_secret = (settings.agent_shared_secret or "").strip()
    api_secret = (settings.api_shared_secret or "").strip()
    environment = (settings.environment or "").strip().casefold()
    local_environments = {"local", "development", "dev", "test", "testing"}
    allow_legacy_fallback = bool(settings.agent_allow_legacy_api_secret)
    if not agent_secret:
        if allow_legacy_fallback and environment in local_environments:
            return _is_authorized(request)
        logger.error(
            "Rejecting agent request: AGENT_SHARED_SECRET is not configured "
            "(the legacy fallback requires explicit local/test opt-in)"
        )
        return False
    if api_secret and secrets.compare_digest(agent_secret, api_secret):
        logger.error(
            "Rejecting agent request: AGENT_SHARED_SECRET must differ from "
            "API_SHARED_SECRET outside local development"
        )
        if not (allow_legacy_fallback and environment in local_environments):
            return False
    return _is_authorized_with_secret(
        request,
        configured_secret=agent_secret,
        setting_name="AGENT_SHARED_SECRET",
    )


def _is_webhook_authorized(request: Request) -> bool:
    """Validate the external webhook secret, with legacy API secret fallback."""
    webhook_secret = (settings.webhook_shared_secret or "").strip()
    if webhook_secret:
        return _is_authorized_with_secret(
            request,
            configured_secret=webhook_secret,
            setting_name="WEBHOOK_SHARED_SECRET",
        )

    return _is_authorized_with_secret(
        request,
        configured_secret=settings.api_shared_secret,
        setting_name="API_SHARED_SECRET",
    )


def _agent_request_rate_limited(discord_user_id: str) -> bool:
    now = time.monotonic()
    window_start = now - _AGENT_REQUEST_RATE_LIMIT_WINDOW_SECONDS
    with _AGENT_REQUEST_RATE_LIMIT_LOCK:
        for stored_user_id, stored_timestamps in list(
            _AGENT_REQUEST_TIMESTAMPS.items()
        ):
            active_timestamps = [
                timestamp
                for timestamp in stored_timestamps
                if timestamp >= window_start
            ]
            if active_timestamps:
                _AGENT_REQUEST_TIMESTAMPS[stored_user_id] = active_timestamps
            else:
                del _AGENT_REQUEST_TIMESTAMPS[stored_user_id]

        timestamps = _AGENT_REQUEST_TIMESTAMPS.get(discord_user_id, [])
        if len(timestamps) >= _AGENT_REQUEST_RATE_LIMIT_MAX_REQUESTS:
            _AGENT_REQUEST_TIMESTAMPS[discord_user_id] = timestamps
            return True
        timestamps.append(now)
        _AGENT_REQUEST_TIMESTAMPS[discord_user_id] = timestamps
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
    attempts = settings.resolved_resume_ai_provider_attempts
    if attempts:
        provider = attempts[0]
        if provider.label == "primary":
            return provider.model
        return f"{provider.label}/{provider.model}"
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


def _newsletter_sync_idempotency_key(*, now: datetime) -> str:
    interval_seconds = max(60, settings.newsletter_sync_interval_seconds)
    bucket = int(now.timestamp()) // interval_seconds
    return f"newsletter-sync:508-members:{bucket}"


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


def _normalize_tally_label(label: str | None) -> str:
    normalized = re.sub(r"\s+", " ", str(label or "").strip().casefold())
    while normalized.endswith(("?", "*")):
        normalized = normalized[:-1].strip()
    return normalized


def _tally_intake_key_for_label(label: str) -> str | None:
    local_key = TALLY_INTAKE_FIELD_LABEL_MAP.get(label)
    if local_key:
        return local_key
    if label.startswith("primary role"):
        return "primary_role"
    if label.startswith("resume / cv"):
        return "resume_url"
    return None


def _tally_field_display_value(field: TallyWebhookField) -> Any:
    value = field.value
    if value is None:
        return None

    if isinstance(value, list):
        option_text_by_id = {
            option.id: option.text
            for option in field.options
            if option.text and option.id
        }
        values: list[str] = []
        for item in value:
            if isinstance(item, str) and item in option_text_by_id:
                values.append(str(option_text_by_id[item]))
            elif isinstance(item, str):
                values.append(item)
            elif isinstance(item, Mapping):
                name = item.get("name")
                url = item.get("url")
                values.append(str(name or url or item))
            else:
                values.append(str(item))
        return ", ".join(value for value in values if value.strip()) or None

    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip() or None


def _normalize_tally_github_username(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    if text.startswith("@"):
        text = text[1:].strip()

    parse_candidate = text
    if re.match(r"^(?:www\.)?github\.com/", text, flags=re.IGNORECASE):
        parse_candidate = f"https://{text}"

    parsed = urlparse(parse_candidate)
    host = parsed.netloc.lower()
    if host in {"github.com", "www.github.com"}:
        segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
        if not segments:
            return None
        if segments[0].lower() in {"users", "orgs"} and len(segments) >= 2:
            return segments[1]
        return segments[0]

    return text or None


def _apply_tally_name_parts(mapped: dict[str, Any]) -> None:
    if mapped.get("first_name") and mapped.get("last_name"):
        return

    name = str(mapped.get("name") or "").strip()
    if not name:
        return

    parts = name.split()
    if not parts:
        return

    mapped.setdefault("first_name", parts[0])
    last_name = " ".join(parts[1:]).strip()
    if last_name:
        mapped.setdefault("last_name", last_name)
        return

    if "last_name" not in mapped:
        mapped["last_name"] = "Unknown"
        mapped["last_name_is_placeholder"] = True


def _extract_tally_file_upload(
    field: TallyWebhookField,
) -> tuple[str, str | None] | None:
    if not isinstance(field.value, list) or not field.value:
        return None
    first_file = field.value[0]
    if not isinstance(first_file, Mapping):
        return None
    url = str(first_file.get("url") or "").strip()
    if not url:
        return None
    name = str(first_file.get("name") or "").strip() or None
    return url, name


def _tally_webhook_signature_valid(*, body: bytes, signature: str, secret: str) -> bool:
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(signature.strip(), expected)


def _is_tally_webhook_authorized(request: Request, body: bytes) -> bool:
    signing_secret = str(settings.onboarding_tally_webhook_signing_secret or "").strip()
    if not signing_secret:
        return _is_webhook_authorized(request)

    signature = request.headers.get("Tally-Signature", "")
    if not signature:
        logger.warning("Rejecting Tally webhook: missing Tally-Signature")
        return False
    if _tally_webhook_signature_valid(
        body=body,
        signature=signature,
        secret=signing_secret,
    ):
        return True
    logger.warning("Rejecting Tally webhook: invalid Tally-Signature")
    return False


def _tally_intake_dry_run_mode(
    request: Request,
) -> Literal["none", "webhook", "worker"]:
    value = request.query_params.get("dry_run") or request.headers.get("X-Dry-Run")
    normalized = str(value or "").strip().casefold()
    if normalized in {"worker", "job", "enqueue"}:
        return "worker"
    if normalized in {"1", "true", "yes", "on", "webhook"}:
        return "webhook"
    return "none"


def _strip_url_query_and_fragment(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    if not parsed.query and not parsed.fragment:
        return value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _sanitize_intake_raw_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_intake_raw_payload(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_intake_raw_payload(item) for item in value]
    if isinstance(value, str):
        return _strip_url_query_and_fragment(value)
    return value


def _validate_tally_submission(payload: TallyWebhookPayload) -> JSONResponse | None:
    allowed_form_ids = settings.onboarding_tally_allowed_form_ids_set
    if not allowed_form_ids:
        logger.error(
            "Rejecting Tally webhook: onboarding Tally form allowlist is unset"
        )
        return JSONResponse({"error": "invalid_form_id"}, status_code=403)

    form_id = payload.data.form_id.strip()
    if form_id and form_id in allowed_form_ids:
        return None

    return JSONResponse({"error": "invalid_form_id"}, status_code=403)


def _tally_to_intake_payload(payload: TallyWebhookPayload) -> dict[str, Any]:
    mapped: dict[str, Any] = {
        "source": "tally",
        "form_id": payload.data.form_id,
        "submission_id": payload.data.submission_id or payload.data.response_id,
        "submitted_at": payload.data.created_at or payload.created_at,
    }

    for field in payload.data.fields:
        label = field.label or field.key
        normalized_label = _normalize_tally_label(label)
        local_key = _tally_intake_key_for_label(normalized_label)
        if not local_key:
            continue

        if local_key == "resume_url":
            upload = _extract_tally_file_upload(field)
            if upload:
                mapped["resume_url"] = upload[0]
                if upload[1]:
                    mapped["resume_file_name"] = upload[1]
            continue

        value = _tally_field_display_value(field)
        if value is not None:
            if local_key == "github_username":
                value = _normalize_tally_github_username(value)
            if value is None:
                continue
            mapped[local_key] = value

    _apply_tally_name_parts(mapped)
    mapped["raw_tally_fields"] = [
        field.model_dump(by_alias=True, exclude_none=True)
        for field in payload.data.fields
    ]
    return mapped


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


async def _enqueue_erpnext_project_sync_job(
    queue: QueueClient,
    *,
    reason: str,
) -> EnqueuedJob:
    now = datetime.now(tz=timezone.utc)
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=JOB_FUNCTIONS["sync_projects_from_erpnext_job"],
        args=(),
        settings=settings,
        idempotency_key=f"erpnext-project-sync:{now.strftime('%Y%m%d%H%M')}",
    )
    logger.info(
        "Enqueued ERPNext project sync job id=%s created=%s reason=%s",
        job.id,
        job.created,
        reason,
    )
    return job


async def _enqueue_newsletter_sync_job(
    queue: QueueClient,
    *,
    reason: str,
) -> EnqueuedJob:
    now = datetime.now(tz=timezone.utc)
    idempotency_key = (
        _newsletter_sync_idempotency_key(now=now)
        if reason == "scheduler"
        else (
            f"newsletter-sync:508-members:{reason}:"
            f"{now.strftime('%Y%m%d%H%M%S%f')}:{uuid4().hex}"
        )
    )
    job: EnqueuedJob = await asyncio.to_thread(
        enqueue_job,
        queue=queue,
        fn=JOB_FUNCTIONS["sync_508_members_newsletters_job"],
        args=(),
        settings=settings,
        idempotency_key=idempotency_key,
    )
    logger.info(
        "Enqueued 508 members newsletter sync job id=%s created=%s reason=%s",
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


def _should_start_crm_sync_scheduler() -> bool:
    return _crm_sync_scheduler_skip_reason() is None


def _crm_sync_scheduler_skip_reason() -> str | None:
    if not settings.crm_sync_enabled:
        return "disabled"
    if settings.espo_configured:
        return None
    return "missing_espo"


async def _newsletter_sync_scheduler(app: FastAPI) -> None:
    queue = app.state.queue
    interval_seconds = max(60, settings.newsletter_sync_interval_seconds)
    while True:
        try:
            await _enqueue_newsletter_sync_job(queue, reason="scheduler")
        except Exception:
            logger.exception("Failed scheduling 508 members newsletter sync job")
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


def _check_postgres_connection(connection: Connection | None) -> bool:
    if connection is None:
        return False
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

        if connection is not None:
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


def _valid_uuid_or_none(value: str) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


async def _sync_discord_gig_thread_status(
    request: Request,
    *,
    thread_id: str | None,
    status: EngagementStatus,
) -> dict[str, Any]:
    """Best-effort mirror of dashboard gig status to Discord thread title."""
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return {"status": "skipped", "reason": "missing_thread_id"}

    base_url = settings.discord_bot_internal_base_url.strip()
    if not base_url:
        return {"status": "skipped", "reason": "bot_endpoint_not_configured"}

    api_secret = str(settings.api_shared_secret or "").strip()
    if not api_secret:
        return {"status": "skipped", "reason": "api_secret_not_configured"}

    try:
        response = await _http_client_from_app(request.app).post(
            f"{base_url.rstrip('/')}/internal/jobs/thread-status",
            headers={"X-API-Secret": api_secret},
            json={"thread_id": normalized_thread_id, "status": status.value},
            timeout=8.0,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Failed syncing Discord gig thread status thread_id=%s: %s",
            normalized_thread_id,
            exc,
        )
        return {"status": "error", "reason": "bot_request_failed"}

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        logger.warning(
            "Discord gig thread status sync failed thread_id=%s status=%s payload=%s",
            normalized_thread_id,
            response.status_code,
            payload,
        )
        return {
            "status": "error",
            "reason": "bot_rejected_request",
            "status_code": response.status_code,
        }
    return cast(dict[str, Any], payload)


async def _post_job_lead_to_discord(
    request: Request,
    *,
    lead_id: str,
    reviewer_discord_user_id: str,
    channel_id: str | None = None,
    tags: str | None = None,
    engagement_status: EngagementStatus = EngagementStatus.LEAD,
) -> tuple[dict[str, Any], int]:
    """Ask the Discord bot to promote a qualified lead into a jobs forum."""
    base_url = settings.discord_bot_internal_base_url.strip()
    if not base_url:
        return {"error": "bot_endpoint_not_configured"}, 503

    api_secret = str(settings.api_shared_secret or "").strip()
    if not api_secret:
        return {"error": "api_secret_not_configured"}, 503

    try:
        response = await _http_client_from_app(request.app).post(
            f"{base_url.rstrip('/')}/internal/jobs/job-leads/post",
            headers={"X-API-Secret": api_secret},
            json={
                "lead_id": lead_id,
                "reviewer_discord_user_id": reviewer_discord_user_id,
                "channel_id": channel_id,
                "tags": tags,
                "engagement_status": engagement_status.value,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Failed posting job lead %s to Discord: %s", lead_id, exc)
        return {"error": "bot_request_failed"}, 502

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if response.status_code == 401:
        return {"error": "bot_auth_failed"}, 502
    if response.status_code >= 400 and "error" not in payload:
        payload = {**payload, "error": "bot_request_failed"}
    return cast(dict[str, Any], payload), response.status_code


async def _stage_job_lead_to_discord(
    request: Request,
    *,
    lead_id: str,
    reviewer_discord_user_id: str,
) -> tuple[dict[str, Any], int]:
    """Ask the Discord bot to create an unqualified holding thread for one lead."""
    base_url = settings.discord_bot_internal_base_url.strip()
    if not base_url:
        return {"error": "bot_endpoint_not_configured"}, 503

    api_secret = str(settings.api_shared_secret or "").strip()
    if not api_secret:
        return {"error": "api_secret_not_configured"}, 503

    try:
        response = await _http_client_from_app(request.app).post(
            f"{base_url.rstrip('/')}/internal/jobs/job-leads/stage",
            headers={"X-API-Secret": api_secret},
            json={
                "lead_id": lead_id,
                "reviewer_discord_user_id": reviewer_discord_user_id,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Failed staging job lead %s in Discord: %s", lead_id, exc)
        return {"error": "bot_request_failed"}, 502

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if response.status_code == 401:
        return {"error": "bot_auth_failed"}, 502
    if response.status_code >= 400 and "error" not in payload:
        payload = {**payload, "error": "bot_request_failed"}
    return cast(dict[str, Any], payload), response.status_code


async def _list_job_channels_from_bot(
    request: Request,
    *,
    register_defaults: bool = True,
) -> dict[str, Any] | None:
    """Ask the Discord bot for registered job forums and live tag metadata."""
    base_url = settings.discord_bot_internal_base_url.strip()
    api_secret = str(settings.api_shared_secret or "").strip()
    if not base_url or not api_secret:
        return None
    try:
        params = {}
        if not register_defaults:
            params["register_defaults"] = "false"
        response = await _http_client_from_app(request.app).get(
            f"{base_url.rstrip('/')}/internal/jobs/channels",
            headers={"X-API-Secret": api_secret},
            params=params,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Failed loading Discord job channel metadata: %s", exc)
        return None
    if response.status_code >= 400:
        logger.warning(
            "Discord job channel metadata request failed status=%s payload=%s",
            response.status_code,
            response.text[:500],
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


MAX_SESSION_COOKIE_CANDIDATES = 5


async def _current_session(request: Request) -> tuple[str | None, AuthSession | None]:
    store = _auth_store_from_app(request.app)
    if store is None:
        return None, None

    session_ids = _session_cookie_values(request)
    if not session_ids:
        return None, None

    for session_id in session_ids:
        session = await store.get_session(session_id)
        if session is not None:
            return session_id, session

    return session_ids[0], None


def _session_cookie_values(request: Request) -> list[str]:
    """Return bounded, de-duplicated session cookie values in browser order."""
    cookie_name = settings.auth_session_cookie_name
    values: list[str] = []

    def append_candidate(value: str | None) -> None:
        if (
            value
            and value not in values
            and len(values) < MAX_SESSION_COOKIE_CANDIDATES
        ):
            values.append(value)

    raw_cookie = request.headers.get("cookie", "")
    for item in raw_cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == cookie_name:
            append_candidate(value)

    append_candidate(request.cookies.get(cookie_name))
    return values


def _has_sso_validated_session(session: AuthSession) -> bool:
    return bool(session.id_token.strip()) or _dashboard_dev_sensitive_access_enabled()


def _dashboard_dev_sensitive_access_enabled() -> bool:
    return settings.environment.strip().lower() in {
        "local",
        "dev",
        "development",
        "test",
    }


def _dev_discord_link_identity_from_roles(
    *,
    discord_user_id: str,
    discord_roles: list[str],
    discord_display_name: str | None,
    required_role: str = "Member",
) -> DiscordAdminIdentity | None:
    """Allow trusted bot role context as a local/dev fallback without CRM sync."""
    if not _dashboard_dev_sensitive_access_enabled():
        return None
    if not has_dashboard_discord_role(
        discord_roles,
        required_role,
        admin_role_names=settings.discord_admin_role_names,
    ):
        return None
    return DiscordAdminIdentity(
        discord_user_id=discord_user_id,
        crm_contact_id="",
        email=None,
        display_name=discord_display_name,
        discord_roles=discord_roles,
    )


def _request_prefers_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept.strip():
        return False
    return _accept_quality(accept, "application/json") > _accept_quality(
        accept, "text/html"
    )


def _discord_link_request_fingerprint(request: Request) -> str:
    """Fingerprint the browser source for a short consumed-link replay window."""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip()
    if not client_ip:
        client_ip = request.headers.get("x-real-ip", "").strip()
    if not client_ip and request.client is not None:
        client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "").strip()
    fingerprint_source = f"{client_ip}\n{user_agent}"
    return hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()


async def _discord_link_replay_from_request(
    *,
    store: RedisAuthStore,
    token: str,
    request: Request,
) -> ConsumedDiscordLinkGrant | None:
    if not isinstance(store, RedisAuthStore):
        return None

    replay = await store.get_consumed_discord_link(token)
    if replay is None:
        return None
    if replay.request_fingerprint != _discord_link_request_fingerprint(request):
        return None
    return replay


async def _save_discord_link_replay(
    *,
    store: RedisAuthStore,
    token: str,
    request: Request,
    session_id: str,
    next_path: str,
) -> None:
    if not isinstance(store, RedisAuthStore):
        return

    await store.save_consumed_discord_link(
        token=token,
        payload=ConsumedDiscordLinkGrant(
            session_id=session_id,
            next_path=next_path,
            request_fingerprint=_discord_link_request_fingerprint(request),
        ),
        ttl_seconds=_DISCORD_LINK_REPLAY_TTL_SECONDS,
    )


def _discord_link_replay_redirect(
    replay: ConsumedDiscordLinkGrant,
) -> RedirectResponse:
    response = RedirectResponse(url=replay.next_path, status_code=302)
    _set_session_cookie(response, replay.session_id)
    return response


def _accept_quality(accept: str, media_type: str) -> float:
    media_main, media_sub = media_type.casefold().split("/", 1)
    best = 0.0
    for item in accept.split(","):
        parts = [part.strip() for part in item.split(";")]
        if not parts or not parts[0]:
            continue
        accepted = parts[0].casefold()
        try:
            accepted_main, accepted_sub = accepted.split("/", 1)
        except ValueError:
            continue
        if accepted_main not in {"*", media_main}:
            continue
        if accepted_sub not in {"*", media_sub}:
            continue

        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().casefold() == "q":
                with contextlib.suppress(ValueError):
                    quality = float(value.strip())
                break
        best = max(best, min(max(quality, 0.0), 1.0))
    return best


class _OptionalDirectoryStaticFiles(StaticFiles):
    """StaticFiles variant for generated assets that may appear after startup."""

    async def check_config(self) -> None:
        return None


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
        permissions -= DASHBOARD_WORKFLOWS_ENGINEER_SENSITIVE_PERMISSIONS
        if _discord_admin_can_use_sensitive_dashboard(
            raw_roles,
            is_admin=is_admin,
            actor_provider=actor_provider,
        ):
            permissions |= DASHBOARD_SENSITIVE_PERMISSIONS
        elif _discord_workflows_engineer_can_use_sensitive_dashboard(
            raw_roles,
            actor_provider=actor_provider,
        ):
            permissions |= DASHBOARD_WORKFLOWS_ENGINEER_SENSITIVE_PERMISSIONS
    return sorted(permissions)


def _discord_admin_can_use_sensitive_dashboard(
    raw_roles: object,
    *,
    is_admin: bool,
    actor_provider: ActorProvider,
) -> bool:
    if actor_provider != ActorProvider.DISCORD:
        return False
    return is_admin or has_dashboard_discord_role(
        raw_roles,
        "Admin",
        admin_role_names=settings.discord_admin_role_names,
    )


def _discord_workflows_engineer_can_use_sensitive_dashboard(
    raw_roles: object,
    *,
    actor_provider: ActorProvider,
) -> bool:
    if actor_provider != ActorProvider.DISCORD:
        return False
    return has_workflows_engineer_role(raw_roles)


def _base_session_dashboard_permissions(session: AuthSession) -> set[str]:
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
        permissions -= DASHBOARD_WORKFLOWS_ENGINEER_SENSITIVE_PERMISSIONS
        if _discord_admin_can_use_sensitive_dashboard(
            session.groups,
            is_admin=session.is_admin,
            actor_provider=actor_provider,
        ):
            permissions |= DASHBOARD_SENSITIVE_PERMISSIONS
        elif _discord_workflows_engineer_can_use_sensitive_dashboard(
            session.groups,
            actor_provider=actor_provider,
        ):
            permissions |= DASHBOARD_WORKFLOWS_ENGINEER_SENSITIVE_PERMISSIONS
    return permissions


def _session_dashboard_permissions(session: AuthSession) -> set[str]:
    permissions = _base_session_dashboard_permissions(session)
    if (
        DASHBOARD_PERMISSION_PROJECTS_READ not in permissions
        and _session_can_view_any_project(session)
    ):
        permissions.add(DASHBOARD_PERMISSION_PROJECTS_READ)
    return permissions


async def _session_dashboard_permissions_async(session: AuthSession) -> set[str]:
    permissions = _base_session_dashboard_permissions(session)
    if (
        DASHBOARD_PERMISSION_PROJECTS_READ not in permissions
        and await asyncio.to_thread(
            _session_can_view_any_project,
            session,
        )
    ):
        permissions.add(DASHBOARD_PERMISSION_PROJECTS_READ)
    return permissions


def _crm_web_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.lower().endswith("/api/v1"):
        normalized = normalized[: -len("/api/v1")].rstrip("/")
    return normalized


def _crm_base_url() -> str:
    return _crm_web_base_url(settings.espo_base_url)


def _contact_id_from_crm_profile(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None

    def valid_contact_id(candidate: str | None) -> str | None:
        normalized = str(candidate or "").strip()
        if not normalized or normalized.casefold() in {"view", "list", "create"}:
            return None
        if re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
            return normalized
        return None

    parsed = urlparse(raw)
    haystacks = [parsed.fragment, parsed.path, raw]
    for haystack in haystacks:
        parts = [
            unquote(part).strip()
            for part in re.split(r"[/?#]+", haystack)
            if part.strip()
        ]
        for index, part in enumerate(parts):
            if (
                part == "Contact"
                and index + 2 < len(parts)
                and parts[index + 1] == "view"
            ):
                return valid_contact_id(parts[index + 2])
            if (
                part == "Contact"
                and index + 1 < len(parts)
                and parts[index + 1] == "view"
            ):
                return None
            if part == "Contact" and index + 1 < len(parts):
                return valid_contact_id(parts[index + 1])
    return valid_contact_id(raw)


async def _session_has_dashboard_permission(
    session: AuthSession,
    required_permission: str,
) -> bool:
    permissions = _base_session_dashboard_permissions(session)
    if required_permission in permissions:
        return True
    if required_permission != DASHBOARD_PERMISSION_PROJECTS_READ:
        return False
    return await asyncio.to_thread(_session_can_view_any_project, session)


async def _session_has_any_dashboard_permission(session: AuthSession) -> bool:
    permissions = _base_session_dashboard_permissions(session)
    if permissions:
        return True
    return await asyncio.to_thread(_session_can_view_any_project, session)


def _session_has_steering_access(session: AuthSession) -> bool:
    actor_provider = _session_actor_provider(session)
    if actor_provider == ActorProvider.DISCORD:
        return has_dashboard_discord_role(
            session.groups,
            "Steering Committee",
            admin_role_names=settings.discord_admin_role_names,
        )
    return session.is_admin


def _dashboard_project_viewer_emails(session: AuthSession) -> list[str]:
    """Return normalized emails that can prove project roster membership."""
    candidates: set[str] = set()
    if session.email:
        candidates.add(session.email.strip().casefold())

    conditions: list[str] = []
    params: list[Any] = []
    if session.crm_contact_id:
        conditions.append("crm_contact_id = %s")
        params.append(session.crm_contact_id)
    if _session_actor_provider(session) == ActorProvider.DISCORD and session.subject:
        conditions.append("discord_user_id = %s")
        params.append(session.subject)
    if session.email:
        conditions.append("(LOWER(email) = %s OR LOWER(email_508) = %s)")
        normalized_email = session.email.strip().casefold()
        params.extend([normalized_email, normalized_email])
    if not conditions:
        return sorted(email for email in candidates if email)

    query = f"""
        SELECT email, email_508
        FROM people
        WHERE sync_status = 'active'
          AND ({" OR ".join(conditions)})
        LIMIT 5
        """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), params)
            for row in cursor.fetchall():
                for key in ("email", "email_508"):
                    value = row.get(key)
                    if isinstance(value, str) and value.strip():
                        candidates.add(value.strip().casefold())
    return sorted(email for email in candidates if email)


def _session_can_view_any_project(session: AuthSession) -> bool:
    """Return whether this non-Steering user is on any cached ERP project roster."""
    if _session_has_steering_access(session):
        return True
    try:
        viewer_emails = _dashboard_project_viewer_emails(session)
    except Exception:
        logger.warning("Failed resolving project viewer emails", exc_info=True)
        return False
    if not viewer_emails:
        return False
    try:
        with get_postgres_connection(settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT 1
                    FROM project_roster_members
                    WHERE source = 'erpnext'
                      AND roster_kind = 'erp_users'
                      AND (
                        LOWER(email) = ANY(%s)
                        OR LOWER(source_user_id) = ANY(%s)
                      )
                    LIMIT 1
                    """,
                    (viewer_emails, viewer_emails),
                )
                return cursor.fetchone() is not None
    except Exception:
        logger.warning("Failed checking project roster visibility", exc_info=True)
        return False


def _project_summary_for_visible_rows(projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Return non-leaky project metrics for a roster-limited viewer."""
    last_synced_values = [
        parsed
        for project in projects
        if (parsed := _parse_dashboard_timestamp(project.get("last_synced_at")))
        is not None
    ]
    return {
        "project_count": len(projects),
        "open_project_count": sum(
            1
            for project in projects
            if str(project.get("source_status") or "").casefold() == "open"
        ),
        "projects_with_roster": sum(
            1 for project in projects if int(project.get("roster_count") or 0) > 0
        ),
        "roster_member_count": sum(
            int(project.get("roster_count") or 0) for project in projects
        ),
        "last_synced_at": (
            max(last_synced_values).isoformat() if last_synced_values else None
        ),
    }


def _parse_dashboard_timestamp(value: Any) -> datetime | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_discord_actor_id(session: AuthSession) -> str | None:
    """Return a Discord user ID only for Discord-backed dashboard sessions."""
    if _session_actor_provider(session) != ActorProvider.DISCORD:
        return None
    return session.subject


def _dashboard_steering_or_error(session: AuthSession) -> JSONResponse | None:
    """Require a steering/admin dashboard identity for global lead mutations."""
    if _session_has_steering_access(session):
        return None
    return JSONResponse({"error": "steering_required"}, status_code=403)


async def _dashboard_session_or_error(
    request: Request,
    *,
    required_permission: str | None = DASHBOARD_PERMISSION_PEOPLE_READ,
) -> tuple[AuthSession | None, JSONResponse | None]:
    session_id, session = await _current_session(request)
    if session is None:
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        if session_id is not None:
            _clear_session_cookie(response)
        return None, response
    if required_permission is None:
        if not await _session_has_any_dashboard_permission(session):
            return None, JSONResponse({"error": "forbidden"}, status_code=403)
    elif not await _session_has_dashboard_permission(session, required_permission):
        return None, JSONResponse({"error": "forbidden"}, status_code=403)
    return session, None


async def _dashboard_write_session_or_dry_run(
    request: Request,
    *,
    required_permission: str,
    dry_run_permission: str,
) -> tuple[AuthSession | None, JSONResponse | None, bool]:
    session_id, session = await _current_session(request)
    if session is None:
        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        if session_id is not None:
            _clear_session_cookie(response)
        return None, response, False

    permissions = _base_session_dashboard_permissions(session)
    if required_permission in permissions:
        return session, None, False
    if dry_run_permission in permissions:
        return session, None, True
    return None, JSONResponse({"error": "forbidden"}, status_code=403), False


def _origin_from_url(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _request_origin(request: Request) -> str | None:
    return _origin_from_url(str(request.base_url))


def _dashboard_allowed_post_origins(request: Request) -> set[str]:
    origins: set[str] = set()
    request_origin = _request_origin(request)
    if request_origin is not None:
        origins.add(request_origin)

    public_base_url = (settings.dashboard_public_base_url or "").strip().rstrip("/")
    public_origin = _origin_from_url(public_base_url)
    if public_origin is not None:
        origins.add(public_origin)

    return origins


def _dashboard_same_origin_post_or_error(request: Request) -> JSONResponse | None:
    allowed_origins = _dashboard_allowed_post_origins(request)
    if not allowed_origins:
        return JSONResponse({"error": "invalid_request_origin"}, status_code=403)

    origin = request.headers.get("origin")
    if origin is not None and _origin_from_url(origin) not in allowed_origins:
        return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    referer = request.headers.get("referer")
    if origin is None and referer is not None:
        if _origin_from_url(referer) not in allowed_origins:
            return JSONResponse({"error": "csrf_check_failed"}, status_code=403)

    return None


async def _session_payload(session: AuthSession) -> dict[str, Any]:
    return {
        "subject": session.subject,
        "email": session.email,
        "display_name": session.display_name,
        "groups": session.groups,
        "is_admin": session.is_admin,
        "can_manage_leads": _session_has_steering_access(session),
        "permissions": sorted(await _session_dashboard_permissions_async(session)),
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


def _sanitize_agent_improvement_message(message: str) -> str:
    sanitized = re.sub(r"\s+", " ", message).strip()
    sanitized = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[email]",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"https?://\S+|www\.\S+", "[url]", sanitized, flags=re.IGNORECASE
    )
    sanitized = re.sub(r"<@!?\d+>", "[discord_user]", sanitized)
    sanitized = re.sub(
        r"\bcontact[-_][A-Za-z0-9_-]+\b", "[contact_id]", sanitized, flags=re.IGNORECASE
    )
    sanitized = re.sub(r"\b(?:\+?\d[\d .()/-]{7,}\d)\b", "[phone]", sanitized)
    for person_pattern in [
        r"(member agreement\s+(?:to|for)\s+)([^,.;!?]+)",
        r"((?:look\s*up|lookup|find|show)\s+(?:info|information|profile)\s+"
        r"(?:on|for|about)\s+)([^,.;!?]+)",
        r"((?:find|lookup|search)\s+(?:contact|member)\s+)([^,.;!?]+)",
    ]:
        sanitized = re.sub(
            person_pattern,
            r"\1[person]",
            sanitized,
            flags=re.IGNORECASE,
        )
    return sanitized[:256]


def _sanitize_agent_audit_message(message: str) -> str:
    """Keep credentials and other high-risk values out of agent audit records."""

    if contains_sensitive_memory_text(message):
        return "[sensitive agent request redacted]"
    return _sanitize_agent_improvement_message(message)


def _agent_request_audit_metadata(
    *,
    message: str,
    response: AgentResponse,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "status": response.status,
        "intent": response.plan.intent if response.plan else None,
        "planner": response.plan.planner if response.plan else None,
        "operation_id": response.plan.operation_id if response.plan else None,
        "model": response.plan.model.model if response.plan else None,
        "model_tier": response.plan.model_tier if response.plan else None,
        "model_source_tier": (
            response.plan.model.source_tier if response.plan else None
        ),
        "action_names": (
            [action.tool_name for action in response.plan.actions]
            if response.plan
            else []
        ),
        "tool_outcomes": [
            {"tool_name": result.tool_name, "status": result.status}
            for result in response.results
        ],
        "context_sources": (
            [source.model_dump(mode="json") for source in response.plan.context_sources]
            if response.plan
            else []
        ),
        "requires_confirmation": (
            response.plan.requires_confirmation if response.plan else False
        ),
    }
    if (
        response.status == "needs_clarification"
        and response.plan is None
        and response.message == _GENERIC_UNSUPPORTED_AGENT_MESSAGE
    ):
        metadata.update(
            {
                "reason": "unsupported_agent_request",
                "improvement_log": True,
                "message_sanitized": _sanitize_agent_audit_message(message),
            }
        )
        return metadata
    metadata["message_sanitized"] = _sanitize_agent_audit_message(message)
    return metadata


def _datetime_or_none(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


_ONBOARDING_STATUS_LABELS = {
    "pending": "Needs review",
    "selected": "Selected",
    "reachingout": "Reaching out",
    "awaitingcontribution": "Awaiting contribution",
    "onboarded": "Onboarded",
    "waitlist": "Waitlist",
    "rejected": "Rejected",
}
_DASHBOARD_ONBOARDING_STATUS_VALUES = frozenset(_ONBOARDING_STATUS_LABELS)
_DASHBOARD_ONBOARDING_TERMINAL_STATUSES = frozenset(
    {"onboarded", "waitlist", "rejected"}
)


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


def _normalize_dashboard_onboarding_status(value: Any) -> str | None:
    normalized = _normalize_onboarding_state_key(value)
    if normalized not in _DASHBOARD_ONBOARDING_STATUS_VALUES:
        return None
    return normalized


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


def _dashboard_job_lead_scrape_status_payload(
    job: JobRecord | None,
) -> dict[str, Any]:
    """Return a Gigs-scoped view of the latest HN scrape background job."""
    if job is None:
        return {
            "status": "not_run",
            "job_id": None,
            "source": "hackernews_who_is_hiring",
            "story_id": None,
            "created_at": None,
            "updated_at": None,
            "last_error": None,
            "result": None,
        }

    payload = job.payload if isinstance(job.payload, dict) else {}
    kwargs = payload.get("kwargs")
    kwargs = kwargs if isinstance(kwargs, dict) else {}
    raw_result = payload.get("result")
    result = _redact_sensitive_payload(raw_result)
    result_source = raw_result.get("source") if isinstance(raw_result, dict) else None
    return {
        "status": job.status.value,
        "job_id": job.id,
        "source": result_source or kwargs.get("source") or "hackernews_who_is_hiring",
        "story_id": kwargs.get("story_id"),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "last_error": job.last_error,
        "result": result,
    }


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip()
        return bool(normalized) and normalized.lower() not in {"no discord", "none"}
    return bool(value)


def _intake_resume_present(latest_intake_submission: Any) -> bool:
    if not isinstance(latest_intake_submission, dict):
        return False
    normalized_payload = latest_intake_submission.get("normalized_payload")
    if not isinstance(normalized_payload, dict):
        return False
    return _is_present(normalized_payload.get("resume_file_name")) or _is_present(
        normalized_payload.get("resume_url")
    )


def _preferred_intake_resume_submission(person: dict[str, Any]) -> Any:
    return person.get("latest_resume_intake_submission") or person.get(
        "latest_intake_submission"
    )


def _intake_resume_file_name(latest_intake_submission: Any) -> str | None:
    if not isinstance(latest_intake_submission, dict):
        return None
    normalized_payload = latest_intake_submission.get("normalized_payload")
    if not isinstance(normalized_payload, dict):
        return None
    resume_file_name = normalized_payload.get("resume_file_name")
    if isinstance(resume_file_name, str) and resume_file_name.strip():
        return resume_file_name.strip()
    return None


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


_DASHBOARD_INTAKE_RESUME_EXISTS_SQL = """
EXISTS (
    SELECT 1
    FROM onboarding_intake_submissions resume_intake
    WHERE (
        resume_intake.crm_contact_id = people.crm_contact_id
        OR (
            resume_intake.crm_contact_id IS NULL
            AND resume_intake.email = lower(people.email)
        )
    )
    AND coalesce(
        nullif(btrim(resume_intake.normalized_payload->>'resume_file_name'), ''),
        nullif(btrim(resume_intake.normalized_payload->>'resume_url'), '')
    ) IS NOT NULL
)
"""


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
            f"""
            (
            (
                latest_resume_id IS NOT NULL
                AND btrim(latest_resume_id) <> ''
            )
            OR (
                latest_resume_name IS NOT NULL
                AND btrim(latest_resume_name) <> ''
            )
            OR {_DASHBOARD_INTAKE_RESUME_EXISTS_SQL}
            )
        """
        )
    elif resume == "missing":
        conditions.append(
            f"""
            (
                latest_resume_id IS NULL
                OR btrim(latest_resume_id) = ''
            )
            AND (
                latest_resume_name IS NULL
                OR btrim(latest_resume_name) = ''
            )
            AND NOT {_DASHBOARD_INTAKE_RESUME_EXISTS_SQL}
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
            professional_roles,
            linkedin,
            skills,
            latest_resume_id,
            latest_resume_name,
            onboarding_state,
            onboarder,
            onboarding_updated_at,
            onboarding_email_sent_at,
            onboarding_email_sent_by,
            onboarding_email_recipient,
            latest_intake_submission,
            latest_resume_intake_submission,
            sync_status,
            created_at,
            updated_at
        FROM people
        LEFT JOIN LATERAL (
            SELECT jsonb_build_object(
                'source', onboarding_intake_submissions.source,
                'form_id', onboarding_intake_submissions.form_id,
                'submission_id', onboarding_intake_submissions.submission_id,
                'submitted_at', onboarding_intake_submissions.submitted_at,
                'normalized_payload', onboarding_intake_submissions.normalized_payload,
                'created_at', onboarding_intake_submissions.created_at
            ) AS latest_intake_submission
            FROM onboarding_intake_submissions
            WHERE onboarding_intake_submissions.crm_contact_id = people.crm_contact_id
               OR (
                    onboarding_intake_submissions.crm_contact_id IS NULL
                    AND onboarding_intake_submissions.email = lower(people.email)
               )
            ORDER BY
                onboarding_intake_submissions.submitted_at DESC NULLS LAST,
                onboarding_intake_submissions.created_at DESC
            LIMIT 1
        ) intake ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_build_object(
                'source', onboarding_intake_submissions.source,
                'form_id', onboarding_intake_submissions.form_id,
                'submission_id', onboarding_intake_submissions.submission_id,
                'submitted_at', onboarding_intake_submissions.submitted_at,
                'normalized_payload', onboarding_intake_submissions.normalized_payload,
                'created_at', onboarding_intake_submissions.created_at
            ) AS latest_resume_intake_submission
            FROM onboarding_intake_submissions
            WHERE (
                onboarding_intake_submissions.crm_contact_id = people.crm_contact_id
                OR (
                    onboarding_intake_submissions.crm_contact_id IS NULL
                    AND onboarding_intake_submissions.email = lower(people.email)
                )
            )
            AND coalesce(
                nullif(btrim(onboarding_intake_submissions.normalized_payload->>'resume_file_name'), ''),
                nullif(btrim(onboarding_intake_submissions.normalized_payload->>'resume_url'), '')
            ) IS NOT NULL
            ORDER BY
                onboarding_intake_submissions.submitted_at DESC NULLS LAST,
                onboarding_intake_submissions.created_at DESC
            LIMIT 1
        ) latest_resume_intake ON true
        {where_clause}
        ORDER BY updated_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(sql), params)
            rows = cursor.fetchall()

    return _shape_dashboard_people_rows(rows)


def _shape_dashboard_people_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    for row in rows:
        roles = row.get("discord_roles") or []
        skills = row.get("skills") or []
        person = dict(row)
        person.pop("latest_intake_sort_at", None)
        latest_intake_submission = person.get("latest_intake_submission")
        if isinstance(latest_intake_submission, dict):
            latest_intake_submission.pop("raw_payload", None)
        latest_resume_intake_submission = person.get("latest_resume_intake_submission")
        if isinstance(latest_resume_intake_submission, dict):
            latest_resume_intake_submission.pop("raw_payload", None)
        intake_resume_submission = _preferred_intake_resume_submission(person)
        intake_resume_present = _intake_resume_present(intake_resume_submission)
        if not _is_present(person.get("latest_resume_name")):
            intake_resume_file_name = _intake_resume_file_name(intake_resume_submission)
            if intake_resume_file_name:
                person["latest_resume_name"] = intake_resume_file_name
        person["created_at"] = _datetime_or_none(row.get("created_at"))
        person["updated_at"] = _datetime_or_none(row.get("updated_at"))
        person["onboarding_updated_at"] = _datetime_or_none(
            row.get("onboarding_updated_at")
        )
        person["onboarding_email_sent_at"] = _datetime_or_none(
            row.get("onboarding_email_sent_at")
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
            or _is_present(row.get("latest_resume_name"))
            or intake_resume_present,
            "roles_count": len(roles) if isinstance(roles, list) else 0,
            "skills_count": len(skills) if isinstance(skills, list) else 0,
        }
        people.append(person)
    return people


_ORPHAN_INTAKE_SEARCH_SQL = """
(
    coalesce(email, '') || ' ' ||
    coalesce(normalized_payload->>'name', '') || ' ' ||
    coalesce(normalized_payload->>'first_name', '') || ' ' ||
    coalesce(normalized_payload->>'last_name', '') || ' ' ||
    coalesce(normalized_payload->>'github_username', '') || ' ' ||
    coalesce(normalized_payload->>'linkedin_url', '')
)
"""

_ORPHAN_INTAKE_RESUME_SQL = """
coalesce(
    nullif(btrim(normalized_payload->>'resume_file_name'), ''),
    nullif(btrim(normalized_payload->>'resume_url'), '')
)
"""

_ORPHAN_INTAKE_SKILLS_SQL = """
coalesce(
    nullif(btrim(normalized_payload->>'primary_skills_interests'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_next_js'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_react_native_expo'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_supabase'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_ai_ml_engineering'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_python_django_fastapi'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_wordpress'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_devops'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_crypto_blockchain'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_chat_bots'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_unity_video_game'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_project_management'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_client_management'), ''),
    nullif(btrim(normalized_payload->>'skill_proficiency_sales_marketing'), ''),
    nullif(
        btrim(normalized_payload->>'skill_proficiency_internal_business_development'),
        ''
    )
)
"""


def _list_dashboard_orphan_intake_submissions(
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
    if onboarding_state not in (None, "pending"):
        return []
    if onboarder or discord == "linked" or email_508 == "present":
        return []

    conditions: list[str] = [
        "crm_contact_id IS NULL",
        """
        NOT EXISTS (
            SELECT 1
            FROM people
            WHERE lower(people.email) = onboarding_intake_submissions.email
                AND people.sync_status = 'active'
                AND people.is_member = false
                AND people.contact_type ILIKE %s
                AND (
                    people.onboarding_state IS NULL
                    OR replace(
                        replace(
                            replace(lower(btrim(people.onboarding_state)), '_', ''),
                            '-',
                            ''
                        ),
                        ' ',
                        ''
                    ) NOT IN ('onboarded', 'waitlist', 'rejected')
                )
        )
        """,
    ]
    params: list[Any] = ["%prospect%"]

    if normalized_query:
        conditions.append(f"{_ORPHAN_INTAKE_SEARCH_SQL} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if resume == "present":
        conditions.append(f"{_ORPHAN_INTAKE_RESUME_SQL} IS NOT NULL")
    elif resume == "missing":
        conditions.append(f"{_ORPHAN_INTAKE_RESUME_SQL} IS NULL")
    if skills == "present":
        conditions.append(f"{_ORPHAN_INTAKE_SKILLS_SQL} IS NOT NULL")
    elif skills == "missing":
        conditions.append(f"{_ORPHAN_INTAKE_SKILLS_SQL} IS NULL")

    params.append(limit)
    where_clause = " AND ".join(conditions)
    sql = f"""
        SELECT
            id::text,
            NULL::text AS crm_contact_id,
            coalesce(
                nullif(btrim(normalized_payload->>'name'), ''),
                nullif(
                    btrim(
                        concat_ws(
                            ' ',
                            normalized_payload->>'first_name',
                            nullif(normalized_payload->>'last_name', 'Unknown')
                        )
                    ),
                    ''
                ),
                email
            ) AS name,
            email,
            NULL::text AS email_508,
            NULL::text AS discord_user_id,
            normalized_payload->>'discord_username' AS discord_username,
            ARRAY[]::text[] AS discord_roles,
            normalized_payload->>'github_username' AS github_username,
            'Prospect' AS contact_type,
            false AS is_member,
            normalized_payload->>'address_country' AS address_country,
            normalized_payload->>'address_city' AS address_city,
            normalized_payload->>'address_state' AS address_state,
            normalized_payload->>'timezone' AS timezone,
            normalized_payload->>'seniority_level' AS seniority,
            normalized_payload->>'linkedin_url' AS linkedin,
            CASE WHEN {_ORPHAN_INTAKE_SKILLS_SQL} IS NULL
                THEN ARRAY[]::text[]
                ELSE ARRAY['application']::text[]
            END AS skills,
            NULL::text AS latest_resume_id,
            normalized_payload->>'resume_file_name' AS latest_resume_name,
            'pending' AS onboarding_state,
            NULL::text AS onboarder,
            created_at AS onboarding_updated_at,
            NULL::timestamptz AS onboarding_email_sent_at,
            NULL::text AS onboarding_email_sent_by,
            NULL::text AS onboarding_email_recipient,
            jsonb_build_object(
                'source', source,
                'form_id', form_id,
                'submission_id', submission_id,
                'submitted_at', submitted_at,
                'normalized_payload', normalized_payload,
                'created_at', created_at
            ) AS latest_intake_submission,
            CASE WHEN {_ORPHAN_INTAKE_RESUME_SQL} IS NULL
                THEN NULL::jsonb
                ELSE jsonb_build_object(
                    'source', source,
                    'form_id', form_id,
                    'submission_id', submission_id,
                    'submitted_at', submitted_at,
                    'normalized_payload', normalized_payload,
                    'created_at', created_at
                )
            END AS latest_resume_intake_submission,
            'intake' AS sync_status,
            created_at,
            updated_at
        FROM onboarding_intake_submissions
        WHERE {where_clause}
        ORDER BY submitted_at DESC NULLS LAST, created_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(sql), params)
            return cursor.fetchall()


def _dashboard_onboarding_row_sort_key(
    row: Mapping[str, Any],
) -> tuple[int, float, str]:
    state_rank = (
        0
        if _normalize_onboarding_state_key(row.get("onboarding_state")) == "pending"
        else 1
    )
    updated_at = _dashboard_onboarding_row_activity_at(row)
    updated_rank = (
        -updated_at.timestamp() if isinstance(updated_at, datetime) else float("inf")
    )
    name = str(row.get("name") or "").casefold()
    return (state_rank, updated_rank, name or "\uffff")


def _dashboard_onboarding_row_activity_at(row: Mapping[str, Any]) -> Any:
    latest_intake_submission = row.get("latest_intake_submission")
    if isinstance(latest_intake_submission, Mapping):
        submitted_at = latest_intake_submission.get("submitted_at")
        if isinstance(submitted_at, datetime):
            return submitted_at
        created_at = latest_intake_submission.get("created_at")
        if isinstance(created_at, datetime):
            return created_at
    return row.get("onboarding_updated_at")


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
            f"""
            (
            (
                latest_resume_id IS NOT NULL
                AND btrim(latest_resume_id) <> ''
            )
            OR (
                latest_resume_name IS NOT NULL
                AND btrim(latest_resume_name) <> ''
            )
            OR {_DASHBOARD_INTAKE_RESUME_EXISTS_SQL}
            )
        """
        )
    elif resume == "missing":
        conditions.append(
            f"""
            (
                latest_resume_id IS NULL
                OR btrim(latest_resume_id) = ''
            )
            AND (
                latest_resume_name IS NULL
                OR btrim(latest_resume_name) = ''
            )
            AND NOT {_DASHBOARD_INTAKE_RESUME_EXISTS_SQL}
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
            professional_roles,
            linkedin,
            skills,
            latest_resume_id,
            latest_resume_name,
            onboarding_state,
            onboarder,
            onboarding_updated_at,
            onboarding_email_sent_at,
            onboarding_email_sent_by,
            onboarding_email_recipient,
            latest_intake_submission,
            latest_intake_sort_at,
            latest_resume_intake_submission,
            sync_status,
            created_at,
            updated_at
        FROM people
        LEFT JOIN LATERAL (
            SELECT
                jsonb_build_object(
                    'source', onboarding_intake_submissions.source,
                    'form_id', onboarding_intake_submissions.form_id,
                    'submission_id', onboarding_intake_submissions.submission_id,
                    'submitted_at', onboarding_intake_submissions.submitted_at,
                    'normalized_payload', onboarding_intake_submissions.normalized_payload,
                    'created_at', onboarding_intake_submissions.created_at
                ) AS latest_intake_submission,
                coalesce(
                    onboarding_intake_submissions.submitted_at,
                    onboarding_intake_submissions.created_at
                ) AS latest_intake_sort_at
            FROM onboarding_intake_submissions
            WHERE onboarding_intake_submissions.crm_contact_id = people.crm_contact_id
               OR (
                    onboarding_intake_submissions.crm_contact_id IS NULL
                    AND onboarding_intake_submissions.email = lower(people.email)
               )
            ORDER BY
                onboarding_intake_submissions.submitted_at DESC NULLS LAST,
                onboarding_intake_submissions.created_at DESC
            LIMIT 1
        ) intake ON true
        LEFT JOIN LATERAL (
            SELECT jsonb_build_object(
                'source', onboarding_intake_submissions.source,
                'form_id', onboarding_intake_submissions.form_id,
                'submission_id', onboarding_intake_submissions.submission_id,
                'submitted_at', onboarding_intake_submissions.submitted_at,
                'normalized_payload', onboarding_intake_submissions.normalized_payload,
                'created_at', onboarding_intake_submissions.created_at
            ) AS latest_resume_intake_submission
            FROM onboarding_intake_submissions
            WHERE (
                onboarding_intake_submissions.crm_contact_id = people.crm_contact_id
                OR (
                    onboarding_intake_submissions.crm_contact_id IS NULL
                    AND onboarding_intake_submissions.email = lower(people.email)
                )
            )
            AND coalesce(
                nullif(btrim(onboarding_intake_submissions.normalized_payload->>'resume_file_name'), ''),
                nullif(btrim(onboarding_intake_submissions.normalized_payload->>'resume_url'), '')
            ) IS NOT NULL
            ORDER BY
                onboarding_intake_submissions.submitted_at DESC NULLS LAST,
                onboarding_intake_submissions.created_at DESC
            LIMIT 1
        ) latest_resume_intake ON true
        WHERE {where_clause}
        ORDER BY
            CASE WHEN COALESCE({_ONBOARDING_STATE_NORMALIZED_SQL}, '') = 'pending'
                THEN 0 ELSE 1 END,
            coalesce(latest_intake_sort_at, onboarding_updated_at) DESC NULLS LAST,
            name ASC NULLS LAST
        LIMIT %s
    """
    params.append(limit)
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(sql), params)
            rows = cursor.fetchall()

    orphan_rows = _list_dashboard_orphan_intake_submissions(
        query=query,
        limit=limit,
        onboarding_state=onboarding_state,
        onboarder=onboarder,
        discord=discord,
        email_508=email_508,
        resume=resume,
        skills=skills,
    )
    combined_rows = sorted(
        rows + orphan_rows,
        key=_dashboard_onboarding_row_sort_key,
    )[:limit]
    return _shape_dashboard_people_rows(combined_rows)


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


def _dashboard_onboarding_email_marker(contact_id: str) -> dict[str, Any]:
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    onboarding_email_sent_at,
                    onboarding_email_sent_by,
                    onboarding_email_recipient
                FROM people
                WHERE crm_contact_id = %s
                LIMIT 1
                """,
                (contact_id,),
            )
            row = cursor.fetchone()

    if not row:
        return {
            "onboarding_email_sent_at": None,
            "onboarding_email_sent_by": None,
            "onboarding_email_recipient": None,
        }
    return {
        "onboarding_email_sent_at": _datetime_or_none(
            row.get("onboarding_email_sent_at")
        ),
        "onboarding_email_sent_by": row.get("onboarding_email_sent_by"),
        "onboarding_email_recipient": row.get("onboarding_email_recipient"),
    }


def _mark_dashboard_onboarding_email_sent(
    *,
    contact_id: str,
    recipient_email: str,
    actor: str,
) -> dict[str, Any]:
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE people
                SET
                    onboarding_email_sent_at = NOW(),
                    onboarding_email_sent_by = %s,
                    onboarding_email_recipient = %s,
                    updated_at = NOW()
                WHERE crm_contact_id = %s
                RETURNING
                    onboarding_email_sent_at,
                    onboarding_email_sent_by,
                    onboarding_email_recipient
                """,
                (actor, recipient_email, contact_id),
            )
            row = cursor.fetchone()
        conn.commit()

    if not row:
        raise DashboardOnboardingEmailError("contact_not_found", status_code=404)
    return {
        "onboarding_email_sent_at": _datetime_or_none(
            row.get("onboarding_email_sent_at")
        ),
        "onboarding_email_sent_by": row.get("onboarding_email_sent_by"),
        "onboarding_email_recipient": row.get("onboarding_email_recipient"),
    }


def _dashboard_onboarding_contact_for_email(contact_id: str) -> dict[str, Any]:
    normalized_contact_id = contact_id.strip()
    if not normalized_contact_id:
        raise DashboardOnboardingEmailError("contact_id_required")
    if not _is_dashboard_onboarding_contact_eligible(normalized_contact_id):
        raise DashboardOnboardingEmailError(
            "contact_not_onboarding_eligible",
            status_code=403,
        )

    client = EspoClient(settings.espo_base_url, settings.espo_api_key)
    contact = client.request("GET", f"Contact/{normalized_contact_id}")
    if str(contact.get("id") or "").strip() != normalized_contact_id:
        raise DashboardOnboardingEmailError("crm_profile_mismatch", status_code=409)

    onboarding_status = _normalize_onboarding_state_key(
        contact.get(_ONBOARDING_STATUS_FIELD)
    )
    if onboarding_status in _DASHBOARD_ONBOARDING_TERMINAL_STATUSES:
        raise DashboardOnboardingEmailError(
            "candidate_terminal_onboarding_state",
            status_code=403,
        )

    return contact


def _dashboard_preferred_contact_email(contact: dict[str, Any]) -> str | None:
    for field_name in ("emailAddress", "c508Email"):
        candidate = str(contact.get(field_name) or "").strip()
        if not candidate:
            continue
        try:
            return validate_plain_email(candidate, field_name)
        except ValueError:
            continue
    return None


def _dashboard_contact_display_name(contact: dict[str, Any]) -> str:
    return str(contact.get("name") or "").strip() or "CRM contact"


def _dashboard_session_profile_row(session: AuthSession) -> dict[str, Any] | None:
    conditions: list[str] = []
    params: list[Any] = []
    if session.crm_contact_id:
        conditions.append("crm_contact_id = %s")
        params.append(session.crm_contact_id)
    if _session_actor_provider(session) == ActorProvider.DISCORD and session.subject:
        conditions.append("discord_user_id = %s")
        params.append(session.subject)
    if session.email:
        normalized_email = session.email.strip().casefold()
        conditions.append("(LOWER(email) = %s OR LOWER(email_508) = %s)")
        params.extend([normalized_email, normalized_email])
    if not conditions:
        return None

    query = f"""
        SELECT name, email, email_508
        FROM people
        WHERE sync_status = 'active'
          AND ({" OR ".join(conditions)})
        ORDER BY updated_at DESC NULLS LAST
        LIMIT 1
        """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), params)
            return cursor.fetchone()


def _dashboard_sender_names(session: AuthSession) -> tuple[str, str]:
    profile = _dashboard_session_profile_row(session)
    profile_name = str(profile.get("name") or "").strip() if profile else ""
    if profile_name and profile_name != "508.dev":
        display_name = profile_name
    else:
        display_name = str(session.display_name or "").strip()
    if not display_name and session.email:
        display_name = session.email.partition("@")[0]
    if not display_name:
        display_name = session.subject
    display_name = " ".join(display_name.split())
    signature_name = display_name.split()[0] if display_name.split() else display_name
    return display_name, signature_name


def _dashboard_reply_to_email(session: AuthSession) -> str | None:
    if session.email:
        try:
            return validate_plain_email(session.email, "session.email")
        except ValueError:
            pass

    profile = _dashboard_session_profile_row(session)
    if not profile:
        return None
    for field_name in ("email_508", "email"):
        candidate = str(profile.get(field_name) or "").strip()
        if not candidate:
            continue
        try:
            return validate_plain_email(candidate, field_name)
        except ValueError:
            continue
    return None


def _dashboard_sender_cc_email(session: AuthSession) -> str | None:
    if session.email and session.email.lower().endswith("@508.dev"):
        try:
            return validate_plain_email(session.email, "session.email")
        except ValueError:
            pass

    profile = _dashboard_session_profile_row(session)
    if not profile:
        return None
    candidate = str(profile.get("email_508") or "").strip()
    if not candidate or not candidate.lower().endswith("@508.dev"):
        return None
    try:
        return validate_plain_email(candidate, "email_508")
    except ValueError:
        return None


def _dashboard_onboarding_email_actor(session: AuthSession) -> str:
    return session.email or session.subject


def _dashboard_onboarding_email_smtp_config() -> OnboardingEmailSmtpConfig:
    return OnboardingEmailSmtpConfig(
        smtp_server=settings.onboarding_email_smtp_server,
        smtp_port=settings.onboarding_email_smtp_port,
        smtp_use_ssl=settings.onboarding_email_smtp_use_ssl,
        smtp_starttls=settings.onboarding_email_smtp_starttls,
        smtp_username=settings.onboarding_email_smtp_username,
        smtp_password=settings.onboarding_email_smtp_password,
        smtp_timeout_seconds=settings.onboarding_email_smtp_timeout_seconds,
    )


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


def _increment_dashboard_count(counts: dict[str, int], value: Any) -> None:
    key = str(value or "unknown").strip() or "unknown"
    counts[key] = counts.get(key, 0) + 1


def _dashboard_agent_actor(row: dict[str, Any]) -> str:
    return str(
        row.get("actor_display_name")
        or row.get("actor_subject")
        or row.get("actor_provider")
        or "Unknown"
    )


def _shape_dashboard_agent_request_report(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "total": sum(
            1
            for row in rows
            if str(row.get("action") or "agent.request") == "agent.request"
        ),
        "handled": 0,
        "requires_confirmation": 0,
        "needs_clarification": 0,
        "unsupported": 0,
        "denied_or_failed": 0,
    }
    status_counts: dict[str, int] = {}
    intent_counts: dict[str, int] = {}
    planner_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    tool_outcome_counts: dict[str, int] = {}
    recent_unsupported: list[dict[str, Any]] = []

    for row in rows:
        event_action = str(row.get("action") or "agent.request")
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        status = str(metadata.get("status") or "unknown")
        intent = metadata.get("intent") or "unknown"
        planner = metadata.get("planner") or "unknown"
        model = metadata.get("model") or "unknown"

        if event_action == "agent.request":
            _increment_dashboard_count(status_counts, status)
            _increment_dashboard_count(intent_counts, intent)
            _increment_dashboard_count(planner_counts, planner)
            _increment_dashboard_count(model_counts, model)
            for action_name in metadata.get("action_names") or []:
                _increment_dashboard_count(action_counts, action_name)
        for outcome in metadata.get("tool_outcomes") or []:
            if not isinstance(outcome, dict):
                continue
            tool_name = str(outcome.get("tool_name") or "unknown")
            status_label = str(outcome.get("status") or "unknown")
            _increment_dashboard_count(
                tool_outcome_counts, f"{tool_name}:{status_label}"
            )

        if event_action != "agent.request":
            continue
        if status in {"executed", "requires_confirmation"}:
            summary["handled"] += 1
        if status == "requires_confirmation" or metadata.get("requires_confirmation"):
            summary["requires_confirmation"] += 1
        if status == "needs_clarification":
            summary["needs_clarification"] += 1
        if status == "denied" or row.get("result") in {
            AuditResult.DENIED,
            AuditResult.ERROR,
            "denied",
            "error",
        }:
            summary["denied_or_failed"] += 1

        is_unsupported = (
            metadata.get("improvement_log") is True
            or metadata.get("reason") == "unsupported_agent_request"
        )
        if not is_unsupported:
            continue

        summary["unsupported"] += 1
        message_sanitized = str(metadata.get("message_sanitized") or "").strip()
        if not message_sanitized:
            continue
        recent_unsupported.append(
            {
                "id": row.get("id"),
                "occurred_at": _datetime_or_none(row.get("occurred_at")),
                "actor": _dashboard_agent_actor(row),
                "message_sanitized": message_sanitized,
                "result": row.get("result"),
                "correlation_id": row.get("correlation_id"),
            }
        )

    return {
        "summary": summary,
        "status_counts": status_counts,
        "intent_counts": intent_counts,
        "planner_counts": planner_counts,
        "model_counts": model_counts,
        "action_counts": action_counts,
        "tool_outcome_counts": tool_outcome_counts,
        "recent_unsupported": recent_unsupported,
    }


def _dashboard_agent_request_report(limit: int) -> dict[str, Any]:
    limit = _limit_dashboard_count(limit)
    sql = """
        WITH recent_requests AS (
            SELECT
                id::text,
                occurred_at,
                action,
                result,
                actor_provider,
                actor_subject,
                actor_display_name,
                correlation_id,
                metadata
            FROM audit_events
            WHERE action = 'agent.request'
            ORDER BY occurred_at DESC
            LIMIT %s
        ), recent_confirmations AS (
            SELECT
                id::text,
                occurred_at,
                action,
                result,
                actor_provider,
                actor_subject,
                actor_display_name,
                correlation_id,
                metadata
            FROM audit_events
            WHERE action = 'agent.confirmation'
            ORDER BY occurred_at DESC
            LIMIT %s
        )
        SELECT * FROM recent_requests
        UNION ALL
        SELECT * FROM recent_confirmations
        ORDER BY occurred_at DESC
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, (limit, limit))
            rows = cursor.fetchall()

    return _shape_dashboard_agent_request_report(rows)


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
    client.request(
        "PUT",
        f"Contact/{normalized_contact_id}",
        {_ONBOARDER_FIELD: onboarder_username},
    )
    return {
        "status": "updated",
        "contact_id": normalized_contact_id,
        "contact_name": full_contact.get("name") or "CRM contact",
        "onboarder": onboarder_username,
        "previous_state": normalized_state or None,
        "onboarding_state": current_state or None,
        "onboarding_status_label": _onboarding_status_label(current_state),
    }


def _update_dashboard_onboarding_status_in_crm(
    *,
    contact_id: str,
    status: str,
) -> dict[str, Any]:
    normalized_contact_id = contact_id.strip()
    if not normalized_contact_id:
        raise DashboardOnboarderAssignmentError("contact_id_required")

    normalized_status = _normalize_dashboard_onboarding_status(status)
    if normalized_status is None:
        raise DashboardOnboarderAssignmentError("invalid_status")

    if not _is_dashboard_onboarding_contact_eligible(normalized_contact_id):
        raise DashboardOnboarderAssignmentError(
            "contact_not_onboarding_eligible",
            status_code=403,
        )

    client = EspoClient(settings.espo_base_url, settings.espo_api_key)
    full_contact = client.request("GET", f"Contact/{normalized_contact_id}")
    if _ONBOARDING_STATUS_FIELD not in full_contact:
        raise DashboardOnboarderAssignmentError(
            "missing_onboarding_status_field",
            status_code=422,
        )

    previous_state = str(full_contact.get(_ONBOARDING_STATUS_FIELD) or "").strip()
    onboarder_raw = str(full_contact.get(_ONBOARDER_FIELD) or "").strip()
    onboarder_username = _normalize_508_username(onboarder_raw)
    onboarder_is_unassigned = (
        onboarder_username is None or onboarder_raw.casefold() in {"none", "no discord"}
    )
    if normalized_status == "reachingout" and onboarder_is_unassigned:
        raise DashboardOnboarderAssignmentError(
            "onboarder_required_for_active_stage",
            status_code=409,
        )
    client.request(
        "PUT",
        f"Contact/{normalized_contact_id}",
        {_ONBOARDING_STATUS_FIELD: normalized_status},
    )
    return {
        "status": "updated",
        "contact_id": normalized_contact_id,
        "contact_name": full_contact.get("name") or "CRM contact",
        "previous_state": previous_state or None,
        "onboarding_state": normalized_status,
        "onboarding_status_label": _onboarding_status_label(normalized_status),
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
    response.delete_cookie(key=settings.auth_session_cookie_name, path="/dashboard")


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


async def _audit_dashboard_update_onboarding_status(
    session: AuthSession,
    *,
    result: AuditResult,
    contact_id: str,
    onboarding_status: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    audit_metadata = {
        "source": "dashboard",
        "onboarding_status": onboarding_status,
    }
    audit_metadata.update(metadata or {})
    await _write_auth_audit_event(
        action="crm.update_onboarding_status",
        result=result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="crm_contact",
        resource_id=contact_id,
        metadata=audit_metadata,
    )


async def _audit_dashboard_onboarding_email(
    session: AuthSession,
    *,
    result: AuditResult,
    contact_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    audit_metadata = {
        "source": "dashboard",
    }
    audit_metadata.update(metadata or {})
    await _write_auth_audit_event(
        action="onboarding.email",
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

    postgres_migrations_ok = getattr(request.app.state, "postgres_migrations_ok", True)
    if hasattr(request.app.state, "postgres_conn"):
        postgres_ok = await _is_postgres_connection_healthy(request.app)
    else:
        postgres_ok = await asyncio.to_thread(is_postgres_healthy, settings)

    intake_resume_scan_required = settings.effective_intake_resume_require_virus_scan
    intake_resume_scan_configured = settings.intake_resume_virus_scan_configured
    healthy = (
        redis_ok
        and postgres_ok
        and postgres_migrations_ok
        and intake_resume_scan_configured
    )
    payload = {
        "status": "healthy" if healthy else "degraded",
        "redis_connected": redis_ok,
        "postgres_connected": postgres_ok,
        "postgres_migrations_ok": postgres_migrations_ok,
        "intake_resume_scan_required": intake_resume_scan_required,
        "intake_resume_scan_configured": intake_resume_scan_configured,
        "queue_name": settings.redis_queue_name,
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)


async def ingest_handler(request: Request, source: str) -> JSONResponse:
    """Validate and enqueue incoming webhook payloads."""
    if not _is_webhook_authorized(request):
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
    if not _is_webhook_authorized(request):
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


@dataclass(frozen=True)
class _ValidatedRerunJob:
    source_job: JobRecord
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


async def _validate_rerun_job(
    job_id: str,
) -> tuple[_ValidatedRerunJob | None, dict[str, Any] | None, int]:
    """Validate a persisted job can be rerun and return its call payload."""
    normalized_job_id = job_id.strip()
    if not normalized_job_id:
        return None, {"error": "job_id_required"}, 400

    source_job = await asyncio.to_thread(get_job, settings, normalized_job_id)
    if source_job is None:
        return None, {"error": "job_not_found"}, 404

    fn = JOB_FUNCTIONS.get(source_job.type)
    if fn is None:
        return (
            None,
            {
                "error": "unsupported_job_type",
                "job_type": source_job.type,
            },
            400,
        )

    raw_payload = source_job.payload
    if not isinstance(raw_payload, dict):
        return None, {"error": "invalid_job_payload"}, 400
    if "args" not in raw_payload or "kwargs" not in raw_payload:
        return None, {"error": "invalid_job_payload"}, 400

    raw_args = raw_payload["args"]
    raw_kwargs = raw_payload["kwargs"]
    if not isinstance(raw_args, list) or not isinstance(raw_kwargs, dict):
        return None, {"error": "invalid_job_payload"}, 400

    return (
        _ValidatedRerunJob(
            source_job=source_job,
            fn=fn,
            args=tuple(raw_args),
            kwargs=raw_kwargs,
        ),
        None,
        200,
    )


async def _rerun_job(job_id: str, queue: QueueClient) -> tuple[dict[str, Any], int]:
    """Create a duplicate queued job from an existing persisted job."""
    validated, error_payload, status_code = await _validate_rerun_job(job_id)
    if validated is None:
        return cast(dict[str, Any], error_payload), status_code

    rerun_idempotency_key = f"manual-rerun:{validated.source_job.id}:{_generate_ulid()}"

    try:
        rerun_job: EnqueuedJob = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=validated.fn,
            args=validated.args,
            kwargs=validated.kwargs,
            settings=settings,
            idempotency_key=rerun_idempotency_key,
            max_attempts=validated.source_job.max_attempts,
        )
    except Exception:
        logger.exception(
            "Failed rerunning job source_job_id=%s type=%s",
            validated.source_job.id,
            validated.source_job.type,
        )
        return {"error": "enqueue_failed"}, 503

    return {
        "status": "queued",
        "source_job_id": validated.source_job.id,
        "job_id": rerun_job.id,
        "type": validated.source_job.type,
        "created": rerun_job.created,
    }, 202


async def _rerun_job_dry_run(job_id: str) -> tuple[dict[str, Any], int]:
    """Validate a dashboard job rerun and describe the enqueue without writing."""
    validated, error_payload, status_code = await _validate_rerun_job(job_id)
    if validated is None:
        return cast(dict[str, Any], error_payload), status_code

    return {
        "status": "dry_run",
        "dry_run": True,
        "source_job_id": validated.source_job.id,
        "type": validated.source_job.type,
        "would_enqueue": {
            "queue": settings.redis_queue_name,
            "job_type": validated.source_job.type,
            "args_count": len(validated.args),
            "kwargs_keys": sorted(str(key) for key in validated.kwargs),
            "idempotency_key_prefix": f"manual-rerun:{validated.source_job.id}:",
            "max_attempts": validated.source_job.max_attempts,
        },
    }, 200


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
    if not await _session_has_any_dashboard_permission(session):
        return HTMLResponse("Forbidden", status_code=403)
    return HTMLResponse(dashboard_html(), status_code=200)


async def dashboard_me_handler(request: Request) -> JSONResponse:
    """Return the dashboard session identity."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=None,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    return JSONResponse(await _session_payload(session))


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


async def dashboard_gigs_handler(
    request: Request,
    status: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=200),
    include_historical: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    """Return dashboard-visible Discord gigs and candidate fit snapshots."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    normalized_status: EngagementStatus | None = None
    if status:
        normalized_status = normalize_engagement_status(status)
        if (
            normalized_status is EngagementStatus.UNKNOWN
            and status.strip().casefold()
            not in {
                "unknown",
            }
        ):
            return JSONResponse({"error": "invalid_status"}, status_code=400)

    normalized_query = query.strip() if query is not None else ""
    include_all = _session_has_steering_access(session)
    gigs = await asyncio.to_thread(
        list_dashboard_engagements,
        settings,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
        include_historical=include_historical and include_all,
        status=normalized_status,
        query=normalized_query or None,
        limit=limit,
    )
    return JSONResponse(gigs)


async def dashboard_gig_detail_handler(
    request: Request,
    engagement_id: str,
) -> JSONResponse:
    """Return one dashboard-visible Discord gig by id."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    normalized_engagement_id = _valid_uuid_or_none(engagement_id)
    if normalized_engagement_id is None:
        return JSONResponse({"error": "invalid_engagement_id"}, status_code=400)

    include_all = _session_has_steering_access(session)
    gigs = await asyncio.to_thread(
        list_dashboard_engagements,
        settings,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
        include_historical=True,
        engagement_id=normalized_engagement_id,
        limit=1,
    )
    if not gigs:
        return JSONResponse({"error": "gig_not_found"}, status_code=404)
    return JSONResponse(gigs[0])


async def dashboard_job_channels_handler(request: Request) -> JSONResponse:
    """Return registered Discord job forums for dashboard lead posting."""
    include_available = request.query_params.get(
        "include_available", ""
    ).casefold() in {
        "1",
        "true",
        "yes",
    }
    required_permission = (
        DASHBOARD_PERMISSION_CONFIGURATION_READ
        if include_available
        else DASHBOARD_PERMISSION_GIGS_READ
    )
    _session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=required_permission,
    )
    if error_response is not None:
        return error_response

    return JSONResponse(
        await _dashboard_job_channels_payload(
            request,
            include_available=include_available,
        )
    )


async def _dashboard_job_channels_payload(
    request: Request,
    *,
    include_available: bool = False,
) -> dict[str, Any]:
    """Return registered job-channel metadata, with live forum options when requested."""
    guild_id = str(settings.discord_server_id or "").strip()
    if not guild_id:
        payload: dict[str, Any] = {"channels": []}
        if include_available:
            payload["available_channels"] = []
        return payload

    live_channels = await _list_job_channels_from_bot(
        request,
        register_defaults=not include_available,
    )
    if live_channels is not None:
        channels = live_channels.get("channels")
        if isinstance(channels, list):
            payload = {"channels": channels}
            if include_available:
                available_channels = live_channels.get("available_channels")
                payload["available_channels"] = (
                    available_channels
                    if isinstance(available_channels, list)
                    else channels
                )
            return payload

    channels = await asyncio.to_thread(
        list_registered_job_post_channel_configs,
        settings,
        guild_id=guild_id,
    )
    payload = {
        "channels": [
            {
                "channel_id": channel.channel_id,
                "posting_type": channel.posting_type.value,
                **({"registered": True} if include_available else {}),
            }
            for channel in channels
        ]
    }
    if include_available:
        payload["available_channels"] = payload["channels"]
    return payload


def _valid_discord_channel_id_or_none(channel_id: str) -> str | None:
    normalized = str(channel_id or "").strip()
    if not normalized or not normalized.isdigit():
        return None
    return normalized


async def _dashboard_job_forum_is_available(
    request: Request,
    *,
    channel_id: str,
) -> bool | None:
    """Return whether the bot exposes this forum as a safe registration target."""
    live_channels = await _list_job_channels_from_bot(
        request,
        register_defaults=False,
    )
    if live_channels is None:
        return None
    available_channels = live_channels.get("available_channels")
    if not isinstance(available_channels, list):
        return None
    return any(
        isinstance(candidate, Mapping)
        and str(candidate.get("channel_id") or "").strip() == channel_id
        for candidate in available_channels
    )


async def dashboard_update_job_channel_handler(
    request: Request,
    channel_id: str,
) -> JSONResponse:
    """Register or update one Discord job forum from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_CONFIGURATION_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    normalized_channel_id = _valid_discord_channel_id_or_none(channel_id)
    if normalized_channel_id is None:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=channel_id,
            action="job_channel.update",
            metadata={"error": "invalid_channel_id"},
        )
        return JSONResponse({"error": "invalid_channel_id"}, status_code=400)

    try:
        payload = DashboardJobChannelUpdateRequest.model_validate(await request.json())
    except ValidationError as exc:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"error": "invalid_job_channel_payload", "detail": exc.errors()},
        )
        return JSONResponse(
            {"error": "invalid_job_channel_payload", "detail": exc.errors()},
            status_code=400,
        )
    except Exception as exc:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"error": "invalid_json", "detail": str(exc)},
        )
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    guild_id = str(settings.discord_server_id or "").strip()
    if not guild_id:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"error": "discord_server_not_configured"},
        )
        return JSONResponse({"error": "discord_server_not_configured"}, status_code=409)

    forum_available = await _dashboard_job_forum_is_available(
        request,
        channel_id=normalized_channel_id,
    )
    if forum_available is None:
        logger.warning(
            "Refusing dashboard job-channel update because forum validation is unavailable "
            "guild=%s channel=%s",
            guild_id,
            normalized_channel_id,
        )
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"error": "job_forum_validation_unavailable"},
        )
        return JSONResponse(
            {"error": "job_forum_validation_unavailable"},
            status_code=503,
        )
    if not forum_available:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"error": "job_forum_not_available"},
        )
        return JSONResponse({"error": "job_forum_not_available"}, status_code=403)

    try:
        created = await asyncio.to_thread(
            register_job_post_channel,
            settings,
            guild_id=guild_id,
            channel_id=normalized_channel_id,
            posting_type=payload.posting_type,
            update_existing=True,
        )
    except Exception as exc:
        logger.warning(
            "Failed updating dashboard job channel guild=%s channel=%s: %s",
            guild_id,
            normalized_channel_id,
            exc,
        )
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.update",
            metadata={"posting_type": payload.posting_type, "error": str(exc)},
        )
        return JSONResponse({"error": "job_channel_update_failed"}, status_code=500)

    action = "job_channel.register" if created else "job_channel.update"
    await _audit_dashboard_job_channel_change(
        session,
        result=AuditResult.SUCCESS,
        channel_id=normalized_channel_id,
        action=action,
        metadata={"posting_type": payload.posting_type, "created": created},
    )
    return JSONResponse(
        await _dashboard_job_channels_payload(request, include_available=True)
    )


async def dashboard_delete_job_channel_handler(
    request: Request,
    channel_id: str,
) -> JSONResponse:
    """Deregister one Discord job forum from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_CONFIGURATION_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    normalized_channel_id = _valid_discord_channel_id_or_none(channel_id)
    if normalized_channel_id is None:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=channel_id,
            action="job_channel.unregister",
            metadata={"error": "invalid_channel_id"},
        )
        return JSONResponse({"error": "invalid_channel_id"}, status_code=400)

    guild_id = str(settings.discord_server_id or "").strip()
    if not guild_id:
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.unregister",
            metadata={"error": "discord_server_not_configured"},
        )
        return JSONResponse({"error": "discord_server_not_configured"}, status_code=409)

    try:
        removed = await asyncio.to_thread(
            unregister_job_post_channel,
            settings,
            guild_id=guild_id,
            channel_id=normalized_channel_id,
        )
    except Exception as exc:
        logger.warning(
            "Failed deleting dashboard job channel guild=%s channel=%s: %s",
            guild_id,
            normalized_channel_id,
            exc,
        )
        await _audit_dashboard_job_channel_change(
            session,
            result=AuditResult.ERROR,
            channel_id=normalized_channel_id,
            action="job_channel.unregister",
            metadata={"error": str(exc)},
        )
        return JSONResponse({"error": "job_channel_delete_failed"}, status_code=500)

    await _audit_dashboard_job_channel_change(
        session,
        result=AuditResult.SUCCESS,
        channel_id=normalized_channel_id,
        action="job_channel.unregister",
        metadata={"removed": removed},
    )
    return JSONResponse(
        await _dashboard_job_channels_payload(request, include_available=True)
    )


async def dashboard_job_leads_handler(
    request: Request,
    status: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=50),
) -> JSONResponse:
    """Return sourced job leads for dashboard review."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    normalized_status: JobLeadStatus | None
    if status is None or status.strip().casefold() in {"", "all", "any"}:
        normalized_status = None
    else:
        try:
            normalized_status = JobLeadStatus(status.strip().casefold())
        except ValueError:
            return JSONResponse({"error": "invalid_status"}, status_code=400)

    leads = await asyncio.to_thread(
        list_job_leads,
        settings,
        status=normalized_status,
        limit=limit,
    )
    return JSONResponse(
        jsonable_encoder([job_lead_display_payload(lead) for lead in leads])
    )


async def dashboard_job_lead_scrape_status_handler(
    request: Request,
    job_id: str | None = Query(default=None),
) -> JSONResponse:
    """Return the latest, or one requested, HN scrape status for the Gigs view."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    normalized_job_id = job_id.strip() if job_id is not None else ""
    if job_id is not None and not normalized_job_id:
        return JSONResponse({"error": "job_id_required"}, status_code=400)

    if normalized_job_id:
        job = await asyncio.to_thread(get_job, settings, normalized_job_id)
        if job is None or job.type != _JOB_LEAD_SCRAPE_JOB_TYPE:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
    else:
        jobs = await asyncio.to_thread(
            list_jobs,
            settings,
            created_after=datetime.now(tz=timezone.utc)
            - timedelta(days=_JOB_LEAD_SCRAPE_STATUS_LOOKBACK_DAYS),
            limit=1,
            job_type=_JOB_LEAD_SCRAPE_JOB_TYPE,
        )
        job = jobs[0] if jobs else None

    return JSONResponse(
        jsonable_encoder(_dashboard_job_lead_scrape_status_payload(job))
    )


async def dashboard_review_job_lead_handler(
    request: Request,
    lead_id: str,
) -> JSONResponse:
    """Review or restore one sourced job lead from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
        payload = DashboardJobLeadReviewRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    reviewer = _session_discord_actor_id(session) or session.subject
    try:
        lead = await asyncio.to_thread(
            review_job_lead,
            settings,
            lead_id=lead_id,
            status=payload.status,
            reviewer_discord_user_id=reviewer,
        )
    except ValueError:
        return JSONResponse({"error": "invalid_status"}, status_code=400)

    if lead is None:
        return JSONResponse({"error": "job_lead_not_found"}, status_code=404)
    reviewed_lead_id = (
        str(lead.get("id") or lead_id)
        if isinstance(lead, dict)
        else str(lead.id or lead_id)
    )
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="job_leads.review",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="job_lead",
        resource_id=reviewed_lead_id,
        metadata={
            "lead_id": reviewed_lead_id,
            "status": payload.status,
        },
    )
    return JSONResponse(jsonable_encoder(job_lead_display_payload(lead)))


async def dashboard_clear_job_lead_staging_recovery_handler(
    request: Request,
    lead_id: str,
) -> JSONResponse:
    """Clear a holding-thread recovery block after an operator removes the orphan."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        payload = DashboardJobLeadStagingRecoveryClearRequest.model_validate(
            await request.json()
        )
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    cleared = await asyncio.to_thread(
        clear_job_lead_staging_cleanup_required,
        settings,
        lead_id=lead_id,
    )
    if cleared is None:
        return JSONResponse(
            {"error": "job_lead_staging_recovery_not_found"},
            status_code=409,
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="job_leads.staging_recovery.clear",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="job_lead",
        resource_id=cleared.id,
        metadata={"lead_id": cleared.id, "orphan_deleted": payload.orphan_deleted},
    )
    return JSONResponse(jsonable_encoder(job_lead_display_payload(cleared)))


async def dashboard_stage_job_lead_handler(
    request: Request,
    lead_id: str,
) -> JSONResponse:
    """Stage one sourced lead in the unqualified Discord holding forum."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    reviewer = _session_discord_actor_id(session) or session.subject
    result, status_code = await _stage_job_lead_to_discord(
        request,
        lead_id=lead_id,
        reviewer_discord_user_id=reviewer,
    )
    if status_code >= 400:
        return JSONResponse(result, status_code=status_code)

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="job_leads.stage",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="job_lead",
        resource_id=str(result.get("lead_id") or lead_id),
        metadata={
            "lead_id": result.get("lead_id") or lead_id,
            "guild_id": result.get("guild_id"),
            "channel_id": result.get("channel_id"),
            "thread_id": result.get("thread_id"),
        },
    )
    return JSONResponse(result)


async def dashboard_post_job_lead_handler(
    request: Request,
    lead_id: str,
) -> JSONResponse:
    """Promote one qualified lead into a registered Discord jobs forum."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        payload = DashboardJobLeadPostRequest.model_validate(body or {})
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    reviewer = _session_discord_actor_id(session) or session.subject
    result, status_code = await _post_job_lead_to_discord(
        request,
        lead_id=lead_id,
        reviewer_discord_user_id=reviewer,
        channel_id=payload.channel_id,
        tags=payload.tags,
        engagement_status=EngagementStatus(payload.engagement_status),
    )
    if status_code >= 400:
        return JSONResponse(result, status_code=status_code)

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="job_leads.promote",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="job_lead",
        resource_id=str(result.get("lead_id") or lead_id),
        metadata={
            "lead_id": result.get("lead_id") or lead_id,
            "guild_id": result.get("guild_id"),
            "channel_id": result.get("channel_id"),
            "thread_id": result.get("thread_id"),
            "engagement_status": result.get("engagement_status")
            or payload.engagement_status,
        },
    )
    return JSONResponse(result)


async def dashboard_sync_job_leads_handler(request: Request) -> JSONResponse:
    """Enqueue a sourced job lead scrape from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None
    steering_error = _dashboard_steering_or_error(session)
    if steering_error is not None:
        return steering_error

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        payload = DashboardJobLeadSyncRequest.model_validate(body or {})
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_source = payload.source.strip() or "hackernews_who_is_hiring"
    if normalized_source.casefold() not in {
        "hn",
        "hackernews",
        "hackernews_who_is_hiring",
    }:
        return JSONResponse({"error": "unsupported_job_lead_source"}, status_code=400)
    now = datetime.now(tz=timezone.utc)
    job = await asyncio.to_thread(
        enqueue_job,
        queue=request.app.state.queue,
        fn=JOB_FUNCTIONS["scrape_job_leads_job"],
        args=(),
        settings=settings,
        kwargs={"source": normalized_source, "story_id": payload.story_id},
        idempotency_key=(
            f"job-leads:{normalized_source}:{payload.story_id or 'latest'}:"
            f"{now.strftime('%Y%m%d%H%M')}"
        ),
    )
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="job_leads.sync",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="job_lead_source",
        resource_id=normalized_source,
        metadata={
            "source": normalized_source,
            "story_id": payload.story_id,
            "job_id": job.id,
            "created": job.created,
        },
    )
    return JSONResponse(
        {
            "status": "queued",
            "source": normalized_source,
            "story_id": payload.story_id,
            "job_id": job.id,
            "created": job.created,
        },
        status_code=202,
    )


async def dashboard_notifications_handler(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
) -> JSONResponse:
    """Return dashboard notifications for the current operator."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    include_all = _session_has_steering_access(session)
    notifications = await asyncio.to_thread(
        list_dashboard_notifications,
        settings,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
        stale_days=settings.gig_recruiting_stale_days,
        contacted_reminder_days=settings.gig_contacted_reminder_days,
        max_age_days=settings.gig_recruiting_reminder_max_age_days,
        limit=limit,
    )
    return JSONResponse(
        {
            "stale_days": settings.gig_recruiting_stale_days,
            "contacted_reminder_days": settings.gig_contacted_reminder_days,
            "notifications": notifications,
        }
    )


async def dashboard_projects_handler(
    request: Request,
    query: str | None = Query(default=None),
    status: str | None = Query(default="Open"),
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    """Return locally cached ERP project rows for the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    include_all = _session_has_steering_access(session)
    viewer_emails = (
        []
        if include_all
        else await asyncio.to_thread(_dashboard_project_viewer_emails, session)
    )

    projects = await asyncio.to_thread(
        list_dashboard_projects,
        settings,
        query=query,
        status=status,
        viewer_emails=viewer_emails,
        include_all=include_all,
        limit=limit,
    )
    summary = _project_summary_for_visible_rows(projects)
    if include_all:
        summary = await asyncio.to_thread(project_cache_summary, settings)
    return JSONResponse({"projects": projects, "summary": summary})


async def dashboard_project_member_candidates_handler(
    request: Request,
    query: str = Query(default="", min_length=0, max_length=200),
) -> JSONResponse:
    """Return validated @508.dev people candidates for project roster writes."""
    _session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response

    normalized_query = query.strip()
    if not _project_roster_user_candidate_query_ready(normalized_query):
        return JSONResponse([])
    candidates = await asyncio.to_thread(
        _project_roster_user_candidates,
        normalized_query,
    )
    return JSONResponse(candidates)


def _erpnext_client() -> ERPNextClient:
    base_url = (settings.erpnext_base_url or "").strip()
    api_key = (settings.erpnext_api_key or "").strip()
    if not base_url or not api_key:
        raise ERPNextAPIError("ERPNEXT_BASE_URL and ERPNEXT_API_KEY must be configured")
    return ERPNextClient(
        base_url,
        api_key,
        timeout_seconds=settings.erpnext_api_timeout_seconds,
    )


class HistoricalProjectMemberResolutionError(ValueError):
    """Raised when a historical roster entry cannot resolve to one person."""

    def __init__(
        self,
        code: str,
        *,
        candidates: list[dict[str, Any]] | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.candidates = candidates or []
        self.detail = detail


def _historical_project_member_error_payload(
    exc: HistoricalProjectMemberResolutionError,
    *,
    person: str,
) -> dict[str, Any]:
    """Return dashboard-friendly resolution error details."""
    if exc.detail:
        detail = exc.detail
    elif exc.code == "person_not_found":
        detail = (
            f'No CRM person, ERPNext user, or ERPNext supplier matched "{person}". '
            "Try an email address or an exact name from CRM/ERPNext."
        )
    elif exc.code == "candidate_not_found":
        detail = (
            "The selected person record is no longer available. Search again and "
            "choose one of the current matches."
        )
    elif exc.code == "ambiguous_person":
        detail = (
            f'Multiple people matched "{person}". Choose the matching person record.'
        )
    else:
        detail = "Unable to resolve that person for the historical roster."

    return {
        "error": exc.code,
        "detail": detail,
        "person": person,
        "candidates": exc.candidates,
    }


def _project_roster_user_error_payload(
    exc: HistoricalProjectMemberResolutionError,
    *,
    user: str,
) -> dict[str, Any]:
    """Return dashboard-friendly ERP roster person resolution errors."""
    if exc.detail:
        detail = exc.detail
    elif exc.code == "candidate_required":
        detail = (
            f'Choose a verified @508.dev person for "{user}" from the dropdown '
            "before adding them to the ERP roster."
        )
    elif exc.code == "person_not_found":
        detail = (
            f'No active CRM person or ERPNext user with a @508.dev email matched "{user}". '
            "Try a 508 email address or a more specific name."
        )
    elif exc.code == "candidate_not_found":
        detail = (
            "The selected person record is no longer available. Search again and "
            "choose one of the current matches."
        )
    elif exc.code == "invalid_candidate":
        detail = "Choose a person with a verified @508.dev email before adding them."
    else:
        detail = "Unable to resolve that person for the ERP project roster."

    return {
        "error": exc.code,
        "detail": detail,
        "person": user,
        "candidates": exc.candidates,
    }


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _historical_candidate_key(candidate: dict[str, Any]) -> str:
    email = _text_or_none(candidate.get("email"))
    if email:
        return f"email:{email.casefold()}"
    for key in ("crm_contact_id", "erpnext_user_id", "supplier_erpnext_id"):
        value = _text_or_none(candidate.get(key))
        if value:
            return f"{key}:{value.casefold()}"
    label = _text_or_none(candidate.get("label")) or _generate_ulid()
    return f"label:{label.casefold()}"


def _merge_historical_candidate(
    candidates: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
) -> None:
    key = _historical_candidate_key(candidate)
    existing = candidates.setdefault(
        key,
        {
            "candidate_id": key,
            "label": candidate.get("label"),
            "full_name": candidate.get("full_name"),
            "email": candidate.get("email"),
            "crm_contact_id": None,
            "erpnext_user_id": None,
            "supplier_erpnext_id": None,
            "supplier_name": None,
            "sources": [],
        },
    )
    for field in (
        "label",
        "full_name",
        "email",
        "crm_contact_id",
        "erpnext_user_id",
        "supplier_erpnext_id",
        "supplier_name",
    ):
        value = _text_or_none(candidate.get(field))
        if value and not existing.get(field):
            existing[field] = value
    for source in candidate.get("sources") or []:
        if source not in existing["sources"]:
            existing["sources"].append(source)
    if not existing.get("label"):
        existing["label"] = (
            existing.get("full_name")
            or existing.get("email")
            or existing.get("erpnext_user_id")
            or existing.get("supplier_erpnext_id")
        )


def _dashboard_people_candidates_for_project_member(query: str) -> list[dict[str, Any]]:
    normalized_query = query.strip()
    if not normalized_query:
        return []
    params: list[Any]
    if "@" in normalized_query:
        where_clause = """
            sync_status = 'active'
            AND (
                LOWER(COALESCE(email, '')) = LOWER(%s)
                OR LOWER(COALESCE(email_508, '')) = LOWER(%s)
            )
        """
        params = [normalized_query, normalized_query]
    else:
        where_clause = (
            f"sync_status = 'active' AND {_DASHBOARD_PEOPLE_SEARCH_SQL} ILIKE %s"
        )
        params = [f"%{normalized_query}%"]
    params.append(10)
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT crm_contact_id, name, email, email_508
                FROM people
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                params,
            )
            rows = [dict(row) for row in cursor.fetchall()]

    candidates: list[dict[str, Any]] = []
    for row in rows:
        email = _text_or_none(row.get("email_508")) or _text_or_none(row.get("email"))
        full_name = _text_or_none(row.get("name"))
        candidates.append(
            {
                "label": full_name or email or row.get("crm_contact_id"),
                "full_name": full_name,
                "email": email,
                "crm_contact_id": _text_or_none(row.get("crm_contact_id")),
                "sources": ["CRM"],
            }
        )
    return candidates


def _erpnext_candidates_for_project_member(query: str) -> list[dict[str, Any]]:
    if not (settings.erpnext_base_url and settings.erpnext_api_key):
        return []
    client = _erpnext_client()
    try:
        users = client.search_users(query, limit=10)
        supplier_rows_by_id: dict[str, dict[str, Any]] = {}
        for supplier in client.search_suppliers(query, limit=10):
            supplier_id = _text_or_none(supplier.get("name"))
            if supplier_id:
                supplier_rows_by_id[supplier_id] = supplier
        for user in users:
            email = _text_or_none(user.get("email")) or _text_or_none(user.get("name"))
            if not email:
                continue
            for supplier in client.search_suppliers(email, limit=10):
                supplier_id = _text_or_none(supplier.get("name"))
                if supplier_id:
                    supplier_rows_by_id[supplier_id] = supplier
    finally:
        client.close()

    candidates: list[dict[str, Any]] = []
    for user in users:
        email = _text_or_none(user.get("email")) or _text_or_none(user.get("name"))
        full_name = _text_or_none(user.get("full_name"))
        candidates.append(
            {
                "label": full_name or email or user.get("name"),
                "full_name": full_name,
                "email": email,
                "erpnext_user_id": _text_or_none(user.get("name")) or email,
                "sources": ["ERP User"],
            }
        )
    for supplier in supplier_rows_by_id.values():
        email = _text_or_none(supplier.get("email_id"))
        supplier_id = _text_or_none(supplier.get("name"))
        supplier_name = _text_or_none(supplier.get("supplier_name")) or supplier_id
        candidates.append(
            {
                "label": supplier_name or email or supplier_id,
                "full_name": supplier_name,
                "email": email,
                "supplier_erpnext_id": supplier_id,
                "supplier_name": supplier_name,
                "sources": ["ERP Supplier"],
            }
        )
    return candidates


def _erpnext_user_candidates_for_project_member(query: str) -> list[dict[str, Any]]:
    """Return ERPNext User candidates without supplier fan-out for typeahead."""
    if not (settings.erpnext_base_url and settings.erpnext_api_key):
        return []
    client = _erpnext_client()
    try:
        users = client.search_users(query, limit=10)
    finally:
        client.close()

    candidates: list[dict[str, Any]] = []
    for user in users:
        email = _text_or_none(user.get("email")) or _text_or_none(user.get("name"))
        full_name = _text_or_none(user.get("full_name"))
        candidates.append(
            {
                "label": full_name or email or user.get("name"),
                "full_name": full_name,
                "email": email,
                "erpnext_user_id": _text_or_none(user.get("name")) or email,
                "sources": ["ERP User"],
            }
        )
    return candidates


def _historical_project_member_candidates(query: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    crm_candidates = _dashboard_people_candidates_for_project_member(query)
    for candidate in crm_candidates:
        _merge_historical_candidate(merged, candidate)
    erp_queries = [query]
    for candidate in crm_candidates:
        email = _text_or_none(candidate.get("email"))
        if email and email.casefold() not in {item.casefold() for item in erp_queries}:
            erp_queries.append(email)
    try:
        erpnext_candidates = [
            candidate
            for erp_query in erp_queries
            for candidate in _erpnext_candidates_for_project_member(erp_query)
        ]
    except ERPNextAPIError:
        logger.warning("Failed resolving historical project member in ERPNext")
        erpnext_candidates = []
    for candidate in erpnext_candidates:
        _merge_historical_candidate(merged, candidate)
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("label") or "").casefold(),
            str(item.get("email") or "").casefold(),
        ),
    )


def _erpnext_customer_lookup_url(customer_id: str) -> str | None:
    base_url = _text_or_none(settings.erpnext_base_url)
    normalized_customer_id = _text_or_none(customer_id)
    if not base_url or not normalized_customer_id:
        return None
    return (
        f"{base_url.rstrip('/')}/app/customer/{quote(normalized_customer_id, safe='')}"
    )


def _erpnext_project_lookup_url(project_id: str) -> str | None:
    base_url = _text_or_none(settings.erpnext_base_url)
    normalized_project_id = _text_or_none(project_id)
    if not base_url or not normalized_project_id:
        return None
    return f"{base_url.rstrip('/')}/app/project/{quote(normalized_project_id, safe='')}"


def _dashboard_project_fallback_from_erpnext(
    project_detail: dict[str, Any],
) -> dict[str, Any]:
    erpnext_project_id = _text_or_none(project_detail.get("name"))
    project_name = (
        _text_or_none(project_detail.get("project_name")) or erpnext_project_id
    )
    customer = _text_or_none(project_detail.get("customer"))
    return {
        "id": "",
        "display_name": project_name,
        "customer": customer,
        "erpnext_project_id": erpnext_project_id,
        "erpnext_project_url": _erpnext_project_lookup_url(erpnext_project_id or ""),
        "customer_erpnext_url": _erpnext_customer_lookup_url(customer or ""),
        "source_status": _text_or_none(project_detail.get("status")),
        "roster_members": [],
        "roster_count": 0,
        "linked_engagement_count": 0,
        "local_cache_pending": True,
    }


def _dashboard_shape_erpnext_customer(customer: dict[str, Any]) -> dict[str, Any]:
    customer_id = _text_or_none(customer.get("name")) or _text_or_none(
        customer.get("customer_name")
    )
    return {
        "name": customer_id,
        "customer_name": _text_or_none(customer.get("customer_name")) or customer_id,
        "customer_type": _text_or_none(customer.get("customer_type")),
        "default_currency": _text_or_none(customer.get("default_currency")),
        "account_manager": _text_or_none(customer.get("account_manager")),
        "url": _erpnext_customer_lookup_url(customer_id or ""),
    }


def _dashboard_shape_erpnext_contact(contact: dict[str, Any]) -> dict[str, Any]:
    contact_id = _text_or_none(contact.get("name"))
    full_name = _text_or_none(contact.get("full_name")) or " ".join(
        part
        for part in [
            _text_or_none(contact.get("first_name")),
            _text_or_none(contact.get("last_name")),
        ]
        if part
    )
    return {
        "name": contact_id,
        "first_name": _text_or_none(contact.get("first_name")),
        "last_name": _text_or_none(contact.get("last_name")),
        "full_name": full_name or contact_id,
        "email_id": _text_or_none(contact.get("email_id")),
        "phone": _text_or_none(contact.get("phone")),
        "mobile_no": _text_or_none(contact.get("mobile_no")),
        "company_name": _text_or_none(contact.get("company_name")),
    }


def _dashboard_shape_erpnext_user(user: dict[str, Any]) -> dict[str, Any]:
    user_id = _text_or_none(user.get("name")) or _text_or_none(user.get("email"))
    email = _text_or_none(user.get("email")) or user_id
    return {
        "name": user_id,
        "email": email,
        "full_name": _text_or_none(user.get("full_name")) or email or user_id,
        "enabled": user.get("enabled"),
    }


def _dashboard_shape_erpnext_cost_center(row: dict[str, Any]) -> dict[str, Any]:
    name = _text_or_none(row.get("name")) or _text_or_none(row.get("cost_center_name"))
    return {
        "name": name,
        "cost_center_name": _text_or_none(row.get("cost_center_name")) or name,
        "company": _text_or_none(row.get("company")),
    }


def _search_erpnext_customers(query: str) -> list[dict[str, Any]]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        return []
    client = _erpnext_client()
    try:
        return [
            _dashboard_shape_erpnext_customer(customer)
            for customer in client.search_customers(normalized_query, limit=10)
        ]
    finally:
        client.close()


def _search_erpnext_contacts(query: str) -> list[dict[str, Any]]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        return []
    client = _erpnext_client()
    try:
        return [
            _dashboard_shape_erpnext_contact(contact)
            for contact in client.search_contacts(normalized_query, limit=10)
        ]
    finally:
        client.close()


def _search_erpnext_account_managers(query: str) -> list[dict[str, Any]]:
    normalized_query = query.strip()
    if len(normalized_query) < 2:
        return []
    client = _erpnext_client()
    try:
        users = client.search_users(
            normalized_query,
            limit=10,
            enabled_only=True,
            email_domain="@508.dev",
        )
    finally:
        client.close()
    return [_dashboard_shape_erpnext_user(user) for user in users]


def _list_erpnext_cost_centers() -> list[dict[str, Any]]:
    client = _erpnext_client()
    try:
        rows = client.list_cost_centers(limit=100)
    finally:
        client.close()

    options = [_dashboard_shape_erpnext_cost_center(row) for row in rows]
    by_name = {
        cost_center["name"]: cost_center
        for cost_center in options
        if _text_or_none(cost_center.get("name")) is not None
    }
    if "Projects - 5" not in by_name:
        by_name["Projects - 5"] = {
            "name": "Projects - 5",
            "cost_center_name": "Projects",
            "company": None,
        }
    return sorted(
        by_name.values(),
        key=lambda row: (row.get("name") != "Projects - 5", str(row.get("name") or "")),
    )


def _default_activity_type_for_project(project_name: str) -> str:
    return f"Engineering for {project_name.strip()}"[:140]


def _has_any_project_create_text(
    payload: DashboardProjectCreateRequest,
    field_names: tuple[str, ...],
) -> bool:
    return any(
        _text_or_none(getattr(payload, field_name)) for field_name in field_names
    )


def _create_erpnext_project_setup(
    payload: DashboardProjectCreateRequest,
) -> dict[str, Any]:
    project_name = payload.project_name.strip()
    if not project_name:
        raise ValueError("project_name_required")
    if len(project_name) > 140:
        raise ValueError("project_name_too_long")

    default_cost_center = (payload.default_cost_center or "Projects - 5").strip()
    if len(default_cost_center) > 140:
        raise ValueError("default_cost_center_too_long")
    customer_details = _text_or_none(payload.customer_details)
    if customer_details is not None and len(customer_details) > 2000:
        raise ValueError("customer_details_too_long")
    customer_website = _text_or_none(payload.customer_website)
    if customer_website is not None and len(customer_website) > 255:
        raise ValueError("customer_website_too_long")

    customer_id: str | None = None
    customer_name: str | None = None
    currency = "USD"
    account_manager: str | None = None
    if payload.customer_mode == "existing":
        customer_id = _text_or_none(payload.customer)
        if customer_id is None:
            raise ValueError("customer_required")
        if len(customer_id) > 140:
            raise ValueError("customer_too_long")
    else:
        customer_name = _text_or_none(payload.customer_name)
        if customer_name is None:
            raise ValueError("customer_name_required")
        if len(customer_name) > 140:
            raise ValueError("customer_name_too_long")
        currency = (payload.default_billing_currency or "USD").strip().upper()
        if len(currency) > 3:
            raise ValueError("default_billing_currency_too_long")
        account_manager = _text_or_none(payload.account_manager)
        if account_manager is not None and len(account_manager) > 200:
            raise ValueError("account_manager_too_long")
        if account_manager is not None and not account_manager.casefold().endswith(
            "@508.dev"
        ):
            raise ValueError("account_manager_must_be_508_email")

    address_fields = (
        "address_line1",
        "address_line2",
        "address_city",
        "address_state",
        "address_country",
        "address_postal_code",
    )
    has_address = _has_any_project_create_text(payload, address_fields)
    address_line1 = _text_or_none(payload.address_line1)
    if has_address and address_line1 is None:
        raise ValueError("address_line1_required")

    contact_id = _text_or_none(payload.contact)
    contact_fields = (
        "contact_first_name",
        "contact_last_name",
        "contact_email",
        "contact_phone",
        "contact_mobile",
    )
    has_contact = _has_any_project_create_text(payload, contact_fields)
    contact_first_name = _text_or_none(payload.contact_first_name)
    if contact_id is not None and len(contact_id) > 140:
        raise ValueError("contact_too_long")
    if contact_id is None and has_contact and contact_first_name is None:
        raise ValueError("contact_first_name_required")

    explicit_activity_type_name = _text_or_none(payload.activity_type)
    if (
        explicit_activity_type_name is not None
        and len(explicit_activity_type_name) > 140
    ):
        raise ValueError("activity_type_too_long")
    activity_type_name = (
        explicit_activity_type_name or _default_activity_type_for_project(project_name)
    )
    if len(activity_type_name) > 140:
        raise ValueError("activity_type_too_long")

    cache_refresh_error: str | None = None
    cache_refresh_message: str | None = None
    setup_warnings: list[str] = []
    client = _erpnext_client()
    try:
        activity_type = client.ensure_activity_type(activity_type_name)
        customer_doc: dict[str, Any]
        created_customer_id: str | None = None
        if payload.customer_mode == "existing":
            assert customer_id is not None
            customer_doc = {"name": customer_id, "customer_name": customer_id}
        else:
            assert customer_name is not None
            customer_doc = client.create_customer(
                customer_name=customer_name,
                account_manager=account_manager,
                default_currency=currency or "USD",
            )

        customer_id = _text_or_none(customer_doc.get("name"))
        if customer_id is None:
            raise ERPNextAPIError("ERPNext Customer response is missing an id")
        if payload.customer_mode == "new":
            created_customer_id = customer_id

        def cleanup_created_customer() -> None:
            if created_customer_id is None:
                return
            try:
                client.delete_record("Customer", created_customer_id)
            except Exception:
                logger.exception(
                    "ERPNext Project creation failed and new Customer cleanup failed customer=%s",
                    created_customer_id,
                )

        try:
            project_detail = client.create_project(
                project_name=project_name,
                customer=customer_id,
                project_type="External",
                default_cost_center=default_cost_center or "Projects - 5",
            )
        except Exception:
            cleanup_created_customer()
            raise
        erpnext_project_id = _text_or_none(project_detail.get("name"))
        if erpnext_project_id is None:
            cleanup_created_customer()
            raise ERPNextAPIError("ERPNext Project response is missing an id")
        if account_manager is not None:
            try:
                project_detail = client.add_project_user(
                    erpnext_project_id, account_manager
                )
            except ERPNextAPIError:
                logger.exception(
                    "ERPNext Project was created but account manager roster setup failed project=%s account_manager=%s",
                    erpnext_project_id,
                    account_manager,
                )
                setup_warnings.append("account_manager_project_user_failed")

        address_doc: dict[str, Any] | None = None
        if has_address:
            assert address_line1 is not None
            address_doc = client.create_address(
                customer=customer_id,
                address_line1=address_line1,
                address_title=_text_or_none(customer_doc.get("customer_name"))
                or customer_id,
                address_line2=_text_or_none(payload.address_line2),
                city=_text_or_none(payload.address_city),
                state=_text_or_none(payload.address_state),
                country=_text_or_none(payload.address_country),
                pincode=_text_or_none(payload.address_postal_code),
                email_id=_text_or_none(payload.contact_email),
                phone=_text_or_none(payload.contact_phone)
                or _text_or_none(payload.contact_mobile),
            )

        contact_doc: dict[str, Any] | None = None
        if contact_id is not None:
            contact_doc = client.link_contact_to_customer(
                contact=contact_id,
                customer=customer_id,
            )
        elif has_contact:
            assert contact_first_name is not None
            contact_doc = client.create_contact(
                customer=customer_id,
                first_name=contact_first_name,
                last_name=_text_or_none(payload.contact_last_name),
                email_id=_text_or_none(payload.contact_email),
                phone=_text_or_none(payload.contact_phone),
                mobile_no=_text_or_none(payload.contact_mobile),
            )
        if account_manager is not None and contact_doc is not None:
            contact_doc_id = _text_or_none(contact_doc.get("name"))
            if contact_doc_id is not None:
                try:
                    contact_doc = client.set_contact_portal_user(
                        contact=contact_doc_id,
                        portal_user=account_manager,
                    )
                except ERPNextAPIError:
                    logger.exception(
                        "ERPNext Contact was linked but portal user setup failed contact=%s account_manager=%s",
                        contact_doc_id,
                        account_manager,
                    )
                    setup_warnings.append("account_manager_contact_user_failed")

        primary_address = (
            _text_or_none(address_doc.get("name")) if address_doc is not None else None
        )
        primary_contact = (
            _text_or_none(contact_doc.get("name")) if contact_doc is not None else None
        )
        if (
            primary_address
            or primary_contact
            or customer_details is not None
            or customer_website is not None
        ):
            customer_doc = client.set_customer_primary_records(
                customer_id,
                address=primary_address,
                contact=primary_contact,
                customer_details=customer_details,
                website=customer_website,
            )

        try:
            project = _refresh_cached_erpnext_project_with_client(
                client,
                erpnext_project_id,
                detail=project_detail,
            )
        except Exception:
            logger.exception(
                "ERPNext Project was created but local cache refresh failed project=%s",
                erpnext_project_id,
            )
            cache_refresh_error = "cache_refresh_failed"
            cache_refresh_message = (
                "Created the project in ERPNext, but the dashboard sync is still pending. "
                "Refresh projects in a moment."
            )
            project = _dashboard_project_fallback_from_erpnext(project_detail)
    finally:
        client.close()

    result: dict[str, Any] = {
        "project": project,
        "customer": _dashboard_shape_erpnext_customer(customer_doc),
        "activity_type": {
            "name": _text_or_none(activity_type.get("name")) or activity_type_name,
            "activity_type": _text_or_none(activity_type.get("activity_type"))
            or activity_type_name,
        },
        "address": address_doc,
        "contact": contact_doc,
    }
    if cache_refresh_error is not None:
        result["cache_refresh_error"] = cache_refresh_error
    if cache_refresh_message is not None:
        result["cache_refresh_message"] = cache_refresh_message
    if setup_warnings:
        result["setup_warnings"] = setup_warnings
        result["setup_warning_message"] = (
            "Created the project, but account manager setup needs follow-up."
        )
    return result


def _candidate_508_email(candidate: dict[str, Any]) -> str | None:
    email = _text_or_none(candidate.get("email"))
    if email and email.casefold().endswith("@508.dev"):
        return email
    return None


def _project_roster_user_candidate_query_ready(query: str) -> bool:
    normalized_query = query.strip()
    if not normalized_query:
        return False
    if "@" in normalized_query:
        return len(normalized_query) >= 5
    return len(normalized_query) >= 3


def _copy_project_roster_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for candidate in candidates:
        next_candidate = dict(candidate)
        sources = next_candidate.get("sources")
        if isinstance(sources, list):
            next_candidate["sources"] = list(sources)
        copied.append(next_candidate)
    return copied


def _project_roster_user_candidates_uncached(query: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    crm_candidates = [
        candidate
        for candidate in _dashboard_people_candidates_for_project_member(query)
        if _candidate_508_email(candidate) is not None
    ]
    try:
        erpnext_candidates = _erpnext_user_candidates_for_project_member(query)
    except ERPNextAPIError:
        logger.warning("Failed resolving project roster user in ERPNext")
        erpnext_candidates = []
    for candidate in erpnext_candidates:
        if _candidate_508_email(candidate) is not None:
            _merge_historical_candidate(merged, candidate)
    for candidate in crm_candidates:
        email = _candidate_508_email(candidate)
        if email and f"email:{email.casefold()}" in merged:
            _merge_historical_candidate(merged, candidate)
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("label") or "").casefold(),
            str(item.get("email") or "").casefold(),
        ),
    )


def _project_roster_user_candidates(query: str) -> list[dict[str, Any]]:
    """Return validated @508.dev people candidates for ERP roster writes."""
    normalized_query = query.strip()
    if not _project_roster_user_candidate_query_ready(normalized_query):
        return []

    cache_key = normalized_query.casefold()
    now = time.monotonic()
    with _PROJECT_ROSTER_USER_CANDIDATE_CACHE_LOCK:
        cached = _PROJECT_ROSTER_USER_CANDIDATE_CACHE.get(cache_key)
        if cached is not None:
            expires_at, candidates = cached
            if expires_at > now:
                return _copy_project_roster_candidates(candidates)
            _PROJECT_ROSTER_USER_CANDIDATE_CACHE.pop(cache_key, None)

    candidates = _project_roster_user_candidates_uncached(normalized_query)
    with _PROJECT_ROSTER_USER_CANDIDATE_CACHE_LOCK:
        if len(_PROJECT_ROSTER_USER_CANDIDATE_CACHE) >= (
            _PROJECT_ROSTER_USER_CANDIDATE_CACHE_MAX_SIZE
        ):
            oldest_key = min(
                _PROJECT_ROSTER_USER_CANDIDATE_CACHE,
                key=lambda key: _PROJECT_ROSTER_USER_CANDIDATE_CACHE[key][0],
            )
            _PROJECT_ROSTER_USER_CANDIDATE_CACHE.pop(oldest_key, None)
        _PROJECT_ROSTER_USER_CANDIDATE_CACHE[cache_key] = (
            now + _PROJECT_ROSTER_USER_CANDIDATE_CACHE_TTL_SECONDS,
            _copy_project_roster_candidates(candidates),
        )
    return candidates


def _resolve_project_roster_user_candidate(
    *,
    user: str,
    candidate_id: str | None,
) -> dict[str, Any]:
    normalized_user = user.strip()
    candidates = _project_roster_user_candidates(normalized_user)
    normalized_candidate_id = _text_or_none(candidate_id)
    if normalized_candidate_id is None:
        raise HistoricalProjectMemberResolutionError(
            "candidate_required",
            candidates=candidates,
        )
    if not candidates:
        raise HistoricalProjectMemberResolutionError("person_not_found")
    for candidate in candidates:
        if candidate.get("candidate_id") == normalized_candidate_id:
            if _candidate_508_email(candidate) is None:
                raise HistoricalProjectMemberResolutionError(
                    "invalid_candidate",
                    candidates=candidates,
                )
            return candidate
    raise HistoricalProjectMemberResolutionError(
        "candidate_not_found",
        candidates=candidates,
    )


def _resolve_historical_project_member(
    *,
    person: str,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    candidates = _historical_project_member_candidates(person)
    if not candidates:
        raise HistoricalProjectMemberResolutionError("person_not_found")
    normalized_candidate_id = _text_or_none(candidate_id)
    if normalized_candidate_id:
        for candidate in candidates:
            if candidate.get("candidate_id") == normalized_candidate_id:
                return candidate
        raise HistoricalProjectMemberResolutionError(
            "candidate_not_found",
            candidates=candidates,
        )
    normalized_person = person.strip().casefold()
    exact_matches = [
        candidate
        for candidate in candidates
        if normalized_person
        in {
            str(candidate.get("email") or "").strip().casefold(),
            str(candidate.get("erpnext_user_id") or "").strip().casefold(),
            str(candidate.get("supplier_erpnext_id") or "").strip().casefold(),
            str(candidate.get("crm_contact_id") or "").strip().casefold(),
        }
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(candidates) == 1:
        return candidates[0]
    raise HistoricalProjectMemberResolutionError(
        "ambiguous_person",
        candidates=candidates,
    )


def _cached_dashboard_project_by_id(project_id: str) -> dict[str, Any] | None:
    projects = list_dashboard_projects(
        settings,
        project_id=project_id,
        include_all=True,
        limit=1,
    )
    return projects[0] if projects else None


def _cached_erpnext_project_refs_by_id(project_ids: list[str]) -> dict[str, str | None]:
    """Return local project id to ERPNext Project id without dashboard enrichment."""
    if not project_ids:
        return {}
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    p.id::text AS id,
                    pei.external_id AS erpnext_project_id
                FROM projects p
                LEFT JOIN project_external_ids pei
                  ON pei.project_id = p.id
                 AND pei.source = 'erpnext'
                 AND pei.active = TRUE
                WHERE p.id = ANY(%s::uuid[])
                """,
                (project_ids,),
            )
            return {
                str(row["id"]): (
                    str(row["erpnext_project_id"])
                    if row.get("erpnext_project_id") is not None
                    else None
                )
                for row in cursor.fetchall()
            }


def _refresh_cached_erpnext_project(project_id: str) -> dict[str, Any]:
    client = _erpnext_client()
    try:
        return _refresh_cached_erpnext_project_with_client(client, project_id)
    finally:
        client.close()


def _refresh_cached_erpnext_project_with_client(
    client: ERPNextClient,
    project_id: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_detail = detail or client.get_project(project_id)
    payload = erpnext_project_to_input(project_detail)
    if payload is None:
        raise ERPNextAPIError("ERPNext Project response is missing an id or name")
    local_project_id = upsert_project(settings, payload)
    project = _cached_dashboard_project_by_id(local_project_id)
    if project is None:
        raise ERPNextAPIError("Updated project was not found in the local cache")
    return project


def _update_erpnext_project_status(
    *,
    external_project_id: str,
    status: str,
) -> dict[str, Any]:
    client = _erpnext_client()
    try:
        client.set_project_status(external_project_id, status)
    finally:
        client.close()
    return _refresh_cached_erpnext_project(external_project_id)


def _update_erpnext_project_type(
    *,
    external_project_id: str,
    project_type: str,
) -> dict[str, Any]:
    client = _erpnext_client()
    try:
        client.set_project_type(external_project_id, project_type)
    finally:
        client.close()
    return _refresh_cached_erpnext_project(external_project_id)


def _bulk_update_erpnext_projects(
    *,
    project_ids: list[str],
    fields: dict[str, str],
) -> dict[str, Any]:
    updated_projects: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    project_refs = _cached_erpnext_project_refs_by_id(project_ids)
    linked_project_refs: dict[str, str] = {}
    for project_id in project_ids:
        if project_id not in project_refs:
            failures.append({"project_id": project_id, "error": "project_not_found"})
            continue
        external_project_id = (project_refs[project_id] or "").strip()
        if not external_project_id:
            failures.append(
                {"project_id": project_id, "error": "project_not_linked_to_erpnext"}
            )
            continue
        linked_project_refs[project_id] = external_project_id
    if not linked_project_refs:
        return {"projects": updated_projects, "failures": failures}

    client: ERPNextClient | None = None
    try:
        client = _erpnext_client()
        for project_id, external_project_id in linked_project_refs.items():
            try:
                detail = client.update_project(external_project_id, fields)
                updated_projects.append(
                    _refresh_cached_erpnext_project_with_client(
                        client,
                        external_project_id,
                        detail=detail,
                    )
                )
            except ERPNextAPIError as exc:
                failures.append({"project_id": project_id, "error": str(exc)})
    except ERPNextAPIError as exc:
        failures.extend(
            {"project_id": project_id, "error": str(exc)}
            for project_id in linked_project_refs
        )
    finally:
        if client is not None:
            client.close()
    return {"projects": updated_projects, "failures": failures}


def _add_erpnext_project_user(
    *,
    external_project_id: str,
    user: str,
    candidate_id: str | None,
    activity_type: str | None = None,
    billing_rate: float | None = None,
    costing_rate: float | None = None,
) -> dict[str, Any]:
    candidate = _resolve_project_roster_user_candidate(
        user=user,
        candidate_id=candidate_id,
    )
    resolved_user = _candidate_508_email(candidate)
    if resolved_user is None:
        raise HistoricalProjectMemberResolutionError("invalid_candidate")
    client = _erpnext_client()
    try:
        activity_cost_request = None
        if activity_type or billing_rate is not None or costing_rate is not None:
            activity_cost_request = ActivityCostRequest(
                user=resolved_user,
                activity_type=activity_type or "",
                billing_rate=billing_rate,
                costing_rate=costing_rate,
            )
        result = add_engineer_to_project(
            client,
            project_id=external_project_id,
            user=resolved_user,
            activity_cost=activity_cost_request,
        )
    finally:
        client.close()
    project = _refresh_cached_erpnext_project(external_project_id)
    response = {"project": project, "activity_cost": result.get("activity_cost")}
    if result.get("activity_cost_error"):
        response["activity_cost_error"] = result["activity_cost_error"]
    if result.get("partial_success"):
        response["partial_success"] = True
    return response


def _setup_erpnext_engineer(payload: DashboardEngineerSetupRequest) -> dict[str, Any]:
    client = _erpnext_client()
    try:
        return setup_engineer(
            client,
            EngineerSetupRequest(
                email=payload.email,
                first_name=payload.first_name,
                middle_name=payload.middle_name,
                last_name=payload.last_name,
                country=payload.country,
                gender=payload.gender,
                date_of_birth=payload.date_of_birth,
                date_of_joining=payload.date_of_joining,
                personal_email=payload.personal_email,
                prefered_email=payload.prefered_email,
            ),
        )
    finally:
        client.close()


async def _audit_dashboard_engineer_setup(
    session: AuthSession,
    *,
    result: AuditResult,
    email: str,
    metadata: dict[str, Any],
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.engineer_setup",
        result=result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_user",
        resource_id=email,
        metadata={"source": "dashboard", **metadata},
    )


async def _audit_dashboard_configuration_change(
    session: AuthSession,
    *,
    result: AuditResult,
    key: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action=action,
        result=result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="runtime_config",
        resource_id=key,
        metadata={"source": "dashboard", **(metadata or {})},
    )


async def _audit_dashboard_job_channel_change(
    session: AuthSession,
    *,
    result: AuditResult,
    channel_id: str,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action=action,
        result=result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="discord_job_channel",
        resource_id=channel_id,
        metadata={"source": "dashboard", **(metadata or {})},
    )


def _remove_erpnext_project_user(
    *,
    external_project_id: str,
    user: str,
) -> dict[str, Any]:
    client = _erpnext_client()
    try:
        client.remove_project_user(external_project_id, user)
    finally:
        client.close()
    return _refresh_cached_erpnext_project(external_project_id)


async def _dashboard_cached_project_or_error(
    project_id: str,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    normalized_project_id = _valid_uuid_or_none(project_id)
    if normalized_project_id is None:
        return None, JSONResponse({"error": "invalid_project_id"}, status_code=400)
    project = await asyncio.to_thread(
        _cached_dashboard_project_by_id,
        normalized_project_id,
    )
    if project is None:
        return None, JSONResponse({"error": "project_not_found"}, status_code=404)
    return project, None


async def _dashboard_erpnext_project_or_error(
    project_id: str,
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    project, error_response = await _dashboard_cached_project_or_error(project_id)
    if error_response is not None:
        return None, error_response
    assert project is not None
    if not project.get("erpnext_project_id"):
        return None, JSONResponse(
            {"error": "project_not_linked_to_erpnext"}, status_code=400
        )
    return project, None


def _add_historical_project_member(
    *,
    project_id: str,
    person: str,
    candidate_id: str | None,
    actor_subject: str | None,
) -> dict[str, Any]:
    normalized_person = person.strip()
    candidate = _resolve_historical_project_member(
        person=normalized_person,
        candidate_id=candidate_id,
    )
    email = _text_or_none(candidate.get("email"))
    full_name = _text_or_none(candidate.get("full_name")) or _text_or_none(
        candidate.get("label")
    )
    source_user_id = (
        email
        or _text_or_none(candidate.get("erpnext_user_id"))
        or _text_or_none(candidate.get("crm_contact_id"))
        or _text_or_none(candidate.get("supplier_erpnext_id"))
        or normalized_person
    )
    add_project_roster_member(
        settings,
        project_id=project_id,
        source_user_id=source_user_id,
        email=email,
        full_name=full_name,
        source_payload={
            "added_by": actor_subject,
            "entry": normalized_person,
            "candidate": candidate,
            "crm_contact_id": candidate.get("crm_contact_id"),
            "erpnext_user_id": candidate.get("erpnext_user_id"),
            "supplier_erpnext_id": candidate.get("supplier_erpnext_id"),
            "supplier_name": candidate.get("supplier_name"),
            "sources": candidate.get("sources") or [],
        },
    )
    project = _cached_dashboard_project_by_id(project_id)
    if project is None:
        raise ValueError("project_not_found")
    return project


def _remove_historical_project_member(
    *,
    project_id: str,
    source_user_id: str,
) -> dict[str, Any]:
    removed = remove_project_roster_member(
        settings,
        project_id=project_id,
        source=PROJECT_SOURCE_MANUAL,
        source_user_id=source_user_id,
        roster_kind=PROJECT_ROSTER_KIND_HISTORICAL,
    )
    if not removed:
        raise ValueError("roster_member_not_found")
    project = _cached_dashboard_project_by_id(project_id)
    if project is None:
        raise ValueError("project_not_found")
    return project


async def dashboard_project_wiki_matches_handler(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    """Return read-only fuzzy matches between cached projects and the wiki table."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_READ,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    include_all = _session_has_steering_access(session)
    viewer_emails = (
        []
        if include_all
        else await asyncio.to_thread(_dashboard_project_viewer_emails, session)
    )

    try:
        preview = await asyncio.to_thread(
            wiki_project_match_preview,
            settings,
            document_id=DEFAULT_WIKI_PROJECT_DOC_ID,
            viewer_emails=viewer_emails,
            include_all=include_all,
            limit=limit,
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": "wiki_match_preview_failed", "detail": str(exc)},
            status_code=502,
        )
    if not include_all:
        preview["wiki_rows"] = []
        for item in preview.get("matches", []):
            if not isinstance(item, dict):
                continue
            for match_key in ("best_match", "fuzzy_match"):
                match_value = item.get(match_key)
                if isinstance(match_value, dict):
                    match_value["row"] = None
            manual_match = item.get("manual_match")
            if isinstance(manual_match, dict):
                manual_match.pop("source_payload", None)
                manual_match.pop("wiki_row_label", None)
                manual_match.pop("wiki_row_section", None)
    return JSONResponse(preview)


async def dashboard_erpnext_customers_handler(
    request: Request,
    query: str = Query(default="", min_length=0, max_length=140),
) -> JSONResponse:
    """Search ERPNext Customers for dashboard project creation."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    try:
        customers = await asyncio.to_thread(_search_erpnext_customers, query)
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_customer_search_failed", "detail": str(exc)},
            status_code=502,
        )
    return JSONResponse({"customers": customers})


async def dashboard_erpnext_contacts_handler(
    request: Request,
    query: str = Query(default="", min_length=0, max_length=140),
) -> JSONResponse:
    """Search ERPNext Contacts for dashboard project creation."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    try:
        contacts = await asyncio.to_thread(_search_erpnext_contacts, query)
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_contact_search_failed", "detail": str(exc)},
            status_code=502,
        )
    return JSONResponse({"contacts": contacts})


async def dashboard_erpnext_account_managers_handler(
    request: Request,
    query: str = Query(default="", min_length=0, max_length=140),
) -> JSONResponse:
    """Search ERPNext Users eligible for Customer Account Manager."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    try:
        users = await asyncio.to_thread(_search_erpnext_account_managers, query)
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_account_manager_search_failed", "detail": str(exc)},
            status_code=502,
        )
    return JSONResponse({"users": users})


async def dashboard_erpnext_cost_centers_handler(request: Request) -> JSONResponse:
    """List ERPNext Cost Centers for dashboard project creation."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    try:
        cost_centers = await asyncio.to_thread(_list_erpnext_cost_centers)
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_cost_center_list_failed", "detail": str(exc)},
            status_code=502,
        )
    return JSONResponse({"cost_centers": cost_centers})


async def dashboard_create_project_handler(request: Request) -> JSONResponse:
    """Create a Customer-backed ERPNext Project setup from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
        payload = DashboardProjectCreateRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    try:
        result = await asyncio.to_thread(_create_erpnext_project_setup, payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_project_create_failed", "detail": str(exc)},
            status_code=502,
        )

    project = (
        cast(dict[str, Any], result.get("project"))
        if isinstance(result.get("project"), dict)
        else {}
    )
    customer = (
        cast(dict[str, Any], result.get("customer"))
        if isinstance(result.get("customer"), dict)
        else {}
    )
    activity_type = (
        cast(dict[str, Any], result.get("activity_type"))
        if isinstance(result.get("activity_type"), dict)
        else {}
    )
    address = (
        cast(dict[str, Any], result.get("address"))
        if isinstance(result.get("address"), dict)
        else {}
    )
    contact = (
        cast(dict[str, Any], result.get("contact"))
        if isinstance(result.get("contact"), dict)
        else {}
    )
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.project_setup_create",
        result=AuditResult.ERROR
        if isinstance(result.get("setup_warnings"), list)
        else AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project",
        resource_id=str(project.get("erpnext_project_id") or ""),
        metadata={
            "source": "dashboard",
            "local_project_id": project.get("id"),
            "customer": customer.get("name"),
            "customer_mode": payload.customer_mode,
            "activity_type": activity_type.get("name"),
            "primary_address": address.get("name")
            if isinstance(address, dict)
            else None,
            "primary_contact": contact.get("name")
            if isinstance(contact, dict)
            else None,
            "setup_warnings": result.get("setup_warnings"),
        },
    )
    return JSONResponse(result, status_code=201)


async def dashboard_update_project_status_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Update one ERPNext Project status from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_erpnext_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectStatusRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_status = payload.status.strip()
    if normalized_status not in {"Open", "Completed", "Cancelled"}:
        return JSONResponse({"error": "invalid_status"}, status_code=400)

    external_project_id = str(project["erpnext_project_id"])
    try:
        updated_project = await asyncio.to_thread(
            _update_erpnext_project_status,
            external_project_id=external_project_id,
            status=normalized_status,
        )
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_project_update_failed", "detail": str(exc)},
            status_code=502,
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.project_status_update",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project",
        resource_id=external_project_id,
        metadata={
            "source": "dashboard",
            "status": normalized_status,
            "local_project_id": project_id,
        },
    )
    return JSONResponse({"project": updated_project})


async def dashboard_bulk_update_projects_handler(request: Request) -> JSONResponse:
    """Bulk update ERPNext Project fields from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    try:
        body = await request.json()
        payload = DashboardBulkProjectUpdateRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_project_ids = sorted(
        {
            normalized_id
            for project_id in payload.project_ids
            if (normalized_id := _valid_uuid_or_none(project_id)) is not None
        }
    )
    if not normalized_project_ids:
        return JSONResponse({"error": "project_ids_required"}, status_code=400)
    if len(normalized_project_ids) > 100:
        return JSONResponse({"error": "too_many_projects"}, status_code=400)

    fields: dict[str, str] = {}
    normalized_status = (payload.status or "").strip()
    normalized_project_type = (payload.project_type or "").strip()
    if normalized_status:
        if normalized_status not in {"Open", "Completed", "Cancelled"}:
            return JSONResponse({"error": "invalid_status"}, status_code=400)
        fields["status"] = normalized_status
    if normalized_project_type:
        if normalized_project_type not in {"Internal", "External"}:
            return JSONResponse({"error": "invalid_project_type"}, status_code=400)
        fields["project_type"] = normalized_project_type
    if not fields:
        return JSONResponse({"error": "no_fields_to_update"}, status_code=400)

    result = await asyncio.to_thread(
        _bulk_update_erpnext_projects,
        project_ids=normalized_project_ids,
        fields=fields,
    )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.projects_bulk_update",
        result=AuditResult.SUCCESS if not result["failures"] else AuditResult.ERROR,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project",
        resource_id="bulk",
        metadata={
            "source": "dashboard",
            "fields": fields,
            "project_count": len(normalized_project_ids),
            "updated_count": len(result["projects"]),
            "failed_count": len(result["failures"]),
        },
    )
    return JSONResponse(result)


async def dashboard_setup_engineer_handler(request: Request) -> JSONResponse:
    """Set up one ERPNext engineer account from the dashboard."""
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
        payload = DashboardEngineerSetupRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_email = payload.email.strip().lower()
    normalized_first_name = payload.first_name.strip()
    if not normalized_email or not normalized_email.endswith("@508.dev"):
        return JSONResponse({"error": "invalid_email"}, status_code=400)
    if not normalized_first_name:
        return JSONResponse({"error": "first_name_required"}, status_code=400)
    normalized_payload = payload.model_copy(
        update={
            "email": normalized_email,
            "first_name": normalized_first_name,
            "middle_name": _text_or_none(payload.middle_name),
            "last_name": _text_or_none(payload.last_name),
            "country": _text_or_none(payload.country),
            "gender": _text_or_none(payload.gender),
            "date_of_birth": _text_or_none(payload.date_of_birth),
            "date_of_joining": _text_or_none(payload.date_of_joining),
            "personal_email": _text_or_none(payload.personal_email),
            "prefered_email": _text_or_none(payload.prefered_email),
        }
    )

    try:
        result = await asyncio.to_thread(_setup_erpnext_engineer, normalized_payload)
    except EngineerOnboardingDuplicateNameError as exc:
        await _audit_dashboard_engineer_setup(
            session,
            result=AuditResult.DENIED,
            email=normalized_email,
            metadata={
                "error": "similar_engineer_exists",
                "detail": str(exc),
                "matches_count": len(exc.matches),
            },
        )
        return JSONResponse(
            {
                "error": "similar_engineer_exists",
                "detail": str(exc),
                "matches": exc.matches,
            },
            status_code=409,
        )
    except EngineerOnboardingError as exc:
        await _audit_dashboard_engineer_setup(
            session,
            result=AuditResult.DENIED,
            email=normalized_email,
            metadata={"error": "engineer_setup_failed", "detail": str(exc)},
        )
        return JSONResponse(
            {"error": "engineer_setup_failed", "detail": str(exc)},
            status_code=400,
        )
    except ERPNextAPIError as exc:
        await _audit_dashboard_engineer_setup(
            session,
            result=AuditResult.ERROR,
            email=normalized_email,
            metadata={"error": "erpnext_engineer_setup_failed", "detail": str(exc)},
        )
        return JSONResponse(
            {"error": "erpnext_engineer_setup_failed", "detail": str(exc)},
            status_code=502,
        )

    await _audit_dashboard_engineer_setup(
        session,
        result=AuditResult.SUCCESS,
        email=normalized_email,
        metadata={
            "user_id": result.get("user"),
            "employee_id": result.get("employee"),
            "supplier_id": result.get("supplier"),
            "created": result.get("created"),
            "updated": result.get("updated"),
        },
    )
    return JSONResponse(result)


async def dashboard_add_project_user_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Add one ERPNext User to a Project roster from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_erpnext_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectUserRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_user = payload.user.strip()
    if not normalized_user or len(normalized_user) > 200:
        return JSONResponse({"error": "invalid_user"}, status_code=400)
    if "@" in normalized_user:
        normalized_user = normalized_user.lower()
        if not normalized_user.endswith("@508.dev"):
            return JSONResponse({"error": "invalid_user_email"}, status_code=400)
    normalized_activity_type = _text_or_none(payload.activity_type)
    has_activity_type = normalized_activity_type is not None
    has_billing_rate = payload.billing_rate is not None
    has_costing_rate = payload.costing_rate is not None
    if (has_billing_rate or has_costing_rate) and not has_activity_type:
        return JSONResponse({"error": "activity_type_required"}, status_code=400)
    if has_activity_type and not (has_billing_rate and has_costing_rate):
        return JSONResponse({"error": "activity_cost_rates_required"}, status_code=400)

    external_project_id = str(project["erpnext_project_id"])
    try:
        add_user_kwargs: dict[str, Any] = {
            "external_project_id": external_project_id,
            "user": normalized_user,
            "candidate_id": payload.candidate_id,
        }
        if has_activity_type or has_billing_rate or has_costing_rate:
            add_user_kwargs.update(
                {
                    "activity_type": normalized_activity_type,
                    "billing_rate": payload.billing_rate,
                    "costing_rate": payload.costing_rate,
                }
            )
        project_user_result = await asyncio.to_thread(
            _add_erpnext_project_user,
            **add_user_kwargs,
        )
    except HistoricalProjectMemberResolutionError as exc:
        status_code = 409 if exc.candidates else 400
        return JSONResponse(
            _project_roster_user_error_payload(exc, user=normalized_user),
            status_code=status_code,
        )
    except EngineerOnboardingError as exc:
        return JSONResponse(
            {"error": "activity_cost_update_failed", "detail": str(exc)},
            status_code=400,
        )
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_project_user_add_failed", "detail": str(exc)},
            status_code=502,
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    audit_result = (
        AuditResult.ERROR
        if project_user_result.get("partial_success")
        or project_user_result.get("activity_cost_error")
        else AuditResult.SUCCESS
    )
    await _write_auth_audit_event(
        action="erpnext.project_user_add",
        result=audit_result,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project",
        resource_id=external_project_id,
        metadata={
            "source": "dashboard",
            "user": normalized_user,
            "candidate_id": payload.candidate_id,
            "activity_cost": project_user_result.get("activity_cost"),
            "activity_cost_error": project_user_result.get("activity_cost_error"),
            "local_project_id": project_id,
        },
    )
    return JSONResponse(project_user_result)


async def dashboard_remove_project_user_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Remove one ERPNext User from a Project roster from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_erpnext_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectUserRemoveRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_user = payload.user.strip()
    if not normalized_user or len(normalized_user) > 200:
        return JSONResponse({"error": "invalid_user"}, status_code=400)

    external_project_id = str(project["erpnext_project_id"])
    try:
        updated_project = await asyncio.to_thread(
            _remove_erpnext_project_user,
            external_project_id=external_project_id,
            user=normalized_user,
        )
    except ERPNextAPIError as exc:
        return JSONResponse(
            {"error": "erpnext_project_user_remove_failed", "detail": str(exc)},
            status_code=502,
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.project_user_remove",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project",
        resource_id=external_project_id,
        metadata={
            "source": "dashboard",
            "user": normalized_user,
            "local_project_id": project_id,
        },
    )
    return JSONResponse({"project": updated_project})


async def dashboard_add_project_historical_member_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Add one local historical Project roster member from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_cached_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectHistoricalMemberRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_person = payload.person.strip()
    if not normalized_person or len(normalized_person) > 200:
        return JSONResponse({"error": "invalid_person"}, status_code=400)

    actor_provider, actor_subject = _session_audit_actor(session)
    try:
        updated_project = await asyncio.to_thread(
            _add_historical_project_member,
            project_id=project_id,
            person=normalized_person,
            candidate_id=payload.candidate_id,
            actor_subject=actor_subject,
        )
    except HistoricalProjectMemberResolutionError as exc:
        status_code = 409 if exc.candidates else 400
        return JSONResponse(
            _historical_project_member_error_payload(exc, person=normalized_person),
            status_code=status_code,
        )
    except ValueError:
        return JSONResponse({"error": "project_not_found"}, status_code=404)

    await _write_auth_audit_event(
        action="project.historical_member_add",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="project",
        resource_id=project_id,
        metadata={
            "source": "dashboard",
            "person": normalized_person,
            "erpnext_project_id": project.get("erpnext_project_id"),
        },
    )
    return JSONResponse({"project": updated_project})


async def dashboard_remove_project_historical_member_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Remove one local historical Project roster member from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_cached_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectHistoricalMemberRemoveRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_source_user_id = payload.source_user_id.strip()
    if not normalized_source_user_id or len(normalized_source_user_id) > 200:
        return JSONResponse({"error": "invalid_source_user_id"}, status_code=400)

    try:
        updated_project = await asyncio.to_thread(
            _remove_historical_project_member,
            project_id=project_id,
            source_user_id=normalized_source_user_id,
        )
    except ValueError as exc:
        error = str(exc) or "roster_member_not_found"
        return JSONResponse(
            {"error": error},
            status_code=404
            if error in {"project_not_found", "roster_member_not_found"}
            else 400,
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="project.historical_member_remove",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="project",
        resource_id=project_id,
        metadata={
            "source": "dashboard",
            "source_user_id": normalized_source_user_id,
            "erpnext_project_id": project.get("erpnext_project_id"),
        },
    )
    return JSONResponse({"project": updated_project})


async def dashboard_update_project_wiki_match_handler(
    request: Request,
    project_id: str,
) -> JSONResponse:
    """Persist a manual project-to-wiki row match decision."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    project, error_response = await _dashboard_cached_project_or_error(project_id)
    if error_response is not None:
        return error_response
    assert project is not None

    try:
        body = await request.json()
        payload = DashboardProjectWikiMatchRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_status = payload.status.strip()
    if normalized_status not in {
        PROJECT_WIKI_MATCH_CONFIRMED,
        PROJECT_WIKI_MATCH_NO_ROW,
    }:
        return JSONResponse({"error": "invalid_status"}, status_code=400)

    wiki_row: dict[str, Any] | None = None
    if normalized_status == PROJECT_WIKI_MATCH_CONFIRMED:
        normalized_row_key = (payload.row_key or "").strip()
        if not normalized_row_key:
            return JSONResponse({"error": "row_key_required"}, status_code=400)
        try:
            wiki_doc = await asyncio.to_thread(
                fetch_outline_document,
                settings,
                document_id=DEFAULT_WIKI_PROJECT_DOC_ID,
            )
            wiki_rows = parse_project_wiki_tables(str(wiki_doc.get("text") or ""))
            wiki_row = wiki_row_by_key(wiki_rows, normalized_row_key)
        except ValueError as exc:
            return JSONResponse(
                {"error": "wiki_match_update_failed", "detail": str(exc)},
                status_code=502,
            )
        if wiki_row is None:
            return JSONResponse({"error": "wiki_row_not_found"}, status_code=404)

    try:
        manual_match = await asyncio.to_thread(
            set_project_wiki_match,
            settings,
            project_id=project_id,
            document_id=DEFAULT_WIKI_PROJECT_DOC_ID,
            match_status=normalized_status,
            wiki_row=wiki_row,
        )
    except ValueError as exc:
        return JSONResponse(
            {"error": "invalid_wiki_match", "detail": str(exc)}, status_code=400
        )

    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="project.wiki_match_update",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="project",
        resource_id=project_id,
        metadata={
            "source": "dashboard",
            "status": normalized_status,
            "row_key": payload.row_key,
            "erpnext_project_id": project.get("erpnext_project_id"),
        },
    )
    return JSONResponse({"manual_match": manual_match})


async def dashboard_sync_projects_handler(request: Request) -> JSONResponse:
    """Queue an ERPNext project sync from the authenticated dashboard."""
    session, error_response, dry_run = await _dashboard_write_session_or_dry_run(
        request,
        required_permission=DASHBOARD_PERMISSION_PROJECTS_SYNC,
        dry_run_permission=DASHBOARD_PERMISSION_PROJECTS_SYNC_DRY_RUN,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    if dry_run:
        return JSONResponse(
            {
                "status": "dry_run",
                "dry_run": True,
                "source": "dashboard",
                "would_enqueue": {
                    "queue": settings.redis_queue_name,
                    "job_type": "sync_projects_from_erpnext_job",
                    "reason": "dashboard",
                    "idempotency_key_pattern": "erpnext-project-sync:YYYYMMDDHHMM",
                },
            }
        )

    job = await _enqueue_erpnext_project_sync_job(
        request.app.state.queue,
        reason="dashboard",
    )
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="erpnext.projects_sync",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="erpnext_project_sync",
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


async def dashboard_update_gig_status_handler(
    request: Request,
    engagement_id: str,
) -> JSONResponse:
    """Update one gig status from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    normalized_engagement_id = _valid_uuid_or_none(engagement_id)
    if normalized_engagement_id is None:
        return JSONResponse({"error": "invalid_engagement_id"}, status_code=400)

    try:
        body = await request.json()
        payload = DashboardGigStatusRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_status = normalize_engagement_status(payload.status)
    if (
        normalized_status is EngagementStatus.UNKNOWN
        and payload.status.strip().casefold()
        not in {
            "unknown",
        }
    ):
        return JSONResponse({"error": "invalid_status"}, status_code=400)

    include_all = _session_has_steering_access(session)
    can_update = await asyncio.to_thread(
        viewer_can_update_engagement,
        settings,
        engagement_id=normalized_engagement_id,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
    )
    if not can_update:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    result = await asyncio.to_thread(
        update_engagement_status,
        settings,
        engagement_id=normalized_engagement_id,
        status=normalized_status,
        actor_discord_user_id=_session_discord_actor_id(session),
    )
    if result is None:
        return JSONResponse({"error": "gig_not_found"}, status_code=404)
    result["discord_title_sync"] = await _sync_discord_gig_thread_status(
        request,
        thread_id=cast(str | None, result.get("discord_thread_id")),
        status=normalized_status,
    )
    return JSONResponse(result)


async def dashboard_update_gig_application_status_handler(
    request: Request,
    engagement_id: str,
    application_id: str,
) -> JSONResponse:
    """Update one gig candidate/application status from the dashboard."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    normalized_engagement_id = _valid_uuid_or_none(engagement_id)
    normalized_application_id = _valid_uuid_or_none(application_id)
    if normalized_engagement_id is None:
        return JSONResponse({"error": "invalid_engagement_id"}, status_code=400)
    if normalized_application_id is None:
        return JSONResponse({"error": "invalid_application_id"}, status_code=400)

    try:
        body = await request.json()
        payload = DashboardGigApplicationStatusRequest.model_validate(body)
        normalized_status = EngagementApplicationStatus(payload.status)
    except ValueError:
        return JSONResponse({"error": "invalid_application_status"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    include_all = _session_has_steering_access(session)
    can_update = await asyncio.to_thread(
        viewer_can_update_engagement,
        settings,
        engagement_id=normalized_engagement_id,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
    )
    if not can_update:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    result = await asyncio.to_thread(
        update_engagement_application_status,
        settings,
        engagement_id=normalized_engagement_id,
        application_id=normalized_application_id,
        status=normalized_status,
        actor_discord_user_id=_session_discord_actor_id(session),
    )
    if result is None:
        return JSONResponse({"error": "application_not_found"}, status_code=404)
    return JSONResponse(result)


async def dashboard_add_gig_application_handler(
    request: Request,
    engagement_id: str,
) -> JSONResponse:
    """Add one CRM-verified candidate/application to a dashboard gig."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_GIGS_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    normalized_engagement_id = _valid_uuid_or_none(engagement_id)
    if normalized_engagement_id is None:
        return JSONResponse({"error": "invalid_engagement_id"}, status_code=400)

    try:
        body = await request.json()
        payload = DashboardGigApplicationCreateRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    contact_id = _contact_id_from_crm_profile(payload.crm_profile)
    if contact_id is None:
        return JSONResponse({"error": "invalid_crm_profile"}, status_code=400)

    include_all = _session_has_steering_access(session)
    can_update = await asyncio.to_thread(
        viewer_can_update_engagement,
        settings,
        engagement_id=normalized_engagement_id,
        viewer_discord_user_id=session.subject,
        include_all=include_all,
    )
    if not can_update:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    client = EspoClient(settings.espo_base_url, settings.espo_api_key)
    try:
        contact = await asyncio.to_thread(
            client.request, "GET", f"Contact/{contact_id}"
        )
    except EspoAPIError:
        if client.status_code == 404:
            return JSONResponse({"error": "crm_profile_not_found"}, status_code=404)
        return JSONResponse({"error": "crm_profile_lookup_failed"}, status_code=502)

    if str(contact.get("id") or "").strip() != contact_id:
        return JSONResponse({"error": "crm_profile_mismatch"}, status_code=409)

    result = await asyncio.to_thread(
        add_crm_application_to_engagement,
        settings,
        engagement_id=normalized_engagement_id,
        crm_contact_id=contact_id,
        contact_payload=contact,
        actor_discord_user_id=_session_discord_actor_id(session),
    )
    if result is None:
        return JSONResponse({"error": "gig_not_found"}, status_code=404)
    return JSONResponse(result, status_code=201)


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


def _onboarding_candidate_timezone(contact_id: str) -> str | None:
    """Load the cached candidate timezone used for volunteer suggestions."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT timezone FROM people WHERE crm_contact_id = %s LIMIT 1",
                (contact_id,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    timezone_name = str(row.get("timezone") or "").strip()
    return timezone_name or None


async def dashboard_onboarding_volunteers_handler(request: Request) -> JSONResponse:
    """List willing onboarders with their current workload and success counts."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_ONBOARDING_READ,
    )
    if error_response is not None:
        return error_response
    volunteers = await asyncio.to_thread(list_onboarding_volunteers, settings)
    return JSONResponse(jsonable_encoder(volunteers))


async def dashboard_onboarding_volunteer_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Create or update one willing-onboarder registry entry."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_ONBOARDING_WRITE,
    )
    if error_response is not None:
        return error_response
    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error
    try:
        payload = DashboardOnboardingVolunteerRequest.model_validate(
            await request.json()
        )
        result = await asyncio.to_thread(
            upsert_onboarding_volunteer,
            settings,
            crm_contact_id=contact_id,
            timezone_name=payload.timezone,
            availability=VolunteerAvailability(payload.availability),
            paused_until=payload.paused_until,
            max_active_assignments=payload.max_active_assignments,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse(jsonable_encoder(result))


async def dashboard_onboarding_suggestions_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Suggest available onboarders for a candidate without auto-assigning one."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_ONBOARDING_READ,
    )
    if error_response is not None:
        return error_response
    candidate_timezone = await asyncio.to_thread(
        _onboarding_candidate_timezone, contact_id
    )
    volunteers = await asyncio.to_thread(
        suggested_onboarders,
        settings,
        candidate_timezone=candidate_timezone,
    )
    return JSONResponse(
        jsonable_encoder(
            {"candidate_timezone": candidate_timezone, "volunteers": volunteers}
        )
    )


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

    try:
        await asyncio.to_thread(mark_onboarder_assigned, settings, result["onboarder"])
    except Exception:
        logger.warning(
            "Failed recording onboarding assignment recency contact_id=%s",
            result["contact_id"],
            exc_info=True,
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
            "sync_job_id": sync_job_id,
        },
    )
    result["sync_job_id"] = sync_job_id
    return JSONResponse(result, status_code=200)


async def dashboard_update_onboarding_status_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Update one CRM contact's onboarding status from the dashboard."""
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
        payload = DashboardOnboardingStatusRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    try:
        result = await asyncio.to_thread(
            _update_dashboard_onboarding_status_in_crm,
            contact_id=contact_id,
            status=payload.status,
        )
    except DashboardOnboarderAssignmentError as exc:
        await _audit_dashboard_update_onboarding_status(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            onboarding_status=_normalize_dashboard_onboarding_status(payload.status),
            metadata={"reason": exc.error},
        )
        return JSONResponse({"error": exc.error}, status_code=exc.status_code)
    except EspoAPIError as exc:
        logger.error(
            "CRM onboarding status update failed contact_id=%s error=%s",
            contact_id,
            exc,
        )
        await _audit_dashboard_update_onboarding_status(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            onboarding_status=_normalize_dashboard_onboarding_status(payload.status),
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
            idempotency_key=(
                "dashboard-onboarding-status-sync:"
                f"{result['contact_id']}:{_generate_ulid()}"
            ),
        )
        sync_job_id = sync_job.id
    except Exception:
        logger.warning(
            "Failed enqueueing post-status-update people sync contact_id=%s",
            result["contact_id"],
            exc_info=True,
        )

    await _audit_dashboard_update_onboarding_status(
        session,
        result=AuditResult.SUCCESS,
        contact_id=result["contact_id"],
        onboarding_status=result["onboarding_state"],
        metadata={
            "contact_name": result["contact_name"],
            "previous_state": result["previous_state"],
            "sync_job_id": sync_job_id,
        },
    )
    result["sync_job_id"] = sync_job_id
    return JSONResponse(result, status_code=200)


async def dashboard_onboarding_email_draft_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Build a reviewed dashboard onboarding email draft for one candidate."""
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
        payload = DashboardOnboardingEmailDraftRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    try:
        contact = await asyncio.to_thread(
            _dashboard_onboarding_contact_for_email,
            contact_id,
        )
    except DashboardOnboardingEmailError as exc:
        return JSONResponse({"error": exc.error}, status_code=exc.status_code)
    except EspoAPIError as exc:
        logger.warning(
            "CRM onboarding email draft lookup failed contact_id=%s error=%s",
            contact_id,
            exc,
            exc_info=True,
        )
        return JSONResponse({"error": "crm_lookup_failed"}, status_code=502)

    sender_display_name, signature_name = await asyncio.to_thread(
        _dashboard_sender_names,
        session,
    )
    reply_to_email = await asyncio.to_thread(_dashboard_reply_to_email, session)
    cc_email = await asyncio.to_thread(_dashboard_sender_cc_email, session)
    candidate_name = _dashboard_contact_display_name(contact)
    recipient_email = _dashboard_preferred_contact_email(contact)
    draft = build_onboarding_email(
        OnboardingEmailRequest(
            candidate_name=candidate_name,
            sender_name=signature_name,
            has_contributed=payload.has_contributed,
            discord_joined=payload.discord_joined,
            membership_agreement_signed=payload.agreement_signed,
        )
    )
    marker = await asyncio.to_thread(
        _dashboard_onboarding_email_marker,
        contact_id.strip(),
    )
    smtp_ready = onboarding_email_smtp_ready(_dashboard_onboarding_email_smtp_config())

    return JSONResponse(
        {
            "contact_id": contact_id.strip(),
            "candidate_name": candidate_name,
            "recipient_email": recipient_email,
            "reply_to_email": reply_to_email,
            "cc_email": cc_email,
            "sender_display_name": sender_display_name,
            "signature_name": signature_name,
            "subject": draft.subject,
            "markdown_body": draft.markdown_body,
            "can_send": bool(recipient_email and reply_to_email and smtp_ready),
            **marker,
        }
    )


async def dashboard_onboarding_email_send_handler(
    request: Request,
    contact_id: str,
) -> JSONResponse:
    """Send a reviewed dashboard onboarding email and mark it locally."""
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
        payload = DashboardOnboardingEmailSendRequest.model_validate(body)
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    metadata: dict[str, Any] = {
        "has_contributed": payload.has_contributed,
        "discord_joined": payload.discord_joined,
        "agreement_signed": payload.agreement_signed,
    }
    try:
        if not payload.markdown_body.strip():
            raise DashboardOnboardingEmailError("empty_email_body")

        contact = await asyncio.to_thread(
            _dashboard_onboarding_contact_for_email,
            contact_id,
        )
        candidate_name = _dashboard_contact_display_name(contact)
        onboarding_status = _normalize_onboarding_state_key(
            contact.get(_ONBOARDING_STATUS_FIELD)
        )
        sender_display_name, signature_name = await asyncio.to_thread(
            _dashboard_sender_names,
            session,
        )
        reply_to_email = await asyncio.to_thread(_dashboard_reply_to_email, session)
        if reply_to_email is None:
            raise DashboardOnboardingEmailError(
                "reply_to_email_required",
                status_code=409,
            )
        cc_email = await asyncio.to_thread(_dashboard_sender_cc_email, session)
        recipient_email = _dashboard_preferred_contact_email(contact)
        if recipient_email is None:
            raise DashboardOnboardingEmailError(
                "recipient_email_required",
                status_code=409,
            )
        smtp_config = _dashboard_onboarding_email_smtp_config()
        if not onboarding_email_smtp_ready(smtp_config):
            raise DashboardOnboardingEmailError(
                "smtp_not_configured",
                status_code=409,
            )

        text_body = markdown_body_to_text(payload.markdown_body)
        html_body = markdown_body_to_html(payload.markdown_body)
        message = build_onboarding_email_message(
            recipient_email=recipient_email,
            reply_to_email=reply_to_email,
            sender_name=sender_display_name,
            sender_email=settings.onboarding_email_sender_email,
            cc_email=cc_email,
            subject="508.dev onboarding",
            text_body=text_body,
            html_body=html_body,
        )
        await asyncio.to_thread(
            send_onboarding_email_message,
            message,
            config=smtp_config,
        )
        marker_status = "saved"
        marker_error: str | None = None
        marker_actor = _dashboard_onboarding_email_actor(session)
        try:
            marker = await asyncio.to_thread(
                _mark_dashboard_onboarding_email_sent,
                contact_id=contact_id.strip(),
                recipient_email=recipient_email,
                actor=marker_actor,
            )
        except DashboardOnboardingEmailError as exc:
            marker_status = "error"
            marker_error = exc.error
            marker = {
                "onboarding_email_sent_at": datetime.now(timezone.utc).isoformat(),
                "onboarding_email_sent_by": marker_actor,
                "onboarding_email_recipient": recipient_email,
            }
            logger.warning(
                "Onboarding email sent but marker update failed contact_id=%s reason=%s",
                contact_id,
                exc.error,
            )
        except Exception as exc:
            marker_status = "error"
            marker_error = "marker_update_failed"
            marker = {
                "onboarding_email_sent_at": datetime.now(timezone.utc).isoformat(),
                "onboarding_email_sent_by": marker_actor,
                "onboarding_email_recipient": recipient_email,
            }
            logger.warning(
                "Onboarding email sent but marker update failed contact_id=%s error=%s",
                contact_id,
                exc,
                exc_info=True,
            )
    except DashboardOnboardingEmailError as exc:
        await _audit_dashboard_onboarding_email(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            metadata={**metadata, "reason": exc.error},
        )
        return JSONResponse({"error": exc.error}, status_code=exc.status_code)
    except EspoAPIError as exc:
        logger.warning(
            "CRM onboarding email send lookup failed contact_id=%s error=%s",
            contact_id,
            exc,
            exc_info=True,
        )
        await _audit_dashboard_onboarding_email(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            metadata={
                **metadata,
                "reason": "crm_lookup_failed",
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse({"error": "crm_lookup_failed"}, status_code=502)
    except (OSError, ValueError, smtplib.SMTPException) as exc:
        logger.warning(
            "Dashboard onboarding email send failed contact_id=%s error=%s",
            contact_id,
            exc,
            exc_info=True,
        )
        await _audit_dashboard_onboarding_email(
            session,
            result=AuditResult.ERROR,
            contact_id=contact_id,
            metadata={
                **metadata,
                "reason": "email_send_failed",
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse({"error": "email_send_failed"}, status_code=502)

    await _audit_dashboard_onboarding_email(
        session,
        result=AuditResult.SUCCESS,
        contact_id=contact_id.strip(),
        metadata={
            **metadata,
            "candidate_name": candidate_name,
            "recipient_email": recipient_email,
            "reply_to_email": reply_to_email,
            "cc_email": cc_email,
            "sender_display_name": sender_display_name,
            "signature_name": signature_name,
            "onboarding_status": onboarding_status,
            "marker_status": marker_status,
            "marker_error": marker_error,
        },
    )
    return JSONResponse(
        {
            "status": "sent",
            "contact_id": contact_id.strip(),
            "candidate_name": candidate_name,
            "recipient_email": recipient_email,
            "reply_to_email": reply_to_email,
            "cc_email": cc_email,
            "sender_display_name": sender_display_name,
            "signature_name": signature_name,
            "subject": "508.dev onboarding",
            "markdown_body": payload.markdown_body,
            "can_send": False,
            "marker_status": marker_status,
            "marker_error": marker_error,
            **marker,
        }
    )


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


async def dashboard_agent_report_handler(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> JSONResponse:
    """Return admin-only agent request analytics for the dashboard."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_AUDIT_READ,
    )
    if error_response is not None:
        return error_response

    report = await asyncio.to_thread(_dashboard_agent_request_report, limit)
    return JSONResponse(report)


async def dashboard_configuration_handler(request: Request) -> JSONResponse:
    """Return admin-only dashboard configuration metadata."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_CONFIGURATION_READ,
    )
    if error_response is not None:
        return error_response

    items = await asyncio.to_thread(list_runtime_config, settings)
    return JSONResponse({"items": items})


async def dashboard_newsletter_suppressions_handler(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> JSONResponse:
    """Return active newsletter suppressions for admin dashboard visibility."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC,
    )
    if error_response is not None:
        return error_response

    records = await asyncio.to_thread(
        list_newsletter_suppressions,
        settings,
        limit=limit,
        active_only=True,
    )
    return JSONResponse(
        {
            "suppressions": [
                {
                    "email": record.email,
                    "source_provider": record.source_provider,
                    "reason": record.reason,
                    "active": record.active,
                    "metadata": record.metadata,
                    "first_seen_at": record.first_seen_at.isoformat(),
                    "last_seen_at": record.last_seen_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
                for record in records
            ]
        }
    )


async def dashboard_newsletter_status_handler(request: Request) -> JSONResponse:
    """Return current dashboard status for the 508 members newsletter sync."""
    _, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC,
    )
    if error_response is not None:
        return error_response

    recent_jobs = await asyncio.to_thread(
        list_jobs,
        settings,
        created_after=datetime.now(tz=timezone.utc) - timedelta(days=90),
        limit=1,
        job_type=JOB_FUNCTIONS["sync_508_members_newsletters_job"].__name__,
    )
    latest_job = recent_jobs[0] if recent_jobs else None
    suppressions = await asyncio.to_thread(
        list_newsletter_suppressions,
        settings,
        limit=1000,
        active_only=True,
    )
    suppressed_emails = {record.email for record in suppressions}
    return JSONResponse(
        {
            "scheduler_enabled": settings.newsletter_sync_enabled,
            "interval_seconds": settings.newsletter_sync_interval_seconds,
            "active_suppression_count": len(suppressions),
            "active_suppressed_email_count": len(suppressed_emails),
            "latest_job": _dashboard_job_payload(latest_job)
            if latest_job is not None
            else None,
        }
    )


async def dashboard_update_configuration_handler(
    request: Request,
    key: str,
) -> JSONResponse:
    """Update or clear one admin-managed dashboard configuration value."""
    session, error_response = await _dashboard_session_or_error(
        request,
        required_permission=DASHBOARD_PERMISSION_CONFIGURATION_WRITE,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    definition = runtime_config_definition_for_key(key)
    if definition is None:
        await _audit_dashboard_configuration_change(
            session,
            result=AuditResult.ERROR,
            key=key,
            action="configuration.update",
            metadata={
                "key": key,
                "category": None,
                "is_secret": False,
                "error": "unknown_configuration_key",
            },
        )
        return JSONResponse({"error": "unknown_configuration_key"}, status_code=404)

    metadata = {
        "key": definition.key,
        "category": definition.category,
        "is_secret": definition.is_secret,
    }
    try:
        payload = DashboardConfigurationUpdateRequest.model_validate(
            await request.json()
        )
    except ValidationError as exc:
        await _audit_dashboard_configuration_change(
            session,
            result=AuditResult.ERROR,
            key=definition.key,
            action="configuration.update",
            metadata={
                **metadata,
                "error": "invalid_configuration_payload",
                "detail": exc.errors(),
            },
        )
        return JSONResponse(
            {"error": "invalid_configuration_payload", "detail": exc.errors()},
            status_code=400,
        )
    except Exception as exc:
        await _audit_dashboard_configuration_change(
            session,
            result=AuditResult.ERROR,
            key=definition.key,
            action="configuration.update",
            metadata={**metadata, "error": "invalid_json", "detail": str(exc)},
        )
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    actor_provider, actor_subject = _session_audit_actor(session)
    audit_action = "configuration.clear" if payload.clear else "configuration.update"
    try:
        if payload.clear:
            await asyncio.to_thread(delete_runtime_config_value, settings, definition)
        else:
            if definition.is_secret and not str(payload.value or "").strip():
                await _audit_dashboard_configuration_change(
                    session,
                    result=AuditResult.ERROR,
                    key=definition.key,
                    action=audit_action,
                    metadata={**metadata, "error": "secret_value_required"},
                )
                return JSONResponse(
                    {"error": "secret_value_required"},
                    status_code=400,
                )
            await asyncio.to_thread(
                set_runtime_config_value,
                settings,
                definition,
                payload.value,
                updated_by_provider=actor_provider.value,
                updated_by_subject=actor_subject,
            )
    except ValueError as exc:
        status_code = 409 if "environment" in str(exc) else 400
        await _audit_dashboard_configuration_change(
            session,
            result=AuditResult.ERROR,
            key=definition.key,
            action=audit_action,
            metadata={**metadata, "error": str(exc)},
        )
        return JSONResponse({"error": str(exc)}, status_code=status_code)
    except RuntimeError as exc:
        await _audit_dashboard_configuration_change(
            session,
            result=AuditResult.ERROR,
            key=definition.key,
            action=audit_action,
            metadata={**metadata, "error": str(exc)},
        )
        return JSONResponse({"error": str(exc)}, status_code=409)

    global _AGENT_ORCHESTRATOR
    with _AGENT_ORCHESTRATOR_LOCK:
        _AGENT_ORCHESTRATOR = None
    await _audit_dashboard_configuration_change(
        session,
        result=AuditResult.SUCCESS,
        key=definition.key,
        action=audit_action,
        metadata=metadata,
    )
    items = await asyncio.to_thread(list_runtime_config, settings)
    return JSONResponse({"items": items})


async def dashboard_rerun_job_handler(
    request: Request,
    job_id: str,
) -> JSONResponse:
    """Rerun one job from the authenticated dashboard."""
    session, error_response, dry_run = await _dashboard_write_session_or_dry_run(
        request,
        required_permission=DASHBOARD_PERMISSION_JOBS_WRITE,
        dry_run_permission=DASHBOARD_PERMISSION_JOBS_WRITE_DRY_RUN,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    if dry_run:
        payload, status_code = await _rerun_job_dry_run(job_id)
        return JSONResponse(payload, status_code=status_code)

    payload, status_code = await _rerun_job(job_id, request.app.state.queue)
    if status_code == 202:
        await _audit_dashboard_job_rerun(session, payload)
    return JSONResponse(payload, status_code=status_code)


async def dashboard_sync_people_handler(request: Request) -> JSONResponse:
    """Queue a people-cache sync from the authenticated dashboard."""
    session, error_response, dry_run = await _dashboard_write_session_or_dry_run(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC,
        dry_run_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC_DRY_RUN,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    if dry_run:
        return JSONResponse(
            {
                "status": "dry_run",
                "dry_run": True,
                "source": "dashboard",
                "would_enqueue": {
                    "queue": settings.redis_queue_name,
                    "job_type": "sync_people_from_crm_job",
                    "reason": "dashboard",
                    "idempotency_key_pattern": "crm-sync:<interval-bucket>",
                },
            }
        )

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


def _redact_newsletter_sync_preview(value: object) -> object:
    """Recursively redact emails from dashboard newsletter dry-run previews."""
    if isinstance(value, str):
        return redact_email_addresses(value)
    if isinstance(value, list):
        return [_redact_newsletter_sync_preview(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_newsletter_sync_preview(item) for key, item in value.items()
        }
    return value


async def dashboard_sync_newsletters_handler(request: Request) -> JSONResponse:
    """Queue a 508 members newsletter sync from the authenticated dashboard."""
    session, error_response, dry_run = await _dashboard_write_session_or_dry_run(
        request,
        required_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC,
        dry_run_permission=DASHBOARD_PERMISSION_PEOPLE_SYNC_DRY_RUN,
    )
    if error_response is not None:
        return error_response
    assert session is not None

    csrf_error = _dashboard_same_origin_post_or_error(request)
    if csrf_error is not None:
        return csrf_error

    if dry_run:
        try:
            preview = await asyncio.to_thread(
                NewsletterSyncProcessor(settings).sync_508_members,
                dry_run=True,
            )
        except Exception as exc:
            logger.warning(
                "Newsletter sync dry run failed: %s",
                type(exc).__name__,
            )
            return JSONResponse(
                {
                    "status": "dry_run_failed",
                    "dry_run": True,
                    "source": "dashboard",
                    "error": "newsletter_dry_run_failed",
                },
                status_code=502,
            )
        return JSONResponse(
            {
                "status": "dry_run",
                "dry_run": True,
                "source": "dashboard",
                "preview": _redact_newsletter_sync_preview(preview),
                "would_enqueue": {
                    "queue": settings.redis_queue_name,
                    "job_type": "sync_508_members_newsletters_job",
                    "reason": "dashboard",
                    "idempotency_key_pattern": "newsletter-sync:508-members:dashboard:<timestamp>:<uuid>",
                },
            }
        )

    job = await _enqueue_newsletter_sync_job(
        request.app.state.queue, reason="dashboard"
    )
    actor_provider, actor_subject = _session_audit_actor(session)
    await _write_auth_audit_event(
        action="newsletter.508_members_sync",
        result=AuditResult.SUCCESS,
        actor_subject=actor_subject,
        actor_display_name=session.display_name,
        actor_provider=actor_provider,
        resource_type="newsletter_sync",
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
    if not _is_webhook_authorized(request):
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
    if not _is_webhook_authorized(request):
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
    if not _is_webhook_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        payload = GoogleFormsIntakePayload.model_validate(payload_data)
    except (ValidationError, TypeError) as exc:
        logger.warning(
            "Rejecting Google Forms intake webhook: invalid payload: %s",
            exc,
        )
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

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
    normalized_payload["raw_payload"] = _sanitize_intake_raw_payload(payload_data)

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


async def tally_intake_webhook_handler(request: Request) -> JSONResponse:
    """Validate a Tally intake submission and enqueue a processing job."""
    body = await request.body()
    if not _is_tally_webhook_authorized(request, body):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = json.loads(body)
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    try:
        tally_payload = TallyWebhookPayload.model_validate(payload_data)
    except (ValidationError, TypeError) as exc:
        logger.warning("Rejecting Tally webhook: invalid payload: %s", exc)
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    if tally_payload.event_type != "FORM_RESPONSE":
        return JSONResponse(
            {
                "status": "ignored",
                "source": "tally",
                "event_type": tally_payload.event_type,
            },
            status_code=202,
        )

    form_validation_error = _validate_tally_submission(tally_payload)
    if form_validation_error is not None:
        return form_validation_error

    try:
        payload = GoogleFormsIntakePayload.model_validate(
            _tally_to_intake_payload(tally_payload)
        )
    except (ValidationError, TypeError) as exc:
        logger.warning(
            "Rejecting Tally webhook: invalid normalized intake payload: %s",
            exc,
        )
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    email = (payload.email or "").strip().lower()
    first_name = (payload.first_name or "").strip()
    last_name = (payload.last_name or "").strip()
    if not email or not first_name or not last_name:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)

    normalized_payload = payload.model_dump(exclude_none=True)
    raw_tally_fields = [
        field.model_dump(by_alias=True, exclude_none=True)
        for field in tally_payload.data.fields
    ]
    normalized_payload.update(
        {
            "source": "tally",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "raw_payload": _sanitize_intake_raw_payload(payload_data),
            "raw_tally_fields": _sanitize_intake_raw_payload(raw_tally_fields),
        }
    )

    idempotency_key = _google_forms_intake_idempotency_key(
        email=email,
        submission_id=payload.submission_id,
        submitted_at=payload.submitted_at,
        payload=normalized_payload,
    )
    tally_idempotency_key = f"tally:{idempotency_key}"
    dry_run_mode = _tally_intake_dry_run_mode(request)

    if dry_run_mode == "webhook":
        preview_payload = {
            key: value
            for key, value in normalized_payload.items()
            if key not in {"raw_payload", "raw_tally_fields"}
        }
        return JSONResponse(
            {
                "status": "dry_run",
                "source": "tally",
                "dry_run": True,
                "email": email,
                "normalized_payload": preview_payload,
                "raw_tally_field_count": len(tally_payload.data.fields),
                "would_enqueue": {
                    "job_type": "process_intake_form_job",
                    "idempotency_key": tally_idempotency_key,
                    "queue": settings.redis_queue_name,
                },
            },
            status_code=200,
        )

    if dry_run_mode == "worker":
        normalized_payload["dry_run"] = True
        tally_idempotency_key = f"tally:dry-run:{idempotency_key}"

    queue = request.app.state.queue
    try:
        job = await asyncio.to_thread(
            enqueue_job,
            queue=queue,
            fn=JOB_FUNCTIONS["process_intake_form_job"],
            args=(normalized_payload,),
            settings=settings,
            idempotency_key=tally_idempotency_key,
        )
    except Exception:
        logger.exception(
            "Failed enqueueing Tally intake form job masked_email=%s",
            mask_email(email),
        )
        return JSONResponse({"error": "enqueue_failed"}, status_code=503)

    return JSONResponse(
        {
            "status": "queued",
            "source": "tally",
            "dry_run": dry_run_mode == "worker",
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


async def _write_agent_audit_event(
    *,
    context: AgentIdentityContext,
    action: str,
    result: AuditResult,
    plan: AgentPlan | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort agent audit write that never breaks command execution."""
    try:
        await asyncio.to_thread(
            insert_audit_event,
            settings,
            AuditEventInput(
                source=AuditSource.DISCORD,
                action=action,
                result=result,
                actor_provider=ActorProvider.DISCORD,
                actor_subject=context.discord_user_id,
                resource_type="agent_plan" if plan is not None else "agent_request",
                resource_id=plan.plan_id if plan is not None else None,
                correlation_id=(
                    context.operation_id or context.interaction_id or context.message_id
                ),
                metadata=metadata or {},
            ),
        )
    except Exception:
        logger.warning(
            "Best-effort agent audit write failed action=%s user=%s",
            action,
            context.discord_user_id,
            exc_info=True,
        )


def _schedule_agent_audit_event(
    *,
    context: AgentIdentityContext,
    action: str,
    result: AuditResult,
    plan: AgentPlan | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Schedule a best-effort agent audit write and keep it alive until done."""
    task = asyncio.create_task(
        _write_agent_audit_event(
            context=context,
            action=action,
            result=result,
            plan=plan,
            metadata=metadata,
        )
    )
    _AGENT_AUDIT_TASKS.add(task)
    task.add_done_callback(_AGENT_AUDIT_TASKS.discard)


def _is_agent_plan_expired(plan: AgentPlan, *, now: datetime | None = None) -> bool:
    if plan.expires_at is None:
        return False
    expires_at = plan.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    comparison_time = now or datetime.now(timezone.utc)
    return comparison_time > expires_at.astimezone(timezone.utc)


def _cleanup_expired_pending_agent_plans(*, now: datetime | None = None) -> None:
    comparison_time = now or datetime.now(timezone.utc)
    expired_plan_ids = [
        plan_id
        for plan_id, (plan, _context) in _PENDING_AGENT_PLANS.items()
        if _is_agent_plan_expired(plan, now=comparison_time)
    ]
    for plan_id in expired_plan_ids:
        _PENDING_AGENT_PLANS.pop(plan_id, None)


def _pending_agent_plan_count_for_actor(discord_user_id: str) -> int:
    return sum(
        1
        for _plan, context in _PENDING_AGENT_PLANS.values()
        if context.discord_user_id == discord_user_id
    )


def _confirmation_execution_context(
    *,
    original_context: AgentIdentityContext,
    confirmation_context: AgentIdentityContext,
) -> AgentIdentityContext:
    roles = [role for role in confirmation_context.roles if role.strip()]
    role_ids = [role_id for role_id in confirmation_context.role_ids if role_id.strip()]
    return AgentIdentityContext(
        discord_user_id=original_context.discord_user_id,
        internal_user_id=original_context.internal_user_id,
        organization_id=original_context.organization_id,
        workspace_id=original_context.workspace_id,
        project_id=original_context.project_id,
        guild_id=original_context.guild_id,
        channel_id=original_context.channel_id,
        thread_id=original_context.thread_id,
        parent_message_id=original_context.parent_message_id,
        response_destination_visibility=(
            original_context.response_destination_visibility
        ),
        role_ids=role_ids,
        roles=roles,
        scopes=[],
        impersonation=(
            original_context.impersonation or confirmation_context.impersonation
        ),
        interaction_id=(
            confirmation_context.interaction_id or original_context.interaction_id
        ),
        message_id=confirmation_context.message_id or original_context.message_id,
        operation_id=original_context.operation_id,
        context_snippets=original_context.context_snippets,
    )


def _confirmation_execution_scopes(
    *,
    original_context: AgentIdentityContext,
    confirmation_context: AgentIdentityContext,
) -> set[str]:
    policy = PolicyEngine.from_settings(settings)
    original_scopes = policy.scopes_for_context(original_context)
    confirmation_scopes = policy.scopes_for_context(confirmation_context)
    return original_scopes & confirmation_scopes


def _pending_agent_plans_lock() -> asyncio.Lock:
    global _PENDING_AGENT_PLANS_LOCK, _PENDING_AGENT_PLANS_LOCK_LOOP
    loop = asyncio.get_running_loop()
    if _PENDING_AGENT_PLANS_LOCK is None or _PENDING_AGENT_PLANS_LOCK_LOOP is not loop:
        _PENDING_AGENT_PLANS_LOCK = asyncio.Lock()
        _PENDING_AGENT_PLANS_LOCK_LOOP = loop
    return _PENDING_AGENT_PLANS_LOCK


async def _store_pending_agent_plan(
    plan: AgentPlan,
    context: AgentIdentityContext,
) -> bool:
    async with _pending_agent_plans_lock():
        _cleanup_expired_pending_agent_plans()
        if (
            len(_PENDING_AGENT_PLANS) >= _MAX_PENDING_AGENT_PLANS
            or _pending_agent_plan_count_for_actor(context.discord_user_id)
            >= _MAX_PENDING_AGENT_PLANS_PER_ACTOR
        ):
            return False
        _PENDING_AGENT_PLANS[plan.plan_id] = (plan, context)
        return True


async def _claim_pending_agent_plan(
    plan_id: str,
    *,
    discord_user_id: str,
) -> tuple[str, tuple[AgentPlan, AgentIdentityContext] | None]:
    async with _pending_agent_plans_lock():
        now = datetime.now(timezone.utc)
        pending = _PENDING_AGENT_PLANS.get(plan_id)
        if pending is None:
            _cleanup_expired_pending_agent_plans(now=now)
            return "not_found", None

        plan, original_context = pending
        if original_context.discord_user_id != discord_user_id:
            _cleanup_expired_pending_agent_plans(now=now)
            return "actor_mismatch", pending

        if _is_agent_plan_expired(plan, now=now):
            _PENDING_AGENT_PLANS.pop(plan_id, None)
            _cleanup_expired_pending_agent_plans(now=now)
            return "expired", pending

        claimed = _PENDING_AGENT_PLANS.pop(plan_id, pending)
        _cleanup_expired_pending_agent_plans(now=now)
        return "claimed", claimed


async def agent_request_handler(request: Request) -> JSONResponse:
    """Plan and execute supported English agent commands."""
    if not _is_agent_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, dict):
        return JSONResponse({"error": "payload_must_be_object"}, status_code=400)

    try:
        payload = AgentRequest.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)}, status_code=400
        )
    if _agent_request_rate_limited(payload.context.discord_user_id):
        _schedule_agent_audit_event(
            context=payload.context,
            action="agent.request",
            result=AuditResult.DENIED,
            plan=None,
            metadata={
                "status": "denied",
                "reason": "rate_limited",
                "message_sanitized": _sanitize_agent_audit_message(payload.message),
            },
        )
        return JSONResponse(
            {
                "status": "denied",
                "message": "Too many agent requests. Try again in a minute.",
            },
            status_code=429,
        )

    try:
        orchestrator = _get_agent_orchestrator()
    except Exception as exc:
        logger.error("Agent orchestrator configuration failed", exc_info=True)
        return JSONResponse(
            {
                "status": "failed",
                "message": "Agent routes are not configured correctly.",
                "error": str(exc),
            },
            status_code=503,
        )

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                _run_agent_plan,
                orchestrator,
                payload.message,
                payload.context,
            ),
            timeout=settings.agent_request_response_budget_seconds,
        )
    except AgentRequestPlanCapacityError:
        _schedule_agent_audit_event(
            context=payload.context,
            action="agent.request",
            result=AuditResult.ERROR,
            plan=None,
            metadata={
                "status": "failed",
                "reason": "agent_planner_capacity_exceeded",
                "message_sanitized": _sanitize_agent_audit_message(payload.message),
            },
        )
        return JSONResponse(
            {
                "status": "failed",
                "message": "Agent capacity is busy. Please try again shortly.",
            },
            status_code=503,
        )
    except TimeoutError:
        _schedule_agent_audit_event(
            context=payload.context,
            action="agent.request",
            result=AuditResult.ERROR,
            plan=None,
            metadata={
                "status": "failed",
                "reason": "agent_response_budget_exceeded",
                "message_sanitized": _sanitize_agent_audit_message(payload.message),
            },
        )
        return JSONResponse(
            {
                "status": "failed",
                "message": (
                    "The agent request took too long to complete. Please narrow "
                    "the request and try again."
                ),
            },
            status_code=504,
        )
    if response.plan is not None and response.status == "requires_confirmation":
        # Confirmation execution uses the frozen plan and fresh role snapshot;
        # it never needs raw client-supplied thread text. Do not retain that
        # potentially sensitive/untrusted text for the ten-minute plan window.
        pending_context = payload.context.model_copy(update={"context_snippets": []})
        stored = await _store_pending_agent_plan(response.plan, pending_context)
        if not stored:
            _schedule_agent_audit_event(
                context=payload.context,
                action="agent.request",
                result=AuditResult.ERROR,
                plan=response.plan,
                metadata={
                    "status": "failed",
                    "reason": "pending_plan_capacity_exceeded",
                    "message_sanitized": _sanitize_agent_audit_message(payload.message),
                },
            )
            response = AgentResponse(
                status="failed",
                message=("Agent confirmation capacity is full. Try again shortly."),
            )
            return JSONResponse(
                response.model_dump(mode="json"),
                status_code=503,
            )

    audit_result = AuditResult.SUCCESS
    if response.status == "denied":
        audit_result = AuditResult.DENIED
    elif response.status == "failed":
        audit_result = AuditResult.ERROR
    _schedule_agent_audit_event(
        context=payload.context,
        action="agent.request",
        result=audit_result,
        plan=response.plan,
        metadata=_agent_request_audit_metadata(
            message=payload.message,
            response=response,
        ),
    )

    status_code = {
        "executed": 200,
        "requires_confirmation": 202,
        "needs_clarification": 422,
        "canceled": 200,
        "denied": 403,
        "failed": 500,
    }[response.status]
    return JSONResponse(response.model_dump(mode="json"), status_code=status_code)


async def agent_confirmation_handler(
    request: Request,
    plan_id: str,
) -> JSONResponse:
    """Execute or cancel a frozen agent plan after user confirmation."""
    if not _is_agent_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload_data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not isinstance(payload_data, dict):
        return JSONResponse({"error": "payload_must_be_object"}, status_code=400)

    try:
        payload = AgentConfirmationRequest.model_validate(payload_data)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "invalid_payload", "detail": str(exc)}, status_code=400
        )

    orchestrator: AgentOrchestrator | None = None
    if payload.confirm:
        try:
            orchestrator = _get_agent_orchestrator()
        except Exception as exc:
            logger.error("Agent orchestrator configuration failed", exc_info=True)
            return JSONResponse(
                {
                    "status": "failed",
                    "message": "Agent routes are not configured correctly.",
                    "error": str(exc),
                },
                status_code=503,
            )

    claim_status, pending = await _claim_pending_agent_plan(
        plan_id,
        discord_user_id=payload.context.discord_user_id,
    )
    if pending is None:
        _schedule_agent_audit_event(
            context=payload.context,
            action="agent.confirmation",
            result=AuditResult.DENIED,
            plan=None,
            metadata={"reason": "plan_not_found", "plan_id": plan_id},
        )
        return JSONResponse({"error": "plan_not_found"}, status_code=404)

    plan, original_context = pending
    if claim_status == "actor_mismatch":
        _schedule_agent_audit_event(
            context=payload.context,
            action="agent.confirmation",
            result=AuditResult.DENIED,
            plan=plan,
            metadata={"reason": "actor_mismatch"},
        )
        return JSONResponse({"error": "actor_mismatch"}, status_code=403)
    if claim_status == "expired":
        _schedule_agent_audit_event(
            context=original_context,
            action="agent.confirmation",
            result=AuditResult.DENIED,
            plan=plan,
            metadata={"reason": "plan_expired"},
        )
        return JSONResponse({"error": "plan_expired"}, status_code=410)

    if not payload.confirm:
        _schedule_agent_audit_event(
            context=original_context,
            action="agent.confirmation",
            result=AuditResult.SUCCESS,
            plan=plan,
            metadata={"status": "canceled"},
        )
        response = AgentResponse(
            status="canceled",
            plan=plan,
            message="Agent plan was canceled before execution.",
        )
        return JSONResponse(response.model_dump(mode="json"), status_code=200)

    execution_context = _confirmation_execution_context(
        original_context=original_context,
        confirmation_context=payload.context,
    )
    assert orchestrator is not None
    results = await asyncio.to_thread(
        orchestrator.execute_plan,
        plan,
        execution_context,
        confirmed=True,
        effective_scopes=_confirmation_execution_scopes(
            original_context=original_context,
            confirmation_context=payload.context,
        ),
    )
    if any(result.status == "denied" for result in results):
        status = "denied"
    elif all(result.status == "succeeded" for result in results):
        status = "executed"
    else:
        status = "failed"
    if status == "executed":
        response = AgentResponse(
            status="executed",
            plan=plan,
            results=results,
            message="Executed the confirmed agent plan.",
        )
    elif status == "denied":
        response = AgentResponse(
            status="denied",
            plan=plan,
            results=results,
            message="The confirmed agent plan was denied by policy.",
        )
    else:
        response = AgentResponse(
            status="failed",
            plan=plan,
            results=results,
            message="One or more confirmed agent actions failed.",
        )
    audit_result = {
        "executed": AuditResult.SUCCESS,
        "denied": AuditResult.DENIED,
        "failed": AuditResult.ERROR,
    }[status]
    _schedule_agent_audit_event(
        context=execution_context,
        action="agent.confirmation",
        result=audit_result,
        plan=plan,
        metadata={
            "status": response.status,
            "intent": plan.intent,
            "planner": plan.planner,
            "model": plan.model.model,
            "model_tier": plan.model_tier,
            "model_source_tier": plan.model.source_tier,
            "action_names": [action.tool_name for action in plan.actions],
            # Results can contain CRM, account, ERP, or private-memory data.
            # Audit records retain outcomes for operational traceability, not
            # the returned payloads themselves.
            "tool_outcomes": [
                {"tool_name": result.tool_name, "status": result.status}
                for result in results
            ],
        },
    )
    return JSONResponse(
        response.model_dump(mode="json"),
        status_code={"executed": 200, "denied": 403, "failed": 500}[status],
    )


async def auth_login_handler(
    request: Request,
    next_path: str | None = Query(default=None, alias="next"),
    discord_link_token: str | None = Query(default=None),
) -> JSONResponse | RedirectResponse | HTMLResponse:
    """Start OIDC auth-code flow with PKCE and server-side state."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    oidc = _oidc_client_from_app(request.app)
    if not oidc.configured:
        if not _request_prefers_json(request):
            return HTMLResponse(oidc_not_configured_html(), status_code=503)
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

        dev_role_identity = _dev_discord_link_identity_from_roles(
            discord_user_id=grant.discord_user_id,
            discord_roles=grant.discord_roles,
            discord_display_name=grant.discord_display_name,
        )
        identity: DiscordAdminIdentity | None
        if enforce_discord_link_identity_checks:
            if not email and dev_role_identity is None:
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
            linked = False
            if email:
                linked = await verifier.is_dashboard_email_for_discord_user(
                    email=email,
                    discord_user_id=grant.discord_user_id,
                    http_client=http_client,
                )
            if not linked:
                if dev_role_identity is not None:
                    identity = dev_role_identity
                else:
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
            else:
                identity = await verifier.resolve_dashboard_identity(
                    discord_user_id=grant.discord_user_id,
                    http_client=http_client,
                )
                if identity is None:
                    identity = dev_role_identity
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
                identity = dev_role_identity
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
    expires_at = now + max(1, settings.auth_session_ttl_seconds)

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

    return JSONResponse(await _session_payload(session))


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
    dev_role_identity = None
    if not is_dashboard_user:
        dev_role_identity = _dev_discord_link_identity_from_roles(
            discord_user_id=payload.discord_user_id,
            discord_roles=payload.discord_roles,
            discord_display_name=payload.discord_display_name,
        )
        is_dashboard_user = dev_role_identity is not None
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
            discord_roles=payload.discord_roles if dev_role_identity else [],
            discord_display_name=(
                payload.discord_display_name if dev_role_identity else None
            ),
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
) -> JSONResponse | HTMLResponse:
    """Render a non-consuming interstitial for one-time Discord deep links."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    grant = await store.get_discord_link(token)
    if grant is None:
        if _request_prefers_json(request):
            return JSONResponse({"error": "link_not_found"}, status_code=404)
        response = HTMLResponse(discord_link_unavailable_html(), status_code=404)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    response = HTMLResponse(
        discord_link_continue_html(token=token),
        status_code=200,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def auth_discord_link_consume_handler(
    request: Request,
    token: str,
) -> JSONResponse | HTMLResponse | RedirectResponse:
    """Consume one-time Discord deep link and create or resume a dashboard session."""
    store = _auth_store_from_app(request.app)
    if store is None:
        return JSONResponse({"error": "auth_not_ready"}, status_code=503)

    grant = await store.get_discord_link(token)
    if grant is None:
        replay = await _discord_link_replay_from_request(
            store=store,
            token=token,
            request=request,
        )
        if replay is not None:
            return _discord_link_replay_redirect(replay)
        if _request_prefers_json(request):
            return JSONResponse({"error": "link_not_found"}, status_code=404)
        response = HTMLResponse(discord_link_unavailable_html(), status_code=404)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    session_id, session = await _current_session(request)
    if not settings.discord_link_require_oidc_identity_checks:
        verifier = _discord_admin_verifier_from_app(request.app)
        http_client = _http_client_from_app(request.app)
        identity = await verifier.resolve_dashboard_identity(
            discord_user_id=grant.discord_user_id,
            http_client=http_client,
        )
        if identity is None:
            identity = _dev_discord_link_identity_from_roles(
                discord_user_id=grant.discord_user_id,
                discord_roles=grant.discord_roles,
                discord_display_name=grant.discord_display_name,
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
        await _save_discord_link_replay(
            store=store,
            token=token,
            request=request,
            session_id=session_id,
            next_path=grant.next_path,
        )
        await store.delete_discord_link(token)

        redirect_response = RedirectResponse(url=grant.next_path, status_code=302)
        _set_session_cookie(redirect_response, session_id)
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
        return redirect_response

    if session is not None:
        dev_role_identity = _dev_discord_link_identity_from_roles(
            discord_user_id=grant.discord_user_id,
            discord_roles=grant.discord_roles,
            discord_display_name=grant.discord_display_name,
        )
        if not session.email and dev_role_identity is None:
            return JSONResponse(
                {"error": "forbidden", "detail": "email_claim_required"},
                status_code=403,
            )

        verifier = _discord_admin_verifier_from_app(request.app)
        http_client = _http_client_from_app(request.app)
        linked = False
        if session.email:
            linked = await verifier.is_dashboard_email_for_discord_user(
                email=session.email,
                discord_user_id=grant.discord_user_id,
                http_client=http_client,
            )
        if not linked:
            if dev_role_identity is not None:
                identity = dev_role_identity
            else:
                return JSONResponse(
                    {
                        "error": "forbidden",
                        "detail": "oidc_user_not_linked_to_discord_dashboard_user",
                    },
                    status_code=403,
                )
        else:
            identity = await verifier.resolve_dashboard_identity(
                discord_user_id=grant.discord_user_id,
                http_client=http_client,
            )
            if identity is None:
                identity = dev_role_identity
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
        await _save_discord_link_replay(
            store=store,
            token=token,
            request=request,
            session_id=session_id,
            next_path=grant.next_path,
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
    redis_conn = get_redis_connection(settings)
    app.state.redis_conn = redis_conn
    app.state.postgres_conn_lock = asyncio.Lock()
    app.state.postgres_migrations_ok = True
    try:
        await asyncio.to_thread(run_job_migrations)
    except Exception:
        logger.exception("Failed to run job migrations during startup")
        app.state.postgres_migrations_ok = False
    try:
        app.state.postgres_conn = await asyncio.to_thread(
            get_postgres_connection,
            settings,
        )
    except Exception:
        logger.exception("Failed to open startup Postgres connection")
        app.state.postgres_conn = None
    app.state.queue = build_queue_client()
    app.state.auth_store = RedisAuthStore(redis_conn)
    app.state.oidc_client = OIDCProviderClient(settings)
    app.state.discord_admin_verifier = DiscordAdminVerifier(settings)
    app.state.http_client = httpx.AsyncClient(follow_redirects=False)

    crm_sync_skip_reason = _crm_sync_scheduler_skip_reason()
    if crm_sync_skip_reason is None:
        app.state.crm_sync_task = asyncio.create_task(_crm_sync_scheduler(app))
    elif crm_sync_skip_reason == "missing_espo":
        logger.warning(
            "CRM sync scheduler enabled but ESPO_BASE_URL/ESPO_API_KEY are not configured; skipping scheduler startup"
        )
    else:
        logger.info("CRM sync scheduler disabled by config")

    if settings.newsletter_sync_enabled:
        app.state.newsletter_sync_task = asyncio.create_task(
            _newsletter_sync_scheduler(app)
        )
    else:
        logger.info("508 members newsletter sync scheduler disabled by config")

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

        if hasattr(app.state, "newsletter_sync_task"):
            task = app.state.newsletter_sync_task
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

    register_routes(app, cast(BackendRouteSurface, sys.modules[__name__]))

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
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
