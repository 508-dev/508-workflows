"""LLM-backed intent normalization for the agent gateway."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from five08.agent.model_routing import (
    DEFAULT_OPENAI_BASE_URL,
    AgentModelConfig,
    AgentTierModelConfig,
)
from five08.agent.models import AgentModelSelection
from five08.model_catalog import model_chat_completion_options
from five08.tls import default_ca_bundle_path

_SYSTEM_PROMPT = """You normalize Discord workflow requests.

Return only JSON with:
{"normalized_message": string|null, "confidence": number}

Rewrite the user's request into one supported command pattern when confident:
- Find member <name or query>
- Find contact <name or query>
- Search tasks for project <project> matching <query>
- Create a task to <title> [for <assignee>] [in project <project>] [by <date>]
- Update TASK-123 ...
- Search GitHub issues matching <query> [in repo owner/name]
- Create GitHub issue titled <title> [in repo owner/name]
- Send member agreement to <name> at <email>
- Create mailbox <mailbox> for <name> with backup email <email>
- Create SSO user for <CRM contact ID or contact name>
- Invite <email or CRM contact name> to Outline
- Create 508 accounts for <CRM contact ID or contact name> with mailbox <mailbox>

Do not invent IDs, emails, project names, repositories, or contact names.
Use null when the request is small talk, help, a sensitive report/list request,
or cannot safely map to one of the patterns.
"""


@dataclass(frozen=True)
class OpenAICompatibleIntentNormalizer:
    """Normalize loose user phrasing through one OpenAI-compatible chat model."""

    model: str
    api_key: str
    base_url: str
    timeout_seconds: float = 3.0

    @classmethod
    def from_settings(cls, settings: Any) -> "OpenAICompatibleIntentNormalizer | None":
        """Build a normalizer from agent routing settings when credentials exist."""
        if getattr(settings, "agent_intent_normalizer_enabled", True) is False:
            return None
        config = AgentModelConfig.from_settings(settings)
        selection = config.resolve("fast")
        api_key = _api_key_for_selection(config, selection)
        base_url = _base_url_for_selection(config, selection, api_key)
        if not selection.api_key_configured or not api_key or not base_url:
            return None
        return cls(
            model=selection.model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=float(
                getattr(settings, "agent_intent_normalizer_timeout_seconds", 3.0)
            ),
        )

    def normalize(self, message: str) -> str | None:
        """Return a supported command-shaped message, or None when uncertain."""
        payload = self._payload(message)
        response = requests.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
            verify=default_ca_bundle_path(),
        )
        if (
            _should_retry_without_response_format(response)
            and "response_format" in payload
        ):
            payload.pop("response_format", None)
            response = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        response.raise_for_status()
        content = _response_content(response.json())
        if not content:
            return None
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        confidence = parsed.get("confidence")
        if not isinstance(confidence, int | float) or confidence < 0.65:
            return None
        normalized = parsed.get("normalized_message")
        return str(normalized).strip() if isinstance(normalized, str) else None

    def _payload(self, message: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "response_format": {"type": "json_object"},
        }
        model_options = model_chat_completion_options(self.model)
        max_tokens_parameter = model_options.get("max_tokens_parameter")
        if isinstance(max_tokens_parameter, str) and max_tokens_parameter:
            payload[max_tokens_parameter] = 300
            reasoning_effort = model_options.get("reasoning_effort")
            if isinstance(reasoning_effort, str) and reasoning_effort:
                payload["reasoning_effort"] = reasoning_effort
            verbosity = model_options.get("verbosity")
            if isinstance(verbosity, str) and verbosity:
                payload["verbosity"] = verbosity
            if model_options.get("supports_temperature", True):
                payload["temperature"] = 0
        else:
            payload["max_tokens"] = 300
            payload["temperature"] = 0
        return payload


def _api_key_for_selection(
    config: AgentModelConfig,
    selection: AgentModelSelection,
) -> str | None:
    if selection.source_tier in {"fast", "strong", "reasoning"}:
        tier_config = _tier_config(config, selection.source_tier)
        return tier_config.api_key or (
            config.openai_api_key
            if selection.base_url == config.openai_base_url
            else None
        )
    return config.openai_api_key


def _base_url_for_selection(
    config: AgentModelConfig,
    selection: AgentModelSelection,
    api_key: str | None,
) -> str | None:
    base_url = selection.base_url or config.openai_base_url
    if base_url:
        return base_url
    if api_key and _selection_uses_openai_fallback_key(config, selection):
        return DEFAULT_OPENAI_BASE_URL
    return None


def _selection_uses_openai_fallback_key(
    config: AgentModelConfig,
    selection: AgentModelSelection,
) -> bool:
    if selection.source_tier in {"fast", "strong", "reasoning"}:
        tier_config = _tier_config(config, selection.source_tier)
        return tier_config.api_key is None
    return True


def _tier_config(config: AgentModelConfig, tier: str) -> AgentTierModelConfig:
    return {
        "fast": config.fast,
        "strong": config.strong,
        "reasoning": config.reasoning,
    }[tier]


def _response_content(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    return content if isinstance(content, str) else None


def _should_retry_without_response_format(response: requests.Response) -> bool:
    if response.status_code != 400:
        return False
    body = response.text.casefold()
    return "response_format" in body and (
        "unsupported" in body
        or "not support" in body
        or "invalid parameter" in body
        or "unknown parameter" in body
    )
