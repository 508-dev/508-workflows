"""Project cache, roster, and wiki-match helpers."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.clients.erpnext import ERPNextAPIError, ERPNextClient
from five08.queue import get_postgres_connection, trusted_sql
from five08.settings import SharedSettings
from five08.tls import default_ca_bundle_path

PROJECT_SOURCE_ERPNEXT = "erpnext"
PROJECT_SOURCE_MANUAL = "manual"
PROJECT_ROSTER_KIND_ERP_USERS = "erp_users"
PROJECT_ROSTER_KIND_HISTORICAL = "historical"
DEFAULT_WIKI_PROJECT_DOC_ID = "9hJOnWkafL"
PROJECT_WIKI_MATCH_CONFIRMED = "confirmed"
PROJECT_WIKI_MATCH_NO_ROW = "no_row"
PROJECT_WIKI_MATCH_STATUSES = frozenset(
    {PROJECT_WIKI_MATCH_CONFIRMED, PROJECT_WIKI_MATCH_NO_ROW}
)
_SUPPLIER_EMAIL_CACHE_TTL_SECONDS = 600
_SUPPLIER_EMAIL_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}


@dataclass(frozen=True)
class ProjectRosterMemberInput:
    """One source-provided project roster member."""

    source_user_id: str
    email: str | None = None
    full_name: str | None = None
    source_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProjectInput:
    """Source-normalized project data for local cache upserts."""

    source: str
    external_id: str
    display_name: str
    customer: str | None = None
    source_status: str | None = None
    project_type: str | None = None
    priority: str | None = None
    percent_complete: float | None = None
    expected_start_date: date | None = None
    expected_end_date: date | None = None
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    source_modified_at: datetime | None = None
    source_payload: dict[str, Any] | None = None
    roster_members: list[ProjectRosterMemberInput] | None = None


def text_or_none(value: Any) -> str | None:
    """Normalize optional text fields."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_source_date(value: Any) -> date | None:
    """Parse a Frappe date-like value."""
    text = text_or_none(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def parse_source_datetime(value: Any) -> datetime | None:
    """Parse a Frappe datetime-like value as UTC when timezone is absent."""
    text = text_or_none(value)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def erpnext_project_to_input(project: dict[str, Any]) -> ProjectInput | None:
    """Convert one ERPNext Project detail payload into cache input."""
    external_id = text_or_none(project.get("name"))
    display_name = text_or_none(project.get("project_name")) or external_id
    if external_id is None or display_name is None:
        return None

    roster_members: list[ProjectRosterMemberInput] = []
    raw_users = project.get("users")
    if isinstance(raw_users, list):
        for raw_user in raw_users:
            if not isinstance(raw_user, dict):
                continue
            source_user_id = (
                text_or_none(raw_user.get("user"))
                or text_or_none(raw_user.get("email"))
                or text_or_none(raw_user.get("name"))
            )
            if source_user_id is None:
                continue
            roster_members.append(
                ProjectRosterMemberInput(
                    source_user_id=source_user_id,
                    email=text_or_none(raw_user.get("email")),
                    full_name=text_or_none(raw_user.get("full_name")),
                    source_payload=raw_user,
                )
            )

    percent_complete: float | None = None
    raw_percent = project.get("percent_complete")
    if isinstance(raw_percent, (int, float)):
        percent_complete = float(raw_percent)

    return ProjectInput(
        source=PROJECT_SOURCE_ERPNEXT,
        external_id=external_id,
        display_name=display_name,
        customer=text_or_none(project.get("customer")),
        source_status=text_or_none(project.get("status")),
        project_type=text_or_none(project.get("project_type")),
        priority=text_or_none(project.get("priority")),
        percent_complete=percent_complete,
        expected_start_date=parse_source_date(project.get("expected_start_date")),
        expected_end_date=parse_source_date(project.get("expected_end_date")),
        actual_start_date=parse_source_date(project.get("actual_start_date")),
        actual_end_date=parse_source_date(project.get("actual_end_date")),
        source_modified_at=parse_source_datetime(project.get("modified")),
        source_payload=project,
        roster_members=roster_members,
    )


def upsert_project(settings: SharedSettings, payload: ProjectInput) -> str:
    """Upsert one source project and its current roster, returning local id."""
    roster_members = payload.roster_members or []
    seen_source_user_ids: set[str] = set()

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            project_id = _resolve_or_create_project_id(cursor, payload)
            cursor.execute(
                """
                UPDATE projects
                SET
                    display_name = %s,
                    customer = %s,
                    source_status = %s,
                    project_type = %s,
                    priority = %s,
                    percent_complete = %s,
                    expected_start_date = %s,
                    expected_end_date = %s,
                    actual_start_date = %s,
                    actual_end_date = %s,
                    source_modified_at = %s,
                    source_payload = %s,
                    last_synced_at = NOW()
                WHERE id = %s
                """,
                (
                    payload.display_name,
                    payload.customer,
                    payload.source_status,
                    payload.project_type,
                    payload.priority,
                    payload.percent_complete,
                    payload.expected_start_date,
                    payload.expected_end_date,
                    payload.actual_start_date,
                    payload.actual_end_date,
                    payload.source_modified_at,
                    Jsonb(payload.source_payload or {}),
                    project_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO project_external_ids (
                    id,
                    project_id,
                    source,
                    external_id,
                    active,
                    last_seen_at
                ) VALUES (%s, %s, %s, %s, TRUE, NOW())
                ON CONFLICT (source, external_id) DO UPDATE SET
                    project_id = EXCLUDED.project_id,
                    active = TRUE,
                    last_seen_at = NOW()
                """,
                (str(uuid4()), project_id, payload.source, payload.external_id),
            )

            for member in roster_members:
                seen_source_user_ids.add(member.source_user_id)
                cursor.execute(
                    """
                    INSERT INTO project_roster_members (
                        id,
                        project_id,
                        source,
                        source_user_id,
                        email,
                        full_name,
                        roster_kind,
                        source_payload,
                        last_seen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (project_id, source, source_user_id) DO UPDATE SET
                        email = EXCLUDED.email,
                        full_name = EXCLUDED.full_name,
                        roster_kind = EXCLUDED.roster_kind,
                        source_payload = EXCLUDED.source_payload,
                        last_seen_at = NOW()
                    """,
                    (
                        str(uuid4()),
                        project_id,
                        payload.source,
                        member.source_user_id,
                        member.email,
                        member.full_name,
                        PROJECT_ROSTER_KIND_ERP_USERS,
                        Jsonb(member.source_payload or {}),
                    ),
                )

            if seen_source_user_ids:
                cursor.execute(
                    """
                    DELETE FROM project_roster_members
                    WHERE project_id = %s
                      AND source = %s
                      AND source_user_id <> ALL(%s)
                    """,
                    (project_id, payload.source, list(seen_source_user_ids)),
                )
            else:
                cursor.execute(
                    """
                    DELETE FROM project_roster_members
                    WHERE project_id = %s
                      AND source = %s
                    """,
                    (project_id, payload.source),
                )
    return project_id


def _resolve_or_create_project_id(
    cursor: Any,
    payload: ProjectInput,
) -> str:
    lock_key = f"project:{payload.source}:{payload.external_id}"
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
    cursor.execute(
        """
        SELECT project_id::text
        FROM project_external_ids
        WHERE source = %s
          AND external_id = %s
          AND active IS TRUE
        LIMIT 1
        """,
        (payload.source, payload.external_id),
    )
    row = cursor.fetchone()
    if row is not None:
        return str(row["project_id"])

    project_id = str(uuid4())
    cursor.execute(
        """
        INSERT INTO projects (
            id,
            display_name,
            customer,
            source_status,
            project_type,
            priority,
            percent_complete,
            expected_start_date,
            expected_end_date,
            actual_start_date,
            actual_end_date,
            source_modified_at,
            source_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            project_id,
            payload.display_name,
            payload.customer,
            payload.source_status,
            payload.project_type,
            payload.priority,
            payload.percent_complete,
            payload.expected_start_date,
            payload.expected_end_date,
            payload.actual_start_date,
            payload.actual_end_date,
            payload.source_modified_at,
            Jsonb(payload.source_payload or {}),
        ),
    )
    return project_id


def list_dashboard_projects(
    settings: SharedSettings,
    *,
    project_id: str | None = None,
    query: str | None = None,
    status: str | None = None,
    viewer_emails: list[str] | None = None,
    include_all: bool = False,
    limit: int = 100,
    include_roster: bool = True,
) -> list[dict[str, Any]]:
    """Return project cache rows shaped for the operations dashboard.

    Set include_roster=False to skip the roster-members query — useful for
    lightweight access checks where only project IDs are needed.
    """
    where = []
    params: list[Any] = []
    normalized_query = (query or "").strip()
    normalized_status = (status or "").strip()
    normalized_project_id = text_or_none(project_id)
    if normalized_project_id:
        where.append("p.id = %s::uuid")
        params.append(normalized_project_id)
    if normalized_query:
        where.append(
            """
            (
                p.display_name ILIKE %s
                OR p.customer ILIKE %s
                OR pei.external_id ILIKE %s
            )
            """
        )
        token = f"%{normalized_query}%"
        params.extend([token, token, token])
    if normalized_status:
        where.append("LOWER(COALESCE(p.source_status, '')) = LOWER(%s)")
        params.append(normalized_status)
    normalized_viewer_emails = sorted(
        {
            normalized_email.casefold()
            for email in (viewer_emails or [])
            if (normalized_email := text_or_none(email)) is not None
        }
    )
    if not include_all:
        if not normalized_viewer_emails:
            return []
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM project_roster_members visible_prm
                WHERE visible_prm.project_id = p.id
                  AND visible_prm.source = 'erpnext'
                  AND visible_prm.roster_kind = 'erp_users'
                  AND (
                    LOWER(visible_prm.email) = ANY(%s)
                    OR LOWER(visible_prm.source_user_id) = ANY(%s)
                  )
            )
            """
        )
        params.extend([normalized_viewer_emails, normalized_viewer_emails])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(limit, 500)))
    query = f"""
        SELECT
            p.id::text,
            p.display_name,
            p.customer,
            p.source_status,
            p.project_type,
            p.priority,
            p.percent_complete,
            p.expected_start_date,
            p.expected_end_date,
            p.actual_start_date,
            p.actual_end_date,
            p.source_modified_at,
            p.last_synced_at,
            pei.external_id AS erpnext_project_id,
            COALESCE(ec.linked_engagement_count, 0) AS linked_engagement_count
        FROM projects p
        LEFT JOIN project_external_ids pei
          ON pei.project_id = p.id
         AND pei.source = 'erpnext'
         AND pei.active IS TRUE
        LEFT JOIN (
            SELECT erpnext_project_id, COUNT(*)::int AS linked_engagement_count
            FROM engagements
            WHERE erpnext_project_id IS NOT NULL
            GROUP BY erpnext_project_id
        ) ec
          ON ec.erpnext_project_id = pei.external_id
        {where_sql}
        ORDER BY
            LOWER(COALESCE(p.source_status, '')) = 'open' DESC,
            p.source_modified_at DESC NULLS LAST,
            LOWER(p.display_name) ASC
        LIMIT %s
        """

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), params)
            project_rows = [dict(row) for row in cursor.fetchall()]
            project_ids = [row["id"] for row in project_rows]
            members_by_project: dict[str, list[dict[str, Any]]] = {
                project_id: [] for project_id in project_ids
            }
            if include_roster and project_ids:
                cursor.execute(
                    """
                    SELECT
                        prm.project_id::text,
                        prm.source,
                        prm.source_user_id,
                        prm.email,
                        prm.full_name,
                        prm.roster_kind,
                        prm.source_payload,
                        prm.last_seen_at,
                        COALESCE(
                            matched_person.crm_contact_id,
                            prm.source_payload->>'crm_contact_id'
                        ) AS crm_contact_id,
                        matched_person.email AS crm_email,
                        matched_person.email_508 AS crm_email_508
                    FROM project_roster_members prm
                    LEFT JOIN LATERAL (
                        SELECT p.crm_contact_id, p.email, p.email_508
                        FROM people p
                        WHERE (
                            NULLIF(LOWER(COALESCE(prm.email, '')), '') IS NOT NULL
                            AND (
                                LOWER(COALESCE(p.email, '')) = LOWER(prm.email)
                                OR LOWER(COALESCE(p.email_508, '')) = LOWER(prm.email)
                            )
                        )
                        OR (
                            NULLIF(LOWER(COALESCE(prm.source_user_id, '')), '') IS NOT NULL
                            AND (
                                LOWER(COALESCE(p.email, '')) = LOWER(prm.source_user_id)
                                OR LOWER(COALESCE(p.email_508, '')) = LOWER(prm.source_user_id)
                            )
                        )
                        ORDER BY p.updated_at DESC NULLS LAST
                        LIMIT 1
                    ) matched_person ON TRUE
                    WHERE prm.project_id = ANY(%s::uuid[])
                    ORDER BY LOWER(COALESCE(prm.full_name, prm.email, prm.source_user_id))
                    """,
                    (project_ids,),
                )
                for member in cursor.fetchall():
                    item = dict(member)
                    project_id = str(item.pop("project_id"))
                    shaped_member = _serialize_row(item)
                    source_payload = shaped_member.get("source_payload")
                    if not isinstance(source_payload, dict):
                        source_payload = {}
                    shaped_member["erpnext_user_url"] = _erpnext_record_url(
                        settings,
                        "user",
                        source_payload.get("erpnext_user_id")
                        or shaped_member.get("source_user_id")
                        or shaped_member.get("email"),
                    )
                    supplier_id = source_payload.get("supplier_erpnext_id")
                    shaped_member["supplier_erpnext_url"] = _erpnext_record_url(
                        settings,
                        "supplier",
                        supplier_id,
                    )
                    shaped_member.pop("crm_email", None)
                    shaped_member.pop("crm_email_508", None)
                    shaped_member.pop("source_payload", None)
                    members_by_project.setdefault(project_id, []).append(shaped_member)

    result: list[dict[str, Any]] = []
    for row in project_rows:
        project_id = str(row["id"])
        shaped = _serialize_row(row)
        shaped["erpnext_project_url"] = _erpnext_record_url(
            settings,
            "project",
            shaped.get("erpnext_project_id"),
        )
        shaped["customer_erpnext_url"] = _erpnext_record_url(
            settings,
            "customer",
            shaped.get("customer"),
        )
        shaped["roster_members"] = (
            members_by_project.get(project_id, []) if include_roster else []
        )
        shaped["roster_count"] = len(shaped["roster_members"])
        result.append(shaped)
    return result


def _erpnext_record_url(
    settings: SharedSettings,
    doctype: str,
    record_id: Any,
) -> str | None:
    base_url = text_or_none(settings.erpnext_base_url)
    normalized_record_id = text_or_none(record_id)
    if base_url is None or normalized_record_id is None:
        return None
    return (
        f"{base_url.rstrip('/')}/app/{quote(doctype.strip().casefold(), safe='')}/"
        f"{quote(normalized_record_id, safe='')}"
    )


def _active_supplier_id_for_roster_member(
    settings: SharedSettings,
    member: dict[str, Any],
    source_payload: dict[str, Any],
) -> str | None:
    """Resolve an active ERP Supplier by known roster/CRM emails only."""
    for email in _roster_member_supplier_lookup_emails(member, source_payload):
        supplier_id = _cached_active_supplier_id_for_email(settings, email)
        if supplier_id is not None:
            return supplier_id
    return None


def _roster_member_supplier_lookup_emails(
    member: dict[str, Any],
    source_payload: dict[str, Any],
) -> list[str]:
    emails: list[str] = []
    for value in (
        source_payload.get("supplier_email"),
        source_payload.get("email_id"),
        source_payload.get("email"),
        member.get("crm_email"),
        member.get("crm_email_508"),
        member.get("email"),
        member.get("source_user_id"),
    ):
        text = text_or_none(value)
        if text is None or "@" not in text:
            continue
        normalized = text.casefold()
        if normalized not in {email.casefold() for email in emails}:
            emails.append(text)
    return emails


def _cached_active_supplier_id_for_email(
    settings: SharedSettings,
    email: str,
) -> str | None:
    base_url = text_or_none(settings.erpnext_base_url)
    api_key = text_or_none(settings.erpnext_api_key)
    normalized_email = text_or_none(email)
    if base_url is None or api_key is None or normalized_email is None:
        return None

    cache_key = (base_url.rstrip("/"), normalized_email.casefold())
    now = time.monotonic()
    cached = _SUPPLIER_EMAIL_CACHE.get(cache_key)
    if cached is not None:
        cached_at, supplier_id = cached
        if now - cached_at < _SUPPLIER_EMAIL_CACHE_TTL_SECONDS:
            return supplier_id

    client = ERPNextClient(
        base_url,
        api_key,
        timeout_seconds=settings.erpnext_api_timeout_seconds,
    )
    try:
        suppliers = client.search_suppliers(normalized_email, limit=1)
    except ERPNextAPIError:
        _SUPPLIER_EMAIL_CACHE[cache_key] = (now, None)
        return None
    finally:
        client.close()

    supplier_id = None
    if suppliers:
        supplier_id = text_or_none(suppliers[0].get("name"))
    _SUPPLIER_EMAIL_CACHE[cache_key] = (now, supplier_id)
    return supplier_id


def project_cache_summary(settings: SharedSettings) -> dict[str, Any]:
    """Return lightweight project cache metrics."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*)::int AS project_count,
                    COUNT(*) FILTER (
                        WHERE LOWER(COALESCE(source_status, '')) = 'open'
                    )::int AS open_project_count,
                    MAX(last_synced_at) AS last_synced_at
                FROM projects
                """
            )
            project_counts = dict(cursor.fetchone() or {})
            cursor.execute(
                """
                SELECT COUNT(DISTINCT project_id)::int AS projects_with_roster,
                       COUNT(*)::int AS roster_member_count
                FROM project_roster_members
                """
            )
            roster_counts = dict(cursor.fetchone() or {})
    result = {**project_counts, **roster_counts}
    return _serialize_row(result)


def mark_missing_erpnext_open_projects_not_open(
    settings: SharedSettings,
    seen_external_ids: list[str],
) -> int:
    """Mark cached ERPNext projects absent from the open sync as no longer open."""
    normalized_ids = sorted(
        {
            normalized_id
            for external_id in seen_external_ids
            if (normalized_id := text_or_none(external_id)) is not None
        }
    )
    if not normalized_ids:
        return 0
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE projects p
                SET source_status = 'Not Open',
                    last_synced_at = NOW()
                FROM project_external_ids pei
                WHERE pei.project_id = p.id
                  AND pei.source = %s
                  AND pei.active IS TRUE
                  AND LOWER(COALESCE(p.source_status, '')) = 'open'
                  AND NOT (pei.external_id = ANY(%s::text[]))
                """,
                (PROJECT_SOURCE_ERPNEXT, normalized_ids),
            )
            return int(cursor.rowcount or 0)


def project_viewer_emails_for_discord(
    settings: SharedSettings,
    discord_user_id: str,
) -> list[str]:
    """Return active people-cache emails that can prove Discord project membership."""
    normalized_user_id = text_or_none(discord_user_id)
    if normalized_user_id is None:
        return []

    emails: set[str] = set()
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT email, email_508
                FROM people
                WHERE sync_status = 'active'
                  AND discord_user_id = %s
                LIMIT 5
                """,
                (normalized_user_id,),
            )
            for row in cursor.fetchall():
                for key in ("email", "email_508"):
                    value = text_or_none(row.get(key))
                    if value:
                        emails.add(value.casefold())
    return sorted(emails)


def add_project_roster_member(
    settings: SharedSettings,
    *,
    project_id: str,
    source_user_id: str,
    email: str | None = None,
    full_name: str | None = None,
    roster_kind: str = PROJECT_ROSTER_KIND_HISTORICAL,
    source: str = PROJECT_SOURCE_MANUAL,
    source_payload: dict[str, Any] | None = None,
) -> None:
    """Add or update one local project roster member."""
    normalized_project_id = text_or_none(project_id)
    normalized_source_user_id = text_or_none(source_user_id)
    normalized_source = text_or_none(source)
    normalized_roster_kind = text_or_none(roster_kind)
    if normalized_project_id is None:
        raise ValueError("project_id is required")
    if normalized_source_user_id is None:
        raise ValueError("source_user_id is required")
    if normalized_source is None:
        raise ValueError("source is required")
    if normalized_roster_kind is None:
        raise ValueError("roster_kind is required")

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO project_roster_members (
                    id,
                    project_id,
                    source,
                    source_user_id,
                    email,
                    full_name,
                    roster_kind,
                    source_payload,
                    last_seen_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (project_id, source, source_user_id) DO UPDATE SET
                    email = EXCLUDED.email,
                    full_name = EXCLUDED.full_name,
                    roster_kind = EXCLUDED.roster_kind,
                    source_payload = EXCLUDED.source_payload,
                    last_seen_at = NOW()
                """,
                (
                    str(uuid4()),
                    normalized_project_id,
                    normalized_source,
                    normalized_source_user_id,
                    text_or_none(email),
                    text_or_none(full_name),
                    normalized_roster_kind,
                    Jsonb(source_payload or {}),
                ),
            )


def remove_project_roster_member(
    settings: SharedSettings,
    *,
    project_id: str,
    source: str,
    source_user_id: str,
    roster_kind: str | None = None,
) -> bool:
    """Remove one local project roster member."""
    normalized_project_id = text_or_none(project_id)
    normalized_source = text_or_none(source)
    normalized_source_user_id = text_or_none(source_user_id)
    normalized_roster_kind = text_or_none(roster_kind)
    if normalized_project_id is None:
        raise ValueError("project_id is required")
    if normalized_source is None:
        raise ValueError("source is required")
    if normalized_source_user_id is None:
        raise ValueError("source_user_id is required")

    params: list[Any] = [
        normalized_project_id,
        normalized_source,
        normalized_source_user_id,
    ]
    if normalized_roster_kind is not None:
        params.append(normalized_roster_kind)

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            if normalized_roster_kind is None:
                cursor.execute(
                    """
                    DELETE FROM project_roster_members
                    WHERE project_id = %s::uuid
                      AND source = %s
                      AND source_user_id = %s
                    """,
                    params,
                )
            else:
                cursor.execute(
                    """
                    DELETE FROM project_roster_members
                    WHERE project_id = %s::uuid
                      AND source = %s
                      AND source_user_id = %s
                      AND roster_kind = %s
                    """,
                    params,
                )
            return bool(cursor.rowcount)


def wiki_project_match_preview(
    settings: SharedSettings,
    *,
    document_id: str = DEFAULT_WIKI_PROJECT_DOC_ID,
    viewer_emails: list[str] | None = None,
    include_all: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Return fuzzy matches between cached projects and the wiki project tables."""
    wiki_doc = fetch_outline_document(settings, document_id=document_id)
    wiki_rows = parse_project_wiki_tables(str(wiki_doc.get("text") or ""))
    projects = list_dashboard_projects(
        settings,
        viewer_emails=viewer_emails,
        include_all=include_all,
        limit=limit,
    )
    manual_matches = project_wiki_matches(
        settings,
        project_ids=[str(project.get("id")) for project in projects],
        document_id=document_id,
    )
    matches = []
    for project in projects:
        fuzzy_best = best_wiki_match(project, wiki_rows)
        manual_match = manual_matches.get(str(project.get("id")))
        best = fuzzy_best
        if manual_match is not None:
            if manual_match.get("match_status") == PROJECT_WIKI_MATCH_NO_ROW:
                best = None
            elif manual_match.get("match_status") == PROJECT_WIKI_MATCH_CONFIRMED:
                manual_row = wiki_row_by_key(
                    wiki_rows,
                    text_or_none(manual_match.get("wiki_row_key")),
                ) or manual_match.get("source_payload")
                best = {
                    "score": 1,
                    "confidence": "confirmed",
                    "row": manual_row if isinstance(manual_row, dict) else None,
                }
        matches.append(
            {
                "project": project,
                "best_match": best,
                "fuzzy_match": fuzzy_best,
                "manual_match": manual_match,
            }
        )
    return {
        "document": {
            "id": wiki_doc.get("id"),
            "urlId": wiki_doc.get("urlId"),
            "title": wiki_doc.get("title"),
            "updatedAt": wiki_doc.get("updatedAt"),
        },
        "wiki_rows": wiki_rows,
        "matches": matches,
    }


def fetch_outline_document(
    settings: SharedSettings,
    *,
    document_id: str,
) -> dict[str, Any]:
    """Fetch one Outline document via the configured API key."""
    api_key = (settings.outline_admin_api_key or "").strip()
    if not api_key:
        raise ValueError("OUTLINE_ADMIN_API_KEY is not configured")
    base_url = settings.outline_base_url.rstrip("/")
    try:
        response = requests.post(
            f"{base_url}/api/documents.info",
            json={"id": document_id},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=settings.outline_api_timeout_seconds,
            verify=default_ca_bundle_path(),
        )
    except requests.RequestException as exc:
        raise ValueError(f"Outline document fetch failed: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise ValueError(
            f"Outline document fetch failed: status={response.status_code}"
        )
    data = response.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise ValueError("Outline document fetch failed")
    doc = data.get("data")
    if not isinstance(doc, dict):
        raise ValueError("Outline document payload is invalid")
    return doc


def parse_project_wiki_tables(markdown: str) -> list[dict[str, Any]]:
    """Parse the simple current/past project tables from the wiki document."""
    rows: list[dict[str, Any]] = []
    current_section = ""
    headers: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = line[3:].strip()
            headers = []
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("*") for cell in line.strip("|").split("|")]
        if not cells or all(_is_markdown_separator(cell) for cell in cells):
            continue
        if not headers:
            headers = cells
            continue
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        row = {headers[index]: cells[index] for index in range(len(headers))}
        row["section"] = current_section
        row["match_text"] = " ".join(
            text_or_none(row.get(key)) or ""
            for key in ("Client", "Description", "DRI", "Members", "Status")
        ).strip()
        row["row_key"] = wiki_row_key(row)
        rows.append(row)
    return rows


def wiki_row_key(row: dict[str, Any]) -> str:
    """Return a stable-ish key for a wiki table row."""
    parts = [
        text_or_none(row.get("section")) or "",
        text_or_none(row.get("Client")) or "",
        text_or_none(row.get("Description")) or "",
        text_or_none(row.get("DRI")) or "",
        text_or_none(row.get("Members")) or "",
        text_or_none(row.get("Status")) or "",
    ]
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def wiki_row_by_key(
    wiki_rows: list[dict[str, Any]],
    row_key: str | None,
) -> dict[str, Any] | None:
    """Find a parsed wiki row by row_key."""
    if row_key is None:
        return None
    for row in wiki_rows:
        if text_or_none(row.get("row_key")) == row_key:
            return row
    return None


def project_wiki_matches(
    settings: SharedSettings,
    *,
    project_ids: list[str],
    document_id: str,
) -> dict[str, dict[str, Any]]:
    """Return stored manual wiki match decisions keyed by project id."""
    normalized_project_ids = sorted(
        {
            normalized_id
            for project_id in project_ids
            if (normalized_id := text_or_none(project_id)) is not None
        }
    )
    normalized_document_id = text_or_none(document_id)
    if not normalized_project_ids or normalized_document_id is None:
        return {}
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT
                    project_id::text,
                    document_id,
                    match_status,
                    wiki_row_key,
                    wiki_row_label,
                    wiki_row_section,
                    source_payload,
                    confirmed_at,
                    updated_at
                FROM project_wiki_matches
                WHERE project_id = ANY(%s::uuid[])
                  AND document_id = %s
                """,
                (normalized_project_ids, normalized_document_id),
            )
            rows = cursor.fetchall()
    return {str(row["project_id"]): _serialize_row(dict(row)) for row in rows}


def set_project_wiki_match(
    settings: SharedSettings,
    *,
    project_id: str,
    document_id: str,
    match_status: str,
    wiki_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a manual wiki match decision for one project."""
    normalized_project_id = text_or_none(project_id)
    normalized_document_id = text_or_none(document_id)
    normalized_status = text_or_none(match_status)
    if normalized_project_id is None:
        raise ValueError("project_id is required")
    if normalized_document_id is None:
        raise ValueError("document_id is required")
    if normalized_status not in PROJECT_WIKI_MATCH_STATUSES:
        raise ValueError("invalid wiki match status")
    if normalized_status == PROJECT_WIKI_MATCH_CONFIRMED and wiki_row is None:
        raise ValueError("wiki_row is required when confirming a match")

    row_key = text_or_none(wiki_row.get("row_key")) if wiki_row is not None else None
    row_label = text_or_none(wiki_row.get("Client")) if wiki_row is not None else None
    row_section = (
        text_or_none(wiki_row.get("section")) if wiki_row is not None else None
    )
    source_payload = wiki_row or {}

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO project_wiki_matches (
                    id,
                    project_id,
                    document_id,
                    match_status,
                    wiki_row_key,
                    wiki_row_label,
                    wiki_row_section,
                    source_payload,
                    confirmed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (project_id, document_id) DO UPDATE SET
                    match_status = EXCLUDED.match_status,
                    wiki_row_key = EXCLUDED.wiki_row_key,
                    wiki_row_label = EXCLUDED.wiki_row_label,
                    wiki_row_section = EXCLUDED.wiki_row_section,
                    source_payload = EXCLUDED.source_payload,
                    confirmed_at = NOW()
                RETURNING
                    project_id::text,
                    document_id,
                    match_status,
                    wiki_row_key,
                    wiki_row_label,
                    wiki_row_section,
                    source_payload,
                    confirmed_at,
                    updated_at
                """,
                (
                    str(uuid4()),
                    normalized_project_id,
                    normalized_document_id,
                    normalized_status,
                    row_key,
                    row_label,
                    row_section,
                    Jsonb(source_payload),
                ),
            )
            saved = cursor.fetchone()
    return _serialize_row(dict(saved or {}))


def best_wiki_match(
    project: dict[str, Any],
    wiki_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the best fuzzy wiki row match for one dashboard project."""
    project_terms = [
        text_or_none(project.get("display_name")),
        text_or_none(project.get("customer")),
        text_or_none(project.get("erpnext_project_id")),
    ]
    project_text = normalize_match_text(
        " ".join(term for term in project_terms if term)
    )
    if not project_text:
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for row in wiki_rows:
        row_terms = [
            text_or_none(row.get("Client")),
            text_or_none(row.get("Description")),
            text_or_none(row.get("match_text")),
        ]
        row_text = normalize_match_text(" ".join(term for term in row_terms if term))
        if not row_text:
            continue
        score = SequenceMatcher(None, project_text, row_text).ratio()
        project_tokens = set(project_text.split())
        row_tokens = set(row_text.split())
        if project_tokens and row_tokens:
            score = max(
                score,
                len(project_tokens & row_tokens) / len(project_tokens | row_tokens),
            )
        if best is None or score > best[0]:
            best = (score, row)
    if best is None:
        return None
    score, row = best
    return {
        "score": round(score, 3),
        "confidence": match_confidence(score),
        "row": row,
    }


def normalize_match_text(value: str) -> str:
    """Normalize project/client labels for fuzzy matching."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    text = text.casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(
        r"\b(?:llc|inc|corp|corporation|ltd|dba|the|project|platform)\b",
        " ",
        text,
    )
    return " ".join(text.split())


def match_confidence(score: float) -> str:
    """Classify a fuzzy match score for review UI."""
    if score >= 0.72:
        return "high"
    if score >= 0.46:
        return "medium"
    return "low"


def _is_markdown_separator(value: str) -> bool:
    normalized = value.strip().replace(":", "").replace("-", "")
    return normalized == ""


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = value
    return serialized
