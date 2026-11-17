"""Wiki route tests for the bot-only role/identity assertion boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Request

from five08.backend import api
from five08.wiki_editing.assertions import (
    create_wiki_action_assertion,
)
from five08.wiki_editing.models import WikiEditResponse
from five08.wiki_editing.service import WikiProposalStart


def _payload() -> dict[str, object]:
    return {
        "context": {
            "discord_user_id": "attacker-claimed-owner",
            "organization_id": "guild-1",
            "guild_id": "guild-1",
            "roles": ["Steering Committee"],
        },
        "instruction": "Attempt a wiki update.",
        "request_idempotency_key": "interaction-1",
    }


def _request(
    payload: dict[str, object],
    *,
    assertion: str | None,
) -> Request:
    body = json.dumps(payload).encode()
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"x-api-secret", b"widely-shared-secret")]
    if assertion is not None:
        headers.append((b"x-wiki-assertion", assertion.encode()))
    scope: dict[str, object] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/wiki/updates",
        "raw_path": b"/wiki/updates",
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(queue=None)),
    }
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_wiki_create_rejects_forged_context_with_only_api_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    service = SimpleNamespace(create=Mock())
    monkeypatch.setattr(api.settings, "api_shared_secret", "widely-shared-secret")
    monkeypatch.setattr(
        api.settings, "wiki_editing_assertion_secret", "bot-only-secret"
    )
    monkeypatch.setattr(api, "_WIKI_EDITING_SERVICE", service)

    response = await api.wiki_create_handler(_request(payload, assertion=None))

    assert response.status_code == 401
    assert json.loads(bytes(response.body)) == {"error": "invalid_wiki_assertion"}
    service.create.assert_not_called()


@pytest.mark.asyncio
async def test_wiki_create_accepts_a_body_bound_bot_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    response_model = WikiEditResponse(
        proposal_id="11111111-1111-1111-1111-111111111111",
        status="queued",
        message="Queued.",
        action="review",
    )
    service = SimpleNamespace(
        create=Mock(
            return_value=WikiProposalStart(
                response=response_model,
                should_enqueue=False,
            )
        )
    )
    monkeypatch.setattr(api.settings, "api_shared_secret", "widely-shared-secret")
    monkeypatch.setattr(
        api.settings, "wiki_editing_assertion_secret", "bot-only-secret"
    )
    monkeypatch.setattr(api, "_WIKI_EDITING_SERVICE", service)
    assertion = create_wiki_action_assertion(
        "bot-only-secret",
        method="POST",
        path="/wiki/updates",
        payload=payload,
    )

    response = await api.wiki_create_handler(_request(payload, assertion=assertion))

    assert response.status_code == 200
    service.create.assert_called_once()
