"""Search recent Discord job posts visible to the requesting member."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.rows import dict_row

from five08.queue import get_postgres_connection, trusted_sql
from five08.settings import SharedSettings

JOB_SEARCH_MAX_AGE_DAYS = 45
_STOP_WORDS = frozenset(
    "a an and are any as at be could do for from have i in is job jobs looking me "
    "of on or role roles show some that the there to want what with work gigs gig "
    "opportunities opportunity find search fit fitting my hours timezone time zone "
    "please remote full part fulltime parttime contract freelance position positions".split()
)
_APAC_TERMS = (
    "apac",
    "asia pacific",
    "asia-pacific",
    "asian timezone",
    "asian time zone",
    "singapore",
    "japan",
    "australia",
    "new zealand",
    "hong kong",
    "india",
    "sydney",
    "melbourne",
    "tokyo",
    "seoul",
    "taipei",
    "manila",
    "jakarta",
    "bangkok",
    "kuala lumpur",
    "bengaluru",
    "auckland",
    "utc+8",
    "utc +8",
    "utc+9",
    "utc +9",
    "gmt+8",
    "gmt+9",
    "aest",
    "jst",
    "sgt",
)
_REGION_WORDS = frozenset({"apac", "asia", "pacific", "asian"})
_SINGLE_LETTER_SKILLS = frozenset({"c", "r"})


def _search_terms(query: str) -> tuple[list[str], bool, bool]:
    lowered = query.casefold()
    apac = bool(re.search(r"\b(?:apac|asia[ -]pacific|asian)\b", lowered))
    remote = bool(re.search(r"\bremote\b", lowered))
    words = [word.strip(".") for word in re.findall(r"[a-z0-9+#.]+", lowered)]
    return (
        [
            word
            for word in words
            if (len(word) > 1 or word in _SINGLE_LETTER_SKILLS)
            and word not in _STOP_WORDS
            and word not in _REGION_WORDS
        ],
        apac,
        remote,
    )


def _contains_term(text: str, term: str) -> bool:
    """Match a skill or region as a complete token, including internal symbols."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def rank_job_posts(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Rank bounded, already authorized rows by the member's prose request."""
    terms, apac, remote = _search_terms(query)
    full_time = re.search(r"\bfull[ -]?time\b", query, re.I) is not None
    part_time = (
        re.search(
            r"\b(?:part[ -]?time|contracts?|contractors?|freelanc(?:e|er|ers|ing))\b",
            query,
            re.I,
        )
        is not None
    )
    ranked: list[tuple[int, datetime, dict[str, Any]]] = []
    for row in rows:
        title = str(row.get("title") or "")
        body = str(row.get("body") or "")
        location = str(row.get("location") or "")
        tags = " ".join(str(tag) for tag in row.get("tags") or [])
        searchable = f"{title} {body} {location} {tags}".casefold()
        posting_type = str(row.get("posting_type") or "")
        if (
            full_time
            and not part_time
            and posting_type
            not in {
                "full_time",
                "part_time_or_full_time",
            }
        ):
            continue
        if (
            part_time
            and not full_time
            and posting_type
            not in {
                "part_time",
                "part_time_or_full_time",
            }
        ):
            continue
        if apac and not any(_contains_term(searchable, term) for term in _APAC_TERMS):
            continue
        remote_negated = (
            re.search(
                r"\b(?:non[- ]remote|not remote|no remote|on[- ]site only)\b",
                searchable,
            )
            is not None
        )
        if remote and not (
            row.get("remote") is True
            or (_contains_term(searchable, "remote") and not remote_negated)
        ):
            continue
        title_folded = title.casefold()
        score = sum(
            3 if _contains_term(title_folded, term) else 1
            for term in terms
            if _contains_term(searchable, term)
        )
        if terms and score == 0:
            continue
        posted_at = row.get("posted_at")
        if not isinstance(posted_at, datetime):
            continue
        ranked.append((score, posted_at, row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in ranked]


def search_job_posts(
    settings: SharedSettings,
    *,
    guild_id: str,
    active_channel_ids: list[str],
    lead_channel_ids: list[str],
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Search recent recruiting gigs and recent, explicitly unverified leads.

    Channel IDs are derived from Discord permissions by the caller. A lead must
    have a staged forum thread; unpublished source records are never returned.
    """
    if not guild_id or not (active_channel_ids or lead_channel_ids):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=JOB_SEARCH_MAX_AGE_DAYS)
    rows: list[dict[str, Any]] = []
    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            if active_channel_ids:
                cursor.execute(
                    trusted_sql("""
                        SELECT title, COALESCE(body_normalized, body_raw, '') AS body,
                               NULL::text AS location, NULL::boolean AS remote,
                               required_skills AS tags, posting_type, posted_at,
                               discord_thread_id AS thread_id,
                               discord_channel_id AS channel_id, 'gig' AS kind
                        FROM engagements
                        WHERE lifecycle_stage = 'pending_gig'
                          AND status = 'recruiting'
                          AND discord_guild_id = %s
                          AND discord_channel_id = ANY(%s)
                          AND discord_thread_id IS NOT NULL
                          AND posted_at >= %s
                        ORDER BY posted_at DESC LIMIT 100
                    """),
                    (guild_id, active_channel_ids, cutoff),
                )
                rows.extend(dict(row) for row in cursor.fetchall())
            if lead_channel_ids:
                cursor.execute(
                    trusted_sql("""
                        SELECT title, COALESCE(body_normalized, body_raw, '') AS body,
                               location, remote, tags, posting_type,
                               source_posted_at AS posted_at,
                               staged_discord_thread_id AS thread_id,
                               staged_discord_channel_id AS channel_id,
                               'unverified_lead' AS kind
                        FROM job_leads
                        WHERE status = 'pending'
                          AND staged_discord_guild_id = %s
                          AND staged_discord_channel_id = ANY(%s)
                          AND staged_discord_thread_id IS NOT NULL
                          AND source_posted_at >= %s
                          AND staged_at >= %s
                        ORDER BY source_posted_at DESC LIMIT 100
                    """),
                    (guild_id, lead_channel_ids, cutoff, cutoff),
                )
                rows.extend(dict(row) for row in cursor.fetchall())
    return rank_job_posts(rows, query)[: max(1, min(limit, 50))]
