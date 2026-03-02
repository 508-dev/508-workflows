"""Best-effort Discord webhook transport for operator visibility."""

from __future__ import annotations

import contextlib
import json
import logging
from urllib import error, request

logger = logging.getLogger(__name__)


class DiscordWebhookLogger:
    """Send short messages to a Discord webhook URL without affecting workflows."""

    def __init__(self, webhook_url: str | None, timeout_seconds: float = 2.0) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        """Return whether webhook logging is configured."""
        return bool(self.webhook_url)

    def send(self, *, content: str) -> None:
        """Best-effort send one Discord message."""
        if not self.enabled:
            return

        payload = {"content": content}
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.webhook_url,
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
