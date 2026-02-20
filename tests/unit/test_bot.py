"""
Unit tests for the main bot class.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path
import discord

from five08.discord_bot.bot import Bot508, create_bot


class TestBot508:
    """Test the Bot508 class."""

    def test_create_bot_returns_bot508_instance(self):
        """Test that create_bot returns a Bot508 instance."""
        bot = create_bot()
        assert isinstance(bot, Bot508)

    def test_bot_initialization(self):
        """Test bot initialization with correct parameters."""
        bot = Bot508()

        assert bot.command_prefix == "$508$"
        assert bot.intents.value == discord.Intents.all().value

    @pytest.mark.asyncio
    async def test_setup_hook_calls_load_extensions(self):
        """Test that setup_hook calls load_extensions."""
        bot = Bot508()

        with patch.object(bot, "load_extensions", new_callable=AsyncMock) as mock_load:
            await bot.setup_hook()
            mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_extensions_loads_py_files(self):
        """Test that load_extensions loads .py files from features directory."""
        bot = Bot508()

        # Mock the features directory and files
        mock_features_dir = Mock()
        mock_file1 = Mock()
        mock_file1.name = "test_feature.py"
        mock_file1.stem = "test_feature"

        mock_file2 = Mock()
        mock_file2.name = "__init__.py"

        mock_features_dir.glob.return_value = [mock_file1, mock_file2]

        with patch.object(Path, "glob", return_value=[mock_file1, mock_file2]):
            with patch.object(
                bot, "load_extension", new_callable=AsyncMock
            ) as mock_load_ext:
                await bot.load_extensions()

                # Should only load test_feature.py, not __init__.py
                mock_load_ext.assert_called_once_with(
                    "five08.discord_bot.cogs.test_feature"
                )

    @pytest.mark.asyncio
    async def test_load_extensions_handles_errors(self, caplog):
        """Test that load_extensions handles loading errors gracefully."""
        bot = Bot508()

        mock_file = Mock()
        mock_file.name = "broken_feature.py"
        mock_file.stem = "broken_feature"

        with patch.object(Path, "glob", return_value=[mock_file]):
            with patch.object(
                bot, "load_extension", side_effect=Exception("Load error")
            ):
                await bot.load_extensions()

                # Check that error was logged (not raised)
                assert "Failed to load cog" in caplog.text
                assert "broken_feature" in caplog.text

    @pytest.mark.asyncio
    async def test_on_ready_sends_activation_message(self, mock_discord_channel):
        """Test that on_ready sends activation message to channel."""
        bot = Bot508()
        mock_user = Mock()
        mock_user.__str__ = Mock(return_value="TestBot")

        with patch.object(bot, "get_channel", return_value=mock_discord_channel):
            with patch.object(type(bot), "user", new_callable=lambda: mock_user):
                await bot.on_ready()

                mock_discord_channel.send.assert_called_once()
                call_args = mock_discord_channel.send.call_args[0][0]
                assert "508.dev Bot activated" in call_args

    @pytest.mark.asyncio
    async def test_on_ready_handles_missing_channel(self, caplog):
        """Test that on_ready handles missing channel gracefully."""
        import logging

        caplog.set_level(logging.INFO)

        bot = Bot508()
        mock_user = Mock()
        mock_user.__str__ = Mock(return_value="TestBot")

        with patch.object(bot, "get_channel", return_value=None):
            with patch.object(type(bot), "user", new_callable=lambda: mock_user):
                # Should not raise an exception
                await bot.on_ready()

                assert "ready for 508.dev" in caplog.text
