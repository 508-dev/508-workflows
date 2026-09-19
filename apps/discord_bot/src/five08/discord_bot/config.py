"""
Configuration management for the 508.dev Discord bot.

This module uses Pydantic settings to handle environment variables
and configuration with type validation and default values.
"""

from ipaddress import ip_address
from typing import ClassVar
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator

from five08.openai_fallback import (
    OpenAICompatibleProvider,
    build_openai_compatible_provider_attempts,
)
from five08.settings import SharedSettings


_COMPOSE_BACKEND_API_HOST = "web"
_COMPOSE_BACKEND_API_PORT = 8090


def _is_loopback_host(host: str) -> bool:
    """Return whether a parsed URL host is an explicit loopback endpoint."""
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_privileged_backend_url(value: str, *, setting_name: str) -> str:
    """Require a safe transport endpoint before sending an API secret."""
    normalized = value.strip()
    try:
        parsed = urlparse(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            f"{setting_name} must be a valid absolute HTTP(S) URL"
        ) from exc

    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if (
        scheme not in {"http", "https"}
        or not parsed.netloc
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{setting_name} must be a valid absolute HTTP(S) URL")

    if scheme == "https" or _is_loopback_host(host):
        return normalized
    if host == _COMPOSE_BACKEND_API_HOST and port == _COMPOSE_BACKEND_API_PORT:
        return normalized

    raise ValueError(
        f"{setting_name} must use HTTPS unless it targets a loopback host or "
        "the internal Compose endpoint http://web:8090"
    )


class Settings(SharedSettings):
    """
    Bot configuration settings with environment variable support.

    Most settings can be overridden via environment variables.
    Fixed platform limits remain in code.
    Required settings must be provided via environment variables or .env file.
    """

    discord_bot_token: str = ""

    # The bot sends narrow backend requests for Outline invitations and wiki
    # actions. It must never recover the privileged Outline writer credential
    # through SharedSettings' database-backed runtime-config fallback.
    runtime_config_overlay_excluded_attributes: ClassVar[frozenset[str]] = (
        SharedSettings.runtime_config_overlay_excluded_attributes
        | frozenset(
            {
                "legacy_outline_admin_api_key",
                "outline_admin_api_key",
                "outline_api_key",
            }
        )
    )

    discord_admin_roles: str = "Admin,Owner"
    discord_default_job_forum_channels: str = "gigs:part_time,fulltime-roles:full_time"
    discord_unqualified_leads_forum_channel: str = "unqualified-leads"
    # Healthcheck Configuration
    healthcheck_port: int = 3000

    # CRM/EspoCRM settings
    espo_api_key: str = ""
    espo_base_url: str = ""
    discord_server_id: str | None = None
    backend_api_base_url: str = "http://127.0.0.1:8090"
    audit_api_base_url: str | None = None
    audit_api_timeout_seconds: float = 2.0
    agent_api_timeout_seconds: float = 8.0
    # A confirmed wiki publish can synchronously fetch the current Outline
    # document and then write it. Keep the bot's deadline above two default
    # Outline request windows plus transport overhead.
    wiki_editing_request_timeout_seconds: float = Field(default=45.0, ge=45.0)
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-5-mini"
    openai_direct_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_DIRECT_API_KEY",
            "OPENAI_API_KEY_DIRECT",
            "openai_direct_api_key",
            "openai_api_key_direct",
        ),
    )
    openai_direct_base_url: str | None = None
    openai_direct_model: str | None = None
    fireworks_api_key: str | None = None
    openrouter_api_key: str | None = None
    agent_fallback_model: str = "gpt-4.1-mini"
    resume_ai_api_key: str | None = None
    resume_ai_base_url: str | None = None
    resume_ai_model: str = "gpt-4.1-mini"
    resume_extractor_max_tokens: int = 2000

    @field_validator("backend_api_base_url")
    @classmethod
    def _validate_backend_api_base_url(cls, value: str) -> str:
        """Require TLS for external backend requests that carry API secrets."""
        return _validate_privileged_backend_url(
            value,
            setting_name="BACKEND_API_BASE_URL",
        )

    @field_validator("audit_api_base_url")
    @classmethod
    def _validate_audit_api_base_url(cls, value: str | None) -> str | None:
        """Apply the same secret-carrying transport policy to audit overrides."""
        if value is None or not value.strip():
            return None
        return _validate_privileged_backend_url(
            value,
            setting_name="AUDIT_API_BASE_URL",
        )

    @model_validator(mode="after")
    def _remove_privileged_outline_credentials(self) -> "Settings":
        """Make accidental bot env/dotenv inheritance non-authoritative too."""
        object.__setattr__(self, "outline_admin_api_key", None)
        object.__setattr__(self, "legacy_outline_admin_api_key", None)
        return self

    @property
    def discord_sendmsg_character_limit(self) -> int:
        """Discord message splitting should follow the platform limit."""
        return 2000

    @property
    def discord_admin_role_names(self) -> set[str]:
        """Lower-cased configured Discord admin role names."""
        values = [item.strip() for item in self.discord_admin_roles.split(",")]
        return {value.casefold() for value in values if value}

    @property
    def resolved_resume_ai_api_key(self) -> str | None:
        """Return the resume-specific API key, falling back to the OpenAI key."""
        return (self.resume_ai_api_key or "").strip() or (
            (self.openai_api_key or "").strip() or None
        )

    @property
    def resolved_resume_ai_base_url(self) -> str | None:
        """Return the resume-specific base URL, falling back to the OpenAI base URL."""
        if (self.resume_ai_api_key or "").strip() and (
            self.resume_ai_base_url or ""
        ).strip():
            return self.resume_ai_base_url
        return self.openai_base_url

    @property
    def resolved_resume_ai_model(self) -> str:
        """Resolve provider-specific resume model name."""
        candidate = self.resume_ai_model.strip() or self.openai_model.strip()
        if not candidate:
            candidate = "gpt-4.1-mini"
        if "/" in candidate:
            return candidate
        base_url = (self.resolved_resume_ai_base_url or "").strip()
        if not base_url:
            return candidate
        parsed = urlparse(base_url)
        host = (parsed.netloc or parsed.path).split("/")[0].split(":")[0].lower()
        if host.endswith("openrouter.ai"):
            return f"openai/{candidate}"
        return candidate

    @property
    def resolved_openai_direct_api_key(self) -> str | None:
        """Return the preferred direct OpenAI key, including legacy env support."""
        return (self.openai_direct_api_key or "").strip() or None

    @property
    def resolved_resume_ai_provider_attempts(
        self,
    ) -> tuple[OpenAICompatibleProvider, ...]:
        """Ordered resume LLM providers with direct fallbacks after Bifrost."""
        return build_openai_compatible_provider_attempts(
            primary_model=self.resolved_resume_ai_model,
            primary_api_key=self.resolved_resume_ai_api_key,
            primary_base_url=self.resolved_resume_ai_base_url,
            openai_direct_api_key=self.resolved_openai_direct_api_key,
            openai_direct_base_url=self.openai_direct_base_url,
            openai_direct_model=self.openai_direct_model or self.agent_fallback_model,
            fireworks_api_key=self.fireworks_api_key,
            openrouter_api_key=self.openrouter_api_key,
        )


settings = Settings()  # type: ignore[call-arg]
