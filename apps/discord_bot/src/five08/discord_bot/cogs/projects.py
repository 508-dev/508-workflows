"""ERP project lookup commands for Discord."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.role_decorators import check_user_roles_with_hierarchy
from five08.projects import list_dashboard_projects, project_viewer_emails_for_discord

logger = logging.getLogger(__name__)


class ProjectsCog(commands.Cog, name="Projects"):
    """Read-only ERP project status views."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="projects",
        description="List visible open ERP projects and their ERP roster.",
    )
    @app_commands.describe(
        query="Optional project or customer search.",
        limit="Maximum projects to show.",
    )
    async def projects(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        limit: app_commands.Range[int, 1, 15] = 10,
    ) -> None:
        """List open ERPNext projects visible to the caller in Discord."""
        await interaction.response.defer(ephemeral=True)
        try:
            projects, include_all, had_roster_identity = await asyncio.to_thread(
                self._load_projects,
                interaction,
                query,
                int(limit),
            )
        except Exception:
            logger.exception("Cached project lookup failed")
            await interaction.followup.send(
                "Project lookup failed. Try again after the project cache has synced.",
                ephemeral=True,
            )
            return

        if not include_all and not had_roster_identity:
            await interaction.followup.send(
                "Projects are visible to Steering Committee, or to confirmed ERP project members for their own projects.",
                ephemeral=True,
            )
            return
        if not projects:
            await interaction.followup.send(
                "No visible open ERP projects matched that search.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Open ERP Projects" if include_all else "Your Open ERP Projects",
            description=f"Showing {len(projects)} project(s)",
            color=discord.Color.teal(),
        )
        for project in projects[: int(limit)]:
            raw_roster = project.get("roster_members")
            roster_members = raw_roster if isinstance(raw_roster, list) else []
            roster = [
                str(
                    member.get("full_name")
                    or member.get("email")
                    or member.get("source_user_id")
                )
                for member in roster_members
                if isinstance(member, dict)
                and (
                    member.get("full_name")
                    or member.get("email")
                    or member.get("source_user_id")
                )
            ]
            roster_text = ", ".join(roster[:6]) if roster else "No ERP roster"
            if len(roster) > 6:
                roster_text += f" +{len(roster) - 6}"
            value = "\n".join(
                part
                for part in (
                    f"Customer: {project.get('customer') or 'None'}",
                    f"Status: {project.get('source_status') or 'Unknown'}",
                    f"Roster: {roster_text}",
                )
                if part
            )
            embed.add_field(
                name=f"{project.get('display_name') or project.get('erpnext_project_id')}",
                value=value[:1024],
                inline=False,
            )
        embed.set_footer(text="Data from the synced ERPNext Project.users roster")
        await interaction.followup.send(embed=embed, ephemeral=True)

    def _load_projects(
        self,
        interaction: discord.Interaction,
        query: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        roles = getattr(interaction.user, "roles", [])
        include_all = check_user_roles_with_hierarchy(
            roles,
            ["Steering Committee"],
        )
        viewer_emails: list[str] = []
        if not include_all:
            viewer_emails = project_viewer_emails_for_discord(
                settings,
                str(interaction.user.id),
            )
            if not viewer_emails:
                return [], False, False

        projects = list_dashboard_projects(
            settings,
            query=query,
            status="Open",
            viewer_emails=viewer_emails,
            include_all=include_all,
            limit=limit,
        )
        return projects, include_all, True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectsCog(bot))
