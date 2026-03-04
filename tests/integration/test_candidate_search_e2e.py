"""E2E tests for candidate search against a real PostgreSQL database.

Run these tests with a live Postgres instance reachable via POSTGRES_TEST_URL
(defaults to postgresql://postgres:postgres@localhost:5432/postgres).  They are
skipped automatically when Postgres is unavailable.
"""

from __future__ import annotations

import os

import pytest
from psycopg import connect
from psycopg.types.json import Jsonb

from five08.candidate_search import search_candidates
from five08.job_match import JobRequirements
from five08.settings import SharedSettings

_POSTGRES_TEST_URL = os.environ.get(
    "POSTGRES_TEST_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)

_CREATE_PEOPLE_TABLE = """
    CREATE TABLE IF NOT EXISTS people (
        id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        crm_contact_id     TEXT        NOT NULL UNIQUE,
        name               TEXT,
        email              TEXT,
        email_508          TEXT,
        discord_user_id    TEXT,
        discord_username   TEXT,
        linkedin           TEXT,
        latest_resume_id   TEXT,
        latest_resume_name TEXT,
        is_member          BOOLEAN     NOT NULL DEFAULT false,
        sync_status        TEXT        NOT NULL DEFAULT 'active',
        seniority          TEXT,
        address_country    TEXT,
        timezone           TEXT,
        skills             TEXT[]      NOT NULL DEFAULT '{}',
        skill_attrs        JSONB       NOT NULL DEFAULT '{}',
        discord_roles      JSONB       NOT NULL DEFAULT '[]'
    )
"""

_CREATE_DISCORD_MEMBERS_TABLE = """
    CREATE TABLE IF NOT EXISTS discord_members (
        guild_id           TEXT        NOT NULL,
        discord_user_id    TEXT        NOT NULL,
        discord_username   TEXT,
        display_name       TEXT,
        roles              JSONB       NOT NULL DEFAULT '[]',
        PRIMARY KEY (guild_id, discord_user_id)
    )
"""

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_settings() -> SharedSettings:
    """Session-scoped: verify connectivity, create table, yield settings, drop table."""
    try:
        with connect(_POSTGRES_TEST_URL) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(
            f"Postgres not reachable ({exc}). Set POSTGRES_TEST_URL to run e2e tests."
        )

    with connect(_POSTGRES_TEST_URL) as conn:
        conn.execute(_CREATE_PEOPLE_TABLE)
        conn.execute(
            "ALTER TABLE people "
            "ADD COLUMN IF NOT EXISTS discord_roles JSONB NOT NULL DEFAULT '[]'"
        )
        conn.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS discord_user_id TEXT")
        conn.execute(
            "ALTER TABLE people ADD COLUMN IF NOT EXISTS discord_username TEXT"
        )
        conn.execute(_CREATE_DISCORD_MEMBERS_TABLE)

    settings = SharedSettings(postgres_url=_POSTGRES_TEST_URL, environment="test")
    yield settings

    with connect(_POSTGRES_TEST_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS discord_members")
        conn.execute("DROP TABLE IF EXISTS people")


@pytest.fixture()
def pg_db(pg_settings: SharedSettings) -> SharedSettings:
    """Function-scoped: truncate people for test isolation, return settings."""
    with connect(_POSTGRES_TEST_URL) as conn:
        conn.execute("TRUNCATE TABLE people")
        conn.execute("TRUNCATE TABLE discord_members")
    return pg_settings


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _insert(
    *,
    crm_contact_id: str,
    name: str = "Test Person",
    email: str | None = None,
    email_508: str | None = None,
    discord_user_id: str | None = None,
    discord_username: str | None = None,
    is_member: bool = False,
    sync_status: str = "active",
    seniority: str | None = None,
    address_country: str | None = None,
    timezone: str | None = None,
    skills: list[str] | None = None,
    skill_attrs: dict | None = None,
    discord_roles: list[str] | None = None,
) -> None:
    with connect(_POSTGRES_TEST_URL) as conn:
        conn.execute(
            """
            INSERT INTO people (
                crm_contact_id, name, email, email_508,
                discord_user_id, discord_username,
                is_member, sync_status, seniority,
                address_country, timezone, skills, skill_attrs, discord_roles
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                crm_contact_id,
                name,
                email,
                email_508,
                discord_user_id,
                discord_username,
                is_member,
                sync_status,
                seniority,
                address_country,
                timezone,
                skills or [],
                Jsonb(skill_attrs or {}),
                Jsonb(discord_roles or []),
            ),
        )


def _insert_discord_member(
    *,
    discord_user_id: str,
    guild_id: str = "guild-1",
    discord_username: str | None = None,
    display_name: str | None = None,
    roles: list[str] | None = None,
) -> None:
    with connect(_POSTGRES_TEST_URL) as conn:
        conn.execute(
            """
            INSERT INTO discord_members (
                discord_user_id, guild_id, discord_username, display_name, roles
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (
                discord_user_id,
                guild_id,
                discord_username,
                display_name,
                Jsonb(roles or []),
            ),
        )


def _reqs(**overrides) -> JobRequirements:
    defaults: dict = dict(
        required_skills=["python"],
        preferred_skills=[],
        discord_role_types=[],
        seniority=None,
        location_type=None,
        preferred_timezones=[],
        raw_location_text=None,
        title=None,
    )
    defaults.update(overrides)
    return JobRequirements(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSearchCandidatesE2E:
    def test_matches_candidate_with_required_skill(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="c1", skills=["python", "django"])

        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        assert len(results) == 1
        assert results[0].crm_contact_id == "c1"
        assert "python" in results[0].matched_required_skills

    def test_excludes_candidate_without_required_skill(
        self, pg_db: SharedSettings
    ) -> None:
        _insert(crm_contact_id="c1", skills=["javascript", "react"])

        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        assert results == []

    def test_no_required_skills_returns_empty(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="c1", skills=["python"])

        results = search_candidates(pg_db, _reqs(required_skills=[]))

        assert results == []

    def test_member_ranked_before_prospect(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="prospect", skills=["python"], is_member=False)
        _insert(crm_contact_id="member", skills=["python"], is_member=True)

        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        assert len(results) == 2
        assert results[0].crm_contact_id == "member"
        assert results[1].crm_contact_id == "prospect"

    def test_inactive_candidate_excluded(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="active", skills=["python"], sync_status="active")
        _insert(crm_contact_id="gone", skills=["python"], sync_status="missing_in_crm")

        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        ids = [r.crm_contact_id for r in results]
        assert "active" in ids
        assert "gone" not in ids

    def test_us_only_filter_excludes_non_us(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="uk_dev", skills=["python"], address_country="UK")
        _insert(crm_contact_id="us_dev", skills=["python"], address_country="US")

        results = search_candidates(
            pg_db, _reqs(required_skills=["python"], location_type="us_only")
        )

        ids = [r.crm_contact_id for r in results]
        assert "us_dev" in ids
        assert "uk_dev" not in ids

    def test_us_only_accepts_all_us_country_variants(
        self, pg_db: SharedSettings
    ) -> None:
        _insert(crm_contact_id="usa", skills=["python"], address_country="USA")
        _insert(
            crm_contact_id="united_states",
            skills=["python"],
            address_country="United States",
        )

        results = search_candidates(
            pg_db, _reqs(required_skills=["python"], location_type="us_only")
        )

        ids = [r.crm_contact_id for r in results]
        assert "usa" in ids
        assert "united_states" in ids

    def test_more_required_skills_matched_ranks_higher(
        self, pg_db: SharedSettings
    ) -> None:
        _insert(crm_contact_id="one", skills=["python"])
        _insert(crm_contact_id="two", skills=["python", "django"])

        results = search_candidates(pg_db, _reqs(required_skills=["python", "django"]))

        assert results[0].crm_contact_id == "two"
        assert results[1].crm_contact_id == "one"

    def test_preferred_skills_boost_ranking(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="basic", skills=["python"])
        _insert(crm_contact_id="preferred", skills=["python", "django"])

        results = search_candidates(
            pg_db, _reqs(required_skills=["python"], preferred_skills=["django"])
        )

        assert results[0].crm_contact_id == "preferred"
        assert len(results[0].matched_preferred_skills) == 1

    def test_skill_strength_score_ranks_higher(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="strong", skills=["python"], skill_attrs={"python": "5"})
        _insert(crm_contact_id="weak", skills=["python"], skill_attrs={"python": "1"})

        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        assert results[0].crm_contact_id == "strong"
        assert results[0].required_skill_score == 5
        assert results[1].crm_contact_id == "weak"
        assert results[1].required_skill_score == 1

    def test_timezone_match_ranks_higher(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="tz_other", skills=["python"], timezone="Europe/Berlin")
        _insert(
            crm_contact_id="tz_match", skills=["python"], timezone="America/New_York"
        )

        results = search_candidates(
            pg_db,
            _reqs(required_skills=["python"], preferred_timezones=["America/New_York"]),
        )

        assert results[0].crm_contact_id == "tz_match"

    def test_limit_respected(self, pg_db: SharedSettings) -> None:
        for i in range(5):
            _insert(crm_contact_id=f"c{i}", skills=["python"])

        results = search_candidates(pg_db, _reqs(required_skills=["python"]), limit=3)

        assert len(results) == 3

    def test_matched_skills_populated_correctly(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="c1", skills=["python", "django", "react"])

        results = search_candidates(
            pg_db,
            _reqs(required_skills=["python", "django"], preferred_skills=["react"]),
        )

        assert len(results) == 1
        c = results[0]
        assert set(c.matched_required_skills) == {"python", "django"}
        assert c.matched_preferred_skills == ["react"]

    def test_empty_db_returns_empty_list(self, pg_db: SharedSettings) -> None:
        results = search_candidates(pg_db, _reqs(required_skills=["python"]))

        assert results == []

    def test_discord_role_match_includes_candidate(self, pg_db: SharedSettings) -> None:
        _insert(crm_contact_id="backend", discord_user_id="111")
        _insert(crm_contact_id="frontend", discord_user_id="222")
        _insert_discord_member(discord_user_id="111", roles=["Backend"])
        _insert_discord_member(discord_user_id="222", roles=["Frontend"])

        results = search_candidates(
            pg_db, _reqs(required_skills=[], discord_role_types=["Backend"])
        )

        assert len(results) == 1
        assert results[0].crm_contact_id == "backend"
        assert results[0].matched_discord_roles == ["Backend"]

    def test_discord_role_match_includes_unlinked_member(
        self, pg_db: SharedSettings
    ) -> None:
        _insert_discord_member(discord_user_id="333", roles=["Backend"])

        results = search_candidates(
            pg_db, _reqs(required_skills=[], discord_role_types=["Backend"])
        )

        assert len(results) == 1
        assert results[0].discord_user_id == "333"
        assert results[0].has_crm_link is False
