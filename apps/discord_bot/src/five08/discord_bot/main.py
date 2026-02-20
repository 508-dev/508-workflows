"""
508.dev Discord Bot Entry Point

A modular Discord bot for the 508.dev co-op that allows multiple developers
to work independently on different bot functions through the cogs system.
"""

import asyncio
import logging

from five08.discord_bot.bot import create_bot
from five08.discord_bot.config import settings
from five08.logging import configure_logging

configure_logging(settings.log_level)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for the bot."""
    bot = create_bot()

    try:
        await bot.start(settings.discord_bot_token)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await bot.close()


def run() -> None:
    """Sync entrypoint for console script."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
