"""Best-effort Discord webhook transport for operator visibility."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any
from urllib import error, request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)


class DiscordWebhookLogger:
    """Send short messages to a Discord webhook URL without affecting workflows."""

    _MAX_CONTENT_LENGTH = 2000
    _MAX_EMBED_COUNT = 10
    _DEFAULT_USER_AGENT = "508-workflows-discord-webhook/1.0 (+https://508.dev)"

    def __init__(
        self,
        webhook_url: str | None,
        timeout_seconds: float = 2.0,
        *,
        wait_for_response: bool = True,
    ) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.timeout_seconds = timeout_seconds
        self.wait_for_response = wait_for_response

    @property
    def enabled(self) -> bool:
        """Return whether webhook logging is configured."""
        return bool(self.webhook_url)

    def send(
        self,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        username: str | None = None,
    ) -> None:
        """Best-effort send one Discord message."""
        if not self.enabled:
            return

        payload = self._build_payload(
            content=content,
            embeds=embeds,
            username=username,
        )
        if not payload:
            return

        query_params = self._request_query_params()
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._request_url(query_params),
            data=body,
            headers={
                "Content-Type": "application/json",
                # Discord is fronted by Cloudflare and may reject default Python UAs.
                "User-Agent": self._DEFAULT_USER_AGENT,
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                if response.status >= 400:
                    logger.warning(
                        "Discord webhook returned status=%s for message",
                        response.status,
                    )
        except error.HTTPError as exc:
            body_text = ""
            with contextlib.suppress(Exception):
                body_text = exc.read().decode("utf-8", errors="replace")[:240]
            logger.warning(
                "Discord webhook failed status=%s body=%s",
                exc.code,
                body_text,
            )
        except error.URLError as exc:
            logger.warning("Discord webhook request failed error=%s", exc)
        except (
            Exception
        ) as exc:  # pragma: no cover - defensive for transport edge-cases
            logger.warning("Discord webhook failed error=%s", exc)

    def _normalize_content(self, content: str) -> str:
        """Normalize and safely truncate message content for Discord API limits."""
        normalized = (content or "").strip()
        if not normalized:
            return "(empty message)"

        if len(normalized) > self._MAX_CONTENT_LENGTH:
            return f"{normalized[: self._MAX_CONTENT_LENGTH - 3]}..."

        return normalized

    @staticmethod
    def _normalize_embed(embed: dict[str, Any]) -> dict[str, Any]:
        """Drop empty or non-JSON-serializable embed fields."""
        normalized: dict[str, Any] = {}
        for key, value in embed.items():
            if value is None:
                continue
            if key == "fields" and isinstance(value, list):
                normalized_fields: list[dict[str, Any]] = []
                for raw_field in value:
                    if not isinstance(raw_field, dict):
                        continue
                    name = str(raw_field.get("name", "")).strip()
                    if not name:
                        continue
                    field_value = str(raw_field.get("value", "")).strip()
                    field_entry: dict[str, Any] = {"name": name, "value": field_value}
                    if isinstance(raw_field.get("inline"), bool):
                        field_entry["inline"] = raw_field["inline"]
                    normalized_fields.append(field_entry)
                if normalized_fields:
                    normalized["fields"] = normalized_fields
                continue

            if isinstance(value, (str, int, float, bool, list, dict)):
                if isinstance(value, str) and not value.strip():
                    continue
                normalized[key] = value
        return normalized

    def _build_payload(
        self,
        *,
        content: str | None,
        embeds: list[dict[str, Any]] | None,
        username: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"allowed_mentions": {"parse": []}}
        if content is not None:
            payload["content"] = self._normalize_content(content)
        if username:
            trimmed_username = username.strip()
            if trimmed_username:
                payload["username"] = trimmed_username
        if embeds:
            payload["embeds"] = [
                self._normalize_embed(embed)
                for embed in embeds[: self._MAX_EMBED_COUNT]
                if embed
            ]
            payload["embeds"] = [embed for embed in payload["embeds"] if embed]
            if not payload["embeds"]:
                payload.pop("embeds")
        if "content" not in payload and "embeds" not in payload:
            return {}
        return payload

    def _request_query_params(self) -> dict[str, str]:
        """Build webhook query params while enforcing text-only message behavior."""
        parsed = urlparse(self.webhook_url)
        query_params = {
            key: value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key == "wait"
        }
        if self.wait_for_response and "wait" not in query_params:
            query_params["wait"] = "true"
        return query_params

    def _request_url(self, query_params: dict[str, str]) -> str:
        """Build webhook URL with request query params."""
        parsed = urlparse(self.webhook_url)
        return urlunparse(parsed._replace(query=urlencode(query_params)))
