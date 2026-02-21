"""Integration tests for legacy EmailMonitor compatibility cog."""

from unittest.mock import patch

import pytest

from five08.discord_bot.cogs.email_monitor import EmailMonitor


class TestEmailMonitorIntegration:
    """Integration tests for EmailMonitor compatibility behavior."""

    @pytest.mark.asyncio
    async def test_cog_load_logs_deprecation_notice(self, mock_bot) -> None:
        monitor = EmailMonitor(mock_bot)

        with patch("five08.discord_bot.cogs.email_monitor.logger.info") as mock_info:
            await monitor.cog_load()

        mock_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_function_adds_cog(self, mock_bot) -> None:
        from five08.discord_bot.cogs.email_monitor import setup

        await setup(mock_bot)

        mock_bot.add_cog.assert_called_once()
        added_cog = mock_bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, EmailMonitor)

    @pytest.mark.asyncio
    async def test_setup_constructs_email_monitor(self, mock_bot) -> None:
        from five08.discord_bot.cogs.email_monitor import setup

        with patch(
            "five08.discord_bot.cogs.email_monitor.EmailMonitor", wraps=EmailMonitor
        ) as mock_class:
            await setup(mock_bot)

        mock_class.assert_called_once_with(mock_bot)

    def test_cog_stores_bot_reference(self, mock_bot) -> None:
        monitor = EmailMonitor(mock_bot)
        assert monitor.bot is mock_bot
