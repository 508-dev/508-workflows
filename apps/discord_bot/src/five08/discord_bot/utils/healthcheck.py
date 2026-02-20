"""
Healthcheck HTTP server for monitoring Discord bot status.

Provides a simple HTTP endpoint for health monitoring and status checks.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web
from discord.ext import commands

from five08.discord_bot.config import settings

logger = logging.getLogger(__name__)


class HealthcheckServer:
    """HTTP server for bot health monitoring."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.port = settings.healthcheck_port
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_time = datetime.now(timezone.utc)

        # Setup routes
        self.app.router.add_get("/health", self.health_handler)
        self.app.router.add_get("/", self.health_handler)  # Root also returns health

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

    async def start(self) -> None:
        """Start the healthcheck HTTP server."""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()

            self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
            await self.site.start()

            logger.info(f"Healthcheck server started on port {self.port}")
            logger.info(f"Health endpoint: http://localhost:{self.port}/health")

        except Exception as e:
            logger.error(f"Failed to start healthcheck server: {e}")
            raise

    async def stop(self) -> None:
        """Stop the healthcheck HTTP server."""
        try:
            if self.site:
                await self.site.stop()
                logger.info("Healthcheck server stopped")

            if self.runner:
                await self.runner.cleanup()

        except Exception as e:
            logger.error(f"Error stopping healthcheck server: {e}")


async def start_healthcheck_server(bot: commands.Bot) -> HealthcheckServer:
    """Start the healthcheck server for the bot."""
    server = HealthcheckServer(bot)
    await server.start()
    return server
