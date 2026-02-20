"""
Kimai time tracking integration cog for the 508.dev Discord bot.

This cog provides commands for interacting with Kimai time tracking through Discord slash commands.
It allows authorized team members to view project hours and breakdowns.
"""

import logging
from datetime import datetime, timedelta
from typing import Any
from discord.ext import commands
from discord import app_commands
import discord

from five08.discord_bot.config import settings
from five08.clients import kimai, espo

from five08.discord_bot.utils.role_decorators import (
    require_role,
    check_user_roles_with_hierarchy,
)

logger = logging.getLogger(__name__)


class KimaiCog(commands.Cog, name="Kimai"):
    """Cog for Kimai time tracking integration."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the Kimai cog."""
        self.bot = bot
        self.api = kimai.KimaiAPI(settings.kimai_base_url, settings.kimai_api_token)
        api_url = settings.espo_base_url.rstrip("/") + "/api/v1"
        self.espo_api = espo.EspoAPI(api_url, settings.espo_api_key)
        logger.info("Kimai cog initialized")

    @app_commands.command(
        name="kimai-project-hours",
        description="Get hours logged for a project with breakdown by team members",
    )
    @app_commands.describe(
        project_name="Name of the project to query",
        month="Month in YYYY-MM format (e.g., 2024-03). Leave empty for current month.",
        start_date="Custom start date in YYYY-MM-DD format (overrides month parameter).",
        end_date="Custom end date in YYYY-MM-DD format (optional with start_date), defaults to end of month if no end_date given.",
    )
    async def project_hours(
        self,
        interaction: discord.Interaction,
        project_name: str,
        month: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        """
        Get hours logged for a project with breakdown by team members.

        Accessible to Steering Committee+ or team leads of the project.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Parse date range
            begin, end, date_description = self._parse_date_range(
                month, start_date, end_date
            )

            # Find the project
            project = self.api.get_project_by_name(project_name)
            if not project:
                await interaction.followup.send(
                    f"Project '{project_name}' not found. Please check the project name and try again.",
                    ephemeral=True,
                )
                return

            project_id = project["id"]
            project_display_name = project.get("name", project_name)

            # Check permissions: Steering Committee+ OR team lead of this project
            has_steering_role = hasattr(
                interaction.user, "roles"
            ) and check_user_roles_with_hierarchy(
                interaction.user.roles, ["Steering Committee"]
            )

            # Only check team lead status if user doesn't have steering role
            if not has_steering_role:
                is_team_lead = await self._is_discord_user_team_lead(
                    str(interaction.user.id), project_id
                )
                if not is_team_lead:
                    await interaction.followup.send(
                        f"❌ You don't have permission to view hours for '{project_display_name}'. "
                        f"This command is only accessible to Steering Committee members or team leads of the project.",
                        ephemeral=True,
                    )
                    return

            # Get hours breakdown by user
            user_hours = self.api.get_project_hours_by_user(
                project_id=project_id, begin=begin, end=end
            )

            # Calculate total hours and billed amount
            total_hours = sum(data["hours"] for data in user_hours.values())
            total_entries = sum(data["entries"] for data in user_hours.values())
            total_billed = sum(data["billed_amount"] for data in user_hours.values())

            # Build response embed
            embed = discord.Embed(
                title=f"Project Hours: {project_display_name}",
                description=f"Time period: {date_description}",
                color=discord.Color.blue(),
            )

            # Add total summary
            embed.add_field(
                name="Total Hours",
                value=f"**{self._format_hours(total_hours)}** ({total_entries} entries)",
                inline=True,
            )

            # Add total billed amount
            embed.add_field(
                name="Total Billed",
                value=f"**${total_billed:,.2f}**",
                inline=True,
            )

            # Add breakdown by team member
            if user_hours:
                # Sort by hours (descending)
                sorted_users = sorted(
                    user_hours.items(), key=lambda x: x[1]["hours"], reverse=True
                )

                breakdown_lines = []
                for user_name, data in sorted_users:
                    hours = data["hours"]
                    entries = data["entries"]
                    billed = data["billed_amount"]
                    zero_rate_entries = data.get("zero_rate_entries", 0)
                    zero_rate_note = (
                        f" — {zero_rate_entries} $0 record(s) found"
                        if zero_rate_entries > 0
                        else ""
                    )
                    breakdown_lines.append(
                        f"**{user_name}**: {self._format_hours(hours)} ({entries} entries) - ${billed:,.2f}{zero_rate_note}"
                    )

                # Discord field value limit is 1024 characters
                breakdown_text = "\n".join(breakdown_lines)
                if len(breakdown_text) > 1024:
                    # Split into multiple fields if needed
                    chunks = self._chunk_text(breakdown_lines, 1024)
                    for i, chunk in enumerate(chunks):
                        field_name = (
                            "Team Member Breakdown"
                            if i == 0
                            else f"Team Member Breakdown (cont. {i + 1})"
                        )
                        embed.add_field(name=field_name, value=chunk, inline=False)
                else:
                    embed.add_field(
                        name="Team Member Breakdown",
                        value=breakdown_text,
                        inline=False,
                    )
            else:
                embed.add_field(
                    name="Team Member Breakdown",
                    value="No time entries found for this project in the specified period.",
                    inline=False,
                )

            embed.set_footer(text="Data from Kimai time tracking")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except kimai.KimaiAPIError as e:
            logger.error(f"Kimai API error in project_hours command: {e}")
            await interaction.followup.send(
                f"Failed to retrieve project hours: {str(e)}", ephemeral=True
            )
        except ValueError as e:
            logger.error(f"Date parsing error in project_hours command: {e}")
            await interaction.followup.send(
                f"Invalid date format: {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Unexpected error in project_hours command: {e}")
            await interaction.followup.send(
                "An unexpected error occurred while retrieving project hours.",
                ephemeral=True,
            )

    @app_commands.command(
        name="kimai-projects",
        description="List available projects in Kimai (all if Steering Committee+, your team lead projects otherwise)",
    )
    async def list_projects(self, interaction: discord.Interaction) -> None:
        """
        List available projects in Kimai.

        Steering Committee+ can see all projects.
        Others can see projects they are team lead of.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Check if user has Steering Committee role or higher
            has_steering_role = hasattr(
                interaction.user, "roles"
            ) and check_user_roles_with_hierarchy(
                interaction.user.roles, ["Steering Committee"]
            )

            if has_steering_role:
                # Show all projects
                projects = self.api.get_projects()
                title_suffix = "All Projects"
            else:
                # Show only team lead projects
                projects = await self._get_discord_user_team_lead_projects(
                    str(interaction.user.id)
                )
                title_suffix = "Your Team Lead Projects"

            if not projects:
                if has_steering_role:
                    await interaction.followup.send(
                        "No projects found in Kimai.", ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "You are not a team lead of any projects. If you believe this is an error, please contact a Steering Committee member.",
                        ephemeral=True,
                    )
                return

            embed = discord.Embed(
                title=f"Kimai Projects - {title_suffix}",
                description=f"Found {len(projects)} project(s)",
                color=discord.Color.green(),
            )

            # Group projects into chunks for Discord's field limit
            project_lines = []
            for project in projects:
                name = project.get("name", "Unknown")
                customer = project.get("customer", {})
                customer_name = (
                    customer.get("name", "") if isinstance(customer, dict) else ""
                )
                visible = project.get("visible", True)
                status = "" if visible else " [Hidden]"

                if customer_name:
                    project_lines.append(
                        f"**{name}** (Customer: {customer_name}){status}"
                    )
                else:
                    project_lines.append(f"**{name}**{status}")

            # Discord field value limit is 1024 characters
            chunks = self._chunk_text(project_lines, 1024)
            for i, chunk in enumerate(chunks):
                field_name = "Projects" if i == 0 else f"Projects (cont. {i + 1})"
                embed.add_field(name=field_name, value=chunk, inline=False)

            embed.set_footer(
                text="Use /kimai-project-hours to view hours for a project"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except kimai.KimaiAPIError as e:
            logger.error(f"Kimai API error in list_projects command: {e}")
            await interaction.followup.send(
                f"Failed to retrieve projects: {str(e)}", ephemeral=True
            )
        except espo.EspoAPIError as e:
            logger.error(f"CRM API error in list_projects command: {e}")
            await interaction.followup.send(
                f"Failed to retrieve your CRM profile: {str(e)}", ephemeral=True
            )
        except Exception as e:
            logger.error(f"Unexpected error in list_projects command: {e}")
            await interaction.followup.send(
                "An unexpected error occurred while retrieving projects.",
                ephemeral=True,
            )

    @app_commands.command(
        name="kimai-status", description="Check Kimai API connection status"
    )
    @require_role("Steering Committee")
    async def status(self, interaction: discord.Interaction) -> None:
        """
        Check Kimai API connection status.

        Only accessible to Steering Committee, Admin, and Owner roles.
        """
        await interaction.response.defer(ephemeral=True)

        try:
            # Try to fetch projects as a connectivity test
            projects = self.api.get_projects()
            project_count = len(projects)

            embed = discord.Embed(
                title="Kimai API Status",
                description="Connection successful",
                color=discord.Color.green(),
            )
            embed.add_field(name="Status", value="Connected", inline=True)
            embed.add_field(
                name="Projects Found", value=str(project_count), inline=True
            )
            embed.add_field(
                name="Base URL", value=settings.kimai_base_url, inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except kimai.KimaiAPIError as e:
            logger.error(f"Kimai API error in status command: {e}")
            embed = discord.Embed(
                title="Kimai API Status",
                description="Connection failed",
                color=discord.Color.red(),
            )
            embed.add_field(name="Status", value="Disconnected", inline=True)
            embed.add_field(name="Error", value=str(e), inline=False)
            embed.add_field(
                name="Base URL", value=settings.kimai_base_url, inline=False
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error in status command: {e}")
            await interaction.followup.send(
                f"An unexpected error occurred while checking status: {str(e)}",
                ephemeral=True,
            )

    async def _get_kimai_user_from_discord(
        self, discord_user_id: str
    ) -> dict[str, Any] | None:
        """
        Get Kimai user from Discord user ID via CRM linkage.

        Args:
            discord_user_id: Discord user ID

        Returns:
            Kimai user dictionary if found, None otherwise
        """
        try:
            # Find CRM contact by Discord ID
            search_params = {
                "where": [
                    {
                        "type": "equals",
                        "attribute": "cDiscordUserID",
                        "value": discord_user_id,
                    }
                ],
                "maxSize": 1,
                "select": "id,name,c508Email",
            }

            response = self.espo_api.request("GET", "Contact", search_params)
            contacts = response.get("list", [])

            if not contacts:
                logger.debug(f"No CRM contact found for Discord ID: {discord_user_id}")
                return None

            contact = contacts[0]
            email = contact.get("c508Email")

            if not email:
                logger.warning(f"CRM contact {contact.get('id')} has no c508Email set")
                return None

            # Extract username from email (portion before @)
            username = email.split("@")[0]

            # Find Kimai user by username
            kimai_user = self.api.get_user_by_username(username)
            if not kimai_user:
                logger.debug(f"No Kimai user found for username: {username}")
                return None

            return kimai_user

        except espo.EspoAPIError as e:
            logger.error(
                f"EspoCRM API error getting user from Discord ID {discord_user_id}: {e}"
            )
            return None
        except kimai.KimaiAPIError as e:
            logger.error(
                f"Kimai API error getting user from Discord ID {discord_user_id}: {e}"
            )
            return None

    async def _is_discord_user_team_lead(
        self, discord_user_id: str, project_id: int
    ) -> bool:
        """
        Check if a Discord user is the team lead of a project.

        Args:
            discord_user_id: Discord user ID
            project_id: Project ID

        Returns:
            True if the user is the team lead, False otherwise
        """
        kimai_user = await self._get_kimai_user_from_discord(discord_user_id)

        if not kimai_user:
            return False

        user_id = kimai_user.get("id")
        if not user_id:
            return False

        return self.api.is_project_team_lead(project_id, user_id)

    async def _get_discord_user_team_lead_projects(
        self, discord_user_id: str
    ) -> list[dict[str, Any]]:
        """
        Get all projects where a Discord user is the team lead.

        Args:
            discord_user_id: Discord user ID

        Returns:
            List of projects where the user is team lead
        """
        kimai_user = await self._get_kimai_user_from_discord(discord_user_id)

        if not kimai_user:
            return []

        user_id = kimai_user.get("id")
        if not user_id:
            return []

        return self.api.get_projects_by_team_lead(user_id)

    def _parse_date_range(
        self, month: str | None, start_date: str | None, end_date: str | None
    ) -> tuple[datetime, datetime, str]:
        """
        Parse date range from command parameters.

        Returns:
            Tuple of (begin_datetime, end_datetime, description_string)
        """
        if start_date:
            # Custom date range
            try:
                begin = datetime.strptime(start_date, "%Y-%m-%d")
            except ValueError:
                raise ValueError(
                    f"Invalid start_date format: '{start_date}'. Expected YYYY-MM-DD."
                )

            if end_date:
                try:
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                    # Set end to end of day
                    end = end.replace(hour=23, minute=59, second=59)
                except ValueError:
                    raise ValueError(
                        f"Invalid end_date format: '{end_date}'. Expected YYYY-MM-DD."
                    )
            else:
                # If no end_date, use end of start_date's month
                if begin.month == 12:
                    end = begin.replace(year=begin.year + 1, month=1, day=1)
                else:
                    end = begin.replace(month=begin.month + 1, day=1)
                end = end - timedelta(seconds=1)

            description = f"{begin.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"

        elif month:
            # Month format: YYYY-MM
            try:
                begin = datetime.strptime(month, "%Y-%m")
            except ValueError:
                raise ValueError(
                    f"Invalid month format: '{month}'. Expected YYYY-MM (e.g., 2024-03)."
                )

            # Calculate last day of month
            if begin.month == 12:
                end = begin.replace(year=begin.year + 1, month=1, day=1)
            else:
                end = begin.replace(month=begin.month + 1, day=1)
            end = end - timedelta(seconds=1)

            description = begin.strftime("%B %Y")

        else:
            # Default to current month
            now = datetime.now()
            begin = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            # Calculate last day of current month
            if begin.month == 12:
                end = begin.replace(year=begin.year + 1, month=1, day=1)
            else:
                end = begin.replace(month=begin.month + 1, day=1)
            end = end - timedelta(seconds=1)

            description = f"{begin.strftime('%B %Y')} (current month)"

        return begin, end, description

    def _format_hours(self, hours: float) -> str:
        """
        Format hours as hh:mm.

        Args:
            hours: Hours as a decimal number

        Returns:
            Formatted string in hh:mm format
        """
        total_minutes = int(hours * 60)
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h}:{m:02d}"

    def _chunk_text(self, lines: list[str], max_length: int) -> list[str]:
        """
        Split lines into chunks that don't exceed max_length.

        Args:
            lines: List of text lines to chunk
            max_length: Maximum character length per chunk

        Returns:
            List of text chunks
        """
        chunks = []
        current_chunk: list[str] = []
        current_length = 0

        for line in lines:
            line_length = len(line) + 1  # +1 for newline

            if current_length + line_length > max_length and current_chunk:
                # Current chunk is full, start a new one
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = line_length
            else:
                current_chunk.append(line)
                current_length += line_length

        # Add remaining lines
        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks


async def setup(bot: commands.Bot) -> None:
    """Setup function to add the cog to the bot."""
    # Avoid breaking the entire bot if Kimai isn't configured
    if not settings.kimai_base_url or not settings.kimai_api_token:
        logger.warning(
            "Kimai cog not loaded: missing KIMAI_BASE_URL and/or KIMAI_API_TOKEN"
        )
        return
    await bot.add_cog(KimaiCog(bot))
