"""
Pytest configuration and shared fixtures for the 508.dev Discord bot tests.
"""

import asyncio
import os
import pytest
from unittest.mock import Mock, AsyncMock
from discord.ext import commands
import discord
from typing import Generator

# Config tests removed - no need to import Settings
os.environ.setdefault("DISCORD_BOT_TOKEN", "test")
os.environ.setdefault("ESPO_API_KEY", "test")
os.environ.setdefault("ESPO_BASE_URL", "https://crm.example.invalid")
os.environ.setdefault("KIMAI_BASE_URL", "https://kimai.example.invalid")
os.environ.setdefault("KIMAI_API_TOKEN", "test")


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_bot() -> Mock:
    """Create a mock Discord bot for testing."""
    bot = Mock(spec=commands.Bot)
    bot.user = Mock()
    bot.user.name = "TestBot"
    bot.latency = 0.1

    # Mock async methods
    bot.add_cog = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.get_channel = Mock(return_value=Mock())

    return bot


@pytest.fixture
def mock_discord_context() -> Mock:
    """Create a mock Discord context for command testing."""
    ctx = Mock(spec=commands.Context)
    ctx.send = AsyncMock()
    ctx.author = Mock()
    ctx.author.mention = "<@123456789>"
    ctx.author.name = "TestUser"
    ctx.guild = Mock()
    ctx.guild.name = "TestGuild"
    ctx.guild.member_count = 10
    ctx.command = Mock()
    ctx.command.name = "test_command"

    return ctx


@pytest.fixture
def mock_discord_channel() -> Mock:
    """Create a mock Discord channel for testing."""
    channel = Mock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    channel.id = 123456789
    channel.name = "test-channel"

    return channel


@pytest.fixture
def mock_discord_member() -> Mock:
    """Create a mock Discord member for testing."""
    member = Mock(spec=discord.Member)
    member.mention = "<@987654321>"
    member.name = "NewMember"
    member.id = 987654321

    return member


@pytest.fixture
def mock_email_message() -> Mock:
    """Create a mock email message for testing."""
    message = Mock()
    message.is_multipart.return_value = False
    message.__getitem__ = Mock(
        side_effect=lambda key: {
            "From": "test@example.com",
            "Subject": "Test Subject",
            "Received": "by server; Mon, 21 Oct 2024 12:00:00 +0000",
        }.get(key)
    )
    message.get_content_type.return_value = "text/plain"
    message.get_payload.return_value = Mock()
    message.get_payload.return_value.decode.return_value = "Test email body"

    return message


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock environment variables for all tests."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test_token")
    monkeypatch.setenv("EMAIL_USERNAME", "test@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "test_password")
    monkeypatch.setenv("CHANNEL_ID", "123456789")
    monkeypatch.setenv("IMAP_SERVER", "imap.test.com")
    monkeypatch.setenv("SMTP_SERVER", "smtp.test.com")
    monkeypatch.setenv("ESPO_API_KEY", "test_api_key")
    monkeypatch.setenv("ESPO_BASE_URL", "https://crm.test.com")
    monkeypatch.setenv("HEALTHCHECK_PORT", "8081")  # Different port for tests
    monkeypatch.setenv("KIMAI_BASE_URL", "https://kimai.test.com")
    monkeypatch.setenv("KIMAI_API_TOKEN", "test_kimai_token")


@pytest.fixture
def mock_imap_server() -> Mock:
    """Create a mock IMAP server for email testing."""
    mock_imap = Mock()
    mock_imap.login = Mock()
    mock_imap.select = Mock(return_value=("OK", []))
    mock_imap.search = Mock(return_value=("OK", [b"1 2 3"]))
    mock_imap.fetch = Mock(return_value=("OK", [(None, b"test email data")]))
    mock_imap.store = Mock(return_value=("OK", []))
    mock_imap.close = Mock()
    mock_imap.logout = Mock()

    return mock_imap
