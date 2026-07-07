"""Persistence helpers for sourced job leads awaiting review."""

from __future__ import annotations

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
    )


def upsert_job_lead(settings: SharedSettings, lead: JobLeadInput) -> tuple[str, bool]:
    """Create or update a sourced lead without changing human review state."""
    lead_id = str(uuid4())
    posting_type = normalize_job_posting_type(lead.posting_type)
    source_posted_at = _as_utc(lead.source_posted_at)
    tags = sorted({tag.strip().casefold() for tag in lead.tags or [] if tag.strip()})
    query = """
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
            apply_url = COALESCE(EXCLUDED.apply_url, job_leads.apply_url),
            tags = EXCLUDED.tags,
            confidence = GREATEST(job_leads.confidence, EXCLUDED.confidence),
            metadata = job_leads.metadata || EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id::text, (xmax = 0) AS inserted
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
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
                    Jsonb(lead.metadata or {}),
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Unable to upsert job lead.")
    return str(row["id"]), bool(row["inserted"])


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
        SELECT *
        FROM job_leads
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
    """Approve or reject a pending lead without publishing it."""
    normalized_status = _normalize_status(status)
    if normalized_status not in {JobLeadStatus.APPROVED, JobLeadStatus.REJECTED}:
        raise ValueError("Job lead review status must be approved or rejected.")
    existing = get_job_lead(settings, lead_id)
    if existing is None:
        return None
    query = """
        UPDATE job_leads
        SET
            status = %s,
            reviewed_by_discord_user_id = %s,
            reviewed_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
          AND status IN ('pending', 'approved')
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (normalized_status.value, reviewer_discord_user_id, existing.id),
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
