"""
Unit tests for Kimai cog functionality.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from freezegun import freeze_time

from five08.discord_bot.cogs.kimai import KimaiCog
from five08.clients.kimai import KimaiAPIError


class TestKimaiCog:
    """Unit tests for KimaiCog class."""

    @pytest.fixture
    def mock_bot(self):
        """Create a mock bot for testing."""
        bot = Mock()
        bot.get_cog = Mock()
        return bot

    @pytest.fixture
    def mock_kimai_api(self):
        """Create a mock KimaiAPI for testing."""
        with patch("five08.clients.kimai.KimaiAPI") as mock_api_class:
            mock_api = Mock()
            mock_api_class.return_value = mock_api
            yield mock_api

    @pytest.fixture
    def kimai_cog(self, mock_bot, mock_kimai_api):
        """Create a KimaiCog instance for testing."""
        cog = KimaiCog(mock_bot)
        cog.api = mock_kimai_api
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
    def mock_steering_role(self):
        """Create a mock Steering Committee role."""
        role = Mock()
        role.name = "Steering Committee"
        return role

    def test_cog_initialization(self, mock_bot, mock_kimai_api):
        """Test Kimai cog initialization."""
        cog = KimaiCog(mock_bot)
        assert cog.bot == mock_bot
        assert cog.api is not None

    @pytest.mark.asyncio
    async def test_project_hours_success(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test successful project hours retrieval."""
        mock_interaction.user.roles = [mock_steering_role]

        # Mock project
        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project

        # Mock hours breakdown
        mock_hours = {
            "John Doe": {
                "hours": 10.5,
                "entries": 5,
                "duration_seconds": 37800,
                "billed_amount": 1050.0,
            },
            "Jane Smith": {
                "hours": 8.0,
                "entries": 3,
                "duration_seconds": 28800,
                "billed_amount": 800.0,
            },
        }
        kimai_cog.api.get_project_hours_by_user.return_value = mock_hours

        # Call the callback function to bypass app_commands decorator
        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project"
        )

        # Verify API calls
        kimai_cog.api.get_project_by_name.assert_called_once_with("Test Project")
        kimai_cog.api.get_project_hours_by_user.assert_called_once()

        # Verify response was sent with embed
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert "Test Project" in embed.title

    @pytest.mark.asyncio
    async def test_project_hours_project_not_found(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours when project is not found."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_project_by_name.return_value = None

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Nonexistent Project"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "not found" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_project_hours_with_month_filter(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours with month filter."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project
        kimai_cog.api.get_project_hours_by_user.return_value = {}

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project", month="2024-03"
        )

        # Verify that date range was parsed correctly
        call_args = kimai_cog.api.get_project_hours_by_user.call_args
        assert call_args[1]["begin"] is not None
        assert call_args[1]["end"] is not None

    @pytest.mark.asyncio
    async def test_project_hours_with_custom_dates(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours with custom start and end dates."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project
        kimai_cog.api.get_project_hours_by_user.return_value = {}

        await kimai_cog.project_hours.callback(
            kimai_cog,
            mock_interaction,
            "Test Project",
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        call_args = kimai_cog.api.get_project_hours_by_user.call_args
        assert call_args[1]["begin"].strftime("%Y-%m-%d") == "2024-01-01"
        assert call_args[1]["end"].strftime("%Y-%m-%d") == "2024-01-31"

    @pytest.mark.asyncio
    async def test_project_hours_api_error(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours with API error."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_project_by_name.side_effect = KimaiAPIError(
            "Connection failed"
        )

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "Failed to retrieve project hours" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_project_hours_invalid_date_format(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours with invalid date format."""
        mock_interaction.user.roles = [mock_steering_role]

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project", month="invalid-date"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "Invalid date format" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_project_hours_no_entries(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project hours with no time entries."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project
        kimai_cog.api.get_project_hours_by_user.return_value = {}

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        # Should show 0 hours in hh:mm format
        assert "0:00" in embed.fields[0].value

    @pytest.mark.asyncio
    async def test_list_projects_success(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test successful project listing."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_projects = [
            {
                "id": 1,
                "name": "Project 1",
                "customer": {"name": "Customer A"},
                "visible": True,
            },
            {
                "id": 2,
                "name": "Project 2",
                "customer": {"name": "Customer B"},
                "visible": False,
            },
        ]
        kimai_cog.api.get_projects.return_value = mock_projects

        await kimai_cog.list_projects.callback(kimai_cog, mock_interaction)

        kimai_cog.api.get_projects.assert_called_once()
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]

    @pytest.mark.asyncio
    async def test_list_projects_no_projects(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project listing with no projects."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_projects.return_value = []

        await kimai_cog.list_projects.callback(kimai_cog, mock_interaction)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "No projects found" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_list_projects_api_error(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project listing with API error."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_projects.side_effect = KimaiAPIError("Connection failed")

        await kimai_cog.list_projects.callback(kimai_cog, mock_interaction)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "Failed to retrieve projects" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_status_success(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test successful API status check."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_projects.return_value = [
            {"id": 1, "name": "Project 1"},
            {"id": 2, "name": "Project 2"},
        ]

        await kimai_cog.status.callback(kimai_cog, mock_interaction)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert "Connection successful" in embed.description

    @pytest.mark.asyncio
    async def test_status_api_error(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test API status check with connection error."""
        mock_interaction.user.roles = [mock_steering_role]

        kimai_cog.api.get_projects.side_effect = KimaiAPIError("Connection failed")

        await kimai_cog.status.callback(kimai_cog, mock_interaction)

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        assert "embed" in call_args[1]
        embed = call_args[1]["embed"]
        assert "Connection failed" in embed.description

    @freeze_time("2024-06-15 12:00:00")
    def test_parse_date_range_default_current_month(self, kimai_cog):
        """Test _parse_date_range defaults to current month."""
        begin, end, description = kimai_cog._parse_date_range(None, None, None)

        # With frozen time, we know exactly what "now" is
        assert begin.year == 2024
        assert begin.month == 6
        assert begin.day == 1
        assert "current month" in description.lower()

    def test_parse_date_range_with_month(self, kimai_cog):
        """Test _parse_date_range with month parameter."""
        begin, end, description = kimai_cog._parse_date_range("2024-03", None, None)

        assert begin.year == 2024
        assert begin.month == 3
        assert begin.day == 1
        assert end.month == 3
        assert "March 2024" in description

    def test_parse_date_range_with_custom_dates(self, kimai_cog):
        """Test _parse_date_range with custom start and end dates."""
        begin, end, description = kimai_cog._parse_date_range(
            None, "2024-01-15", "2024-01-20"
        )

        assert begin.strftime("%Y-%m-%d") == "2024-01-15"
        assert end.strftime("%Y-%m-%d") == "2024-01-20"
        assert "2024-01-15 to 2024-01-20" in description

    def test_parse_date_range_start_date_only(self, kimai_cog):
        """Test _parse_date_range with only start_date (defaults to end of month)."""
        begin, end, description = kimai_cog._parse_date_range(None, "2024-01-15", None)

        assert begin.strftime("%Y-%m-%d") == "2024-01-15"
        assert end.strftime("%Y-%m-%d") == "2024-01-31"  # End of January
        assert end.hour == 23
        assert end.minute == 59

    def test_parse_date_range_invalid_month(self, kimai_cog):
        """Test _parse_date_range with invalid month format."""
        with pytest.raises(ValueError) as exc_info:
            kimai_cog._parse_date_range("invalid", None, None)

        assert "Invalid month format" in str(exc_info.value)

    def test_parse_date_range_invalid_start_date(self, kimai_cog):
        """Test _parse_date_range with invalid start_date format."""
        with pytest.raises(ValueError) as exc_info:
            kimai_cog._parse_date_range(None, "invalid-date", None)

        assert "Invalid start_date format" in str(exc_info.value)

    def test_parse_date_range_invalid_end_date(self, kimai_cog):
        """Test _parse_date_range with invalid end_date format."""
        with pytest.raises(ValueError) as exc_info:
            kimai_cog._parse_date_range(None, "2024-01-15", "invalid")

        assert "Invalid end_date format" in str(exc_info.value)

    def test_parse_date_range_december_rollover(self, kimai_cog):
        """Test _parse_date_range handles December to January rollover."""
        begin, end, description = kimai_cog._parse_date_range("2024-12", None, None)

        assert begin.year == 2024
        assert begin.month == 12
        assert end.year == 2024
        assert end.month == 12
        assert end.day == 31

    def test_chunk_text_single_chunk(self, kimai_cog):
        """Test _chunk_text with content that fits in one chunk."""
        lines = ["Line 1", "Line 2", "Line 3"]
        chunks = kimai_cog._chunk_text(lines, 1024)

        assert len(chunks) == 1
        assert chunks[0] == "Line 1\nLine 2\nLine 3"

    def test_chunk_text_multiple_chunks(self, kimai_cog):
        """Test _chunk_text splits into multiple chunks."""
        # Create lines that will exceed max_length
        lines = ["A" * 100 for _ in range(20)]
        chunks = kimai_cog._chunk_text(lines, 500)

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 500

    def test_chunk_text_respects_line_boundaries(self, kimai_cog):
        """Test _chunk_text doesn't split individual lines."""
        lines = ["Line 1", "Line 2", "Line 3"]
        chunks = kimai_cog._chunk_text(lines, 10)

        # Each line should be in its own chunk since they're too long
        assert len(chunks) == 3

    def test_chunk_text_empty_lines(self, kimai_cog):
        """Test _chunk_text with empty list."""
        chunks = kimai_cog._chunk_text([], 1024)

        assert len(chunks) == 0

    @pytest.mark.asyncio
    async def test_project_hours_long_breakdown_chunking(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test that project hours chunks long breakdowns correctly."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project

        # Create a large number of users to force chunking
        mock_hours = {
            f"User {i}": {
                "hours": 10.0,
                "entries": 5,
                "duration_seconds": 36000,
                "billed_amount": 500.0,
            }
            for i in range(50)
        }
        kimai_cog.api.get_project_hours_by_user.return_value = mock_hours

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project"
        )

        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]

        # Should have multiple fields due to chunking
        assert len(embed.fields) > 1

    @pytest.mark.asyncio
    async def test_list_projects_with_customer_info(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project listing includes customer information."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_projects = [
            {
                "id": 1,
                "name": "Project 1",
                "customer": {"name": "Customer A"},
                "visible": True,
            },
            {"id": 2, "name": "Project 2", "customer": {}, "visible": True},
            {"id": 3, "name": "Project 3", "visible": True},
        ]
        kimai_cog.api.get_projects.return_value = mock_projects

        await kimai_cog.list_projects.callback(kimai_cog, mock_interaction)

        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]

        # Check that customer name is included for project 1
        field_value = embed.fields[0].value
        assert "Customer A" in field_value

    @pytest.mark.asyncio
    async def test_list_projects_shows_hidden_status(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test project listing shows hidden status."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_projects = [
            {"id": 1, "name": "Visible Project", "visible": True},
            {"id": 2, "name": "Hidden Project", "visible": False},
        ]
        kimai_cog.api.get_projects.return_value = mock_projects

        await kimai_cog.list_projects.callback(kimai_cog, mock_interaction)

        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        field_value = embed.fields[0].value

        # Hidden project should have [Hidden] marker
        assert "[Hidden]" in field_value

    @pytest.mark.asyncio
    async def test_project_hours_sorts_by_hours_descending(
        self, kimai_cog, mock_interaction, mock_steering_role
    ):
        """Test that project hours are sorted by hours in descending order."""
        mock_interaction.user.roles = [mock_steering_role]

        mock_project = {"id": 5, "name": "Test Project"}
        kimai_cog.api.get_project_by_name.return_value = mock_project

        mock_hours = {
            "User A": {
                "hours": 5.0,
                "entries": 2,
                "duration_seconds": 18000,
                "billed_amount": 250.0,
            },
            "User B": {
                "hours": 15.0,
                "entries": 3,
                "duration_seconds": 54000,
                "billed_amount": 750.0,
            },
            "User C": {
                "hours": 10.0,
                "entries": 1,
                "duration_seconds": 36000,
                "billed_amount": 500.0,
            },
        }
        kimai_cog.api.get_project_hours_by_user.return_value = mock_hours

        await kimai_cog.project_hours.callback(
            kimai_cog, mock_interaction, "Test Project"
        )

        call_args = mock_interaction.followup.send.call_args
        embed = call_args[1]["embed"]
        breakdown_field = embed.fields[2].value  # Third field is the breakdown

        # User B (15h) should appear before User C (10h) before User A (5h)
        b_pos = breakdown_field.index("User B")
        c_pos = breakdown_field.index("User C")
        a_pos = breakdown_field.index("User A")
        assert b_pos < c_pos < a_pos
