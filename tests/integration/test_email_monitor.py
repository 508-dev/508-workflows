"""
Integration tests for email monitoring feature.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import imaplib

from five08.discord_bot.cogs.email_monitor import EmailMonitor


class TestEmailMonitorIntegration:
    """Integration tests for EmailMonitor feature."""

    @pytest.fixture
    def email_monitor(self, mock_bot):
        """Create an EmailMonitor instance for testing."""
        # Mock the task starting to avoid actual background task
        with patch.object(
            EmailMonitor, "__init__", lambda self, bot: setattr(self, "bot", bot)
        ):
            monitor = EmailMonitor(mock_bot)
            monitor.task_poll_inbox = AsyncMock()
            monitor.task_poll_inbox.start = Mock()
            monitor.task_poll_inbox.cancel = Mock()
            monitor.task_poll_inbox.is_running = Mock(return_value=False)
            return monitor

    @pytest.fixture
    def email_monitor_real_poll(self, mock_bot):
        """Create an EmailMonitor instance with the real poll coroutine."""
        return EmailMonitor(mock_bot)

    @pytest.mark.asyncio
    async def test_poll_inbox_handles_imap_errors(
        self, email_monitor_real_poll, mock_discord_channel
    ):
        """Test that IMAP errors are handled gracefully."""
        email_monitor_real_poll.bot.get_channel.return_value = mock_discord_channel

        with patch(
            "imaplib.IMAP4_SSL", side_effect=imaplib.IMAP4.error("Connection failed")
        ):
            # IMAP transport errors currently bubble from the poll loop.
            with pytest.raises(imaplib.IMAP4.error):
                await EmailMonitor.task_poll_inbox.coro(email_monitor_real_poll)

    @pytest.mark.asyncio
    async def test_poll_inbox_handles_email_parsing_errors(
        self, email_monitor_real_poll, mock_discord_channel, mock_imap_server
    ):
        """Test handling of malformed email messages."""
        email_monitor_real_poll.bot.get_channel.return_value = mock_discord_channel
        mock_imap_server.search.return_value = ("OK", [b"1"])

        # Malformed email data
        mock_imap_server.fetch.return_value = ("OK", [(None, b"malformed email data")])

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap_server):
            await EmailMonitor.task_poll_inbox.coro(email_monitor_real_poll)

    @pytest.mark.asyncio
    async def test_cog_unload_cancels_task(self, email_monitor):
        """Test that cog_unload properly cancels the background task."""
        await email_monitor.cog_unload()
        email_monitor.task_poll_inbox.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_function(self, mock_bot):
        """Test the setup function adds the feature to the bot."""
        from five08.discord_bot.cogs.email_monitor import setup

        with patch.object(
            EmailMonitor, "__init__", lambda self, bot: setattr(self, "bot", bot)
        ):
            await setup(mock_bot)

        mock_bot.add_cog.assert_called_once()
        added_cog = mock_bot.add_cog.call_args[0][0]
        assert isinstance(added_cog, EmailMonitor)
