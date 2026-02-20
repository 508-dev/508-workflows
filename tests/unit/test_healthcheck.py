"""
Unit tests for healthcheck functionality.
"""

import pytest
from unittest.mock import Mock
import json

from five08.discord_bot.utils.healthcheck import HealthcheckServer


class TestHealthcheckServer:
    """Unit tests for HealthcheckServer class."""

    @pytest.fixture
    def mock_bot(self):
        """Create a mock bot for testing."""
        bot = Mock()
        bot.is_ready.return_value = True
        bot.latency = 0.05  # 50ms
        bot.guilds = [Mock(), Mock()]
        bot.guilds[0].member_count = 100
        bot.guilds[1].member_count = 50
        bot.cogs = {
            "EmailMonitor": Mock(),
            "CRMCog": Mock(),
        }
        # Mock commands
        for cog in bot.cogs.values():
            cog.get_commands.return_value = [Mock(), Mock()]  # 2 commands each
            cog.get_app_commands.return_value = [Mock()]  # 1 app command each

        return bot

    @pytest.fixture
    def healthcheck_server(self, mock_bot):
        """Create a HealthcheckServer instance for testing."""
        server = HealthcheckServer(mock_bot)
        return server

    def test_server_initialization(self, healthcheck_server, mock_bot):
        """Test healthcheck server initialization."""
        from five08.discord_bot.config import settings

        assert healthcheck_server.bot == mock_bot
        assert healthcheck_server.port == settings.healthcheck_port
        assert healthcheck_server.app is not None
        assert healthcheck_server.start_time is not None

    @pytest.mark.asyncio
    async def test_health_handler_healthy_bot(self, healthcheck_server):
        """Test health handler with healthy bot."""
        # Mock request
        mock_request = Mock()

        response = await healthcheck_server.health_handler(mock_request)

        assert response.status == 200
        assert response.content_type == "application/json"

        # Parse response body
        response_text = response.body.decode("utf-8")
        data = json.loads(response_text)

        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "uptime_seconds" in data
        assert data["bot"]["connected"] is True
        assert data["bot"]["latency_ms"] == 50.0
        assert data["bot"]["guild_count"] == 2
        assert data["bot"]["user_count"] == 150
        assert "cogs" in data
        assert len(data["cogs"]) == 2
        assert data["cogs"]["emailmonitor"]["loaded"] is True
        assert data["cogs"]["emailmonitor"]["commands"] == 2
        assert data["cogs"]["emailmonitor"]["app_commands"] == 1

    @pytest.mark.asyncio
    async def test_health_handler_unhealthy_bot(self, healthcheck_server):
        """Test health handler with unhealthy bot."""
        # Make bot not ready
        healthcheck_server.bot.is_ready.return_value = False

        # Mock request
        mock_request = Mock()

        response = await healthcheck_server.health_handler(mock_request)

        assert response.status == 503  # Service Unavailable

        # Parse response body
        response_text = response.body.decode("utf-8")
        data = json.loads(response_text)

        assert data["status"] == "unhealthy"
        assert data["bot"]["connected"] is False

    @pytest.mark.asyncio
    async def test_health_handler_error(self, healthcheck_server):
        """Test health handler with error condition."""
        # Make bot raise an error
        healthcheck_server.bot.is_ready.side_effect = Exception("Bot error")

        # Mock request
        mock_request = Mock()

        response = await healthcheck_server.health_handler(mock_request)

        assert response.status == 500

        # Parse response body
        response_text = response.body.decode("utf-8")
        data = json.loads(response_text)

        assert data["status"] == "error"
        assert "error" in data
        assert "Bot error" in data["error"]

    @pytest.mark.asyncio
    async def test_health_handler_no_guilds(self, healthcheck_server):
        """Test health handler when bot has no guilds."""
        healthcheck_server.bot.guilds = []

        # Mock request
        mock_request = Mock()

        response = await healthcheck_server.health_handler(mock_request)

        assert response.status == 200

        # Parse response body
        response_text = response.body.decode("utf-8")
        data = json.loads(response_text)

        assert data["bot"]["guild_count"] == 0
        assert data["bot"]["user_count"] == 0

    @pytest.mark.asyncio
    async def test_health_handler_none_latency(self, healthcheck_server):
        """Test health handler when bot latency is None."""
        healthcheck_server.bot.latency = None

        # Mock request
        mock_request = Mock()

        response = await healthcheck_server.health_handler(mock_request)

        assert response.status == 200

        # Parse response body
        response_text = response.body.decode("utf-8")
        data = json.loads(response_text)

        assert data["bot"]["latency_ms"] is None

    @pytest.mark.asyncio
    async def test_server_start_stop(self, healthcheck_server):
        """Test starting and stopping the server."""
        # Note: This is a basic test - in practice we'd need more sophisticated
        # mocking of aiohttp components for full integration testing

        assert healthcheck_server.runner is None
        assert healthcheck_server.site is None

        # Test that server can be created without errors
        # (Actual network testing would require more complex setup)
        await healthcheck_server.stop()  # Should handle None gracefully
