"""Onboarding volunteer registry, load suggestions, and reminder persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.rows import dict_row

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class VolunteerAvailability(StrEnum):
    AVAILABLE = "available"
    PAUSED = "paused"


ACTIVE_ONBOARDING_STATES = ("selected", "reachingout", "awaitingcontribution")
REMINDER_STATES = ("selected", "reachingout")


def normalize_onboarder_username(value: str | None) -> str | None:
    """Return the 508 username component used by CRM onboarding assignments."""
    text = str(value or "").strip().casefold()
    if not text:
        return None
    if "@" in text:
        local, domain = text.split("@", 1)
        if domain != "508.dev":
            return None
        text = local
    if not text or any(char.isspace() for char in text):
        return None
    return text


def validate_timezone(value: str) -> str:
    """Validate and preserve an IANA timezone identifier."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("timezone_required")
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("invalid_timezone") from exc
    return candidate


def list_onboarding_volunteers(settings: SharedSettings) -> list[dict[str, Any]]:
    """List volunteers with current load and latest-onboarder success counts."""
    query = """
        SELECT
            v.person_id::text AS person_id,
            p.crm_contact_id,
            p.name,
            p.email_508,
            p.discord_user_id,
            p.discord_username,
            v.username,
            v.timezone,
            v.availability,
            v.paused_until,
            v.max_active_assignments,
            v.last_assigned_at,
            COALESCE(active_assignments.count, 0)::int AS active_assignments,
            COALESCE(successful_onboardings.count, 0)::int AS successful_onboardings
        FROM onboarding_volunteers v
        JOIN people p ON p.id = v.person_id
        LEFT JOIN LATERAL (
            SELECT count(*) AS count
            FROM people candidate
            WHERE split_part(lower(btrim(coalesce(candidate.onboarder, ''))), '@', 1) = v.username
              AND replace(replace(replace(lower(btrim(coalesce(candidate.onboarding_state, ''))), '_', ''), '-', ''), ' ', '') = ANY(%s)
        ) active_assignments ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS count
            FROM people candidate
            WHERE split_part(lower(btrim(coalesce(candidate.onboarder, ''))), '@', 1) = v.username
              AND replace(replace(replace(lower(btrim(coalesce(candidate.onboarding_state, ''))), '_', ''), '-', ''), ' ', '') = 'onboarded'
        ) successful_onboardings ON true
        ORDER BY
            CASE WHEN v.availability = 'available' AND (v.paused_until IS NULL OR v.paused_until <= NOW()) THEN 0 ELSE 1 END,
            active_assignments.count ASC,
            v.last_assigned_at ASC NULLS FIRST,
            p.name ASC NULLS LAST
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (list(ACTIVE_ONBOARDING_STATES),))
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


def upsert_onboarding_volunteer(
    settings: SharedSettings,
    *,
    crm_contact_id: str,
    timezone_name: str,
    availability: VolunteerAvailability = VolunteerAvailability.AVAILABLE,
    paused_until: datetime | None = None,
    max_active_assignments: int | None = None,
) -> dict[str, Any]:
    """Add or update a linked 508 member in the willing-onboarder registry."""
    timezone_name = validate_timezone(timezone_name)
    if max_active_assignments is not None and max_active_assignments < 1:
        raise ValueError("invalid_max_active_assignments")
    query = """
        INSERT INTO onboarding_volunteers (
            person_id, username, timezone, availability, paused_until, max_active_assignments
        )
        SELECT
            p.id,
            split_part(lower(btrim(p.email_508)), '@', 1),
            %s, %s, %s, %s
        FROM people p
        WHERE p.crm_contact_id = %s
          AND p.email_508 ILIKE '%%@508.dev'
          AND p.discord_user_id IS NOT NULL
          AND btrim(p.discord_user_id) <> ''
        ON CONFLICT (person_id) DO UPDATE SET
            timezone = EXCLUDED.timezone,
            availability = EXCLUDED.availability,
            paused_until = EXCLUDED.paused_until,
            max_active_assignments = EXCLUDED.max_active_assignments,
            updated_at = NOW()
        RETURNING person_id::text, username, timezone, availability, paused_until, max_active_assignments
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    timezone_name,
                    availability.value,
                    paused_until,
                    max_active_assignments,
                    crm_contact_id,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise ValueError("volunteer_requires_linked_508_discord_member")
    return dict(row)


def mark_onboarder_assigned(settings: SharedSettings, username: str) -> None:
    """Record assignment recency for fair future suggestions."""
    normalized = normalize_onboarder_username(username)
    if not normalized:
        return
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE onboarding_volunteers SET last_assigned_at = NOW(), updated_at = NOW() WHERE username = %s",
                (normalized,),
            )


def onboarding_volunteer_is_available(settings: SharedSettings, username: str) -> bool:
    """Return whether a registry member can receive a new onboarding assignment."""
    normalized = normalize_onboarder_username(username)
    if not normalized:
        return False
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM onboarding_volunteers
                WHERE username = %s
                  AND availability = 'available'
                  AND (paused_until IS NULL OR paused_until <= NOW())
                LIMIT 1
                """,
                (normalized,),
            )
            return cursor.fetchone() is not None


def linked_member_for_discord_user(
    settings: SharedSettings,
    discord_user_id: str,
) -> dict[str, Any] | None:
    """Return the CRM identity for a linked member self-managing availability."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT crm_contact_id, timezone
                FROM people
                WHERE discord_user_id = %s AND is_member = true
                LIMIT 1
                """,
                (discord_user_id,),
            )
            row = cursor.fetchone()
    return dict(row) if row is not None else None


def suggested_onboarders(
    settings: SharedSettings,
    *,
    candidate_timezone: str | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return available volunteers ordered by capacity, timezone, and rotation."""
    now = datetime.now(timezone.utc)
    suggestions: list[dict[str, Any]] = []
    candidate_offset = _utc_offset_hours(candidate_timezone, now)
    for volunteer in list_onboarding_volunteers(settings):
        if volunteer["availability"] != VolunteerAvailability.AVAILABLE.value:
            continue
        paused_until = volunteer.get("paused_until")
        if isinstance(paused_until, datetime) and paused_until > now:
            continue
        max_active = volunteer.get("max_active_assignments")
        if (
            isinstance(max_active, int)
            and volunteer["active_assignments"] >= max_active
        ):
            continue
        volunteer_offset = _utc_offset_hours(volunteer.get("timezone"), now)
        timezone_distance = (
            abs(candidate_offset - volunteer_offset)
            if candidate_offset is not None and volunteer_offset is not None
            else 24
        )
        volunteer["timezone_distance_hours"] = timezone_distance
        suggestions.append(volunteer)
    suggestions.sort(
        key=lambda volunteer: (
            int(volunteer["active_assignments"]),
            int(volunteer["timezone_distance_hours"]),
            volunteer.get("last_assigned_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            str(volunteer.get("name") or ""),
        )
    )
    return suggestions[: max(1, min(limit, 10))]


def claim_due_onboarding_reminders(
    settings: SharedSettings,
    *,
    stale_days: int,
    repeat_days: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Atomically claim due reminder windows, retrying failed claims after one hour."""
    stale = max(1, stale_days)
    repeat = max(1, repeat_days)
    query = """
        WITH candidates AS (
            SELECT
                p.id,
                CASE
                    WHEN replace(replace(replace(lower(btrim(coalesce(p.onboarding_state, ''))), '_', ''), '-', ''), ' ', '') = 'selected'
                        AND lower(btrim(coalesce(p.onboarder, ''))) NOT IN ('', 'none', 'no discord')
                    THEN 'assigned'
                    ELSE replace(replace(replace(lower(btrim(coalesce(p.onboarding_state, ''))), '_', ''), '-', ''), ' ', '')
                END AS stage,
                COALESCE(p.onboarding_updated_at, p.created_at) AS activity_at,
                FLOOR(
                    EXTRACT(EPOCH FROM (NOW() - COALESCE(p.onboarding_updated_at, p.created_at)))
                    / (86400 * %s)
                )::int AS reminder_number
            FROM people p
            WHERE replace(replace(replace(lower(btrim(coalesce(p.onboarding_state, ''))), '_', ''), '-', ''), ' ', '') = ANY(%s)
              AND COALESCE(p.onboarding_updated_at, p.created_at) <= NOW() - make_interval(days => %s)
            ORDER BY COALESCE(p.onboarding_updated_at, p.created_at) ASC
            LIMIT %s
        ), claimed AS (
            INSERT INTO onboarding_reminder_deliveries (
                person_id, stage, activity_at, reminder_number, destination, claimed_at
            )
            SELECT
                id, stage, activity_at, reminder_number,
                CASE WHEN stage = 'selected' THEN 'volunteers_channel' ELSE 'onboarder_dm' END,
                NOW()
            FROM candidates
            ON CONFLICT (person_id, stage, activity_at, reminder_number) DO UPDATE
                SET claimed_at = NOW(), last_error = NULL
                WHERE onboarding_reminder_deliveries.sent_at IS NULL
                  AND onboarding_reminder_deliveries.claimed_at <= NOW() - interval '1 hour'
            RETURNING person_id, stage, activity_at, reminder_number, destination
        )
        SELECT
            c.person_id::text,
            c.stage,
            c.activity_at,
            c.reminder_number,
            c.destination,
            p.crm_contact_id,
            p.name,
            p.onboarder,
            p.address_city,
            p.address_state,
            p.address_country,
            p.professional_roles,
            p.seniority,
            p.timezone
        FROM claimed c
        JOIN people p ON p.id = c.person_id
        ORDER BY c.activity_at ASC
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query, (repeat, list(REMINDER_STATES), stale, max(1, min(limit, 500)))
            )
            return [dict(row) for row in cursor.fetchall()]


def mark_onboarding_reminder_sent(
    settings: SharedSettings,
    *,
    person_id: str,
    stage: str,
    activity_at: datetime,
    reminder_number: int,
    message_id: str,
) -> None:
    """Mark one claimed reminder window as successfully delivered."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE onboarding_reminder_deliveries
                SET sent_at = NOW(), message_id = %s, last_error = NULL
                WHERE person_id = %s AND stage = %s AND activity_at = %s AND reminder_number = %s
                """,
                (message_id, person_id, stage, activity_at, reminder_number),
            )


def mark_onboarding_reminder_failed(
    settings: SharedSettings,
    *,
    person_id: str,
    stage: str,
    activity_at: datetime,
    reminder_number: int,
    error: str,
) -> None:
    """Record a delivery failure so the claim becomes retryable after its lease."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE onboarding_reminder_deliveries
                SET last_error = %s
                WHERE person_id = %s AND stage = %s AND activity_at = %s AND reminder_number = %s
                """,
                (error[:500], person_id, stage, activity_at, reminder_number),
            )


def _utc_offset_hours(timezone_name: str | None, now: datetime) -> int | None:
    if not timezone_name:
        return None
    try:
        offset = now.astimezone(ZoneInfo(timezone_name)).utcoffset()
    except ZoneInfoNotFoundError:
        return None
    return int(offset.total_seconds() // 3600) if offset is not None else None
