"""Helpers for local model profile metadata used by eval tooling."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def default_model_profiles_path() -> Path:
    """Return the repo-local model profile catalog path."""
    return (
        Path(__file__).resolve().parents[4] / "tests" / "evals" / "model-profiles.json"
    )


@lru_cache(maxsize=1)
def load_model_profiles() -> dict[str, Any]:
    """Load model profile metadata from the repo-local JSON catalog."""
    path = default_model_profiles_path()
    if not path.exists():
        return {"version": "model-profiles.v1", "models": {}}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return {"version": "model-profiles.v1", "models": {}}
    models = data.get("models")
    if not isinstance(models, dict):
        data["models"] = {}
    return data


def model_profile_for(model: str) -> dict[str, Any] | None:
    """Return the best matching profile for a model id or alias."""
    normalized = model.casefold().strip()
    if not normalized:
        return None
    models = load_model_profiles().get("models", {})
    if not isinstance(models, dict):
        return None
    best: tuple[str, dict[str, Any]] | None = None
    for key, raw_profile in models.items():
        if not isinstance(raw_profile, dict):
            continue
        candidates = {
            str(key).casefold(),
            str(raw_profile.get("name") or "").casefold(),
            str(raw_profile.get("model") or "").casefold(),
        }
        if normalized in candidates or any(
            normalized.startswith(f"{candidate}-")
            for candidate in candidates
            if candidate
        ):
            key_text = str(key)
            if best is None or len(key_text) > len(best[0]):
                best = (key_text, raw_profile)
    return best[1] if best else None


def model_pricing_source() -> str:
    """Return a human-readable source note for cost estimates."""
    source = load_model_profiles().get("pricing_source")
    return str(source) if source else "model profile catalog"


def model_cost_per_1m(model: str) -> dict[str, float] | None:
    """Return normalized input/cached_input/output pricing for a model."""
    profile = model_profile_for(model)
    if not profile:
        return None
    raw_cost = profile.get("cost_per_1m")
    if not isinstance(raw_cost, dict):
        return None
    input_cost = _float_or_none(raw_cost.get("input"))
    cached_cost = _float_or_none(raw_cost.get("cached_input", raw_cost.get("cached")))
    output_cost = _float_or_none(raw_cost.get("output"))
    if input_cost is None or output_cost is None:
        return None
    return {
        "input": input_cost,
        "cached_input": cached_cost if cached_cost is not None else input_cost,
        "output": output_cost,
    }


def model_chat_completion_options(
    model: str,
    *,
    purpose: str = "default",
) -> dict[str, Any]:
    """Return model-specific chat-completion behavior from the catalog."""
    profile = model_profile_for(model)
    if not profile:
        return {}
    field = (
        "baseline_chat_completion_options"
        if purpose == "baseline"
        else "chat_completion_options"
    )
    options = profile.get(field)
    if not isinstance(options, dict) and field != "chat_completion_options":
        options = profile.get("chat_completion_options")
    return dict(options) if isinstance(options, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
