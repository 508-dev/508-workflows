"""Unit tests for jobs cog match formatting."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, call
from types import SimpleNamespace
from unittest.mock import patch

import five08.discord_bot.cogs.jobs as jobs_module
from five08.discord_bot.cogs.jobs import JobsCog
from five08.engagements import EngagementStatus
from five08.job_leads import JobLead, JobLeadStatus
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


def _make_job_lead(**overrides: object) -> JobLead:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": JobLeadStatus.APPROVED,
        "source_key": "hackernews_who_is_hiring",
        "source_type": "hackernews",
        "external_id": "48392586",
        "source_url": "https://news.ycombinator.com/item?id=48392586",
        "title": "CO-Ver | Fullstack SWE | Remote US | 1099 Contract-to-Hire",
        "body_raw": "raw",
        "body_normalized": "CO-Ver needs a 1099 contractor.",
        "organization": "CO-Ver",
        "external_parent_id": "48357725",
        "source_posted_at": now,
        "posting_type": jobs_module.JobPostingType.PART_TIME,
        "location": "Remote US",
        "remote": True,
        "apply_url": "https://example.com",
        "tags": ["1099", "contract-to-hire"],
        "confidence": 0.65,
        "metadata": {},
        "reviewed_by_discord_user_id": "42",
        "reviewed_at": now,
        "discord_guild_id": None,
        "discord_channel_id": None,
        "discord_thread_id": None,
        "posted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return JobLead(**base)


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


def test_job_lead_forum_tags_match_detected_and_extra_tags() -> None:
    channel = SimpleNamespace(
        available_tags=[
            SimpleNamespace(name="Remote"),
            SimpleNamespace(name="Contract-to-Hire"),
            SimpleNamespace(name="Python"),
            SimpleNamespace(name="Full-time"),
        ]
    )
    lead = _make_job_lead()

    tags = JobsCog._resolve_job_lead_forum_tags(channel, lead, "Python")

    assert [tag.name for tag in tags] == ["Remote", "Contract-to-Hire", "Python"]


def test_format_job_lead_thread_content_includes_review_context() -> None:
    lead = _make_job_lead()

    content = JobsCog._format_job_lead_thread_content(lead)

    assert "Source: https://news.ycombinator.com/item?id=48392586" in content
    assert "Apply/contact: https://example.com" in content
    assert "Lead tags: 1099, contract-to-hire" in content
    assert "CO-Ver needs a 1099 contractor." in content


def test_format_job_lead_review_line_explains_confidence_source() -> None:
    lead = _make_job_lead(
        metadata={
            "contractor_classification": {
                "is_contractor_friendly": True,
                "posting_type": "part_time",
                "tags": ["contract", "1099"],
                "confidence": 0.91,
                "confidence_label": "high",
                "rationale": "Explicitly allows 1099 contract work.",
                "method": "llm",
            }
        }
    )

    line = JobsCog._format_job_lead_review_line(1, lead)

    assert "LLM: Part-time / contract; evidence: contract, 1099" in line
    assert "91%" not in line


def test_format_job_lead_review_message_respects_discord_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        type(jobs_module.settings),
        "discord_sendmsg_character_limit",
        property(lambda _settings: 600),
    )
    leads = [
        _make_job_lead(
            id=f"11111111-1111-1111-1111-11111111111{index}",
            title=f"Contract role {index} " + ("x" * 100),
            source_url=f"https://news.ycombinator.com/item?id={48000000 + index}",
            metadata={
                "contractor_classification": {
                    "is_contractor_friendly": True,
                    "posting_type": "part_time",
                    "tags": ["contract", "1099", "remote"],
                    "confidence": 0.91,
                    "confidence_label": "high",
                    "rationale": "Explicitly allows contract work. " + ("y" * 120),
                    "method": "llm",
                }
            },
        )
        for index in range(10)
    ]

    message = JobsCog._format_job_lead_review_message(leads)

    assert len(message) <= jobs_module.settings.discord_sendmsg_character_limit
    assert "Showing" in message
    assert "Use a lower limit" in message


def test_format_job_lead_review_message_handles_empty_leads() -> None:
    assert (
        JobsCog._format_job_lead_review_message([])
        == "Pending job leads:\n\nNo pending job leads found."
    )


def test_format_job_lead_thread_content_respects_discord_limit() -> None:
    lead = _make_job_lead(body_normalized="x" * 5000)

    content = JobsCog._format_job_lead_thread_content(lead)

    assert len(content) <= jobs_module.settings.discord_sendmsg_character_limit
    assert content.endswith("...")


def test_job_lead_allowed_mentions_disables_all_mentions() -> None:
    allowed_mentions = JobsCog._job_lead_allowed_mentions()

    payload = allowed_mentions.to_dict()
    assert payload["parse"] == []


async def test_post_job_lead_to_discord_requires_approved_lead() -> None:
    cog = JobsCog(Mock())
    lead = _make_job_lead(status=JobLeadStatus.PENDING)

    with patch.object(jobs_module, "get_job_lead", return_value=lead):
        result, status_code = await cog.post_job_lead_to_discord(
            lead_id=lead.id,
            reviewer_discord_user_id="42",
        )

    assert status_code == 409
    assert result == {"error": "job_lead_not_approved", "lead_id": lead.id}


async def test_post_job_lead_to_discord_posts_qualified_lead_without_holding_thread() -> (
    None
):
    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(
        id=456,
        name="gigs",
        guild=guild,
        available_tags=[],
        create_thread=AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(id=789),
                message=SimpleNamespace(id=790),
            )
        ),
    )
    lead = _make_job_lead(status=JobLeadStatus.APPROVED)
    posted = _make_job_lead(
        status=JobLeadStatus.POSTED,
        discord_guild_id="123",
        discord_channel_id="456",
        discord_thread_id="789",
    )
    cog = JobsCog(Mock())

    with (
        patch.object(jobs_module, "get_job_lead", return_value=lead),
        patch.object(jobs_module, "mark_job_lead_posted", return_value=posted) as mark,
        patch.object(
            jobs_module,
            "upsert_discord_engagement",
            return_value="engagement-1",
        ) as upsert_engagement,
        patch.object(jobs_module, "add_engagement_event") as add_event,
    ):
        result, status_code = await cog.post_job_lead_to_discord(
            lead_id=lead.id,
            reviewer_discord_user_id="42",
            channel=channel,
            engagement_status=EngagementStatus.RECRUITING,
        )

    assert status_code == 200
    assert result["thread_id"] == "789"
    assert result["engagement_status"] == "recruiting"
    assert result["engagement_id"] == "engagement-1"
    mark.assert_called_once_with(
        jobs_module.settings,
        lead_id=lead.id,
        reviewer_discord_user_id="42",
        guild_id="123",
        channel_id="456",
        thread_id="789",
    )
    channel.create_thread.assert_awaited_once()
    assert channel.create_thread.await_args.kwargs["name"].startswith("[RECRUITING] ")
    payload = upsert_engagement.call_args.args[1]
    assert payload.status is EngagementStatus.RECRUITING
    assert payload.message_id == "790"
    assert payload.thread_id == "789"
    assert payload.posted_by_discord_user_id == "42"
    add_event.assert_called_once()


async def test_stage_job_lead_to_discord_creates_holding_thread_without_gig() -> None:
    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(
        id=456,
        name="unqualified-leads",
        guild=guild,
        available_tags=[],
        create_thread=AsyncMock(
            return_value=SimpleNamespace(
                thread=SimpleNamespace(id=789),
                message=SimpleNamespace(id=790),
            )
        ),
    )
    lead = _make_job_lead(status=JobLeadStatus.PENDING)
    staged = _make_job_lead(
        status=JobLeadStatus.PENDING,
        staged_discord_guild_id="123",
        staged_discord_channel_id="456",
        staged_discord_thread_id="789",
        staged_at=lead.created_at,
    )
    cog = JobsCog(Mock())
    cog._resolve_unqualified_leads_forum = AsyncMock(return_value=(channel, None, 200))

    with (
        patch.object(jobs_module, "get_job_lead", return_value=lead),
        patch.object(jobs_module, "mark_job_lead_staged", return_value=staged) as mark,
        patch.object(jobs_module, "upsert_discord_engagement") as upsert_engagement,
        patch.object(jobs_module, "add_engagement_event") as add_event,
    ):
        result, status_code = await cog.stage_job_lead_to_discord(
            lead_id=lead.id,
            reviewer_discord_user_id="42",
        )

    assert status_code == 200
    assert result == {
        "status": "staged",
        "lead_id": lead.id,
        "guild_id": "123",
        "channel_id": "456",
        "thread_id": "789",
        "staged_at": lead.created_at.isoformat(),
    }
    mark.assert_called_once_with(
        jobs_module.settings,
        lead_id=lead.id,
        guild_id="123",
        channel_id="456",
        thread_id="789",
    )
    thread_kwargs = channel.create_thread.await_args.kwargs
    assert thread_kwargs["name"].startswith("[UNQUALIFIED] ")
    assert "do not treat as an active gig" in thread_kwargs["content"]
    upsert_engagement.assert_not_called()
    add_event.assert_not_called()


async def test_unqualified_leads_forum_cannot_be_registered_for_matching() -> None:
    guild = SimpleNamespace(id=123)
    channel = SimpleNamespace(id=456, guild=guild)
    cog = JobsCog(Mock())
    cog._jobs_channels_by_guild[123] = {456}

    with (
        patch.object(
            jobs_module.settings,
            "discord_unqualified_leads_forum_channel",
            "456",
        ),
        patch.object(cog, "_resolve_configured_guild", return_value=guild),
        patch.object(
            cog,
            "_fetch_forum_channel",
            new_callable=AsyncMock,
            return_value=(channel, None, 200),
        ),
        patch.object(cog, "_refresh_jobs_channel_cache", new_callable=AsyncMock),
    ):
        resolved, error, status_code = await cog._resolve_unqualified_leads_forum()

    assert resolved is None
    assert error == {"error": "unqualified_leads_forum_registered"}
    assert status_code == 409


def test_job_lead_forum_tags_select_required_fallback() -> None:
    channel = SimpleNamespace(
        available_tags=[
            SimpleNamespace(name="Full-time", moderated=False),
            SimpleNamespace(name="Contract", moderated=False),
        ],
        flags=SimpleNamespace(require_tag=True),
    )
    lead = _make_job_lead(tags=[], remote=False)

    tags = JobsCog._resolve_job_lead_forum_tags(channel, lead, None)

    assert [tag.name for tag in tags] == ["Contract"]


async def test_resolve_job_lead_post_channel_requires_registered_explicit_channel(
    monkeypatch,
) -> None:
    monkeypatch.setattr(jobs_module.settings, "discord_server_id", "123")

    class FakeForumChannel:
        id = 456
        guild = SimpleNamespace(id=123)

    bot = Mock()
    bot.guilds = []
    bot.get_channel.return_value = FakeForumChannel()
    bot.get_guild.return_value = FakeForumChannel.guild
    cog = JobsCog(bot)
    cog._jobs_channels_by_guild[123] = set()
    monkeypatch.setattr(jobs_module.discord, "ForumChannel", FakeForumChannel)

    with (
        patch.object(cog, "_refresh_jobs_channel_cache", new_callable=AsyncMock),
        patch.object(
            cog, "_register_default_job_forum_channels", new_callable=AsyncMock
        ),
    ):
        channel, result, status_code = await cog._resolve_job_lead_post_channel(
            _make_job_lead(),
            channel_id="456",
        )

    assert channel is None
    assert status_code == 403
    assert result == {"error": "job_forum_not_registered"}


async def test_resolve_job_lead_post_channel_rejects_wrong_guild(
    monkeypatch,
) -> None:
    monkeypatch.setattr(jobs_module.settings, "discord_server_id", "123")

    class FakeForumChannel:
        id = 456
        guild = SimpleNamespace(id=999)

    bot = Mock()
    bot.guilds = []
    bot.get_channel.return_value = FakeForumChannel()
    bot.get_guild.return_value = SimpleNamespace(id=123)
    cog = JobsCog(bot)
    monkeypatch.setattr(jobs_module.discord, "ForumChannel", FakeForumChannel)

    channel, result, status_code = await cog._resolve_job_lead_post_channel(
        _make_job_lead(),
        channel_id="456",
    )

    assert channel is None
    assert status_code == 403
    assert result == {"error": "job_forum_wrong_guild"}


def test_update_gig_status_allows_original_thread_poster() -> None:
    interaction = SimpleNamespace(user=SimpleNamespace(id=123, roles=[]))

    assert JobsCog._interaction_user_can_update_gig_thread(interaction, "123") is True


def test_update_gig_status_allows_steering_or_higher_role() -> None:
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="Admin")],
        )
    )

    assert JobsCog._interaction_user_can_update_gig_thread(interaction, "456") is True


def test_update_gig_status_rejects_unprivileged_non_poster() -> None:
    interaction = SimpleNamespace(
        user=SimpleNamespace(
            id=123,
            roles=[SimpleNamespace(name="Member")],
        )
    )

    assert JobsCog._interaction_user_can_update_gig_thread(interaction, "456") is False


def test_update_gig_status_accepts_only_explicit_command_values() -> None:
    assert JobsCog._explicit_gig_status("LEAD") is EngagementStatus.LEAD
    assert JobsCog._explicit_gig_status("CONTACTED") is EngagementStatus.CONTACTED
    assert JobsCog._explicit_gig_status("FILLED") is EngagementStatus.FILLED
    assert JobsCog._explicit_gig_status("open") is None
    assert JobsCog._explicit_gig_status("stale") is None
    assert JobsCog._explicit_gig_status("cancelled") is None


def test_locked_or_archived_recruiting_threads_are_treated_as_outdated() -> None:
    locked = SimpleNamespace(name="[RECRUITING] Need help", locked=True, archived=False)
    archived = SimpleNamespace(name="Need help", locked=False, archived=True)
    contacted = SimpleNamespace(
        name="[CONTACTED] Need help", locked=False, archived=True
    )
    filled = SimpleNamespace(name="[FILLED] Need help", locked=True, archived=True)
    lost = SimpleNamespace(name="[LOST] Need help", locked=True, archived=True)

    assert JobsCog._status_for_thread(locked) is EngagementStatus.OUTDATED
    assert JobsCog._status_for_thread(archived) is EngagementStatus.OUTDATED
    assert JobsCog._status_for_thread(contacted) is EngagementStatus.CONTACTED
    assert JobsCog._status_for_thread(filled) is EngagementStatus.FILLED
    assert JobsCog._status_for_thread(lost) is EngagementStatus.LOST


def test_rename_gig_thread_for_status_rewrites_visible_marker() -> None:
    class FakeThread:
        id = 200
        name = "[RECRUITING] Need help"
        archived = False
        guild = SimpleNamespace(me=object())

        def __init__(self) -> None:
            self.edit = AsyncMock()

        def permissions_for(self, _member: object) -> SimpleNamespace:
            return SimpleNamespace(manage_threads=True)

    thread = FakeThread()

    result = asyncio.run(
        JobsCog._rename_gig_thread_for_status(
            thread,
            EngagementStatus.FILLED,
            reason="test",
        )
    )

    assert result == "[FILLED] Need help"
    thread.edit.assert_awaited_once_with(name="[FILLED] Need help", reason="test")


def test_rename_gig_thread_for_status_uses_fallback_for_marker_only_title() -> None:
    class FakeThread:
        id = 200
        name = "[RECRUITING]"
        archived = False
        guild = SimpleNamespace(me=object())

        def __init__(self) -> None:
            self.edit = AsyncMock()

        def permissions_for(self, _member: object) -> SimpleNamespace:
            return SimpleNamespace(manage_threads=True)

    thread = FakeThread()

    result = asyncio.run(
        JobsCog._rename_gig_thread_for_status(
            thread,
            EngagementStatus.FILLED,
            reason="test",
        )
    )

    assert result == "[FILLED] Discord gig 200"
    thread.edit.assert_awaited_once_with(
        name="[FILLED] Discord gig 200",
        reason="test",
    )


def test_rename_gig_thread_for_lost_status_locks_and_archives() -> None:
    class FakeThread:
        id = 200
        name = "[RECRUITING] Need help"
        archived = False
        locked = False
        guild = SimpleNamespace(me=object())

        def __init__(self) -> None:
            self.edit = AsyncMock()

        def permissions_for(self, _member: object) -> SimpleNamespace:
            return SimpleNamespace(manage_threads=True)

    thread = FakeThread()

    result = asyncio.run(
        JobsCog._rename_gig_thread_for_status(
            thread,
            EngagementStatus.LOST,
            reason="test",
        )
    )

    assert result == "[LOST] Need help"
    assert thread.edit.await_args_list == [
        call(name="[LOST] Need help", reason="test"),
        call(locked=True, archived=True, reason="test"),
    ]


def test_rename_gig_thread_for_non_lost_status_reopens_thread() -> None:
    class FakeThread:
        id = 200
        name = "[LOST] Need help"
        archived = True
        locked = True
        guild = SimpleNamespace(me=object())

        def __init__(self) -> None:
            self.edit = AsyncMock()

        def permissions_for(self, _member: object) -> SimpleNamespace:
            return SimpleNamespace(manage_threads=True)

    thread = FakeThread()

    result = asyncio.run(
        JobsCog._rename_gig_thread_for_status(
            thread,
            EngagementStatus.RECRUITING,
            reason="test",
        )
    )

    assert result == "[RECRUITING] Need help"
    assert thread.edit.await_args_list == [
        call(locked=False, archived=False, reason="test"),
        call(name="[RECRUITING] Need help", reason="test"),
    ]


def test_rename_gig_thread_for_non_lost_status_preserves_moderator_lock() -> None:
    class FakeThread:
        id = 200
        name = "[RECRUITING] Need help"
        archived = False
        locked = True
        guild = SimpleNamespace(me=object())

        def __init__(self) -> None:
            self.edit = AsyncMock()

        def permissions_for(self, _member: object) -> SimpleNamespace:
            return SimpleNamespace(manage_threads=True)

    thread = FakeThread()

    result = asyncio.run(
        JobsCog._rename_gig_thread_for_status(
            thread,
            EngagementStatus.FILLED,
            reason="test",
        )
    )

    assert result == "[FILLED] Need help"
    thread.edit.assert_awaited_once_with(name="[FILLED] Need help", reason="test")


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
    assert "anchor hard skill `webflow` stayed mandatory" in (plan[1][2] or "")
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
    assert plan[1][0].hard_required_skills == ["japanese", "next engine"]
    assert plan[1][0].soft_required_skills == ["shopify", "wms"]
    assert "required language gates" in (plan[1][2] or "")


def test_build_candidate_search_plan_keeps_llm_required_languages_hard() -> None:
    requirements = JobRequirements(
        hard_required_skills=["spanish", "salesforce"],
        soft_required_skills=["crm"],
        required_languages=["spanish"],
    )

    plan = JobsCog._build_candidate_search_plan(requirements, min_match_score=8.0)

    assert plan[-1][0].hard_required_skills == ["spanish", "salesforce"]
    assert plan[-1][0].soft_required_skills == [
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
    assert "anchor hard skill `webflow` stayed mandatory" in (outcome.search_note or "")


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


def test_on_thread_create_indexes_public_registered_forum_post(monkeypatch) -> None:
    class FakeForumChannel:
        id = 123
        name = "gigs"

        def permissions_for(self, _role: object) -> SimpleNamespace:
            return SimpleNamespace(view_channel=True)

    monkeypatch.setattr(jobs_module.discord, "ForumChannel", FakeForumChannel)

    cog = JobsCog(Mock())
    cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
    cog._is_jobs_channel_registered = Mock(return_value=True)
    cog._persist_thread_engagement_index = AsyncMock(return_value=True)
    cog._run_auto_match_candidates_for_thread = AsyncMock()

    guild = SimpleNamespace(id=456, name="508", default_role=object())
    thread = SimpleNamespace(id=789, guild=guild, parent=FakeForumChannel())

    asyncio.run(cog.on_thread_create(thread))

    cog._persist_thread_engagement_index.assert_awaited_once_with(
        thread,
        source="thread_create",
    )
    cog._run_auto_match_candidates_for_thread.assert_not_awaited()


def _fake_thread_with_history(
    *,
    created_at: datetime,
    messages: list[SimpleNamespace],
) -> SimpleNamespace:
    history_calls: list[dict[str, object]] = []
    starter = SimpleNamespace(
        id=100,
        content="Need help with a build",
        created_at=created_at,
        author=SimpleNamespace(id=10, bot=False, name="poster"),
    )

    async def history(**_kwargs: object):
        history_calls.append(_kwargs)
        for message in messages:
            yield message

    return SimpleNamespace(
        id=200,
        name="[RECRUITING] Need help",
        guild=SimpleNamespace(id=300),
        parent=SimpleNamespace(id=400, name="gigs"),
        owner_id=10,
        starter_message=starter,
        applied_tags=[],
        created_at=created_at,
        history=history,
        history_calls=history_calls,
    )


def test_backfill_thread_reply_interest_records_interest_and_marks(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    message_at = now - timedelta(days=1)
    messages = [
        SimpleNamespace(
            id=101,
            content="I'm interested in this",
            created_at=message_at,
            author=SimpleNamespace(id=20, bot=False, name="jamie"),
        ),
        SimpleNamespace(
            id=102,
            content="following",
            created_at=message_at,
            author=SimpleNamespace(id=21, bot=False, name="casey"),
        ),
        SimpleNamespace(
            id=104,
            content="I can help with this one",
            created_at=message_at,
            author=SimpleNamespace(id=20, bot=False, name="jamie"),
        ),
        SimpleNamespace(
            id=103,
            content="I'm interested",
            created_at=message_at,
            author=SimpleNamespace(id=22, bot=True, name="bot"),
        ),
        SimpleNamespace(
            id=105,
            content="I'm interested",
            created_at=message_at,
            author=SimpleNamespace(id=10, bot=False, name="poster"),
        ),
    ]
    thread = _fake_thread_with_history(created_at=now, messages=messages)
    markers: list[dict[str, object] | None] = []
    applications: list[dict[str, object]] = []

    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_engagement",
        lambda *_args, **_kwargs: "engagement-1",
    )
    monkeypatch.setattr(
        jobs_module,
        "get_gig_thread_interest_backfill_marker",
        lambda *_args, **_kwargs: None,
    )

    def upsert_application(*_args: object, **kwargs: object) -> str:
        applications.append(kwargs)
        return "application-1"

    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_interest_application",
        upsert_application,
    )

    def upsert_marker(*_args: object, **kwargs: object) -> None:
        markers.append(kwargs.get("payload"))

    monkeypatch.setattr(
        jobs_module, "upsert_gig_thread_interest_backfill_marker", upsert_marker
    )

    result = asyncio.run(
        JobsCog(Mock())._backfill_thread_reply_interest(
            thread,
            source="test",
            max_age_days=14,
        )
    )

    assert result.status == "backfilled"
    assert result.scanned_count == 3
    assert result.interested_count == 1
    assert len(applications) == 2
    assert applications[0]["discord_user_id"] == "20"
    assert applications[0]["message_id"] == "101"
    assert applications[0]["refresh_activity"] is True
    assert applications[0]["activity_at"] == message_at
    assert applications[0]["event_created_at"] == message_at
    assert markers == [
        {
            "source": "test",
            "thread_id": "200",
            "max_age_days": 14,
            "force": False,
            "scanned_count": 3,
            "interested_count": 1,
            "created_at": now.isoformat(),
            "last_scanned_message_created_at": message_at.isoformat(),
        }
    ]


def test_backfill_thread_reply_interest_skips_old_automatic_thread(
    monkeypatch,
) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=15)
    thread = _fake_thread_with_history(created_at=old, messages=[])
    upsert_engagement = Mock(return_value="engagement-1")
    monkeypatch.setattr(jobs_module, "upsert_discord_engagement", upsert_engagement)

    result = asyncio.run(
        JobsCog(Mock())._backfill_thread_reply_interest(
            thread,
            source="test",
            max_age_days=14,
        )
    )

    assert result.status == "skipped"
    assert result.reason == "older_than_backfill_window"
    upsert_engagement.assert_not_called()


def test_backfill_thread_reply_interest_skips_when_no_new_marked_replies(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    message_at = now - timedelta(minutes=5)
    thread = _fake_thread_with_history(
        created_at=now,
        messages=[
            SimpleNamespace(
                id=101,
                content="I'm interested",
                created_at=message_at,
                author=SimpleNamespace(id=20, bot=False, name="jamie"),
            )
        ],
    )
    application = Mock(return_value="application-1")
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_engagement",
        lambda *_args, **_kwargs: "engagement-1",
    )
    monkeypatch.setattr(
        jobs_module,
        "get_gig_thread_interest_backfill_marker",
        lambda *_args, **_kwargs: {"last_scanned_message_created_at": now.isoformat()},
    )
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_interest_application",
        application,
    )

    result = asyncio.run(
        JobsCog(Mock())._backfill_thread_reply_interest(
            thread,
            source="test",
            max_age_days=14,
        )
    )

    assert result.status == "skipped"
    assert result.reason == "no_new_replies"
    application.assert_not_called()


def test_backfill_thread_reply_interest_scans_replies_after_marker(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    marker_at = now - timedelta(minutes=5)
    message_at = now - timedelta(minutes=1)
    thread = _fake_thread_with_history(
        created_at=now,
        messages=[
            SimpleNamespace(
                id=101,
                content="old interest",
                created_at=marker_at - timedelta(seconds=1),
                author=SimpleNamespace(id=20, bot=False, name="jamie"),
            ),
            SimpleNamespace(
                id=102,
                content="I'm interested",
                created_at=message_at,
                author=SimpleNamespace(id=21, bot=False, name="casey"),
            ),
        ],
    )
    application = Mock(return_value="application-2")
    marker = Mock()
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_engagement",
        lambda *_args, **_kwargs: "engagement-1",
    )
    monkeypatch.setattr(
        jobs_module,
        "get_gig_thread_interest_backfill_marker",
        lambda *_args, **_kwargs: {
            "last_scanned_message_created_at": marker_at.isoformat()
        },
    )
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_interest_application",
        application,
    )
    monkeypatch.setattr(
        jobs_module, "upsert_gig_thread_interest_backfill_marker", marker
    )

    result = asyncio.run(
        JobsCog(Mock())._backfill_thread_reply_interest(
            thread,
            source="test",
            max_age_days=14,
        )
    )

    assert result.status == "backfilled"
    assert result.scanned_count == 1
    assert thread.history_calls == [
        {"limit": None, "oldest_first": True, "after": marker_at}
    ]
    application.assert_called_once()
    assert application.call_args.kwargs["discord_user_id"] == "21"
    assert marker.call_args.kwargs["payload"]["last_scanned_message_created_at"] == (
        message_at.isoformat()
    )


def test_backfill_thread_reply_interest_force_rescans_already_marked_thread(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    thread = _fake_thread_with_history(
        created_at=now,
        messages=[
            SimpleNamespace(
                id=101,
                content="I'm interested",
                created_at=now,
                author=SimpleNamespace(id=20, bot=False, name="jamie"),
            )
        ],
    )
    application = Mock(return_value="application-1")
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_engagement",
        lambda *_args, **_kwargs: "engagement-1",
    )
    monkeypatch.setattr(
        jobs_module,
        "get_gig_thread_interest_backfill_marker",
        lambda *_args, **_kwargs: {"last_scanned_message_created_at": now.isoformat()},
    )
    monkeypatch.setattr(
        jobs_module,
        "upsert_discord_interest_application",
        application,
    )
    monkeypatch.setattr(
        jobs_module,
        "upsert_gig_thread_interest_backfill_marker",
        lambda *_args, **_kwargs: None,
    )

    result = asyncio.run(
        JobsCog(Mock())._backfill_thread_reply_interest(
            thread,
            source="manual",
            max_age_days=None,
            force=True,
        )
    )

    assert result.status == "backfilled"
    application.assert_called_once()


def test_sync_job_forum_channel_keeps_index_failures_separate_from_backfill_failures() -> (
    None
):
    async def archived_threads(**_kwargs: object):
        if False:
            yield None

    cog = JobsCog(Mock())
    cog._persist_thread_engagement_index = AsyncMock(return_value=True)
    cog._backfill_thread_reply_interest = AsyncMock(
        return_value=jobs_module.GigInterestBackfillResult(
            status="failed",
            reason="marker_write_failed",
        )
    )
    channel = SimpleNamespace(
        id=400,
        threads=[SimpleNamespace(id=200)],
        archived_threads=archived_threads,
    )

    indexed, failed = asyncio.run(cog._sync_job_forum_channel(channel, source="test"))

    assert indexed == 1
    assert failed == 0


def test_sync_job_forum_channel_isolates_backfill_crashes() -> None:
    async def archived_threads(**_kwargs: object):
        if False:
            yield None

    cog = JobsCog(Mock())
    cog._persist_thread_engagement_index = AsyncMock(return_value=True)
    cog._backfill_thread_reply_interest = AsyncMock(side_effect=RuntimeError("boom"))
    channel = SimpleNamespace(
        id=400,
        threads=[SimpleNamespace(id=200)],
        archived_threads=archived_threads,
    )

    indexed, failed = asyncio.run(cog._sync_job_forum_channel(channel, source="test"))

    assert indexed == 1
    assert failed == 0


def test_recruiting_reminder_marks_locked_thread_outdated_without_sending(
    monkeypatch,
) -> None:
    class FakeThread:
        locked = True
        archived = False

        def __init__(self) -> None:
            self.send = AsyncMock()

    thread = FakeThread()
    monkeypatch.setattr(jobs_module.discord, "Thread", FakeThread)
    monkeypatch.setattr(
        jobs_module,
        "list_due_status_reminders",
        Mock(
            return_value=[
                {
                    "id": "engagement-1",
                    "discord_thread_id": "200",
                    "posted_by_discord_user_id": "10",
                    "title": "Need help",
                    "age_days": 8,
                }
            ]
        ),
    )
    update_status = Mock(return_value={"id": "engagement-1"})
    monkeypatch.setattr(jobs_module, "update_engagement_status", update_status)

    bot = Mock()
    bot.get_channel.return_value = thread
    cog = JobsCog(bot)

    asyncio.run(cog._send_due_status_reminders())

    thread.send.assert_not_awaited()
    update_status.assert_called_once_with(
        jobs_module.settings,
        engagement_id="engagement-1",
        status=EngagementStatus.OUTDATED,
        actor_discord_user_id=None,
    )


def test_contacted_reminder_does_not_mark_archived_thread_outdated(
    monkeypatch,
) -> None:
    class FakeThread:
        locked = False
        archived = True

        def __init__(self) -> None:
            self.send = AsyncMock()

    thread = FakeThread()
    monkeypatch.setattr(jobs_module.discord, "Thread", FakeThread)
    monkeypatch.setattr(
        jobs_module,
        "list_due_status_reminders",
        Mock(
            return_value=[
                {
                    "id": "engagement-1",
                    "status": "contacted",
                    "discord_thread_id": "200",
                    "posted_by_discord_user_id": "10",
                }
            ]
        ),
    )
    update_status = Mock()
    monkeypatch.setattr(jobs_module, "update_engagement_status", update_status)

    bot = Mock()
    bot.get_channel.return_value = thread
    cog = JobsCog(bot)

    asyncio.run(cog._send_due_status_reminders())

    thread.send.assert_not_awaited()
    update_status.assert_not_called()


def test_contacted_reminder_uses_status_age_and_mentions_poster(monkeypatch) -> None:
    class FakeThread:
        locked = False
        archived = False

        def __init__(self) -> None:
            self.send = AsyncMock(return_value=SimpleNamespace(id=300))

    thread = FakeThread()
    monkeypatch.setattr(jobs_module.discord, "Thread", FakeThread)
    monkeypatch.setattr(
        jobs_module,
        "list_due_status_reminders",
        Mock(
            return_value=[
                {
                    "id": "engagement-1",
                    "status": "contacted",
                    "discord_thread_id": "200",
                    "posted_by_discord_user_id": "10",
                    "title": "Need help",
                    "age_days": 5,
                }
            ]
        ),
    )
    mark_sent = Mock()
    monkeypatch.setattr(jobs_module, "mark_status_reminder_sent", mark_sent)

    bot = Mock()
    bot.get_channel.return_value = thread
    cog = JobsCog(bot)

    asyncio.run(cog._send_due_status_reminders())

    assert "CONTACTED for 5 day(s)" in thread.send.await_args.args[0]
    mark_sent.assert_called_once_with(
        jobs_module.settings,
        engagement_id="engagement-1",
        message_id="300",
    )
