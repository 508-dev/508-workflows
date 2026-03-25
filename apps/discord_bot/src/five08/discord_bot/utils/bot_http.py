"""Shared aiohttp server wiring for bot health and internal endpoints."""

import logging
from typing import Optional

from aiohttp import web
from discord.ext import commands

from five08.discord_bot.config import settings
from five08.discord_bot.utils.healthcheck import HealthcheckRoutes
from five08.discord_bot.utils.internal_api import InternalAPIRoutes

logger = logging.getLogger(__name__)


class BotHTTPServer:
    """HTTP server for bot health checks and authenticated internal routes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.port = settings.healthcheck_port
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

        self.healthcheck_routes = HealthcheckRoutes(bot)
        self.internal_api_routes = InternalAPIRoutes(bot)
        self.healthcheck_routes.register(self.app)
        self.internal_api_routes.register(self.app)

    async def start(self) -> None:
        """Start the aiohttp server."""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()

            self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
            await self.site.start()

            logger.info("Bot HTTP server started on port %s", self.port)
            logger.info("Health endpoint: http://localhost:%s/health", self.port)
        except Exception as exc:
            logger.error("Failed to start bot HTTP server: %s", exc)
            raise

    async def stop(self) -> None:
        """Stop the aiohttp server."""
        try:
            if self.site:
                await self.site.stop()
                logger.info("Bot HTTP server stopped")

            if self.runner:
                await self.runner.cleanup()
        except Exception as exc:
            logger.error("Error stopping bot HTTP server: %s", exc)


async def start_bot_http_server(bot: commands.Bot) -> BotHTTPServer:
    """Start the shared bot aiohttp server."""
    server = BotHTTPServer(bot)
    await server.start()
    return server
