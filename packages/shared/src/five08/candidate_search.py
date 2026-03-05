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

    crm_contact_id: str | None
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
    discord_user_id: str | None = None
    has_crm_link: bool = True
    matched_required_skills: list[str] = field(default_factory=list)
    matched_preferred_skills: list[str] = field(default_factory=list)
    matched_discord_roles: list[str] = field(default_factory=list)
    match_score: float = 0.0
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
    min_match_score: float = 0.0,
) -> list[CandidateMatch]:
    """Return ranked candidates from the people cache for the given job requirements.

    Ranking priority:
    1. Members before prospects.
    2. US-only location restriction when enabled (hard filter; non-US candidates excluded).
    3. Match score (weighted score from skills + CRM signals).
    4. Timezone match (soft signal; 1 when candidate timezone is in preferred_timezones).
    5. Required skill count matched.
    6. Discord role type matched (1 if any discord role matches the required role types).
    7. Required skill strength score (sum of skill_attrs values).
    8. Preferred skill count matched.
    9. Seniority score (applied in Python after the query).

    Candidates are included if they match ANY required skill OR any discord role type.
    A minimum final match score can be requested via min_match_score.
    """
    required_skills = requirements.required_skills
    preferred_skills = requirements.preferred_skills
    role_types = requirements.discord_role_types

    if not required_skills and not role_types:
        logger.warning(
            "search_candidates called with no required skills or role types; returning empty list"
        )
        return []

    us_only = requirements.location_type == "us_only"
    preferred_timezones = requirements.preferred_timezones or []

    # Build the query. We use unnest + lateral subselects so a single round-trip
    # handles scoring without pulling all rows into Python.
    # Candidates match if they have ANY required skill OR any discord role type.
    query = """
        WITH
          req AS (SELECT %s::text[] AS skills),
          pref AS (SELECT %s::text[] AS skills),
          rtypes AS (SELECT %s::text[] AS types),
          dm_agg AS (
            SELECT
                dm_raw.discord_user_id,
                MAX(dm_raw.discord_username) AS discord_username,
                MAX(dm_raw.display_name) AS display_name,
                COALESCE(
                    jsonb_agg(DISTINCT role) FILTER (WHERE role IS NOT NULL),
                    '[]'::jsonb
                ) AS roles
            FROM discord_members dm_raw
            LEFT JOIN LATERAL jsonb_array_elements_text(dm_raw.roles) AS role ON true
            GROUP BY dm_raw.discord_user_id
          ),
          scored AS (
            SELECT
                p.crm_contact_id AS crm_contact_id,
                COALESCE(dm.display_name, dm.discord_username, p.name) AS name,
                p.email_508,
                p.email,
                p.linkedin,
                p.latest_resume_id,
                p.latest_resume_name,
                COALESCE(p.is_member, false) AS is_member,
                p.seniority,
                p.address_country,
                p.timezone,
                COALESCE(p.skills, '{}'::text[]) AS skills,
                COALESCE(p.skill_attrs, '{}'::jsonb) AS skill_attrs,
                COALESCE(dm.roles, p.discord_roles, '[]'::jsonb) AS discord_roles,
                COALESCE(p.discord_user_id, dm.discord_user_id) AS discord_user_id,
                (p.crm_contact_id IS NOT NULL) AS has_crm_link,
                -- How many required skills this candidate has
                (SELECT count(*)::int
                 FROM unnest(COALESCE(p.skills, '{}'::text[])) s
                 WHERE s IN (SELECT unnest(skills) FROM req)
                ) AS required_matched,
                -- Weighted score: sum of strength attrs for matched required skills (default 1)
                (SELECT COALESCE(
                    SUM(
                        LEAST(
                            COALESCE((COALESCE(p.skill_attrs, '{}'::jsonb) ->> s)::int, 1),
                            5
                        )
                    ),
                    0
                 )::int
                 FROM unnest(COALESCE(p.skills, '{}'::text[])) s
                 WHERE s IN (SELECT unnest(skills) FROM req)
                ) AS required_skill_score,
                -- How many preferred skills this candidate has
                (SELECT count(*)::int
                 FROM unnest(COALESCE(p.skills, '{}'::text[])) s
                 WHERE s IN (SELECT unnest(skills) FROM pref)
                ) AS preferred_matched,
                -- Timezone match: 1 if candidate timezone is in the preferred list
                CASE
                  WHEN %s::text[] = '{}'::text[] THEN 0
                  ELSE (COALESCE(p.timezone, '') = ANY(%s::text[]))::int
                END AS timezone_matched,
                -- Discord role match: 1 if any discord role matches the required role types
                CASE
                  WHEN array_length(rtypes.types, 1) IS NULL THEN 0
                  WHEN EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(
                        COALESCE(dm.roles, p.discord_roles, '[]'::jsonb)
                    ) r
                    WHERE r = ANY(rtypes.types)
                  ) THEN 1
                  ELSE 0
                END AS discord_role_matched
            FROM people p
            FULL OUTER JOIN dm_agg dm ON dm.discord_user_id = p.discord_user_id
            CROSS JOIN rtypes
            WHERE (p.sync_status = 'active' OR p.sync_status IS NULL)
              -- Must match at least one required skill OR one discord role type
              AND (
                COALESCE(p.skills, '{}'::text[]) && (SELECT skills FROM req)
                OR EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(
                      COALESCE(dm.roles, p.discord_roles, '[]'::jsonb)
                  ) r
                  WHERE r = ANY(rtypes.types)
                )
              )
              -- Hard location filter when us_only
              AND (
                NOT %s
                OR LOWER(COALESCE(p.address_country, '')) = ANY(%s::text[])
              )
          )
        SELECT
            crm_contact_id,
            name,
            email_508,
            email,
            linkedin,
            latest_resume_id,
            latest_resume_name,
            is_member,
            seniority,
            address_country,
            timezone,
            skills,
            skill_attrs,
            discord_roles,
            discord_user_id,
            has_crm_link,
            required_matched,
            required_skill_score,
            preferred_matched,
            timezone_matched,
            discord_role_matched,
            (
              required_matched * 10
              + required_skill_score * 2
              + preferred_matched * 2
              + timezone_matched * 2
              + (discord_role_matched * 2)
              + CASE WHEN is_member THEN 4 ELSE 0 END
              + CASE WHEN has_crm_link THEN 6 ELSE 0 END
            ) AS match_score
        FROM scored
        ORDER BY
            is_member DESC,
            has_crm_link DESC,
            match_score DESC,
            timezone_matched DESC,
            required_matched DESC,
            discord_role_matched DESC,
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
                    role_types,
                    preferred_timezones,
                    preferred_timezones,
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
        role_types_set = set(role_types)

        matched_req = [s for s in candidate_skills if s in required_set]
        matched_pref = [s for s in candidate_skills if s in preferred_set]

        raw_discord_roles = row.get("discord_roles") or []
        matched_discord = [
            r for r in raw_discord_roles if isinstance(r, str) and r in role_types_set
        ]

        sen_score = _seniority_score(row.get("seniority"), requirements.seniority)
        raw_match_score = row.get("match_score")
        if raw_match_score is None:
            required_matched = row.get("required_matched") or 0
            required_skill_score = row.get("required_skill_score") or 0
            preferred_matched = row.get("preferred_matched") or 0
            timezone_matched = row.get("timezone_matched") or 0
            discord_role_matched = row.get("discord_role_matched") or 0
            is_member = bool(row.get("is_member"))
            has_crm_link = bool(row.get("has_crm_link", True))
            raw_match_score = (
                required_matched * 10
                + required_skill_score * 2
                + preferred_matched * 2
                + timezone_matched * 2
                + (discord_role_matched * 2)
                + (4 if is_member else 0)
                + (6 if has_crm_link else 0)
            )
        base_score = float(raw_match_score or 0)
        match_score = base_score + (sen_score * 3)

        results.append(
            CandidateMatch(
                crm_contact_id=row.get("crm_contact_id"),
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
                discord_user_id=row.get("discord_user_id"),
                has_crm_link=bool(row.get("has_crm_link", True)),
                matched_required_skills=matched_req,
                matched_preferred_skills=matched_pref,
                matched_discord_roles=matched_discord,
                match_score=match_score,
                required_skill_score=row.get("required_skill_score") or 0,
                seniority_score=sen_score,
            )
        )

    if min_match_score > 0:
        results = [c for c in results if c.match_score >= min_match_score]

    # Secondary sort: preserve primary SQL ranking, break ties with seniority alignment.
    results.sort(
        key=lambda c: (
            not c.is_member,
            not c.has_crm_link,
            -c.match_score,
            -len(c.matched_required_skills),
            -len(c.matched_discord_roles),
            -c.required_skill_score,
            -len(c.matched_preferred_skills),
            -c.seniority_score,
        )
    )

    return results
