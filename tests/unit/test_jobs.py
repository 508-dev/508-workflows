"""Unit tests for jobs cog match formatting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
from unittest.mock import patch

from five08.discord_bot.cogs.jobs import JobsCog
from five08.job_match import CandidateRerankResult, JobRequirements


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


def test_build_candidate_search_plan_relaxes_hard_requirements() -> None:
    requirements = JobRequirements(
        hard_required_skills=["webflow", "figma", "hubspot"],
        discord_role_types=["Designer"],
    )

    plan = JobsCog._build_candidate_search_plan(requirements, min_match_score=8.0)

    assert len(plan) == 3
    assert plan[0][0].hard_required_skills == ["webflow", "figma"]
    assert plan[0][1] == 8.0
    assert plan[0][2] is None
    assert plan[1][0].hard_required_skills == ["webflow"]
    assert plan[1][0].soft_required_skills == ["figma", "hubspot"]
    assert "anchor hard need `webflow`" in (plan[1][2] or "")
    assert plan[2][0].hard_required_skills == []
    assert plan[2][0].soft_required_skills == ["webflow", "figma", "hubspot"]
    assert plan[2][1] == 0.0
    assert "any relevant required skill" in (plan[2][2] or "")


def test_build_candidate_search_plan_keeps_language_requirements_hard() -> None:
    requirements = JobRequirements(
        hard_required_skills=["japanese", "next engine"],
        soft_required_skills=["shopify", "wms"],
        required_languages=["japanese"],
        discord_role_types=["Backend"],
    )

    plan = JobsCog._build_candidate_search_plan(requirements, min_match_score=8.0)

    assert len(plan) == 2
    for planned_requirements, *_ in plan:
        assert "japanese" in planned_requirements.hard_required_skills
    assert plan[0][0].hard_required_skills == ["japanese", "next engine"]
    assert plan[1][0].hard_required_skills == ["japanese"]
    assert plan[1][0].soft_required_skills == ["next engine", "shopify", "wms"]
    assert "language gates mandatory" in (plan[1][2] or "")


def test_build_candidate_search_plan_keeps_llm_required_languages_hard() -> None:
    requirements = JobRequirements(
        hard_required_skills=["spanish", "salesforce"],
        soft_required_skills=["crm"],
        required_languages=["spanish"],
    )

    plan = JobsCog._build_candidate_search_plan(requirements, min_match_score=8.0)

    assert plan[-1][0].hard_required_skills == ["spanish"]
    assert plan[-1][0].soft_required_skills == [
        "salesforce",
        "customer relationship management",
    ]


def test_message_expresses_gig_interest_is_conservative() -> None:
    assert JobsCog._message_expresses_gig_interest("I'm interested in this")
    assert JobsCog._message_expresses_gig_interest("I can help with this one")
    assert JobsCog._message_expresses_gig_interest("available for a quick chat")
    assert not JobsCog._message_expresses_gig_interest("@someone ?")
    assert not JobsCog._message_expresses_gig_interest("looks interesting")
    assert not JobsCog._message_expresses_gig_interest("not available for this")


def test_build_job_match_header_uses_single_skills_line_when_hard_is_empty() -> None:
    cog = JobsCog(Mock())
    requirements = JobRequirements(
        hard_required_skills=[],
        soft_required_skills=["product design", "ux design"],
        title="Product Designer",
    )

    header_lines, *_ = cog._build_job_match_header_and_mentions(
        requirements=requirements,
        candidates_count=2,
        guild=None,
        search_note="Search note: broadened matching.",
    )

    assert "Skills: `product design`, `ux design`" in header_lines[1]
    assert "Other needs:" not in header_lines[1]
    assert header_lines[2] == "Search note: broadened matching."
    assert header_lines[3] == "Found **2** candidate(s)."


def test_search_and_rerank_candidates_retries_with_relaxed_requirements() -> None:
    cog = JobsCog(Mock())
    candidate = _make_candidate(crm_name="Jamie", match_score=18.0)
    requirements = JobRequirements(
        hard_required_skills=["webflow", "figma", "hubspot"],
        discord_role_types=["Designer"],
    )

    with (
        patch(
            "five08.discord_bot.cogs.jobs.search_candidates",
            side_effect=[[], [candidate]],
        ) as search_candidates_mock,
        patch.object(
            cog,
            "_rerank_candidates",
            AsyncMock(return_value=[candidate]),
        ) as rerank_mock,
    ):
        outcome = asyncio.run(
            cog._search_and_rerank_candidates(
                posting="Webflow role",
                requirements=requirements,
                guild_id="123",
                limit=20,
                min_match_score=8.0,
            )
        )

    assert search_candidates_mock.call_count == 2
    first_requirements = search_candidates_mock.call_args_list[0].args[1]
    second_requirements = search_candidates_mock.call_args_list[1].args[1]
    assert first_requirements.hard_required_skills == ["webflow", "figma"]
    assert second_requirements.hard_required_skills == ["webflow"]
    assert second_requirements.soft_required_skills == ["figma", "hubspot"]
    rerank_mock.assert_awaited_once()
    assert outcome.candidates == [candidate]
    assert outcome.effective_requirements.hard_required_skills == ["webflow"]
    assert "anchor hard need `webflow`" in (outcome.search_note or "")


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
        r"evidence: hard skill \`webflow\` (strength 5) · resume \`jamie.pdf\`"
        in lines[0]
    )
    assert "summary: Strong Webflow fit but portfolio proof is still thin." in lines[0]
    assert "missing: `live webflow projects`" in lines[0]


def test_build_match_candidate_lines_escapes_untrusted_evidence_and_missing_items() -> (
    None
):
    candidate = _make_candidate(
        crm_name="Jamie",
        evidence_signals=["resume `jamie.pdf` <@123>"],
        llm_missing_requirements=["needs `webflow` <@456>"],
    )

    lines, _ = JobsCog._build_match_candidate_lines(
        candidates=[candidate],
        crm_base="https://crm.example",
    )

    assert r"evidence: resume \`jamie.pdf\` <@​123>" in lines[0]
    assert "missing: `needs ˋwebflowˋ <@\u200b456>`" in lines[0]


def test_build_rerank_candidate_payload_uses_opaque_id_and_omits_pii() -> None:
    candidate = _make_candidate(
        crm_contact_id="crm-123",
        discord_user_id="discord-456",
        crm_name="Jamie",
        name="Jamie Display",
        linkedin="https://linkedin.example/jamie",
        github_username="jamie-dev",
        latest_resume_name="jamie.pdf",
        matched_hard_required_skills=["webflow"],
        matched_soft_required_skills=["figma"],
        matched_preferred_skills=["hubspot"],
        matched_discord_roles=["Frontend"],
        missing_hard_required_skills=["webflow cms"],
        match_score=42.0,
        seniority="midlevel",
        address_city="Seattle",
        address_state="Washington",
        address_country="US",
        timezone="UTC-08:00",
    )

    payload = JobsCog._build_rerank_candidate_payload(candidate=candidate, index=3)

    assert payload["candidate_id"] == "candidate:3"
    assert payload["country"] == "US"
    assert payload["has_linkedin"] is True
    assert payload["has_github"] is True
    assert payload["has_resume"] is True
    assert "name" not in payload
    assert "linkedin" not in payload
    assert "github_username" not in payload
    assert "resume_name" not in payload
    assert payload["evidence"] == [
        "linkedin profile on file",
        "github profile on file",
        "resume on file",
        "relevant discord role match",
    ]


def test_rerank_candidates_preserves_llm_order_for_tied_scores() -> None:
    cog = JobsCog(Mock())
    candidates = [
        _make_candidate(crm_name="Alpha", match_score=80.0),
        _make_candidate(crm_name="Beta", match_score=95.0),
    ]
    rerank_results = [
        CandidateRerankResult(candidate_id="candidate:2", fit_score=90.0),
        CandidateRerankResult(candidate_id="candidate:1", fit_score=90.0),
    ]

    with (
        patch("five08.discord_bot.cogs.jobs.rerank_shortlisted_candidates") as rerank,
        patch("five08.discord_bot.cogs.jobs.settings.openai_api_key", "test-key"),
    ):
        rerank.return_value = rerank_results
        reranked = asyncio.run(
            cog._rerank_candidates(
                posting="Webflow job",
                requirements=JobRequirements(required_skills=["webflow"]),
                candidates=candidates,
            )
        )

    assert [candidate.crm_name for candidate in reranked[:2]] == ["Beta", "Alpha"]
