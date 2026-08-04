"""Discord controls for bounded recurring agent reports."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
import requests
from discord import app_commands
from discord.ext import commands

from five08.agent import AgentIdentityContext, PolicyEngine
from five08.discord_bot.config import settings
from five08.tls import default_ca_bundle_path

logger = logging.getLogger(__name__)


class AgentSchedulesCog(commands.Cog):
    """Create and control policy-bound recurring agent reports."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="schedule-github-issues",
        description="Schedule a bounded GitHub issue report to a Discord channel",
    )
    @app_commands.describe(
        name="A short name for this recurring report",
        cron="Five-field cron, for example 0 9 * * 1",
        repository="GitHub owner/repository",
        channel="Channel where each report is posted",
        prompt="Instructions for the report's summary",
        timezone="IANA timezone, for example Asia/Tokyo",
        query="Optional GitHub issue search terms",
        public_sources="Allow a configured model to summarize public issue metadata",
    )
    async def schedule_github_issues_command(
        self,
        interaction: discord.Interaction,
        name: str,
        cron: str,
        repository: str,
        channel: discord.TextChannel,
        prompt: str,
        timezone: str = "UTC",
        query: str = "",
        public_sources: bool = False,
    ) -> None:
        """Create one immutable recurring GitHub issue report envelope."""

        await interaction.response.defer(ephemeral=True)
        context = self._context(interaction)
        if context is None:
            await interaction.followup.send(
                "Recurring agent schedules are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.followup.send(
                "Choose a channel in this Discord server.",
                ephemeral=True,
            )
            return
        payload = {
            "context": context,
            "name": name,
            "cron_expression": cron,
            "timezone": timezone,
            "prompt": prompt,
            "execution_mode": "frozen_actions",
            "repository": repository,
            "query": query,
            "state": "open",
            "limit": 10,
            "channel_id": str(channel.id),
            "summary_mode": (
                "model_for_public_data" if public_sources else "deterministic"
            ),
            "sources_are_public": public_sources,
        }
        response = await self._post_backend("/agent/schedules", payload)
        if response.get("http_status", 500) >= 400:
            await interaction.followup.send(
                self._error_message(response, "Unable to create the schedule."),
                ephemeral=True,
            )
            return
        schedule = response.get("schedule")
        if not isinstance(schedule, dict):
            await interaction.followup.send(
                "The schedule was created, but the backend returned an incomplete response.",
                ephemeral=True,
            )
            return
        next_run = str(schedule.get("next_run_at") or "unknown")
        await interaction.followup.send(
            "Created scheduled report "
            f"`{schedule.get('id')}` for <#{channel.id}>. Next run: {next_run}",
            ephemeral=True,
        )

    @app_commands.command(
        name="schedule-agent",
        description="Schedule a bounded read-only agent report to a Discord channel",
    )
    @app_commands.describe(
        name="A short name for this recurring report",
        cron="Five-field cron, for example 0 9 * * 1",
        channel="Channel where each report is posted",
        prompt="What to inspect and report on each run",
        timezone="IANA timezone, for example Asia/Tokyo",
    )
    async def schedule_agent_command(
        self,
        interaction: discord.Interaction,
        name: str,
        cron: str,
        channel: discord.TextChannel,
        prompt: str,
        timezone: str = "UTC",
    ) -> None:
        """Create a generic loop over the creator's saved read-only catalog."""

        await interaction.response.defer(ephemeral=True)
        context = self._context(interaction)
        if context is None:
            await interaction.followup.send(
                "Recurring agent schedules are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.followup.send(
                "Choose a channel in this Discord server.",
                ephemeral=True,
            )
            return
        payload = {
            "context": context,
            "name": name,
            "cron_expression": cron,
            "timezone": timezone,
            "prompt": prompt,
            "execution_mode": "agent_loop",
            "channel_id": str(channel.id),
        }
        response = await self._post_backend("/agent/schedules", payload)
        if response.get("http_status", 500) >= 400:
            await interaction.followup.send(
                self._error_message(response, "Unable to create the schedule."),
                ephemeral=True,
            )
            return
        schedule = response.get("schedule")
        if not isinstance(schedule, dict):
            await interaction.followup.send(
                "The schedule was created, but the backend returned an incomplete response.",
                ephemeral=True,
            )
            return
        next_run = str(schedule.get("next_run_at") or "unknown")
        await interaction.followup.send(
            "Created bounded agent schedule "
            f"`{schedule.get('id')}` for <#{channel.id}>. Next run: {next_run}",
            ephemeral=True,
        )

    @app_commands.command(
        name="schedules",
        description="List recurring agent reports in this Discord server",
    )
    async def schedules_command(self, interaction: discord.Interaction) -> None:
        """List schedules without exposing prompt text or execution credentials."""

        await interaction.response.defer(ephemeral=True)
        context = self._context(interaction)
        if context is None:
            await interaction.followup.send(
                "Recurring agent schedules are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        response = await self._post_backend(
            "/agent/schedules/list", {"context": context}
        )
        if response.get("http_status", 500) >= 400:
            await interaction.followup.send(
                self._error_message(response, "Unable to list schedules."),
                ephemeral=True,
            )
            return
        schedules = response.get("schedules")
        if not isinstance(schedules, list) or not schedules:
            await interaction.followup.send(
                "No recurring agent schedules are configured.", ephemeral=True
            )
            return
        lines = ["Recurring agent schedules:"]
        for schedule in schedules[:20]:
            if not isinstance(schedule, dict):
                continue
            schedule_id = str(schedule.get("id") or "")
            name = str(schedule.get("name") or "Unnamed")
            status = str(schedule.get("status") or "unknown")
            next_run = str(schedule.get("next_run_at") or "not scheduled")
            lines.append(f"- `{schedule_id}` — {name} ({status}; next: {next_run})")
        await interaction.followup.send("\n".join(lines)[:1_900], ephemeral=True)

    @app_commands.command(
        name="schedule-control",
        description="Pause, resume, or archive a recurring agent report",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Pause", value="pause"),
            app_commands.Choice(name="Resume", value="resume"),
            app_commands.Choice(name="Archive", value="archive"),
        ]
    )
    async def schedule_control_command(
        self,
        interaction: discord.Interaction,
        schedule_id: str,
        action: app_commands.Choice[str],
    ) -> None:
        """Change a retained schedule's lifecycle without editing its envelope."""

        await interaction.response.defer(ephemeral=True)
        context = self._context(interaction)
        if context is None:
            await interaction.followup.send(
                "Recurring agent schedules are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        response = await self._post_backend(
            f"/agent/schedules/{schedule_id}/control",
            {"context": context, "action": action.value},
        )
        if response.get("http_status", 500) >= 400:
            await interaction.followup.send(
                self._error_message(response, "Unable to update the schedule."),
                ephemeral=True,
            )
            return
        schedule = (
            response.get("schedule")
            if isinstance(response.get("schedule"), dict)
            else {}
        )
        await interaction.followup.send(
            f"Schedule `{schedule_id}` is now {schedule.get('status') or action.value}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="schedule-run",
        description="Queue a manual run of a recurring agent report",
    )
    async def schedule_run_command(
        self,
        interaction: discord.Interaction,
        schedule_id: str,
    ) -> None:
        """Queue a normal durable worker run rather than executing inline."""

        await interaction.response.defer(ephemeral=True)
        context = self._context(interaction)
        if context is None:
            await interaction.followup.send(
                "Recurring agent schedules are only available in the configured Discord server.",
                ephemeral=True,
            )
            return
        response = await self._post_backend(
            f"/agent/schedules/{schedule_id}/run",
            {"context": context},
        )
        if response.get("http_status", 500) >= 400:
            await interaction.followup.send(
                self._error_message(response, "Unable to queue the schedule run."),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            self._manual_run_response_message(response),
            ephemeral=True,
        )

    @staticmethod
    def _manual_run_response_message(response: dict[str, Any]) -> str:
        """Describe whether a manual click created work or found a prior run."""

        run = response.get("run") if isinstance(response.get("run"), dict) else {}
        run_id = str(run.get("id") or "unknown")
        job_id = str(response.get("job_id") or run.get("job_id") or "dispatch pending")
        status = str(response.get("status") or "").strip()
        if status == "queued":
            return f"Queued schedule run `{run_id}` (worker job: {job_id})."
        if status == "already_queued":
            return f"Schedule run `{run_id}` is already queued (worker job: {job_id})."
        if status == "already_requested":
            run_status = str(run.get("status") or "completed")
            return f"A recent schedule run `{run_id}` already exists ({run_status})."
        return f"Schedule run `{run_id}` request accepted ({status or 'unknown status'})."

    def _context(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        guild_id = getattr(interaction, "guild_id", None)
        if guild_id is None:
            return None
        policy = PolicyEngine.from_settings(settings)
        if not policy.guild_is_allowed(str(guild_id)):
            return None
        role_ids: list[str] = []
        roles: list[str] = []
        for role in getattr(interaction.user, "roles", []):
            role_id = str(getattr(role, "id", "")).strip()
            role_name = str(getattr(role, "name", "")).strip()
            if role_id.isdecimal() and int(role_id) > 0 and role_id not in role_ids:
                role_ids.append(role_id)
            if role_name and role_name not in roles:
                roles.append(role_name)
        return AgentIdentityContext(
            discord_user_id=str(interaction.user.id),
            organization_id=str(guild_id),
            guild_id=str(guild_id),
            channel_id=str(interaction.channel_id) if interaction.channel_id else None,
            interaction_id=str(interaction.id),
            role_ids=role_ids,
            roles=roles,
        ).model_dump(mode="json")

    async def _post_backend(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._post_backend_sync, path, payload)

    @staticmethod
    def _post_backend_sync(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        base_url = settings.backend_api_base_url.rstrip("/")
        secret = str(settings.api_shared_secret or "").strip()
        if not base_url or not secret:
            return {
                "http_status": 503,
                "error": "backend_api_or_api_secret_not_configured",
            }
        try:
            response = requests.post(
                f"{base_url}{path}",
                headers={"X-API-Secret": secret},
                json=payload,
                timeout=settings.agent_api_timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            logger.warning("Schedule backend request failed: %s", exc)
            return {"http_status": 503, "error": "backend_request_failed"}
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("http_status", response.status_code)
        return data

    @staticmethod
    def _error_message(response: dict[str, Any], fallback: str) -> str:
        detail = str(response.get("detail") or response.get("error") or "").strip()
        return f"{fallback} {detail}".strip()


async def setup(bot: commands.Bot) -> None:
    """Register the recurring schedules cog."""

    await bot.add_cog(AgentSchedulesCog(bot))
