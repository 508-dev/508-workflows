"""Lazy Langfuse client construction for shared LLM observability."""

from __future__ import annotations

from typing import Any

from five08.settings import SharedSettings


def get_langfuse_client(settings: SharedSettings) -> Any | None:
    """Return a Langfuse client when an endpoint is configured."""
    base_url = (settings.langfuse_base_url or "").strip()
    if not base_url:
        return None

    from langfuse import Langfuse

    return Langfuse(base_url=base_url)
