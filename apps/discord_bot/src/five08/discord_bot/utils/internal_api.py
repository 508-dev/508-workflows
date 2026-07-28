"""Authenticated internal HTTP routes for the Discord bot."""

import asyncio
import logging
import secrets
from typing import Any

from aiohttp import web
import discord
from discord.ext import commands
from pydantic import BaseModel, ValidationError

from five08.discord_bot.config import settings
from five08.engagements import (
    EngagementStatus,
    normalize_engagement_status,
    parse_status_from_title,
    strip_status_from_title,
)

logger = logging.getLogger(__name__)


class MemberAgreementRoleRequest(BaseModel):
    """Internal payload for granting Member role after agreement signing."""

    discord_user_id: str
    contact_id: str | None = None
    contact_name: str | None = None
    submission_id: int | None = None
    completed_at: str | None = None


class GigThreadStatusRequest(BaseModel):
    """Internal payload for mirroring dashboard gig status to Discord."""

    thread_id: str
    status: str


class PostJobLeadRequest(BaseModel):
    """Internal payload for promoting a qualified lead to a gigs forum."""

    lead_id: str
    reviewer_discord_user_id: str
    channel_id: str | None = None
    tags: str | None = None
    engagement_status: str = EngagementStatus.LEAD.value


class StageJobLeadRequest(BaseModel):
    """Internal payload for posting a sourced lead to the holding forum."""

    lead_id: str
    reviewer_discord_user_id: str


class InternalAPIRoutes:
    """Authenticated bot-internal automation routes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._pending_gig_thread_statuses: dict[int, GigThreadStatusRequest] = {}
        self._gig_thread_status_tasks: dict[int, asyncio.Task[None]] = {}

    def register(self, app: web.Application) -> None:
        """Register internal routes on the shared aiohttp app."""
        app.router.add_post(
            "/internal/member-agreements/member-role",
            self.member_agreement_role_handler,
        )
        app.router.add_post(
            "/internal/jobs/thread-status",
            self.gig_thread_status_handler,
        )
        app.router.add_post(
            "/internal/jobs/job-leads/post",
            self.post_job_lead_handler,
        )
        app.router.add_post(
            "/internal/jobs/job-leads/stage",
            self.stage_job_lead_handler,
        )
        app.router.add_get(
            "/internal/jobs/channels",
            self.job_channels_handler,
        )
        app.router.add_get(
            "/internal/diagnostics/discord",
            self.discord_diagnostics_handler,
        )

    @staticmethod
    def _is_authorized(request: web.Request) -> bool:
        api_secret = str(settings.api_shared_secret or "").strip()
        if not api_secret:
            logger.error(
                "Rejecting bot internal request: API_SHARED_SECRET is not configured"
            )
            return False

        provided_secret = request.headers.get("X-API-Secret", "")
        if secrets.compare_digest(provided_secret, api_secret):
            return True

        logger.warning("Rejecting bot internal request: invalid X-API-Secret")
        return False

    def _resolve_target_guild(self) -> discord.Guild | None:
        configured_guild_id = str(settings.discord_server_id or "").strip()
        if configured_guild_id:
            try:
                return self.bot.get_guild(int(configured_guild_id))
            except ValueError:
                return None

        if len(self.bot.guilds) == 1:
            return self.bot.guilds[0]
        return None

    @staticmethod
    def _member_role_from_guild(guild: discord.Guild) -> discord.Role | None:
        for role in guild.roles:
            if role.name.casefold() == "member":
                return role
        return None

    async def _grant_member_role(
        self,
        payload: MemberAgreementRoleRequest,
    ) -> tuple[dict[str, Any], int]:
        guild = self._resolve_target_guild()
        if guild is None:
            return {"error": "guild_not_found"}, 404

        member_role = self._member_role_from_guild(guild)
        if member_role is None:
            return {"error": "member_role_not_found", "guild_id": str(guild.id)}, 404

        try:
            discord_user_id = int(payload.discord_user_id)
        except ValueError:
            return {"error": "invalid_discord_user_id"}, 400

        member = guild.get_member(discord_user_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_user_id)
            except discord.NotFound:
                member = None
            except discord.Forbidden:
                logger.warning(
                    "Failed fetching guild member guild_id=%s contact_id=%s: forbidden",
                    guild.id,
                    payload.contact_id,
                )
                return {
                    "error": "member_lookup_forbidden",
                    "guild_id": str(guild.id),
                    "discord_user_id": str(payload.discord_user_id),
                }, 403
            except discord.HTTPException as exc:
                logger.warning(
                    "Failed fetching guild member guild_id=%s contact_id=%s: %s",
                    guild.id,
                    payload.contact_id,
                    exc,
                )
                return {
                    "error": "member_lookup_failed",
                    "guild_id": str(guild.id),
                    "discord_user_id": str(payload.discord_user_id),
                }, 502
        if member is None:
            return {
                "error": "member_not_in_guild",
                "guild_id": str(guild.id),
                "discord_user_id": str(payload.discord_user_id),
            }, 404

        if member_role in member.roles:
            return {
                "status": "already_present",
                "guild_id": str(guild.id),
                "discord_user_id": str(payload.discord_user_id),
                "role": member_role.name,
            }, 200

        bot_member = guild.me
        if bot_member is None:
            return {"error": "bot_member_unresolved", "guild_id": str(guild.id)}, 503

        if not bot_member.guild_permissions.manage_roles:
            return {"error": "missing_manage_roles_permission"}, 403

        if not (bot_member.top_role > member.top_role):
            return {"error": "target_hierarchy_blocked"}, 403

        if not (bot_member.top_role > member_role):
            return {"error": "role_hierarchy_blocked"}, 403

        reason_suffix = []
        if payload.contact_id:
            reason_suffix.append(f"contact {payload.contact_id}")
        if payload.submission_id is not None:
            reason_suffix.append(f"submission {payload.submission_id}")
        reason = "Member agreement signed via Docuseal"
        if reason_suffix:
            reason += f" ({', '.join(reason_suffix)})"

        try:
            await member.add_roles(member_role, reason=reason)
        except discord.HTTPException as exc:
            logger.error(
                "Failed granting Member role guild_id=%s user_id=%s: %s",
                guild.id,
                payload.discord_user_id,
                exc,
            )
            return {"error": "discord_http_exception", "detail": str(exc)}, 502

        return {
            "status": "applied",
            "guild_id": str(guild.id),
            "discord_user_id": str(payload.discord_user_id),
            "role": member_role.name,
        }, 200

    async def member_agreement_role_handler(self, request: web.Request) -> web.Response:
        """Grant the Member role to one linked Discord user after signing."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            payload_data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        if not isinstance(payload_data, dict):
            return web.json_response({"error": "payload_must_be_object"}, status=400)

        try:
            payload = MemberAgreementRoleRequest.model_validate(payload_data)
        except (ValidationError, TypeError) as exc:
            return web.json_response(
                {"error": "invalid_payload", "detail": str(exc)},
                status=400,
            )

        result, status_code = await self._grant_member_role(payload)
        return web.json_response(result, status=status_code)

    async def _enqueue_gig_thread_status(
        self,
        payload: GigThreadStatusRequest,
    ) -> tuple[dict[str, Any], int]:
        try:
            thread_id = int(payload.thread_id)
        except ValueError:
            return {"error": "invalid_thread_id"}, 400

        normalized_status = normalize_engagement_status(payload.status)
        self._pending_gig_thread_statuses[thread_id] = GigThreadStatusRequest(
            thread_id=str(thread_id),
            status=normalized_status.value,
        )

        existing_task = self._gig_thread_status_tasks.get(thread_id)
        if existing_task is None or existing_task.done():
            self._gig_thread_status_tasks[thread_id] = asyncio.create_task(
                self._run_gig_thread_status_sync(thread_id)
            )

        return {
            "status": "queued",
            "thread_id": str(thread_id),
            "target_status": normalized_status.value,
        }, 202

    async def _run_gig_thread_status_sync(self, thread_id: int) -> None:
        """Coalesce dashboard status changes and sync the latest one to Discord."""
        current_task = asyncio.current_task()
        try:
            while True:
                await asyncio.sleep(2)
                payload = self._pending_gig_thread_statuses.pop(thread_id, None)
                if payload is None:
                    return

                await self._apply_gig_thread_status_with_retries(payload)
                if thread_id not in self._pending_gig_thread_statuses:
                    return
        finally:
            if self._gig_thread_status_tasks.get(thread_id) is current_task:
                self._gig_thread_status_tasks.pop(thread_id, None)
                if thread_id in self._pending_gig_thread_statuses:
                    self._gig_thread_status_tasks[thread_id] = asyncio.create_task(
                        self._run_gig_thread_status_sync(thread_id)
                    )

    async def _apply_gig_thread_status_with_retries(
        self,
        payload: GigThreadStatusRequest,
    ) -> None:
        retry_delays = (5, 30, 120)
        for attempt, retry_delay in enumerate((*retry_delays, None), start=1):
            result, status_code = await self._update_gig_thread_status(payload)
            if status_code < 500:
                if status_code >= 400:
                    logger.warning(
                        "Discord gig thread status sync rejected thread_id=%s "
                        "status=%s result=%s",
                        payload.thread_id,
                        status_code,
                        result,
                    )
                return

            logger.warning(
                "Discord gig thread status sync failed thread_id=%s attempt=%s "
                "status=%s result=%s",
                payload.thread_id,
                attempt,
                status_code,
                result,
            )
            if retry_delay is None:
                return
            await asyncio.sleep(retry_delay)

    async def _update_gig_thread_status(
        self,
        payload: GigThreadStatusRequest,
    ) -> tuple[dict[str, Any], int]:
        try:
            thread_id = int(payload.thread_id)
        except ValueError:
            return {"error": "invalid_thread_id"}, 400

        normalized_status = normalize_engagement_status(payload.status)
        status_marker = normalized_status.value.upper()

        channel = self.bot.get_channel(thread_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(thread_id)
            except discord.NotFound:
                return {"error": "thread_not_found"}, 404
            except discord.Forbidden:
                return {"error": "thread_lookup_forbidden"}, 403
            except discord.HTTPException as exc:
                logger.warning("Failed fetching gig thread %s: %s", thread_id, exc)
                return {"error": "thread_lookup_failed"}, 502

        if not isinstance(channel, discord.Thread):
            return {"error": "channel_is_not_thread"}, 400

        bot_member = channel.guild.me if channel.guild is not None else None
        if bot_member is None:
            return {"error": "bot_member_unresolved"}, 503

        permissions = channel.permissions_for(bot_member)
        permission_payload = {
            "manage_threads": permissions.manage_threads,
            "view_channel": permissions.view_channel,
            "send_messages_in_threads": permissions.send_messages_in_threads,
            "archived": channel.archived,
            "locked": channel.locked,
        }
        if not permissions.manage_threads:
            return {
                "error": "missing_manage_threads_permission",
                **permission_payload,
            }, 403

        raw_title = str(channel.name or "").strip()
        stripped_title = strip_status_from_title(raw_title)
        if (
            parse_status_from_title(raw_title) is not EngagementStatus.UNKNOWN
            and stripped_title == raw_title
        ):
            base_title = ""
        else:
            base_title = stripped_title
        base_title = base_title.strip() or f"Discord gig {thread_id}"
        next_name = f"[{status_marker}] {base_title}"[:100]
        should_close_thread = normalized_status in {
            EngagementStatus.LOST,
            EngagementStatus.DUPLICATE,
        }
        was_closed_thread = parse_status_from_title(raw_title) in {
            EngagementStatus.LOST,
            EngagementStatus.DUPLICATE,
        }
        is_locked = bool(getattr(channel, "locked", False))
        is_archived = bool(getattr(channel, "archived", False))
        needs_rename = channel.name != next_name
        needs_reopen = (
            not should_close_thread and was_closed_thread and (is_locked or is_archived)
        )
        needs_close = should_close_thread and (not is_locked or not is_archived)
        needs_unarchive_for_rename = needs_rename and is_archived
        needs_restore_closed = should_close_thread and needs_unarchive_for_rename
        if not needs_rename and not needs_close and not needs_reopen:
            return {
                "status": "unchanged",
                "thread_id": str(thread_id),
                "title": next_name,
                "closed": should_close_thread,
            }, 200

        try:
            if needs_reopen:
                await channel.edit(
                    locked=False,
                    archived=False,
                    reason="Dashboard gig status update",
                )
            elif needs_unarchive_for_rename:
                await channel.edit(
                    archived=False,
                    reason="Dashboard gig status update",
                )
            if needs_rename:
                await channel.edit(
                    name=next_name,
                    reason="Dashboard gig status update",
                )
            if needs_close or needs_restore_closed:
                await channel.edit(
                    locked=True,
                    archived=True,
                    reason="Dashboard gig status update",
                )
        except discord.Forbidden:
            return {"error": "thread_update_forbidden"}, 403
        except discord.HTTPException as exc:
            logger.warning("Failed updating gig thread %s: %s", thread_id, exc)
            return {"error": "thread_update_failed"}, 502

        return {
            "status": "updated",
            "thread_id": str(thread_id),
            "title": next_name,
            "closed": should_close_thread,
        }, 200

    async def gig_thread_status_handler(self, request: web.Request) -> web.Response:
        """Mirror a dashboard gig status update to the Discord thread title."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            payload_data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        if not isinstance(payload_data, dict):
            return web.json_response({"error": "payload_must_be_object"}, status=400)

        try:
            payload = GigThreadStatusRequest.model_validate(payload_data)
        except (ValidationError, TypeError) as exc:
            return web.json_response(
                {"error": "invalid_payload", "detail": str(exc)},
                status=400,
            )

        result, status_code = await self._enqueue_gig_thread_status(payload)
        return web.json_response(result, status=status_code)

    async def _post_job_lead(
        self,
        payload: PostJobLeadRequest,
    ) -> tuple[dict[str, Any], int]:
        jobs_cog = self.bot.get_cog("JobsCog")
        if jobs_cog is None or not hasattr(jobs_cog, "post_job_lead_to_discord"):
            return {"error": "jobs_cog_unavailable"}, 503

        result, status_code = await jobs_cog.post_job_lead_to_discord(
            lead_id=payload.lead_id,
            reviewer_discord_user_id=payload.reviewer_discord_user_id,
            channel_id=payload.channel_id,
            tags=payload.tags,
            engagement_status=normalize_engagement_status(payload.engagement_status),
            reason="Dashboard promoted qualified job lead",
        )
        return result, status_code

    async def _stage_job_lead(
        self,
        payload: StageJobLeadRequest,
    ) -> tuple[dict[str, Any], int]:
        jobs_cog = self.bot.get_cog("JobsCog")
        if jobs_cog is None or not hasattr(jobs_cog, "stage_job_lead_to_discord"):
            return {"error": "jobs_cog_unavailable"}, 503

        result, status_code = await jobs_cog.stage_job_lead_to_discord(
            lead_id=payload.lead_id,
            reviewer_discord_user_id=payload.reviewer_discord_user_id,
            reason="Dashboard staged unqualified job lead",
        )
        return result, status_code

    async def _list_job_channels(
        self,
        *,
        register_defaults: bool = True,
    ) -> tuple[dict[str, Any], int]:
        jobs_cog = self.bot.get_cog("JobsCog")
        if jobs_cog is None or not hasattr(jobs_cog, "list_registered_job_post_forums"):
            return {"error": "jobs_cog_unavailable"}, 503
        if register_defaults:
            result, status_code = await jobs_cog.list_registered_job_post_forums()
        else:
            result, status_code = await jobs_cog.list_registered_job_post_forums(
                register_defaults=False,
            )
        return result, status_code

    async def job_channels_handler(self, request: web.Request) -> web.Response:
        """Return registered jobs forums with live Discord tag metadata."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        register_defaults = str(
            request.query.get("register_defaults", "true")
        ).strip().casefold() not in {"0", "false", "no"}
        result, status_code = await self._list_job_channels(
            register_defaults=register_defaults,
        )
        return web.json_response(result, status=status_code)

    async def _get_discord_diagnostics(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[dict[str, Any], int]:
        """Delegate server role diagnostics to the read-only diagnostics cog."""
        diagnostics_cog = self.bot.get_cog("DiagnosticsCog")
        if diagnostics_cog is None or not hasattr(
            diagnostics_cog,
            "get_diagnostics_snapshot",
        ):
            return {"error": "diagnostics_cog_unavailable"}, 503
        return await diagnostics_cog.get_diagnostics_snapshot(refresh=refresh)

    async def discord_diagnostics_handler(self, request: web.Request) -> web.Response:
        """Return the configured guild's read-only role catalog for the dashboard."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        refresh = str(request.query.get("refresh", "false")).strip().casefold() in {
            "1",
            "true",
            "yes",
        }
        result, status_code = await self._get_discord_diagnostics(refresh=refresh)
        return web.json_response(result, status=status_code)

    async def post_job_lead_handler(self, request: web.Request) -> web.Response:
        """Promote a qualified sourced lead into a registered Discord gigs forum."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            payload_data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        if not isinstance(payload_data, dict):
            return web.json_response({"error": "payload_must_be_object"}, status=400)

        try:
            payload = PostJobLeadRequest.model_validate(payload_data)
        except (ValidationError, TypeError) as exc:
            return web.json_response(
                {"error": "invalid_payload", "detail": str(exc)},
                status=400,
            )

        result, status_code = await self._post_job_lead(payload)
        return web.json_response(result, status=status_code)

    async def stage_job_lead_handler(self, request: web.Request) -> web.Response:
        """Create a holding-forum thread for an unqualified sourced job lead."""
        if not self._is_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            payload_data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid_json"}, status=400)

        if not isinstance(payload_data, dict):
            return web.json_response({"error": "payload_must_be_object"}, status=400)

        try:
            payload = StageJobLeadRequest.model_validate(payload_data)
        except (ValidationError, TypeError) as exc:
            return web.json_response(
                {"error": "invalid_payload", "detail": str(exc)},
                status=400,
            )

        result, status_code = await self._stage_job_lead(payload)
        return web.json_response(result, status=status_code)
