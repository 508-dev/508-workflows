"""OpenAI-compatible provider fallback helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from five08.model_catalog import model_chat_completion_options

logger = logging.getLogger(__name__)

DEFAULT_FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_FIREWORKS_MODEL = "accounts/fireworks/models/kimi-k2p6"


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    """One OpenAI-compatible provider attempt, including credentials."""

    label: str
    model: str
    api_key: str
    base_url: str | None = None


class FallbackOpenAIClient:
    """Small OpenAI client facade that retries compatible providers in order."""

    def __init__(
        self,
        *,
        providers: Iterable[OpenAICompatibleProvider],
        client_factory: Callable[..., Any],
        timeout_seconds: float | None = None,
    ) -> None:
        self.providers = tuple(providers)
        self.client_factory = client_factory
        self.timeout_seconds = timeout_seconds
        self.last_provider: OpenAICompatibleProvider | None = None
        self.chat = _FallbackChat(self)
        self.beta = _FallbackBeta(self)
        self._clients: dict[OpenAICompatibleProvider, Any] = {}

    def _client_for(self, provider: OpenAICompatibleProvider) -> Any:
        client = self._clients.get(provider)
        if client is not None:
            return client
        kwargs: dict[str, Any] = {"api_key": provider.api_key}
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        if self.timeout_seconds is not None:
            kwargs["timeout"] = self.timeout_seconds
        client = self.client_factory(**kwargs)
        self._clients[provider] = client
        return client

    def _invoke(self, operation: str, kwargs: dict[str, Any]) -> Any:
        if not self.providers:
            raise RuntimeError("No OpenAI-compatible providers configured")

        last_error: Exception | None = None
        for index, provider in enumerate(self.providers):
            request_kwargs = _completion_kwargs_for_model(kwargs, provider.model)
            try:
                response = _resolve_operation(
                    self._client_for(provider),
                    operation,
                )(**request_kwargs)
            except Exception as exc:
                last_error = exc
                is_last = index == len(self.providers) - 1
                if is_last or not _should_retry_provider(exc):
                    raise
                logger.warning(
                    "OpenAI-compatible provider %s failed, trying fallback %s: %s",
                    provider.label,
                    self.providers[index + 1].label,
                    exc,
                )
                continue
            self.last_provider = provider
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("No OpenAI-compatible providers attempted")


class _FallbackChat:
    def __init__(self, client: FallbackOpenAIClient) -> None:
        self.completions = _FallbackCompletions(client, "chat.completions.create")


class _FallbackBeta:
    def __init__(self, client: FallbackOpenAIClient) -> None:
        self.chat = _FallbackBetaChat(client)


class _FallbackBetaChat:
    def __init__(self, client: FallbackOpenAIClient) -> None:
        self.completions = _FallbackCompletions(
            client,
            "beta.chat.completions.parse",
        )


class _FallbackCompletions:
    def __init__(self, client: FallbackOpenAIClient, operation: str) -> None:
        self.client = client
        self.operation = operation

    def create(self, **kwargs: Any) -> Any:
        return self.client._invoke(self.operation, kwargs)

    def parse(self, **kwargs: Any) -> Any:
        return self.client._invoke(self.operation, kwargs)


def build_openai_compatible_provider_attempts(
    *,
    primary_model: str | None,
    primary_api_key: str | None,
    primary_base_url: str | None,
    openai_direct_api_key: str | None = None,
    openai_direct_base_url: str | None = None,
    openai_direct_model: str | None = None,
    fireworks_api_key: str | None = None,
    fireworks_model: str | None = None,
    openrouter_api_key: str | None = None,
    openrouter_model: str | None = None,
) -> tuple[OpenAICompatibleProvider, ...]:
    """Build ordered provider attempts for one logical OpenAI-compatible call."""
    primary = _provider_from_values(
        label="primary",
        model=primary_model,
        api_key=primary_api_key,
        base_url=primary_base_url,
    )
    attempts: list[OpenAICompatibleProvider] = []
    if primary is not None:
        attempts.append(primary)

    primary_model_value = (
        primary.model if primary is not None else _clean(primary_model)
    )
    primary_base_value = (
        primary.base_url if primary is not None else _clean(primary_base_url)
    )
    primary_is_bifrost = bool(
        primary_base_value and _is_bifrost_base_url(primary_base_value)
    )

    if primary_is_bifrost and primary_model_value:
        _append_provider(
            attempts,
            _direct_provider_for_bifrost_model(
                model=primary_model_value,
                openai_direct_api_key=openai_direct_api_key,
                openai_direct_base_url=openai_direct_base_url,
                openai_direct_model=openai_direct_model,
                fireworks_api_key=fireworks_api_key,
                fireworks_model=fireworks_model,
                openrouter_api_key=openrouter_api_key,
                openrouter_model=openrouter_model,
            ),
        )

    if fireworks_api_key and fireworks_model:
        _append_provider(
            attempts,
            OpenAICompatibleProvider(
                label="fireworks-direct",
                model=_strip_provider_prefix(fireworks_model, "fireworks"),
                api_key=fireworks_api_key.strip(),
                base_url=DEFAULT_FIREWORKS_BASE_URL,
            ),
        )

    if openrouter_api_key:
        fallback_openrouter_model = (
            _clean(openrouter_model)
            or _openrouter_model_from_primary(primary_model_value)
            or "openai/gpt-4.1-mini"
        )
        _append_provider(
            attempts,
            OpenAICompatibleProvider(
                label="openrouter-direct",
                model=fallback_openrouter_model,
                api_key=openrouter_api_key.strip(),
                base_url=DEFAULT_OPENROUTER_BASE_URL,
            ),
        )

    direct_openai_key = _clean(openai_direct_api_key)
    if direct_openai_key:
        direct_openai_model = (
            _clean(openai_direct_model)
            or _openai_model_from_primary(primary_model_value)
            or "gpt-4.1-mini"
        )
        _append_provider(
            attempts,
            OpenAICompatibleProvider(
                label="openai-direct",
                model=direct_openai_model,
                api_key=direct_openai_key,
                base_url=_clean(openai_direct_base_url) or DEFAULT_OPENAI_BASE_URL,
            ),
        )

    return tuple(attempts)


def _provider_from_values(
    *,
    label: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> OpenAICompatibleProvider | None:
    clean_model = _clean(model)
    clean_key = _clean(api_key)
    if not clean_model or not clean_key:
        return None
    return OpenAICompatibleProvider(
        label=label,
        model=clean_model,
        api_key=clean_key,
        base_url=_clean(base_url),
    )


def _direct_provider_for_bifrost_model(
    *,
    model: str,
    openai_direct_api_key: str | None,
    openai_direct_base_url: str | None,
    openai_direct_model: str | None,
    fireworks_api_key: str | None,
    fireworks_model: str | None,
    openrouter_api_key: str | None,
    openrouter_model: str | None,
) -> OpenAICompatibleProvider | None:
    if model.startswith("fireworks/") and _clean(fireworks_api_key):
        return OpenAICompatibleProvider(
            label="fireworks-direct",
            model=(
                _clean(fireworks_model)
                or _strip_provider_prefix(model, "fireworks")
                or DEFAULT_FIREWORKS_MODEL
            ),
            api_key=str(fireworks_api_key).strip(),
            base_url=DEFAULT_FIREWORKS_BASE_URL,
        )

    if model.startswith("openrouter/") and _clean(openrouter_api_key):
        return OpenAICompatibleProvider(
            label="openrouter-direct",
            model=(
                _clean(openrouter_model)
                or _strip_provider_prefix(model, "openrouter")
                or "openai/gpt-4.1-mini"
            ),
            api_key=str(openrouter_api_key).strip(),
            base_url=DEFAULT_OPENROUTER_BASE_URL,
        )

    if _clean(openai_direct_api_key):
        return OpenAICompatibleProvider(
            label="openai-direct",
            model=(
                _clean(openai_direct_model)
                or _openai_model_from_primary(model)
                or "gpt-4.1-mini"
            ),
            api_key=str(openai_direct_api_key).strip(),
            base_url=_clean(openai_direct_base_url) or DEFAULT_OPENAI_BASE_URL,
        )
    return None


def _completion_kwargs_for_model(
    kwargs: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    adjusted = dict(kwargs)
    adjusted["model"] = model
    model_options = model_chat_completion_options(model)
    max_tokens_parameter = model_options.get("max_tokens_parameter")
    if max_tokens_parameter == "max_completion_tokens":
        if "max_tokens" in adjusted and "max_completion_tokens" not in adjusted:
            adjusted["max_completion_tokens"] = adjusted.pop("max_tokens")
        else:
            adjusted.pop("max_tokens", None)
    else:
        if "max_completion_tokens" in adjusted and "max_tokens" not in adjusted:
            adjusted["max_tokens"] = adjusted.pop("max_completion_tokens")
        else:
            adjusted.pop("max_completion_tokens", None)
        adjusted.pop("reasoning_effort", None)
        adjusted.pop("verbosity", None)

    if not model_options.get("supports_temperature", True):
        adjusted.pop("temperature", None)
    return adjusted


def _resolve_operation(client: Any, operation: str) -> Callable[..., Any]:
    value = client
    for part in operation.split("."):
        value = getattr(value, part)
    return value


def _should_retry_provider(exc: Exception) -> bool:
    if isinstance(exc, (ValidationError, json.JSONDecodeError, ValueError)):
        return False
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in {400, 401, 403, 404, 408, 409, 429} or status_code >= 500
    return True


def _append_provider(
    providers: list[OpenAICompatibleProvider],
    provider: OpenAICompatibleProvider | None,
) -> None:
    if provider is None:
        return
    key = (provider.label, provider.model, provider.base_url)
    if any((item.label, item.model, item.base_url) == key for item in providers):
        return
    providers.append(provider)


def _openai_model_from_primary(model: str | None) -> str | None:
    value = _clean(model)
    if not value:
        return None
    if value.startswith("openai/"):
        return _strip_provider_prefix(value, "openai")
    if "/" not in value:
        return value
    return None


def _openrouter_model_from_primary(model: str | None) -> str | None:
    value = _clean(model)
    if not value:
        return None
    if value.startswith("openrouter/"):
        return _strip_provider_prefix(value, "openrouter")
    if value.startswith("openai/"):
        return value
    if "/" not in value:
        return f"openai/{value}"
    return None


def _strip_provider_prefix(model: str, provider: str) -> str:
    prefix = f"{provider}/"
    if model.startswith(prefix):
        return model[len(prefix) :]
    return model


def _is_bifrost_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (parsed.hostname or "").casefold() == "bifrost.508.dev"


def _clean(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
