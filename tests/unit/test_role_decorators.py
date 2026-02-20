"""
Unit tests for role decorator functionality.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from five08.discord_bot.utils.role_decorators import (
    require_role,
    require_roles,
    check_user_roles,
    get_missing_roles,
    check_user_roles_with_hierarchy,
    get_user_hierarchy_level,
)


class TestRoleDecorators:
    """Tests for role-based access control decorators."""

    @pytest.fixture
    def mock_interaction(self):
        """Create a mock Discord interaction."""
        interaction = AsyncMock()
        interaction.response = AsyncMock()
        interaction.response.send_message = AsyncMock()
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

    @pytest.fixture
    def mock_user_role(self):
        """Create a mock User role."""
        role = Mock()
        role.name = "User"
        return role

    @pytest.mark.asyncio
    async def test_require_role_with_correct_role(
        self, mock_interaction, mock_member_role
    ):
        """Test require_role decorator allows access with correct role."""
        mock_interaction.user.roles = [mock_member_role]

        @require_role("Member")
        async def test_command(self, interaction):
            return "success"

        # Create a mock self object
        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result == "success"
        mock_interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_role_without_correct_role(
        self, mock_interaction, mock_user_role
    ):
        """Test require_role decorator denies access without correct role."""
        mock_interaction.user.roles = [mock_user_role]

        @require_role("Member")
        async def test_command(self, interaction):
            return "success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result is None  # Function should not complete
        mock_interaction.response.send_message.assert_called_once_with(
            "❌ You must have one of these roles to use this command: Member",
            ephemeral=True,
        )

    @pytest.mark.asyncio
    async def test_require_roles_with_one_correct_role(
        self, mock_interaction, mock_member_role, mock_user_role
    ):
        """Test require_roles decorator allows access with one of multiple required roles."""
        mock_interaction.user.roles = [mock_member_role, mock_user_role]

        @require_roles("Member", "Admin")
        async def test_command(self, interaction):
            return "success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result == "success"
        mock_interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_roles_without_any_correct_role(
        self, mock_interaction, mock_user_role
    ):
        """Test require_roles decorator denies access without any required roles."""
        mock_interaction.user.roles = [mock_user_role]

        @require_roles("Member", "Admin")
        async def test_command(self, interaction):
            return "success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result is None
        mock_interaction.response.send_message.assert_called_once_with(
            "❌ You must have one of these roles to use this command: Member, Admin",
            ephemeral=True,
        )

    @pytest.mark.asyncio
    async def test_require_role_with_admin_role(
        self, mock_interaction, mock_admin_role
    ):
        """Test require_role decorator works with Admin role."""
        mock_interaction.user.roles = [mock_admin_role]

        @require_role("Admin")
        async def test_command(self, interaction):
            return "admin_success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result == "admin_success"
        mock_interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""

        @require_role("Member")
        async def test_command(self, interaction):
            """Test command docstring."""
            return "success"

        assert test_command.__name__ == "test_command"
        assert test_command.__doc__ == "Test command docstring."

    @pytest.mark.asyncio
    async def test_decorator_with_args_and_kwargs(
        self, mock_interaction, mock_member_role
    ):
        """Test decorator works with functions that have additional args and kwargs."""
        mock_interaction.user.roles = [mock_member_role]

        @require_role("Member")
        async def test_command(self, interaction, arg1, arg2, kwarg1=None):
            return f"success-{arg1}-{arg2}-{kwarg1}"

        mock_self = Mock()

        result = await test_command(
            mock_self, mock_interaction, "test1", "test2", kwarg1="test3"
        )

        assert result == "success-test1-test2-test3"


class TestRoleHelpers:
    """Tests for role helper functions."""

    @pytest.fixture
    def mock_roles(self):
        """Create mock Discord roles."""
        member_role = Mock()
        member_role.name = "Member"

        admin_role = Mock()
        admin_role.name = "Admin"

        user_role = Mock()
        user_role.name = "User"

        return [member_role, admin_role, user_role]

    def test_check_user_roles_with_required_role(self, mock_roles):
        """Test check_user_roles returns True when user has required role."""
        result = check_user_roles(mock_roles, ["Member"])
        assert result is True

    def test_check_user_roles_without_required_role(self, mock_roles):
        """Test check_user_roles returns False when user doesn't have required role."""
        result = check_user_roles(mock_roles, ["Moderator"])
        assert result is False

    def test_check_user_roles_with_multiple_required_roles(self, mock_roles):
        """Test check_user_roles with multiple required roles."""
        result = check_user_roles(mock_roles, ["Member", "Moderator"])
        assert result is True  # User has Member role

    def test_check_user_roles_empty_user_roles(self):
        """Test check_user_roles with no user roles."""
        result = check_user_roles([], ["Member"])
        assert result is False

    def test_check_user_roles_empty_required_roles(self, mock_roles):
        """Test check_user_roles with no required roles."""
        result = check_user_roles(mock_roles, [])
        assert result is False

    def test_get_missing_roles_with_some_missing(self, mock_roles):
        """Test get_missing_roles returns roles user doesn't have."""
        missing = get_missing_roles(mock_roles, ["Member", "Moderator", "Owner"])
        assert set(missing) == {"Moderator", "Owner"}

    def test_get_missing_roles_with_all_roles(self, mock_roles):
        """Test get_missing_roles returns empty list when user has all roles."""
        missing = get_missing_roles(mock_roles, ["Member", "Admin"])
        assert missing == []

    def test_get_missing_roles_with_no_roles(self):
        """Test get_missing_roles when user has no roles."""
        missing = get_missing_roles([], ["Member", "Admin"])
        assert set(missing) == {"Member", "Admin"}

    def test_get_missing_roles_empty_required(self, mock_roles):
        """Test get_missing_roles with no required roles."""
        missing = get_missing_roles(mock_roles, [])
        assert missing == []


class TestRoleDecoratorsIntegration:
    """Integration tests for role decorators with actual Discord.py objects."""

    def test_role_name_matching_case_sensitive(self):
        """Test that role name matching is case sensitive."""
        # Create mock roles with different cases
        member_role = Mock()
        member_role.name = "member"  # lowercase

        roles = [member_role]

        # Should not match "Member" (uppercase M)
        result = check_user_roles(roles, ["Member"])
        assert result is False

        # Should match "member" (lowercase)
        result = check_user_roles(roles, ["member"])
        assert result is True

    def test_multiple_roles_any_match(self):
        """Test that having any of the required roles grants access."""
        admin_role = Mock()
        admin_role.name = "Admin"

        user_role = Mock()
        user_role.name = "User"

        roles = [admin_role, user_role]

        # Should match because user has Admin role
        result = check_user_roles(roles, ["Admin", "Moderator", "Owner"])
        assert result is True


class TestHierarchicalRoles:
    """Tests for hierarchical role checking functionality."""

    @pytest.fixture
    def mock_roles_with_hierarchy(self):
        """Create mock roles for hierarchy testing."""
        member_role = Mock()
        member_role.name = "Member"

        steering_committee_role = Mock()
        steering_committee_role.name = "Steering Committee"

        admin_role = Mock()
        admin_role.name = "Admin"

        owner_role = Mock()
        owner_role.name = "Owner"

        user_role = Mock()
        user_role.name = "User"

        return {
            "member": member_role,
            "steering_committee": steering_committee_role,
            "admin": admin_role,
            "owner": owner_role,
            "user": user_role,
        }

    def test_check_user_roles_with_hierarchy_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Member role grants Member access."""
        roles = [mock_roles_with_hierarchy["member"]]
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is True

    def test_check_user_roles_with_hierarchy_admin_grants_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Admin role grants Member access."""
        roles = [mock_roles_with_hierarchy["admin"]]
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is True

    def test_check_user_roles_with_hierarchy_steering_committee_grants_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Steering Committee role grants Member access."""
        roles = [mock_roles_with_hierarchy["steering_committee"]]
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is True

    def test_check_user_roles_with_hierarchy_admin_grants_steering_committee_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Admin role grants Steering Committee access."""
        roles = [mock_roles_with_hierarchy["admin"]]
        result = check_user_roles_with_hierarchy(roles, ["Steering Committee"])
        assert result is True

    def test_check_user_roles_with_hierarchy_owner_grants_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Owner role grants Member access."""
        roles = [mock_roles_with_hierarchy["owner"]]
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is True

    def test_check_user_roles_with_hierarchy_owner_grants_steering_committee_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Owner role grants Steering Committee access."""
        roles = [mock_roles_with_hierarchy["owner"]]
        result = check_user_roles_with_hierarchy(roles, ["Steering Committee"])
        assert result is True

    def test_check_user_roles_with_hierarchy_owner_grants_admin_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Owner role grants Admin access."""
        roles = [mock_roles_with_hierarchy["owner"]]
        result = check_user_roles_with_hierarchy(roles, ["Admin"])
        assert result is True

    def test_check_user_roles_with_hierarchy_member_denied_steering_committee_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Member role does not grant Steering Committee access."""
        roles = [mock_roles_with_hierarchy["member"]]
        result = check_user_roles_with_hierarchy(roles, ["Steering Committee"])
        assert result is False

    def test_check_user_roles_with_hierarchy_member_denied_admin_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Member role does not grant Admin access."""
        roles = [mock_roles_with_hierarchy["member"]]
        result = check_user_roles_with_hierarchy(roles, ["Admin"])
        assert result is False

    def test_check_user_roles_with_hierarchy_steering_committee_denied_admin_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that Steering Committee role does not grant Admin access."""
        roles = [mock_roles_with_hierarchy["steering_committee"]]
        result = check_user_roles_with_hierarchy(roles, ["Admin"])
        assert result is False

    def test_check_user_roles_with_hierarchy_no_roles(self, mock_roles_with_hierarchy):
        """Test that no roles denies access."""
        roles = []
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is False

    def test_check_user_roles_with_hierarchy_non_hierarchical_role(
        self, mock_roles_with_hierarchy
    ):
        """Test that non-hierarchical roles still work with direct matching."""
        roles = [mock_roles_with_hierarchy["user"]]
        result = check_user_roles_with_hierarchy(roles, ["User"])
        assert result is True

    def test_check_user_roles_with_hierarchy_mixed_roles(
        self, mock_roles_with_hierarchy
    ):
        """Test hierarchy with mix of hierarchical and non-hierarchical roles."""
        roles = [mock_roles_with_hierarchy["admin"], mock_roles_with_hierarchy["user"]]

        # Should grant Member access due to Admin role
        result = check_user_roles_with_hierarchy(roles, ["Member"])
        assert result is True

        # Should grant Admin access due to Admin role
        result = check_user_roles_with_hierarchy(roles, ["Admin"])
        assert result is True

        # Should grant User access due to User role
        result = check_user_roles_with_hierarchy(roles, ["User"])
        assert result is True

    def test_get_user_hierarchy_level_member(self, mock_roles_with_hierarchy):
        """Test getting hierarchy level for Member."""
        roles = [mock_roles_with_hierarchy["member"]]
        level = get_user_hierarchy_level(roles)
        assert level == 0  # Member is level 0

    def test_get_user_hierarchy_level_steering_committee(
        self, mock_roles_with_hierarchy
    ):
        """Test getting hierarchy level for Steering Committee."""
        roles = [mock_roles_with_hierarchy["steering_committee"]]
        level = get_user_hierarchy_level(roles)
        assert level == 1  # Steering Committee is level 1

    def test_get_user_hierarchy_level_admin(self, mock_roles_with_hierarchy):
        """Test getting hierarchy level for Admin."""
        roles = [mock_roles_with_hierarchy["admin"]]
        level = get_user_hierarchy_level(roles)
        assert level == 2  # Admin is level 2

    def test_get_user_hierarchy_level_owner(self, mock_roles_with_hierarchy):
        """Test getting hierarchy level for Owner."""
        roles = [mock_roles_with_hierarchy["owner"]]
        level = get_user_hierarchy_level(roles)
        assert level == 3  # Owner is level 3

    def test_get_user_hierarchy_level_no_hierarchical_roles(
        self, mock_roles_with_hierarchy
    ):
        """Test getting hierarchy level with no hierarchical roles."""
        roles = [mock_roles_with_hierarchy["user"]]
        level = get_user_hierarchy_level(roles)
        assert level == -1  # No hierarchical roles

    def test_get_user_hierarchy_level_multiple_roles(self, mock_roles_with_hierarchy):
        """Test getting highest hierarchy level with multiple roles."""
        roles = [
            mock_roles_with_hierarchy["member"],
            mock_roles_with_hierarchy["admin"],
        ]
        level = get_user_hierarchy_level(roles)
        assert level == 2  # Highest is Admin (level 2)

    @pytest.mark.asyncio
    async def test_require_role_with_admin_grants_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that require_role decorator allows Admin to access Member-only commands."""
        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.user = Mock()
        mock_interaction.user.roles = [mock_roles_with_hierarchy["admin"]]

        @require_role("Member")
        async def test_command(self, interaction):
            return "success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result == "success"
        mock_interaction.response.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_role_with_owner_grants_member_access(
        self, mock_roles_with_hierarchy
    ):
        """Test that require_role decorator allows Owner to access Member-only commands."""
        mock_interaction = AsyncMock()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.user = Mock()
        mock_interaction.user.roles = [mock_roles_with_hierarchy["owner"]]

        @require_role("Member")
        async def test_command(self, interaction):
            return "success"

        mock_self = Mock()

        result = await test_command(mock_self, mock_interaction)

        assert result == "success"
        mock_interaction.response.send_message.assert_not_called()
