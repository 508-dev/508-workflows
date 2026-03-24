"""Healthcheck route helpers for the Discord bot."""

import logging
from datetime import datetime, timezone

from aiohttp import web
from discord.ext import commands

logger = logging.getLogger(__name__)


class HealthcheckRoutes:
    """Healthcheck-only route collection."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.start_time = datetime.now(timezone.utc)

    def register(self, app: web.Application) -> None:
        """Register healthcheck routes on the shared aiohttp app."""
        app.router.add_get("/health", self.health_handler)
        app.router.add_get("/", self.health_handler)

    async def health_handler(self, request: web.Request) -> web.Response:
        """Handle health check requests."""
        try:
            # Calculate uptime
            uptime_seconds = (
                datetime.now(timezone.utc) - self.start_time
            ).total_seconds()

            # Get bot status
            bot_status = {
                "connected": self.bot.is_ready(),
                "latency_ms": round(self.bot.latency * 1000, 2)
                if self.bot.latency
                else None,
                "guild_count": len(self.bot.guilds) if self.bot.guilds else 0,
                "user_count": sum(
                    guild.member_count
                    for guild in self.bot.guilds
                    if guild.member_count
                )
                if self.bot.guilds
                else 0,
            }

            # Get cog status
            cog_status = {}
            for cog_name, cog in self.bot.cogs.items():
                cog_status[cog_name.lower()] = {
                    "loaded": True,
                    "commands": len([cmd for cmd in cog.get_commands()]),
                    "app_commands": len([cmd for cmd in cog.get_app_commands()]),
                }

            health_data = {
                "status": "healthy" if self.bot.is_ready() else "unhealthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": round(uptime_seconds, 2),
                "bot": bot_status,
                "cogs": cog_status,
                "version": "0.1.0",  # Could be dynamic from pyproject.toml
            }

            # Determine HTTP status code
            status_code = 200 if self.bot.is_ready() else 503

            return web.json_response(
                health_data,
                status=status_code,
            )

        except Exception as e:
            logger.error(f"Error in health check handler: {e}")
            return web.json_response(
                {
                    "status": "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": str(e),
                },
                status=500,
            )
