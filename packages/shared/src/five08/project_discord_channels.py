"""Persistence helpers for private Discord channels registered to ERP projects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from psycopg.rows import dict_row

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class ProjectDiscordChannelConflict(ValueError):
    """Raised when a private channel is already actively owned by another project."""


class ProjectPaymentDiscordDeliveryClaimStatus(StrEnum):
    """Outcome of the bot-side lease for one notification ID."""

    CLAIMED = "claimed"
    ALREADY_DELIVERED = "already_delivered"
    IN_PROGRESS = "in_progress"
    SCOPE_MISMATCH = "scope_mismatch"


@dataclass(frozen=True)
class RegisteredProjectDiscordChannel:
    """A verified private Discord target for project payment notifications."""

    id: str
    project_id: str
    guild_id: str
    channel_id: str
    channel_name: str | None
    active: bool
    registered_by_discord_user_id: str | None
    last_verified_at: datetime | None
    last_verification_error: str | None


@dataclass(frozen=True)
class ProjectPaymentDiscordDeliveryClaim:
    """The bot's durable delivery lease or an existing receipt."""

    status: ProjectPaymentDiscordDeliveryClaimStatus
    discord_message_id: str | None = None
    lease_token: str | None = None


def _as_registered_channel(row: dict[str, Any]) -> RegisteredProjectDiscordChannel:
    return RegisteredProjectDiscordChannel(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        guild_id=str(row["guild_id"]),
        channel_id=str(row["channel_id"]),
        channel_name=(str(row["channel_name"]) if row.get("channel_name") else None),
        active=bool(row["active"]),
        registered_by_discord_user_id=(
            str(row["registered_by_discord_user_id"])
            if row.get("registered_by_discord_user_id")
            else None
        ),
        last_verified_at=row.get("last_verified_at"),
        last_verification_error=(
            str(row["last_verification_error"])
            if row.get("last_verification_error")
            else None
        ),
    )


def register_project_discord_channel(
    settings: SharedSettings,
    *,
    project_id: str,
    guild_id: str,
    channel_id: str,
    channel_name: str | None,
    registered_by_discord_user_id: str | None,
) -> tuple[RegisteredProjectDiscordChannel, bool]:
    """Activate one channel for a project without reassigning another project.

    Returns ``(channel, created)``. The partial unique index in the migration
    guarantees a channel can have only one *active* project mapping.
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            # The partial unique index is the serialization boundary. A plain
            # SELECT ... FOR UPDATE cannot lock a row that does not exist, so
            # it allows two first registrations to race into a raw unique error.
            # At most one retry is needed if another request deactivated the
            # winning mapping between our conflict and follow-up lookup.
            for _ in range(2):
                cursor.execute(
                    """
                    INSERT INTO project_discord_channels (
                        id,
                        project_id,
                        guild_id,
                        channel_id,
                        channel_name,
                        active,
                        registered_by_discord_user_id,
                        last_verified_at
                    ) VALUES (%s, %s::uuid, %s, %s, %s, TRUE, %s, NOW())
                    ON CONFLICT (guild_id, channel_id) WHERE active IS TRUE
                    DO NOTHING
                    RETURNING id::text, project_id::text, guild_id, channel_id,
                              channel_name, active, registered_by_discord_user_id,
                              last_verified_at, last_verification_error
                    """,
                    (
                        str(uuid4()),
                        project_id,
                        guild_id,
                        channel_id,
                        channel_name,
                        registered_by_discord_user_id,
                    ),
                )
                created = cursor.fetchone()
                if created is not None:
                    return _as_registered_channel(created), True

                cursor.execute(
                    """
                    SELECT id::text, project_id::text, guild_id, channel_id, channel_name,
                           active, registered_by_discord_user_id, last_verified_at,
                           last_verification_error
                    FROM project_discord_channels
                    WHERE guild_id = %s AND channel_id = %s AND active IS TRUE
                    FOR UPDATE
                    """,
                    (guild_id, channel_id),
                )
                active_mapping = cursor.fetchone()
                if active_mapping is None:
                    continue
                if str(active_mapping["project_id"]) != project_id:
                    raise ProjectDiscordChannelConflict(
                        "This Discord channel is already registered to another project"
                    )
                cursor.execute(
                    """
                    UPDATE project_discord_channels
                    SET channel_name = %s,
                        registered_by_discord_user_id = %s,
                        last_verified_at = NOW(),
                        last_verification_error = NULL,
                        updated_at = NOW()
                    WHERE id = %s::uuid
                    RETURNING id::text, project_id::text, guild_id, channel_id,
                              channel_name, active, registered_by_discord_user_id,
                              last_verified_at, last_verification_error
                    """,
                    (
                        channel_name,
                        registered_by_discord_user_id,
                        str(active_mapping["id"]),
                    ),
                )
                updated = cursor.fetchone()
                if updated is None:
                    raise RuntimeError("Unable to update project Discord channel")
                return _as_registered_channel(updated), False
    raise RuntimeError("Unable to register project Discord channel")


def claim_project_payment_discord_delivery(
    settings: SharedSettings,
    *,
    notification_id: str,
    project_discord_channel_id: str,
    worker_lease_token: str,
    stale_after_seconds: int = 300,
) -> ProjectPaymentDiscordDeliveryClaim:
    """Claim bot-side delivery or return an existing receipt without sending.

    The worker's outbox and the bot are separate processes. This lease avoids
    duplicate Discord sends when an internal HTTP request is retried after a
    timeout, while the hidden message marker remains recovery for the tiny
    Discord-send/receipt-write crash window.
    """
    normalized_stale_after_seconds = max(1, stale_after_seconds)
    lease_token = str(uuid4())
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                INSERT INTO project_payment_discord_deliveries (
                    notification_id,
                    project_discord_channel_id,
                    project_id,
                    channel_id,
                    status,
                    attempts,
                    locked_at,
                    lock_token
                )
                SELECT outbox.id,
                       channel.id,
                       channel.project_id,
                       channel.channel_id,
                       'sending',
                       1,
                       NOW(),
                       %s::uuid
                FROM project_payment_notification_outbox outbox
                INNER JOIN project_discord_channels channel
                    ON channel.id = outbox.project_discord_channel_id
                INNER JOIN projects project ON project.id = channel.project_id
                WHERE outbox.id = %s::uuid
                  AND channel.id = %s::uuid
                  AND outbox.status = 'sending'
                  AND outbox.lease_token = %s::uuid
                  AND channel.active IS TRUE
                  AND LOWER(COALESCE(project.source_status, '')) = 'open'
                ON CONFLICT (notification_id) DO NOTHING
                RETURNING notification_id
                """,
                (
                    lease_token,
                    notification_id,
                    project_discord_channel_id,
                    worker_lease_token,
                ),
            )
            if cursor.fetchone() is not None:
                return ProjectPaymentDiscordDeliveryClaim(
                    status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
                    lease_token=lease_token,
                )

            cursor.execute(
                """
                UPDATE project_payment_discord_deliveries
                SET status = 'sending',
                    attempts = attempts + 1,
                    locked_at = NOW(),
                    lock_token = %s::uuid,
                    last_error = NULL,
                    updated_at = NOW()
                FROM project_payment_notification_outbox outbox
                INNER JOIN project_discord_channels channel
                    ON channel.id = outbox.project_discord_channel_id
                INNER JOIN projects project ON project.id = channel.project_id
                WHERE project_payment_discord_deliveries.notification_id = outbox.id
                  AND project_payment_discord_deliveries.notification_id = %s::uuid
                  AND project_payment_discord_deliveries.project_discord_channel_id
                      = %s::uuid
                  AND channel.id = project_payment_discord_deliveries.project_discord_channel_id
                  AND outbox.status = 'sending'
                  AND outbox.lease_token = %s::uuid
                  AND channel.active IS TRUE
                  AND LOWER(COALESCE(project.source_status, '')) = 'open'
                  AND (
                    project_payment_discord_deliveries.status = 'failed'
                    OR (
                        project_payment_discord_deliveries.status = 'sending'
                        AND project_payment_discord_deliveries.locked_at
                            < NOW() - (%s * INTERVAL '1 second')
                    )
                  )
                RETURNING notification_id
                """,
                (
                    lease_token,
                    notification_id,
                    project_discord_channel_id,
                    worker_lease_token,
                    normalized_stale_after_seconds,
                ),
            )
            if cursor.fetchone() is not None:
                return ProjectPaymentDiscordDeliveryClaim(
                    status=ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED,
                    lease_token=lease_token,
                )

            cursor.execute(
                """
                SELECT delivery.project_discord_channel_id::text,
                       delivery.status,
                       delivery.discord_message_id
                FROM project_payment_discord_deliveries delivery
                INNER JOIN project_payment_notification_outbox outbox
                    ON outbox.id = delivery.notification_id
                WHERE delivery.notification_id = %s::uuid
                  AND outbox.status = 'sending'
                  AND outbox.lease_token = %s::uuid
                """,
                (notification_id, worker_lease_token),
            )
            existing = cursor.fetchone()
    if existing is None or (
        str(existing["project_discord_channel_id"]) != project_discord_channel_id
    ):
        return ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.SCOPE_MISMATCH
        )
    message_id = str(existing.get("discord_message_id") or "").strip() or None
    if str(existing["status"]) == "sent" and message_id is not None:
        return ProjectPaymentDiscordDeliveryClaim(
            status=ProjectPaymentDiscordDeliveryClaimStatus.ALREADY_DELIVERED,
            discord_message_id=message_id,
        )
    return ProjectPaymentDiscordDeliveryClaim(
        status=ProjectPaymentDiscordDeliveryClaimStatus.IN_PROGRESS
    )


def mark_project_payment_discord_delivery_sent(
    settings: SharedSettings,
    *,
    notification_id: str,
    project_discord_channel_id: str,
    discord_message_id: str,
    lease_token: str,
) -> bool:
    """Persist the Discord receipt before returning success to the worker."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_discord_deliveries
                SET status = 'sent',
                    discord_message_id = %s,
                    locked_at = NULL,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE notification_id = %s::uuid
                  AND project_discord_channel_id = %s::uuid
                  AND status = 'sending'
                  AND lock_token = %s::uuid
                RETURNING notification_id
                """,
                (
                    discord_message_id,
                    notification_id,
                    project_discord_channel_id,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def mark_project_payment_discord_delivery_failed(
    settings: SharedSettings,
    *,
    notification_id: str,
    project_discord_channel_id: str,
    error: str,
    lease_token: str,
) -> bool:
    """Release a bot-side lease for a retry with bounded diagnostics."""
    normalized_error = error.strip()[:2000] or "project payment Discord delivery failed"
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_discord_deliveries
                SET status = 'failed',
                    locked_at = NULL,
                    last_error = %s,
                    updated_at = NOW()
                WHERE notification_id = %s::uuid
                  AND project_discord_channel_id = %s::uuid
                  AND status = 'sending'
                  AND lock_token = %s::uuid
                RETURNING notification_id
                """,
                (
                    normalized_error,
                    notification_id,
                    project_discord_channel_id,
                    lease_token,
                ),
            )
            return cursor.fetchone() is not None


def renew_project_payment_discord_delivery_lease(
    settings: SharedSettings,
    *,
    notification_id: str,
    project_discord_channel_id: str,
    worker_lease_token: str,
    lease_token: str,
) -> bool:
    """Extend an active bot lease while its worker still owns the outbox row."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_payment_discord_deliveries
                SET locked_at = NOW(), updated_at = NOW()
                FROM project_payment_notification_outbox outbox
                WHERE project_payment_discord_deliveries.notification_id = outbox.id
                  AND project_payment_discord_deliveries.notification_id = %s::uuid
                  AND project_payment_discord_deliveries.project_discord_channel_id
                      = %s::uuid
                  AND project_payment_discord_deliveries.status = 'sending'
                  AND project_payment_discord_deliveries.lock_token = %s::uuid
                  AND outbox.status = 'sending'
                  AND outbox.lease_token = %s::uuid
                RETURNING notification_id
                """,
                (
                    notification_id,
                    project_discord_channel_id,
                    lease_token,
                    worker_lease_token,
                ),
            )
            return cursor.fetchone() is not None


def unregister_project_discord_channel(
    settings: SharedSettings,
    *,
    guild_id: str,
    channel_id: str,
) -> bool:
    """Deactivate a channel mapping while preserving the audit/history row."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                UPDATE project_discord_channels
                SET active = FALSE, updated_at = NOW()
                WHERE guild_id = %s AND channel_id = %s AND active IS TRUE
                RETURNING id
                """,
                (guild_id, channel_id),
            )
            row = cursor.fetchone()
    return row is not None


def list_active_project_discord_channels(
    settings: SharedSettings,
    *,
    project_id: str,
) -> list[RegisteredProjectDiscordChannel]:
    """Return active notification channels for one project."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, project_id::text, guild_id, channel_id, channel_name,
                       active, registered_by_discord_user_id, last_verified_at,
                       last_verification_error
                FROM project_discord_channels
                WHERE project_id = %s::uuid AND active IS TRUE
                ORDER BY created_at ASC
                """,
                (project_id,),
            )
            rows = cursor.fetchall()
    return [_as_registered_channel(row) for row in rows]


def get_active_project_discord_channel(
    settings: SharedSettings,
    *,
    project_id: str,
    channel_id: str,
) -> RegisteredProjectDiscordChannel | None:
    """Load exactly one active mapping, for live bot delivery revalidation."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id::text, project_id::text, guild_id, channel_id, channel_name,
                       active, registered_by_discord_user_id, last_verified_at,
                       last_verification_error
                FROM project_discord_channels
                WHERE project_id = %s::uuid
                  AND channel_id = %s
                  AND active IS TRUE
                """,
                (project_id, channel_id),
            )
            row = cursor.fetchone()
    return _as_registered_channel(row) if row is not None else None


def record_project_discord_channel_verification(
    settings: SharedSettings,
    *,
    mapping_id: str,
    error: str | None = None,
) -> None:
    """Record a live bot verification result without changing mapping ownership."""
    with get_postgres_connection(settings) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE project_discord_channels
                SET last_verified_at = NOW(),
                    last_verification_error = %s,
                    updated_at = NOW()
                WHERE id = %s::uuid
                """,
                ((error or "").strip()[:1000] or None, mapping_id),
            )
