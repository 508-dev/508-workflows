"""
Unit tests for CRM cog functionality.
"""

import json

import pytest
from unittest.mock import Mock, AsyncMock, patch
import discord

from five08.discord_bot.cogs.crm import CRMCog, ResumeButtonView, ResumeDownloadButton
from five08.clients.espo import EspoAPIError


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
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.user = Mock()
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

    def test_parse_contact_skill_attrs_recovers_python_literal(self, crm_cog):
        """Malformed JSON-like skill attrs should recover via literal parsing."""
        parsed = crm_cog._parse_contact_skill_attrs(
            "{'python': {'strength': 4}, 'go': {'strength': 3},}"
        )
        assert parsed == {"python": 4, "go": 3}

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
        assert "`go` (5/5)" in embed.fields[0].value
        assert "`python` (4/5)" in embed.fields[0].value

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
        assert "`python`" in embed.fields[0].value
        assert "`sql`" in embed.fields[0].value
        assert "/5" not in embed.fields[0].value

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
        assert search_params["where"][0]["attribute"] == "name"
        assert search_params["where"][0]["value"] == "john"

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

    def test_query_normalization_username(self, crm_cog):
        """Test that username gets @508.dev appended."""
        # This would be tested in the actual command, but we can verify the logic
        query = "john"
        # Simulate the normalization logic from get_resume
        normalized = (
            f"{query}@508.dev"
            if "@" not in query and not any(char in query for char in [" ", ".", "#"])
            else query
        )
        assert normalized == "john@508.dev"

    def test_query_normalization_at_sign(self, crm_cog):
        """Test that john@ becomes john@508.dev."""
        query = "john@"
        # Simulate the normalization logic from get_resume
        normalized = f"{query}508.dev" if query.endswith("@") else query
        assert normalized == "john@508.dev"

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

    @pytest.mark.asyncio
    async def test_set_github_username_success_self(self, crm_cog, mock_interaction):
        """Test successful GitHub username update for self."""
        mock_interaction.user.id = 123456789
        mock_interaction.user.display_name = "TestUser"

        # Mock API responses
        # First call: search for contact by Discord ID
        search_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "Test User",
                    "cDiscordUserID": "123456789",
                    "cGitHubUsername": "",
                }
            ]
        }

        # Second call: update contact
        update_response = {"id": "contact123"}

        crm_cog.espo_api.request.side_effect = [search_response, update_response]

        # Call the function (without search_term means setting for self)
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify API calls
        assert crm_cog.espo_api.request.call_count == 2

        # Check search call
        search_call = crm_cog.espo_api.request.call_args_list[0]
        assert search_call[0][0] == "GET"
        assert search_call[0][1] == "Contact"
        search_params = search_call[0][2]
        assert search_params["where"][0]["attribute"] == "cDiscordUserID"
        assert search_params["where"][0]["value"] == "123456789"

        # Check update call
        update_call = crm_cog.espo_api.request.call_args_list[1]
        assert update_call[0][0] == "PUT"
        assert update_call[0][1] == "Contact/contact123"
        assert update_call[0][2]["cGitHubUsername"] == "myusername"

        # Verify success message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        # Check if message was sent with embed
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert embed.title == "✅ GitHub Username Set"
        assert "Successfully updated GitHub username" in embed.description

    @pytest.mark.asyncio
    async def test_set_github_username_api_error(self, crm_cog, mock_interaction):
        """Test GitHub username update with API error."""
        mock_interaction.user.id = 123456789

        # Mock API to raise an error
        crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")

        # Call the function
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify error message was sent
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌ CRM API error:" in message
        assert "Connection failed" in message

    @pytest.mark.asyncio
    async def test_set_github_username_user_not_found(self, crm_cog, mock_interaction):
        """Test GitHub username update when user not found in CRM."""
        mock_interaction.user.id = 123456789
        mock_interaction.user.display_name = "TestUser"

        # Mock API response with empty list (no contact found)
        search_response = {"list": []}

        crm_cog.espo_api.request.return_value = search_response

        # Call the function
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify only one API call was made (search only, no update)
        crm_cog.espo_api.request.assert_called_once()

        # Verify error message was sent
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌" in message
        assert "Discord account is not linked to a CRM contact" in message
        assert "Steering Committee" in message

    @pytest.mark.asyncio
    async def test_set_github_username_permission_check(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Test permission check when setting GitHub username for others."""
        # User has only Member role, not Steering Committee
        mock_interaction.user.roles = [mock_member_role]

        # Try to set GitHub username for someone else (with search_term)
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "someusername", "john@508.dev"
        )

        # Verify no API calls were made
        crm_cog.espo_api.request.assert_not_called()

        # Verify permission error message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌" in message
        assert "Steering Committee role or higher" in message

    @pytest.mark.asyncio
    async def test_set_github_username_for_other_with_permission(
        self, crm_cog, mock_interaction
    ):
        """Test setting GitHub username for another user with proper permissions."""
        # Give user Steering Committee role
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        # Mock API responses
        search_response = {
            "list": [
                {
                    "id": "contact456",
                    "name": "John Doe",
                    "c508Email": "john@508.dev",
                    "cGitHubUsername": "",
                }
            ]
        }
        update_response = {"id": "contact456"}

        crm_cog.espo_api.request.side_effect = [search_response, update_response]

        # Call the function with search_term (setting for someone else)
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "johngithub", "john@508.dev"
        )

        # Verify API calls
        assert crm_cog.espo_api.request.call_count == 2

        # Check update call has correct username
        update_call = crm_cog.espo_api.request.call_args_list[1]
        assert update_call[0][2]["cGitHubUsername"] == "johngithub"

        # Verify success message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert "GitHub Username Set" in embed.title

    @pytest.mark.asyncio
    async def test_set_github_username_multiple_contacts_found(
        self, crm_cog, mock_interaction
    ):
        """Test setting GitHub username when multiple contacts found."""
        # Give user Steering Committee role
        steering_role = Mock()
        steering_role.name = "Steering Committee"
        mock_interaction.user.roles = [steering_role]

        # Mock search helper to return multiple contacts
        with patch.object(crm_cog, "_search_contact_for_linking") as mock_search:
            mock_search.return_value = [
                {"id": "contact1", "name": "John Doe", "c508Email": "john1@508.dev"},
                {"id": "contact2", "name": "John Smith", "c508Email": "john2@508.dev"},
            ]

            # Call the function with search_term
            await crm_cog.set_github_username.callback(
                crm_cog, mock_interaction, "johngithub", "john"
            )

            # Verify no update API call was made
            crm_cog.espo_api.request.assert_not_called()

            # Verify error message about multiple contacts
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            message = call_args[0][0]
            assert "❌ Multiple contacts found" in message
            assert "john" in message
            assert "more specific" in message

    @pytest.mark.asyncio
    async def test_set_github_username_cleans_at_prefix(
        self, crm_cog, mock_interaction
    ):
        """Test that @ prefix is removed from GitHub username."""
        mock_interaction.user.id = 123456789

        # Mock API responses
        search_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "Test User",
                    "cDiscordUserID": "123456789",
                    "cGitHubUsername": "",
                }
            ]
        }
        update_response = {"id": "contact123"}

        crm_cog.espo_api.request.side_effect = [search_response, update_response]

        # Call with @ prefix in username
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "@myusername", None
        )

        # Verify the @ was stripped in the update call
        update_call = crm_cog.espo_api.request.call_args_list[1]
        assert update_call[0][2]["cGitHubUsername"] == "myusername"  # @ removed

        # Verify the embed shows it with @ (for display)
        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        github_field = [f for f in embed.fields if "GitHub" in f.name][0]
        assert "@myusername" in github_field.value  # Display shows with @

    @pytest.mark.asyncio
    async def test_set_github_username_update_failure(self, crm_cog, mock_interaction):
        """Test when update request returns None/False."""
        mock_interaction.user.id = 123456789

        # Mock API responses
        search_response = {
            "list": [
                {
                    "id": "contact123",
                    "name": "Test User",
                    "cDiscordUserID": "123456789",
                    "cGitHubUsername": "",
                }
            ]
        }
        # Update returns None to simulate failure
        crm_cog.espo_api.request.side_effect = [search_response, None]

        # Call the function
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify both API calls were made
        assert crm_cog.espo_api.request.call_count == 2

        # Verify error message was sent
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌ Failed to update contact in CRM" in message
        assert "try again" in message

    @pytest.mark.asyncio
    async def test_set_github_username_contact_without_id(
        self, crm_cog, mock_interaction
    ):
        """Test when contact has no ID field."""
        mock_interaction.user.id = 123456789

        # Mock API response with contact missing ID
        search_response = {
            "list": [
                {
                    # Missing "id" field
                    "name": "Test User",
                    "cDiscordUserID": "123456789",
                    "cGitHubUsername": "",
                }
            ]
        }

        crm_cog.espo_api.request.return_value = search_response

        # Call the function
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify only search call was made (no update)
        crm_cog.espo_api.request.assert_called_once()

        # Verify error message was sent
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌ Contact ID not found" in message

    @pytest.mark.asyncio
    async def test_set_github_username_unexpected_exception(
        self, crm_cog, mock_interaction
    ):
        """Test handling of unexpected exceptions."""
        mock_interaction.user.id = 123456789

        # Mock API to raise an unexpected exception
        crm_cog.espo_api.request.side_effect = ValueError("Unexpected error occurred")

        # Call the function
        await crm_cog.set_github_username.callback(
            crm_cog, mock_interaction, "myusername", None
        )

        # Verify error message was sent
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "❌ An unexpected error occurred" in message
        assert "setting the GitHub username" in message

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

    # ------------------------------------------------------------------
    # /update-skills tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_search_contacts_for_mutation_returns_all_name_matches(
        self, crm_cog
    ):
        """Name search always returns multiple results instead of auto-selecting."""
        crm_cog.espo_api.request.return_value = {
            "list": [
                {"id": "c1", "name": "John Doe"},
                {"id": "c2", "name": "John Smith"},
            ]
        }

        contacts = await crm_cog._search_contacts_for_mutation("John Doe")

        assert len(contacts) == 2
        # Verify maxSize=10 was used (not 1)
        call_args = crm_cog.espo_api.request.call_args
        assert call_args[0][2]["maxSize"] == 10

    def test_update_skills_parses_valid_input(self, crm_cog):
        """Valid comma-separated skill:strength pairs are parsed correctly."""
        result = crm_cog._parse_skills_input("python:5, react:4, go:3")
        assert result == {"python": 5, "react": 4, "go": 3}

    def test_update_skills_rejects_invalid_strength(self, crm_cog):
        """Strength outside 1-5 raises ValueError."""
        with pytest.raises(ValueError, match="out of range"):
            crm_cog._parse_skills_input("python:6")
        with pytest.raises(ValueError, match="out of range"):
            crm_cog._parse_skills_input("python:0")

    def test_update_skills_rejects_malformed_input(self, crm_cog):
        """Missing colon or empty skill raises ValueError."""
        with pytest.raises(ValueError, match=r"expected `skill:strength`"):
            crm_cog._parse_skills_input("python")
        with pytest.raises(ValueError, match="Empty skill name"):
            crm_cog._parse_skills_input(":5")

    def test_update_skills_normalizes_skill_names(self, crm_cog):
        """Skill aliases and punctuation are normalized."""
        result = crm_cog._parse_skills_input("Node.JS:4, js:5")
        assert "node" in result
        assert "javascript" in result

    @pytest.mark.asyncio
    async def test_update_skills_self_success(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """No search_term updates the invoker's own skills."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 123456789

        crm_cog.espo_api.request.side_effect = [
            # _find_contact_by_discord_id
            {"list": [{"id": "c1", "name": "Jane Doe"}]},
            # GET full contact
            {"id": "c1", "name": "Jane Doe", "cSkillAttrs": '{"sql":{"strength":3}}'},
            # PUT update
            {"id": "c1"},
        ]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5, react:4", None
        )

        # Verify PUT was called with merged skills
        put_call = crm_cog.espo_api.request.call_args_list[2]
        assert put_call[0][0] == "PUT"
        assert put_call[0][1] == "Contact/c1"
        written_attrs = json.loads(put_call[0][2]["cSkillAttrs"])
        assert written_attrs["python"]["strength"] == 5
        assert written_attrs["react"]["strength"] == 4
        assert written_attrs["sql"]["strength"] == 3  # preserved

        mock_interaction.followup.send.assert_called_once()
        embed = mock_interaction.followup.send.call_args[1]["embed"]
        assert embed.title == "✅ Skills Updated"

    @pytest.mark.asyncio
    async def test_update_skills_self_not_linked(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Discord account not linked returns error message."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 999

        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5", None
        )

        msg = mock_interaction.followup.send.call_args[0][0]
        assert "not linked" in msg

    @pytest.mark.asyncio
    async def test_update_skills_other_requires_steering_committee(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Member role cannot update another user's skills."""
        mock_interaction.user.roles = [mock_member_role]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5", "john@508.dev"
        )

        crm_cog.espo_api.request.assert_not_called()
        msg = mock_interaction.followup.send.call_args[0][0]
        assert "Steering Committee role or higher" in msg

    @pytest.mark.asyncio
    async def test_update_skills_other_with_permission(
        self, crm_cog, mock_interaction
    ):
        """Steering Committee+ can update another user's skills."""
        sc_role = Mock()
        sc_role.name = "Steering Committee"
        mock_interaction.user.roles = [sc_role]

        crm_cog.espo_api.request.side_effect = [
            # _search_contact_for_linking
            {
                "list": [
                    {
                        "id": "c2",
                        "name": "John Doe",
                        "emailAddress": "john@508.dev",
                    }
                ]
            },
            # GET full contact
            {"id": "c2", "name": "John Doe", "cSkillAttrs": None},
            # PUT update
            {"id": "c2"},
        ]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "go:3", "john@508.dev"
        )

        assert crm_cog.espo_api.request.call_count == 3
        embed = mock_interaction.followup.send.call_args[1]["embed"]
        assert embed.title == "✅ Skills Updated"
        assert "`go` (3/5)" in embed.fields[0].value

    @pytest.mark.asyncio
    async def test_update_skills_other_not_found(
        self, crm_cog, mock_interaction
    ):
        """Search returning no contacts shows error."""
        sc_role = Mock()
        sc_role.name = "Steering Committee"
        mock_interaction.user.roles = [sc_role]

        crm_cog.espo_api.request.return_value = {"list": []}

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5", "nobody@508.dev"
        )

        msg = mock_interaction.followup.send.call_args[0][0]
        assert "No contact found" in msg

    @pytest.mark.asyncio
    async def test_update_skills_other_multiple_contacts(
        self, crm_cog, mock_interaction
    ):
        """Multiple contacts found shows refinement message."""
        sc_role = Mock()
        sc_role.name = "Steering Committee"
        mock_interaction.user.roles = [sc_role]

        crm_cog.espo_api.request.return_value = {
            "list": [
                {"id": "c1", "name": "John A"},
                {"id": "c2", "name": "John B"},
            ]
        }

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5", "john"
        )

        msg = mock_interaction.followup.send.call_args[0][0]
        assert "Multiple contacts found" in msg

    @pytest.mark.asyncio
    async def test_update_skills_merges_with_existing(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Existing skills are preserved, overlapping updated, new added."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 111

        crm_cog.espo_api.request.side_effect = [
            {"list": [{"id": "c1", "name": "Alice"}]},
            {
                "id": "c1",
                "name": "Alice",
                "cSkillAttrs": '{"python":{"strength":2},"sql":{"strength":4}}',
            },
            {"id": "c1"},
        ]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5, react:3", None
        )

        put_call = crm_cog.espo_api.request.call_args_list[2]
        written = json.loads(put_call[0][2]["cSkillAttrs"])
        assert written["python"]["strength"] == 5   # updated
        assert written["sql"]["strength"] == 4       # preserved
        assert written["react"]["strength"] == 3     # added

    @pytest.mark.asyncio
    async def test_update_skills_empty_existing_attrs(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Works when contact has no prior cSkillAttrs."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 222

        crm_cog.espo_api.request.side_effect = [
            {"list": [{"id": "c1", "name": "Bob"}]},
            {"id": "c1", "name": "Bob", "cSkillAttrs": None},
            {"id": "c1"},
        ]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "go:4", None
        )

        put_call = crm_cog.espo_api.request.call_args_list[2]
        written = json.loads(put_call[0][2]["cSkillAttrs"])
        assert written == {"go": {"strength": 4}}

    @pytest.mark.asyncio
    async def test_update_skills_api_error(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """EspoAPIError is handled gracefully."""
        mock_interaction.user.roles = [mock_member_role]
        mock_interaction.user.id = 333

        crm_cog.espo_api.request.side_effect = EspoAPIError("Connection failed")

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python:5", None
        )

        msg = mock_interaction.followup.send.call_args[0][0]
        assert "CRM API error" in msg

    @pytest.mark.asyncio
    async def test_update_skills_invalid_input_returns_error(
        self, crm_cog, mock_interaction, mock_member_role
    ):
        """Malformed skills input returns user-friendly error."""
        mock_interaction.user.roles = [mock_member_role]

        await crm_cog.update_skills.callback(
            crm_cog, mock_interaction, "python", None
        )

        crm_cog.espo_api.request.assert_not_called()
        msg = mock_interaction.followup.send.call_args[0][0]
        assert "expected `skill:strength`" in msg


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
