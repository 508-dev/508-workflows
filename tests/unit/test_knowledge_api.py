"""Backend handler tests for Discord knowledge capture and queries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import Request

from five08.backend import api
from five08.knowledge.models import (
    KnowledgeCaptureCandidate,
    KnowledgeCaptureResponse,
    KnowledgeCitation,
    KnowledgeQueryResponse,
)


class _KnowledgeServiceStub:
    def create_capture(self, _payload: object) -> KnowledgeCaptureResponse:
        return KnowledgeCaptureResponse(
            status="requires_confirmation",
            message="Review and confirm.",
            draft_id="11111111-1111-1111-1111-111111111111",
            candidates=[
                KnowledgeCaptureCandidate(
                    question="Does the website auto deploy?",
                    answer="Yes, with Cloudflare Pages.",
                    source_message_ids=["100", "101"],
                )
            ],
            scope_type="org",
            scope_id="guild-1",
            visibility="org",
        )

    def confirm_capture(
        self,
        _draft_id: str,
        *,
        context: object,
        confirm: bool,
    ) -> KnowledgeCaptureResponse:
        return KnowledgeCaptureResponse(
            status="canceled" if not confirm else "saved",
            message="Canceled." if not confirm else "Saved 1 remembered answer(s).",
        )

    def answer(self, _payload: object) -> KnowledgeQueryResponse:
        return KnowledgeQueryResponse(
            status="answered",
            answer="Yes, it auto-deploys with Cloudflare Pages.",
            citations=[
                KnowledgeCitation(
                    citation_id="1",
                    source_type="memory",
                    title="Website deployment",
                    source_ref="discord:message:101",
                    url="https://discord.example/message/101",
                )
            ],
            confidence=0.8,
            public_safe=True,
            visibility="org",
        )


def _context() -> dict[str, object]:
    return {
        "discord_user_id": "caleb",
        "organization_id": "guild-1",
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "roles": ["Member"],
    }


def _configure(monkeypatch: pytest.MonkeyPatch) -> Mock:
    async def run_inline(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(api.settings, "api_shared_secret", "test-secret")
    monkeypatch.setattr(api, "_AGENT_REQUEST_TIMESTAMPS", {})
    monkeypatch.setattr(api, "_KNOWLEDGE_SERVICE", _KnowledgeServiceStub())
    monkeypatch.setattr(api.asyncio, "to_thread", run_inline)
    audit = Mock()
    monkeypatch.setattr(api, "_schedule_agent_audit_event", audit)
    return audit


def _request(payload: dict[str, Any], *, authorized: bool = True) -> Request:
    body = json.dumps(payload).encode()
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"x-api-secret", b"test-secret")] if authorized else []
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive,
    )


def _response_json(response: api.JSONResponse) -> dict[str, Any]:
    payload = json.loads(bytes(response.body))
    assert isinstance(payload, dict)
    return payload


async def test_knowledge_capture_handler_returns_frozen_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _configure(monkeypatch)
    response = await api.knowledge_capture_handler(
        _request(
            {
                "context": _context(),
                "source": {
                    "source_type": "discord_thread",
                    "source_ref": "https://discord.example/thread/1",
                    "title": "Website deployment",
                    "guild_id": "guild-1",
                    "channel_id": "channel-1",
                    "source_visibility": "org",
                },
                "messages": [
                    {
                        "message_id": "100",
                        "author_id": "caleb",
                        "author_name": "Caleb",
                        "content": "Does the website auto deploy?",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    {
                        "message_id": "101",
                        "author_id": "michael",
                        "author_name": "Michael",
                        "content": "Yes, with Cloudflare Pages.",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                ],
            }
        )
    )

    assert response.status_code == 202
    payload = _response_json(response)
    assert payload["status"] == "requires_confirmation"
    assert payload["candidates"][0]["answer"] == ("Yes, with Cloudflare Pages.")
    metadata = audit.call_args.kwargs["metadata"]
    assert metadata["message_count"] == 2
    assert "content" not in metadata


async def test_knowledge_query_handler_returns_typed_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _configure(monkeypatch)
    response = await api.knowledge_query_handler(
        _request(
            {
                "question": "Does the website auto deploy?",
                "context": _context(),
            }
        )
    )

    assert response.status_code == 200
    payload = _response_json(response)
    assert payload["public_safe"] is True
    assert payload["citations"][0]["source_type"] == "memory"
    assert audit.call_args.kwargs["metadata"]["citation_source_types"] == ["memory"]


async def test_knowledge_confirmation_handler_rejects_invalid_draft_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = _configure(monkeypatch)
    response = await api.knowledge_capture_confirmation_handler(
        _request({"context": _context(), "confirm": True}),
        "not-a-uuid",
    )

    assert response.status_code == 400
    assert _response_json(response) == {"error": "invalid_draft_id"}
    audit.assert_not_called()


async def test_knowledge_handlers_require_internal_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    response = await api.knowledge_query_handler(
        _request(
            {
                "question": "Does the website auto deploy?",
                "context": _context(),
            },
            authorized=False,
        )
    )

    assert response.status_code == 401


def test_knowledge_routes_are_registered() -> None:
    paths = {
        getattr(route, "path", None)
        for route in api.create_app(run_lifespan=False).routes
    }

    assert "/knowledge/captures" in paths
    assert "/knowledge/captures/{draft_id}/confirmation" in paths
    assert "/knowledge/queries" in paths
