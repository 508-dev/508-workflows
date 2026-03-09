"""
Unit tests for CRM cog functionality.
"""

import json
from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest

from five08.discord_bot.cogs.crm import (
    CRMCog,
    ResumeButtonView,
    ResumeCreateContactView,
    ResumeUpdateConfirmationView,
    ResumeReprocessConfirmationView,
    ReprocessResumeSelectionView,
    ResumeDownloadButton,
    ResumeSeniorityOverrideSelect,
    ResumeEditLocationButton,
    ResumeEditLocationModal,
    ResumeEditWebsitesButton,
    ResumeEditSocialLinksButton,
    ResumeEditWebsitesModal,
    ResumeEditSocialLinksModal,
    ResumeEditSkillsButton,
    ResumeEditSkillsModal,
    ResumeEditRolesButton,
    ResumeEditRolesModal,
    ResumeApplyDiscordRolesButton,
    ResumeEditDiscordRolesButton,
    ResumeEditDiscordRolesModal,
    _extract_parsed_seniority,
    _format_seniority_label,
)
from five08.discord_bot.cogs.jobs import JobsCog
from five08.discord_bot.cogs import jobs as jobs_module
from five08.clients.espo import EspoAPIError
from five08.job_match import JobRequirements


class TestCRMCog:
    """Unit tests for CRMCog class."""

    @pytest.fixture
    def mock_bot(self):
        """Create a mock bot for testing."""
        bot = Mock()
        bot.get_cog = Mock()
        return bot

    @pytest.fixture
    def mock_espo_api(self):
        """Create a mock EspoAPI for testing."""
        with patch("five08.discord_bot.cogs.crm.EspoAPI") as mock_api_class:
            mock_api = Mock()
            mock_api_class.return_value = mock_api
            yield mock_api

    @pytest.fixture
    def crm_cog(self, mock_bot, mock_espo_api):
        """Create a CRMCog instance for testing."""
        cog = CRMCog(mock_bot)
        cog.espo_api = mock_espo_api
        return cog

    @pytest.fixture
    def jobs_cog(self, mock_bot):
        """Create a JobsCog instance for testing."""
        return JobsCog(mock_bot)

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.user = Mock()
        interaction.user.roles = []
        return interaction

    @pytest.fixture
    def mock_member_role(self):
        """Create a mock Member role."""
        role = Mock()
        role.name = "Member"
        return role

    @pytest.fixture
    def mock_admin_role(self):
        """Create a mock Admin role."""
        role = Mock()
        role.name = "Admin"
        return role

    def test_cog_initialization(self, mock_bot, mock_espo_api):
        """Test CRM cog initialization."""
        cog = CRMCog(mock_bot)
        assert cog.bot == mock_bot
        assert cog.espo_api is not None

    def test_build_job_match_header_and_mentions_guild_none(self, jobs_cog):
        """Header/mention builder should return backticks when guild is None."""
        requirements = JobRequirements(
            required_skills=["python"],
            preferred_skills=[],
            discord_role_types=["Full Stack"],
            seniority=None,
            location_type=None,
            preferred_timezones=["Asia/Tokyo"],
            raw_location_text="Japan or Asia",
            title="Full Stack Engineer",
        )

        (
            header_lines,
            role_line,
            role_ids,
            locality_line,
            locality_ids,
        ) = jobs_cog._build_job_match_header_and_mentions(
            requirements=requirements,
            candidates_count=3,
            guild=None,
        )

        assert header_lines[0] == "## Job Match Results"
        assert "Full Stack Engineer" in header_lines[1]
        assert "Skills: `python`" in header_lines[1]
        assert header_lines[-1] == "Found **3** candidate(s)."
        assert role_line == "Discord roles: `Full Stack`"
        assert role_ids == []
        assert locality_line == "Locality: `Asia`, `Japan`"
        assert locality_ids == []

    def test_build_job_match_header_and_mentions_with_guild(self, jobs_cog):
        """Header/mention builder should resolve real role mentions with a guild."""
        full_stack_role = Mock()
        full_stack_role.name = "Full Stack"
        full_stack_role.id = 111
        full_stack_role.position = 2
        full_stack_role.mention = "<@&111>"

        asia_role = Mock()
        asia_role.name = "Asia"
        asia_role.id = 222
        asia_role.position = 1
        asia_role.mention = "<@&222>"

        japan_role = Mock()
        japan_role.name = "Japan"
        japan_role.id = 333
        japan_role.position = 0
        japan_role.mention = "<@&333>"

        guild = Mock()
        guild.id = 99
        guild.roles = [full_stack_role, asia_role, japan_role]

        requirements = JobRequirements(
            required_skills=[],
            preferred_skills=[],
            discord_role_types=["Full Stack"],
            seniority=None,
            location_type=None,
            preferred_timezones=["Asia/Tokyo"],
            raw_location_text="Japan",
            title=None,
        )

        (
            _header_lines,
            role_line,
            role_ids,
            locality_line,
            locality_ids,
        ) = jobs_cog._build_job_match_header_and_mentions(
            requirements=requirements,
            candidates_count=1,
            guild=guild,
        )

        assert role_line == "Discord roles: <@&111>"
        assert role_ids == [111]
        assert locality_line == "Locality: <@&222>, <@&333>"
        assert locality_ids == [222, 333]

    def test_check_member_role_with_member(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test _check_member_role returns True for users with Member role."""
        mock_interaction.user.roles = [mock_member_role]

        result = crm_cog._check_member_role(mock_interaction)

        assert result is True

    def test_check_member_role_without_member(self, crm_cog, mock_interaction):
        """Test _check_member_role returns False for users without Member role."""
        other_role = Mock()
        other_role.name = "User"
        mock_interaction.user.roles = [other_role]

        result = crm_cog._check_member_role(mock_interaction)

        assert result is False

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_combines_skills_and_strengths(
        self, crm_cog
    ):
        """Applied updates should render skills and skill attrs as one combined line."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        lines = view._build_applied_updates_lines(
            updated_fields=["skills", "cSkillAttrs", "cGitHubUsername"],
            updated_values={
                "skills": ["python", "redis"],
                "cSkillAttrs": {
                    "python": {"strength": 5},
                    "redis": {"strength": 3},
                },
                "cGitHubUsername": "wumichaelm",
            },
        )

        assert lines[0] == "**Skills**: `python (5), redis (3)`"
        assert lines[1] == "**GitHub**: `@wumichaelm`"
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_maps_skill_attrs_only_to_skills(
        self, crm_cog
    ):
        """Updated fields should collapse cSkillAttrs-only changes into Skills label."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        collapsed = view._collapse_updated_fields(["cSkillAttrs", "phoneNumber"])

        assert collapsed == ["skills", "phoneNumber"]

    @pytest.mark.asyncio
    async def test_resume_apply_success_embed_includes_discord_link_mention(
        self, crm_cog
    ):
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            link_discord={"user_id": "123456789", "username": "test-user"},
        )

        embed = view._build_apply_success_embed(
            updated_fields=["skills"],
            updated_values={"skills": ["python"]},
            link_discord_applied=True,
        )

        discord_link_field = next(
            field for field in embed.fields if field.name == "Discord Link"
        )
        assert "<@123456789>" in discord_link_field.value
        assert "test-user" in discord_link_field.value

    def test_format_discord_link_value_escapes_username_mentions_and_markdown(self):
        username = "@everyone **ops**"
        expected_username = discord.utils.escape_markdown(
            discord.utils.escape_mentions(username)
        )

        value = ResumeUpdateConfirmationView._format_discord_link_value(
            link_discord={"user_id": "123456789", "username": username}
        )

        assert value == f"<@123456789> ({expected_username})"

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_groups_location_fields(self, crm_cog):
        """Applied updates should render location fields as one combined line."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        lines = view._build_applied_updates_lines(
            updated_fields=[
                "addressCity",
                "addressState",
                "addressCountry",
                "cTimezone",
            ],
            updated_values={
                "addressCity": "Nanzih",
                "addressState": "Kaohsiung City",
                "addressCountry": "Taiwan",
                "cTimezone": "UTC+08:00",
            },
        )

        assert lines == [
            "**Location**: `Nanzih, Kaohsiung City, Taiwan (Timezone: UTC+08:00)`"
        ]

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_maps_location_fields_to_location(
        self, crm_cog
    ):
        """Updated fields should collapse location subfields into one Location label."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        collapsed = view._collapse_updated_fields(
            ["addressCity", "cTimezone", "phoneNumber"]
        )

        assert collapsed == ["location", "phoneNumber"]

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_caps_updated_fields_length(self, crm_cog):
        """Updated Fields text should stay within Discord field limits."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        labels = [f"field-{idx:03d}" for idx in range(400)]
        summary = view._format_updated_fields_value(labels)

        assert len(summary) <= view._EMBED_FIELD_LIMIT

    @pytest.mark.asyncio
    async def test_resume_apply_confirmation_caps_applied_updates_length(self, crm_cog):
        """Applied updates text should stay within Discord field limits."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        long_value = "x" * 500
        lines = [f"**Field {idx}**: `{long_value}`" for idx in range(20)]
        summary = view._format_applied_updates_value(lines)

        assert len(summary) <= view._APPLIED_FIELD_TOTAL_LIMIT

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("junior", "Junior"),
            ("midlevel", "Mid-level"),
            ("mid-level", "Mid-level"),
            ("senior", "Senior"),
            ("staff", "Staff"),
            ("unknown", "Unknown"),
            ("", "Unknown"),
            (None, "Unknown"),
        ],
    )
    def test_format_seniority_label(self, raw, expected):
        """Seniority labels should normalize consistent display strings."""
        assert _format_seniority_label(raw) == expected

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"seniority_level": "senior"}, "senior"),
            ({"seniority_level": " unknown "}, None),
            ({}, None),
        ],
    )
    def test_extract_parsed_seniority_from_dict(self, payload, expected):
        """Parsed seniority should be extracted when present and not unknown."""
        assert _extract_parsed_seniority(payload) == expected

    def test_extract_parsed_seniority_from_object(self):
        """Parsed seniority should handle object attributes."""

        class DummyProfile:
            seniority_level = "midlevel"

        assert _extract_parsed_seniority(DummyProfile()) == "midlevel"

    def test_build_discord_role_suggestions_filters_current_roles_and_blocked_terms(
        self, crm_cog
    ):
        """Discord role suggestions should filter existing roles and blocked terms."""
        extracted_profile = {
            "skills": ["Python", "Python"],
            "primary_roles": ["Staff Engineer"],
            "address_country": "Taiwan",
        }

        with (
            patch(
                "five08.discord_bot.cogs.crm.suggest_technical_discord_roles",
                return_value=["Backend", "Manager", "Member", "Steering Committee"],
            ),
            patch(
                "five08.discord_bot.cogs.crm.suggest_locality_discord_roles",
                return_value=["Taiwan", "Manager"],
            ),
            patch(
                "five08.discord_bot.cogs.crm.DISCORD_ROLES_NEVER_SUGGEST",
                {"Manager"},
            ),
        ):
            technical, locality = crm_cog._build_discord_role_suggestions(
                extracted_profile=extracted_profile,
                current_discord_roles=["backend"],
            )

        assert technical == []
        assert locality == ["Taiwan"]

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_seniority_select(self, crm_cog):
        """Resume update view should expose a seniority override dropdown."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            parsed_seniority="senior",
        )

        assert any(
            isinstance(child, ResumeSeniorityOverrideSelect) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_seniority_select_last_with_buttons_first(
        self, crm_cog
    ):
        """Seniority override select should be last input while other controls stay buttons."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "cWebsiteLink": ["https://example.com"],
                "cSocialLinks": ["https://example.com/social"],
                "skills": ["python"],
                "cRoles": ["developer"],
                "addressCity": "Nanzih",
            },
            discord_role_suggestions=["Backend"],
            parsed_seniority="senior",
        )

        interactive_children = [
            child
            for child in view.children
            if isinstance(child, (discord.ui.Button, discord.ui.Select))
        ]
        override_index = next(
            idx
            for idx, child in enumerate(interactive_children)
            if isinstance(child, ResumeSeniorityOverrideSelect)
        )
        button_indices = [
            idx
            for idx, child in enumerate(interactive_children)
            if isinstance(child, discord.ui.Button)
        ]

        assert override_index == len(interactive_children) - 1
        assert all(idx < override_index for idx in button_indices)

    @pytest.mark.asyncio
    async def test_resume_update_view_sets_seniority_override(self, crm_cog):
        """Seniority override should update the proposed CRM payload."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            parsed_seniority="junior",
        )

        label = view._set_seniority_override("staff")

        assert label == "Staff"
        assert view.proposed_updates["cSeniority"] == "staff"

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_websites_button_when_websites_proposed(
        self, crm_cog
    ):
        """Edit Websites button should appear when cWebsiteLink is in proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cWebsiteLink": ["https://example.com"]},
        )

        assert any(
            isinstance(child, ResumeEditWebsitesButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_no_websites_button_without_websites(
        self, crm_cog
    ):
        """Edit Websites button should not appear when cWebsiteLink is absent."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert not any(
            isinstance(child, ResumeEditWebsitesButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_social_links_button_when_social_links_proposed(
        self, crm_cog
    ):
        """Edit Social Links button should appear when cSocialLinks is in proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cSocialLinks": ["https://linkedin.com/in/user"]},
        )

        assert any(
            isinstance(child, ResumeEditSocialLinksButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_no_social_links_button_without_social_links(
        self, crm_cog
    ):
        """Edit Social Links button should not appear when cSocialLinks is absent."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert not any(
            isinstance(child, ResumeEditSocialLinksButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_skills_button_when_skills_proposed(
        self, crm_cog
    ):
        """Edit Skills button should appear when skills are in proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"skills": ["python"]},
        )

        assert any(isinstance(child, ResumeEditSkillsButton) for child in view.children)

    @pytest.mark.asyncio
    async def test_resume_update_view_no_skills_button_without_skills(self, crm_cog):
        """Edit Skills button should not appear when skills are absent."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert not any(
            isinstance(child, ResumeEditSkillsButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_location_button_when_location_proposed(
        self, crm_cog
    ):
        """Edit Location button should appear when location fields are proposed."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "addressCity": "Nanzih",
                "addressCountry": "Taiwan",
                "cTimezone": "UTC+08:00",
            },
        )

        assert any(
            isinstance(child, ResumeEditLocationButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_location_button_without_location(
        self, crm_cog
    ):
        """Edit Location button should still appear when location fields are absent."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert any(
            isinstance(child, ResumeEditLocationButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_roles_button_when_roles_proposed(
        self, crm_cog
    ):
        """Edit Roles button should appear when cRoles is in proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cRoles": ["developer"]},
        )

        assert any(isinstance(child, ResumeEditRolesButton) for child in view.children)

    @pytest.mark.asyncio
    async def test_resume_update_view_no_roles_button_without_roles(self, crm_cog):
        """Edit Roles button should not appear when cRoles is absent."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert not any(
            isinstance(child, ResumeEditRolesButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_adds_discord_roles_buttons_with_suggestions(
        self, crm_cog
    ):
        """Discord roles edit/apply buttons should appear when suggestions exist."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=["Backend", "Taiwan"],
        )

        assert any(
            isinstance(child, ResumeEditDiscordRolesButton) for child in view.children
        )
        assert any(
            isinstance(child, ResumeApplyDiscordRolesButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_resume_update_view_without_discord_role_suggestions_no_role_buttons(
        self, crm_cog
    ):
        """Discord role buttons should not appear when no suggestions exist."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )

        assert not any(
            isinstance(child, ResumeEditDiscordRolesButton) for child in view.children
        )
        assert not any(
            isinstance(child, ResumeApplyDiscordRolesButton) for child in view.children
        )

    @pytest.mark.asyncio
    async def test_edit_websites_modal_prepopulates_list_values(self, crm_cog):
        """Edit Websites modal should pre-fill with proposed website list, one per line."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "cWebsiteLink": ["https://example.com", "https://blog.example.com"]
            },
        )

        modal = ResumeEditWebsitesModal(confirmation_view=view)

        assert (
            modal.websites_input.default
            == "https://example.com\nhttps://blog.example.com"
        )

    @pytest.mark.asyncio
    async def test_edit_social_links_modal_prepopulates_list_values(self, crm_cog):
        """Edit Social Links modal should pre-fill with proposed social link list."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "cSocialLinks": ["https://linkedin.com/in/user", "https://x.com/user"]
            },
        )

        modal = ResumeEditSocialLinksModal(confirmation_view=view)

        assert (
            modal.social_links_input.default
            == "https://linkedin.com/in/user\nhttps://x.com/user"
        )

    @pytest.mark.asyncio
    async def test_edit_location_modal_prepopulates_values(self, crm_cog):
        """Edit Location modal should pre-fill location fields from proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "addressCity": "Nanzih",
                "addressState": "Kaohsiung City",
                "addressCountry": "Taiwan",
                "cTimezone": "UTC+08:00",
            },
        )

        modal = ResumeEditLocationModal(confirmation_view=view)

        assert modal.city_input.default == "Nanzih"
        assert modal.state_input.default == "Kaohsiung City"
        assert modal.country_input.default == "Taiwan"
        assert modal.timezone_input.default == "UTC+08:00"

    @pytest.mark.asyncio
    async def test_edit_skills_modal_prepopulates_list_values(self, crm_cog):
        """Edit Skills modal should pre-fill with proposed skills + strengths."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "skills": ["python", "go", "rust"],
                "cSkillAttrs": {"python": {"strength": 5}, "rust": {"strength": 4}},
            },
        )

        modal = ResumeEditSkillsModal(confirmation_view=view)

        assert modal.skills_input.default == "python: 5\ngo\nrust: 4"

    @pytest.mark.asyncio
    async def test_edit_roles_modal_prepopulates_list_values(self, crm_cog):
        """Edit Roles modal should pre-fill with proposed roles, one per line."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cRoles": ["developer", "marketing"]},
        )

        modal = ResumeEditRolesModal(confirmation_view=view)

        assert modal.roles_input.default == "developer\nmarketing"

    @pytest.mark.asyncio
    async def test_edit_discord_roles_modal_prepopulates_values(self, crm_cog):
        """Discord Roles modal should pre-fill with suggested role list."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=["Backend", "Operations"],
        )

        modal = ResumeEditDiscordRolesModal(confirmation_view=view)

        assert modal.discord_roles_input.default == "Backend\nOperations"

    @pytest.mark.asyncio
    async def test_apply_discord_roles_button_disabled_without_linked_discord_user(
        self, crm_cog
    ):
        """Apply button should be disabled if no Discord link target is available."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=["Backend", "Operations"],
        )

        apply_button = next(
            child
            for child in view.children
            if isinstance(child, ResumeApplyDiscordRolesButton)
        )
        assert apply_button.disabled is True

    @pytest.mark.asyncio
    async def test_edit_discord_roles_modal_submit_updates_suggested_roles(
        self, crm_cog
    ):
        """Submitting the Edit Discord Roles modal should deduplicate and update suggestions."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=["Backend"],
            discord_role_target_user_id="1001",
        )
        modal = ResumeEditDiscordRolesModal(confirmation_view=view)
        modal.discord_roles_input._value = (
            "Backend\nbackend\nOperations\nSteering Committee\nAdmin\n"
        )
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.response.send_message = AsyncMock()

        await modal.on_submit(interaction)

        assert view.discord_role_suggestions == ["Backend", "Operations"]
        apply_button = next(
            child
            for child in view.children
            if isinstance(child, ResumeApplyDiscordRolesButton)
        )
        assert apply_button.disabled is False
        interaction.response.send_message.assert_called_once_with(
            "✅ Discord roles updated to 2 roles.",
            ephemeral=True,
        )

    @pytest.mark.asyncio
    async def test_edit_discord_roles_modal_submit_keeps_apply_disabled_without_link(
        self, crm_cog
    ):
        """Apply Discord Roles remains disabled until the contact is linked."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=["Backend"],
        )
        modal = ResumeEditDiscordRolesModal(confirmation_view=view)
        modal.discord_roles_input._value = "Backend\nOperations"
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.response.send_message = AsyncMock()

        await modal.on_submit(interaction)

        apply_button = next(
            child
            for child in view.children
            if isinstance(child, ResumeApplyDiscordRolesButton)
        )
        assert apply_button.disabled is True
        interaction.response.send_message.assert_called_once_with(
            "✅ Discord roles updated to 2 roles.",
            ephemeral=True,
        )

    def test_build_role_suggestions_embed_marks_link_required_when_not_linked(
        self, crm_cog
    ):
        """Role suggestions embed should include a link required callout when role apply is blocked."""
        embed = crm_cog._build_role_suggestions_embed(
            contact_name="Test User",
            technical_roles=["Backend"],
            locality_roles=["APAC"],
            can_apply_discord_roles=False,
        )

        assert embed is not None
        assert any(field.name == "🔒 Link required" for field in embed.fields)

    def test_build_role_suggestions_embed_hides_link_required_when_linked(
        self, crm_cog
    ):
        """Role suggestions embed should hide link required callout when application is enabled."""
        embed = crm_cog._build_role_suggestions_embed(
            contact_name="Test User",
            technical_roles=["Backend"],
            locality_roles=["APAC"],
            can_apply_discord_roles=True,
        )

        assert embed is not None
        assert all(field.name != "🔒 Link required" for field in embed.fields)

    @pytest.mark.asyncio
    async def test_apply_discord_roles_button_applies_roles_and_reports_results(
        self, crm_cog
    ):
        """Apply Discord Roles should grant assignable roles and report status."""

        class _Role:
            def __init__(self, name: str, position: int, role_id: int = 0) -> None:
                self.name = name
                self.position = position
                self.id = role_id

            def __gt__(self, other: object) -> bool:
                if not isinstance(other, _Role):
                    return False
                if self.position != other.position:
                    return self.position > other.position
                return self.id > other.id

        bot_top_role = _Role("Bot", 99)
        target_top_role = _Role("Member", 10)
        backend_role = _Role("Backend", 30)
        blocked_role = _Role("Blocked", 150)
        manager_role = _Role("Manager", 5)

        bot_member = Mock()
        bot_member.top_role = bot_top_role
        bot_member.guild_permissions = Mock()
        bot_member.guild_permissions.manage_roles = True

        target_member = Mock()
        target_member.top_role = target_top_role
        target_member.roles = [manager_role]
        target_member.add_roles = AsyncMock()

        interaction = AsyncMock()
        interaction.guild = Mock()
        interaction.guild.get_member = Mock(return_value=target_member)
        interaction.guild.fetch_member = AsyncMock()
        interaction.guild.me = bot_member
        interaction.guild.roles = [backend_role, blocked_role, manager_role]
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.message = Mock()
        interaction.message.edit = AsyncMock()

        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
            discord_role_suggestions=[
                "Backend",
                "Manager",
                "Missing",
                "Blocked",
                "Member",
                "Admin",
                "Steering Committee",
            ],
            discord_role_target_user_id="1001",
        )
        button = next(
            child
            for child in view.children
            if isinstance(child, ResumeApplyDiscordRolesButton)
        )

        await button.callback(interaction)

        target_member.add_roles.assert_awaited_once()
        called_args = target_member.add_roles.await_args.args
        assert called_args[0].name == "Backend"
        interaction.response.defer.assert_awaited_once_with(
            thinking=True, ephemeral=True
        )
        interaction.message.edit.assert_awaited_once_with(view=view)
        summary = interaction.followup.send.call_args.args[0]
        assert "✅ Applied: Backend" in summary
        assert "Already present: Manager" in summary
        assert "Not found: Missing" in summary
        assert "Blocked by hierarchy: Blocked" in summary
        assert "Protected roles blocked: Member, Admin, Steering Committee" in summary

    @pytest.mark.asyncio
    async def test_edit_websites_modal_submit_updates_proposed(
        self, crm_cog, mock_interaction
    ):
        """Submitting the Edit Websites modal should replace proposed cWebsiteLink."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cWebsiteLink": ["https://old.com"]},
        )
        modal = ResumeEditWebsitesModal(confirmation_view=view)
        modal.websites_input._value = "https://new.com\nhttps://other.com"

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["cWebsiteLink"] == [
            "https://new.com",
            "https://other.com",
        ]
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_roles_modal_submit_updates_proposed(
        self, crm_cog, mock_interaction
    ):
        """Submitting the Edit Roles modal should replace proposed cRoles."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cRoles": ["developer"]},
        )
        modal = ResumeEditRolesModal(confirmation_view=view)
        modal.roles_input._value = "developer,marketing"

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["cRoles"] == ["developer", "marketing"]
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_social_links_modal_submit_updates_proposed(
        self, crm_cog, mock_interaction
    ):
        """Submitting the Edit Social Links modal should replace proposed cSocialLinks."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cSocialLinks": ["https://linkedin.com/in/old"]},
        )
        modal = ResumeEditSocialLinksModal(confirmation_view=view)
        modal.social_links_input._value = (
            "https://linkedin.com/in/new\nhttps://x.com/user"
        )

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["cSocialLinks"] == [
            "https://linkedin.com/in/new",
            "https://x.com/user",
        ]
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_location_modal_submit_updates_proposed(
        self, crm_cog, mock_interaction
    ):
        """Submitting the Edit Location modal should replace proposed location values."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"addressCountry": "Canada"},
        )
        modal = ResumeEditLocationModal(confirmation_view=view)
        modal.city_input._value = "Nanzih"
        modal.state_input._value = "Kaohsiung City"
        modal.country_input._value = "Taiwan"
        modal.timezone_input._value = "UTC+8"

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["addressCity"] == "Nanzih"
        assert view.proposed_updates["addressState"] == "Kaohsiung City"
        assert view.proposed_updates["addressCountry"] == "Taiwan"
        assert view.proposed_updates["cTimezone"] == "UTC+08:00"
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_location_modal_accepts_timezone_abbreviations(
        self, crm_cog, mock_interaction
    ):
        """Timezone abbreviations should match the parser behavior used elsewhere."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={},
        )
        modal = ResumeEditLocationModal(confirmation_view=view)
        modal.timezone_input._value = "PST"

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["cTimezone"] == "UTC-08:00"
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_skills_modal_submit_updates_proposed(
        self, crm_cog, mock_interaction
    ):
        """Submitting the Edit Skills modal should update skills and strengths."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "skills": ["python", "go", "rust"],
                "cSkillAttrs": json.dumps(
                    {"python": {"strength": 2}, "rust": {"strength": 4}}
                ),
            },
        )
        modal = ResumeEditSkillsModal(confirmation_view=view)
        modal.skills_input._value = "python: 5\nrust"

        await modal.on_submit(mock_interaction)

        assert view.proposed_updates["skills"] == ["python", "rust"]
        parsed_attrs = json.loads(view.proposed_updates["cSkillAttrs"])
        assert parsed_attrs == {
            "python": {"strength": 5},
            "rust": {"strength": 4},
        }
        mock_interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_edit_websites_modal_submit_removes_field_when_blank(
        self, crm_cog, mock_interaction
    ):
        """Clearing websites in the modal should remove cWebsiteLink from proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cWebsiteLink": ["https://example.com"]},
        )
        modal = ResumeEditWebsitesModal(confirmation_view=view)
        modal.websites_input._value = "   \n  \n  "

        await modal.on_submit(mock_interaction)

        assert "cWebsiteLink" not in view.proposed_updates

    @pytest.mark.asyncio
    async def test_edit_social_links_modal_submit_removes_field_when_blank(
        self, crm_cog, mock_interaction
    ):
        """Clearing social links in the modal should remove cSocialLinks from proposed updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cSocialLinks": ["https://linkedin.com/in/user"]},
        )
        modal = ResumeEditSocialLinksModal(confirmation_view=view)
        modal.social_links_input._value = ""

        await modal.on_submit(mock_interaction)

        assert "cSocialLinks" not in view.proposed_updates

    @pytest.mark.asyncio
    async def test_edit_roles_modal_submit_removes_field_when_blank(
        self, crm_cog, mock_interaction
    ):
        """Clearing roles in the modal should remove proposed cRoles."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cRoles": ["developer"]},
        )
        modal = ResumeEditRolesModal(confirmation_view=view)
        modal.roles_input._value = "   \n  \n  "

        await modal.on_submit(mock_interaction)

        assert "cRoles" not in view.proposed_updates

    @pytest.mark.asyncio
    async def test_edit_location_modal_submit_removes_fields_when_blank(
        self, crm_cog, mock_interaction
    ):
        """Clearing the location modal should remove proposed location updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "addressCity": "Nanzih",
                "addressState": "Kaohsiung City",
                "addressCountry": "Taiwan",
                "cTimezone": "UTC+08:00",
            },
        )
        modal = ResumeEditLocationModal(confirmation_view=view)
        modal.city_input._value = ""
        modal.state_input._value = ""
        modal.country_input._value = ""
        modal.timezone_input._value = ""

        await modal.on_submit(mock_interaction)

        assert "addressCity" not in view.proposed_updates
        assert "addressState" not in view.proposed_updates
        assert "addressCountry" not in view.proposed_updates
        assert "cTimezone" not in view.proposed_updates

    @pytest.mark.asyncio
    async def test_edit_skills_modal_submit_removes_fields_when_blank(
        self, crm_cog, mock_interaction
    ):
        """Clearing skills in the modal should remove skill updates."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={
                "skills": ["python", "go"],
                "cSkillAttrs": {"python": {"strength": 5}},
            },
        )
        modal = ResumeEditSkillsModal(confirmation_view=view)
        modal.skills_input._value = "   \n  \n  "

        await modal.on_submit(mock_interaction)

        assert "skills" not in view.proposed_updates
        assert "cSkillAttrs" not in view.proposed_updates

    def test_resume_preview_skills_delta_added_removed_and_strengths(self, crm_cog):
        """Skills preview should summarize added/removed/strength changes."""
        embed, _ = crm_cog._build_resume_preview_embed(
            contact_id="contact-1",
            contact_name="Test User",
            result={
                "proposed_changes": [
                    {
                        "field": "skills",
                        "label": "Skills",
                        "current": "python (3), go (2)",
                        "proposed": "python (4), rust (5)",
                    }
                ]
            },
            link_member=None,
        )

        proposed_field = next(
            field for field in embed.fields if field.name == "Proposed Changes"
        )
        assert "Added: rust (5)" in proposed_field.value
        assert "Strengths: python (3->4)" in proposed_field.value
        assert "Removed: go (2)" in proposed_field.value

    def test_resume_preview_skills_delta_noop_falls_back_to_full_line(self, crm_cog):
        """Skills preview should fall back to current → proposed when no delta."""
        embed, _ = crm_cog._build_resume_preview_embed(
            contact_id="contact-1",
            contact_name="Test User",
            result={
                "proposed_changes": [
                    {
                        "field": "skills",
                        "label": "Skills",
                        "current": "python (3), go (2)",
                        "proposed": "python (3), go (2)",
                    }
                ]
            },
            link_member=None,
        )

        proposed_field = next(
            field for field in embed.fields if field.name == "Proposed Changes"
        )
        assert "python (3), go (2)" in proposed_field.value
        assert "→" in proposed_field.value

    def test_resume_preview_groups_location_and_timezone_changes(self, crm_cog):
        """Location-related proposed changes should render as one grouped summary."""
        embed, _ = crm_cog._build_resume_preview_embed(
            contact_id="contact-1",
            contact_name="Test User",
            result={
                "proposed_changes": [
                    {
                        "field": "addressCity",
                        "label": "City",
                        "current": None,
                        "proposed": "Nanzih",
                    },
                    {
                        "field": "addressState",
                        "label": "State",
                        "current": None,
                        "proposed": "Kaohsiung City",
                    },
                    {
                        "field": "addressCountry",
                        "label": "Country",
                        "current": "Taiwan",
                        "proposed": "Taiwan",
                    },
                    {
                        "field": "cTimezone",
                        "label": "Timezone",
                        "current": None,
                        "proposed": "UTC+08:00",
                    },
                ]
            },
            link_member=None,
        )

        proposed_field = next(
            field for field in embed.fields if field.name == "Proposed Changes"
        )
        assert (
            "**Location**: `Taiwan` → "
            "`Nanzih, Kaohsiung City, Taiwan (Timezone: UTC+08:00)`"
        ) in proposed_field.value

    def test_resume_preview_embed_includes_debug_field(self, crm_cog):
        """Preview embeds should point operators at the raw extraction payload."""
        embed, _ = crm_cog._build_resume_preview_embed(
            contact_id="contact-1",
            contact_name="Test User",
            result={
                "proposed_changes": [],
                "extracted_profile": {
                    "source": "gpt-5-mini",
                    "confidence": 0.83,
                    "raw_llm_output": '{"address_city":"Berlin"}',
                    "raw_llm_json": {"address_city": "Berlin"},
                    "llm_fallback_reason": "ValueError: normalized with fallback",
                    "current_title": "Founding Engineer",
                    "recent_titles": ["Founding Engineer", "Software Engineer"],
                    "current_location_raw": "Berlin, Germany",
                    "current_location_source": "current_role",
                    "current_location_evidence": (
                        "Founding Engineer | Berlin, Germany | 2024-Present"
                    ),
                    "role_rationale": "Engineering titles indicate a developer profile.",
                },
            },
            link_member=None,
        )

        debug_field = next(field for field in embed.fields if field.name == "Debug")
        assert "resume-extract-debug.json" in debug_field.value
        assert "Fallback:" in debug_field.value
        evidence_field = next(
            field for field in embed.fields if field.name == "Inference Evidence"
        )
        assert "Founding Engineer" in evidence_field.value
        assert "Berlin, Germany" in evidence_field.value
        assert "current role" in evidence_field.value
        assert "developer profile" in evidence_field.value

    def test_resume_preview_embed_includes_discord_link_mention(self, crm_cog):
        link_member = Mock()
        link_member.id = 123456789
        link_member.__str__ = Mock(return_value="resume-user")

        embed, _ = crm_cog._build_resume_preview_embed(
            contact_id="contact-1",
            contact_name="Test User",
            result={"proposed_changes": []},
            link_member=link_member,
        )

        discord_link_field = next(
            field for field in embed.fields if field.name == "Discord Link"
        )
        assert "<@123456789>" in discord_link_field.value
        assert "resume-user" in discord_link_field.value

    def test_build_resume_extract_debug_file_serializes_raw_payload(self, crm_cog):
        """The debug attachment should include raw and normalized extraction payloads."""
        debug_file = crm_cog._build_resume_extract_debug_file(
            contact_id="contact-1",
            contact_name="Test User",
            attachment_id="att-1",
            filename="resume.pdf",
            result={
                "success": True,
                "proposed_updates": {"addressCountry": "Germany"},
                "proposed_changes": [{"field": "addressCountry"}],
                "extracted_profile": {
                    "source": "gpt-5-mini",
                    "confidence": 0.83,
                    "raw_llm_output": '{"address_city":"Berlin"}',
                    "raw_llm_json": {"address_city": "Berlin"},
                    "address_city": "Berlin",
                    "address_country": "Germany",
                },
            },
        )

        payload = json.loads(debug_file.fp.getvalue().decode("utf-8"))
        assert debug_file.filename == "resume-extract-debug.json"
        assert payload["raw_llm_output"] == '{"address_city":"Berlin"}'
        assert payload["raw_llm_json"]["address_city"] == "Berlin"
        assert payload["normalized_extracted_profile"]["address_country"] == "Germany"

    @pytest.mark.asyncio
    async def test_edit_websites_button_callback_opens_modal(
        self, crm_cog, mock_interaction
    ):
        """Edit Websites button callback should open the websites modal."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cWebsiteLink": ["https://example.com"]},
        )
        button = next(
            child
            for child in view.children
            if isinstance(child, ResumeEditWebsitesButton)
        )

        await button.callback(mock_interaction)

        mock_interaction.response.send_modal.assert_called_once()
        modal_arg = mock_interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, ResumeEditWebsitesModal)

    @pytest.mark.asyncio
    async def test_edit_social_links_button_callback_opens_modal(
        self, crm_cog, mock_interaction
    ):
        """Edit Social Links button callback should open the social links modal."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cSocialLinks": ["https://linkedin.com/in/user"]},
        )
        button = next(
            child
            for child in view.children
            if isinstance(child, ResumeEditSocialLinksButton)
        )

        await button.callback(mock_interaction)

        mock_interaction.response.send_modal.assert_called_once()
        modal_arg = mock_interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, ResumeEditSocialLinksModal)

    @pytest.mark.asyncio
    async def test_edit_roles_button_callback_opens_modal(
        self, crm_cog, mock_interaction
    ):
        """Edit Roles button callback should open the roles modal."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"cRoles": ["developer"]},
        )
        button = next(
            child for child in view.children if isinstance(child, ResumeEditRolesButton)
        )

        await button.callback(mock_interaction)

        mock_interaction.response.send_modal.assert_called_once()
        modal_arg = mock_interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, ResumeEditRolesModal)

    @pytest.mark.asyncio
    async def test_edit_location_button_callback_opens_modal(
        self, crm_cog, mock_interaction
    ):
        """Edit Location button callback should open the location modal."""
        view = ResumeUpdateConfirmationView(
            crm_cog=crm_cog,
            requester_id=123,
            contact_id="contact-1",
            contact_name="Test User",
            proposed_updates={"addressCountry": "Taiwan"},
        )
        button = next(
            child
            for child in view.children
            if isinstance(child, ResumeEditLocationButton)
        )

        await button.callback(mock_interaction)

        mock_interaction.response.send_modal.assert_called_once()
        modal_arg = mock_interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, ResumeEditLocationModal)

    @pytest.mark.asyncio
    async def test_download_and_send_resume_success(self, crm_cog, mock_interaction):
        """Test successful resume download and send."""
        # Mock API responses
        file_content = b"fake_pdf_content"
        file_info = {"name": "john_doe_resume.pdf"}

        crm_cog.espo_api.download_file.return_value = file_content
        crm_cog.espo_api.request.return_value = file_info

        await crm_cog._download_and_send_resume(
            mock_interaction, "John Doe", "resume123"
        )

        # Verify API calls
        crm_cog.espo_api.download_file.assert_called_once_with(
            "Attachment/file/resume123"
        )
        crm_cog.espo_api.request.assert_called_once_with("GET", "Attachment/resume123")

        # Verify Discord response
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "📄 Resume for **John Doe**:" in call_args[0][0]
        assert "file" in call_args[1]

    @pytest.mark.asyncio
    async def test_download_and_send_resume_api_error(self, crm_cog, mock_interaction):
        """Test resume download with API error."""
        crm_cog.espo_api.download_file.side_effect = EspoAPIError("API Error")

        await crm_cog._download_and_send_resume(
            mock_interaction, "John Doe", "resume123"
        )

        mock_interaction.followup.send.assert_called_once_with(
            "❌ Failed to download resume: API Error"
        )

    def test_role_id_cache_initializes_empty(self, jobs_cog):
        """Role ID cache should initialize empty on first access."""
        cache = jobs_cog._get_role_id_cache()

        assert cache == {}

    def test_refresh_role_id_cache_builds_casefold_map(self, jobs_cog):
        """Role ID cache should map casefolded role names to IDs."""
        role_frontend = Mock()
        role_frontend.name = "Frontend"
        role_frontend.id = 111
        role_frontend.position = 3

        role_full_stack = Mock()
        role_full_stack.name = "Full Stack"
        role_full_stack.id = 222
        role_full_stack.position = 2

        role_excluded = Mock()
        role_excluded.name = "Bots"
        role_excluded.id = 333
        role_excluded.position = 1

        guild = Mock()
        guild.id = 42
        guild.roles = [role_frontend, role_full_stack, role_excluded]

        with patch(
            "five08.discord_bot.cogs.jobs.DISCORD_ROLES_EXCLUDE_FROM_SYNC",
            {"Bots"},
        ):
            jobs_cog._refresh_role_id_cache(guild)

        cache = jobs_cog._get_role_id_cache()
        assert cache[42] == {"frontend": 111, "full stack": 222}

    @pytest.mark.asyncio
    async def test_on_guild_role_update_refreshes_cache(self, jobs_cog):
        """Role update events should refresh the role ID cache."""
        guild = Mock()
        before = Mock()
        before.guild = guild
        after = Mock()
        after.guild = guild

        with patch.object(jobs_cog, "_refresh_role_id_cache") as refresh:
            await jobs_cog.on_guild_role_update(before, after)

        refresh.assert_called_once_with(guild)

    @pytest.mark.asyncio
    async def test_match_candidates_sends_role_and_locality_mentions(
        self, jobs_cog, mock_interaction, mock_member_role
    ):
        """Match candidates should emit role/locality mention lines safely."""
        role_frontend = Mock()
        role_frontend.name = "Frontend"
        role_frontend.id = 111
        role_frontend.position = 3

        role_usa = Mock()
        role_usa.name = "USA"
        role_usa.id = 222
        role_usa.position = 2

        guild = Mock()
        guild.id = 55
        guild.roles = [role_frontend, role_usa]

        mock_interaction.guild = guild
        mock_interaction.user.id = 999
        mock_interaction.user.name = "Requester"
        mock_interaction.user.roles = [mock_member_role]

        starter_msg = Mock()
        starter_msg.content = "Example job"
        starter_msg.attachments = []
        starter_msg.embeds = []

        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

        class DummyThread:
            id = 123
            applied_tags = []

            def __init__(self, parent: DummyForumChannel) -> None:
                self.parent = parent

        thread_instance = DummyThread(DummyForumChannel(456))
        thread_instance.starter_message = starter_msg
        mock_interaction.channel = thread_instance

        requirements = Mock()
        requirements.title = "Frontend Engineer"
        requirements.discord_role_types = [" Frontend ", "Senior"]
        requirements.raw_location_text = "USA"
        requirements.preferred_timezones = []
        requirements.location_type = "us_only"
        requirements.required_skills = ["python"]
        requirements.preferred_skills = []
        requirements.seniority = "Senior"

        candidate = Mock()
        candidate.is_member = True
        candidate.name = "Alice (Nickname)"
        candidate.email_508 = "alice@508.dev"
        candidate.email = None
        candidate.crm_contact_id = None
        candidate.has_crm_link = False
        candidate.discord_user_id = 12345
        candidate.linkedin = None
        candidate.latest_resume_id = None
        candidate.latest_resume_name = None
        candidate.match_score = 9.2
        candidate.matched_required_skills = ["python"]
        candidate.matched_discord_roles = ["Frontend"]
        candidate.seniority = "Senior"
        candidate.timezone = "America/New_York"

        jobs_cog._refresh_role_id_cache(guild)

        with (
            patch(
                "five08.discord_bot.cogs.jobs.extract_job_requirements",
                return_value=requirements,
            ),
            patch(
                "five08.discord_bot.cogs.jobs.search_candidates",
                return_value=[candidate],
            ),
            patch(
                "five08.discord_bot.cogs.jobs.settings.espo_base_url",
                "https://crm.example.com",
            ),
            patch("five08.discord_bot.cogs.jobs.discord.Thread", DummyThread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
            patch.object(jobs_cog, "_audit_command"),
        ):
            await jobs_cog.match_candidates.callback(jobs_cog, mock_interaction)

        def assert_mentions_disabled(call):
            allowed = call.kwargs["allowed_mentions"]
            assert allowed.roles is False
            assert allowed.users is False
            assert allowed.everyone is False

        calls = mock_interaction.followup.send.call_args_list
        header_call = calls[0]
        assert header_call.args[0].startswith("## Job Match Results")
        assert_mentions_disabled(header_call)

        role_call = next(
            call
            for call in calls
            if call.args and call.args[0].startswith("Discord roles:")
        )
        assert "<@&111>" in role_call.args[0]
        role_allowed = role_call.kwargs["allowed_mentions"]
        assert [r.id for r in role_allowed.roles] == [111]
        assert role_call.kwargs["allowed_mentions"].users is False
        assert role_call.kwargs["allowed_mentions"].everyone is False

        locality_call = next(
            call for call in calls if call.args and call.args[0].startswith("Locality:")
        )
        assert "<@&222>" in locality_call.args[0]
        locality_allowed = locality_call.kwargs["allowed_mentions"]
        assert [r.id for r in locality_allowed.roles] == [222]
        assert locality_call.kwargs["allowed_mentions"].users is False
        assert locality_call.kwargs["allowed_mentions"].everyone is False

        candidate_call = next(
            call for call in calls if call.args and call.args[0].startswith("1. ")
        )
        assert "Alice (Nickname)" in candidate_call.args[0]
        assert "alice@508.dev" not in candidate_call.args[0]
        assert_mentions_disabled(candidate_call)
        assert mock_interaction.response.defer.call_args.kwargs["ephemeral"] is False

    @pytest.mark.asyncio
    async def test_match_candidates_private_arg_private_mode(
        self, jobs_cog, mock_interaction, mock_member_role
    ):
        """Passing a truthy private arg should send all results ephemerally."""
        role_frontend = Mock()
        role_frontend.name = "Frontend"
        role_frontend.id = 111
        role_frontend.position = 3

        role_usa = Mock()
        role_usa.name = "USA"
        role_usa.id = 222
        role_usa.position = 2

        guild = Mock()
        guild.id = 55
        guild.roles = [role_frontend, role_usa]

        mock_interaction.guild = guild
        mock_interaction.user.id = 999
        mock_interaction.user.name = "Requester"
        mock_interaction.user.roles = [mock_member_role]

        starter_msg = Mock()
        starter_msg.content = "Example job"
        starter_msg.attachments = []
        starter_msg.embeds = []

        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

        class DummyThread:
            id = 123
            applied_tags = []

            def __init__(self, parent: DummyForumChannel) -> None:
                self.parent = parent

        thread_instance = DummyThread(DummyForumChannel(456))
        thread_instance.starter_message = starter_msg
        mock_interaction.channel = thread_instance

        requirements = Mock()
        requirements.title = "Frontend Engineer"
        requirements.discord_role_types = [" Frontend ", "Senior"]
        requirements.raw_location_text = "USA"
        requirements.preferred_timezones = []
        requirements.location_type = "us_only"
        requirements.required_skills = ["python"]
        requirements.preferred_skills = []
        requirements.seniority = "Senior"

        candidate = Mock()
        candidate.is_member = True
        candidate.name = "Alice (Nickname)"
        candidate.email_508 = "alice@508.dev"
        candidate.email = None
        candidate.crm_contact_id = None
        candidate.has_crm_link = False
        candidate.discord_user_id = 12345
        candidate.linkedin = None
        candidate.latest_resume_id = None
        candidate.latest_resume_name = None
        candidate.match_score = 9.2
        candidate.matched_required_skills = ["python"]
        candidate.matched_discord_roles = ["Frontend"]
        candidate.seniority = "Senior"
        candidate.timezone = "America/New_York"

        jobs_cog._refresh_role_id_cache(guild)

        with (
            patch(
                "five08.discord_bot.cogs.jobs.extract_job_requirements",
                return_value=requirements,
            ),
            patch(
                "five08.discord_bot.cogs.jobs.search_candidates",
                return_value=[candidate],
            ),
            patch(
                "five08.discord_bot.cogs.jobs.settings.espo_base_url",
                "https://crm.example.com",
            ),
            patch("five08.discord_bot.cogs.jobs.discord.Thread", DummyThread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
            patch.object(jobs_cog, "_audit_command"),
        ):
            await jobs_cog.match_candidates.callback(jobs_cog, mock_interaction, "yes")

        assert mock_interaction.response.defer.call_args.kwargs["ephemeral"] is True

        calls = mock_interaction.followup.send.call_args_list
        assert calls
        for call in calls:
            assert call.kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_match_candidates_private_arg_rejects_falsey_value(
        self, jobs_cog, mock_interaction, mock_member_role
    ):
        """Passing a non-truthy private arg should be rejected."""
        mock_interaction.user.roles = [mock_member_role]

        await jobs_cog.match_candidates.callback(jobs_cog, mock_interaction, "no")

        mock_interaction.response.send_message.assert_called_once_with(
            "⚠️ Invalid value for `private`. Use `true`, `1`, `yes`, `y`, or `on`.",
            ephemeral=True,
        )
        mock_interaction.response.defer.assert_not_called()
        mock_interaction.followup.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_contacts_success(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test successful contact search."""
        # Mock user with Member role
        mock_interaction.user.roles = [mock_member_role]

        # Mock API responses with resume data included
        contacts_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john@example.com",
                    "type": "Member",
                    "c508Email": "john@508.dev",
                    "cDiscordUsername": "johndoe#1234",
                    "resumeIds": ["resume123"],
                    "resumeNames": {"resume123": "john_resume.pdf"},
                }
            ]
        }

        # Only need one API call now
        crm_cog.espo_api.request.return_value = contacts_response

        # Call the callback function to bypass app_commands decorator
        await crm_cog.search_members.callback(crm_cog, mock_interaction, "john")

        # Verify API calls - only one call needed now
        crm_cog.espo_api.request.assert_called_once()
        call_args = crm_cog.espo_api.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "Contact"

        # Verify response was sent
        mock_interaction.followup.send.assert_called_once()

    def test_resolve_jobs_channel_target_prefers_thread_parent(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        class DummyThread:
            def __init__(self, parent: DummyForumChannel) -> None:
                self.parent = parent

        parent = DummyForumChannel(456, "jobs")
        interaction = Mock()
        interaction.channel = DummyThread(parent)

        with (
            patch("five08.discord_bot.cogs.jobs.discord.Thread", DummyThread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel", DummyForumChannel
            ),
        ):
            target = jobs_cog._resolve_jobs_channel_target(interaction, None)

        assert target is parent

    def test_resolve_jobs_channel_target_uses_current_channel(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        channel = DummyForumChannel(456, "jobs")
        interaction = Mock()
        interaction.channel = channel

        with patch(
            "five08.discord_bot.cogs.jobs.discord.ForumChannel",
            DummyForumChannel,
        ):
            target = jobs_cog._resolve_jobs_channel_target(interaction, None)

        assert target is channel

    @pytest.mark.asyncio
    async def test_register_jobs_channel_updates_cache(
        self, jobs_cog, mock_interaction
    ):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        admin_role = Mock()
        admin_role.name = "Steering Committee"
        mock_interaction.user.roles = [admin_role]

        guild = Mock()
        guild.id = 123
        mock_interaction.guild = guild
        mock_interaction.channel = DummyForumChannel(456, "jobs")

        to_thread = AsyncMock(return_value=True)
        with (
            patch("five08.discord_bot.cogs.jobs.asyncio.to_thread", to_thread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
        ):
            await jobs_cog.register_jobs_channel.callback(
                jobs_cog, mock_interaction, None
            )

        to_thread.assert_awaited_once_with(
            jobs_module.register_job_post_channel,
            jobs_module.settings,
            guild_id="123",
            channel_id="456",
        )
        assert jobs_cog._jobs_channels_by_guild[guild.id] == {456}

    @pytest.mark.asyncio
    async def test_unregister_jobs_channel_updates_cache(
        self, jobs_cog, mock_interaction
    ):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        admin_role = Mock()
        admin_role.name = "Steering Committee"
        mock_interaction.user.roles = [admin_role]

        guild = Mock()
        guild.id = 123
        mock_interaction.guild = guild
        mock_interaction.channel = DummyForumChannel(456, "jobs")
        jobs_cog._jobs_channels_by_guild[guild.id] = {456}

        to_thread = AsyncMock(return_value=True)
        with (
            patch("five08.discord_bot.cogs.jobs.asyncio.to_thread", to_thread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
        ):
            await jobs_cog.unregister_jobs_channel.callback(
                jobs_cog, mock_interaction, None
            )

        to_thread.assert_awaited_once_with(
            jobs_module.unregister_job_post_channel,
            jobs_module.settings,
            guild_id="123",
            channel_id="456",
        )
        assert jobs_cog._jobs_channels_by_guild[guild.id] == set()

    @pytest.mark.asyncio
    async def test_register_jobs_channel_denies_non_admin(
        self, jobs_cog, mock_interaction
    ):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        guild = Mock()
        guild.id = 123
        mock_interaction.guild = guild
        mock_interaction.channel = DummyForumChannel(456, "jobs")

        to_thread = AsyncMock(return_value=True)
        with (
            patch("five08.discord_bot.cogs.jobs.asyncio.to_thread", to_thread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
        ):
            await jobs_cog.register_jobs_channel.callback(
                jobs_cog, mock_interaction, None
            )

        to_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unregister_jobs_channel_denies_non_admin(
        self, jobs_cog, mock_interaction
    ):
        class DummyForumChannel:
            def __init__(self, channel_id: int, name: str) -> None:
                self.id = channel_id
                self.name = name

        guild = Mock()
        guild.id = 123
        mock_interaction.guild = guild
        mock_interaction.channel = DummyForumChannel(456, "jobs")
        jobs_cog._jobs_channels_by_guild[guild.id] = {456}

        to_thread = AsyncMock(return_value=True)
        with (
            patch("five08.discord_bot.cogs.jobs.asyncio.to_thread", to_thread),
            patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ),
        ):
            await jobs_cog.unregister_jobs_channel.callback(
                jobs_cog, mock_interaction, None
            )

        to_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_on_thread_create_skips_non_forum_parent(self, jobs_cog):
        guild = Mock()
        guild.id = 123
        parent = Mock()
        parent.id = 456
        thread = Mock()
        thread.guild = guild
        thread.parent = parent
        jobs_cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
        jobs_cog._run_auto_match_candidates_for_thread = AsyncMock()

        await jobs_cog.on_thread_create(thread)

        jobs_cog._run_auto_match_candidates_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_thread_create_skips_unregistered_channel(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

            def permissions_for(self, _role):
                return Mock(view_channel=False)

        guild = Mock()
        guild.id = 123
        guild.default_role = Mock()
        parent = DummyForumChannel(456)
        thread = Mock()
        thread.guild = guild
        thread.parent = parent
        thread.owner_id = None

        jobs_cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
        jobs_cog._is_jobs_channel_registered = Mock(return_value=False)
        jobs_cog._run_auto_match_candidates_for_thread = AsyncMock()

        with patch(
            "five08.discord_bot.cogs.jobs.discord.ForumChannel",
            DummyForumChannel,
        ):
            await jobs_cog.on_thread_create(thread)

        jobs_cog._run_auto_match_candidates_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_thread_create_skips_bot_owner(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

            def permissions_for(self, _role):
                return Mock(view_channel=False)

        guild = Mock()
        guild.id = 123
        guild.default_role = Mock()
        parent = DummyForumChannel(456)
        thread = Mock()
        thread.guild = guild
        thread.parent = parent
        thread.owner_id = 999

        owner = Mock()
        owner.bot = True
        guild.get_member.return_value = owner

        jobs_cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
        jobs_cog._is_jobs_channel_registered = Mock(return_value=True)
        jobs_cog._run_auto_match_candidates_for_thread = AsyncMock()

        with patch(
            "five08.discord_bot.cogs.jobs.discord.ForumChannel",
            DummyForumChannel,
        ):
            await jobs_cog.on_thread_create(thread)

        jobs_cog._run_auto_match_candidates_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_thread_create_skips_non_member(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id

            def permissions_for(self, _role):
                return Mock(view_channel=False)

        guild = Mock()
        guild.id = 123
        guild.default_role = Mock()
        parent = DummyForumChannel(456)
        thread = Mock()
        thread.guild = guild
        thread.parent = parent
        thread.owner_id = 999

        owner = Mock()
        owner.bot = False
        owner.roles = [Mock()]
        guild.get_member.return_value = owner

        jobs_cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
        jobs_cog._is_jobs_channel_registered = Mock(return_value=True)
        jobs_cog._run_auto_match_candidates_for_thread = AsyncMock()

        with patch(
            "five08.discord_bot.cogs.jobs.check_user_roles_with_hierarchy"
        ) as check:
            check.return_value = False
            with patch(
                "five08.discord_bot.cogs.jobs.discord.ForumChannel",
                DummyForumChannel,
            ):
                await jobs_cog.on_thread_create(thread)

        jobs_cog._run_auto_match_candidates_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_thread_create_skips_public_forum(self, jobs_cog):
        class DummyForumChannel:
            def __init__(self, channel_id: int) -> None:
                self.id = channel_id
                self.name = "jobs"

            def permissions_for(self, _role):
                return Mock(view_channel=True)

        guild = Mock()
        guild.id = 123
        guild.name = "508"
        guild.default_role = Mock()
        parent = DummyForumChannel(456)
        thread = Mock()
        thread.guild = guild
        thread.parent = parent
        thread.owner_id = 999

        owner = Mock()
        owner.bot = False
        owner.roles = [Mock()]
        guild.get_member.return_value = owner

        jobs_cog._refresh_jobs_channel_cache_if_missing = AsyncMock(return_value=True)
        jobs_cog._is_jobs_channel_registered = Mock(return_value=True)
        jobs_cog._run_auto_match_candidates_for_thread = AsyncMock()

        with patch(
            "five08.discord_bot.cogs.jobs.discord.ForumChannel",
            DummyForumChannel,
        ):
            await jobs_cog.on_thread_create(thread)

        jobs_cog._run_auto_match_candidates_for_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_ready_runs_startup_sync_once(self, jobs_cog, mock_bot):
        guild = Mock()
        guild.id = 123
        guild.name = "508"
        mock_bot.guilds = [guild]

        jobs_cog._refresh_role_id_cache = Mock()
        jobs_cog._refresh_jobs_channel_cache = AsyncMock(return_value={456})
        jobs_cog._bulk_sync_guild_roles = AsyncMock(return_value=(1, 2, 3))

        await jobs_cog.on_ready()
        await jobs_cog.on_ready()

        jobs_cog._refresh_role_id_cache.assert_called_once_with(guild)
        jobs_cog._refresh_jobs_channel_cache.assert_awaited_once_with(guild.id)
        jobs_cog._bulk_sync_guild_roles.assert_awaited_once_with(guild)

    @pytest.mark.asyncio
    async def test_validate_match_candidates_url_rejects_non_https(self, jobs_cog):
        message = await jobs_cog._validate_match_candidates_url("http://example.com/jd")

        assert message == "Job description URL must use https."

    @pytest.mark.asyncio
    async def test_validate_match_candidates_url_rejects_private_hosts(self, jobs_cog):
        message = await jobs_cog._validate_match_candidates_url(
            "https://127.0.0.1/internal"
        )

        assert message == "Job description URL host resolves to a non-public address."

    @pytest.mark.asyncio
    async def test_on_member_update_skips_bot_members(self, jobs_cog):
        before = Mock()
        before.roles = []
        before.display_name = "Bot"
        before.name = "bot"

        after = Mock()
        after.guild = Mock()
        after.bot = True
        after.roles = [Mock()]
        after.display_name = "Bot"
        after.name = "bot"

        with patch("five08.discord_bot.cogs.jobs.asyncio.to_thread") as to_thread:
            await jobs_cog.on_member_update(before, after)

        to_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_contacts_requires_query_or_skills(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search requires a query or skills."""
        mock_interaction.user.roles = [mock_member_role]

        await crm_cog.search_members.callback(crm_cog, mock_interaction, None, None)

        mock_interaction.followup.send.assert_called_once_with(
            "❌ Please provide a search term or skills to search by."
        )
        crm_cog.espo_api.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_contacts_skills_only(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search by skills only."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.search_members.callback(
            crm_cog, mock_interaction, None, "python,  sql "
        )

        crm_cog.espo_api.request.assert_called_once()
        call_args = crm_cog.espo_api.request.call_args
        search_params = call_args[0][2]
        where_filters = search_params["where"]
        select_fields = search_params["select"].split(",")

        assert len(where_filters) == 1
        assert where_filters[0]["type"] == "arrayAllOf"
        assert where_filters[0]["attribute"] == "skills"
        assert where_filters[0]["value"] == ["python", "sql"]
        assert "skills" in select_fields
        assert "cSkillAttrs" in select_fields

    @pytest.mark.asyncio
    async def test_search_contacts_query_and_skills(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search by query and skills."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.search_members.callback(
            crm_cog, mock_interaction, "john", "python,sql"
        )

        crm_cog.espo_api.request.assert_called_once()
        call_args = crm_cog.espo_api.request.call_args
        search_params = call_args[0][2]
        where_filters = search_params["where"]

        assert where_filters[0]["type"] == "or"
        assert where_filters[1]["type"] == "arrayAllOf"
        assert where_filters[1]["attribute"] == "skills"
        assert where_filters[1]["value"] == ["python", "sql"]

    @pytest.mark.asyncio
    async def test_search_contacts_skills_query_normalizes_to_lowercase(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Skill-only search should normalize incoming terms before arrayAllOf filtering."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.search_members.callback(
            crm_cog, mock_interaction, None, "Python, FastAPI "
        )

        crm_cog.espo_api.request.assert_called_once()
        call_args = crm_cog.espo_api.request.call_args
        search_params = call_args[0][2]
        assert search_params["where"][0]["type"] == "arrayAllOf"
        assert search_params["where"][0]["value"] == ["python", "fastapi"]

    def test_parse_contact_skill_attrs_recovers_python_literal(self, crm_cog):
        """Malformed JSON-like skill attrs should recover via literal parsing."""
        parsed = crm_cog._parse_contact_skill_attrs(
            "{'python': {'strength': 4}, 'go': {'strength': 3},}"
        )
        assert parsed == {"python": 4, "go": 3}

    @pytest.mark.asyncio
    async def test_format_contact_card_supports_additional_fields(self, crm_cog):
        """Contact cards should include optional extra lines when provided."""
        contact = {
            "id": "contact123",
            "name": "John Doe",
            "emailAddress": "john@example.com",
            "type": "Member",
            "c508Email": "john@508.dev",
            "cDiscordUsername": "johndoe#1234",
        }

        formatted = crm_cog._format_contact_card(
            contact,
            interaction=None,
            additional_fields=[
                ("📌 Onboarding Status", "pending"),
                ("🧑‍💼 Onboarder", "mentor"),
                ("⚪ Optional", ""),
            ],
        )

        assert "📌 Onboarding Status: pending" in formatted
        assert "🧑‍💼 Onboarder: mentor" in formatted
        assert "⚪ Optional" not in formatted
        assert "🔗 [View in CRM]" in formatted
        assert "🏢 508 Email: john@508.dev" in formatted

    @pytest.mark.asyncio
    async def test_resolve_onboarder_username_normalizes_direct_username(self, crm_cog):
        """Direct 508 username values should normalize to lowercase without domain."""
        resolved = await crm_cog._resolve_onboarder_username(
            interaction=Mock(), raw_onboarder="John@508.dev"
        )

        assert resolved == "john"

    @pytest.mark.asyncio
    async def test_resolve_onboarder_username_maps_discord_mention(self, crm_cog):
        """Discord mentions should resolve to linked contact 508 usernames."""
        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value={"c508Email": "Mentor@508.dev"}),
        ):
            resolved = await crm_cog._resolve_onboarder_username(
                interaction=Mock(), raw_onboarder="<@987654321>"
            )

        assert resolved == "mentor"

    @pytest.mark.asyncio
    async def test_resolve_onboarder_username_returns_none_for_unlinked_mention(
        self, crm_cog
    ):
        """Unlinked mention values should fail resolution."""
        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value=None),
        ):
            resolved = await crm_cog._resolve_onboarder_username(
                interaction=Mock(), raw_onboarder="<@987654321>"
            )

        assert resolved is None

    @pytest.mark.asyncio
    async def test_assign_onboarder_success_updates_pending_state(
        self, crm_cog, mock_interaction
    ):
        """Assigning onboarder should set pending state to selected."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[{"id": "contact123", "name": "John Doe"}]),
        ):
            crm_cog.espo_api.request.side_effect = [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "cOnboarder": "none",
                    "cOnboardingState": "pending",
                },
                {"id": "contact123"},
            ]

            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "jane"
            )

        request_calls = crm_cog.espo_api.request.call_args_list
        assert request_calls[0].args == ("GET", "Contact/contact123")
        assert request_calls[1].args == (
            "PUT",
            "Contact/contact123",
            {"cOnboarder": "jane", "cOnboardingState": "selected"},
        )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "onboarding state set to `selected`" in message
        assert "Assigned **jane** as onboarder" in message

    @pytest.mark.asyncio
    async def test_assign_onboarder_success_keeps_state_when_not_pending(
        self, crm_cog, mock_interaction
    ):
        """Assigning onboarder should preserve existing non-pending onboarding state."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[{"id": "contact123", "name": "John Doe"}]),
        ):
            crm_cog.espo_api.request.side_effect = [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "cOnboardingCoordinator": "old",
                    "cOnboardingStatus": "onboarded",
                },
                {"id": "contact123"},
            ]

            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "jane"
            )

        payload = crm_cog.espo_api.request.call_args_list[1][0][2]
        assert payload == {"cOnboardingCoordinator": "jane"}
        message = mock_interaction.followup.send.call_args[0][0]
        assert "onboarding state left unchanged" in message

    @pytest.mark.asyncio
    async def test_assign_onboarder_multiple_matches_returns_prompt(
        self, crm_cog, mock_interaction
    ):
        """Multiple matches should return a guided selection embed instead of updating."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]
        crm_cog._audit_command = Mock()

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(
                return_value=[
                    {"id": "contact123", "name": "John Doe"},
                    {"id": "contact456", "name": "John Smith"},
                ]
            ),
        ):
            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "jane"
            )

        crm_cog.espo_api.request.assert_not_called()
        embed = mock_interaction.followup.send.call_args[1]["embed"]
        assert embed.title == "⚠️ Multiple Contacts Found"
        assert len(embed.fields) == 2
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.assign_onboarder"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "contact": "john",
            "onboarder": "jane",
            "contacts_found": 2,
        }

    @pytest.mark.asyncio
    async def test_assign_onboarder_missing_contact(self, crm_cog, mock_interaction):
        """No matching contact should return a not-found message."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]
        crm_cog._audit_command = Mock()

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[]),
        ):
            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "missing", "jane"
            )

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "❌ No contact found for: `missing`" in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.assign_onboarder"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "contact": "missing",
            "onboarder": "jane",
        }

    @pytest.mark.asyncio
    async def test_assign_onboarder_invalid_onboarder_reference(
        self, crm_cog, mock_interaction
    ):
        """Unresolvable onboarder references should fail fast with validation message."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]
        crm_cog._audit_command = Mock()

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(side_effect=ValueError("not found")),
        ):
            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "<@987654321>"
            )

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Could not resolve a valid 508 onboarder username." in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.assign_onboarder"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "contact": "john",
            "onboarder": "<@987654321>",
        }

    @pytest.mark.asyncio
    async def test_assign_onboarder_missing_onboarder_field_records_error(
        self, crm_cog, mock_interaction
    ):
        """Contacts without an onboarder field should return an actionable error."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]
        crm_cog._audit_command = Mock()

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[{"id": "contact123", "name": "John Doe"}]),
        ):
            crm_cog.espo_api.request.return_value = {
                "id": "contact123",
                "name": "John Doe",
            }
            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "jane"
            )

        crm_cog.espo_api.request.assert_called_once_with("GET", "Contact/contact123")
        message = mock_interaction.followup.send.call_args[0][0]
        assert (
            "Could not locate a known onboarder field for this CRM contact." in message
        )
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.assign_onboarder"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "contact_id": "contact123",
            "onboarder": "jane",
            "reason": "missing_onboarder_field",
        }
        assert audit_kwargs["resource_type"] == "crm_contact"
        assert audit_kwargs["resource_id"] == "contact123"

    @pytest.mark.asyncio
    async def test_assign_onboarder_handles_espo_api_error(
        self, crm_cog, mock_interaction
    ):
        """Espo API errors should be surfaced in assign-onboarder flow."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[{"id": "contact123", "name": "John Doe"}]),
        ):
            crm_cog.espo_api.request.side_effect = EspoAPIError("CRM unavailable")
            await crm_cog.assign_onboarder.callback(
                crm_cog, mock_interaction, "john", "jane"
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "❌ CRM API error: CRM unavailable" in message

    @pytest.mark.asyncio
    async def test_view_onboarding_queue_lists_open_entries(
        self, crm_cog, mock_interaction
    ):
        """Onboarding queue should filter out completed/waitlist/rejected states."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        crm_cog.espo_api.request.return_value = {
            "list": [
                {
                    "id": "c1",
                    "name": "Alice",
                    "cOnboardingState": "pending",
                    "cOnboarder": "mentorA",
                    "type": "Member",
                },
                {"id": "c2", "name": "Bob", "cOnboardingStatus": "onboarded"},
                {"id": "c3", "name": "Cara", "cOnboarding": "waitlist"},
                {"id": "c4", "name": "Drew", "cOnboarding": "rejected"},
                {
                    "id": "c5",
                    "name": "Eli",
                    "cOnboardingStatus": "",
                    "type": "Candidate / Member",
                },
            ]
        }

        await crm_cog.view_onboarding_queue.callback(crm_cog, mock_interaction)

        crm_cog.espo_api.request.assert_called_once_with(
            "GET",
            "Contact",
            {
                "maxSize": 200,
                "select": (
                    "id,name,emailAddress,cDiscordUsername,cDiscordUserID,"
                    "cOnboardingState,cOnboardingStatus,cOnboarding,"
                    "cOnboarder,cOnboardingCoordinator,cOnboardingUpdatedAt"
                ),
            },
        )

        send_kwargs = mock_interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        assert "view" in send_kwargs
        embed = send_kwargs["embed"]
        names = [field.name for field in embed.fields]
        values = [field.value for field in embed.fields]
        assert any("Contact: Alice" in name for name in names)
        assert all("Contact: Eli" not in name for name in names)
        assert any("📧 **Email:** No email" in value for value in values)
        assert any("💬 **Linked Discord:** No Discord" in value for value in values)
        assert any("🧑‍💼 **cOnboarder:** mentorA" in value for value in values)
        assert any("📌 **cOnboardingState:** pending" in value for value in values)
        assert any("🔗 [View in CRM](" in value for value in values)

        view = send_kwargs["view"]
        queue_rows = getattr(view, "queue_rows", None)
        assert queue_rows is not None
        queued_names = {row.get("name") for row in queue_rows}
        assert queued_names == {"Alice", "Eli"}
        for row in queue_rows:
            assert row.get("status") not in {"onboarded", "waitlist", "rejected"}

    @pytest.mark.asyncio
    async def test_view_onboarding_queue_empty_when_only_excluded(
        self, crm_cog, mock_interaction
    ):
        """Queue should return friendly message when only excluded states exist."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        crm_cog.espo_api.request.return_value = {
            "list": [
                {"id": "c1", "name": "Bob", "cOnboardingState": "onboarded"},
                {"id": "c2", "name": "Cara", "cOnboardingState": "waitlist"},
                {"id": "c3", "name": "Drew", "cOnboarding": "rejected"},
            ]
        }

        await crm_cog.view_onboarding_queue.callback(crm_cog, mock_interaction)

        crm_cog.espo_api.request.assert_called_once_with(
            "GET",
            "Contact",
            {
                "maxSize": 200,
                "select": (
                    "id,name,emailAddress,cDiscordUsername,cDiscordUserID,"
                    "cOnboardingState,cOnboardingStatus,cOnboarding,"
                    "cOnboarder,cOnboardingCoordinator,cOnboardingUpdatedAt"
                ),
            },
        )
        message = mock_interaction.followup.send.call_args[0][0]
        assert "✅ No contacts found in onboarding queue." in message

    @pytest.mark.asyncio
    async def test_view_onboarding_queue_handles_api_error(
        self, crm_cog, mock_interaction
    ):
        """Onboarding queue should report CRM API errors clearly."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]
        crm_cog.espo_api.request.side_effect = EspoAPIError("Queue service down")

        await crm_cog.view_onboarding_queue.callback(crm_cog, mock_interaction)

        message = mock_interaction.followup.send.call_args[0][0]
        assert "❌ CRM API error: Queue service down" in message

    @pytest.mark.asyncio
    async def test_view_onboarding_queue_uses_pagination_for_large_queues(
        self, crm_cog, mock_interaction
    ):
        """Large queues should be returned as paginated embeds."""
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        long_name_suffix = "X" * 120
        long_email_suffix = "y" * 80
        crm_cog.espo_api.request.return_value = {
            "list": [
                {
                    "id": f"c{i}",
                    "name": f"Contact {i} {long_name_suffix}",
                    "emailAddress": f"user{i}@{long_email_suffix}.example.com",
                    "type": "Member",
                    "c508Email": f"member{i}@508.dev",
                    "cDiscordUsername": f"member{i}#1234",
                    "cOnboardingState": "pending",
                    "cOnboarder": f"mentor{i}",
                }
                for i in range(1, 26)
            ]
        }

        await crm_cog.view_onboarding_queue.callback(crm_cog, mock_interaction)

        assert mock_interaction.followup.send.call_count == 1
        send_kwargs = mock_interaction.followup.send.call_args[1]
        assert "embed" in send_kwargs
        assert "view" in send_kwargs
        assert send_kwargs["view"].total_pages > 1

    def test_format_onboarding_updated_at_normalizes_timezone(self, crm_cog):
        """Timestamps should be normalized consistently for display."""
        assert crm_cog._format_onboarding_updated_at(0) == "1970-01-01 00:00 UTC"
        assert (
            crm_cog._format_onboarding_updated_at("2026-03-03T12:00:00-05:00")
            == "2026-03-03 17:00 UTC"
        )
        assert crm_cog._format_onboarding_updated_at("2026-03-03T12:00:00") == (
            "2026-03-03 12:00"
        )
        assert crm_cog._format_onboarding_updated_at("2026-03-03T00:00:00Z") == (
            "2026-03-03"
        )

    @pytest.mark.asyncio
    async def test_resume_create_contact_view_cancel_path(
        self, crm_cog, mock_interaction
    ):
        """Canceling contact creation should not create a contact."""
        original_interaction = Mock()
        original_interaction.user = Mock(id=101)

        mock_interaction.user.id = 101
        mock_interaction.response = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        mock_interaction.message = AsyncMock()
        mock_interaction.message.edit = AsyncMock()

        crm_cog._audit_command = Mock()

        view = ResumeCreateContactView(
            crm_cog=crm_cog,
            interaction=original_interaction,
            file_content=b"resume-bytes",
            filename="candidate.pdf",
            file_size=1024,
            search_term=None,
            overwrite=False,
            link_user=None,
            inferred_contact_meta={"reason": "no_matching_contact"},
            target_scope="resume_inferred",
        )

        cancel_button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.label == "Cancel"
        )

        await cancel_button.callback(mock_interaction)

        crm_cog.espo_api.request.assert_not_called()
        crm_cog._audit_command.assert_called_once()
        mock_interaction.response.send_message.assert_called_once_with(
            "Contact creation cancelled. No changes were made.",
            ephemeral=True,
        )
        assert all(
            isinstance(item, discord.ui.Button) and item.disabled
            for item in view.children
        )

    @pytest.mark.asyncio
    async def test_view_skills_self_uses_structured_attrs(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """No search term should resolve self and display structured skill strengths."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 123456789

        crm_cog.espo_api.request.side_effect = [
            {"list": [{"id": "contact123", "name": "John Doe"}]},
            {
                "id": "contact123",
                "name": "John Doe",
                "cSkillAttrs": '{"python":{"strength":4},"go":{"strength":5}}',
                "skills": ["python", "go"],
            },
        ]

        await crm_cog.view_skills.callback(crm_cog, mock_interaction, None)

        assert crm_cog.espo_api.request.call_count == 2
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        assert embed.title == "🛠️ CRM Skills"
        assert "Skills for **John Doe**" in embed.description
        assert "go (5)" in embed.fields[0].value
        assert "python (4)" in embed.fields[0].value

    @pytest.mark.asyncio
    async def test_view_skills_falls_back_to_skills_when_attrs_unrecoverable(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """If attrs cannot be recovered, command should display skills multi-enum."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 123456789

        crm_cog.espo_api.request.side_effect = [
            {"list": [{"id": "contact123", "name": "John Doe"}]},
            {
                "id": "contact123",
                "name": "John Doe",
                "cSkillAttrs": "{broken-json",
                "skills": ["Python", " SQL "],
            },
        ]

        await crm_cog.view_skills.callback(crm_cog, mock_interaction, None)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        assert "python" in embed.fields[0].value
        assert "sql" in embed.fields[0].value
        assert "/5" not in embed.fields[0].value

    @pytest.mark.asyncio
    async def test_search_contacts_for_view_skills_delegates_to_linking(self, crm_cog):
        """`_search_contacts_for_view_skills` should delegate to `_search_contact_for_linking`."""
        expected = [{"id": "contact123"}]
        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=expected),
        ) as mock_search:
            result = await crm_cog._search_contacts_for_view_skills("john")

        assert result == expected
        mock_search.assert_awaited_once_with("john")

    @pytest.mark.asyncio
    async def test_view_skills_multiple_contacts_requires_refine(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Search term with multiple matches should ask caller to refine."""
        mock_interaction.user.roles = [mock_member_role]

        with patch.object(crm_cog, "_search_contacts_for_view_skills") as mock_search:
            mock_search.return_value = [
                {"id": "contact1", "name": "John Doe"},
                {"id": "contact2", "name": "John Smith"},
            ]

            await crm_cog.view_skills.callback(crm_cog, mock_interaction, "john")

        crm_cog.espo_api.request.assert_not_called()
        mock_interaction.followup.send.assert_called_once()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Multiple contacts found" in message
        assert "John Doe" in message
        assert "John Smith" in message

    @pytest.mark.asyncio
    async def test_view_skills_self_not_linked(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """No search term should error when requester is not linked to CRM."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 123456789
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.view_skills.callback(crm_cog, mock_interaction, None)

        crm_cog.espo_api.request.assert_called_once()
        mock_interaction.followup.send.assert_called_once()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Discord account is not linked to a CRM contact" in message

    @pytest.mark.asyncio
    async def test_search_contacts_shows_skill_strengths(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search displays requested skills with strengths."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john@example.com",
                    "type": "Member",
                    "c508Email": "john@508.dev",
                    "cDiscordUsername": "johndoe",
                    "cSkillAttrs": {
                        "python": {"strength": 5},
                        "sql": {"strength": "4"},
                        "aws": {"strength": "6"},
                        "gcp": "1",
                    },
                }
            ]
        }

        await crm_cog.search_members.callback(
            crm_cog, mock_interaction, None, "python,sql,aws"
        )

        mock_interaction.followup.send.assert_called_once()
        embed = mock_interaction.followup.send.call_args.kwargs["embed"]
        field_value = embed.fields[0].value
        assert "🧠 Skills: python (5), sql (4), amazon web services" in field_value

    @pytest.mark.asyncio
    async def test_search_contacts_ignores_broken_skill_attrs(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search ignores malformed cSkillAttrs payloads."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john@example.com",
                    "type": "Member",
                    "cSkillAttrs": "not-json",
                }
            ]
        }

        await crm_cog.search_members.callback(
            crm_cog, mock_interaction, None, "python,sql"
        )

        mock_interaction.followup.send.assert_called_once()
        embed = mock_interaction.followup.send.call_args.kwargs["embed"]
        field_value = embed.fields[0].value
        assert "🧠 Skills: python, sql" in field_value

    @pytest.mark.asyncio
    async def test_search_contacts_no_results(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test contact search with no results."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {"list": []}

        # Call the callback function to bypass app_commands decorator
        await crm_cog.search_members.callback(crm_cog, mock_interaction, "nonexistent")

        mock_interaction.followup.send.assert_called_once_with(
            "🔍 No contacts found for: `nonexistent`"
        )

    @pytest.mark.asyncio
    async def test_get_resume_success(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test successful resume retrieval."""
        mock_interaction.user.roles = [mock_member_role]

        # Mock contact search response with resume data included
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "resumeIds": ["resume123"],
                    "resumeNames": {"resume123": "john_resume.pdf"},
                }
            ]
        }

        # Mock file info response
        file_info_response = {"name": "john_resume.pdf"}

        # Set up side_effect for API calls
        crm_cog.espo_api.request.side_effect = [contact_response, file_info_response]

        # Mock file download
        crm_cog.espo_api.download_file.return_value = b"fake_pdf"

        # Call the callback function to bypass app_commands decorator
        await crm_cog.get_resume.callback(crm_cog, mock_interaction, "john@508.dev")

        # Verify API calls (search + file info)
        assert crm_cog.espo_api.request.call_count == 2
        # Verify file download was called
        crm_cog.espo_api.download_file.assert_called_once()
        mock_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_resume_contact_not_found(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test resume retrieval when contact not found."""
        mock_interaction.user.roles = [mock_member_role]
        crm_cog.espo_api.request.return_value = {"list": []}

        # Call the callback function to bypass app_commands decorator
        await crm_cog.get_resume.callback(
            crm_cog, mock_interaction, "nonexistent@example.com"
        )

        mock_interaction.followup.send.assert_called_once_with(
            "❌ No contact found for: `nonexistent@example.com`"
        )

    @pytest.mark.asyncio
    async def test_get_resume_no_resume_found(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test resume retrieval when contact has no resume."""
        mock_interaction.user.roles = [mock_member_role]

        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "resumeIds": [],
                    "resumeNames": {},
                }
            ]
        }

        crm_cog.espo_api.request.return_value = contact_response

        # Call the callback function to bypass app_commands decorator
        await crm_cog.get_resume.callback(crm_cog, mock_interaction, "john@508.dev")

        mock_interaction.followup.send.assert_called_once_with(
            "❌ No resume found for John Doe"
        )

    @pytest.mark.asyncio
    async def test_link_discord_user_success(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test successful Discord user linking."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789
        mock_discord_user.mention = "<@123456789>"
        mock_discord_user.discriminator = "1234"

        # Mock contact search response
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john@example.com",
                    "c508Email": "john@508.dev",
                    "cDiscordUsername": "olduser#0000 (ID: 987654321)",
                }
            ]
        }

        # Mock update response
        update_response = {"id": "contact123"}

        crm_cog.espo_api.request.side_effect = [contact_response, update_response]

        # Call the command
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "john"
        )

        # Verify API calls
        assert crm_cog.espo_api.request.call_count == 2

        # Verify search call
        search_call = crm_cog.espo_api.request.call_args_list[0]
        assert search_call[0][0] == "GET"
        assert search_call[0][1] == "Contact"

        # Verify update call
        update_call = crm_cog.espo_api.request.call_args_list[1]
        assert update_call[0][0] == "PUT"
        assert update_call[0][1] == "Contact/contact123"
        assert "cDiscordUsername" in update_call[0][2]
        assert update_call[0][2]["cDiscordUsername"] == "johndoe#1234"
        assert "cDiscordUserID" in update_call[0][2]
        assert update_call[0][2]["cDiscordUserID"] == "123456789"

        # Verify success response
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]

    @pytest.mark.asyncio
    async def test_link_discord_user_contact_not_found(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking when contact not found."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"

        # Mock empty contact response
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "nonexistent"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "❌ No contact found" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_link_discord_user_name_search(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test name search in Discord user linking."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"

        # Mock contact response
        contact_response = {"list": []}
        crm_cog.espo_api.request.return_value = contact_response

        # Test username normalization
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "john"
        )

        # Verify the search was performed (should search by name since "john" has no @ or space)
        call_args = crm_cog.espo_api.request.call_args
        search_params = call_args[0][2]  # Third argument is the search params
        # Check that it searched for "john" as a name
        first_where = search_params["where"][0]
        if first_where.get("type") == "or" and isinstance(
            first_where.get("value"), list
        ):
            where_filters = first_where["value"]
            assert isinstance(where_filters, list)
            where_filter = next(
                (
                    item
                    for item in where_filters
                    if isinstance(item, dict) and item.get("attribute") == "name"
                ),
                None,
            )
            assert where_filter is not None
            assert where_filter.get("value") == "john"
            return

        assert first_where.get("attribute") == "name"
        assert first_where.get("value") == "john"

    @pytest.mark.asyncio
    async def test_link_discord_user_modern_username(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking with modern username (no discriminator)."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user without discriminator
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789
        mock_discord_user.discriminator = "0"  # Modern Discord users have "0"

        # Mock contact and update responses
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "cDiscordUsername": "",
                }
            ]
        }
        update_response = {"id": "contact123"}

        crm_cog.espo_api.request.side_effect = [contact_response, update_response]

        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "john@508.dev"
        )

        # Verify update call used format without discriminator
        update_call = crm_cog.espo_api.request.call_args_list[1]
        discord_username = update_call[0][2]["cDiscordUsername"]
        assert discord_username == "johndoe"
        assert "#0" not in discord_username
        assert "cDiscordUserID" in update_call[0][2]
        assert update_call[0][2]["cDiscordUserID"] == "123456789"

    @pytest.mark.asyncio
    async def test_link_discord_user_hex_id_search(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking with hex contact ID."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789
        mock_discord_user.mention = "<@123456789>"
        mock_discord_user.discriminator = "0"

        # Mock contact response for direct ID lookup
        contact_response = {
            "id": "65a6b62400e7d0079",
            "name": "John Doe",
            "emailAddress": "john@example.com",
        }
        update_response = {"id": "65a6b62400e7d0079"}

        crm_cog.espo_api.request.side_effect = [contact_response, update_response]

        # Call the command with hex ID
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "65a6b62400e7d0079"
        )

        # Verify direct ID lookup was used
        first_call = crm_cog.espo_api.request.call_args_list[0]
        assert first_call[0][0] == "GET"
        assert first_call[0][1] == "Contact/65a6b62400e7d0079"

        # Verify success response
        mock_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_link_discord_user_email_search(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking with email search."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789
        mock_discord_user.mention = "<@123456789>"
        mock_discord_user.discriminator = "0"

        # Mock contact search response
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john@508.dev",
                }
            ]
        }
        update_response = {"id": "contact123"}

        crm_cog.espo_api.request.side_effect = [contact_response, update_response]

        # Call the command with email
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "john@508.dev"
        )

        # Verify email search was used
        search_call = crm_cog.espo_api.request.call_args_list[0]
        assert search_call[0][0] == "GET"
        assert search_call[0][1] == "Contact"
        search_params = search_call[0][2]
        assert search_params["where"][0]["type"] == "or"
        # Check that it searches both email fields
        email_searches = search_params["where"][0]["value"]
        assert any(param["attribute"] == "emailAddress" for param in email_searches)
        assert any(param["attribute"] == "c508Email" for param in email_searches)

    @pytest.mark.asyncio
    async def test_link_discord_user_multiple_results(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking when multiple contacts found."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789

        # Mock contact search response with multiple results
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john1@example.com",
                },
                {
                    "id": "contact456",
                    "name": "John Smith",
                    "emailAddress": "john2@example.com",
                },
            ]
        }

        crm_cog.espo_api.request.return_value = contact_response

        # Call the command with a name that returns multiple results
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "John"
        )

        # Verify choices were shown instead of linking
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert "Multiple Contacts Found" in embed.title

    @pytest.mark.asyncio
    async def test_link_discord_user_deduplication(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test Discord user linking with duplicate contacts (same ID)."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock Discord user
        mock_discord_user = Mock()
        mock_discord_user.name = "johndoe"
        mock_discord_user.id = 123456789

        # Mock contact search response with duplicates (same ID)
        contact_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "John Doe",
                    "emailAddress": "john1@example.com",
                },
                {
                    "id": "contact123",  # Duplicate ID
                    "name": "John Doe",
                    "emailAddress": "john1@example.com",
                },
                {
                    "id": "contact456",
                    "name": "John Smith",
                    "emailAddress": "john2@example.com",
                },
            ]
        }

        crm_cog.espo_api.request.return_value = contact_response

        # Call the command - should deduplicate and show choices
        await crm_cog.link_discord_user.callback(
            crm_cog, mock_interaction, mock_discord_user, "John"
        )

        # Verify choices were shown with deduplicated contacts
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        assert "view" in call_args[1]  # Should have buttons
        embed = call_args[1]["embed"]
        assert "Multiple Contacts Found" in embed.title
        # Should only show 2 unique contacts, not 3
        assert len(embed.fields) == 3  # 2 contacts + tip field

    @pytest.mark.asyncio
    async def test_unlinked_discord_users_with_unlinked_users(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test unlinked Discord users command with some unlinked users."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock guild with members
        mock_guild = Mock()
        mock_interaction.guild = mock_guild

        # Create mock members - some linked, some not
        mock_member1 = Mock()
        mock_member1.id = 111111111
        mock_member1.display_name = "Alice"
        mock_member1.mention = "<@111111111>"
        mock_member1.bot = False
        mock_member1.roles = [Mock()]
        mock_member1.roles[0].name = "Member"

        mock_member2 = Mock()
        mock_member2.id = 222222222
        mock_member2.display_name = "Bob"
        mock_member2.mention = "<@222222222>"
        mock_member2.bot = False
        mock_member2.roles = [Mock()]
        mock_member2.roles[0].name = "Admin"

        mock_member3 = Mock()  # This one is linked
        mock_member3.id = 333333333
        mock_member3.display_name = "Charlie"
        mock_member3.bot = False
        mock_member3.roles = [Mock()]
        mock_member3.roles[0].name = "Member"

        mock_guild.members = [mock_member1, mock_member2, mock_member3]

        # Mock CRM response - Charlie is linked, others are not
        crm_response = {
            "list": [
                {"cDiscordUserID": "333333333"}  # Charlie is linked
            ]
        }
        crm_cog.espo_api.request.return_value = crm_response

        # Call the command
        await crm_cog.unlinked_discord_users.callback(crm_cog, mock_interaction)

        # Verify API call
        crm_cog.espo_api.request.assert_called_once()
        call_args = crm_cog.espo_api.request.call_args
        assert call_args[0][0] == "GET"
        assert call_args[0][1] == "Contact"
        search_params = call_args[0][2]
        assert search_params["where"][0]["type"] == "isNotNull"
        assert search_params["where"][0]["attribute"] == "cDiscordUserID"

        # Verify response contains unlinked users (Alice and Bob)
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message_text = call_args[0][0]
        assert "Unlinked Discord Users (2)" in message_text
        assert "<@111111111>" in message_text  # Alice's mention
        assert "<@222222222>" in message_text  # Bob's mention

    @pytest.mark.asyncio
    async def test_unlinked_discord_users_all_linked(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test unlinked Discord users command when all users are linked."""
        mock_interaction.user.roles = [mock_admin_role]

        # Mock guild with members
        mock_guild = Mock()
        mock_interaction.guild = mock_guild

        # Create mock member
        mock_member = Mock()
        mock_member.id = 111111111
        mock_member.bot = False
        mock_member.roles = [Mock()]
        mock_member.roles[0].name = "Member"

        mock_guild.members = [mock_member]

        # Mock CRM response - member is linked
        crm_response = {
            "list": [
                {"cDiscordUserID": "111111111"}  # Member is linked
            ]
        }
        crm_cog.espo_api.request.return_value = crm_response

        # Call the command
        await crm_cog.unlinked_discord_users.callback(crm_cog, mock_interaction)

        # Verify response shows all linked
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message_text = call_args[0][0]
        assert "All Members Linked" in message_text

    @pytest.mark.asyncio
    async def test_unlinked_discord_users_no_guild(
        self, crm_cog, mock_interaction, mock_admin_role
    ):
        """Test unlinked Discord users command when not in a guild."""
        mock_interaction.user.roles = [mock_admin_role]
        mock_interaction.guild = None

        # Call the command
        await crm_cog.unlinked_discord_users.callback(crm_cog, mock_interaction)

        # Verify error response
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "❌ This command can only be used in a server." in call_args[0][0]

    def test_build_contact_search_filters_username(self, crm_cog):
        """Build shared search filters for a plain 508 username."""
        filters = crm_cog._build_contact_search_filters("john")

        assert {"type": "contains", "attribute": "name", "value": "john"} in filters
        assert {
            "type": "equals",
            "attribute": "c508Email",
            "value": "john@508.dev",
        } in filters

    def test_build_contact_search_filters_trailing_at(self, crm_cog):
        """Build shared search filters for trailing 508 usernames."""
        filters = crm_cog._build_contact_search_filters("john@")

        assert {
            "type": "equals",
            "attribute": "emailAddress",
            "value": "john@508.dev",
        } in filters
        assert {
            "type": "equals",
            "attribute": "c508Email",
            "value": "john@508.dev",
        } in filters

    def test_build_contact_search_filters_email(self, crm_cog):
        """Build shared search filters for explicit email addresses."""
        filters = crm_cog._build_contact_search_filters("john@example.com")

        assert {
            "type": "equals",
            "attribute": "emailAddress",
            "value": "john@example.com",
        } in filters
        assert {
            "type": "equals",
            "attribute": "c508Email",
            "value": "john@example.com",
        } in filters

    def test_build_contact_search_filters_discord_mention(self, crm_cog):
        """Build shared search filters for Discord mentions."""
        filters = crm_cog._build_contact_search_filters("<@111111111>")

        assert filters == [
            {
                "type": "equals",
                "attribute": "cDiscordUserID",
                "value": "111111111",
            }
        ]

    @pytest.mark.asyncio
    async def test_search_contact_for_linking_includes_discord_username_filter_when_requested(
        self, crm_cog
    ):
        """Search helper includes Discord username criteria when requested."""
        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog._search_contact_for_linking(
            "john", include_discord_username_search=True, max_size=10
        )

        call = crm_cog.espo_api.request.call_args.args
        where_filters = call[2]["where"][0]["value"]
        assert {
            "type": "contains",
            "attribute": "cDiscordUsername",
            "value": "john",
        } in where_filters

    @pytest.mark.asyncio
    async def test_search_contacts_by_field_includes_requested_field_and_excludes_default(
        self, crm_cog
    ):
        """Search-by-field includes the requested field and excludes the default."""
        crm_cog.espo_api.request.return_value = {"list": []}

        with patch.object(
            crm_cog, "_configured_linkedin_field", return_value="cLinkedIn"
        ) as configured_field:
            configured_linkedin_field = crm_cog._configured_linkedin_field()
            await crm_cog._search_contacts_by_field(
                field=configured_linkedin_field, value="https://linkedin.com/in/test"
            )

        call = crm_cog.espo_api.request.call_args
        assert call.args[0] == "GET"
        assert call.args[1] == "Contact"
        params = call.args[2]
        configured_field.assert_called_once()
        assert params["where"][0]["attribute"] == configured_linkedin_field
        assert params["where"][0]["value"] == "https://linkedin.com/in/test"
        assert params["where"][0]["type"] == "equals"
        select_fields = params["select"].split(",")
        assert configured_linkedin_field in select_fields
        assert "cLinkedInUrl" not in select_fields

    @pytest.mark.asyncio
    async def test_crm_status_success(self, crm_cog, mock_interaction):
        """Test successful CRM status check."""
        crm_cog.espo_api.request.return_value = {"user": {"name": "Test User"}}

        await crm_cog.crm_status.callback(crm_cog, mock_interaction)

        crm_cog.espo_api.request.assert_called_once_with("GET", "App/user")
        mock_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_crm_status_api_error(self, crm_cog, mock_interaction):
        """Test CRM status check with API error."""
        crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")

        await crm_cog.crm_status.callback(crm_cog, mock_interaction)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        # Check that embed is sent
        assert "embed" in call_args[1]

    async def test_find_contact_by_discord_id_success(self, crm_cog, mock_interaction):
        """Test finding contact by Discord ID successfully."""
        contact_data = {
            "id": "contact123",
            "name": "John Doe",
            "emailAddress": "john@example.com",
            "c508Email": "john@508.dev",
            "cDiscordUsername": "johndoe#1234",
            "cGitHubUsername": "johngithub",
        }

        crm_cog.espo_api.request.return_value = {"list": [contact_data]}

        result = await crm_cog._find_contact_by_discord_id("123456789")

        # Verify API call
        crm_cog.espo_api.request.assert_called_once_with(
            "GET",
            "Contact",
            {
                "where": [
                    {
                        "type": "equals",
                        "attribute": "cDiscordUserID",
                        "value": "123456789",
                    }
                ],
                "maxSize": 1,
                "select": "id,name,emailAddress,c508Email,cDiscordUsername,cGitHubUsername",
            },
        )

        assert result == contact_data

    async def test_find_contact_by_discord_id_not_found(
        self, crm_cog, mock_interaction
    ):
        """Test finding contact by Discord ID when not found."""
        crm_cog.espo_api.request.return_value = {"list": []}

        result = await crm_cog._find_contact_by_discord_id("123456789")

        assert result is None

    def test_parse_skill_updates_parses_levels(self, crm_cog):
        """Parse valid skill entries and reject invalid formats."""
        parsed_skills, requested_strengths, invalid_entries = (
            crm_cog._parse_skill_updates("python:4, aws, go:2, bad:, :3, rust:6")
        )

        assert parsed_skills == ["python", "amazon web services", "go"]
        assert requested_strengths == {"python": 4, "go": 2}
        assert invalid_entries == ["bad:", ":3", "rust:6"]

    def test_merge_skill_update_payload_merges_existing_and_defaults(self, crm_cog):
        """Merge requested skills while preserving existing values and defaults."""
        contact = {
            "skills": ["python", "AWS"],
            "cSkillAttrs": '{"python": {"strength": 2}}',
        }

        parsed_skills = ["python", "go"]
        requested_strengths = {"python": 5}

        merged_skills, merged_attrs = crm_cog._merge_skill_update_payload(
            contact, parsed_skills, requested_strengths
        )

        assert merged_skills == "python, amazon web services, go"
        assert '"python":{"strength":5}' in merged_attrs
        assert '"amazon web services":{"strength":3}' in merged_attrs
        assert '"go":{"strength":3}' in merged_attrs

    @pytest.mark.asyncio
    async def test_update_contact_success_self_updates_multiple_fields(
        self, crm_cog, mock_interaction
    ):
        """Test successful contact update for self with multiple fields."""
        mock_interaction.user.id = 123456789
        target_contact = {
            "id": "contact123",
            "name": "Test User",
            "skills": "python",
        }

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value=target_contact),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact123"}

            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                github="myusername",
                linkedin="https://linkedin.com/in/test",
                skills="python:4, aws",
                rate_range="120k-150k",
            )

        crm_cog.espo_api.request.assert_called_once()
        update_call = crm_cog.espo_api.request.call_args
        assert update_call[0][0] == "PUT"
        assert update_call[0][1] == "Contact/contact123"
        update_payload = update_call[0][2]
        assert update_payload["cGitHubUsername"] == "myusername"
        assert update_payload["cLinkedIn"] == "https://linkedin.com/in/test"
        assert update_payload["rateRange"] == "120k-150k"
        assert update_payload["skills"] == "python, amazon web services"
        assert '"python":{"strength":4}' in update_payload["cSkillAttrs"]
        assert '"amazon web services":{"strength":3}' in update_payload["cSkillAttrs"]
        assert "embed" in mock_interaction.followup.send.call_args[1]
        assert (
            mock_interaction.followup.send.call_args[1]["embed"].title
            == "✅ Contact Updated"
        )

    @pytest.mark.asyncio
    async def test_update_contact_success_updates_location_hours_website(
        self, crm_cog, mock_interaction
    ):
        """Update location, desired hours, and website links for self."""
        mock_interaction.user.id = 123456789
        target_contact = {
            "id": "contact123",
            "name": "Test User",
        }

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value=target_contact),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact123"}

            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                location="Austin, TX, USA, UTC-06:00",
                desired_hours="25",
                website="example.com, https://github.com/test",
            )

        update_call = crm_cog.espo_api.request.call_args
        update_payload = update_call[0][2]
        assert update_payload["addressCity"] == "Austin"
        assert update_payload["addressState"] == "Texas"
        assert update_payload["addressCountry"] == "United States"
        assert update_payload["cTimezone"] == "UTC-06:00"
        assert update_payload["cDesiredHours"] == "25"
        assert isinstance(update_payload["cWebsiteLink"], list)
        assert any("example.com" in link for link in update_payload["cWebsiteLink"])
        assert any("github.com/test" in link for link in update_payload["cWebsiteLink"])

    @pytest.mark.asyncio
    async def test_update_contact_parses_state_country_location(
        self, crm_cog, mock_interaction
    ):
        """Parse State, Country without forcing a city."""
        mock_interaction.user.id = 123456789
        target_contact = {
            "id": "contact123",
            "name": "Test User",
        }

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value=target_contact),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact123"}

            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                location="California, United States",
            )

        update_call = crm_cog.espo_api.request.call_args
        update_payload = update_call[0][2]
        assert update_payload["addressState"] == "California"
        assert update_payload["addressCountry"] == "United States"
        assert "addressCity" not in update_payload

    @pytest.mark.asyncio
    async def test_update_contact_parses_city_region_country_location(
        self, crm_cog, mock_interaction
    ):
        """Parse city + region + country for non-US locations."""
        mock_interaction.user.id = 123456789
        target_contact = {
            "id": "contact123",
            "name": "Test User",
        }

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value=target_contact),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact123"}

            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                location="Nanzih, Kaohsiung City, Taiwan",
            )

        update_call = crm_cog.espo_api.request.call_args
        update_payload = update_call[0][2]
        assert update_payload["addressCity"] == "Nanzih"
        assert update_payload["addressState"] == "Kaohsiung City"
        assert update_payload["addressCountry"] == "Taiwan"

    @pytest.mark.asyncio
    async def test_update_contact_rejects_invalid_desired_hours(
        self, crm_cog, mock_interaction
    ):
        """Reject desired hours outside the allowed range."""
        mock_interaction.user.id = 123456789

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
        ):
            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                desired_hours="70",
            )

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Invalid desired_hours" in message

    @pytest.mark.asyncio
    async def test_update_contact_permission_denied_for_other_without_steering(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Reject updating other contact without Steering Committee permissions."""
        mock_interaction.user.roles = [mock_member_role]

        await crm_cog.update_contact.callback(
            crm_cog, mock_interaction, github="someusername", search_term="john@508.dev"
        )

        crm_cog.espo_api.request.assert_not_called()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "Steering Committee role or higher" in message
        assert "❌" in message

    @pytest.mark.asyncio
    async def test_update_contact_success_other_with_permission(
        self, crm_cog, mock_interaction
    ):
        """Update another contact when Steering Committee permissions are present."""
        # Give user Steering Committee role
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(return_value=[{"id": "contact456", "name": "John Doe"}]),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact456"}

            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, github="johngithub", search_term="john"
            )

        update_call = crm_cog.espo_api.request.call_args
        assert update_call[0][0] == "PUT"
        assert update_call[0][1] == "Contact/contact456"
        assert update_call[0][2]["cGitHubUsername"] == "johngithub"

        mock_interaction.followup.send.assert_called_once()
        assert (
            mock_interaction.followup.send.call_args[1]["embed"].title
            == "✅ Contact Updated"
        )

    @pytest.mark.asyncio
    async def test_update_contact_multiple_contacts_found(
        self, crm_cog, mock_interaction
    ):
        """Multiple target matches should require a more specific search term."""
        # Give user Steering Committee role
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        # Mock search helper to return multiple contacts
        with patch.object(
            crm_cog,
            "_search_contact_for_linking",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "contact1",
                        "name": "John Doe",
                        "c508Email": "john1@508.dev",
                    },
                    {
                        "id": "contact2",
                        "name": "John Smith",
                        "c508Email": "john2@508.dev",
                    },
                ]
            ),
        ):
            # Call the function with search_term
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, github="johngithub", search_term="john"
            )

            # Verify no update API call was made
            crm_cog.espo_api.request.assert_not_called()

            # Verify error message about multiple contacts
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            message = call_args[0][0]
            assert "❌ Multiple contacts found for `john`." in message

    @pytest.mark.asyncio
    async def test_update_contact_rejects_invalid_skill_format(
        self, crm_cog, mock_interaction
    ):
        """Reject malformed skill input before making CRM calls."""
        mock_interaction.user.id = 123456789

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
        ):
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, skills="python:11"
            )

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Invalid skill entries" in message

    @pytest.mark.asyncio
    async def test_update_contact_requires_updates(self, crm_cog, mock_interaction):
        """Reject command if no updatable arguments are supplied."""
        await crm_cog.update_contact.callback(crm_cog, mock_interaction)

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Provide at least one of" in message

    @pytest.mark.asyncio
    async def test_update_contact_uses_configured_linkedin_field(
        self, crm_cog, mock_interaction
    ):
        """Configured LinkedIn custom field should flow through update payload and embed."""
        mock_interaction.user.id = 123456789

        with (
            patch.object(
                crm_cog, "_configured_linkedin_field", return_value="cLinkedIn"
            ),
            patch.object(
                crm_cog,
                "_find_contact_by_discord_id",
                new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
            ),
        ):
            crm_cog.espo_api.request.return_value = {"id": "contact123"}

            await crm_cog.update_contact.callback(
                crm_cog,
                mock_interaction,
                linkedin="https://www.linkedin.com/in/test-user/",
            )

        assert mock_interaction.followup.send.call_count == 1
        call = crm_cog.espo_api.request.call_args
        assert call.args[0] == "PUT"
        assert call.args[1] == "Contact/contact123"
        assert call.args[2] == {"cLinkedIn": "https://www.linkedin.com/in/test-user/"}

        send_kwargs = mock_interaction.followup.send.call_args.kwargs
        linkedin_value = next(
            field.value
            for field in send_kwargs["embed"].fields
            if field.name == "🔗 LinkedIn"
        )
        assert linkedin_value == "https://www.linkedin.com/in/test-user/"

    @pytest.mark.asyncio
    async def test_update_contact_self_not_linked(self, crm_cog, mock_interaction):
        """Self update without a linked CRM contact should return a helpful error."""
        mock_interaction.user.id = 123456789

        with patch.object(
            crm_cog, "_find_contact_by_discord_id", new=AsyncMock(return_value=None)
        ):
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, github="myusername"
            )

        crm_cog.espo_api.request.assert_not_called()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "Discord account is not linked to a CRM contact" in message

    @pytest.mark.asyncio
    async def test_update_contact_upload_resume_only(self, crm_cog, mock_interaction):
        """Resume upload should trigger the attachment workflow."""
        mock_interaction.user.id = 123456789
        resume_file = Mock()
        resume_file.filename = "resume.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"pdf-bytes")

        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch.object(
                crm_cog,
                "_find_contact_by_discord_id",
                new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
            ),
            patch.object(
                crm_cog, "_upload_resume_attachment_to_contact", new=AsyncMock()
            ) as mock_upload,
        ):
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, resume=resume_file
            )

        mock_upload.assert_awaited_once()
        kwargs = mock_upload.await_args.kwargs
        assert kwargs["contact"]["id"] == "contact123"
        assert kwargs["target_scope"] == "self"

    @pytest.mark.asyncio
    async def test_update_contact_unexpected_exception(self, crm_cog, mock_interaction):
        """Unexpected exceptions should return a useful message."""
        mock_interaction.user.id = 123456789

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
        ):
            crm_cog.espo_api.request.side_effect = ValueError(
                "Unexpected error occurred"
            )
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, github="myusername"
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "❌ An unexpected error occurred while updating the contact." in message

    @pytest.mark.asyncio
    async def test_update_contact_api_error(self, crm_cog, mock_interaction):
        """CRM errors should surface and stop with an error response."""
        mock_interaction.user.id = 123456789

        with patch.object(
            crm_cog,
            "_find_contact_by_discord_id",
            new=AsyncMock(return_value={"id": "contact123", "name": "Test User"}),
        ):
            crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")
            await crm_cog.update_contact.callback(
                crm_cog, mock_interaction, github="myusername"
            )

        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌ CRM API error:" in message
        assert "Connection failed" in message

    async def test_update_contact_resume_new_resume(self, crm_cog, mock_interaction):
        """Test updating contact resume with new attachment."""
        # Mock current contact data
        contact_data = {"resumeIds": ["existing_resume_id"]}

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            {"id": "contact123"},  # PUT update
        ]

        result = await crm_cog._update_contact_resume("contact123", "new_attachment_id")

        assert result is True
        assert crm_cog.espo_api.request.call_count == 2

        # Check GET call
        get_call = crm_cog.espo_api.request.call_args_list[0]
        assert get_call[0][0] == "GET"
        assert get_call[0][1] == "Contact/contact123"

        # Check PUT call
        put_call = crm_cog.espo_api.request.call_args_list[1]
        assert put_call[0][0] == "PUT"
        assert put_call[0][1] == "Contact/contact123"
        assert put_call[0][2]["resumeIds"] == [
            "existing_resume_id",
            "new_attachment_id",
        ]

    async def test_update_contact_resume_duplicate_resume(
        self, crm_cog, mock_interaction
    ):
        """Test updating contact resume with duplicate attachment ID."""
        # Mock current contact data with existing resume
        contact_data = {"resumeIds": ["attachment_id"]}

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            {"id": "contact123"},  # PUT update
        ]

        result = await crm_cog._update_contact_resume(
            "contact123", "attachment_id", False
        )

        assert result is True

        # Check PUT call - should not add duplicate
        put_call = crm_cog.espo_api.request.call_args_list[1]
        assert put_call[0][2]["resumeIds"] == ["attachment_id"]

    async def test_update_contact_resume_no_existing_resumes(
        self, crm_cog, mock_interaction
    ):
        """Test updating contact resume when no existing resumes."""
        # Mock current contact data with no resumes
        contact_data = {"resumeIds": []}

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            {"id": "contact123"},  # PUT update
        ]

        result = await crm_cog._update_contact_resume("contact123", "new_attachment_id")

        assert result is True

        # Check PUT call
        put_call = crm_cog.espo_api.request.call_args_list[1]
        assert put_call[0][2]["resumeIds"] == ["new_attachment_id"]

    @pytest.mark.asyncio
    async def test_update_contact_resume_overwrite_mode(
        self, crm_cog, mock_interaction
    ):
        """Test updating contact resume with overwrite mode enabled."""
        # Mock current contact data with existing resumes
        contact_data = {"resumeIds": ["existing_resume_1", "existing_resume_2"]}

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            {"id": "contact123"},  # PUT update
        ]

        result = await crm_cog._update_contact_resume(
            "contact123", "new_attachment_id", True
        )

        assert result is True

        # Check PUT call - should replace all existing resumes
        put_call = crm_cog.espo_api.request.call_args_list[1]
        assert put_call[0][2]["resumeIds"] == ["new_attachment_id"]

    async def test_update_contact_resume_api_error(self, crm_cog, mock_interaction):
        """Test updating contact resume with API error."""
        crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")

        result = await crm_cog._update_contact_resume(
            "contact123", "attachment_id", False
        )

        assert result is False

    async def test_check_existing_resume_duplicate_found(
        self, crm_cog, mock_interaction
    ):
        """Test checking existing resume when duplicate is found."""
        contact_data = {"resumeIds": ["resume_id_1", "resume_id_2"]}
        attachment_data = {
            "id": "resume_id_1",
            "name": "test_resume.pdf",
            "size": 12345,
        }

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            attachment_data,  # GET first attachment
        ]

        has_duplicate, resume_id = await crm_cog._check_existing_resume(
            "contact123", "test_resume.pdf", 12345
        )

        assert has_duplicate is True
        assert resume_id == "resume_id_1"

        # Verify API calls
        assert crm_cog.espo_api.request.call_count == 2
        get_contact_call = crm_cog.espo_api.request.call_args_list[0]
        assert get_contact_call[0][0] == "GET"
        assert get_contact_call[0][1] == "Contact/contact123"

        get_attachment_call = crm_cog.espo_api.request.call_args_list[1]
        assert get_attachment_call[0][0] == "GET"
        assert get_attachment_call[0][1] == "Attachment/resume_id_1"

    async def test_check_existing_resume_no_duplicate(self, crm_cog, mock_interaction):
        """Test checking existing resume when no duplicate is found."""
        contact_data = {"resumeIds": ["resume_id_1"]}
        attachment_data = {
            "id": "resume_id_1",
            "name": "different_resume.pdf",
            "size": 54321,
        }

        crm_cog.espo_api.request.side_effect = [
            contact_data,  # GET contact
            attachment_data,  # GET attachment
        ]

        has_duplicate, resume_id = await crm_cog._check_existing_resume(
            "contact123", "test_resume.pdf", 12345
        )

        assert has_duplicate is False
        assert resume_id is None

    async def test_check_existing_resume_no_existing_resumes(
        self, crm_cog, mock_interaction
    ):
        """Test checking existing resume when contact has no resumes."""
        contact_data = {"resumeIds": []}

        crm_cog.espo_api.request.return_value = contact_data

        has_duplicate, resume_id = await crm_cog._check_existing_resume(
            "contact123", "test_resume.pdf", 12345
        )

        assert has_duplicate is False
        assert resume_id is None

        # Should only call GET contact, not any attachments
        assert crm_cog.espo_api.request.call_count == 1

    async def test_check_existing_resume_api_error(self, crm_cog, mock_interaction):
        """Test checking existing resume with API error."""
        crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")

        has_duplicate, resume_id = await crm_cog._check_existing_resume(
            "contact123", "test_resume.pdf", 12345
        )

        assert has_duplicate is False
        assert resume_id is None

    def test_build_resume_create_contact_payload_sets_email_field_by_domain(
        self, crm_cog
    ):
        """Test that resume payload writes either emailAddress or c508Email."""
        with (
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "emails": ["person@example.com"],
                    "github_usernames": [],
                    "linkedin_urls": [],
                },
            ),
            patch.object(
                crm_cog, "_extract_resume_name_hint", return_value="Person Example"
            ),
        ):
            payload = crm_cog._build_resume_create_contact_payload(b"resume")
            assert payload["type"] == "Prospect"
            assert payload["name"] == "Person Example"
            assert payload["emailAddress"] == "person@example.com"
            assert payload["firstName"] == "Person"
            assert payload["lastName"] == "Example"
            assert "c508Email" not in payload

        with (
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "emails": ["person@508.dev"],
                    "github_usernames": [],
                    "linkedin_urls": [],
                },
            ),
            patch.object(
                crm_cog, "_extract_resume_name_hint", return_value="Person 508"
            ),
        ):
            payload = crm_cog._build_resume_create_contact_payload(b"resume")
            assert payload["type"] == "Prospect"
            assert payload["name"] == "Person 508"
            assert payload["c508Email"] == "person@508.dev"
            assert payload["firstName"] == "Person"
            assert payload["lastName"] == "Unknown"
            assert "emailAddress" not in payload

    def test_build_resume_create_contact_payload_populates_prospect_details(
        self, crm_cog
    ):
        """Test creating prospect payload includes richer parsed fields."""
        with (
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "emails": ["jane@example.com"],
                    "github_usernames": ["janedoe"],
                    "linkedin_urls": ["https://linkedin.com/in/janedoe"],
                    "phone": "+1 555-0100",
                    "address_country": "Canada",
                    "seniority_level": "senior",
                    "skills": ["Python", " fastapi ", ""],
                },
            ),
            patch.object(crm_cog, "_extract_resume_name_hint", return_value="Jane Doe"),
        ):
            payload = crm_cog._build_resume_create_contact_payload(b"resume")
            assert payload["type"] == "Prospect"
            assert payload["name"] == "Jane Doe"
            assert payload["emailAddress"] == "jane@example.com"
            assert payload["cGitHubUsername"] == "janedoe"
            assert payload["cLinkedIn"] == "https://linkedin.com/in/janedoe"
            assert payload["phoneNumber"] == "+1 555-0100"
            assert payload["addressCountry"] == "Canada"
            assert payload["cSeniority"] == "senior"
            assert payload["skills"] == "Python, fastapi"
            assert payload["firstName"] == "Jane"
            assert payload["lastName"] == "Doe"

    def test_build_resume_create_contact_payload_single_name_uses_unknown_last(
        self, crm_cog
    ):
        """Single token names should include a placeholder lastName."""
        with (
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "emails": ["single@example.com"],
                    "github_usernames": [],
                    "linkedin_urls": [],
                },
            ),
            patch.object(crm_cog, "_extract_resume_name_hint", return_value="Cher"),
        ):
            payload = crm_cog._build_resume_create_contact_payload(b"resume")

        assert payload["type"] == "Prospect"
        assert payload["name"] == "Cher"
        assert payload["firstName"] == "Cher"
        assert payload["lastName"] == "Unknown"
        assert payload["emailAddress"] == "single@example.com"

    def test_build_contact_payload_for_link_user_overwrites_stale_name_fields(
        self, crm_cog
    ):
        """Link-user fallback should regenerate first/last names from Discord display name."""
        user = Mock()
        user.display_name = "Monica Geller"
        user.name = "monica"
        user.id = 999

        with (
            patch.object(
                crm_cog,
                "_build_resume_create_contact_payload",
                return_value={
                    "name": "Resume Candidate",
                    "firstName": "Candidate",
                    "lastName": "User",
                },
            ),
            patch.object(
                crm_cog,
                "_fallback_contact_name_for_discord_user",
                return_value="Monica Geller",
            ),
        ):
            payload = crm_cog._build_contact_payload_for_link_user(
                user=user,
                file_content=b"resume",
            )

        assert payload["name"] == "Monica Geller"
        assert payload["firstName"] == "Monica"
        assert payload["lastName"] == "Geller"
        assert payload["cDiscordUsername"] == "monica"
        assert payload["cDiscordUserID"] == "999"

    def test_is_valid_resume_name_candidate_rejects_heading_spacing_variants(
        self, crm_cog
    ):
        """Heading-like names with spacing variants should be rejected."""
        assert crm_cog._is_valid_resume_name_candidate("Curriculum  Vitae") is False
        assert crm_cog._is_valid_resume_name_candidate("Resume :") is False
        assert crm_cog._is_valid_resume_name_candidate("Jane Doe") is True

    def test_build_inference_lookup_summary_uses_attempt_text(self, crm_cog):
        """Test lookup summary uses attempt text when attempts are present."""
        with (
            patch.object(
                crm_cog,
                "_format_inferred_attempts",
                return_value="`jane@example.com`, `janedoe`",
            ),
            patch.object(crm_cog, "_extract_resume_contact_hints") as extract_hints,
        ):
            summary = crm_cog._build_inference_lookup_summary(
                file_content=b"resume",
                attempts=[
                    {"method": "email", "value": "jane@example.com"},
                    {"method": "github", "value": "janedoe"},
                ],
            )

            assert summary == "\nTried contact lookups: `jane@example.com`, `janedoe`"
            extract_hints.assert_not_called()

    def test_build_inference_lookup_summary_falls_back_to_parsed_identifiers(
        self, crm_cog
    ):
        """Test lookup summary fallback uses parsed identifiers with cleanup."""
        with (
            patch.object(crm_cog, "_format_inferred_attempts", return_value=""),
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "emails": [
                        " jane@example.com ",
                        "jane@example.com",
                        "",
                        " second@example.com ",
                        "second@example.com",
                    ],
                    "github_usernames": [" janedoe ", "janedoe", " "],
                    "linkedin_urls": [
                        "https://linkedin.com/in/jane",
                        "https://linkedin.com/in/jane",
                    ],
                },
            ),
        ):
            summary = crm_cog._build_inference_lookup_summary(
                file_content=b"resume", attempts=[]
            )

            assert (
                summary
                == "\nParsed resume identifiers: "
                + "emails: `jane@example.com`, `second@example.com`; "
                + "github usernames: `janedoe`; linkedin URLs: `https://linkedin.com/in/jane`"
            )

    def test_build_inference_lookup_summary_with_non_dict_hints(self, crm_cog):
        """Test non-dict parsed contact hints produce empty summary safely."""
        with (
            patch.object(crm_cog, "_format_inferred_attempts", return_value=""),
            patch.object(crm_cog, "_extract_resume_contact_hints", return_value=None),
        ):
            summary = crm_cog._build_inference_lookup_summary(
                file_content=b"resume", attempts=[]
            )

            assert summary == ""

    def test_build_resume_parsed_identity_summary_includes_name_and_email(
        self, crm_cog
    ):
        """Parsed name and email are included in resume identity summary."""
        with patch.object(
            crm_cog,
            "_extract_resume_contact_hints",
            return_value={
                "name": "Jane Doe",
                "emails": ["jane@example.com", "ignored@alt.example"],
            },
        ):
            summary = crm_cog._build_resume_parsed_identity_summary(
                file_content=b"resume"
            )

            assert (
                summary
                == "\nParsed contact details: name=`Jane Doe`, email=`jane@example.com`"
            )

    def test_build_resume_parsed_identity_summary_ignores_heading_name(self, crm_cog):
        """Heading-like parsed names should fall back to heuristic name extraction."""
        with (
            patch.object(
                crm_cog,
                "_extract_resume_contact_hints",
                return_value={
                    "name": "Resume:",
                    "emails": ["jane@example.com"],
                },
            ),
            patch.object(
                crm_cog,
                "_extract_resume_name_fallback",
                return_value="Jane Doe",
            ) as fallback_name,
        ):
            summary = crm_cog._build_resume_parsed_identity_summary(
                file_content=b"resume",
                filename="candidate.pdf",
            )

        fallback_name.assert_called_once_with(b"resume", filename="candidate.pdf")
        assert (
            summary
            == "\nParsed contact details: name=`Jane Doe`, email=`jane@example.com`"
        )

    def test_extract_resume_profile_uses_filename_aware_text_extraction(self, crm_cog):
        """Profile extraction should use filename-aware document text extraction."""
        profile = Mock()
        with (
            patch.object(
                crm_cog,
                "_extract_resume_text",
                return_value="Jane Doe\njane@example.com",
            ) as extract_text,
            patch.object(
                crm_cog.resume_extractor, "extract", return_value=profile
            ) as extract_profile,
        ):
            result = crm_cog._extract_resume_profile(
                b"%PDF-binary",
                filename="candidate.pdf",
            )

        assert result is profile
        extract_text.assert_called_once_with(
            b"%PDF-binary",
            filename="candidate.pdf",
        )
        extract_profile.assert_called_once_with("Jane Doe\njane@example.com")

    @pytest.mark.asyncio
    async def test_upload_resume_link_user_shows_confirm_then_creates_contact(
        self, crm_cog, mock_interaction
    ):
        """Test /upload-resume prompts before creating contact for unlinked link_user."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Requester"
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        link_user = Mock()
        link_user.id = 202
        link_user.name = "candidateuser"
        link_user.display_name = "Candidate User"
        link_user.discriminator = "0"

        created_contact = {"id": "contact123", "name": "Candidate User"}
        crm_cog._audit_command = Mock()

        with (
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog, "_find_contact_by_discord_id", new=AsyncMock(return_value=None)
            ),
            patch.object(
                crm_cog, "_upload_resume_attachment_to_contact", new=AsyncMock()
            ) as mock_upload,
            patch.object(
                crm_cog,
                "_build_resume_create_contact_payload",
                return_value={"name": "Resume Candidate"},
            ),
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            crm_cog.espo_api.request.return_value = created_contact

            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                link_user,
            )

            crm_cog.espo_api.request.assert_not_called()
            mock_upload.assert_not_awaited()
            mock_interaction.followup.send.assert_called_once()
            followup_kwargs = mock_interaction.followup.send.call_args.kwargs
            assert "view" in followup_kwargs
            view = followup_kwargs["view"]
            assert isinstance(view, ResumeCreateContactView)

            confirm_interaction = AsyncMock()
            confirm_interaction.user = Mock()
            confirm_interaction.user.id = 101
            confirm_interaction.user.name = "Requester"
            confirm_interaction.response = AsyncMock()
            confirm_interaction.response.defer = AsyncMock()
            confirm_interaction.followup = AsyncMock()
            confirm_interaction.followup.send = AsyncMock()
            confirm_interaction.message = None

            create_button = next(
                child
                for child in view.children
                if isinstance(child, discord.ui.Button)
                and child.label == "Create Contact"
            )
            await create_button.callback(confirm_interaction)

            crm_cog.espo_api.request.assert_called_once_with(
                "POST",
                "Contact",
                {
                    "name": "Candidate User",
                    "firstName": "Candidate",
                    "lastName": "User",
                    "cDiscordUsername": "candidateuser",
                    "cDiscordUserID": "202",
                },
            )
            mock_upload.assert_awaited_once()
            assert (
                mock_upload.await_args.kwargs.get("target_scope") == "other_autocreated"
            )
            assert mock_upload.await_args.kwargs.get("contact") == created_contact
            crm_cog._audit_command.assert_called_once()
            audit_kwargs = crm_cog._audit_command.call_args.kwargs
            assert audit_kwargs["action"] == "crm.upload_resume"
            assert audit_kwargs["result"] == "error"
            assert audit_kwargs["metadata"]["reason"] == "discord_not_linked"
            assert audit_kwargs["metadata"]["target_scope"] == "other"

    @pytest.mark.asyncio
    async def test_upload_resume_search_term_not_found_records_error(
        self, crm_cog, mock_interaction
    ):
        """Search-based resume upload should log an error when no CRM contact matches."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Requester"
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        crm_cog._audit_command = Mock()

        with (
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contact_for_linking",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(
                crm_cog, "_upload_resume_attachment_to_contact", new=AsyncMock()
            ) as mock_upload,
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                "missing-contact",
                False,
                None,
            )

        mock_upload.assert_not_awaited()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "❌ No contact found for: `missing-contact`" in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"]["search_term"] == "missing-contact"
        assert audit_kwargs["metadata"]["contact_found"] is False

    @pytest.mark.asyncio
    async def test_upload_resume_self_not_linked_records_error(
        self, crm_cog, mock_interaction
    ):
        """Uploading own resume should log an error when Discord user is not linked."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Member"
        mock_interaction.user.roles = []

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        crm_cog._audit_command = Mock()

        with (
            patch.object(
                crm_cog,
                "_find_contact_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                None,
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "Your Discord account is not linked to a CRM contact." in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"]["target_scope"] == "self"

    @pytest.mark.asyncio
    async def test_upload_resume_invalid_file_type_records_error(
        self, crm_cog, mock_interaction
    ):
        """Uploading non-PDF/DOC/DOCX/TXT files should be recorded as an error."""
        mock_interaction.user.id = 101

        resume_file = Mock()
        resume_file.filename = "image.png"
        resume_file.size = 1024

        crm_cog._audit_command = Mock()

        with patch(
            "five08.discord_bot.cogs.crm.settings.api_shared_secret",
            "test-shared-secret",
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                None,
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert (
            "Invalid file type. Please upload a PDF, DOC, DOCX, or TXT file." in message
        )
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "filename": "image.png",
            "reason": "invalid_file_type",
        }

    @pytest.mark.asyncio
    async def test_upload_resume_file_too_large_records_error(
        self, crm_cog, mock_interaction
    ):
        """Uploading a resume above 10MB should be recorded as an error."""
        mock_interaction.user.id = 101

        resume_file = Mock()
        resume_file.filename = "resume.pdf"
        resume_file.size = 10 * 1024 * 1024 + 1

        crm_cog._audit_command = Mock()

        with patch(
            "five08.discord_bot.cogs.crm.settings.api_shared_secret",
            "test-shared-secret",
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                None,
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "File too large. Maximum size is 10MB." in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "filename": "resume.pdf",
            "size_bytes": 10485761,
            "reason": "file_too_large",
        }

    @pytest.mark.asyncio
    async def test_upload_resume_search_term_multiple_matches_records_error(
        self, crm_cog, mock_interaction
    ):
        """Search-based uploads should require a unique match."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Requester"
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        crm_cog._audit_command = Mock()

        with (
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contact_for_linking",
                new=AsyncMock(
                    return_value=[{"id": "contact123"}, {"id": "contact456"}]
                ),
            ),
            patch.object(
                crm_cog,
                "_upload_resume_attachment_to_contact",
                new=AsyncMock(),
            ) as mock_upload,
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                "john",
                False,
                None,
            )

        mock_upload.assert_not_awaited()
        message = mock_interaction.followup.send.call_args[0][0]
        assert "⚠️ Multiple contacts found for `john`." in message
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "search_term": "john",
            "filename": "candidate.pdf",
            "contact_found": False,
            "target_scope": "other",
            "reason": "multiple_contacts",
        }

    @pytest.mark.asyncio
    async def test_upload_resume_inferred_multiple_matches_records_error(
        self, crm_cog, mock_interaction
    ):
        """Resume inference returning multiple matches should be logged as an error."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Operator"
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        crm_cog._audit_command = Mock()

        with (
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_infer_contact_from_resume",
                new=AsyncMock(
                    return_value=(
                        None,
                        {"reason": "multiple_matches", "value": "jane@example.com"},
                    )
                ),
            ),
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                None,
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert (
            "⚠️ Multiple contacts match `jane@example.com` from the resume." in message
        )
        crm_cog._audit_command.assert_called_once()
        audit_kwargs = crm_cog._audit_command.call_args.kwargs
        assert audit_kwargs["action"] == "crm.upload_resume"
        assert audit_kwargs["result"] == "error"
        assert audit_kwargs["metadata"] == {
            "filename": "candidate.pdf",
            "target_scope": "resume_inferred",
            "reason": "multiple_matches",
            "inferred_value": "jane@example.com",
        }

    @pytest.mark.asyncio
    async def test_upload_resume_no_matching_inferred_contact_shows_name_and_email(
        self, crm_cog, mock_interaction
    ):
        """No-match inference should show parsed name/email for the candidate."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Requester"
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        resume_file = Mock()
        resume_file.filename = "candidate.pdf"
        resume_file.size = 1024
        resume_file.read = AsyncMock(return_value=b"resume-bytes")

        with (
            patch.object(
                crm_cog,
                "_infer_contact_from_resume",
                new=AsyncMock(return_value=(None, {"reason": "no_matching_contact"})),
            ),
            patch.object(
                crm_cog,
                "_build_resume_parsed_identity_summary",
                return_value=(
                    "\nParsed contact details: name=`Jane Doe`, email=`jane@example.com`"
                ),
            ),
            patch.object(
                crm_cog,
                "_find_contact_by_discord_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
        ):
            await crm_cog.upload_resume.callback(
                crm_cog,
                mock_interaction,
                resume_file,
                None,
                False,
                None,
            )

        message = mock_interaction.followup.send.call_args[0][0]
        assert "⚠️ Could not find a unique contact from this resume." in message
        assert (
            "Parsed contact details: name=`Jane Doe`, email=`jane@example.com`"
            in message
        )
        assert "view" in mock_interaction.followup.send.call_args.kwargs

    @pytest.mark.asyncio
    async def test_resume_create_contact_view_logs_create_failure(
        self, crm_cog, mock_interaction
    ):
        """Test create-contact view writes debug context when contact creation fails."""
        original_interaction = Mock()
        original_interaction.user = Mock()
        original_interaction.user.id = 123

        mock_interaction.user.id = 123
        mock_interaction.user.name = "Requester"
        mock_interaction.message = None
        mock_interaction.response = AsyncMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        crm_cog._audit_command = Mock()
        crm_cog._build_resume_create_contact_payload = Mock(
            return_value={
                "name": "Resume Candidate",
                "emailAddress": "person@example.com",
            }
        )
        crm_cog.espo_api.status_code = 422
        crm_cog.espo_api.request.side_effect = EspoAPIError("validation failed")

        view = ResumeCreateContactView(
            crm_cog=crm_cog,
            interaction=original_interaction,
            file_content=b"resume-bytes",
            filename="candidate.pdf",
            file_size=1024,
            search_term=None,
            overwrite=False,
            link_user=None,
            inferred_contact_meta={"reason": "no_matching_contact"},
            target_scope="resume_inferred",
        )

        with patch(
            "five08.discord_bot.cogs.crm.logger.exception"
        ) as mock_log_exception:
            create_button = next(
                child
                for child in view.children
                if isinstance(child, discord.ui.Button)
                and child.label == "Create Contact"
            )
            await create_button.callback(mock_interaction)

        mock_log_exception.assert_called_once()
        crm_cog._audit_command.assert_called_once()
        audit_metadata = crm_cog._audit_command.call_args.kwargs["metadata"]
        assert audit_metadata["reason"] == "contact_create_failed"
        assert audit_metadata["status_code"] == 422
        assert audit_metadata["create_payload_keys"] == [
            "emailAddress",
            "firstName",
            "lastName",
            "name",
        ]
        mock_interaction.followup.send.assert_called_once()
        failure_message = mock_interaction.followup.send.call_args.args[0]
        assert (
            "Could not create a contact from this resume: `validation failed` (status 422)."
            in failure_message
        )
        assert "Please provide `search_term` or `link_user`." in failure_message

    @pytest.mark.asyncio
    async def test_resume_create_contact_view_sanitizes_long_error_details(
        self, crm_cog, mock_interaction
    ):
        """Very long/unsafe error details are sanitized before being sent to Discord."""
        raw_error = "validation\x00failed\nwith\tcontrol\nchars\r and `backticks` " + (
            "x" * 2500
        )
        mock_interaction.user.id = 123
        mock_interaction.user.name = "Requester"
        mock_interaction.message = None
        mock_interaction.response = AsyncMock()
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        crm_cog._audit_command = Mock()
        crm_cog._build_resume_create_contact_payload = Mock(
            return_value={
                "name": "Resume Candidate",
                "emailAddress": "person@example.com",
            }
        )
        crm_cog.espo_api.status_code = 422
        crm_cog.espo_api.request.side_effect = Exception(raw_error)

        view = ResumeCreateContactView(
            crm_cog=crm_cog,
            interaction=mock_interaction,
            file_content=b"resume-bytes",
            filename="candidate.pdf",
            file_size=1024,
            search_term=None,
            overwrite=False,
            link_user=None,
            inferred_contact_meta={"reason": "no_matching_contact"},
            target_scope="resume_inferred",
        )

        create_button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.label == "Create Contact"
        )

        await create_button.callback(mock_interaction)

        failure_message = mock_interaction.followup.send.call_args.args[0]
        sanitized = crm_cog._sanitize_error_message_for_discord(raw_error)
        assert sanitized in failure_message
        assert "`backticks`" not in failure_message
        assert "\x00" not in failure_message
        assert "\r" not in failure_message
        assert "\n" not in failure_message
        assert "\t" not in failure_message
        assert len(sanitized) <= 1900

    @pytest.mark.asyncio
    async def test_reprocess_resume_shows_confirmation(self, crm_cog, mock_interaction):
        """Show a confirmation view before reprocessing a contact's latest resume."""
        mock_interaction.user.id = 101
        mock_interaction.user.name = "Operator"

        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contacts_for_reprocess_resume",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "contact123",
                            "name": "Candidate User",
                            "resumeIds": ["resume123"],
                            "resumeNames": {"resume123": "candidate.pdf"},
                        },
                    ]
                ),
            ),
            patch.object(
                crm_cog,
                "_get_latest_resume_attachment_for_contact",
                new=AsyncMock(return_value=("resume123", "candidate.pdf")),
            ),
        ):
            await crm_cog.reprocess_resume.callback(
                crm_cog, mock_interaction, "candidate"
            )

        mock_interaction.followup.send.assert_called_once()
        followup_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "view" in followup_kwargs
        assert isinstance(followup_kwargs["view"], ResumeReprocessConfirmationView)
        view = followup_kwargs["view"]
        assert view.contact_id == "contact123"
        assert view.contact_name == "Candidate User"
        assert view.attachment_id == "resume123"
        assert view.filename == "candidate.pdf"

    @pytest.mark.asyncio
    async def test_reprocess_resume_shows_no_contact_message(
        self, crm_cog, mock_interaction
    ):
        """Error when no contact matches the reprocess search term."""
        mock_interaction.user.id = 101
        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contacts_for_reprocess_resume",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await crm_cog.reprocess_resume.callback(
                crm_cog, mock_interaction, "missing-user"
            )

        mock_interaction.followup.send.assert_called_once_with(
            "❌ No contact found for: `missing-user`"
        )

    @pytest.mark.asyncio
    async def test_reprocess_resume_shows_multiple_contacts_selector(
        self, crm_cog, mock_interaction
    ):
        """Show selection view when multiple contacts match the reprocess search term."""
        mock_interaction.user.id = 101
        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contacts_for_reprocess_resume",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "contact123",
                            "name": "John Doe",
                            "resumeIds": ["resume123"],
                            "resumeNames": {},
                        },
                        {
                            "id": "contact456",
                            "name": "John Smith",
                            "resumeIds": [],
                            "resumeNames": {},
                        },
                    ]
                ),
            ),
        ):
            await crm_cog.reprocess_resume.callback(crm_cog, mock_interaction, "john")

        mock_interaction.followup.send.assert_called_once()
        followup_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert "view" in followup_kwargs
        assert isinstance(followup_kwargs["view"], ReprocessResumeSelectionView)
        embed = mock_interaction.followup.send.call_args.kwargs["embed"]
        labels = [
            child.label
            for child in followup_kwargs["view"].children
            if isinstance(child, discord.ui.Button)
        ]
        assert labels == ["John Doe", "Upload Resume: John Smith"]
        assert "📄 Resume: on file" in embed.fields[0].value
        assert "📄 Resume: missing" in embed.fields[1].value

    @pytest.mark.asyncio
    async def test_reprocess_resume_selection_button_without_resume_hands_off_to_upload(
        self, crm_cog
    ):
        """No-resume selections should route to the upload-resume handoff."""
        crm_cog._prompt_reprocess_resume_confirmation = AsyncMock()
        crm_cog._prompt_upload_resume_for_contact = AsyncMock()

        view = ReprocessResumeSelectionView(
            crm_cog=crm_cog,
            requester_id=101,
            search_term="candidate",
        )
        view.add_contact_button(
            {
                "id": "contact123",
                "name": "Candidate User",
                "resumeIds": [],
                "resumeNames": {},
            }
        )

        button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button)
            and child.label == "Upload Resume: Candidate User"
        )
        interaction = AsyncMock()
        interaction.user = Mock()
        interaction.user.id = 101
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.message = None

        await button.callback(interaction)

        crm_cog._prompt_upload_resume_for_contact.assert_awaited_once()
        crm_cog._prompt_reprocess_resume_confirmation.assert_not_called()

    @pytest.mark.asyncio
    async def test_prompt_upload_resume_for_contact_shows_upload_resume_instructions(
        self, crm_cog, mock_interaction
    ):
        """Upload handoff should send the contact into the upload-resume flow."""
        crm_cog._audit_command = Mock()

        await crm_cog._prompt_upload_resume_for_contact(
            interaction=mock_interaction,
            contact={"id": "contact123", "name": "Candidate User"},
            search_term="candidate",
        )

        mock_interaction.followup.send.assert_called_once()
        message = mock_interaction.followup.send.call_args.args[0]
        assert "/upload-resume" in message
        assert "`contact123`" in message
        assert "Candidate User" in message

    @pytest.mark.asyncio
    async def test_prompt_upload_resume_for_contact_rejects_missing_contact_id(
        self, crm_cog, mock_interaction
    ):
        """Missing contact IDs should not fall through as the literal string 'None'."""
        crm_cog._audit_command = Mock()

        await crm_cog._prompt_upload_resume_for_contact(
            interaction=mock_interaction,
            contact={"id": None, "name": "Candidate User"},
            search_term="candidate",
        )

        mock_interaction.followup.send.assert_called_once_with(
            "❌ Contact ID not found."
        )

    @pytest.mark.asyncio
    async def test_reprocess_resume_exact_match_without_resume_hands_off_to_upload(
        self, crm_cog, mock_interaction
    ):
        """Exact-match reprocess requests should use the upload handoff when needed."""
        mock_interaction.user.id = 101
        crm_cog._prompt_reprocess_resume_confirmation = AsyncMock()
        crm_cog._prompt_upload_resume_for_contact = AsyncMock()
        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=True,
            ),
            patch.object(
                crm_cog,
                "_search_contacts_for_reprocess_resume",
                new=AsyncMock(
                    return_value=[
                        {
                            "id": "contact123",
                            "name": "Candidate User",
                            "resumeIds": [],
                            "resumeNames": {},
                        }
                    ]
                ),
            ),
        ):
            await crm_cog.reprocess_resume.callback(
                crm_cog, mock_interaction, "candidate"
            )

        crm_cog._prompt_upload_resume_for_contact.assert_awaited_once()
        crm_cog._prompt_reprocess_resume_confirmation.assert_not_called()

    @pytest.mark.asyncio
    async def test_reprocess_resume_requires_steering(self, crm_cog, mock_interaction):
        """Non-steering users cannot reprocess resumes."""
        mock_interaction.user.id = 101
        with (
            patch(
                "five08.discord_bot.cogs.crm.settings.api_shared_secret",
                "test-shared-secret",
            ),
            patch(
                "five08.discord_bot.cogs.crm.check_user_roles_with_hierarchy",
                return_value=False,
            ),
        ):
            await crm_cog.reprocess_resume.callback(
                crm_cog, mock_interaction, "candidate"
            )

        message = mock_interaction.followup.send.call_args.args[0]
        assert "You must have Steering Committee role or higher" in message

    @pytest.mark.asyncio
    async def test_reprocess_confirmation_view_calls_reprocess_preview(
        self, crm_cog, mock_interaction
    ):
        """Confirming resume reprocessing triggers resume extraction with reprocess action."""
        original_interaction = Mock()
        original_interaction.user = Mock()
        original_interaction.user.id = 101

        crm_cog._run_resume_extract_and_preview = AsyncMock()

        confirm_interaction = AsyncMock()
        confirm_interaction.user = Mock()
        confirm_interaction.user.id = 101
        confirm_interaction.user.name = "Operator"
        confirm_interaction.response = AsyncMock()
        confirm_interaction.response.defer = AsyncMock()
        confirm_interaction.followup = AsyncMock()
        confirm_interaction.followup.send = AsyncMock()
        confirm_interaction.message = None

        view = ResumeReprocessConfirmationView(
            crm_cog=crm_cog,
            interaction=original_interaction,
            contact_id="contact123",
            contact_name="Candidate User",
            attachment_id="resume123",
            filename="candidate.pdf",
        )
        confirm_button = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button)
            and child.label == "Reprocess Resume"
        )
        await confirm_button.callback(confirm_interaction)

        crm_cog._run_resume_extract_and_preview.assert_awaited_once()
        kwargs = crm_cog._run_resume_extract_and_preview.await_args.kwargs
        assert kwargs["action"] == "crm.reprocess_resume"
        assert (
            kwargs["status_message"]
            == "🔄 Reprocessing resume and extracting profile fields now..."
        )

    @pytest.mark.asyncio
    async def test_run_resume_extract_and_preview_uses_refresh_token_for_reprocess(
        self, crm_cog, mock_interaction
    ):
        """Explicit reprocess actions should bypass cached extract jobs."""
        crm_cog._enqueue_resume_extract_job = AsyncMock(return_value="job-123")
        crm_cog._wait_for_backend_job_result = AsyncMock(
            return_value={
                "status": "succeeded",
                "result": {"success": False, "error": "boom"},
            }
        )
        crm_cog._build_resume_extract_debug_file = Mock(return_value=Mock())
        crm_cog._audit_command = Mock()

        with patch(
            "five08.discord_bot.cogs.crm.uuid4",
            return_value=Mock(hex="refresh-token-123"),
        ):
            await crm_cog._run_resume_extract_and_preview(
                mock_interaction,
                contact_id="contact123",
                contact_name="Candidate User",
                attachment_id="resume123",
                filename="candidate.pdf",
                link_member=None,
                action="crm.reprocess_resume",
                status_message="🔄 Reprocessing resume and extracting profile fields now...",
            )

        kwargs = crm_cog._enqueue_resume_extract_job.await_args.kwargs
        assert kwargs["refresh_token"] == "refresh-token-123"

    @pytest.mark.asyncio
    async def test_build_match_candidates_posting_fetches_jd_links_from_text(
        self, jobs_cog
    ):
        """Starter text JD links should be fetched while non-JD links are skipped."""
        starter = Mock()
        jd_url = "https://boards.greenhouse.io/acme/jobs/12345"
        non_jd_url = "https://example.com/about"
        starter.content = f"We are hiring: {jd_url} and docs at {non_jd_url}"
        starter.attachments = []
        starter.embeds = []

        with patch.object(
            jobs_cog,
            "_fetch_match_candidates_link_text",
            new=AsyncMock(return_value="Senior backend role"),
        ) as fetch_mock:
            posting, metadata = await jobs_cog._build_match_candidates_posting(starter)

        fetch_mock.assert_awaited_once_with(jd_url)
        assert "Senior backend role" in posting
        assert metadata["links_discovered"] == 2
        assert metadata["links_fetched"] == 1

    @pytest.mark.asyncio
    async def test_build_match_candidates_posting_does_not_fetch_non_jd_links(
        self, jobs_cog
    ):
        """No fetch should occur when only non-JD links are present."""
        starter = Mock()
        starter.content = "Company info: https://example.com/about"
        starter.attachments = []
        starter.embeds = []

        with patch.object(
            jobs_cog,
            "_fetch_match_candidates_link_text",
            new=AsyncMock(return_value="ignored"),
        ) as fetch_mock:
            posting, metadata = await jobs_cog._build_match_candidates_posting(starter)

        fetch_mock.assert_not_awaited()
        assert "Referenced links:" in posting
        assert metadata["links_discovered"] == 1
        assert metadata["links_fetched"] == 0

    @pytest.mark.asyncio
    async def test_build_match_candidates_posting_scans_attachments_for_jd_links(
        self, jobs_cog
    ):
        """Attachment-extracted URLs should be treated as JD candidates."""
        starter = Mock()
        starter.content = ""
        starter.attachments = [Mock(filename="job-posting.pdf")]
        starter.embeds = []
        attachment_text = (
            "See full JD at https://jobs.lever.co/acme/abcde and apply there."
        )

        with (
            patch.object(
                jobs_cog,
                "_read_match_candidates_attachment_text",
                new=AsyncMock(return_value=attachment_text),
            ) as read_attachment_mock,
            patch.object(
                jobs_cog,
                "_fetch_match_candidates_link_text",
                new=AsyncMock(return_value="Role details from Lever"),
            ) as fetch_mock,
        ):
            posting, metadata = await jobs_cog._build_match_candidates_posting(starter)

        read_attachment_mock.assert_awaited_once()
        fetch_mock.assert_awaited_once_with("https://jobs.lever.co/acme/abcde")
        assert "Attachment job-posting.pdf" in posting
        assert "Role details from Lever" in posting
        assert metadata["attachments_scanned"] == 1
        assert metadata["attachments_extracted"] == 1
        assert metadata["links_fetched"] == 1


class TestResumeButtonView:
    """Tests for ResumeButtonView class."""

    @pytest.mark.asyncio
    async def test_button_view_initialization(self):
        """Test ResumeButtonView initialization."""
        view = ResumeButtonView()
        assert view.timeout == 300
        assert len(view.children) == 0

    @pytest.mark.asyncio
    async def test_add_resume_button(self):
        """Test adding resume button to view."""
        view = ResumeButtonView()
        view.add_resume_button("John Doe", "resume123")

        assert len(view.children) == 1
        button = view.children[0]
        assert isinstance(button, ResumeDownloadButton)
        assert button.contact_name == "John Doe"
        assert button.resume_id == "resume123"

    @pytest.mark.asyncio
    async def test_add_resume_button_limit(self):
        """Test that view respects 5 button limit."""
        view = ResumeButtonView()

        # Add 6 buttons
        for i in range(6):
            view.add_resume_button(f"Contact {i}", f"resume{i}")

        # Should only have 5 buttons
        assert len(view.children) == 5


class TestResumeDownloadButton:
    """Tests for ResumeDownloadButton class."""

    def test_button_initialization(self):
        """Test ResumeDownloadButton initialization."""
        button = ResumeDownloadButton("John Doe", "resume123")

        assert button.contact_name == "John Doe"
        assert button.resume_id == "resume123"
        assert button.label == "📄 Resume: John Doe"
        assert button.style == discord.ButtonStyle.secondary
        assert button.custom_id == "resume_resume123"

    def test_button_long_name_truncation(self):
        """Test that long contact names are truncated in button label."""
        long_name = "A" * 80  # Very long name
        button = ResumeDownloadButton(long_name, "resume123")

        assert len(button.label) <= 80
        assert button.label.endswith("...")

    @pytest.mark.asyncio
    async def test_button_callback_success(self):
        """Test successful button callback."""
        button = ResumeDownloadButton("John Doe", "resume123")

        # Mock interaction
        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.defer = AsyncMock()

        # Mock user with Member role
        member_role = Mock()
        member_role.name = "Member"
        mock_interaction.user.roles = [member_role]

        # Mock CRM cog
        mock_crm_cog = Mock()
        mock_crm_cog._check_member_role = Mock(return_value=True)
        mock_crm_cog._download_and_send_resume = AsyncMock()

        # Mock client
        mock_client = Mock()
        mock_client.get_cog = Mock(return_value=mock_crm_cog)
        mock_interaction.client = mock_client

        await button.callback(mock_interaction)

        mock_crm_cog._download_and_send_resume.assert_called_once_with(
            mock_interaction, "John Doe", "resume123"
        )

    @pytest.mark.asyncio
    async def test_button_callback_no_member_role(self):
        """Test button callback without Member role."""
        button = ResumeDownloadButton("John Doe", "resume123")

        # Mock interaction
        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()

        # Mock user without Member role
        other_role = Mock()
        other_role.name = "User"
        mock_interaction.user.roles = [other_role]

        # Mock CRM cog
        mock_crm_cog = Mock()
        mock_crm_cog._check_member_role = Mock(return_value=False)

        # Mock client
        mock_client = Mock()
        mock_client.get_cog = Mock(return_value=mock_crm_cog)
        mock_interaction.client = mock_client

        await button.callback(mock_interaction)

        mock_interaction.response.send_message.assert_called_once_with(
            "❌ You must have the Member role to download resumes.", ephemeral=True
        )

    @pytest.mark.asyncio
    async def test_button_callback_no_cog(self):
        """Test button callback when CRM cog not available."""
        button = ResumeDownloadButton("John Doe", "resume123")

        # Mock interaction
        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        # Mock client
        mock_client = Mock()
        mock_client.get_cog = Mock(return_value=None)
        mock_interaction.client = mock_client

        await button.callback(mock_interaction)

        mock_interaction.response.send_message.assert_called_once_with(
            "❌ CRM functionality not available.", ephemeral=True
        )
