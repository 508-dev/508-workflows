"""
Role-based access control decorators for Discord commands.

Provides decorators to restrict command access based on user roles.
"""

from functools import wraps
from typing import List, Any, Callable
import discord


def require_roles(
    *required_roles: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to require specific roles for command access.

    Role hierarchy: Owner > Admin > Steering Committee > Member
    If a higher role is present, it grants access to lower role requirements.

    Args:
        *required_roles: Role names that are allowed to use the command

    Usage:
        @require_roles("Member", "Admin")
        async def my_command(self, interaction: discord.Interaction):
            # Command implementation
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(
            self: Any, interaction: discord.Interaction, *args: Any, **kwargs: Any
        ) -> Any:
            # Check if user has any of the required roles (with hierarchy)
            if not hasattr(
                interaction.user, "roles"
            ) or not check_user_roles_with_hierarchy(
                interaction.user.roles, list(required_roles)
            ):
                role_list = ", ".join(required_roles)
                await interaction.response.send_message(
                    f"❌ You must have one of these roles to use this command: {role_list}",
                    ephemeral=True,
                )
                return

            # User has required role, proceed with command
            return await func(self, interaction, *args, **kwargs)

        return wrapper

    return decorator


def require_role(
    required_role: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to require a specific role for command access.

    Args:
        required_role: Role name that is required to use the command

    Usage:
        @require_role("Member")
        async def my_command(self, interaction: discord.Interaction):
            # Command implementation
    """
    return require_roles(required_role)


def check_user_roles(user_roles: List[discord.Role], required_roles: List[str]) -> bool:
    """
    Helper function to check if user has any of the required roles.

    Args:
        user_roles: List of Discord roles the user has
        required_roles: List of role names that are required

    Returns:
        True if user has at least one required role, False otherwise
    """
    user_role_names = {role.name for role in user_roles}
    return any(role in user_role_names for role in required_roles)


def get_missing_roles(
    user_roles: List[discord.Role], required_roles: List[str]
) -> List[str]:
    """
    Get list of required roles that user doesn't have.

    Args:
        user_roles: List of Discord roles the user has
        required_roles: List of role names that are required

    Returns:
        List of role names the user is missing
    """
    user_role_names = {role.name for role in user_roles}
    return [role for role in required_roles if role not in user_role_names]


def check_user_roles_with_hierarchy(
    user_roles: List[discord.Role], required_roles: List[str]
) -> bool:
    """
    Check if user has any of the required roles, considering role hierarchy.

    Role hierarchy: Owner > Admin > Steering Committee > Member
    If a user has a higher role, they automatically have access to lower role requirements.

    Args:
        user_roles: List of Discord roles the user has
        required_roles: List of role names that are required

    Returns:
        True if user has at least one required role or a higher role, False otherwise
    """
    # Define role hierarchy (higher index = higher priority)
    ROLE_HIERARCHY = ["Member", "Steering Committee", "Admin", "Owner"]

    user_role_names = {role.name for role in user_roles}

    # Get the highest user role level
    user_highest_level = -1
    for role_name in user_role_names:
        if role_name in ROLE_HIERARCHY:
            user_highest_level = max(
                user_highest_level, ROLE_HIERARCHY.index(role_name)
            )

    # Check if user has sufficient role level for any required role
    for required_role in required_roles:
        if required_role in ROLE_HIERARCHY:
            required_level = ROLE_HIERARCHY.index(required_role)
            if user_highest_level >= required_level:
                return True
        elif required_role in user_role_names:
            # Non-hierarchical role direct match
            return True

    return False


def get_user_hierarchy_level(user_roles: List[discord.Role]) -> int:
    """
    Get the user's highest role level in the hierarchy.

    Args:
        user_roles: List of Discord roles the user has

    Returns:
        Highest role level (-1 if no hierarchical roles, 0=Member, 1=Steering Committee, 2=Admin, 3=Owner)
    """
    ROLE_HIERARCHY = ["Member", "Steering Committee", "Admin", "Owner"]
    user_role_names = {role.name for role in user_roles}

    highest_level = -1
    for role_name in user_role_names:
        if role_name in ROLE_HIERARCHY:
            highest_level = max(highest_level, ROLE_HIERARCHY.index(role_name))

    return highest_level
