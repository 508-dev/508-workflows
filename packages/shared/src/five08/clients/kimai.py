"""
Kimai API client for time tracking integration.

This module provides a client for interacting with the Kimai time tracking API.
"""

import requests
from typing import Any
from datetime import datetime


class KimaiAPIError(Exception):
    """An exception class for Kimai API errors"""


class KimaiAPI:
    """Client for interacting with the Kimai time tracking API."""

    def __init__(self, base_url: str, api_token: str, timeout: int = 30) -> None:
        """
        Initialize the Kimai API client.

        Args:
            base_url: Base URL of the Kimai instance (e.g., https://kimai.example.com)
            api_token: API token for authentication
            timeout: Request timeout in seconds (default: 30)
        """
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.status_code: int | None = None
        self._session = requests.Session()
        self._session.headers.update(self._get_headers())
        self._user_cache: dict[int, dict[str, Any]] | None = None

    def __del__(self) -> None:
        """Cleanup session on deletion."""
        try:
            if hasattr(self, "_session"):
                self.close()
        except Exception:
            # Suppress exceptions during interpreter shutdown
            pass

    def close(self) -> None:
        """Close the HTTP session."""
        if hasattr(self, "_session") and self._session:
            self._session.close()

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication."""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _normalize_url(self, endpoint: str) -> str:
        """Normalize API endpoint URL."""
        endpoint = endpoint.lstrip("/")
        return f"{self.base_url}/api/{endpoint}"

    def _request(
        self, method: str, endpoint: str, params: dict[str, Any] | None = None
    ) -> Any:
        """
        Make an HTTP request to the Kimai API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., 'timesheets')
            params: Query parameters or JSON body

        Returns:
            API response data (list or dict)

        Raises:
            KimaiAPIError: If the request fails
        """
        if params is None:
            params = {}

        url = self._normalize_url(endpoint)
        # Normalize method to uppercase for case-insensitive comparison
        method = method.upper()

        try:
            if method in ["POST", "PATCH", "PUT"]:
                response = self._session.request(
                    method, url, json=params, timeout=self.timeout
                )
            else:
                response = self._session.request(
                    method, url, params=params, timeout=self.timeout
                )

            self.status_code = response.status_code

            if self.status_code not in [200, 201]:
                error_msg = f"API request failed with status {self.status_code}"
                try:
                    error_data = response.json()
                    if "message" in error_data:
                        error_msg += f": {error_data['message']}"
                except Exception:
                    error_msg += f": {response.text}"
                raise KimaiAPIError(error_msg)

            if not response.content:
                return []

            # Wrap JSON decoding in try/except to catch invalid JSON
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as e:
                raise KimaiAPIError(
                    f"Failed to decode JSON response: {str(e)}. Response body: {response.text}"
                )

        except requests.Timeout:
            raise KimaiAPIError(f"Request timed out after {self.timeout} seconds")
        except requests.RequestException as e:
            raise KimaiAPIError(f"HTTP request failed: {str(e)}")

    def get_projects(self) -> list[dict[str, Any]]:
        """
        Get all projects from Kimai.

        Returns:
            List of project dictionaries
        """
        return self._request("GET", "projects")  # type: ignore[no-any-return]

    def get_activities(
        self,
        project_id: int | None = None,
        globals_only: bool = False,
        order: str = "ASC",
        order_by: str = "name",
    ) -> list[dict[str, Any]]:
        """
        Get activities from Kimai.

        Args:
            project_id: Filter activities by project ID
            globals_only: If True, fetch only global activities
            order: Sort order - "ASC" or "DESC" (default: "ASC")
            order_by: Field to sort by - "id", "name", or "project" (default: "name")

        Returns:
            List of activity dictionaries
        """
        params: dict[str, Any] = {}

        if globals_only:
            params["globals"] = "1"

        if order:
            params["order"] = order

        if order_by:
            params["orderBy"] = order_by

        if project_id is not None:
            params["project"] = project_id

        return self._request("GET", "activities", params)  # type: ignore[no-any-return]

    def get_project_by_name(self, project_name: str) -> dict[str, Any] | None:
        """
        Find a project by name (case-insensitive search).

        Args:
            project_name: Name of the project to search for

        Returns:
            Project dictionary if found, None otherwise
        """
        projects = self.get_projects()
        project_name_lower = project_name.lower()

        for project in projects:
            if project.get("name", "").lower() == project_name_lower:
                return project

        return None

    def get_timesheets(
        self,
        project_id: int | None = None,
        begin: datetime | None = None,
        end: datetime | None = None,
        user: int | str = "all",
        activities: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get timesheet entries with optional filters.

        Args:
            project_id: Filter by project ID
            begin: Start date/time for filtering
            end: End date/time for filtering
            user: Filter by user ID (int) or 'all' for all users (default: 'all', requires 'view_other_timesheet' permission)
            activities: Optional list of activity IDs to filter results

        Returns:
            List of timesheet entry dictionaries
        """
        params: dict[str, Any] = {}

        if project_id is not None:
            params["project"] = project_id

        if begin is not None:
            params["begin"] = begin.strftime("%Y-%m-%dT%H:%M:%S")

        if end is not None:
            params["end"] = end.strftime("%Y-%m-%dT%H:%M:%S")

        if activities is not None and len(activities) > 0:
            params["activities[]"] = activities

        params["user"] = user
        # Set to a large size to get all timesheets
        params["size"] = 10000

        return self._request("GET", "timesheets", params)  # type: ignore[no-any-return]

    def get_users(self, term: str | None = None) -> list[dict[str, Any]]:
        """
        Get users from Kimai.

        Args:
            term: Optional search term to filter users

        Returns:
            List of user dictionaries
        """
        params = {}
        if term:
            params["term"] = term

        return self._request("GET", "users", params)  # type: ignore[no-any-return]

    def _populate_user_cache(self) -> None:
        """
        Populate the user cache by fetching all users.
        This is called lazily on first use.
        """
        import logging

        logger = logging.getLogger(__name__)

        try:
            users = self.get_users()
            if isinstance(users, list):
                self._user_cache = {
                    user["id"]: user
                    for user in users
                    if isinstance(user, dict) and user.get("id") is not None
                }
                logger.debug(f"Populated user cache with {len(self._user_cache)} users")
            else:
                logger.warning("get_users() did not return a list")
                self._user_cache = {}
        except (KimaiAPIError, KeyError, TypeError) as e:
            logger.warning(f"Failed to populate user cache: {e}")
            self._user_cache = {}

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        """
        Get a user by their ID.

        Args:
            user_id: ID of the user

        Returns:
            User dictionary if found, None otherwise
        """
        import logging

        logger = logging.getLogger(__name__)

        # Populate cache on first use
        if self._user_cache is None:
            self._populate_user_cache()

        # Check cache first
        if self._user_cache and user_id in self._user_cache:
            return self._user_cache[user_id]

        # Try direct endpoint if not in cache
        try:
            user: dict[str, Any] = self._request("GET", f"users/{user_id}")
            # Add to cache
            if self._user_cache is not None and user:
                self._user_cache[user_id] = user
            return user
        except KimaiAPIError as e:
            logger.warning(f"Failed to fetch user {user_id} from Kimai API: {e}")
            return None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """
        Find a user by username.

        Args:
            username: Username to search for (e.g., "michael")

        Returns:
            User dictionary if found, None otherwise
        """
        # Search for users with this username term
        users = self.get_users(term=username)

        # Match exact username (case-insensitive)
        username_lower = username.lower()
        for user in users:
            if user.get("username", "").lower() == username_lower:
                return user

        return None

    def is_project_team_lead(self, project_id: int, user_id: int) -> bool:
        """
        Check if a user is the team lead of a project.

        Args:
            project_id: ID of the project
            user_id: ID of the user

        Returns:
            True if the user is the team lead, False otherwise
        """
        projects = self.get_projects()

        for project in projects:
            if project.get("id") == project_id:
                team_lead_id = project.get("teamLead")
                return team_lead_id == user_id

        return False

    def get_projects_by_team_lead(self, user_id: int) -> list[dict[str, Any]]:
        """
        Get all projects where a user is the team lead.

        Args:
            user_id: ID of the user

        Returns:
            List of project dictionaries where the user is team lead
        """
        projects = self.get_projects()
        team_lead_projects = []

        for project in projects:
            if project.get("teamLead") == user_id:
                team_lead_projects.append(project)

        return team_lead_projects

    def get_project_hours_by_user(
        self,
        project_id: int,
        begin: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Get total hours logged for a project, broken down by user.

        Args:
            project_id: ID of the project
            begin: Start date/time for filtering
            end: End date/time for filtering

        Returns:
            Dictionary mapping user names to their hours and details:
            {
                "User Name": {
                    "hours": 12.5,
                    "duration_seconds": 45000,
                    "entries": 5,
                    "billed_amount": 250.0
                }
            }
        """
        # Fetch all activities to build a mapping of activity ID to name
        activities = self.get_activities()
        activity_map: dict[int, str] = {
            activity["id"]: activity.get("name", "")
            for activity in activities
            if "id" in activity
        }

        non_retainer_activity_ids = [
            activity_id
            for activity_id, name in activity_map.items()
            if "retainer" not in name.lower()
        ]

        timesheets = self.get_timesheets(
            project_id=project_id,
            begin=begin,
            end=end,
            activities=non_retainer_activity_ids if non_retainer_activity_ids else None,
        )

        def _is_retainer_activity(activity_id: Any) -> bool:
            """Return True if the activity id maps to a retainer activity name."""
            if not isinstance(activity_id, int):
                return False
            return "retainer" in activity_map.get(activity_id, "").lower()

        # Filter out activities with "Retainer" in the name
        timesheets = [
            entry
            for entry in timesheets
            if not _is_retainer_activity(entry.get("activity"))
        ]

        # Get all unique user IDs from timesheets
        user_ids: set[int] = {
            entry["user"] for entry in timesheets if entry.get("user") is not None
        }

        # Fetch all users at once to build a user map
        # This is more efficient than individual lookups if the direct endpoint fails
        user_map: dict[int, str] = {}
        failed_user_ids: set[int] = set()

        # Try to fetch each user individually first
        for uid in user_ids:
            user_data = self.get_user_by_id(uid)
            if user_data:
                user_map[uid] = user_data.get(
                    "alias", user_data.get("username", f"User {uid}")
                )
            else:
                failed_user_ids.add(uid)

        # Aggregate hours by user
        user_hours: dict[str, dict[str, Any]] = {}

        for entry in timesheets:
            user_id_raw = entry.get("user")
            duration = entry.get("duration", 0)  # Duration in seconds
            rate_raw = entry.get("rate", 0)  # Billed amount for this entry

            if user_id_raw is None:
                continue

            # Type narrowing: user_id is guaranteed to be an int here
            from typing import cast

            user_id = cast(int, user_id_raw)

            # Get user name from map or use fallback
            user_name = user_map.get(user_id, f"User {user_id}")

            if user_name not in user_hours:
                user_hours[user_name] = {
                    "hours": 0.0,
                    "duration_seconds": 0,
                    "entries": 0,
                    "billed_amount": 0.0,
                    "zero_rate_entries": 0,
                }

            rate = float(rate_raw) if rate_raw is not None else 0.0
            user_hours[user_name]["duration_seconds"] += duration
            user_hours[user_name]["hours"] = (
                user_hours[user_name]["duration_seconds"] / 3600
            )
            user_hours[user_name]["entries"] += 1
            user_hours[user_name]["billed_amount"] += rate
            if rate == 0:
                user_hours[user_name]["zero_rate_entries"] += 1

        return user_hours
