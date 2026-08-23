"""Durable review queue for contacts proposed from forwarded email."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection, trusted_sql
from five08.settings import SharedSettings


class ContactEmailCandidateStatus(StrEnum):
    """Lifecycle states for a contact email candidate."""

    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class ContactEmailCandidateInput:
    """A deterministic proposal extracted from one alias-delivered message."""

    message_id: str
    delivered_to: str
    forwarded_by_name: str | None
    forwarded_by_email: str | None
    proposed_name: str | None
    proposed_email: str | None
    subject: str | None
    body_text: str
    links: list[str]
    extraction_method: str


@dataclass(frozen=True)
class ContactEmailCandidate:
    """A persisted candidate awaiting an explicit dashboard decision."""

    id: str
    status: ContactEmailCandidateStatus
    message_id: str
    delivered_to: str
    forwarded_by_name: str | None
    forwarded_by_email: str | None
    proposed_name: str | None
    proposed_email: str | None
    subject: str | None
    body_text: str
    links: list[str]
    extraction_method: str
    crm_contact_id: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _status(value: Any) -> ContactEmailCandidateStatus:
    try:
        return ContactEmailCandidateStatus(str(value or "").strip().casefold())
    except ValueError:
        return ContactEmailCandidateStatus.PENDING


def _as_candidate(row: dict[str, Any]) -> ContactEmailCandidate:
    links = row.get("links") or []
    return ContactEmailCandidate(
        id=str(row["id"]),
        status=_status(row.get("status")),
        message_id=str(row["message_id"]),
        delivered_to=str(row["delivered_to"]),
        forwarded_by_name=row.get("forwarded_by_name"),
        forwarded_by_email=row.get("forwarded_by_email"),
        proposed_name=row.get("proposed_name"),
        proposed_email=row.get("proposed_email"),
        subject=row.get("subject"),
        body_text=str(row.get("body_text") or ""),
        links=[str(link) for link in links if str(link).strip()],
        extraction_method=str(row.get("extraction_method") or "unknown"),
        crm_contact_id=row.get("crm_contact_id"),
        reviewed_by=row.get("reviewed_by"),
        reviewed_at=row.get("reviewed_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def upsert_contact_email_candidate(
    settings: SharedSettings,
    candidate: ContactEmailCandidateInput,
) -> ContactEmailCandidate:
    """Create or refresh a pending candidate without changing its review state."""
    query = """
        INSERT INTO contact_email_candidates (
            id,
            status,
            message_id,
            delivered_to,
            forwarded_by_name,
            forwarded_by_email,
            proposed_name,
            proposed_email,
            subject,
            body_text,
            links,
            extraction_method
        ) VALUES (
            %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (message_id) DO UPDATE SET
            delivered_to = EXCLUDED.delivered_to,
            forwarded_by_name = EXCLUDED.forwarded_by_name,
            forwarded_by_email = EXCLUDED.forwarded_by_email,
            proposed_name = EXCLUDED.proposed_name,
            proposed_email = EXCLUDED.proposed_email,
            subject = EXCLUDED.subject,
            body_text = EXCLUDED.body_text,
            links = EXCLUDED.links,
            extraction_method = EXCLUDED.extraction_method,
            updated_at = NOW()
        WHERE contact_email_candidates.status = 'pending'
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    str(uuid4()),
                    candidate.message_id,
                    candidate.delivered_to,
                    candidate.forwarded_by_name,
                    candidate.forwarded_by_email,
                    candidate.proposed_name,
                    candidate.proposed_email,
                    candidate.subject,
                    candidate.body_text,
                    Jsonb(candidate.links),
                    candidate.extraction_method,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "SELECT * FROM contact_email_candidates WHERE message_id = %s",
                    (candidate.message_id,),
                )
                row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Contact email candidate upsert returned no row")
    return _as_candidate(row)


def list_contact_email_candidates(
    settings: SharedSettings,
    *,
    status: ContactEmailCandidateStatus
    | str
    | None = ContactEmailCandidateStatus.PENDING,
    limit: int = 50,
) -> list[ContactEmailCandidate]:
    """List recent contact candidates for dashboard review."""
    conditions = ""
    params: list[Any] = []
    if status is not None:
        conditions = "WHERE status = %s"
        params.append(_status(status).value)
    params.append(max(1, min(limit, 100)))
    query = f"""
        SELECT *
        FROM contact_email_candidates
        {conditions}
        ORDER BY created_at DESC
        LIMIT %s
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(trusted_sql(query), tuple(params))
            rows = cursor.fetchall()
    return [_as_candidate(row) for row in rows]


def get_contact_email_candidate(
    settings: SharedSettings,
    candidate_id: str,
) -> ContactEmailCandidate | None:
    """Load a candidate by its UUID."""
    if not candidate_id.strip():
        return None
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM contact_email_candidates WHERE id::text = %s",
                (candidate_id.strip(),),
            )
            row = cursor.fetchone()
    return _as_candidate(row) if row is not None else None


def review_contact_email_candidate(
    settings: SharedSettings,
    *,
    candidate_id: str,
    status: ContactEmailCandidateStatus,
    reviewer: str,
    proposed_name: str | None = None,
    proposed_email: str | None = None,
    crm_contact_id: str | None = None,
) -> ContactEmailCandidate | None:
    """Persist one explicit review decision if the candidate is still pending."""
    if status not in {
        ContactEmailCandidateStatus.APPROVED,
        ContactEmailCandidateStatus.DISMISSED,
    }:
        raise ValueError(
            "Contact email candidate status must be approved or dismissed."
        )
    query = """
        UPDATE contact_email_candidates
        SET
            status = %s,
            proposed_name = COALESCE(%s, proposed_name),
            proposed_email = COALESCE(%s, proposed_email),
            crm_contact_id = COALESCE(%s, crm_contact_id),
            reviewed_by = %s,
            reviewed_at = NOW(),
            updated_at = NOW()
        WHERE id::text = %s
          AND status = 'pending'
        RETURNING *
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    status.value,
                    proposed_name,
                    proposed_email,
                    crm_contact_id,
                    reviewer,
                    candidate_id,
                ),
            )
            row = cursor.fetchone()
    return _as_candidate(row) if row is not None else None
