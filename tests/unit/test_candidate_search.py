"""Unit tests for candidate_search helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from five08.candidate_search import _seniority_score, search_candidates
from five08.job_match import JobRequirements


# ---------------------------------------------------------------------------
# _seniority_score
# ---------------------------------------------------------------------------


def test_seniority_score_exact_match() -> None:
    assert _seniority_score("senior", "senior") == 1.0


def test_seniority_score_one_level_above() -> None:
    # midlevel candidate for junior role → 0.7
    assert _seniority_score("midlevel", "junior") == 0.7
    # senior candidate for midlevel role → 0.7
    assert _seniority_score("senior", "midlevel") == 0.7


def test_seniority_score_two_levels_above_returns_zero() -> None:
    assert _seniority_score("senior", "junior") == 0.0


def test_seniority_score_underqualified_returns_zero() -> None:
    assert _seniority_score("junior", "senior") == 0.0


def test_seniority_score_none_required_returns_zero() -> None:
    assert _seniority_score("senior", None) == 0.0


def test_seniority_score_none_candidate_returns_zero() -> None:
    assert _seniority_score(None, "senior") == 0.0


def test_seniority_score_both_none_returns_zero() -> None:
    assert _seniority_score(None, None) == 0.0


def test_seniority_score_unknown_string_returns_zero() -> None:
    assert _seniority_score("lead", "senior") == 0.0
    assert _seniority_score("senior", "lead") == 0.0


# ---------------------------------------------------------------------------
# search_candidates — early-return on empty required skills
# ---------------------------------------------------------------------------


def test_search_candidates_returns_empty_when_no_required_skills() -> None:
    reqs = JobRequirements(
        required_skills=[],
        preferred_skills=["react"],
        seniority="senior",
        location_type=None,
        preferred_timezones=[],
        raw_location_text=None,
        title=None,
    )
    settings = MagicMock()
    result = search_candidates(settings, reqs)
    assert result == []


# ---------------------------------------------------------------------------
# search_candidates — DB rows → CandidateMatch mapping and secondary sort
# ---------------------------------------------------------------------------


def _make_row(**overrides: object) -> dict:
    """Build a minimal fake DB row dict."""
    base: dict = {
        "crm_contact_id": "c1",
        "name": "Alice",
        "email_508": "alice@508.dev",
        "email": "alice@example.com",
        "linkedin": None,
        "latest_resume_id": None,
        "latest_resume_name": None,
        "is_member": True,
        "seniority": "senior",
        "address_country": "US",
        "timezone": "America/New_York",
        "skills": ["python", "django"],
        "skill_attrs": {"python": "4"},
        "required_matched": 2,
        "required_skill_score": 5,
        "preferred_matched": 0,
        "timezone_matched": 1,
    }
    base.update(overrides)
    return base


def _make_requirements(**overrides: object) -> JobRequirements:
    defaults = dict(
        required_skills=["python"],
        preferred_skills=[],
        seniority=None,
        location_type=None,
        preferred_timezones=[],
        raw_location_text=None,
        title=None,
    )
    defaults.update(overrides)
    return JobRequirements(**defaults)  # type: ignore[arg-type]


def _patch_db(rows: list[dict]) -> MagicMock:
    """Return a context-manager mock that yields ``rows`` from fetchall()."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)

    return conn


def test_search_candidates_maps_row_to_candidate_match() -> None:
    row = _make_row(crm_contact_id="abc", name="Bob", seniority="senior")
    conn = _patch_db([row])

    reqs = _make_requirements(required_skills=["python"], seniority="senior")
    settings = MagicMock()

    with patch("five08.candidate_search.get_postgres_connection", return_value=conn):
        results = search_candidates(settings, reqs)

    assert len(results) == 1
    c = results[0]
    assert c.crm_contact_id == "abc"
    assert c.name == "Bob"
    assert c.seniority == "senior"
    assert c.seniority_score == 1.0
    assert "python" in c.matched_required_skills


def test_search_candidates_secondary_sort_members_first() -> None:
    member_row = _make_row(
        crm_contact_id="m1",
        is_member=True,
        required_matched=1,
        required_skill_score=1,
    )
    prospect_row = _make_row(
        crm_contact_id="p1",
        is_member=False,
        required_matched=2,
        required_skill_score=5,
    )
    # DB already returns prospect first (simulating wrong SQL order)
    conn = _patch_db([prospect_row, member_row])

    reqs = _make_requirements(required_skills=["python"])
    settings = MagicMock()

    with patch("five08.candidate_search.get_postgres_connection", return_value=conn):
        results = search_candidates(settings, reqs)

    # Member must appear before prospect regardless of skill scores
    assert results[0].crm_contact_id == "m1"
    assert results[1].crm_contact_id == "p1"


def test_search_candidates_secondary_sort_required_skills_desc() -> None:
    low_row = _make_row(
        crm_contact_id="low",
        is_member=False,
        skills=["python"],
        required_matched=1,
        required_skill_score=1,
    )
    high_row = _make_row(
        crm_contact_id="high",
        is_member=False,
        skills=["python", "django"],
        required_matched=2,
        required_skill_score=4,
    )
    conn = _patch_db([low_row, high_row])

    reqs = _make_requirements(required_skills=["python", "django"])
    settings = MagicMock()

    with patch("five08.candidate_search.get_postgres_connection", return_value=conn):
        results = search_candidates(settings, reqs)

    assert results[0].crm_contact_id == "high"
    assert results[1].crm_contact_id == "low"


def test_search_candidates_seniority_score_applied() -> None:
    exact_row = _make_row(crm_contact_id="exact", seniority="senior")
    above_row = _make_row(crm_contact_id="above", seniority="staff")
    mismatch_row = _make_row(crm_contact_id="miss", seniority="junior")

    conn = _patch_db([exact_row, above_row, mismatch_row])
    reqs = _make_requirements(required_skills=["python"], seniority="senior")
    settings = MagicMock()

    with patch("five08.candidate_search.get_postgres_connection", return_value=conn):
        results = search_candidates(settings, reqs)

    scores = {c.crm_contact_id: c.seniority_score for c in results}
    assert scores["exact"] == 1.0
    assert scores["above"] == 0.7
    assert scores["miss"] == 0.0


def test_search_candidates_empty_db_returns_empty_list() -> None:
    conn = _patch_db([])
    reqs = _make_requirements(required_skills=["python"])
    settings = MagicMock()

    with patch("five08.candidate_search.get_postgres_connection", return_value=conn):
        results = search_candidates(settings, reqs)

    assert results == []
