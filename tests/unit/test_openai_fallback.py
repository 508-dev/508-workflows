"""Unit tests for OpenAI-compatible provider fallback helpers."""

from __future__ import annotations

from types import SimpleNamespace

from five08.openai_fallback import (
    FallbackOpenAIClient,
    OpenAICompatibleProvider,
    build_openai_compatible_provider_attempts,
)


def test_bifrost_openrouter_attempts_fall_back_to_direct_openrouter() -> None:
    providers = build_openai_compatible_provider_attempts(
        primary_model="openrouter/openai/gpt-4.1-mini",
        primary_api_key="bifrost-key",
        primary_base_url="https://bifrost.508.dev/openai",
        openrouter_api_key="openrouter-key",
        openai_direct_api_key="openai-direct-key",
    )

    assert [provider.label for provider in providers] == [
        "primary",
        "openrouter-direct",
        "openai-direct",
    ]
    assert providers[0].model == "openrouter/openai/gpt-4.1-mini"
    assert providers[1].model == "openai/gpt-4.1-mini"
    assert providers[1].base_url == "https://openrouter.ai/api/v1"
    assert providers[2].model == "gpt-4.1-mini"
    assert providers[2].base_url == "https://api.openai.com/v1"


def test_bifrost_fireworks_attempts_fall_back_to_direct_fireworks() -> None:
    providers = build_openai_compatible_provider_attempts(
        primary_model="fireworks/accounts/fireworks/models/kimi-k2p6",
        primary_api_key="bifrost-key",
        primary_base_url="https://bifrost.508.dev/openai",
        fireworks_api_key="fireworks-key",
    )

    assert [provider.label for provider in providers] == [
        "primary",
        "fireworks-direct",
    ]
    assert providers[1].model == "accounts/fireworks/models/kimi-k2p6"
    assert providers[1].base_url == "https://api.fireworks.ai/inference/v1"


def test_fallback_openai_client_retries_next_provider_and_rewrites_model_kwargs() -> (
    None
):
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def __init__(self, label: str) -> None:
            self.label = label

        def create(self, **kwargs: object) -> object:
            calls.append({"label": self.label, "kwargs": kwargs})
            if self.label == "primary-key":
                raise TimeoutError("bifrost timeout")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}'),
                    )
                ]
            )

    class FakeClient:
        def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
            self.chat = SimpleNamespace(
                completions=FakeCompletions(api_key),
            )

    client = FallbackOpenAIClient(
        providers=[
            OpenAICompatibleProvider(
                label="primary",
                model="gpt-5-mini",
                api_key="primary-key",
                base_url="https://bifrost.508.dev/openai",
            ),
            OpenAICompatibleProvider(
                label="openai-direct",
                model="gpt-4.1-mini",
                api_key="direct-key",
                base_url="https://api.openai.com/v1",
            ),
        ],
        client_factory=FakeClient,
    )

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "user", "content": "hi"}],
        max_completion_tokens=20,
        reasoning_effort="minimal",
        verbosity="low",
    )

    assert response.choices[0].message.content == '{"ok": true}'
    assert client.last_provider is not None
    assert client.last_provider.label == "openai-direct"
    assert calls[0]["label"] == "primary-key"
    assert calls[0]["kwargs"]["model"] == "gpt-5-mini"  # type: ignore[index]
    assert calls[1]["label"] == "direct-key"
    fallback_kwargs = calls[1]["kwargs"]
    assert fallback_kwargs["model"] == "gpt-4.1-mini"  # type: ignore[index]
    assert fallback_kwargs["max_tokens"] == 20  # type: ignore[index]
    assert "max_completion_tokens" not in fallback_kwargs
    assert "reasoning_effort" not in fallback_kwargs
    assert "verbosity" not in fallback_kwargs
