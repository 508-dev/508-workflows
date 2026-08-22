"""Persistence helpers for sourced job leads awaiting review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.job_channels import JobPostingType, normalize_job_posting_type
from five08.queue import get_postgres_connection, trusted_sql
from five08.settings import SharedSettings


JOB_LEAD_STAGING_RESERVATION_TTL_SECONDS = 15 * 60
_STAGING_CLEANUP_REQUIRED_METADATA_KEY = "_staging_cleanup_required"
_STAGING_SOURCE_FINGERPRINT_METADATA_KEY = "_staging_source_fingerprint"
_STAGING_CLEANUP_RESERVATION_STATE = "reservation"
_STAGING_CLEANUP_RECOVERY_STATE = "recovery"


def _job_lead_staging_source_changed_sql(
    *,
    persisted: str,
    incoming: str,
) -> str:
    """Return the SQL predicate for holding-thread content changes.

    `organization`, `location`, and `remote` intentionally compare the incoming
    value after the same COALESCE merge used by the source update.  A scraper
    omitting one of those optional fields therefore does not invalidate an
    otherwise unchanged staged thread.
    """
    return f"""
        {persisted}.source_url IS DISTINCT FROM {incoming}.source_url
        OR {persisted}.title IS DISTINCT FROM {incoming}.title
        OR {persisted}.organization IS DISTINCT FROM COALESCE(
            {incoming}.organization, {persisted}.organization
        )
        OR {persisted}.body_normalized IS DISTINCT FROM {incoming}.body_normalized
        OR {persisted}.posting_type IS DISTINCT FROM {incoming}.posting_type
        OR {persisted}.location IS DISTINCT FROM COALESCE(
            {incoming}.location, {persisted}.location
        )
        OR {persisted}.remote IS DISTINCT FROM COALESCE(
            {incoming}.remote, {persisted}.remote
        )
        OR {persisted}.apply_url IS DISTINCT FROM {incoming}.apply_url
        OR {persisted}.tags IS DISTINCT FROM {incoming}.tags
    """


class JobLeadStatus(StrEnum):
    """Review states for externally sourced job leads."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"


@dataclass(frozen=True)
class JobLeadInput:
    """One external job lead candidate produced by a source scraper."""

    source_key: str
    source_type: str
    external_id: str
    source_url: str
    title: str
    body_raw: str
    body_normalized: str
    organization: str | None = None
    external_parent_id: str | None = None
    source_posted_at: datetime | None = None
    posting_type: str | JobPostingType | None = JobPostingType.PART_TIME
    location: str | None = None
    remote: bool | None = None
    apply_url: str | None = None
    tags: list[str] | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class JobLead:
    """Persisted lead row used by review and publishing flows."""

    id: str
    status: JobLeadStatus
    source_key: str
    source_type: str
    external_id: str
    source_url: str
    title: str
    body_raw: str
    body_normalized: str
    organization: str | None
    external_parent_id: str | None
    source_posted_at: datetime | None
    posting_type: JobPostingType
    location: str | None
    remote: bool | None
    apply_url: str | None
    tags: list[str]
    confidence: float
    metadata: dict[str, Any]
    reviewed_by_discord_user_id: str | None
    reviewed_at: datetime | None
    discord_guild_id: str | None
    discord_channel_id: str | None
    discord_thread_id: str | None
    posted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    engagement_id: str | None = None
    staged_discord_guild_id: str | None = None
    staged_discord_channel_id: str | None = None
    staged_discord_thread_id: str | None = None
    staged_at: datetime | None = None


def job_lead_classification(lead: JobLead | dict[str, Any]) -> dict[str, Any]:
    """Return normalized contractor-classification metadata for display."""
    metadata = lead.get("metadata") if isinstance(lead, dict) else lead.metadata
    metadata = metadata if isinstance(metadata, dict) else {}
    classification = metadata.get("contractor_classification")
    if isinstance(classification, dict):
        return {
            "is_contractor_friendly": bool(
                classification.get("is_contractor_friendly", True)
            ),
            "posting_type": str(classification.get("posting_type") or ""),
            "tags": [
                str(tag) for tag in classification.get("tags", []) if str(tag).strip()
            ],
            "confidence": float(classification.get("confidence") or 0.0),
            "confidence_label": str(classification.get("confidence_label") or "low"),
            "rationale": str(classification.get("rationale") or "").strip(),
            "method": str(classification.get("method") or "unknown"),
            "contact_email": str(classification.get("contact_email") or "").strip()
            or None,
        }

    tags = lead.get("tags") if isinstance(lead, dict) else lead.tags
    confidence = lead.get("confidence") if isinstance(lead, dict) else lead.confidence
    tag_values = [str(tag) for tag in tags or [] if str(tag).strip()]
    confidence_value = float(confidence or 0.0)
    if confidence_value >= 0.75:
        confidence_label = "high"
    elif confidence_value >= 0.45:
        confidence_label = "medium"
    else:
        confidence_label = "low"
    return {
        "is_contractor_friendly": bool(tag_values),
        "posting_type": "",
        "tags": tag_values,
        "confidence": confidence_value,
        "confidence_label": confidence_label,
        "rationale": "",
        "method": "heuristic",
        "contact_email": None,
    }


def format_job_lead_review_summary(lead: JobLead | dict[str, Any]) -> str:
    """Return a human-readable employment classification summary."""
    classification = job_lead_classification(lead)
    method = classification.get("method")
    method_label = "LLM" if method == "llm" else "Keyword fallback"
    posting_type = str(classification.get("posting_type") or "")
    posting_type_label = {
        "part_time": "Part-time / contract",
        "full_time": "Full-time",
        "part_time_or_full_time": "Full-time or part-time / contract",
        "unknown": "Employment type unknown",
    }.get(posting_type, "Employment type unknown")
    tags = [str(tag) for tag in classification.get("tags", []) if str(tag).strip()]
    rationale = str(classification.get("rationale") or "").strip()
    summary = f"{method_label}: {posting_type_label}"
    if tags:
        summary = f"{summary}; evidence: {', '.join(tags[:5])}"
    if rationale:
        summary = f"{summary} - {rationale}"
    return summary


def job_lead_display_payload(lead: JobLead | dict[str, Any]) -> dict[str, Any]:
    """Return API payload with shared classification display fields."""
    if isinstance(lead, dict):
        payload = dict(lead)
    else:
        payload = {
            "id": lead.id,
            "status": lead.status.value,
            "source_key": lead.source_key,
            "source_type": lead.source_type,
            "external_id": lead.external_id,
            "external_parent_id": lead.external_parent_id,
            "source_url": lead.source_url,
            "source_posted_at": lead.source_posted_at,
            "title": lead.title,
            "organization": lead.organization,
            "body_raw": lead.body_raw,
            "body_normalized": lead.body_normalized,
            "posting_type": lead.posting_type.value,
            "location": lead.location,
            "remote": lead.remote,
            "apply_url": lead.apply_url,
            "tags": lead.tags,
            "confidence": lead.confidence,
            "metadata": lead.metadata,
            "reviewed_by_discord_user_id": lead.reviewed_by_discord_user_id,
            "reviewed_at": lead.reviewed_at,
            "discord_guild_id": lead.discord_guild_id,
            "discord_channel_id": lead.discord_channel_id,
            "discord_thread_id": lead.discord_thread_id,
            "posted_at": lead.posted_at,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
            "staged_discord_guild_id": lead.staged_discord_guild_id,
            "staged_discord_channel_id": lead.staged_discord_channel_id,
            "staged_discord_thread_id": lead.staged_discord_thread_id,
            "staged_at": lead.staged_at,
        }
    raw_engagement_id = (
        lead.get("engagement_id") if isinstance(lead, dict) else lead.engagement_id
    )
    engagement_id = str(raw_engagement_id or "").strip()
    if engagement_id:
        payload["engagement_id"] = engagement_id
    else:
        payload.pop("engagement_id", None)
    staging_recovery = job_lead_staging_recovery_details(lead)
    if staging_recovery is not None:
        payload["staging_recovery"] = staging_recovery
    else:
        payload.pop("staging_recovery", None)
    payload["contractor_classification"] = job_lead_classification(lead)
    payload["review_summary"] = format_job_lead_review_summary(lead)
    return payload


def _normalize_status(value: str | JobLeadStatus | None) -> JobLeadStatus:
    if isinstance(value, JobLeadStatus):
        return value
    try:
        return JobLeadStatus(str(value or "").strip().casefold())
    except ValueError:
        return JobLeadStatus.PENDING


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _job_lead_staging_source_fingerprint(
    lead: JobLead | JobLeadInput,
    *,
    posting_type: JobPostingType,
    tags: list[str],
) -> str:
    """Return a stable fingerprint of source fields rendered in a holding thread."""
    source_payload = {
        "apply_url": lead.apply_url,
        "body_normalized": lead.body_normalized,
        "location": lead.location,
        "organization": lead.organization,
        "posting_type": posting_type.value,
        "remote": lead.remote,
        "source_url": lead.source_url,
        "tags": tags,
        "title": lead.title.strip() or "Untitled job lead",
    }
    encoded = json.dumps(
        source_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def job_lead_staging_source_fingerprint(lead: JobLead | JobLeadInput) -> str:
    """Return the normalized source fingerprint for a holding-thread payload."""
    posting_type = normalize_job_posting_type(lead.posting_type)
    tags = sorted({tag.strip().casefold() for tag in lead.tags or [] if tag.strip()})
    return _job_lead_staging_source_fingerprint(
        lead,
        posting_type=posting_type,
        tags=tags,
    )


def job_lead_staging_recovery_details(
    lead: JobLead | dict[str, Any],
) -> dict[str, str | None] | None:
    """Return orphaned holding-thread details that require manual cleanup."""
    metadata = lead.get("metadata") if isinstance(lead, dict) else lead.metadata
    raw_recovery = (
        metadata.get(_STAGING_CLEANUP_REQUIRED_METADATA_KEY)
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(raw_recovery, dict):
        return None
    return {
        key: str(raw_recovery.get(key) or "").strip() or None
        for key in ("guild_id", "channel_id", "thread_id")
    }


def _job_lead_source_metadata(lead: JobLeadInput) -> dict[str, Any]:
    """Return external source metadata without internal staging-state keys."""
    metadata = dict(lead.metadata or {})
    metadata.pop(_STAGING_CLEANUP_REQUIRED_METADATA_KEY, None)
    metadata.pop(_STAGING_SOURCE_FINGERPRINT_METADATA_KEY, None)
    return metadata


def _seed_job_lead_staging_source_fingerprint(cursor: Any, lead: JobLead) -> None:
    """Persist the fingerprint computed from this post-merge database row."""
    fingerprint = job_lead_staging_source_fingerprint(lead)
    if lead.metadata.get(_STAGING_SOURCE_FINGERPRINT_METADATA_KEY) == fingerprint:
        return
    query = """
        UPDATE job_leads
        SET
            metadata = metadata || jsonb_build_object(
                %s::text, %s::text
            ),
            updated_at = NOW()
        WHERE id = %s
    """
    cursor.execute(
        query,
        (
            _STAGING_SOURCE_FINGERPRINT_METADATA_KEY,
            fingerprint,
            lead.id,
        ),
    )


def _as_lead(row: dict[str, Any]) -> JobLead:
    tags = row.get("tags") or []
    metadata = row.get("metadata") or {}
    return JobLead(
        id=str(row["id"]),
        status=_normalize_status(row.get("status")),
        source_key=str(row["source_key"]),
        source_type=str(row["source_type"]),
        external_id=str(row["external_id"]),
        source_url=str(row["source_url"]),
        title=str(row["title"]),
        body_raw=str(row.get("body_raw") or ""),
        body_normalized=str(row.get("body_normalized") or ""),
        organization=row.get("organization"),
        external_parent_id=row.get("external_parent_id"),
        source_posted_at=row.get("source_posted_at"),
        posting_type=normalize_job_posting_type(row.get("posting_type")),
        location=row.get("location"),
        remote=row.get("remote"),
        apply_url=row.get("apply_url"),
        tags=list(tags) if isinstance(tags, list) else [],
        confidence=float(row.get("confidence") or 0.0),
        metadata=metadata if isinstance(metadata, dict) else {},
        reviewed_by_discord_user_id=row.get("reviewed_by_discord_user_id"),
        reviewed_at=row.get("reviewed_at"),
        discord_guild_id=row.get("discord_guild_id"),
        discord_channel_id=row.get("discord_channel_id"),
        discord_thread_id=row.get("discord_thread_id"),
        posted_at=row.get("posted_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        engagement_id=str(row.get("engagement_id") or "").strip() or None,
        staged_discord_guild_id=(
            str(row.get("staged_discord_guild_id") or "").strip() or None
        ),
        staged_discord_channel_id=(
            str(row.get("staged_discord_channel_id") or "").strip() or None
        ),
        staged_discord_thread_id=(
            str(row.get("staged_discord_thread_id") or "").strip() or None
        ),
        staged_at=row.get("staged_at"),
    )


def upsert_job_lead(
    settings: SharedSettings, lead: JobLeadInput
) -> tuple[str | None, bool]:
    """Create or update a sourced lead without changing human review state."""
    lead_id = str(uuid4())
    posting_type = normalize_job_posting_type(lead.posting_type)
    source_posted_at = _as_utc(lead.source_posted_at)
    tags = sorted({tag.strip().casefold() for tag in lead.tags or [] if tag.strip()})
    metadata = _job_lead_source_metadata(lead)
    staging_source_changed = _job_lead_staging_source_changed_sql(
        persisted="job_leads",
        incoming="EXCLUDED",
    )
    query = f"""
        INSERT INTO job_leads (
            id,
            status,
            source_key,
            source_type,
            external_id,
            external_parent_id,
            source_url,
            source_posted_at,
            title,
            organization,
            body_raw,
            body_normalized,
            posting_type,
            location,
            remote,
            apply_url,
            tags,
            confidence,
            metadata
        ) VALUES (
            %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (source_key, external_id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            source_posted_at = COALESCE(EXCLUDED.source_posted_at, job_leads.source_posted_at),
            title = EXCLUDED.title,
            organization = COALESCE(EXCLUDED.organization, job_leads.organization),
            body_raw = EXCLUDED.body_raw,
            body_normalized = EXCLUDED.body_normalized,
            posting_type = EXCLUDED.posting_type,
            location = COALESCE(EXCLUDED.location, job_leads.location),
            remote = COALESCE(EXCLUDED.remote, job_leads.remote),
            apply_url = EXCLUDED.apply_url,
            tags = EXCLUDED.tags,
            confidence = GREATEST(job_leads.confidence, EXCLUDED.confidence),
            metadata = job_leads.metadata || EXCLUDED.metadata,
            staged_discord_guild_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_guild_id
            END,
            staged_discord_channel_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_channel_id
            END,
            staged_discord_thread_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_thread_id
            END,
            staged_at = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_at
            END,
            staging_reservation_token = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staging_reservation_token
            END,
            staging_reserved_at = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staging_reserved_at
            END,
            updated_at = NOW()
        WHERE job_leads.status IN ('pending', 'rejected')
        RETURNING job_leads.*, (xmax = 0) AS inserted
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                trusted_sql(query),
                (
                    lead_id,
                    lead.source_key,
                    lead.source_type,
                    lead.external_id,
                    lead.external_parent_id,
                    lead.source_url,
                    source_posted_at,
                    lead.title.strip() or "Untitled job lead",
                    lead.organization,
                    lead.body_raw,
                    lead.body_normalized,
                    posting_type.value,
                    lead.location,
                    lead.remote,
                    lead.apply_url,
                    tags,
                    float(max(0.0, min(1.0, lead.confidence))),
                    Jsonb(metadata),
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None, False
            persisted = _as_lead(row)
            _seed_job_lead_staging_source_fingerprint(cursor, persisted)
    return persisted.id, bool(row["inserted"])


def update_existing_job_lead(
    settings: SharedSettings,
    lead: JobLeadInput,
) -> str | None:
    """Refresh a reviewable stored lead without creating a new candidate."""
    posting_type = normalize_job_posting_type(lead.posting_type)
    source_posted_at = _as_utc(lead.source_posted_at)
    tags = sorted({tag.strip().casefold() for tag in lead.tags or [] if tag.strip()})
    metadata = _job_lead_source_metadata(lead)
    staging_source_changed = _job_lead_staging_source_changed_sql(
        persisted="job_leads",
        incoming="incoming",
    )
    query = f"""
        WITH incoming AS (
            SELECT
                %s::text AS source_url,
                %s::timestamptz AS source_posted_at,
                %s::text AS title,
                %s::text AS organization,
                %s::text AS body_raw,
                %s::text AS body_normalized,
                %s::text AS posting_type,
                %s::text AS location,
                %s::boolean AS remote,
                %s::text AS apply_url,
                %s::text[] AS tags,
                %s::double precision AS confidence,
                %s::jsonb AS metadata
        )
        UPDATE job_leads
        SET source_url = incoming.source_url,
            source_posted_at = COALESCE(
                incoming.source_posted_at, job_leads.source_posted_at
            ),
            title = incoming.title,
            organization = COALESCE(incoming.organization, job_leads.organization),
            body_raw = incoming.body_raw,
            body_normalized = incoming.body_normalized,
            posting_type = incoming.posting_type,
            location = COALESCE(incoming.location, job_leads.location),
            remote = COALESCE(incoming.remote, job_leads.remote),
            apply_url = incoming.apply_url,
            tags = incoming.tags,
            confidence = GREATEST(job_leads.confidence, incoming.confidence),
            metadata = job_leads.metadata || incoming.metadata,
            staged_discord_guild_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_guild_id
            END,
            staged_discord_channel_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_channel_id
            END,
            staged_discord_thread_id = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_discord_thread_id
            END,
            staged_at = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staged_at
            END,
            staging_reservation_token = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staging_reservation_token
            END,
            staging_reserved_at = CASE
                WHEN job_leads.status IN ('pending', 'rejected')
                    AND ({staging_source_changed})
                THEN NULL
                ELSE job_leads.staging_reserved_at
            END,
            updated_at = NOW()
        FROM incoming
        WHERE job_leads.source_key = %s
          AND job_leads.external_id = %s
          AND job_leads.status IN ('pending', 'rejected')
        RETURNING job_leads.*
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                trusted_sql(query),
                (
                    lead.source_url,
                    source_posted_at,
                    lead.title.strip() or "Untitled job lead",
                    lead.organization,
                    lead.body_raw,
                    lead.body_normalized,
                    posting_type.value,
                    lead.location,
                    lead.remote,
                    lead.apply_url,
                    tags,
                    float(max(0.0, min(1.0, lead.confidence))),
                    Jsonb(metadata),
                    lead.source_key,
                    lead.external_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            persisted = _as_lead(row)
            _seed_job_lead_staging_source_fingerprint(cursor, persisted)
    return persisted.id


def existing_job_lead_external_ids(
    settings: SharedSettings,
    *,
    source_key: str,
    external_ids: list[str],
) -> set[str]:
    """Return stored source ids using one query for a scrape batch."""
    normalized_ids = sorted({value for value in external_ids if value})
    if not normalized_ids:
        return set()
    query = """
        SELECT external_id
        FROM job_leads
        WHERE source_key = %s
          AND external_id = ANY(%s)
          AND status IN ('pending', 'rejected')
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (source_key, normalized_ids))
            rows = cursor.fetchall()
    return {str(row["external_id"]) for row in rows}


def list_job_leads(
    settings: SharedSettings,
    *,
    status: JobLeadStatus | str | None = JobLeadStatus.PENDING,
    limit: int = 10,
) -> list[JobLead]:
    """List recent job leads for review."""
    conditions: list[str] = []
    params: list[Any] = []
    if status is not None:
        conditions.append("status = %s")
        params.append(_normalize_status(status).value)
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT job_leads.*, linked_engagement.id::text AS engagement_id
        FROM job_leads
        LEFT JOIN LATERAL (
            SELECT id
            FROM engagements
            WHERE discord_thread_id = job_leads.discord_thread_id
            ORDER BY created_at DESC
            LIMIT 1
        ) AS linked_engagement ON TRUE
        {where_clause}
        ORDER BY confidence DESC, source_posted_at DESC NULLS LAST, created_at DESC
        LIMIT %s
    """
    params.append(max(1, min(limit, 50)))
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), tuple(params))
            rows = cursor.fetchall()
    return [_as_lead(row) for row in rows]


def get_job_lead(settings: SharedSettings, lead_id: str) -> JobLead | None:
    """Load one lead by UUID or unambiguous UUID prefix."""
    raw = lead_id.strip()
    if not raw:
        return None
    query = """
        SELECT *
        FROM job_leads
        WHERE id::text = %s OR id::text LIKE %s
        ORDER BY created_at DESC
        LIMIT 2
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (raw, f"{raw}%"))
            rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    return _as_lead(rows[0])


def review_job_lead(
    settings: SharedSettings,
    *,
    lead_id: str,
    status: JobLeadStatus | str,
    reviewer_discord_user_id: str,
) -> JobLead | None:
    """Qualify a lead, reject a lead, or restore it to the pending queue."""
    try:
        normalized_status = (
            status
            if isinstance(status, JobLeadStatus)
            else JobLeadStatus(str(status).strip().casefold())
        )
    except ValueError:
        raise ValueError(
            "Job lead review status must be pending, approved, or rejected."
        ) from None
    if normalized_status not in {
        JobLeadStatus.PENDING,
        JobLeadStatus.APPROVED,
        JobLeadStatus.REJECTED,
    }:
        raise ValueError(
            "Job lead review status must be pending, approved, or rejected."
        )
    existing = get_job_lead(settings, lead_id)
    if existing is None:
        return None
    allowed_source_statuses = (
        [JobLeadStatus.REJECTED.value]
        if normalized_status is JobLeadStatus.PENDING
        else [JobLeadStatus.PENDING.value, JobLeadStatus.APPROVED.value]
    )
    reviewer = (
        None if normalized_status is JobLeadStatus.PENDING else reviewer_discord_user_id
    )
    query = """
        UPDATE job_leads
        SET
            status = %s,
            reviewed_by_discord_user_id = %s,
            reviewed_at = CASE WHEN %s = 'pending' THEN NULL ELSE NOW() END,
            staging_reservation_token = NULL,
            staging_reserved_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND status = ANY(%s)
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    normalized_status.value,
                    reviewer,
                    normalized_status.value,
                    existing.id,
                    allowed_source_statuses,
                ),
            )
            row = cursor.fetchone()
    return _as_lead(row) if row is not None else None


def mark_job_lead_staged(
    settings: SharedSettings,
    *,
    lead_id: str,
    reservation_token: str,
    source_fingerprint: str,
    guild_id: str,
    channel_id: str,
    thread_id: str,
) -> JobLead | None:
    """Finalize a reserved pending lead's unqualified Discord holding thread."""
    fingerprint = source_fingerprint.strip()
    if not fingerprint:
        raise ValueError("Job lead staging source fingerprint is required.")
    query = """
        UPDATE job_leads
        SET
            staged_discord_guild_id = %s,
            staged_discord_channel_id = %s,
            staged_discord_thread_id = %s,
            staged_at = NOW(),
            metadata = (metadata - %s::text) || jsonb_build_object(
                %s::text, %s::text
            ),
            staging_reservation_token = NULL,
            staging_reserved_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND status = 'pending'
          AND staged_discord_thread_id IS NULL
          AND staging_reservation_token = %s
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    guild_id,
                    channel_id,
                    thread_id,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_SOURCE_FINGERPRINT_METADATA_KEY,
                    fingerprint,
                    lead_id,
                    reservation_token,
                ),
            )
            row = cursor.fetchone()
    return _as_lead(row) if row is not None else None


def reserve_job_lead_staging(
    settings: SharedSettings,
    *,
    lead_id: str,
    reservation_token: str,
    guild_id: str,
    channel_id: str,
) -> JobLead | None:
    """Atomically claim a pending lead and retain a recovery block for Discord work."""
    token = reservation_token.strip()
    if not token:
        raise ValueError("Job lead staging reservation token is required.")
    normalized_guild_id = guild_id.strip()
    normalized_channel_id = channel_id.strip()
    if not normalized_guild_id or not normalized_channel_id:
        raise ValueError("Job lead staging reservation forum is required.")
    reservation_metadata = Jsonb(
        {
            _STAGING_CLEANUP_REQUIRED_METADATA_KEY: {
                "state": _STAGING_CLEANUP_RESERVATION_STATE,
                "guild_id": normalized_guild_id,
                "channel_id": normalized_channel_id,
                "thread_id": None,
            }
        }
    )
    query = """
        UPDATE job_leads
        SET
            metadata = metadata || %s,
            staging_reservation_token = %s,
            staging_reserved_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'pending'
          AND staged_discord_thread_id IS NULL
          AND metadata -> %s::text IS NULL
          AND (
              staging_reservation_token IS NULL
              OR staging_reserved_at IS NULL
              OR staging_reserved_at < NOW() - (%s * INTERVAL '1 second')
          )
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    reservation_metadata,
                    token,
                    lead_id,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    JOB_LEAD_STAGING_RESERVATION_TTL_SECONDS,
                ),
            )
            row = cursor.fetchone()
    return _as_lead(row) if row is not None else None


def release_job_lead_staging_reservation(
    settings: SharedSettings,
    *,
    lead_id: str,
    reservation_token: str,
) -> bool:
    """Release this attempt's staging reservation after no holding thread is saved."""
    token = reservation_token.strip()
    if not token:
        return False
    query = """
        UPDATE job_leads
        SET
            metadata = CASE
                WHEN (metadata -> %s::text) ->> 'state' = %s
                THEN metadata - %s::text
                ELSE metadata
            END,
            staging_reservation_token = NULL,
            staging_reserved_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND staged_discord_thread_id IS NULL
          AND (
              staging_reservation_token = %s
              OR (
                  staging_reservation_token IS NULL
                  AND (metadata -> %s::text) ->> 'state' = %s
              )
          )
        RETURNING id
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_CLEANUP_RESERVATION_STATE,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    lead_id,
                    token,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_CLEANUP_RESERVATION_STATE,
                ),
            )
            row = cursor.fetchone()
    return row is not None


def record_job_lead_staging_cleanup_required(
    settings: SharedSettings,
    *,
    lead_id: str,
    guild_id: str,
    channel_id: str,
    thread_id: str | None,
) -> bool:
    """Persist an orphaned holding thread for operator reconciliation."""
    recovery_metadata = Jsonb(
        {
            _STAGING_CLEANUP_REQUIRED_METADATA_KEY: {
                "state": _STAGING_CLEANUP_RECOVERY_STATE,
                "guild_id": guild_id.strip() or None,
                "channel_id": channel_id.strip() or None,
                "thread_id": thread_id.strip() if thread_id else None,
            }
        }
    )
    query = """
        UPDATE job_leads
        SET
            metadata = metadata || %s,
            updated_at = NOW()
        WHERE id = %s
        RETURNING id
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (recovery_metadata, lead_id))
            row = cursor.fetchone()
    return row is not None


def clear_job_lead_staging_cleanup_required(
    settings: SharedSettings,
    *,
    lead_id: str,
) -> JobLead | None:
    """Clear an operator-reconciled holding-thread cleanup block."""
    query = """
        UPDATE job_leads
        SET
            metadata = metadata - %s::text,
            staging_reservation_token = NULL,
            staging_reserved_at = NULL,
            updated_at = NOW()
        WHERE id = %s
          AND metadata ? %s::text
          AND (
              (metadata -> %s::text) ->> 'state' IS DISTINCT FROM %s
              OR (
                  (metadata -> %s::text) ->> 'state' = %s
                  AND (
                      staging_reservation_token IS NULL
                      OR staging_reserved_at IS NULL
                      OR staging_reserved_at < NOW() - (%s * INTERVAL '1 second')
                  )
              )
          )
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    lead_id,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_CLEANUP_RESERVATION_STATE,
                    _STAGING_CLEANUP_REQUIRED_METADATA_KEY,
                    _STAGING_CLEANUP_RESERVATION_STATE,
                    JOB_LEAD_STAGING_RESERVATION_TTL_SECONDS,
                ),
            )
            row = cursor.fetchone()
    return _as_lead(row) if row is not None else None


def mark_job_lead_posted(
    settings: SharedSettings,
    *,
    lead_id: str,
    reviewer_discord_user_id: str,
    guild_id: str,
    channel_id: str,
    thread_id: str,
) -> JobLead | None:
    """Mark an approved lead as published to Discord."""
    query = """
        UPDATE job_leads
        SET
            status = 'posted',
            reviewed_by_discord_user_id = COALESCE(reviewed_by_discord_user_id, %s),
            reviewed_at = COALESCE(reviewed_at, NOW()),
            discord_guild_id = %s,
            discord_channel_id = %s,
            discord_thread_id = %s,
            posted_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND status = 'approved'
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (reviewer_discord_user_id, guild_id, channel_id, thread_id, lead_id),
            )
            row = cursor.fetchone()
    return _as_lead(row) if row is not None else None
