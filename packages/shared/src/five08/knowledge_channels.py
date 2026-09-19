"""Discord channel allowlist helpers for organizational knowledge retrieval."""

from __future__ import annotations

from collections.abc import Iterable

MAX_KNOWLEDGE_DISCORD_CHANNELS = 8


def normalize_knowledge_discord_channel_ids(value: object) -> str:
    """Validate and normalize the configured Discord knowledge source IDs."""
    raw_items: Iterable[object]
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Iterable):
        raw_items = value
    else:
        raw_items = (value,)

    channel_ids = list(
        dict.fromkeys(str(item).strip() for item in raw_items if str(item).strip())
    )
    if len(channel_ids) > MAX_KNOWLEDGE_DISCORD_CHANNELS or any(
        not channel_id.isascii() or not channel_id.isdigit()
        for channel_id in channel_ids
    ):
        raise ValueError("Specify at most eight Discord channel or thread IDs")
    return ",".join(channel_ids)


def knowledge_discord_channel_ids(value: object) -> list[str]:
    """Read a trusted setting as a bounded, deduplicated list."""
    raw_items: Iterable[object]
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, Iterable):
        raw_items = value
    else:
        raw_items = (value,)
    return list(
        dict.fromkeys(str(item).strip() for item in raw_items if str(item).strip())
    )[:MAX_KNOWLEDGE_DISCORD_CHANNELS]
