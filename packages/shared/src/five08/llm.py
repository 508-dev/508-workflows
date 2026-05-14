"""Provider/model helpers for OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping, TypedDict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PROFILE_RESOURCE = "llm_model_profiles.json"
_OPENAI_PROVIDER_PREFIXES = ("openai/",)


class OpenAIClientKwargs(TypedDict, total=False):
    """Typed OpenAI-compatible client constructor kwargs."""

    api_key: str
    base_url: str


@dataclass(frozen=True)
class ModelProfile:
    """Runtime parameter support for a model."""

    name: str
    provider: str = "openai-compatible"
    model: str | None = None
    supports_temperature: bool = True
    supports_response_format: bool = True
    supports_reasoning_effort: bool = False
    supports_verbosity: bool = False

    @property
    def provider_model_name(self) -> str:
        """Model name to send to the provider when the profile overrides it."""
        return self.model or self.name


@dataclass(frozen=True)
class ProviderModel:
    """Resolved provider/model plus request-option filtering."""

    model: str
    api_key: str | None = None
    base_url: str | None = None
    profile: ModelProfile | None = None

    @classmethod
    def openai_compatible(
        cls,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> "ProviderModel":
        """Build an OpenAI-compatible provider model from app settings."""
        resolved_model = _resolve_openrouter_model(model, base_url)
        profile = get_model_profile(resolved_model)
        provider_model_name = (
            resolved_model
            if _has_provider_prefix(resolved_model)
            else profile.provider_model_name
        )
        return cls(
            model=provider_model_name,
            api_key=api_key,
            base_url=base_url,
            profile=profile,
        )

    def client_kwargs(self) -> OpenAIClientKwargs:
        """Keyword args for constructing an OpenAI-compatible client."""
        kwargs: OpenAIClientKwargs = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return kwargs

    def chat_completion_kwargs(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Any | None = None,
        reasoning_effort: str | None = None,
        verbosity: str | None = None,
    ) -> dict[str, Any]:
        """Build strict-provider-safe kwargs for chat.completions calls."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None and self.supports("response_format"):
            kwargs["response_format"] = response_format
        if temperature is not None and self.supports("temperature"):
            kwargs["temperature"] = temperature
        if reasoning_effort is not None and self.supports("reasoning_effort"):
            kwargs["reasoning_effort"] = reasoning_effort
        if verbosity is not None and self.supports("verbosity"):
            kwargs["verbosity"] = verbosity
        return kwargs

    def supports(self, option: str) -> bool:
        """Return whether the configured model supports a request option."""
        profile = self.profile or _fallback_profile(self.model)
        if option == "temperature":
            return profile.supports_temperature
        if option == "response_format":
            return profile.supports_response_format
        if option == "reasoning_effort":
            return profile.supports_reasoning_effort
        if option == "verbosity":
            return profile.supports_verbosity
        return True


def get_model_profile(model: str) -> ModelProfile:
    """Look up a model profile, handling provider-prefixed OpenAI names."""
    profiles = _load_model_profiles()
    normalized = _profile_lookup_key(model)
    payload = profiles.get(normalized)
    if payload is None:
        return _fallback_profile(model)
    return _profile_from_payload(normalized, payload)


@lru_cache(maxsize=1)
def _load_model_profiles() -> dict[str, Mapping[str, Any]]:
    try:
        raw = resources.files("five08").joinpath(_PROFILE_RESOURCE).read_text()
        data = json.loads(raw)
    except Exception as exc:  # pragma: no cover - static package data should exist
        logger.warning("Failed to load LLM model profiles: %s", exc)
        return {}
    models = data.get("models")
    if not isinstance(models, dict):
        return {}
    return {
        str(key): value for key, value in models.items() if isinstance(value, Mapping)
    }


def _profile_from_payload(name: str, payload: Mapping[str, Any]) -> ModelProfile:
    request_options = payload.get("request_options")
    if not isinstance(request_options, Mapping):
        request_options = {}
    return ModelProfile(
        name=str(payload.get("name") or name),
        provider=str(payload.get("provider") or "openai-compatible"),
        model=str(payload["model"]) if payload.get("model") else None,
        supports_temperature=bool(request_options.get("temperature", True)),
        supports_response_format=bool(request_options.get("response_format", True)),
        supports_reasoning_effort=bool(request_options.get("reasoning_effort", False)),
        supports_verbosity=bool(request_options.get("verbosity", False)),
    )


def _fallback_profile(model: str) -> ModelProfile:
    """Conservative defaults for models missing from the compiled profile."""
    lookup_key = _profile_lookup_key(model)
    reasoning_without_temperature = (
        lookup_key.startswith(("o1", "o3", "o4"))
        or lookup_key.startswith("gpt-5")
        and lookup_key != "gpt-5-chat-latest"
    )
    return ModelProfile(
        name=lookup_key,
        model=model,
        supports_temperature=not reasoning_without_temperature,
        supports_reasoning_effort=reasoning_without_temperature,
        supports_verbosity=reasoning_without_temperature,
    )


def _profile_lookup_key(model: str) -> str:
    value = (model or "").strip()
    for prefix in _OPENAI_PROVIDER_PREFIXES:
        if value.startswith(prefix):
            return value.removeprefix(prefix)
    return value


def _has_provider_prefix(model: str) -> bool:
    return "/" in model


def _resolve_openrouter_model(model: str, base_url: str | None) -> str:
    candidate = model.strip() or "gpt-5-mini"
    if _has_provider_prefix(candidate):
        return candidate

    base = (base_url or "").strip()
    if not base:
        return candidate

    parsed = urlparse(base)
    host = (parsed.netloc or parsed.path).split("/")[0].split(":")[0].lower()
    if host.endswith("openrouter.ai"):
        return f"openai/{candidate}"
    return candidate
