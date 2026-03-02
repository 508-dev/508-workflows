"""Unit tests for Discord webhook logger transport behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from five08.discord_webhook import DiscordWebhookLogger


def _urlopen_context(*, status: int = 204) -> MagicMock:
    response = MagicMock()
    response.status = status
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = None
    return context


def test_send_appends_wait_query_param() -> None:
    logger = DiscordWebhookLogger(
        "https://discord.com/api/webhooks/1/token",
        timeout_seconds=1.5,
    )

    with patch(
        "five08.discord_webhook.request.urlopen",
        return_value=_urlopen_context(),
    ) as mock_urlopen:
        logger.send(content="hello world")

    request_obj = mock_urlopen.call_args.args[0]
    timeout = mock_urlopen.call_args.kwargs["timeout"]
    parsed = urlparse(request_obj.full_url)
    query = parse_qs(parsed.query)

    assert timeout == 1.5
    assert query["wait"] == ["true"]
    assert json.loads(request_obj.data.decode("utf-8")) == {"content": "hello world"}


def test_send_preserves_existing_wait_query_param() -> None:
    logger = DiscordWebhookLogger(
        "https://discord.com/api/webhooks/1/token?wait=false",
    )

    with patch(
        "five08.discord_webhook.request.urlopen",
        return_value=_urlopen_context(),
    ) as mock_urlopen:
        logger.send(content="job completed")

    request_obj = mock_urlopen.call_args.args[0]
    parsed = urlparse(request_obj.full_url)
    query = parse_qs(parsed.query)

    assert query["wait"] == ["false"]
