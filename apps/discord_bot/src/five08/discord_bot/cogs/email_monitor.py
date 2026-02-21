"""Compatibility cog for legacy bot-side email monitoring.

Mailbox resume ingestion now runs in the worker service.
"""

from __future__ import annotations

import logging

from discord.ext import commands

logger = logging.getLogger(__name__)


class EmailMonitor(commands.Cog):
    """Deprecated placeholder cog kept for compatibility/visibility in health checks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        logger.info(
            "EmailMonitor cog loaded (mailbox intake now runs in worker service)"
        )


async def setup(bot: commands.Bot) -> None:
    """Add the EmailMonitor compatibility cog to the bot."""
    await bot.add_cog(EmailMonitor(bot))
