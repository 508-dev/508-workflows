"""ERP project lookup commands for Discord."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin
from five08.discord_bot.utils.role_decorators import (
    check_user_roles_with_hierarchy,
    require_role,
)
from five08.project_discord_channels import (
    ProjectDiscordChannelConflict,
    ProjectPaymentDiscordDeliveryClaimStatus,
    claim_project_payment_discord_delivery,
    mark_project_payment_discord_delivery_failed,
    mark_project_payment_discord_delivery_sent,
    record_project_discord_channel_verification,
    register_project_discord_channel,
    renew_project_payment_discord_delivery_lease,
    unregister_project_discord_channel,
)
from five08.project_payments import get_project_payment_notification_delivery_context
from five08.projects import list_dashboard_projects, project_viewer_emails_for_discord

logger = logging.getLogger(__name__)


class ProjectsCog(DiscordAuditCogMixin, commands.Cog, name="Projects"):
    """Read-only ERP project status views."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()

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

    @staticmethod
    def _project_choice_name(project: dict[str, Any]) -> str:
        """Build a bounded autocomplete label without leaking long ERP fields."""
        display_name = str(
            project.get("display_name")
            or project.get("erpnext_project_id")
            or "Project"
        ).strip()
        customer = str(project.get("customer") or "").strip()
        label = f"{display_name} — {customer}" if customer else display_name
        return label[:100]

    def _is_configured_guild(self, guild_id: str | int) -> bool:
        """Fail closed unless this is the bot's configured/only guild.

        Discord role names are guild-local. A role named ``Steering Committee``
        in another guild must never authorize access to ERP project payments.
        """
        configured_guild_id = str(settings.discord_server_id or "").strip()
        if configured_guild_id:
            return str(guild_id) == configured_guild_id
        guilds = getattr(self.bot, "guilds", None)
        # discord.py exposes ``Client.guilds`` as a SequenceProxy, not a
        # built-in list/tuple. Treat any non-string sequence as the bot's
        # live guild collection while still failing closed for mocks/unknown
        # values that cannot establish a single authoritative guild.
        if (
            not isinstance(guilds, Sequence)
            or isinstance(guilds, (str, bytes))
            or len(guilds) != 1
        ):
            return False
        return str(getattr(guilds[0], "id", "")) == str(guild_id)

    async def project_channel_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest open local-project IDs to authorized channel registrars only."""
        roles = getattr(getattr(interaction, "user", None), "roles", [])
        guild = getattr(interaction, "guild", None)
        if (
            guild is None
            or not self._is_configured_guild(guild.id)
            or not check_user_roles_with_hierarchy(roles, ["Steering Committee"])
        ):
            # Discord invokes autocomplete before the command callback and may
            # not run the command's ``require_role`` check. Do not expose
            # private project/customer names to an unauthorized member here.
            return []
        try:
            projects = await asyncio.to_thread(
                list_dashboard_projects,
                settings,
                query=current or None,
                status="Open",
                include_all=True,
                include_roster=False,
                limit=25,
            )
        except Exception:
            logger.warning("Project-channel autocomplete lookup failed", exc_info=True)
            return []
        return [
            app_commands.Choice(
                name=self._project_choice_name(project),
                value=str(project["id"]),
            )
            for project in projects
            if project.get("id")
        ][:25]

    @staticmethod
    def _target_text_channel(
        interaction: discord.Interaction,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel | None:
        """Resolve an explicit target or the current text channel only."""
        candidate = channel or interaction.channel
        return candidate if isinstance(candidate, discord.TextChannel) else None

    @staticmethod
    def _private_channel_error(
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> str | None:
        """Revalidate privacy and live bot permissions before storing/sending."""
        if channel.guild.id != guild.id:
            return "channel_wrong_guild"
        if channel.permissions_for(guild.default_role).view_channel:
            return "channel_is_public"
        bot_member = guild.me
        if bot_member is None:
            return "bot_member_unresolved"
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel:
            return "missing_view_channel_permission"
        if not permissions.send_messages:
            return "missing_send_messages_permission"
        if not permissions.read_message_history:
            return "missing_read_message_history_permission"
        return None

    @staticmethod
    def _payment_message_content(
        *,
        notification_id: str,
        amount: str,
        currency: str | None,
        posted_at: str | None,
    ) -> str:
        """Render a minimal private-channel receipt without payment-party data."""
        try:
            normalized_amount = Decimal(amount)
            amount_text = format(normalized_amount, ",.2f")
        except (InvalidOperation, ValueError):
            amount_text = amount.strip() or "an unspecified amount"
        money = f"{currency} {amount_text}" if currency else amount_text
        lines = [f"✅ Payment received for this project: **{money}**."]
        if posted_at:
            lines.append(f"Posted: {posted_at}")
        # A durable marker lets a retry find a message accepted by Discord when
        # the worker timed out before it could store the returned message ID.
        lines.append(f"<!-- project-payment-notification:{notification_id} -->")
        return "\n".join(lines)

    async def _resolve_text_channel(
        self,
        *,
        channel_id: str,
    ) -> tuple[discord.TextChannel | None, bool]:
        """Resolve a text channel and distinguish retryable Discord failures."""
        try:
            numeric_channel_id = int(channel_id)
        except ValueError:
            return None, False
        channel = self.bot.get_channel(numeric_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(numeric_channel_id)
            except (discord.NotFound, discord.Forbidden):
                return None, False
            except discord.HTTPException:
                logger.warning(
                    "Failed fetching project payment channel id=%s",
                    channel_id,
                    exc_info=True,
                )
                return None, True
        return (
            (channel, False)
            if isinstance(channel, discord.TextChannel)
            else (None, False)
        )

    async def _record_project_channel_verification(
        self,
        *,
        mapping_id: str,
        error: str | None = None,
    ) -> None:
        """Best-effort verification telemetry must not mask delivery outcomes."""
        try:
            await asyncio.to_thread(
                record_project_discord_channel_verification,
                settings,
                mapping_id=mapping_id,
                error=error,
            )
        except Exception:
            logger.warning(
                "Failed recording project payment channel verification mapping_id=%s",
                mapping_id,
                exc_info=True,
            )

    async def _fail_project_payment_delivery(
        self,
        *,
        notification_id: str,
        project_discord_channel_id: str,
        lease_token: str,
        error: str,
    ) -> bool:
        """Release an owned bot delivery lease before a retryable/permanent reply."""
        try:
            return await asyncio.to_thread(
                mark_project_payment_discord_delivery_failed,
                settings,
                notification_id=notification_id,
                project_discord_channel_id=project_discord_channel_id,
                error=error,
                lease_token=lease_token,
            )
        except Exception:
            logger.warning(
                "Failed releasing project payment delivery lease notification_id=%s",
                notification_id,
                exc_info=True,
            )
            return False

    async def post_project_payment_notification(
        self,
        *,
        notification_id: str,
        worker_lease_token: str,
    ) -> tuple[dict[str, Any], int]:
        """Deliver one canonical notification after live eligibility checks.

        The worker intentionally supplies only the durable outbox ID. Every
        money, project, and Discord-channel value is loaded from Postgres so a
        caller of this internal endpoint cannot forge a payment announcement.
        """
        try:
            context = await asyncio.to_thread(
                get_project_payment_notification_delivery_context,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
            )
        except Exception:
            logger.warning(
                "Failed loading canonical project payment notification id=%s",
                notification_id,
                exc_info=True,
            )
            return {"error": "payment_notification_context_unavailable"}, 503
        if context is None:
            return {"error": "payment_notification_not_eligible"}, 409

        try:
            guild_id = int(context.guild_id)
        except ValueError:
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id,
                error="invalid_registered_guild_id",
            )
            return {"error": "invalid_registered_guild_id"}, 409
        if not self._is_configured_guild(guild_id):
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id,
                error="payment_channel_wrong_guild",
            )
            return {"error": "payment_channel_wrong_guild"}, 403
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id,
                error="guild_not_found",
            )
            # The bot cache can be empty during startup/reconnect, so let the
            # worker retry rather than dead-lettering a valid mapping.
            return {"error": "guild_not_ready"}, 503
        channel, channel_lookup_retryable = await self._resolve_text_channel(
            channel_id=context.channel_id
        )
        if channel is None:
            error = (
                "channel_lookup_failed"
                if channel_lookup_retryable
                else "channel_not_found_or_not_text"
            )
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id,
                error=error,
            )
            return {"error": error}, 503 if channel_lookup_retryable else 404
        channel_error = self._private_channel_error(guild, channel)
        if channel_error is not None:
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id,
                error=channel_error,
            )
            if channel_error == "bot_member_unresolved":
                return {"error": channel_error}, 503
            return {"error": channel_error}, 403

        claimed_mapping_id = context.project_discord_channel_id
        try:
            delivery_claim = await asyncio.to_thread(
                claim_project_payment_discord_delivery,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
                project_discord_channel_id=claimed_mapping_id,
            )
        except Exception:
            logger.warning(
                "Failed claiming project payment Discord delivery id=%s",
                notification_id,
                exc_info=True,
            )
            return {"error": "payment_notification_delivery_claim_unavailable"}, 503
        if (
            delivery_claim.status
            is ProjectPaymentDiscordDeliveryClaimStatus.ALREADY_DELIVERED
        ):
            await self._record_project_channel_verification(
                mapping_id=context.project_discord_channel_id
            )
            return {
                "status": "already_delivered",
                "message_id": delivery_claim.discord_message_id,
            }, 200
        if (
            delivery_claim.status
            is ProjectPaymentDiscordDeliveryClaimStatus.SCOPE_MISMATCH
        ):
            return {"error": "payment_notification_not_eligible"}, 409
        if (
            delivery_claim.status
            is not ProjectPaymentDiscordDeliveryClaimStatus.CLAIMED
        ):
            return {"error": "payment_notification_delivery_in_progress"}, 503
        lease_token = str(delivery_claim.lease_token or "").strip()
        if not lease_token:
            logger.error(
                "Project payment delivery claim missing lease token notification_id=%s",
                notification_id,
            )
            return {"error": "payment_notification_delivery_lease_missing"}, 503

        # The mapping/project can be revoked after the first read or even while
        # the receipt claim is in flight. Re-read the DB-authorized context
        # before any Discord side effect, bound to the exact mapping we claimed.
        try:
            context = await asyncio.to_thread(
                get_project_payment_notification_delivery_context,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
                project_discord_channel_id=claimed_mapping_id,
            )
        except Exception:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="notification_context_refresh_failed",
            )
            return {"error": "payment_notification_context_unavailable"}, 503
        if context is None:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="notification_no_longer_eligible",
            )
            return {"error": "payment_notification_not_eligible"}, 409

        marker = f"<!-- project-payment-notification:{notification_id} -->"
        try:
            lease_renewed = await asyncio.to_thread(
                renew_project_payment_discord_delivery_lease,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
            )
        except Exception:
            logger.warning(
                "Failed renewing project payment delivery lease before history id=%s",
                notification_id,
                exc_info=True,
            )
            return {"error": "payment_notification_delivery_lease_unavailable"}, 503
        if not lease_renewed:
            return {"error": "payment_notification_delivery_lease_lost"}, 503
        try:
            async for message in channel.history(limit=100, oldest_first=False):
                if marker in str(message.content or ""):
                    receipt_saved = await asyncio.to_thread(
                        mark_project_payment_discord_delivery_sent,
                        settings,
                        notification_id=notification_id,
                        project_discord_channel_id=claimed_mapping_id,
                        discord_message_id=str(message.id),
                        lease_token=lease_token,
                    )
                    if not receipt_saved:
                        return {
                            "error": "payment_notification_delivery_lease_lost"
                        }, 503
                    await self._record_project_channel_verification(
                        mapping_id=claimed_mapping_id
                    )
                    return {
                        "status": "already_delivered",
                        "message_id": str(message.id),
                    }, 200
        except discord.Forbidden:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="message_history_forbidden",
            )
            return {"error": "message_history_forbidden"}, 403
        except discord.HTTPException:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="message_history_lookup_failed",
            )
            return {"error": "message_history_lookup_failed"}, 503

        # Re-check eligibility and renew immediately before the non-idempotent
        # Discord write. This narrows the unavoidable DB-to-Discord race and
        # never uses values supplied by the worker's HTTP request.
        try:
            context = await asyncio.to_thread(
                get_project_payment_notification_delivery_context,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
                project_discord_channel_id=claimed_mapping_id,
            )
        except Exception:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="notification_context_refresh_failed",
            )
            return {"error": "payment_notification_context_unavailable"}, 503
        if context is None:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="notification_no_longer_eligible",
            )
            return {"error": "payment_notification_not_eligible"}, 409
        try:
            lease_renewed = await asyncio.to_thread(
                renew_project_payment_discord_delivery_lease,
                settings,
                notification_id=notification_id,
                worker_lease_token=worker_lease_token,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
            )
        except Exception:
            logger.warning(
                "Failed renewing project payment delivery lease before send id=%s",
                notification_id,
                exc_info=True,
            )
            return {"error": "payment_notification_delivery_lease_unavailable"}, 503
        if not lease_renewed:
            return {"error": "payment_notification_delivery_lease_lost"}, 503

        try:
            # Discord limits a nonce to 25 characters. Keep it deterministic
            # for diagnostics without treating it as our correctness boundary;
            # the durable receipt and hidden marker remain the recovery source.
            discord_nonce = (
                "pp-" + hashlib.sha256(notification_id.encode("utf-8")).hexdigest()[:22]
            )
            message = await channel.send(
                self._payment_message_content(
                    notification_id=notification_id,
                    amount=str(context.amount),
                    currency=context.currency,
                    posted_at=(
                        context.posted_at.isoformat()
                        if context.posted_at is not None
                        else None
                    ),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
                nonce=discord_nonce,
            )
        except discord.Forbidden:
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="payment_notification_forbidden",
            )
            return {"error": "payment_notification_forbidden"}, 403
        except discord.HTTPException as exc:
            logger.warning(
                "Failed posting project payment notification id=%s: %s",
                notification_id,
                exc,
            )
            await self._fail_project_payment_delivery(
                notification_id=notification_id,
                project_discord_channel_id=claimed_mapping_id,
                lease_token=lease_token,
                error="payment_notification_failed",
            )
            return {"error": "payment_notification_failed"}, 503

        receipt_saved = await asyncio.to_thread(
            mark_project_payment_discord_delivery_sent,
            settings,
            notification_id=notification_id,
            project_discord_channel_id=claimed_mapping_id,
            discord_message_id=str(message.id),
            lease_token=lease_token,
        )
        if not receipt_saved:
            return {"error": "payment_notification_delivery_lease_lost"}, 503
        await self._record_project_channel_verification(mapping_id=claimed_mapping_id)
        return {
            "status": "sent",
            "message_id": str(message.id),
        }, 200

    @app_commands.command(
        name="register-project-channel",
        description="Register a private Discord channel for a project's payment alerts.",
    )
    @app_commands.describe(
        project="Open ERP project to receive payment alerts.",
        channel="Private text channel. Defaults to the current channel.",
    )
    @app_commands.autocomplete(project=project_channel_autocomplete)
    @require_role("Steering Committee")
    async def register_project_channel(
        self,
        interaction: discord.Interaction,
        project: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        """Validate and register a live private text channel for a project."""
        guild = interaction.guild
        target_channel = self._target_text_channel(interaction, channel)
        if guild is None or target_channel is None:
            await interaction.response.send_message(
                "⚠️ Choose a private text channel inside a server.", ephemeral=True
            )
            return
        if not self._is_configured_guild(guild.id):
            await interaction.response.send_message(
                "⚠️ Project payment channels can only be registered in the configured server.",
                ephemeral=True,
            )
            return
        channel_error = self._private_channel_error(guild, target_channel)
        if channel_error is not None:
            await interaction.response.send_message(
                "⚠️ This must be a private text channel that the bot can view and post in.",
                ephemeral=True,
            )
            self._audit_command_safe(
                interaction=interaction,
                action="project_channel.register",
                result="denied",
                metadata={
                    "reason": channel_error,
                    "channel_id": str(target_channel.id),
                },
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            active_projects = await asyncio.to_thread(
                list_dashboard_projects,
                settings,
                project_id=project,
                status="Open",
                include_all=True,
                include_roster=False,
                limit=1,
            )
        except Exception:
            logger.warning(
                "Failed validating project channel registration", exc_info=True
            )
            active_projects = []
        if not active_projects:
            await interaction.followup.send(
                "⚠️ Choose an active ERP project from the command suggestions.",
                ephemeral=True,
            )
            self._audit_command_safe(
                interaction=interaction,
                action="project_channel.register",
                result="denied",
                metadata={"reason": "project_not_open", "project_id": project},
            )
            return

        try:
            mapping, created = await asyncio.to_thread(
                register_project_discord_channel,
                settings,
                project_id=project,
                guild_id=str(guild.id),
                channel_id=str(target_channel.id),
                channel_name=target_channel.name,
                registered_by_discord_user_id=str(interaction.user.id),
            )
        except ProjectDiscordChannelConflict:
            await interaction.followup.send(
                "⚠️ This channel is already registered to another project.",
                ephemeral=True,
            )
            self._audit_command_safe(
                interaction=interaction,
                action="project_channel.register",
                result="denied",
                metadata={
                    "reason": "channel_already_owned",
                    "channel_id": str(target_channel.id),
                },
            )
            return
        except Exception:
            logger.warning("Failed registering project payment channel", exc_info=True)
            await interaction.followup.send(
                "❌ Could not register this project channel. Please try again.",
                ephemeral=True,
            )
            return

        status = "registered" if created else "already registered"
        await interaction.followup.send(
            f"✅ <#{target_channel.id}> is {status} for payment alerts.",
            ephemeral=True,
        )
        self._audit_command_safe(
            interaction=interaction,
            action="project_channel.register",
            result="success",
            resource_type="project_discord_channel",
            resource_id=mapping.id,
            metadata={
                "project_id": project,
                "guild_id": str(guild.id),
                "channel_id": str(target_channel.id),
                "created": created,
            },
        )

    @app_commands.command(
        name="unregister-project-channel",
        description="Stop payment alerts in a private project channel.",
    )
    @app_commands.describe(
        channel="Private text channel. Defaults to the current channel."
    )
    @require_role("Steering Committee")
    async def unregister_project_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ) -> None:
        """Deactivate one project payment channel mapping."""
        guild = interaction.guild
        target_channel = self._target_text_channel(interaction, channel)
        if guild is None or target_channel is None:
            await interaction.response.send_message(
                "⚠️ Choose a text channel inside a server.", ephemeral=True
            )
            return
        if not self._is_configured_guild(guild.id):
            await interaction.response.send_message(
                "⚠️ Project payment channels can only be managed in the configured server.",
                ephemeral=True,
            )
            return
        if target_channel.guild.id != guild.id:
            await interaction.response.send_message(
                "⚠️ Choose a channel in this server.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            removed = await asyncio.to_thread(
                unregister_project_discord_channel,
                settings,
                guild_id=str(guild.id),
                channel_id=str(target_channel.id),
            )
        except Exception:
            logger.warning(
                "Failed unregistering project payment channel", exc_info=True
            )
            await interaction.followup.send(
                "❌ Could not unregister this project channel. Please try again.",
                ephemeral=True,
            )
            return
        if removed:
            await interaction.followup.send(
                f"✅ Payment alerts are disabled for <#{target_channel.id}>.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "ℹ️ This channel was not registered for payment alerts.",
                ephemeral=True,
            )
        self._audit_command_safe(
            interaction=interaction,
            action="project_channel.unregister",
            result="success",
            metadata={
                "guild_id": str(guild.id),
                "channel_id": str(target_channel.id),
                "removed": removed,
            },
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProjectsCog(bot))
