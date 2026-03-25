"""Authenticated internal HTTP routes for the Discord bot."""

import logging
import secrets
from typing import Any

from aiohttp import web
import discord
from discord.ext import commands
from pydantic import BaseModel, ValidationError

from five08.discord_bot.config import settings

logger = logging.getLogger(__name__)


class MemberAgreementRoleRequest(BaseModel):
    """Internal payload for granting Member role after agreement signing."""

    discord_user_id: str
    contact_id: str | None = None
    contact_name: str | None = None
    submission_id: int | None = None
    completed_at: str | None = None


class InternalAPIRoutes:
    """Authenticated bot-internal automation routes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def register(self, app: web.Application) -> None:
        """Register internal routes on the shared aiohttp app."""
        app.router.add_post(
            "/internal/member-agreements/member-role",
            self.member_agreement_role_handler,
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
