"""Unit tests for bot HTTP server wiring."""

import pytest
from unittest.mock import Mock

from five08.discord_bot.utils.bot_http import BotHTTPServer


class TestBotHTTPServer:
    """Unit tests for BotHTTPServer."""

    @pytest.fixture
    def mock_bot(self):
        bot = Mock()
        bot.guilds = []
        return bot

    @pytest.fixture
    def http_server(self, mock_bot):
        return BotHTTPServer(mock_bot)

    def test_server_initialization(self, http_server, mock_bot):
        """Server should initialize shared app state."""
        from five08.discord_bot.config import settings

        assert http_server.bot == mock_bot
        assert http_server.port == settings.healthcheck_port
        assert http_server.app is not None
        assert http_server.runner is None
        assert http_server.site is None

    @pytest.mark.asyncio
    async def test_server_stop_without_start(self, http_server):
        """Stop should handle unstarted server state."""
        await http_server.stop()
