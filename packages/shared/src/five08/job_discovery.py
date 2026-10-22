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
    "post posts send close near opening openings recommend match suitable "
    "opportunities opportunity find search fit fitting my hours timezone time zone "
    "please remote full part fulltime parttime contract contracts contractor "
    "contractors freelance freelancer freelancers freelancing position positions".split()
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
_PROXIMITY_RE = re.compile(r"\b(?:close\s+to|near(?:\s+to)?)\s+me\b", re.I)
_LOCATION_RE = re.compile(
    r"\b(?:in|around)\s+(?!(?:me|my)\b)"
    r"([a-z][\w+.-]*(?:\s+(?!(?:for|with|and|jobs?|roles?|gigs?|hours?)\b)"
    r"[a-z][\w+.-]*){0,2})",
    re.I,
)
_TIME_ZONE_RE = re.compile(r"\b(?:apac|jst|aest|sgt|(?:utc|gmt)[+-]?\d{1,2})\b", re.I)
_REMOTE_NEGATION_RE = re.compile(
    r"\b(?:non[- ]remote|not remote|no remote|on[- ]site only)\b", re.I
)


def parse_proximity_location(query: str) -> tuple[bool, str | None]:
    """Return whether a nearby search has an explicit place or time zone."""
    proximity = _PROXIMITY_RE.search(query)
    if proximity is None:
        return False, None
    remainder = query[: proximity.start()] + query[proximity.end() :]
    if location := _LOCATION_RE.search(remainder):
        return True, location.group(1).casefold()
    if time_zone := _TIME_ZONE_RE.search(remainder):
        return True, time_zone.group().casefold()
    return True, None


def _search_terms(query: str) -> tuple[list[str], bool, bool | None]:
    lowered = query.casefold()
    apac = bool(re.search(r"\b(?:apac|asia[ -]pacific|asian)\b", lowered))
    remote: bool | None = None
    if _REMOTE_NEGATION_RE.search(lowered):
        remote = False
    elif re.search(r"\bremote\b", lowered):
        remote = True
    lexical_query = _REMOTE_NEGATION_RE.sub(" ", lowered)
    words = [word.strip(".") for word in re.findall(r"[a-z0-9+#.]+", lexical_query)]
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
    proximity_requested, proximity_location = parse_proximity_location(query)
    if proximity_requested and proximity_location is None:
        return []
    if proximity_location:
        location_terms = set(re.findall(r"[a-z0-9+#.]+", proximity_location))
        terms = [term for term in terms if term not in location_terms]
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
        if proximity_location:
            location_text = (location or f"{title} {body}").casefold()
            location_matches = (
                any(_contains_term(location_text, term) for term in _APAC_TERMS)
                if proximity_location in {"apac", "asia", "asia pacific"}
                else _contains_term(location_text, proximity_location)
            )
            if not location_matches:
                continue
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
        remote_negated = _REMOTE_NEGATION_RE.search(searchable) is not None
        is_remote = not remote_negated and (
            row.get("remote") is True or _contains_term(searchable, "remote")
        )
        if remote is not None and is_remote is not remote:
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
