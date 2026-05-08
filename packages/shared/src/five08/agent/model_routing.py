"""Deterministic model-tier routing for agent planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from five08.agent.models import AgentModelSelection, ModelTier

DEFAULT_AGENT_MODEL = "gpt-5-mini"
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
        return cls(
            fast=AgentTierModelConfig(
                model=_optional_str(getattr(settings, "agent_fast_model", None)),
                base_url=_optional_str(getattr(settings, "agent_fast_base_url", None)),
                api_key=_optional_str(getattr(settings, "agent_fast_api_key", None)),
            ),
            strong=AgentTierModelConfig(
                model=_optional_str(getattr(settings, "agent_strong_model", None)),
                base_url=_optional_str(
                    getattr(settings, "agent_strong_base_url", None)
                ),
                api_key=_optional_str(getattr(settings, "agent_strong_api_key", None)),
            ),
            reasoning=AgentTierModelConfig(
                model=_optional_str(getattr(settings, "agent_reasoning_model", None)),
                base_url=_optional_str(
                    getattr(settings, "agent_reasoning_base_url", None)
                ),
                api_key=_optional_str(
                    getattr(settings, "agent_reasoning_api_key", None)
                ),
            ),
            openai_model=_optional_str(getattr(settings, "openai_model", None)),
            openai_base_url=_optional_str(getattr(settings, "openai_base_url", None)),
            openai_api_key=_optional_str(getattr(settings, "openai_api_key", None)),
        )

    def resolve(self, tier: ModelTier) -> AgentModelSelection:
        """Resolve a model tier with deterministic fallbacks."""
        for fallback_tier in _TIER_FALLBACKS[tier]:
            tier_config = self._tier_config(fallback_tier)
            if tier_config.model:
                return AgentModelSelection(
                    tier=tier,
                    model=tier_config.model,
                    base_url=tier_config.base_url or self.openai_base_url,
                    source_tier=fallback_tier,
                    fallback_used=fallback_tier != tier,
                    api_key_configured=bool(tier_config.api_key or self.openai_api_key),
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


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
