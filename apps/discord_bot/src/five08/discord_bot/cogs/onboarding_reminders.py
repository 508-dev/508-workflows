"""Volunteer availability commands and recurring onboarding reminders."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.onboarding import (
    VolunteerAvailability,
    claim_due_onboarding_reminders,
    linked_member_for_discord_user,
    list_onboarding_volunteers,
    mark_onboarding_reminder_failed,
    mark_onboarding_reminder_sent,
    normalize_onboarder_username,
    upsert_onboarding_volunteer,
)

logger = logging.getLogger(__name__)


class OnboardingRemindersCog(commands.Cog):
    """Keep the onboarding queue moving without assigning people automatically."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._reminder_task: asyncio.Task[None] | None = None

    async def cog_unload(self) -> None:
        if self._reminder_task is not None:
            self._reminder_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._reminder_task is None and settings.onboarding_reminders_enabled:
            self._reminder_task = asyncio.create_task(self._reminder_loop())

    @app_commands.command(
        name="onboarding-volunteer",
        description="Join or update the willing-onboarder registry.",
    )
    @app_commands.describe(timezone_name="IANA timezone, e.g. Asia/Tokyo")
    async def onboarding_volunteer(
        self,
        interaction: discord.Interaction,
        timezone_name: str,
        max_active_assignments: int | None = None,
    ) -> None:
        """Let a linked member opt in to receiving onboarding assignments."""
        member = await asyncio.to_thread(
            linked_member_for_discord_user,
            settings,
            str(interaction.user.id),
        )
        if member is None:
            await interaction.response.send_message(
                "Your Discord account must be linked to a current 508 member profile first.",
                ephemeral=True,
            )
            return
        try:
            await asyncio.to_thread(
                upsert_onboarding_volunteer,
                settings,
                crm_contact_id=str(member["crm_contact_id"]),
                timezone_name=timezone_name,
                availability=VolunteerAvailability.AVAILABLE,
                max_active_assignments=max_active_assignments,
            )
        except ValueError as exc:
            await interaction.response.send_message(
                f"Could not register: `{exc}`.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "You are available for onboarding assignments. Use `/pause-onboarding-volunteering` whenever you need a break.",
            ephemeral=True,
        )

    @app_commands.command(
        name="pause-onboarding-volunteering",
        description="Pause new onboarding assignments while keeping current ones.",
    )
    async def pause_onboarding_volunteering(
        self, interaction: discord.Interaction
    ) -> None:
        await self._set_self_availability(interaction, VolunteerAvailability.PAUSED)

    @app_commands.command(
        name="resume-onboarding-volunteering",
        description="Resume receiving new onboarding assignments.",
    )
    async def resume_onboarding_volunteering(
        self, interaction: discord.Interaction
    ) -> None:
        await self._set_self_availability(interaction, VolunteerAvailability.AVAILABLE)

    async def _set_self_availability(
        self,
        interaction: discord.Interaction,
        availability: VolunteerAvailability,
    ) -> None:
        member = await asyncio.to_thread(
            linked_member_for_discord_user,
            settings,
            str(interaction.user.id),
        )
        if member is None:
            await interaction.response.send_message(
                "Your Discord account is not linked to a current member profile.",
                ephemeral=True,
            )
            return
        timezone_name = str(member.get("timezone") or "UTC")
        try:
            await asyncio.to_thread(
                upsert_onboarding_volunteer,
                settings,
                crm_contact_id=str(member["crm_contact_id"]),
                timezone_name=timezone_name,
                availability=availability,
            )
        except ValueError as exc:
            await interaction.response.send_message(
                f"Could not update availability: `{exc}`.", ephemeral=True
            )
            return
        verb = "paused" if availability is VolunteerAvailability.PAUSED else "resumed"
        await interaction.response.send_message(
            f"New onboarding assignments are {verb}. Existing candidate reminders are unchanged.",
            ephemeral=True,
        )

    async def _reminder_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._send_due_reminders()
            except Exception:
                logger.exception("Onboarding reminder loop failed")
            await asyncio.sleep(settings.onboarding_reminder_check_seconds)

    async def _send_due_reminders(self) -> None:
        channel_id = str(
            settings.discord_onboarding_volunteers_channel_id or ""
        ).strip()
        if not channel_id.isdigit():
            logger.warning(
                "Onboarding reminders enabled but DISCORD_ONBOARDING_VOLUNTEERS_CHANNEL_ID is not configured"
            )
            return
        due_rows = await asyncio.to_thread(
            claim_due_onboarding_reminders,
            settings,
            stale_days=settings.onboarding_reminder_stale_days,
            repeat_days=settings.onboarding_reminder_repeat_days,
        )
        if not due_rows:
            return
        selected = [row for row in due_rows if row.get("stage") == "selected"]
        assigned = [row for row in due_rows if row.get("stage") != "selected"]
        if selected:
            await self._send_selected_digest(int(channel_id), selected)
        if assigned:
            await self._send_onboarder_dms(assigned)

    async def _send_selected_digest(
        self, channel_id: int, rows: list[dict[str, Any]]
    ) -> None:
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
                channel_id
            )
            raw_send = getattr(channel, "send", None)
            if not callable(raw_send):
                raise TypeError(
                    "configured onboarding volunteers target is not messageable"
                )
            send = cast(Callable[..., Awaitable[discord.Message]], raw_send)
            body = "\n".join(f"• {self._candidate_summary(row)}" for row in rows[:25])
            if len(rows) > 25:
                body += f"\n• …and {len(rows) - 25} more"
            message = await send(
                "**Onboarding volunteers needed** — these selected candidates have had no onboarding activity for at least a week:\n"
                + body,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            logger.warning("Failed sending selected onboarding digest: %s", exc)
            await self._mark_failed(rows, exc)
            return
        await self._mark_sent(rows, str(message.id))

    async def _send_onboarder_dms(self, rows: list[dict[str, Any]]) -> None:
        volunteers = await asyncio.to_thread(list_onboarding_volunteers, settings)
        volunteer_by_username = {
            str(volunteer.get("username")): volunteer
            for volunteer in volunteers
            if volunteer.get("discord_user_id")
        }
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            username = normalize_onboarder_username(str(row.get("onboarder") or ""))
            if username:
                groups.setdefault(username, []).append(row)
            else:
                await self._mark_failed([row], ValueError("candidate_has_no_onboarder"))
        for username, grouped_rows in groups.items():
            volunteer = volunteer_by_username.get(username)
            if volunteer is None:
                await self._mark_failed(
                    grouped_rows, ValueError("onboarder_not_discord_linked")
                )
                continue
            try:
                user_id = int(str(volunteer["discord_user_id"]))
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                body = "\n".join(
                    f"• {self._candidate_summary(row)}" for row in grouped_rows[:25]
                )
                message = await user.send(
                    "**Onboarding reminder** — please update these inactive candidates when you can:\n"
                    + body,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as exc:
                logger.warning(
                    "Failed sending onboarding reminder DM to %s: %s", username, exc
                )
                await self._mark_failed(grouped_rows, exc)
                continue
            await self._mark_sent(grouped_rows, str(message.id))

    async def _mark_sent(self, rows: list[dict[str, Any]], message_id: str) -> None:
        for row in rows:
            await asyncio.to_thread(
                mark_onboarding_reminder_sent,
                settings,
                person_id=str(row["person_id"]),
                stage=str(row["stage"]),
                activity_at=row["activity_at"],
                reminder_number=int(row["reminder_number"]),
                message_id=message_id,
            )

    async def _mark_failed(self, rows: list[dict[str, Any]], error: Exception) -> None:
        for row in rows:
            await asyncio.to_thread(
                mark_onboarding_reminder_failed,
                settings,
                person_id=str(row["person_id"]),
                stage=str(row["stage"]),
                activity_at=row["activity_at"],
                reminder_number=int(row["reminder_number"]),
                error=str(error),
            )

    def _candidate_summary(self, row: dict[str, Any]) -> str:
        roles = row.get("professional_roles") or []
        role_text = ", ".join(str(role) for role in roles[:2]) or "Role unknown"
        seniority = str(row.get("seniority") or "Seniority unknown")
        location = (
            ", ".join(
                str(value)
                for value in (
                    row.get("address_city"),
                    row.get("address_state"),
                    row.get("address_country"),
                )
                if value
            )
            or "Location unknown"
        )
        activity_at = row.get("activity_at")
        inactive_days = 0
        if isinstance(activity_at, datetime):
            inactive_days = max(0, (datetime.now(timezone.utc) - activity_at).days)
        name = discord.utils.escape_markdown(str(row.get("name") or "Candidate"))
        crm_contact_id = str(row.get("crm_contact_id") or "")
        link = ""
        if crm_contact_id and settings.espo_base_url:
            link = f" <{settings.espo_base_url.rstrip('/')}/#Contact/view/{crm_contact_id}>"
        return f"**{name}** — {location}; {role_text}; {seniority}; {inactive_days} days inactive.{link}"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OnboardingRemindersCog(bot))
