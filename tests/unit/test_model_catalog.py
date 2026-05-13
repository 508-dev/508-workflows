"""Tests for local model profile catalog metadata."""

from __future__ import annotations

from five08.model_catalog import (
    default_model_profiles_path,
    model_chat_completion_options,
    model_cost_per_1m,
    model_profile_for,
)


def test_model_catalog_loads_repo_json() -> None:
    assert default_model_profiles_path().name == "model-profiles.json"
    assert default_model_profiles_path().exists()

    profile = model_profile_for("gpt-4.1-mini")

    assert profile is not None
    assert profile["model"] == "gpt-4.1-mini"
    assert profile["observed_behavior"]["resume_extraction"][
        "suggested_use"
    ].startswith("default candidate")


def test_model_catalog_matches_model_prefixes_longest_first() -> None:
    prices = model_cost_per_1m("gpt-5-mini-2025-08-07")

    assert prices == {"input": 0.25, "cached_input": 0.025, "output": 2.0}


def test_model_catalog_chat_options_are_data_driven() -> None:
    assert model_chat_completion_options("gpt-5")["reasoning_effort"] == "minimal"
    assert model_chat_completion_options("gpt-5.5")["reasoning_effort"] == "low"
    assert (
        model_chat_completion_options("gpt-5.5", purpose="baseline")["reasoning_effort"]
        == "medium"
    )
