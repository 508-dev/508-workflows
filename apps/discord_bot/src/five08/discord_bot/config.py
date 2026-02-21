"""
Configuration management for the 508.dev Discord bot.

This module uses Pydantic settings to handle environment variables
and configuration with type validation and default values.
"""

from five08.settings import SharedSettings


class Settings(SharedSettings):
    """
    Bot configuration settings with environment variable support.

    All settings can be overridden via environment variables.
    Required settings must be provided via environment variables or .env file.
    """

    discord_bot_token: str

    discord_sendmsg_character_limit: int = 2000

    # Healthcheck Configuration
    healthcheck_port: int = 3000

    # Core channel configuration
    channel_id: int

    # CRM/EspoCRM settings
    espo_api_key: str
    espo_base_url: str
    worker_api_base_url: str = "http://worker-api:8090"
    audit_api_base_url: str | None = None
    audit_api_timeout_seconds: float = 2.0

    # Kimai time tracking settings
    kimai_base_url: str
    kimai_api_token: str


settings = Settings()  # type: ignore[call-arg]
