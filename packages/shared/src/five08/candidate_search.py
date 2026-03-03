"""PostgreSQL-based candidate search and ranking for job matching."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from psycopg.rows import dict_row

from five08.job_match import SENIORITY_ORDER, JobRequirements
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings

logger = logging.getLogger(__name__)

# US country strings we accept as-is (lowercased for comparison)
_US_COUNTRY_VALUES = frozenset(
    {"us", "usa", "united states", "united states of america"}
)


@dataclass(frozen=True)
class CandidateMatch:
    """One ranked candidate result from the people cache."""

    crm_contact_id: str
    name: str | None
    email_508: str | None
    email: str | None
    linkedin: str | None
    latest_resume_id: str | None
    latest_resume_name: str | None
    is_member: bool
    seniority: str | None
    address_country: str | None
    timezone: str | None
    matched_required_skills: list[str] = field(default_factory=list)
    matched_preferred_skills: list[str] = field(default_factory=list)
    required_skill_score: int = 0  # sum of strength attrs for matched required skills
    seniority_score: float = 0.0  # 1.0 exact, 0.7 one level up, 0.0 mismatch or unknown


def _seniority_score(candidate: str | None, required: str | None) -> float:
    """Score how well a candidate's seniority matches the requirement."""
    if required is None or candidate is None:
        return 0.0
    if candidate == required:
        return 1.0
    try:
        req_idx = SENIORITY_ORDER.index(required)
        cand_idx = SENIORITY_ORDER.index(candidate)
    except ValueError:
        return 0.0
    # One level above the requirement still works (e.g. midlevel for junior role)
    if cand_idx == req_idx + 1:
        return 0.7
    return 0.0


def search_candidates(
    settings: SharedSettings,
    requirements: JobRequirements,
    *,
    limit: int = 10,
) -> list[CandidateMatch]:
    """Return ranked candidates from the people cache for the given job requirements.

    Ranking priority:
    1. Members before prospects.
    2. Location match (hard filter when us_only, else soft signal).
    3. Required skill count matched.
    4. Required skill strength score (sum of skill_attrs values).
    5. Preferred skill count matched.
    6. Seniority score (applied in Python after the query).
    """
    required_skills = requirements.required_skills
    preferred_skills = requirements.preferred_skills

    if not required_skills:
        logger.warning(
            "search_candidates called with no required skills; returning empty list"
        )
        return []

    us_only = requirements.location_type == "us_only"

    # Build the query. We use unnest + lateral subselects so a single round-trip
    # handles scoring without pulling all rows into Python.
    query = """
        WITH
          req AS (SELECT %s::text[] AS skills),
          pref AS (SELECT %s::text[] AS skills)
        SELECT
            p.crm_contact_id,
            p.name,
            p.email_508,
            p.email,
            p.linkedin,
            p.latest_resume_id,
            p.latest_resume_name,
            p.is_member,
            p.seniority,
            p.address_country,
            p.timezone,
            p.skills,
            p.skill_attrs,
            -- How many required skills this candidate has
            (SELECT count(*)::int
             FROM unnest(p.skills) s
             WHERE s = ANY((SELECT skills FROM req))
            ) AS required_matched,
            -- Weighted score: sum of strength attrs for matched required skills (default 1)
            (SELECT COALESCE(
                SUM(LEAST(COALESCE((p.skill_attrs ->> s)::int, 1), 5)), 0
             )::int
             FROM unnest(p.skills) s
             WHERE s = ANY((SELECT skills FROM req))
            ) AS required_skill_score,
            -- How many preferred skills this candidate has
            (SELECT count(*)::int
             FROM unnest(p.skills) s
             WHERE s = ANY((SELECT skills FROM pref))
            ) AS preferred_matched
        FROM people p
        WHERE p.sync_status = 'active'
          -- Must have at least one required skill
          AND p.skills && (SELECT skills FROM req)
          -- Hard location filter when us_only
          AND (
            NOT %s
            OR LOWER(COALESCE(p.address_country, '')) = ANY(%s::text[])
          )
        ORDER BY
            p.is_member DESC,
            required_matched DESC,
            required_skill_score DESC,
            preferred_matched DESC
        LIMIT %s
    """

    us_values = list(_US_COUNTRY_VALUES)

    with get_postgres_connection(settings) as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                query,
                (
                    required_skills,
                    preferred_skills,
                    us_only,
                    us_values,
                    limit,
                ),
            )
            rows = cursor.fetchall()

    results: list[CandidateMatch] = []
    for row in rows:
        candidate_skills: list[str] = row.get("skills") or []
        required_set = set(required_skills)
        preferred_set = set(preferred_skills)

        matched_req = [s for s in candidate_skills if s in required_set]
        matched_pref = [s for s in candidate_skills if s in preferred_set]

        sen_score = _seniority_score(row.get("seniority"), requirements.seniority)

        results.append(
            CandidateMatch(
                crm_contact_id=row["crm_contact_id"],
                name=row.get("name"),
                email_508=row.get("email_508"),
                email=row.get("email"),
                linkedin=row.get("linkedin"),
                latest_resume_id=row.get("latest_resume_id"),
                latest_resume_name=row.get("latest_resume_name"),
                is_member=bool(row.get("is_member")),
                seniority=row.get("seniority"),
                address_country=row.get("address_country"),
                timezone=row.get("timezone"),
                matched_required_skills=matched_req,
                matched_preferred_skills=matched_pref,
                required_skill_score=row.get("required_skill_score") or 0,
                seniority_score=sen_score,
            )
        )

    # Secondary sort: among equal SQL scores, rank by seniority alignment.
    # This is stable (preserves SQL order within same seniority bucket).
    results.sort(
        key=lambda c: (not c.is_member, -c.required_skill_score, -c.seniority_score)
    )

    return results
