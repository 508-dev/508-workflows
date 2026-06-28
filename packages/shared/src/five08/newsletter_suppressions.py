"""Internal newsletter suppression registry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, LiteralString, cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection, trusted_sql
from five08.settings import SharedSettings

NEWSLETTER_SUPPRESSION_SOURCE_PROVIDERS = {"brevo", "keila", "manual"}


@dataclass(frozen=True, slots=True)
class NewsletterSuppressionRecord:
    """Persisted provider suppression state for one email address."""

    email: str
    source_provider: str
    reason: str
    active: bool
    metadata: dict[str, Any]
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _as_record(row: dict[str, Any]) -> NewsletterSuppressionRecord:
    metadata = row.get("metadata")
    return NewsletterSuppressionRecord(
        email=str(row["email"]),
        source_provider=str(row["source_provider"]),
        reason=str(row["reason"]),
        active=bool(row["active"]),
        metadata=metadata if isinstance(metadata, dict) else {},
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_newsletter_suppression(
    settings: SharedSettings,
    *,
    email: str,
    source_provider: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record an active provider suppression observation."""
    normalized_email = _normalize_email(email)
    normalized_source = source_provider.strip().lower()
    normalized_reason = reason.strip().lower()
    if not normalized_email:
        raise ValueError("email is required")
    if normalized_source not in NEWSLETTER_SUPPRESSION_SOURCE_PROVIDERS:
        raise ValueError("unknown newsletter suppression source provider")
    if not normalized_reason:
        raise ValueError("reason is required")

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO newsletter_suppressions (
                    email,
                    source_provider,
                    reason,
                    active,
                    metadata,
                    first_seen_at,
                    last_seen_at
                ) VALUES (%s, %s, %s, true, %s, NOW(), NOW())
                ON CONFLICT (email, source_provider)
                DO UPDATE SET
                    reason = EXCLUDED.reason,
                    active = true,
                    metadata = EXCLUDED.metadata,
                    last_seen_at = NOW()
                """,
                (
                    normalized_email,
                    normalized_source,
                    normalized_reason,
                    Jsonb(metadata or {}),
                ),
            )


def deactivate_newsletter_suppression(
    settings: SharedSettings,
    *,
    email: str,
    source_provider: str,
) -> bool:
    """Deactivate one provider suppression row, returning whether it changed."""
    normalized_email = _normalize_email(email)
    normalized_source = source_provider.strip().lower()
    if not normalized_email:
        raise ValueError("email is required")
    if normalized_source not in NEWSLETTER_SUPPRESSION_SOURCE_PROVIDERS:
        raise ValueError("unknown newsletter suppression source provider")

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE newsletter_suppressions
                SET active = false
                WHERE email = %s
                  AND source_provider = %s
                  AND active = true
                """,
                (normalized_email, normalized_source),
            )
            return cursor.rowcount > 0


def load_active_newsletter_suppressions_by_email(
    settings: SharedSettings,
    emails: Iterable[str],
) -> dict[str, list[NewsletterSuppressionRecord]]:
    """Return active suppression records keyed by normalized email."""
    normalized_emails = sorted({_normalize_email(email) for email in emails if email})
    if not normalized_emails:
        return {}

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT *
                FROM newsletter_suppressions
                WHERE active = true
                  AND email = ANY(%s)
                ORDER BY email ASC, source_provider ASC
                """,
                (normalized_emails,),
            )
            rows = cursor.fetchall()

    records_by_email: dict[str, list[NewsletterSuppressionRecord]] = {}
    for row in rows:
        record = _as_record(row)
        records_by_email.setdefault(record.email, []).append(record)
    return records_by_email


def list_newsletter_suppressions(
    settings: SharedSettings,
    *,
    limit: int = 200,
    active_only: bool = True,
) -> list[NewsletterSuppressionRecord]:
    """List newsletter suppression records for admin visibility."""
    conditions: list[str] = []
    params: list[Any] = []
    if active_only:
        conditions.append("active = true")
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    query = cast(
        LiteralString,
        f"""
        SELECT *
        FROM newsletter_suppressions
        {where_clause}
        ORDER BY last_seen_at DESC, email ASC, source_provider ASC
        LIMIT %s
        """,
    )

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), params)
            rows = cursor.fetchall()
    return [_as_record(row) for row in rows]
