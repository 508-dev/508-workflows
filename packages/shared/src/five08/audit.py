"""Audit and people-cache persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class AuditSource(StrEnum):
    """Supported human action sources."""

    DISCORD = "discord"
    ADMIN_DASHBOARD = "admin_dashboard"


class AuditResult(StrEnum):
    """Outcome values for audit events."""

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class ActorProvider(StrEnum):
    """Identity providers used to resolve a person."""

    DISCORD = "discord"
    ADMIN_SSO = "admin_sso"


class PeopleSyncStatus(StrEnum):
    """States for CRM-backed people cache records."""

    ACTIVE = "active"
    MISSING_IN_CRM = "missing_in_crm"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PersonRecord:
    """Normalized people cache row shape."""

    crm_contact_id: str
    name: str | None = None
    email: str | None = None
    email_508: str | None = None
    discord_user_id: str | None = None
    discord_username: str | None = None
    discord_roles: list[str] | None = None
    github_username: str | None = None
    contact_type: str | None = None
    is_member: bool = False
    address_country: str | None = None
    address_city: str | None = None
    address_state: str | None = None
    timezone: str | None = None
    seniority: str | None = None
    linkedin: str | None = None
    skills: list[str] | None = None
    skill_attrs: dict[str, int] | None = None
    latest_resume_id: str | None = None
    latest_resume_name: str | None = None
    sync_status: PeopleSyncStatus = PeopleSyncStatus.ACTIVE


@dataclass(frozen=True)
class AuditEventInput:
    """Input payload for writing one audit event."""

    source: AuditSource
    action: str
    result: AuditResult
    actor_provider: ActorProvider
    actor_subject: str
    resource_type: str | None = None
    resource_id: str | None = None
    actor_display_name: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class CreatedAuditEvent:
    """Insert result payload for one audit event."""

    id: str
    person_id: str | None


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_actor_subject(provider: ActorProvider, subject: str) -> str:
    """Normalize actor subject values for stable lookups."""
    normalized = subject.strip()
    if provider == ActorProvider.ADMIN_SSO:
        email = _normalize_email(normalized)
        if email is None:
            raise ValueError("actor_subject is required for admin_sso")
        return email
    if not normalized:
        raise ValueError("actor_subject is required")
    return normalized


def upsert_person(settings: SharedSettings, person: PersonRecord) -> str:
    """Insert or update one people cache record."""
    person_id = str(uuid4())
    query = """
        INSERT INTO people (
            id,
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
            skill_attrs,
            latest_resume_id,
            latest_resume_name,
            sync_status
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (crm_contact_id) DO UPDATE
        SET
            name = EXCLUDED.name,
            email = EXCLUDED.email,
            email_508 = EXCLUDED.email_508,
            discord_user_id = EXCLUDED.discord_user_id,
            discord_username = EXCLUDED.discord_username,
            discord_roles = EXCLUDED.discord_roles,
            github_username = EXCLUDED.github_username,
            contact_type = EXCLUDED.contact_type,
            is_member = EXCLUDED.is_member,
            address_country = EXCLUDED.address_country,
            address_city = EXCLUDED.address_city,
            address_state = EXCLUDED.address_state,
            timezone = EXCLUDED.timezone,
            seniority = EXCLUDED.seniority,
            linkedin = EXCLUDED.linkedin,
            skills = EXCLUDED.skills,
            skill_attrs = EXCLUDED.skill_attrs,
            latest_resume_id = EXCLUDED.latest_resume_id,
            latest_resume_name = EXCLUDED.latest_resume_name,
            sync_status = EXCLUDED.sync_status
        RETURNING id::text;
    """
    roles = person.discord_roles or []
    skills = person.skills or []
    skill_attrs = person.skill_attrs or {}

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    person_id,
                    person.crm_contact_id,
                    person.name,
                    _normalize_email(person.email),
                    _normalize_email(person.email_508),
                    person.discord_user_id,
                    person.discord_username,
                    Jsonb(roles),
                    person.github_username,
                    _normalize_text(person.contact_type),
                    person.is_member,
                    _normalize_text(person.address_country),
                    _normalize_text(person.address_city),
                    _normalize_text(person.address_state),
                    _normalize_text(person.timezone),
                    _normalize_text(person.seniority),
                    _normalize_text(person.linkedin),
                    skills,
                    Jsonb(skill_attrs),
                    _normalize_text(person.latest_resume_id),
                    _normalize_text(person.latest_resume_name),
                    person.sync_status.value,
                ),
            )
            row = cursor.fetchone()

    if row is None:
        raise RuntimeError("Failed to upsert person record")

    return row["id"]


def update_person_discord_roles(
    settings: SharedSettings,
    discord_user_id: str,
    roles: list[str],
) -> bool:
    """Update discord_roles for a person by discord_user_id.

    Only updates the discord_roles column; all other CRM-sourced fields are
    left unchanged. Returns True if a matching record was found and updated.
    """
    query = """
        UPDATE people
        SET discord_roles = %s
        WHERE discord_user_id = %s
        RETURNING id::text;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (Jsonb(roles), discord_user_id))
            row = cursor.fetchone()
    return row is not None


def upsert_discord_member(
    settings: SharedSettings,
    *,
    discord_user_id: str,
    guild_id: str,
    discord_username: str | None,
    display_name: str | None,
    roles: list[str],
) -> None:
    """Insert or update a discord member snapshot in the local cache."""
    query = """
        INSERT INTO discord_members (
            discord_user_id,
            guild_id,
            discord_username,
            display_name,
            roles
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (guild_id, discord_user_id) DO UPDATE
        SET
            guild_id = EXCLUDED.guild_id,
            discord_username = EXCLUDED.discord_username,
            display_name = EXCLUDED.display_name,
            roles = EXCLUDED.roles
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    discord_user_id,
                    guild_id,
                    _normalize_text(discord_username),
                    _normalize_text(display_name),
                    Jsonb(roles),
                ),
            )


def resolve_person_id(
    settings: SharedSettings,
    *,
    actor_provider: ActorProvider,
    actor_subject: str,
) -> str | None:
    """Resolve a person id from audit actor provider + subject."""
    normalized_subject = normalize_actor_subject(actor_provider, actor_subject)

    if actor_provider == ActorProvider.DISCORD:
        query = """
            SELECT id::text
            FROM people
            WHERE discord_user_id = %s
            LIMIT 1;
        """
        params: tuple[str, ...] = (normalized_subject,)
    else:
        query = """
            SELECT id::text
            FROM people
            WHERE lower(email_508) = %s OR lower(email) = %s
            LIMIT 1;
        """
        params = (normalized_subject, normalized_subject)

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

    if row is None:
        return None
    return row["id"]


def get_discord_user_id_for_contact(
    settings: SharedSettings,
    crm_contact_id: str,
) -> str | None:
    """Return the discord_user_id linked to a CRM contact, if any."""
    query = """
        SELECT discord_user_id
        FROM people
        WHERE crm_contact_id = %s
        LIMIT 1;
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (crm_contact_id,))
            row = cursor.fetchone()
    if row is None:
        return None
    return row["discord_user_id"]


def insert_audit_event(
    settings: SharedSettings,
    payload: AuditEventInput,
) -> CreatedAuditEvent:
    """Insert one human audit event."""
    event_id = str(uuid4())
    occurred_at = payload.occurred_at
    if occurred_at is None:
        occurred_at = datetime.now(tz=timezone.utc)

    person_id = resolve_person_id(
        settings,
        actor_provider=payload.actor_provider,
        actor_subject=payload.actor_subject,
    )
    normalized_subject = normalize_actor_subject(
        payload.actor_provider, payload.actor_subject
    )

    query = """
        INSERT INTO audit_events (
            id,
            occurred_at,
            source,
            action,
            resource_type,
            resource_id,
            result,
            actor_provider,
            actor_subject,
            actor_display_name,
            person_id,
            correlation_id,
            metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """

    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    event_id,
                    occurred_at,
                    payload.source.value,
                    payload.action,
                    payload.resource_type,
                    payload.resource_id,
                    payload.result.value,
                    payload.actor_provider.value,
                    normalized_subject,
                    payload.actor_display_name,
                    person_id,
                    payload.correlation_id,
                    Jsonb(payload.metadata or {}),
                ),
            )

    return CreatedAuditEvent(id=event_id, person_id=person_id)
