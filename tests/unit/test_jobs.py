"""Unit tests for jobs cog match formatting."""

from __future__ import annotations

from unittest.mock import Mock
from types import SimpleNamespace

from five08.discord_bot.cogs.jobs import JobsCog


def _make_candidate(**overrides: object) -> SimpleNamespace:
    base = {
        "is_member": False,
        "name": "Display Name",
        "crm_name": None,
        "crm_contact_id": None,
        "has_crm_link": False,
        "discord_user_id": None,
        "discord_username": None,
        "latest_resume_id": None,
        "latest_resume_name": None,
        "matched_required_skills": [],
        "matched_hard_required_skills": [],
        "matched_soft_required_skills": [],
        "matched_discord_roles": [],
        "matched_preferred_skills": [],
        "missing_hard_required_skills": [],
        "evidence_signals": [],
        "llm_fit_score": None,
        "llm_summary": None,
        "llm_risks": [],
        "llm_missing_requirements": [],
        "match_score": 0.0,
        "seniority": None,
        "address_city": None,
        "address_state": None,
        "address_country": None,
        "timezone": None,
        "linkedin": None,
        "github_username": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_match_candidate_lines_uses_crm_name_and_discord_username() -> None:
    candidate = _make_candidate(
        is_member=True,
        name="Server Nickname",
        crm_name="Caleb",
        crm_contact_id="abc",
        has_crm_link=True,
        discord_username="caleb",
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert len(lines) == 1
    assert "Discord ID" not in lines[0]
    assert (
        "1. **[Member]** [Caleb](<https://crm.example/#Contact/view/abc>)" in lines[0]
    )
    assert "`@caleb`" in lines[0]


def test_build_match_candidate_lines_handles_non_string_names() -> None:
    candidate = _make_candidate(
        is_member=True,
        name="Server Nickname",
        crm_name=Mock(),
        discord_username=Mock(),
        crm_contact_id="abc",
        has_crm_link=True,
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert len(lines) == 1
    assert (
        "1. **[Member]** [Server Nickname](<https://crm.example/#Contact/view/abc>)"
        in lines[0]
    )
    assert "`@" not in lines[0]


def test_build_match_candidate_lines_strips_whitespace_names_and_usernames() -> None:
    candidate = _make_candidate(
        is_member=True,
        name="  Server Nickname  ",
        crm_name="  ",
        crm_contact_id="abc",
        has_crm_link=True,
        discord_username="  @caleb  ",
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert len(lines) == 1
    assert (
        "1. **[Member]** [Server Nickname](<https://crm.example/#Contact/view/abc>)"
        in lines[0]
    )
    assert "`@caleb`" in lines[0]


def test_build_match_candidate_lines_omits_crm_link_for_prospect() -> None:
    candidate = _make_candidate(
        is_member=False,
        name="Prospect Name",
        has_crm_link=False,
        discord_username="michaelmwu",
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert len(lines) == 1
    assert "1. [Prospect] Prospect Name" in lines[0]
    assert "`@michaelmwu`" in lines[0]
    assert "<https://crm.example/#Contact/view/" not in lines[0]
    assert "<@" not in lines[0]


def test_build_match_candidate_lines_keeps_name_discord_and_linkedin_on_first_line() -> (
    None
):
    candidate = _make_candidate(
        is_member=True,
        crm_name="Robert Anthony Bellamy",
        discord_username="robertanthonybellamy",
        linkedin="https://linkedin.com/in/robertanthonybellamy",
        match_score=31.0,
        seniority="midlevel",
        address_city="Seattle",
        address_state="Washington",
        address_country="US",
        timezone="UTC-08:00",
        matched_required_skills=["amazon web services"],
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert len(lines) == 1
    first_line, second_line, third_line = lines[0].split("\n")
    assert (
        first_line == "1. **[Member]** Robert Anthony Bellamy `@robertanthonybellamy` "
        "[LinkedIn](<https://linkedin.com/in/robertanthonybellamy>)"
    )
    assert "score: 31.0" in second_line
    assert "seniority: **Mid-level**" in second_line
    assert "location: **Seattle, Washington, US** · tz: `UTC-08:00`" in third_line


def test_build_match_candidate_lines_includes_evidence_and_llm_notes() -> None:
    candidate = _make_candidate(
        crm_name="Jamie",
        matched_hard_required_skills=["webflow"],
        matched_soft_required_skills=["figma"],
        evidence_signals=["hard skill `webflow` (strength 5)", "resume `jamie.pdf`"],
        llm_fit_score=92.0,
        llm_summary="Strong Webflow fit but portfolio proof is still thin.",
        llm_missing_requirements=["live webflow projects"],
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert "LLM fit: 92/100" in lines[0]
    assert "hard: `webflow`" in lines[0]
    assert "soft: `figma`" in lines[0]
    assert (
        "evidence: hard skill `webflow` (strength 5) · resume `jamie.pdf`" in lines[0]
    )
    assert "summary: Strong Webflow fit but portfolio proof is still thin." in lines[0]
    assert "missing: `live webflow projects`" in lines[0]
