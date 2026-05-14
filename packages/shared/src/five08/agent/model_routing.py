"""Deterministic model-tier routing for agent planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from five08.agent.models import AgentModelSelection, ModelTier

DEFAULT_AGENT_MODEL = "gpt-4.1-mini"
DEFAULT_FIREWORKS_PLANNER_MODEL = "accounts/fireworks/models/kimi-k2p6"
DEFAULT_BIFROST_FIREWORKS_PLANNER_MODEL = f"fireworks/{DEFAULT_FIREWORKS_PLANNER_MODEL}"
DEFAULT_FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_BIFROST_PROVIDER_PREFIXES = frozenset(
    {
        "anthropic",
        "bedrock",
        "cohere",
        "fireworks",
        "gemini",
        "groq",
        "openai",
        "openrouter",
        "vertex",
    }
)
_ALLOWED_BASE_URL_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.fireworks.ai",
        "bifrost.508.dev",
        "openrouter.ai",
    }
)
_TIER_FALLBACKS: dict[ModelTier, tuple[ModelTier, ...]] = {
    "fast": ("fast",),
    "strong": ("strong", "fast"),
    "reasoning": ("reasoning", "strong", "fast"),
}


@dataclass(frozen=True)
class AgentTierModelConfig:
    """Provider settings for one agent model tier."""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


@dataclass(frozen=True)
class AgentModelConfig:
    """Provider settings for all agent model tiers plus legacy fallback."""

    fast: AgentTierModelConfig = AgentTierModelConfig()
    strong: AgentTierModelConfig = AgentTierModelConfig()
    reasoning: AgentTierModelConfig = AgentTierModelConfig()
    openai_model: str | None = None
    openai_base_url: str | None = None
    openai_api_key: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "AgentModelConfig":
        """Build model routing config from a settings object."""
        default_planner = _default_planner_provider(settings)
        fallback_base_url = _openai_fallback_base_url(settings)
        fallback_api_key = _openai_fallback_api_key(settings)
        return cls(
            fast=_tier_config_from_settings(
                settings,
                tier="fast",
                default_provider=default_planner,
            ),
            strong=_tier_config_from_settings(
                settings,
                tier="strong",
                default_provider=default_planner,
            ),
            reasoning=_tier_config_from_settings(
                settings,
                tier="reasoning",
                default_provider=default_planner,
            ),
            openai_model=(
                _optional_str(getattr(settings, "agent_fallback_model", None))
                or _optional_str(getattr(settings, "openai_direct_model", None))
                or _optional_str(getattr(settings, "openai_model", None))
            ),
            openai_base_url=fallback_base_url,
            openai_api_key=fallback_api_key,
        )

    def resolve(self, tier: ModelTier) -> AgentModelSelection:
        """Resolve a model tier with deterministic fallbacks."""
        for fallback_tier in _TIER_FALLBACKS[tier]:
            tier_config = self._tier_config(fallback_tier)
            if tier_config.model:
                resolved_base_url = tier_config.base_url or self.openai_base_url
                if not self._api_key_configured_for_tier(
                    tier_config=tier_config,
                    resolved_base_url=resolved_base_url,
                ):
                    continue
                return AgentModelSelection(
                    tier=tier,
                    model=tier_config.model,
                    base_url=resolved_base_url,
                    source_tier=fallback_tier,
                    fallback_used=fallback_tier != tier,
                    api_key_configured=self._api_key_configured_for_tier(
                        tier_config=tier_config,
                        resolved_base_url=resolved_base_url,
                    ),
                )

        if self.openai_model:
            return AgentModelSelection(
                tier=tier,
                model=self.openai_model,
                base_url=self.openai_base_url,
                source_tier="openai_default",
                fallback_used=True,
                api_key_configured=bool(self.openai_api_key),
            )

        return AgentModelSelection(
            tier=tier,
            model=DEFAULT_AGENT_MODEL,
            base_url=self.openai_base_url,
            source_tier="built_in_default",
            fallback_used=True,
            api_key_configured=bool(self.openai_api_key),
        )

    def _tier_config(self, tier: ModelTier) -> AgentTierModelConfig:
        return {
            "fast": self.fast,
            "strong": self.strong,
            "reasoning": self.reasoning,
        }[tier]

    def _api_key_configured_for_tier(
        self,
        *,
        tier_config: AgentTierModelConfig,
        resolved_base_url: str | None,
    ) -> bool:
        if tier_config.api_key:
            return True
        if tier_config.base_url and tier_config.base_url != self.openai_base_url:
            return False
        return bool(self.openai_api_key and resolved_base_url == self.openai_base_url)


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _tier_config_from_settings(
    settings: Any,
    *,
    tier: str,
    default_provider: AgentTierModelConfig,
) -> AgentTierModelConfig:
    model = _optional_str(getattr(settings, f"agent_{tier}_model", None))
    base_url = _validated_base_url(getattr(settings, f"agent_{tier}_base_url", None))
    api_key = _optional_str(getattr(settings, f"agent_{tier}_api_key", None))
    if model:
        return AgentTierModelConfig(model=model, base_url=base_url, api_key=api_key)
    return default_provider


def _default_planner_provider(settings: Any) -> AgentTierModelConfig:
    planner_model = (
        _optional_str(getattr(settings, "agent_planner_model", None))
        or DEFAULT_FIREWORKS_PLANNER_MODEL
    )
    openai_base_url = _validated_base_url(getattr(settings, "openai_base_url", None))
    openai_key = _optional_str(getattr(settings, "openai_api_key", None))
    if openai_base_url and _is_bifrost_base_url(openai_base_url) and openai_key:
        return AgentTierModelConfig(
            model=_bifrost_planner_model(planner_model),
            base_url=openai_base_url,
            api_key=openai_key,
        )

    fireworks_key = _optional_str(getattr(settings, "fireworks_api_key", None))
    if not fireworks_key:
        return AgentTierModelConfig()
    return AgentTierModelConfig(
        model=planner_model,
        base_url=DEFAULT_FIREWORKS_BASE_URL,
        api_key=fireworks_key,
    )


def _openai_fallback_base_url(settings: Any) -> str | None:
    direct_key = _openai_direct_api_key(settings)
    direct_base_url = _validated_base_url(
        getattr(settings, "openai_direct_base_url", None)
    )
    if direct_key and direct_base_url is not None:
        return direct_base_url
    if direct_key:
        return DEFAULT_OPENAI_BASE_URL

    openai_base_url = _validated_base_url(getattr(settings, "openai_base_url", None))
    return openai_base_url


def _openai_fallback_api_key(settings: Any) -> str | None:
    return _openai_direct_api_key(settings) or _optional_str(
        getattr(settings, "openai_api_key", None)
    )


def _openai_direct_api_key(settings: Any) -> str | None:
    return _optional_str(
        getattr(settings, "openai_direct_api_key", None)
    ) or _optional_str(getattr(settings, "openai_api_key_direct", None))


def _is_bifrost_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (parsed.hostname or "").casefold() in {"bifrost.508.dev", "bifrost"}


def _bifrost_planner_model(model: str) -> str:
    provider, separator, _rest = model.partition("/")
    if separator and provider in _BIFROST_PROVIDER_PREFIXES:
        return model
    if not separator:
        return model
    return f"fireworks/{model}"


def _validated_base_url(value: Any) -> str | None:
    base_url = _optional_str(value)
    if base_url is None:
        return None
    parsed = urlparse(base_url)
    if not _is_allowed_base_url(parsed):
        raise ValueError(f"Disallowed agent model base_url: {base_url}")
    return base_url


def _is_allowed_base_url(parsed: Any) -> bool:
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme == "https" and hostname in _ALLOWED_BASE_URL_HOSTS:
        return True
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and hostname == "bifrost"
        and port == 8080
        and parsed.path.rstrip("/") == "/openai"
    )
