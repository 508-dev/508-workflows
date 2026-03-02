"""Best-effort Discord webhook transport for operator visibility."""

from __future__ import annotations

import contextlib
import json
import logging
from urllib import error, request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)


class DiscordWebhookLogger:
    """Send short messages to a Discord webhook URL without affecting workflows."""

    _MAX_CONTENT_LENGTH = 2000

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

    def send(self, *, content: str) -> None:
        """Best-effort send one Discord message."""
        if not self.enabled:
            return

        query_params = self._request_query_params()
        body = json.dumps(
            {
                "content": self._normalize_content(content),
                "allowed_mentions": {"parse": []},
            },
        ).encode("utf-8")
        req = request.Request(
            self._request_url(query_params),
            data=body,
            headers={"Content-Type": "application/json"},
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
