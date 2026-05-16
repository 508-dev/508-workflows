"""Persistence helpers for job-post channel registration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from psycopg.rows import dict_row

from five08.queue import get_postgres_connection
from five08.settings import SharedSettings


class JobPostingType(StrEnum):
    """Supported registered job forum types."""

    PART_TIME = "part_time"
    FULL_TIME = "full_time"
    PART_TIME_OR_FULL_TIME = "part_time_or_full_time"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RegisteredJobPostChannel:
    """One registered Discord job forum."""

    channel_id: str
    posting_type: JobPostingType


_POSTING_TYPE_ALIASES = {
    "part_time": JobPostingType.PART_TIME,
    "part-time": JobPostingType.PART_TIME,
    "contract": JobPostingType.PART_TIME,
    "contracts": JobPostingType.PART_TIME,
    "gig": JobPostingType.PART_TIME,
    "gigs": JobPostingType.PART_TIME,
    "full_time": JobPostingType.FULL_TIME,
    "full-time": JobPostingType.FULL_TIME,
    "fulltime": JobPostingType.FULL_TIME,
    "employee": JobPostingType.FULL_TIME,
    "employment": JobPostingType.FULL_TIME,
    "part_time_or_full_time": JobPostingType.PART_TIME_OR_FULL_TIME,
    "part-time-or-full-time": JobPostingType.PART_TIME_OR_FULL_TIME,
    "part time or full time": JobPostingType.PART_TIME_OR_FULL_TIME,
    "both": JobPostingType.PART_TIME_OR_FULL_TIME,
    "either": JobPostingType.PART_TIME_OR_FULL_TIME,
    "hybrid": JobPostingType.PART_TIME_OR_FULL_TIME,
    "unknown": JobPostingType.UNKNOWN,
}


def normalize_job_posting_type(
    value: str | JobPostingType | None,
) -> JobPostingType:
    """Normalize user input into a supported job posting type."""
    if isinstance(value, JobPostingType):
        return value
    normalized = str(value or "").strip().casefold().replace(" ", "_")
    return _POSTING_TYPE_ALIASES.get(normalized, JobPostingType.UNKNOWN)


def infer_job_posting_type_from_labels(
    labels: list[str],
    *,
    default: JobPostingType = JobPostingType.UNKNOWN,
) -> JobPostingType:
    """Infer posting type from Discord forum tag/channel labels."""
    normalized = " ".join(labels).casefold().replace("_", " ").replace("-", " ")
    part_time = any(
        token in normalized
        for token in ("part time", "contract", "contracts", "freelance", "gig")
    )
    full_time = any(
        token in normalized
        for token in ("full time", "fulltime", "permanent", "employee")
    )
    if part_time and full_time:
        return JobPostingType.PART_TIME_OR_FULL_TIME
    if part_time:
        return JobPostingType.PART_TIME
    if full_time:
        return JobPostingType.FULL_TIME
    return default


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


def list_registered_job_post_channel_configs(
    settings: SharedSettings, *, guild_id: str
) -> list[RegisteredJobPostChannel]:
    """Return registered job-post channels with their posting type."""
    query = """
        SELECT channel_id, COALESCE(posting_type, 'unknown') AS posting_type
        FROM job_post_channels
        WHERE guild_id = %s
        ORDER BY channel_id ASC
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (guild_id,))
            rows = cursor.fetchall()
    return [
        RegisteredJobPostChannel(
            channel_id=str(row["channel_id"]),
            posting_type=normalize_job_posting_type(row.get("posting_type")),
        )
        for row in rows
    ]


def register_job_post_channel(
    settings: SharedSettings,
    *,
    guild_id: str,
    channel_id: str,
    posting_type: str | JobPostingType | None = JobPostingType.PART_TIME,
) -> bool:
    """Register one channel for automatic job matching.

    Returns True when a new registration is created, False when already present.
    """
    normalized_type = normalize_job_posting_type(posting_type)
    query = """
        INSERT INTO job_post_channels (guild_id, channel_id, posting_type)
        VALUES (%s, %s, %s)
        ON CONFLICT (guild_id, channel_id) DO NOTHING
        RETURNING channel_id
    """
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(query, (guild_id, channel_id, normalized_type.value))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    UPDATE job_post_channels
                    SET posting_type = %s
                    WHERE guild_id = %s AND channel_id = %s
                    """,
                    (normalized_type.value, guild_id, channel_id),
                )
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
