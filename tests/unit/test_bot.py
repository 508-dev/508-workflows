"""
Unit tests for the main bot class.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace
import discord

from five08.discord_bot.bot import (
    Bot508,
    DISCORD_COMMAND_DESCRIPTION_LIMIT,
    create_bot,
    settings,
    validate_app_command_descriptions,
)
from five08.discord_bot.config import Settings
from five08.discord_bot import main as bot_main


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

        def glob_side_effect(pattern: str):
            if pattern == "*.py":
                return [mock_file1, mock_file2]
            return []

        with patch.object(Path, "glob", side_effect=glob_side_effect):
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

        def glob_side_effect(pattern: str):
            if pattern == "*.py":
                return [mock_file]
            return []

        with patch.object(Path, "glob", side_effect=glob_side_effect):
            with patch.object(
                bot, "load_extension", side_effect=Exception("Load error")
            ):
                await bot.load_extensions()

                # Check that error was logged (not raised)
                assert "Failed to load cog" in caplog.text
                assert "broken_feature" in caplog.text

    @pytest.mark.asyncio
    async def test_on_ready_sends_activation_message(self):
        """Test that on_ready sends activation message to webhook."""
        bot = Bot508()
        mock_user = Mock()
        mock_user.__str__ = Mock(return_value="TestBot")
        webhook_url = "https://discord.com/api/webhooks/123/abc"

        with patch.object(settings, "discord_logs_webhook_url", webhook_url):
            with patch(
                "five08.discord_bot.bot.DiscordWebhookLogger"
            ) as mock_logger_cls:
                with patch.object(type(bot), "user", new_callable=lambda: mock_user):
                    await bot.on_ready()

                    mock_logger_cls.assert_called_once_with(
                        webhook_url=webhook_url,
                        timeout_seconds=2.0,
                        wait_for_response=settings.discord_logs_webhook_wait,
                    )

                    mock_logger_instance = mock_logger_cls.return_value
                    mock_logger_instance.send.assert_called_once()
                    sent_content = mock_logger_instance.send.call_args.kwargs["content"]
                    assert "508.dev Bot activated" in sent_content

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

    def test_discord_message_limit_is_not_env_configurable(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("DISCORD_SENDMSG_CHARACTER_LIMIT", "500")

        config = Settings()

        assert config.discord_sendmsg_character_limit == 2000

    def test_discord_bot_token_defaults_blank_for_config_imports(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

        config = Settings()

        assert config.discord_bot_token == ""

    @pytest.mark.asyncio
    async def test_discord_bot_token_is_required_to_start(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(bot_main.settings, "discord_bot_token", " ")

        with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN is required"):
            await bot_main.main()

    def test_backend_api_base_url_defaults_to_host_runtime(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("BACKEND_API_BASE_URL", raising=False)

        config = Settings(
            discord_bot_token="token",
            espo_api_key="espo-key",
            espo_base_url="https://crm.example.com",
        )

        assert config.backend_api_base_url == "http://127.0.0.1:8090"

    def test_outline_wiki_api_key_uses_its_own_bot_setting(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("OUTLINE_WIKI_API_KEY", "wiki-read-only-key")

        config = Settings()

        assert config.outline_wiki_api_key == "wiki-read-only-key"

    def test_onboarding_email_smtp_settings_fall_back_to_generic_smtp_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_PORT", raising=False)
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_USE_SSL", raising=False)
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_STARTTLS", raising=False)
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_USERNAME", raising=False)
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_PASSWORD", raising=False)
        monkeypatch.delenv("ONBOARDING_EMAIL_SMTP_TIMEOUT_SECONDS", raising=False)
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USE_SSL", "false")
        monkeypatch.setenv("SMTP_STARTTLS", "true")
        monkeypatch.setenv("SMTP_USERNAME", "onboarding@508.dev")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "45")

        config = Settings(
            discord_bot_token="token",
            espo_api_key="espo-key",
            espo_base_url="https://crm.example.com",
        )

        assert config.onboarding_email_smtp_port == 587
        assert config.onboarding_email_smtp_use_ssl is False
        assert config.onboarding_email_smtp_starttls is True
        assert config.onboarding_email_smtp_username == "onboarding@508.dev"
        assert config.onboarding_email_smtp_password == "secret"
        assert config.onboarding_email_smtp_timeout_seconds == 45.0

    def test_validate_app_command_descriptions_accepts_valid_lengths(self):
        """Test that valid command descriptions pass validation."""
        tree = Mock()
        cmd = SimpleNamespace(
            name="valid",
            qualified_name="valid",
            description="short",
        )
        tree.walk_commands.return_value = [cmd]

        validate_app_command_descriptions(tree)

    def test_validate_app_command_descriptions_rejects_over_limit(self):
        """Test that overlong command descriptions raise a clear error."""
        tree = Mock()
        cmd = SimpleNamespace(
            name="too-long",
            qualified_name="too-long",
            description="x" * (DISCORD_COMMAND_DESCRIPTION_LIMIT + 1),
        )
        tree.walk_commands.return_value = [cmd]

        with pytest.raises(
            ValueError,
            match=r"/too-long description has 101 characters",
        ):
            validate_app_command_descriptions(tree)

    def test_validate_app_command_descriptions_falls_back_to_name(self):
        """Test that missing qualified names fall back to the command name."""
        tree = Mock()
        cmd = SimpleNamespace(
            name="too-long",
            description="x" * (DISCORD_COMMAND_DESCRIPTION_LIMIT + 1),
        )
        tree.walk_commands.return_value = [cmd]

        with pytest.raises(
            ValueError,
            match=r"/too-long description has 101 characters",
        ):
            validate_app_command_descriptions(tree)
