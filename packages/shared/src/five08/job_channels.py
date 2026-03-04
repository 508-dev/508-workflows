"""Persistence helpers for job-post channel registration."""

from __future__ import annotations

from psycopg.rows import dict_row

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


def list_registered_job_post_channels(
    settings: SharedSettings, *, guild_id: str
) -> list[str]:
    """Return registered job-post channel IDs for a guild."""
    query = """
        SELECT channel_id
        FROM job_post_channels
        WHERE guild_id = %s
        ORDER BY channel_id ASC
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (guild_id,))
            rows = cursor.fetchall()
    return [str(row["channel_id"]) for row in rows]


def register_job_post_channel(
    settings: SharedSettings, *, guild_id: str, channel_id: str
) -> bool:
    """Register one channel for automatic job matching.

    Returns True when a new registration is created, False when already present.
    """
    query = """
        INSERT INTO job_post_channels (guild_id, channel_id)
        VALUES (%s, %s)
        ON CONFLICT (guild_id, channel_id) DO NOTHING
        RETURNING channel_id
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (guild_id, channel_id))
            row = cursor.fetchone()
    return row is not None


def unregister_job_post_channel(
    settings: SharedSettings, *, guild_id: str, channel_id: str
) -> bool:
    """Remove one channel registration.

    Returns True when an existing registration is removed, False when not present.
    """
    query = """
        DELETE FROM job_post_channels
        WHERE guild_id = %s AND channel_id = %s
        RETURNING channel_id
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (guild_id, channel_id))
            row = cursor.fetchone()
    return row is not None
