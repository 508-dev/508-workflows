"""Unit tests for provider/model request profiles."""

from five08.llm import ProviderModel, get_model_profile


def test_gpt_5_mini_profile_omits_temperature() -> None:
    provider_model = ProviderModel.openai_compatible(model="gpt-5-mini")

    kwargs = provider_model.chat_completion_kwargs(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=100,
        reasoning_effort="minimal",
        verbosity="low",
    )

    assert kwargs["model"] == "gpt-5-mini"
    assert "temperature" not in kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["reasoning_effort"] == "minimal"
    assert kwargs["verbosity"] == "low"


def test_gpt_4_1_mini_profile_keeps_temperature_but_omits_reasoning_options() -> None:
    provider_model = ProviderModel.openai_compatible(model="gpt-4.1-mini")

    kwargs = provider_model.chat_completion_kwargs(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.1,
        response_format={"type": "json_object"},
        max_tokens=100,
        reasoning_effort="minimal",
        verbosity="low",
    )

    assert kwargs["temperature"] == 0.1
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in kwargs
    assert "verbosity" not in kwargs


def test_openrouter_plain_openai_model_gets_provider_prefix_for_request() -> None:
    provider_model = ProviderModel.openai_compatible(
        model="gpt-5-mini",
        base_url="https://openrouter.ai/api/v1",
    )

    kwargs = provider_model.chat_completion_kwargs(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.1,
    )

    assert provider_model.model == "openai/gpt-5-mini"
    assert "temperature" not in kwargs


def test_unknown_non_reasoning_model_preserves_temperature() -> None:
    profile = get_model_profile("fake-model")
    provider_model = ProviderModel.openai_compatible(model="fake-model")

    kwargs = provider_model.chat_completion_kwargs(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.0,
    )

    assert profile is not None
    assert kwargs["temperature"] == 0.0
