"""
Unit tests for Kimai API client functionality.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from five08.clients.kimai import KimaiAPI, KimaiAPIError


class TestKimaiAPI:
    """Unit tests for KimaiAPI class."""

    @pytest.fixture
    def kimai_api(self):
        """Create a KimaiAPI instance for testing."""
        return KimaiAPI("https://kimai.test.com", "test_token")

    def test_initialization(self, kimai_api):
        """Test Kimai API client initialization."""
        assert kimai_api.base_url == "https://kimai.test.com"
        assert kimai_api.api_token == "test_token"
        assert kimai_api.status_code is None

    def test_initialization_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base URL."""
        api = KimaiAPI("https://kimai.test.com/", "test_token")
        assert api.base_url == "https://kimai.test.com"

    def test_get_headers(self, kimai_api):
        """Test that headers include authentication token."""
        headers = kimai_api._get_headers()
        assert headers["Authorization"] == "Bearer test_token"
        assert headers["Content-Type"] == "application/json"

    def test_normalize_url(self, kimai_api):
        """Test URL normalization."""
        # Test with leading slash
        url = kimai_api._normalize_url("/projects")
        assert url == "https://kimai.test.com/api/projects"

        # Test without leading slash
        url = kimai_api._normalize_url("projects")
        assert url == "https://kimai.test.com/api/projects"

    def test_request_get_success(self, kimai_api):
        """Test successful GET request."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"id": 1, "name": "Test Project"}]
        mock_response.content = b'[{"id": 1, "name": "Test Project"}]'

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            result = kimai_api._request("GET", "projects")

            assert result == [{"id": 1, "name": "Test Project"}]
            assert kimai_api.status_code == 200
            mock_request.assert_called_once()

    def test_request_post_success(self, kimai_api):
        """Test successful POST request."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 1, "name": "New Project"}
        mock_response.content = b'{"id": 1, "name": "New Project"}'

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            result = kimai_api._request("POST", "projects", {"name": "New Project"})

            assert result == {"id": 1, "name": "New Project"}
            assert kimai_api.status_code == 201
            mock_request.assert_called_once()

    def test_request_empty_response(self, kimai_api):
        """Test request with empty response content."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b""

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            result = kimai_api._request("GET", "projects")
            assert result == []

    def test_request_404_error(self, kimai_api):
        """Test request with 404 error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.json.side_effect = ValueError("No JSON")

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            with pytest.raises(KimaiAPIError) as exc_info:
                kimai_api._request("GET", "projects/999")

            assert "API request failed with status 404" in str(exc_info.value)
            assert kimai_api.status_code == 404

    def test_request_error_with_json_message(self, kimai_api):
        """Test request error with JSON error message."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"message": "Invalid request parameters"}

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            with pytest.raises(KimaiAPIError) as exc_info:
                kimai_api._request("GET", "projects")

            assert "Invalid request parameters" in str(exc_info.value)

    def test_request_connection_error(self, kimai_api):
        """Test request with connection error."""
        import requests

        with patch.object(
            kimai_api._session,
            "request",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            with pytest.raises(KimaiAPIError) as exc_info:
                kimai_api._request("GET", "projects")

            assert "HTTP request failed" in str(exc_info.value)

    def test_request_invalid_json(self, kimai_api):
        """Test request with 200 OK but invalid JSON response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"not-json"
        mock_response.text = "not-json"
        mock_response.json.side_effect = ValueError("No JSON object could be decoded")

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            with pytest.raises(KimaiAPIError) as exc_info:
                kimai_api._request("GET", "projects")

            assert kimai_api.status_code == 200
            assert "Failed to decode JSON response" in str(exc_info.value)
            assert "not-json" in str(exc_info.value)

    def test_get_projects(self, kimai_api):
        """Test get_projects method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1"}]'
        mock_response.json.return_value = [{"id": 1, "name": "Project 1"}]

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            projects = kimai_api.get_projects()

            assert len(projects) == 1
            assert projects[0]["name"] == "Project 1"
            mock_request.assert_called_once()

    def test_get_project_by_name_found(self, kimai_api):
        """Test get_project_by_name when project is found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Test Project"}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Test Project"},
            {"id": 2, "name": "Another Project"},
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            project = kimai_api.get_project_by_name("Test Project")

            assert project is not None
            assert project["id"] == 1
            assert project["name"] == "Test Project"

    def test_get_project_by_name_case_insensitive(self, kimai_api):
        """Test get_project_by_name is case-insensitive."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Test Project"}]'
        mock_response.json.return_value = [{"id": 1, "name": "Test Project"}]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            project = kimai_api.get_project_by_name("test project")

            assert project is not None
            assert project["id"] == 1

    def test_get_project_by_name_not_found(self, kimai_api):
        """Test get_project_by_name when project is not found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Test Project"}]'
        mock_response.json.return_value = [{"id": 1, "name": "Test Project"}]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            project = kimai_api.get_project_by_name("Nonexistent Project")

            assert project is None

    def test_get_timesheets_no_filters(self, kimai_api):
        """Test get_timesheets without filters defaults to user='all'."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "duration": 3600}]'
        mock_response.json.return_value = [{"id": 1, "duration": 3600}]

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            timesheets = kimai_api.get_timesheets()

            assert len(timesheets) == 1
            mock_request.assert_called_once()
            # Verify default user='all' is passed
            call_args = mock_request.call_args
            assert call_args[1]["params"]["user"] == "all"

    def test_get_timesheets_with_project_filter(self, kimai_api):
        """Test get_timesheets with project filter."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "project": 5}]'
        mock_response.json.return_value = [{"id": 1, "project": 5}]

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            kimai_api.get_timesheets(project_id=5)

            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["params"]["project"] == 5

    def test_get_timesheets_with_date_filters(self, kimai_api):
        """Test get_timesheets with date filters."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"[]"
        mock_response.json.return_value = []

        begin = datetime(2024, 1, 1, 0, 0, 0)
        end = datetime(2024, 1, 31, 23, 59, 59)

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            kimai_api.get_timesheets(begin=begin, end=end)

            call_args = mock_request.call_args
            assert call_args[1]["params"]["begin"] == "2024-01-01T00:00:00"
            assert call_args[1]["params"]["end"] == "2024-01-31T23:59:59"

    def test_get_timesheets_with_user_filter(self, kimai_api):
        """Test get_timesheets with user filter."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"[]"
        mock_response.json.return_value = []

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            kimai_api.get_timesheets(user=10)

            call_args = mock_request.call_args
            assert call_args[1]["params"]["user"] == 10

    def test_get_timesheets_with_activity_filter(self, kimai_api):
        """Test get_timesheets with activity filters."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b"[]"
        mock_response.json.return_value = []

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            kimai_api.get_timesheets(activities=[1, 2, 3])

            call_args = mock_request.call_args
            assert call_args[1]["params"]["activities[]"] == [1, 2, 3]

    def test_get_users(self, kimai_api):
        """Test get_users method."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "username": "john"}]'
        mock_response.json.return_value = [{"id": 1, "username": "john"}]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            users = kimai_api.get_users()

            assert len(users) == 1
            assert users[0]["username"] == "john"

    def test_get_project_hours_by_user(self, kimai_api):
        """Test get_project_hours_by_user method."""
        # Mock get_activities
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [
                {"id": 10, "name": "Development"},
            ]

            # Mock get_timesheets to return timesheet data
            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {
                        "user": 1,
                        "duration": 3600,
                        "rate": 100,
                        "activity": 10,
                    },  # 1 hour, billed $100
                    {
                        "user": 1,
                        "duration": 7200,
                        "rate": 200,
                        "activity": 10,
                    },  # 2 hours, billed $200
                    {
                        "user": 2,
                        "duration": 1800,
                        "rate": 75,
                        "activity": 10,
                    },  # 0.5 hours, billed $75
                ]

                # Mock get_user_by_id to return user data
                def mock_get_user_by_id(user_id):
                    if user_id == 1:
                        return {"id": 1, "alias": "John Doe"}
                    elif user_id == 2:
                        return {"id": 2, "username": "jane"}
                    return None

                with patch.object(
                    kimai_api, "get_user_by_id", side_effect=mock_get_user_by_id
                ):
                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    assert len(result) == 2
                    assert "John Doe" in result
                    assert "jane" in result
                    assert result["John Doe"]["hours"] == 3.0  # 3 hours total
                    assert result["John Doe"]["entries"] == 2
                    assert result["John Doe"]["billed_amount"] == 300.0  # $100 + $200
                    assert result["jane"]["hours"] == 0.5
                    assert result["jane"]["entries"] == 1
                    assert result["jane"]["billed_amount"] == 75.0  # $75

    def test_get_project_hours_by_user_with_dates(self, kimai_api):
        """Test get_project_hours_by_user with date filters."""
        begin = datetime(2024, 1, 1)
        end = datetime(2024, 1, 31)

        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = []

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = []

                result = kimai_api.get_project_hours_by_user(
                    project_id=5, begin=begin, end=end
                )

                assert len(result) == 0
                # Verify that timesheets was called with date parameters
                mock_timesheets.assert_called_once_with(
                    project_id=5, begin=begin, end=end, activities=None
                )

    def test_get_project_hours_by_user_uses_alias(self, kimai_api):
        """Test that get_project_hours_by_user prefers alias over username."""
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [{"id": 10, "name": "Development"}]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {"user": 1, "duration": 3600, "rate": 50, "activity": 10}
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.return_value = {
                        "id": 1,
                        "alias": "John Doe",
                        "username": "john",
                    }

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    # Should use alias, not username
                    assert "John Doe" in result
                    assert "john" not in result
                    assert result["John Doe"]["billed_amount"] == 50.0

    def test_get_project_hours_by_user_fallback_to_username(self, kimai_api):
        """Test that get_project_hours_by_user falls back to username if no alias."""
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [{"id": 10, "name": "Development"}]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {"user": 1, "duration": 3600, "rate": 50, "activity": 10}
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.return_value = {"id": 1, "username": "john"}

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    # Should use username since no alias
                    assert "john" in result
                    assert result["john"]["billed_amount"] == 50.0

    def test_get_project_hours_by_user_flags_zero_rate_entries(self, kimai_api):
        """Ensure zero-rate timesheets are counted for warning purposes."""
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [{"id": 10, "name": "Development"}]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {"user": 1, "duration": 3600, "rate": 0, "activity": 10},
                    {"user": 1, "duration": 3600, "rate": 0.0, "activity": 10},
                    {"user": 1, "duration": 3600, "rate": 100, "activity": 10},
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.return_value = {"id": 1, "username": "john"}

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    assert result["john"]["entries"] == 3
                    assert result["john"]["zero_rate_entries"] == 2
                    assert result["john"]["billed_amount"] == 100.0

    def test_get_project_hours_by_user_unknown_user(self, kimai_api):
        """Test get_project_hours_by_user with unknown user ID."""
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [{"id": 10, "name": "Development"}]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {"user": 999, "duration": 3600, "rate": 75, "activity": 10}
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.return_value = None  # User not found

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    # Should create entry with "User 999"
                    assert "User 999" in result
                    assert result["User 999"]["hours"] == 1.0
                    assert result["User 999"]["billed_amount"] == 75.0

    def test_get_project_hours_by_user_skips_null_user(self, kimai_api):
        """Test that entries with null user are skipped."""
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [{"id": 10, "name": "Development"}]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                mock_timesheets.return_value = [
                    {"user": None, "duration": 3600, "rate": 100, "activity": 10},
                    {"user": 1, "duration": 1800, "rate": 50, "activity": 10},
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.return_value = {"id": 1, "username": "john"}

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    # Should only have one user (null user is skipped)
                    assert len(result) == 1
                    assert "john" in result
                    assert result["john"]["billed_amount"] == 50.0  # $50 total

    def test_get_project_hours_by_user_filters_retainer(self, kimai_api):
        """Test that activities with 'Retainer' in the name are filtered out."""
        # Mock activities with IDs and names
        with patch.object(kimai_api, "get_activities") as mock_activities:
            mock_activities.return_value = [
                {"id": 10, "name": "Development"},
                {"id": 20, "name": "Engineering Retainer"},
                {"id": 30, "name": "Design Retainer"},
                {"id": 40, "name": "Support retainer"},
                {"id": 50, "name": "Code Review"},
            ]

            with patch.object(kimai_api, "get_timesheets") as mock_timesheets:
                # Timesheets now reference activity IDs, not objects
                mock_timesheets.return_value = [
                    {
                        "user": 1,
                        "duration": 3600,
                        "rate": 100,
                        "activity": 10,  # Development
                    },
                    {
                        "user": 1,
                        "duration": 7200,
                        "rate": 200,
                        "activity": 20,  # Engineering Retainer - should be filtered
                    },
                    {
                        "user": 1,
                        "duration": 1800,
                        "rate": 90,
                        "activity": 30,  # Design Retainer - should be filtered
                    },
                    {
                        "user": 1,
                        "duration": 900,
                        "rate": 45,
                        "activity": 40,  # Support retainer - should be filtered
                    },
                    {
                        "user": 2,
                        "duration": 1800,
                        "rate": 75,
                        "activity": 50,  # Code Review
                    },
                ]

                with patch.object(kimai_api, "get_user_by_id") as mock_get_user:
                    mock_get_user.side_effect = lambda uid: (
                        {"id": 1, "alias": "John Doe"}
                        if uid == 1
                        else {"id": 2, "username": "jane"}
                    )

                    result = kimai_api.get_project_hours_by_user(project_id=5)

                    mock_timesheets.assert_called_once_with(
                        project_id=5, begin=None, end=None, activities=[10, 50]
                    )
                    # Should only count non-retainer entries
                    assert result["John Doe"]["hours"] == 1.0  # Only Development entry
                    assert result["John Doe"]["entries"] == 1  # Only 1 entry
                    assert result["John Doe"]["billed_amount"] == 100.0  # Only $100
                    assert result["jane"]["hours"] == 0.5
                    assert result["jane"]["billed_amount"] == 75.0

    def test_get_user_by_id_found(self, kimai_api):
        """Test get_user_by_id when user is found in cache."""
        # Mock get_users to populate cache
        with patch.object(kimai_api, "get_users") as mock_get_users:
            mock_get_users.return_value = [
                {"id": 1, "username": "john", "alias": "John Doe"},
                {"id": 2, "username": "jane", "alias": "Jane Doe"},
            ]

            user = kimai_api.get_user_by_id(1)

            assert user is not None
            assert user["id"] == 1
            assert user["username"] == "john"
            # Verify cache was populated
            mock_get_users.assert_called_once()

    def test_get_user_by_id_not_found(self, kimai_api):
        """Test get_user_by_id when user is not found in cache or API."""
        # Mock get_users to populate cache (without the user we're looking for)
        with patch.object(kimai_api, "get_users") as mock_get_users:
            mock_get_users.return_value = [
                {"id": 1, "username": "john", "alias": "John Doe"},
            ]

            # Mock direct API call to also return failure
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.text = "Not Found"
            mock_response.json.side_effect = ValueError("No JSON")

            with patch.object(
                kimai_api._session, "request", return_value=mock_response
            ):
                user = kimai_api.get_user_by_id(999)

                assert user is None

    def test_get_user_by_username_found(self, kimai_api):
        """Test get_user_by_username when user is found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "username": "john", "alias": "John Doe"}]'
        mock_response.json.return_value = [
            {"id": 1, "username": "john", "alias": "John Doe"},
        ]

        with patch.object(
            kimai_api._session, "request", return_value=mock_response
        ) as mock_request:
            user = kimai_api.get_user_by_username("john")

            assert user is not None
            assert user["id"] == 1
            assert user["username"] == "john"
            # Verify term parameter was used
            mock_request.assert_called_once()
            assert mock_request.call_args[1]["params"]["term"] == "john"

    def test_get_user_by_username_case_insensitive(self, kimai_api):
        """Test get_user_by_username is case-insensitive."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "username": "john", "alias": "John Doe"}]'
        mock_response.json.return_value = [
            {"id": 1, "username": "john", "alias": "John Doe"}
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            # Uppercase username should still match lowercase username
            user = kimai_api.get_user_by_username("JOHN")

            assert user is not None
            assert user["id"] == 1

    def test_get_user_by_username_not_found(self, kimai_api):
        """Test get_user_by_username when user is not found."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "username": "john", "alias": "John Doe"}]'
        mock_response.json.return_value = [
            {"id": 1, "username": "john", "alias": "John Doe"}
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            # Search for different username
            user = kimai_api.get_user_by_username("nonexistent")

            assert user is None

    def test_is_project_team_lead_true(self, kimai_api):
        """Test is_project_team_lead when user is team lead."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1", "teamLead": 5}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1", "teamLead": 5},
            {"id": 2, "name": "Project 2", "teamLead": 3},
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            is_team_lead = kimai_api.is_project_team_lead(project_id=1, user_id=5)

            assert is_team_lead is True

    def test_is_project_team_lead_false(self, kimai_api):
        """Test is_project_team_lead when user is not team lead."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1", "teamLead": 5}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1", "teamLead": 5}
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            is_team_lead = kimai_api.is_project_team_lead(project_id=1, user_id=3)

            assert is_team_lead is False

    def test_is_project_team_lead_project_not_found(self, kimai_api):
        """Test is_project_team_lead when project doesn't exist."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1", "teamLead": 5}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1", "teamLead": 5}
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            is_team_lead = kimai_api.is_project_team_lead(project_id=999, user_id=5)

            assert is_team_lead is False

    def test_is_project_team_lead_no_team_lead(self, kimai_api):
        """Test is_project_team_lead when project has no team lead."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1"}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1"}  # No teamLead field
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            is_team_lead = kimai_api.is_project_team_lead(project_id=1, user_id=5)

            assert is_team_lead is False

    def test_get_projects_by_team_lead(self, kimai_api):
        """Test get_projects_by_team_lead."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1", "teamLead": 5}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1", "teamLead": 5},
            {"id": 2, "name": "Project 2", "teamLead": 3},
            {"id": 3, "name": "Project 3", "teamLead": 5},
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            projects = kimai_api.get_projects_by_team_lead(user_id=5)

            assert len(projects) == 2
            assert projects[0]["id"] == 1
            assert projects[1]["id"] == 3

    def test_get_projects_by_team_lead_none_found(self, kimai_api):
        """Test get_projects_by_team_lead with no matching projects."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": 1, "name": "Project 1", "teamLead": 3}]'
        mock_response.json.return_value = [
            {"id": 1, "name": "Project 1", "teamLead": 3}
        ]

        with patch.object(kimai_api._session, "request", return_value=mock_response):
            projects = kimai_api.get_projects_by_team_lead(user_id=5)

            assert len(projects) == 0
