"""Bounded context loading helpers for agent requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from five08.agent.models import (
    AgentContextSnippet,
    AgentContextSource,
    AgentIdentityContext,
)


@dataclass(frozen=True)
class ContextLoadBounds:
    """Hard limits for loading untrusted context snippets."""

    max_messages: int = 20
    max_age_seconds: int | None = 60 * 60 * 24
    max_tokens: int = 1200


class AgentContextLoader(Protocol):
    """Deterministic adapter that returns context the actor may use."""

    def load(
        self,
        *,
        context: AgentIdentityContext,
        bounds: ContextLoadBounds,
    ) -> list[AgentContextSnippet]:
        """Return source-labeled snippets already checked for source visibility."""


class RequestContextLoader:
    """Bounds snippets supplied on the request envelope.

    The backend does not trust client-supplied snippet text or metadata for policy.
    This loader only makes already-supplied snippets safe to pass through by bounding
    count, age, text size, and approximate token usage.
    """

    def load(
        self,
        *,
        context: AgentIdentityContext,
        bounds: ContextLoadBounds,
    ) -> list[AgentContextSnippet]:
        return bound_context_snippets(
            context.context_snippets,
            bounds=bounds,
            now=datetime.now(timezone.utc),
        )


def bound_context_snippets(
    snippets: Iterable[AgentContextSnippet],
    *,
    bounds: ContextLoadBounds,
    now: datetime | None = None,
) -> list[AgentContextSnippet]:
    """Apply deterministic count, age, and token bounds to context snippets."""

    comparison_time = now or datetime.now(timezone.utc)
    remaining_tokens = max(bounds.max_tokens, 0)
    loaded: list[AgentContextSnippet] = []
    for snippet in snippets:
        if len(loaded) >= max(bounds.max_messages, 0):
            break
        if _is_too_old(snippet.created_at, bounds=bounds, now=comparison_time):
            continue
        token_count = snippet.token_count or estimate_context_tokens(snippet.text)
        if token_count <= 0:
            continue
        if token_count > remaining_tokens:
            continue
        loaded.append(
            snippet.model_copy(
                update={
                    "text": snippet.text[:2048],
                    "token_count": token_count,
                    "trusted": False,
                }
            )
        )
        remaining_tokens -= token_count
    return loaded


def context_sources_for_snippets(
    *,
    context: AgentIdentityContext,
    snippets: Iterable[AgentContextSnippet],
) -> list[AgentContextSource]:
    """Return audit-safe source metadata without raw message bodies."""

    sources: list[AgentContextSource] = []
    for index, snippet in enumerate(snippets):
        if not snippet.trusted:
            sources.append(
                AgentContextSource(
                    source_id=f"request-context-{index}",
                    operation_id=context.operation_id or "unknown-operation",
                    source_type="request",
                    source_ref="client_supplied_context",
                    loaded_by=context.discord_user_id,
                    token_count=snippet.token_count,
                )
            )
            continue
        sources.append(
            AgentContextSource(
                source_id=snippet.source_id,
                operation_id=context.operation_id or "unknown-operation",
                source_type=snippet.source_type,
                source_ref=snippet.source_ref,
                scope_type="discord"
                if snippet.source_type.startswith("discord_")
                else None,
                scope_id=snippet.thread_id or snippet.channel_id or context.guild_id,
                loaded_by=context.discord_user_id,
                token_count=snippet.token_count,
            )
        )
    return sources


def render_untrusted_context(snippets: Iterable[AgentContextSnippet]) -> str:
    """Render source-labeled snippets as untrusted data for planner prompts."""

    parts = []
    for snippet in snippets:
        parts.append(
            "\n".join(
                [
                    f"[{snippet.label}]",
                    f"source={snippet.source_type}:{snippet.source_ref}",
                    "trusted=false",
                    snippet.text,
                ]
            )
        )
    return "\n\n".join(parts)


def estimate_context_tokens(text: str) -> int:
    """Cheap deterministic token estimate used only for bounding."""

    return max(1, (len(text) + 3) // 4)


def _is_too_old(
    created_at: datetime | None,
    *,
    bounds: ContextLoadBounds,
    now: datetime,
) -> bool:
    if created_at is None or bounds.max_age_seconds is None:
        return False
    comparable = created_at
    if comparable.tzinfo is None:
        comparable = comparable.replace(tzinfo=timezone.utc)
    return (now - comparable.astimezone(timezone.utc)).total_seconds() > max(
        bounds.max_age_seconds,
        0,
    )
