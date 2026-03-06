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

    discord_admin_roles: str = "Admin,Owner"
    # Healthcheck Configuration
    healthcheck_port: int = 3000

    # CRM/EspoCRM settings
    espo_api_key: str
    espo_base_url: str
    backend_api_base_url: str = "http://api:8090"
    audit_api_base_url: str | None = None
    audit_api_timeout_seconds: float = 2.0
    migadu_api_user: str | None = None
    migadu_api_key: str | None = None
    migadu_mailbox_domain: str = "508.dev"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5-mini"

    # Kimai time tracking settings
    kimai_base_url: str
    kimai_api_token: str

    @property
    def discord_sendmsg_character_limit(self) -> int:
        """Discord message splitting should follow the platform limit."""
        return 2000

    @property
    def discord_admin_role_names(self) -> set[str]:
        """Lower-cased configured Discord admin role names."""
        values = [item.strip() for item in self.discord_admin_roles.split(",")]
        return {value.casefold() for value in values if value}


settings = Settings()  # type: ignore[call-arg]
