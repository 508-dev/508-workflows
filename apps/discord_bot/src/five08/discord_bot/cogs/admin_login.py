"""Discord slash command for one-time admin dashboard login links."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.audit import DiscordAuditCogMixin

logger = logging.getLogger(__name__)


class AdminLoginCog(DiscordAuditCogMixin, commands.Cog):
    """Mint one-time dashboard login links for Discord admins."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._init_audit_logger()

    def _backend_url(self, path: str) -> str:
        return f"{settings.backend_api_base_url.rstrip('/')}{path}"

    def _backend_headers(self) -> dict[str, str]:
        if not settings.api_shared_secret:
            raise ValueError("API_SHARED_SECRET is required for backend API requests.")
        return {
            "X-API-Secret": settings.api_shared_secret,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _user_can_request_login_link(interaction: discord.Interaction) -> bool:
        member_roles = getattr(interaction.user, "roles", None)
        if member_roles is None:
            return False

        allowed_role_names = settings.discord_admin_role_names
        user_role_names = {
            role.name.casefold()
            for role in member_roles
            if isinstance(role.name, str) and role.name.strip()
        }
        return bool(user_role_names & allowed_role_names)

    async def _create_login_link(self, *, discord_user_id: str) -> tuple[str, int]:
        payload = {"discord_user_id": discord_user_id}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._backend_url("/auth/discord/links"),
                headers=self._backend_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                data: Any = {}
                try:
                    data = await response.json()
                except Exception:
                    data = {}

                if response.status == 201 and isinstance(data, dict):
                    link_url = data.get("link_url")
                    expires_in_seconds = data.get("expires_in_seconds")
                    if (
                        isinstance(link_url, str)
                        and link_url
                        and isinstance(expires_in_seconds, int)
                    ):
                        return link_url, expires_in_seconds
                    raise RuntimeError("Missing link_url or expires_in_seconds.")

                if response.status == 403 and isinstance(data, dict):
                    detail = data.get("detail")
                    if detail == "discord_user_not_admin":
                        raise PermissionError("discord_user_not_admin")

                raise RuntimeError(
                    f"Failed creating login link: status={response.status}"
                )

    def _audit(
        self,
        *,
        interaction: discord.Interaction,
        result: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._audit_command(
            interaction=interaction,
            action="auth.login_link.create",
            result=result,
            metadata=metadata or {},
            resource_type="admin_dashboard_link",
            resource_id=str(interaction.user.id),
        )

    @app_commands.command(
        name="login",
        description="Get a one-time admin dashboard login link.",
    )
    async def login(self, interaction: discord.Interaction) -> None:
        """Create and return a one-time dashboard login URL."""
        if not self._user_can_request_login_link(interaction):
            self._audit(
                interaction=interaction,
                result="denied",
                metadata={"reason": "discord_user_not_admin"},
            )
            await interaction.response.send_message(
                "❌ You are not allowed to create an admin dashboard login link.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            link_url, expires_in_seconds = await self._create_login_link(
                discord_user_id=str(interaction.user.id),
            )
        except ValueError as exc:
            self._audit(
                interaction=interaction,
                result="error",
                metadata={"reason": "missing_api_secret", "error": str(exc)},
            )
            await interaction.followup.send(
                "❌ Login link service is not configured yet (missing API secret).",
                ephemeral=True,
            )
            return
        except PermissionError:
            self._audit(
                interaction=interaction,
                result="denied",
                metadata={"reason": "discord_user_not_admin"},
            )
            await interaction.followup.send(
                "❌ You are not allowed to create an admin dashboard login link.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.error("Failed creating dashboard login link: %s", exc)
            self._audit(
                interaction=interaction,
                result="error",
                metadata={"reason": "link_create_failed", "error": str(exc)},
            )
            await interaction.followup.send(
                "❌ Failed to create login link. Please try again in a minute.",
                ephemeral=True,
            )
            return

        self._audit(
            interaction=interaction,
            result="success",
            metadata={"expires_in_seconds": expires_in_seconds},
        )
        await interaction.followup.send(
            (
                "✅ One-time admin dashboard login link:\n"
                f"{link_url}\n\n"
                f"Expires in {expires_in_seconds} seconds."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the admin login command cog."""
    await bot.add_cog(AdminLoginCog(bot))
