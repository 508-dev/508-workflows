"""Local engagement persistence for Discord gig tracking."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.job_channels import normalize_job_posting_type
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class EngagementStatus(StrEnum):
    """Supported visible gig status states."""

    RECRUITING = "recruiting"
    FILLED = "filled"
    UNKNOWN = "unknown"
    LOST = "lost"
    OUTDATED = "outdated"


class EngagementApplicationStatus(StrEnum):
    """Supported candidate/applicant status states for a gig."""

    SUGGESTED = "suggested"
    INTERESTED = "interested"
    REVIEWING = "reviewing"
    CONTACTED = "contacted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class EngagementApplicationSource(StrEnum):
    """Where a gig application/interest signal came from."""

    MATCH_CANDIDATES = "match_candidates"
    DIRECT_INTEREST = "direct_interest"
    MANUAL_ADD = "manual_add"
    DISCORD = "discord"
    CRM = "crm"
    ERP = "erp"


_STATUS_ALIASES = {
    "recruiting": EngagementStatus.RECRUITING,
    "open": EngagementStatus.RECRUITING,
    "hiring": EngagementStatus.RECRUITING,
    "filled": EngagementStatus.FILLED,
    "staffed": EngagementStatus.FILLED,
    "closed": EngagementStatus.FILLED,
    "unknown": EngagementStatus.UNKNOWN,
    "lost": EngagementStatus.LOST,
    "cancelled": EngagementStatus.LOST,
    "canceled": EngagementStatus.LOST,
    "outdated": EngagementStatus.OUTDATED,
    "stale": EngagementStatus.OUTDATED,
}
_STATUS_TOKEN_RE = re.compile(r"^\s*[\[(]?\s*([A-Z][A-Z0-9 _-]{2,})\s*[\])]?\s*")
_BRACKETED_STATUS_RE = re.compile(r"^\s*[\[(]\s*([A-Z][A-Z0-9 _-]{2,})\s*[\])]\s*")


@dataclass(frozen=True)
class DiscordEngagementInput:
    """Discord-origin gig data used to create/update an engagement."""

    guild_id: str | None
    channel_id: str | None
    message_id: str
    thread_id: str | None
    posted_by_discord_user_id: str | None
    title: str
    channel_name: str | None = None
    posting_type: str = "part_time"
    body_raw: str | None = None
    body_normalized: str | None = None
    posted_at: datetime | None = None
    status: EngagementStatus = EngagementStatus.UNKNOWN
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    requirements: dict[str, Any] | None = None
    preserve_existing_status: bool = False
    refresh_activity: bool = True


def normalize_engagement_status(
    value: str | EngagementStatus | None,
) -> EngagementStatus:
    """Normalize user/Discord status text into the supported enum."""
    if isinstance(value, EngagementStatus):
        return value
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    normalized = normalized.strip("_")
    return _STATUS_ALIASES.get(normalized, EngagementStatus.UNKNOWN)


def parse_status_from_title(title: str | None) -> EngagementStatus:
    """Parse a visible status marker such as [RECRUITING] from a gig title."""
    raw = str(title or "").strip()
    if not raw:
        return EngagementStatus.UNKNOWN
    match = _BRACKETED_STATUS_RE.match(raw) or _STATUS_TOKEN_RE.match(raw)
    if not match:
        return EngagementStatus.UNKNOWN
    return normalize_engagement_status(match.group(1))


def strip_status_from_title(title: str | None) -> str:
    """Remove a leading visible status marker from a Discord gig title."""
    raw = str(title or "").strip()
    stripped = _BRACKETED_STATUS_RE.sub("", raw, count=1).strip()
    if stripped != raw:
        return stripped
    status = parse_status_from_title(raw)
    if status is EngagementStatus.UNKNOWN:
        return raw
    return _STATUS_TOKEN_RE.sub("", raw, count=1).strip() or raw


def status_label(status: str | EngagementStatus | None) -> str:
    """Return a short dashboard label for a gig status."""
    normalized = normalize_engagement_status(status)
    return normalized.value.replace("_", " ").title()


def requirements_to_payload(requirements: Any) -> dict[str, Any]:
    """Serialize a JobRequirements-like object for storage."""
    if requirements is None:
        return {}
    if is_dataclass(requirements):
        return asdict(cast(Any, requirements))
    if isinstance(requirements, dict):
        return dict(requirements)
    keys = (
        "title",
        "required_skills",
        "hard_required_skills",
        "soft_required_skills",
        "preferred_skills",
        "required_evidence",
        "required_languages",
        "discord_role_types",
        "seniority",
        "location_type",
        "preferred_timezones",
        "raw_location_text",
    )
    return {
        key: getattr(requirements, key) for key in keys if hasattr(requirements, key)
    }


def candidate_evaluation_payload(candidate: Any) -> dict[str, Any]:
    """Serialize match/evaluation fields from a CandidateMatch-like object."""
    fields = (
        "match_score",
        "llm_fit_score",
        "llm_summary",
        "llm_risks",
        "llm_missing_requirements",
        "matched_required_skills",
        "matched_hard_required_skills",
        "matched_soft_required_skills",
        "matched_preferred_skills",
        "matched_discord_roles",
        "missing_hard_required_skills",
        "evidence_signals",
        "seniority",
        "timezone",
        "address_country",
        "address_city",
        "address_state",
    )
    payload: dict[str, Any] = {}
    for field in fields:
        value = getattr(candidate, field, None)
        if value is not None:
            payload[field] = value
    return payload


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def upsert_discord_engagement(
    settings: SharedSettings,
    payload: DiscordEngagementInput,
) -> str:
    """Create or update a Discord-origin engagement and return its id."""
    engagement_id = str(uuid4())
    status = normalize_engagement_status(payload.status)
    posting_type = normalize_job_posting_type(payload.posting_type)
    required_skills = payload.required_skills or []
    preferred_skills = payload.preferred_skills or []
    requirements = payload.requirements or {}
    posted_at = _as_utc(payload.posted_at)
    query = """
        INSERT INTO engagements (
            id,
            lifecycle_stage,
            status,
            title,
            body_raw,
            body_normalized,
            required_skills,
            preferred_skills,
            requirements,
            discord_guild_id,
            discord_channel_id,
            discord_channel_name,
            posting_type,
            discord_message_id,
            discord_thread_id,
            posted_by_discord_user_id,
            posted_at,
            last_status_changed_at,
            last_activity_at
        ) VALUES (
            %s, 'pending_gig', %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s,
            COALESCE(%s, NOW()), COALESCE(%s, NOW())
        )
        ON CONFLICT (discord_message_id) DO UPDATE SET
            status = CASE
                WHEN %s OR EXCLUDED.status = 'unknown' THEN engagements.status
                ELSE EXCLUDED.status
            END,
            title = EXCLUDED.title,
            body_raw = COALESCE(EXCLUDED.body_raw, engagements.body_raw),
            body_normalized = COALESCE(
                EXCLUDED.body_normalized,
                engagements.body_normalized
            ),
            required_skills = CASE
                WHEN cardinality(EXCLUDED.required_skills) > 0 THEN EXCLUDED.required_skills
                ELSE engagements.required_skills
            END,
            preferred_skills = CASE
                WHEN cardinality(EXCLUDED.preferred_skills) > 0 THEN EXCLUDED.preferred_skills
                ELSE engagements.preferred_skills
            END,
            requirements = CASE
                WHEN EXCLUDED.requirements <> '{}'::jsonb THEN EXCLUDED.requirements
                ELSE engagements.requirements
            END,
            discord_guild_id = EXCLUDED.discord_guild_id,
            discord_channel_id = EXCLUDED.discord_channel_id,
            discord_channel_name = COALESCE(
                EXCLUDED.discord_channel_name,
                engagements.discord_channel_name
            ),
            posting_type = EXCLUDED.posting_type,
            discord_thread_id = EXCLUDED.discord_thread_id,
            posted_by_discord_user_id = COALESCE(
                engagements.posted_by_discord_user_id,
                EXCLUDED.posted_by_discord_user_id
            ),
            posted_at = COALESCE(engagements.posted_at, EXCLUDED.posted_at),
            last_activity_at = CASE
                WHEN %s THEN NOW()
                ELSE COALESCE(engagements.last_activity_at, EXCLUDED.last_activity_at)
            END,
            last_status_changed_at = CASE
                WHEN
                    NOT %s
                    AND EXCLUDED.status <> 'unknown'
                    AND engagements.status IS DISTINCT FROM EXCLUDED.status
                THEN NOW()
                ELSE engagements.last_status_changed_at
            END
        RETURNING id::text
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    engagement_id,
                    status.value,
                    payload.title.strip() or "Untitled gig",
                    payload.body_raw,
                    payload.body_normalized,
                    required_skills,
                    preferred_skills,
                    Jsonb(requirements),
                    payload.guild_id,
                    payload.channel_id,
                    payload.channel_name,
                    posting_type.value,
                    payload.message_id,
                    payload.thread_id,
                    payload.posted_by_discord_user_id,
                    posted_at,
                    posted_at,
                    posted_at,
                    payload.preserve_existing_status,
                    payload.refresh_activity,
                    payload.preserve_existing_status,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Unable to upsert Discord engagement.")
    return str(row["id"])


def add_engagement_event(
    settings: SharedSettings,
    *,
    engagement_id: str,
    event_type: str,
    actor_discord_user_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one engagement event."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO engagement_events (
                    id,
                    engagement_id,
                    event_type,
                    actor_discord_user_id,
                    payload
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    str(uuid4()),
                    engagement_id,
                    event_type,
                    actor_discord_user_id,
                    Jsonb(payload or {}),
                ),
            )


def upsert_suggested_applications(
    settings: SharedSettings,
    *,
    engagement_id: str,
    candidates: list[Any],
    source: str = EngagementApplicationSource.MATCH_CANDIDATES.value,
) -> int:
    """Persist candidate match results as suggested applications."""
    count = 0
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            for candidate in candidates:
                crm_contact_id = getattr(candidate, "crm_contact_id", None)
                discord_user_id = getattr(candidate, "discord_user_id", None)
                normalized_discord_user_id = (
                    str(discord_user_id) if discord_user_id else None
                )
                if not crm_contact_id and not normalized_discord_user_id:
                    continue
                cursor.execute(
                    """
                    SELECT id
                    FROM people
                    WHERE
                        (%s::text IS NOT NULL AND crm_contact_id = %s)
                        OR (%s::text IS NOT NULL AND discord_user_id = %s)
                    ORDER BY sync_status = 'active' DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (
                        crm_contact_id,
                        crm_contact_id,
                        str(discord_user_id) if discord_user_id else None,
                        str(discord_user_id) if discord_user_id else None,
                    ),
                )
                person = cursor.fetchone()
                person_id = person["id"] if person is not None else None
                match_score = getattr(candidate, "match_score", None)
                fit_score = getattr(candidate, "llm_fit_score", None)
                match_score_value = (
                    float(match_score)
                    if isinstance(match_score, (int, float))
                    else None
                )
                fit_score_value = (
                    float(fit_score) if isinstance(fit_score, (int, float)) else None
                )
                evaluation = Jsonb(candidate_evaluation_payload(candidate))
                cursor.execute(
                    """
                    UPDATE engagement_applications
                    SET
                        person_id = COALESCE(person_id, %s),
                        crm_contact_id = COALESCE(crm_contact_id, %s),
                        discord_user_id = COALESCE(discord_user_id, %s),
                        source = CASE
                            WHEN source = 'direct_interest' THEN source
                            ELSE %s
                        END,
                        match_score = %s,
                        fit_score = %s,
                        evaluation = evaluation || %s
                    WHERE engagement_id = %s
                      AND (
                        (%s::text IS NOT NULL AND crm_contact_id = %s)
                        OR (%s::text IS NOT NULL AND discord_user_id = %s)
                      )
                    RETURNING id
                    """,
                    (
                        person_id,
                        crm_contact_id,
                        normalized_discord_user_id,
                        source,
                        match_score_value,
                        fit_score_value,
                        evaluation,
                        engagement_id,
                        crm_contact_id,
                        crm_contact_id,
                        normalized_discord_user_id,
                        normalized_discord_user_id,
                    ),
                )
                if cursor.fetchone() is not None:
                    count += 1
                    continue
                if crm_contact_id:
                    conflict_clause = """
                    ON CONFLICT (engagement_id, crm_contact_id) DO UPDATE SET
                        person_id = COALESCE(
                            engagement_applications.person_id,
                            EXCLUDED.person_id
                        ),
                        discord_user_id = COALESCE(
                            engagement_applications.discord_user_id,
                            EXCLUDED.discord_user_id
                        ),
                        source = EXCLUDED.source,
                        match_score = EXCLUDED.match_score,
                        fit_score = EXCLUDED.fit_score,
                        evaluation = EXCLUDED.evaluation
                    """
                else:
                    conflict_clause = """
                    ON CONFLICT (engagement_id, discord_user_id) DO UPDATE SET
                        person_id = COALESCE(
                            engagement_applications.person_id,
                            EXCLUDED.person_id
                        ),
                        crm_contact_id = COALESCE(
                            engagement_applications.crm_contact_id,
                            EXCLUDED.crm_contact_id
                        ),
                        source = EXCLUDED.source,
                        match_score = EXCLUDED.match_score,
                        fit_score = EXCLUDED.fit_score,
                        evaluation = EXCLUDED.evaluation
                    """
                cursor.execute(
                    f"""
                    INSERT INTO engagement_applications (
                        id,
                        engagement_id,
                        person_id,
                        crm_contact_id,
                        discord_user_id,
                        status,
                        source,
                        match_score,
                        fit_score,
                        evaluation
                    ) VALUES (%s, %s, %s, %s, %s, 'suggested', %s, %s, %s, %s)
                    {conflict_clause}
                    RETURNING id
                    """,
                    (
                        str(uuid4()),
                        engagement_id,
                        person_id,
                        crm_contact_id,
                        normalized_discord_user_id,
                        source,
                        match_score_value,
                        fit_score_value,
                        evaluation,
                    ),
                )
                if cursor.fetchone() is not None:
                    count += 1
    return count


def upsert_discord_interest_application(
    settings: SharedSettings,
    *,
    engagement_id: str,
    discord_user_id: str,
    discord_username: str | None,
    source: str = EngagementApplicationSource.DIRECT_INTEREST.value,
    message_id: str | None = None,
    message_content: str | None = None,
) -> str | None:
    """Record a Discord user as interested in a gig."""
    normalized_user_id = str(discord_user_id or "").strip()
    if not normalized_user_id:
        return None

    application_id = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id, crm_contact_id
                FROM people
                WHERE discord_user_id = %s
                ORDER BY sync_status = 'active' DESC, updated_at DESC
                LIMIT 1
                """,
                (normalized_user_id,),
            )
            person = cursor.fetchone()
            person_id = person["id"] if person is not None else None
            crm_contact_id = person["crm_contact_id"] if person is not None else None
            cursor.execute(
                """
                UPDATE engagement_applications
                SET
                    person_id = COALESCE(person_id, %s),
                    crm_contact_id = COALESCE(crm_contact_id, %s),
                    discord_user_id = COALESCE(discord_user_id, %s),
                    status = CASE
                        WHEN status = 'suggested' THEN 'interested'
                        ELSE status
                    END,
                    source = %s,
                    evaluation = evaluation || %s
                WHERE engagement_id = %s
                  AND (
                    discord_user_id = %s
                    OR (%s::text IS NOT NULL AND crm_contact_id = %s)
                  )
                RETURNING id::text
                """,
                (
                    person_id,
                    crm_contact_id,
                    normalized_user_id,
                    source,
                    Jsonb(
                        {
                            "discord_username": discord_username,
                            "interest_message_id": message_id,
                            "interest_message_content": message_content,
                        }
                    ),
                    engagement_id,
                    normalized_user_id,
                    crm_contact_id,
                    crm_contact_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO engagement_applications (
                        id,
                        engagement_id,
                        person_id,
                        crm_contact_id,
                        discord_user_id,
                        status,
                        source,
                        evaluation
                    ) VALUES (%s, %s, %s, %s, %s, 'interested', %s, %s)
                    ON CONFLICT (engagement_id, discord_user_id) DO UPDATE SET
                        person_id = COALESCE(
                            engagement_applications.person_id,
                            EXCLUDED.person_id
                        ),
                        crm_contact_id = COALESCE(
                            engagement_applications.crm_contact_id,
                            EXCLUDED.crm_contact_id
                        ),
                        status = CASE
                            WHEN engagement_applications.status = 'suggested'
                            THEN 'interested'
                            ELSE engagement_applications.status
                        END,
                        source = EXCLUDED.source,
                        evaluation = engagement_applications.evaluation || EXCLUDED.evaluation
                    RETURNING id::text
                    """,
                    (
                        application_id,
                        engagement_id,
                        person_id,
                        crm_contact_id,
                        normalized_user_id,
                        source,
                        Jsonb(
                            {
                                "discord_username": discord_username,
                                "interest_message_id": message_id,
                                "interest_message_content": message_content,
                            }
                        ),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
            cursor.execute(
                """
                UPDATE engagements
                SET last_activity_at = NOW()
                WHERE id = %s
                """,
                (engagement_id,),
            )
            cursor.execute(
                """
                INSERT INTO engagement_events (
                    id,
                    engagement_id,
                    event_type,
                    actor_discord_user_id,
                    payload
                ) VALUES (%s, %s, 'direct_interest_detected', %s, %s)
                """,
                (
                    str(uuid4()),
                    engagement_id,
                    normalized_user_id,
                    Jsonb(
                        {
                            "application_id": row["id"],
                            "source": source,
                            "message_id": message_id,
                        }
                    ),
                ),
            )
    return str(row["id"])


def list_dashboard_engagements(
    settings: SharedSettings,
    *,
    viewer_discord_user_id: str | None,
    include_all: bool,
    status: EngagementStatus | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return dashboard-visible gigs with nested application summaries."""
    params: list[Any] = []
    conditions = ["e.lifecycle_stage = 'pending_gig'"]
    if not include_all:
        conditions.append("e.posted_by_discord_user_id = %s")
        params.append(viewer_discord_user_id or "")
    if status is not None:
        conditions.append("e.status = %s")
        params.append(status.value)
    params.append(max(1, min(limit, 100)))
    sql = f"""
        SELECT
            e.id::text,
            e.lifecycle_stage,
            e.status,
            e.title,
            e.body_raw,
            e.required_skills,
            e.preferred_skills,
            e.requirements,
            e.discord_guild_id,
            e.discord_channel_id,
            e.discord_channel_name,
            e.posting_type,
            e.discord_message_id,
            e.discord_thread_id,
            e.posted_by_discord_user_id,
            e.posted_at,
            e.last_status_changed_at,
            e.last_activity_at,
            e.created_at,
            e.updated_at,
            COUNT(a.id)::int AS application_count,
            COUNT(a.id) FILTER (WHERE a.status = 'interested')::int AS interested_count,
            COALESCE(
                jsonb_agg(
                    jsonb_build_object(
                        'id', a.id::text,
                        'status', a.status,
                        'source', a.source,
                        'match_score', a.match_score,
                        'fit_score', a.fit_score,
                        'evaluation', a.evaluation,
                        'crm_contact_id', COALESCE(a.crm_contact_id, p.crm_contact_id),
                        'discord_user_id', COALESCE(a.discord_user_id, p.discord_user_id),
                        'name', p.name,
                        'email_508', p.email_508,
                        'discord_username', p.discord_username,
                        'latest_resume_id', p.latest_resume_id,
                        'latest_resume_name', p.latest_resume_name,
                        'skills_count', COALESCE(cardinality(p.skills), 0),
                        'is_member', p.is_member
                    )
                    ORDER BY
                        CASE a.status
                            WHEN 'interested' THEN 0
                            WHEN 'accepted' THEN 1
                            WHEN 'contacted' THEN 2
                            WHEN 'reviewing' THEN 3
                            WHEN 'suggested' THEN 4
                            ELSE 5
                        END,
                        COALESCE(a.fit_score, a.match_score, 0) DESC,
                        a.created_at ASC
                ) FILTER (WHERE a.id IS NOT NULL),
                '[]'::jsonb
            ) AS applications
        FROM engagements e
        LEFT JOIN engagement_applications a ON a.engagement_id = e.id
        LEFT JOIN people p
            ON p.id = a.person_id
            OR (
                a.person_id IS NULL
                AND a.crm_contact_id IS NOT NULL
                AND p.crm_contact_id = a.crm_contact_id
            )
        WHERE {" AND ".join(conditions)}
        GROUP BY e.id
        ORDER BY e.last_activity_at DESC NULLS LAST, e.created_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    return [_shape_engagement_row(row) for row in rows]


def list_dashboard_notifications(
    settings: SharedSettings,
    *,
    viewer_discord_user_id: str | None,
    include_all: bool,
    stale_days: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return dashboard notification items visible to one viewer."""
    days = max(1, stale_days)
    params: list[Any] = [days]
    conditions = [
        "e.lifecycle_stage = 'pending_gig'",
        "e.status = 'recruiting'",
        """
        GREATEST(
            COALESCE(e.last_activity_at, '-infinity'::timestamptz),
            COALESCE(e.last_status_changed_at, '-infinity'::timestamptz),
            COALESCE(e.posted_at, '-infinity'::timestamptz),
            e.created_at
        ) <= NOW() - make_interval(days => %s)
        """,
    ]
    if not include_all:
        conditions.append("e.posted_by_discord_user_id = %s")
        params.append(viewer_discord_user_id or "")
    params.append(max(1, min(limit, 50)))
    sql = f"""
        SELECT
            e.id::text,
            e.title,
            e.status,
            e.discord_thread_id,
            e.posted_by_discord_user_id,
            e.posted_at,
            e.last_status_changed_at,
            e.last_activity_at,
            e.last_recruiting_reminder_at,
            FLOOR(
                EXTRACT(
                    EPOCH FROM (
                        NOW() - GREATEST(
                            COALESCE(e.last_activity_at, '-infinity'::timestamptz),
                            COALESCE(
                                e.last_status_changed_at,
                                '-infinity'::timestamptz
                            ),
                            COALESCE(e.posted_at, '-infinity'::timestamptz),
                            e.created_at
                        )
                    )
                ) / 86400
            )::int AS age_days
        FROM engagements e
        WHERE {" AND ".join(conditions)}
        ORDER BY age_days DESC, e.created_at ASC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    return [_shape_stale_recruiting_notification(row, days) for row in rows]


def list_due_recruiting_reminders(
    settings: SharedSettings,
    *,
    stale_days: int,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return recruiting gig threads that need a Discord status reminder."""
    days = max(1, stale_days)
    sql = """
        SELECT
            e.id::text,
            e.title,
            e.discord_guild_id,
            e.discord_channel_id,
            e.discord_thread_id,
            e.posted_by_discord_user_id,
            e.last_recruiting_reminder_at,
            FLOOR(
                EXTRACT(
                    EPOCH FROM (
                        NOW() - GREATEST(
                            COALESCE(e.last_activity_at, '-infinity'::timestamptz),
                            COALESCE(
                                e.last_status_changed_at,
                                '-infinity'::timestamptz
                            ),
                            COALESCE(e.posted_at, '-infinity'::timestamptz),
                            e.created_at
                        )
                    )
                ) / 86400
            )::int AS age_days
        FROM engagements e
        WHERE e.lifecycle_stage = 'pending_gig'
          AND e.status = 'recruiting'
          AND e.discord_thread_id IS NOT NULL
          AND e.posted_by_discord_user_id IS NOT NULL
          AND GREATEST(
                COALESCE(e.last_activity_at, '-infinity'::timestamptz),
                COALESCE(e.last_status_changed_at, '-infinity'::timestamptz),
                COALESCE(e.posted_at, '-infinity'::timestamptz),
                e.created_at
              ) <= NOW() - make_interval(days => %s)
          AND (
                e.last_recruiting_reminder_at IS NULL
                OR e.last_recruiting_reminder_at <= NOW() - make_interval(days => %s)
              )
        ORDER BY e.last_recruiting_reminder_at ASC NULLS FIRST, e.created_at ASC
        LIMIT %s
    """
    params = (days, days, max(1, min(limit, 100)))
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    return [_shape_reminder_row(row) for row in rows]


def mark_recruiting_reminder_sent(
    settings: SharedSettings,
    *,
    engagement_id: str,
    message_id: str,
) -> None:
    """Record that the bot sent a recruiting status reminder."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE engagements
                SET last_recruiting_reminder_at = NOW()
                WHERE id = %s AND lifecycle_stage = 'pending_gig'
                """,
                (engagement_id,),
            )
            cursor.execute(
                """
                INSERT INTO engagement_events (
                    id,
                    engagement_id,
                    event_type,
                    payload
                ) VALUES (%s, %s, 'recruiting_reminder_sent', %s)
                """,
                (
                    str(uuid4()),
                    engagement_id,
                    Jsonb({"message_id": message_id}),
                ),
            )


def _shape_engagement_row(row: dict[str, Any]) -> dict[str, Any]:
    shaped = dict(row)
    for key in (
        "posted_at",
        "last_status_changed_at",
        "last_activity_at",
        "last_recruiting_reminder_at",
        "created_at",
        "updated_at",
    ):
        value = row.get(key)
        shaped[key] = value.isoformat() if isinstance(value, datetime) else None
    shaped["status_label"] = status_label(row.get("status"))
    shaped["applications"] = row.get("applications") or []
    return shaped


def _shape_stale_recruiting_notification(
    row: dict[str, Any],
    stale_days: int,
) -> dict[str, Any]:
    title = str(row.get("title") or "Untitled gig")
    age_days = int(row.get("age_days") or stale_days)
    shaped = _shape_reminder_row(row)
    return {
        "id": f"stale-recruiting:{row.get('id')}",
        "type": "stale_recruiting_gig",
        "severity": "warning",
        "title": "Recruiting gig needs an update",
        "message": f"{title} has had no updates for {age_days} day(s).",
        "engagement_id": row.get("id"),
        "gig_title": title,
        "age_days": age_days,
        "discord_thread_id": row.get("discord_thread_id"),
        "posted_by_discord_user_id": row.get("posted_by_discord_user_id"),
        "posted_at": shaped.get("posted_at"),
        "last_status_changed_at": shaped.get("last_status_changed_at"),
        "last_activity_at": shaped.get("last_activity_at"),
        "last_recruiting_reminder_at": shaped.get("last_recruiting_reminder_at"),
    }


def _shape_reminder_row(row: dict[str, Any]) -> dict[str, Any]:
    shaped = dict(row)
    for key in (
        "posted_at",
        "last_status_changed_at",
        "last_activity_at",
        "last_recruiting_reminder_at",
    ):
        value = row.get(key)
        shaped[key] = value.isoformat() if isinstance(value, datetime) else None
    return shaped


def viewer_can_update_engagement(
    settings: SharedSettings,
    *,
    engagement_id: str,
    viewer_discord_user_id: str | None,
    include_all: bool,
) -> bool:
    """Return whether a viewer may mutate one engagement."""
    if not include_all and not viewer_discord_user_id:
        return False
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM engagements
                WHERE id = %s
                  AND lifecycle_stage = 'pending_gig'
                  AND (
                    %s
                    OR posted_by_discord_user_id = %s
                  )
                LIMIT 1
                """,
                (engagement_id, include_all, viewer_discord_user_id),
            )
            return cursor.fetchone() is not None


def update_engagement_status(
    settings: SharedSettings,
    *,
    engagement_id: str,
    status: EngagementStatus,
    actor_discord_user_id: str | None,
) -> dict[str, Any] | None:
    """Update one engagement status and append an event."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE engagements
                SET
                    status = %s,
                    last_status_changed_at = CASE
                        WHEN status IS DISTINCT FROM %s THEN NOW()
                        ELSE last_status_changed_at
                    END,
                    last_activity_at = NOW()
                WHERE id = %s AND lifecycle_stage = 'pending_gig'
                RETURNING id::text, status, title, discord_thread_id, updated_at
                """,
                (status.value, status.value, engagement_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                INSERT INTO engagement_events (
                    id,
                    engagement_id,
                    event_type,
                    actor_discord_user_id,
                    payload
                ) VALUES (%s, %s, 'status_changed', %s, %s)
                """,
                (
                    str(uuid4()),
                    engagement_id,
                    actor_discord_user_id,
                    Jsonb({"status": status.value}),
                ),
            )
    result = dict(row)
    result["updated_at"] = (
        row["updated_at"].isoformat()
        if isinstance(row.get("updated_at"), datetime)
        else None
    )
    result["status_label"] = status_label(status)
    return result


def update_engagement_application_status(
    settings: SharedSettings,
    *,
    engagement_id: str,
    application_id: str,
    status: EngagementApplicationStatus,
    actor_discord_user_id: str | None,
) -> dict[str, Any] | None:
    """Update one application status and append an event."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE engagement_applications
                SET status = %s
                WHERE id = %s AND engagement_id = %s
                  AND EXISTS (
                    SELECT 1
                    FROM engagements
                    WHERE engagements.id = engagement_applications.engagement_id
                      AND engagements.lifecycle_stage = 'pending_gig'
                  )
                RETURNING id::text, engagement_id::text, status, updated_at
                """,
                (status.value, application_id, engagement_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                """
                UPDATE engagements
                SET last_activity_at = NOW()
                WHERE id = %s
                """,
                (row["engagement_id"],),
            )
            cursor.execute(
                """
                INSERT INTO engagement_events (
                    id,
                    engagement_id,
                    event_type,
                    actor_discord_user_id,
                    payload
                ) VALUES (%s, %s, 'application_status_changed', %s, %s)
                """,
                (
                    str(uuid4()),
                    row["engagement_id"],
                    actor_discord_user_id,
                    Jsonb({"application_id": application_id, "status": status.value}),
                ),
            )
    result = dict(row)
    result["updated_at"] = (
        row["updated_at"].isoformat()
        if isinstance(row.get("updated_at"), datetime)
        else None
    )
    return result
