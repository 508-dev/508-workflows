"""Focused API and worker contracts for approval-gated wiki editing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from fastapi import Request

from five08.backend import api
from five08.knowledge.models import KnowledgeEvidence
from five08.queue import EnqueuedJob
from five08.wiki_editing.assertions import (
    WIKI_ASSERTION_HEADER,
    create_wiki_action_assertion,
)
from five08.wiki_editing.models import (
    WikiEditConflictError,
    WikiEditNotFoundError,
    WikiEditPermissionError,
    WikiEditResponse,
    WikiEditStateError,
    WikiAuthoringWorkItem,
)
from five08.wiki_editing.service import (
    WikiEditingConfigurationError,
    WikiEditingValidationError,
    WikiProposalStart,
)
from five08.worker import jobs


_PROPOSAL_ID = "11111111-1111-1111-1111-111111111111"
_REQUEST_ID = "22222222-2222-2222-2222-222222222222"
_REVISION_ID = "33333333-3333-3333-3333-333333333333"


class _WikiEditingServiceStub:
    def __init__(self) -> None:
        self.create_payload: object | None = None
        self.revise_payload: object | None = None
        self.action_payload: object | None = None
        self.error: Exception | None = None

    def create(self, payload: object) -> WikiProposalStart:
        self.create_payload = payload
        if self.error is not None:
            raise self.error
        return WikiProposalStart(
            response=_response(proposal_id=_PROPOSAL_ID, status="queued"),
            should_enqueue=True,
        )

    def revise(self, payload: object) -> WikiProposalStart:
        self.revise_payload = payload
        if self.error is not None:
            raise self.error
        return WikiProposalStart(
            response=_response(proposal_id=_REVISION_ID, status="queued"),
            should_enqueue=True,
        )

    def status(self, payload: object) -> WikiEditResponse:
        self.action_payload = payload
        if self.error is not None:
            raise self.error
        return _response(proposal_id=_PROPOSAL_ID, status="proposed", source_count=2)

    def publish(self, payload: object) -> WikiEditResponse:
        self.action_payload = payload
        if self.error is not None:
            raise self.error
        return _response(proposal_id=_PROPOSAL_ID, status="published")

    def acknowledge_review(self, payload: object) -> WikiEditResponse:
        self.action_payload = payload
        if self.error is not None:
            raise self.error
        return _response(proposal_id=_PROPOSAL_ID, status="proposed")

    def cancel(self, payload: object) -> WikiEditResponse:
        self.action_payload = payload
        if self.error is not None:
            raise self.error
        return _response(proposal_id=_PROPOSAL_ID, status="canceled")

    def mark_authoring_enqueue_failed(self, payload: object) -> WikiEditResponse:
        self.action_payload = payload
        if self.error is not None:
            raise self.error
        return _response(proposal_id=_PROPOSAL_ID, status="failed")


def _response(
    *,
    proposal_id: str,
    status: str,
    source_count: int = 0,
) -> WikiEditResponse:
    action = (
        "review"
        if status == "queued"
        else "publish"
        if status == "proposed"
        else "none"
    )
    return WikiEditResponse(
        proposal_id=proposal_id,
        request_id=_REQUEST_ID,
        status=status,  # type: ignore[arg-type]
        message="Safe workflow status.",
        action=action,  # type: ignore[arg-type]
        source_count=source_count,
    )


def _context() -> dict[str, object]:
    return {
        "discord_user_id": "writer-1",
        "organization_id": "guild-1",
        "guild_id": "guild-1",
        "channel_id": "channel-1",
        "roles": ["Workflows Engineer"],
    }


def _create_payload() -> dict[str, object]:
    return {
        "context": _context(),
        "instruction": "PRIVATE INSTRUCTION: publish the confidential launch plan",
        "request_idempotency_key": "interaction-1",
        "selected_conversation": [
            {
                "provenance": {
                    "source_type": "discord_thread",
                    "source_ref": "https://discord.example/thread/1",
                    "title": "Launch discussion",
                    "guild_id": "guild-1",
                    "channel_id": "channel-1",
                },
                "organization_visible_text": "PRIVATE SOURCE: launch date is secret",
            }
        ],
    }


def _request(
    payload: dict[str, object],
    *,
    path: str = "/wiki/updates",
    queue: object | None = None,
    authorized: bool = True,
) -> Request:
    body = json.dumps(payload).encode()
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"x-api-secret", b"test-secret")] if authorized else []
    headers.append(
        (
            WIKI_ASSERTION_HEADER.lower().encode("ascii"),
            create_wiki_action_assertion(
                "test-wiki-assertion-secret",
                method="POST",
                path=path,
                payload=payload,
            ).encode("ascii"),
        )
    )
    scope: dict[str, object] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(queue=queue)),
    }
    return Request(scope, receive)


def _response_json(response: api.JSONResponse) -> dict[str, Any]:
    payload = json.loads(bytes(response.body))
    assert isinstance(payload, dict)
    return payload


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    service: _WikiEditingServiceStub,
) -> tuple[Mock, Mock]:
    async def run_inline(function: Any, *args: Any, **kwargs: Any) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(api.settings, "api_shared_secret", "test-secret")
    monkeypatch.setattr(
        api.settings,
        "wiki_editing_assertion_secret",
        "test-wiki-assertion-secret",
    )
    monkeypatch.setattr(api, "_WIKI_EDITING_SERVICE", service)
    monkeypatch.setattr(api.asyncio, "to_thread", run_inline)
    audit = Mock()
    enqueue = Mock(return_value=EnqueuedJob(id="job-1", created=True))
    monkeypatch.setattr(api, "_schedule_agent_audit_event", audit)
    monkeypatch.setattr(api, "enqueue_job", enqueue)
    return audit, enqueue


async def test_wiki_create_requires_secret_and_enqueues_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _WikiEditingServiceStub()
    audit, enqueue = _configure(monkeypatch, service)
    queue = object()

    unauthorized = await api.wiki_create_handler(
        _request(_create_payload(), queue=queue, authorized=False)
    )
    assert unauthorized.status_code == 401
    assert service.create_payload is None
    enqueue.assert_not_called()
    audit.assert_not_called()

    response = await api.wiki_create_handler(_request(_create_payload(), queue=queue))

    assert response.status_code == 202
    assert _response_json(response)["proposal_id"] == _PROPOSAL_ID
    assert service.create_payload is not None
    enqueue.assert_called_once_with(
        queue=queue,
        fn=api.author_wiki_edit_proposal_job,
        args=(_PROPOSAL_ID, "guild-1"),
        settings=api.settings,
        idempotency_key=f"wiki-author:{_PROPOSAL_ID}",
    )
    metadata = audit.call_args.kwargs["metadata"]
    assert metadata == {
        "status": "queued",
        "action": "review",
        "source_count": 1,
        "proposal_id": _PROPOSAL_ID,
        "request_id": _REQUEST_ID,
    }
    serialized_audit = json.dumps(metadata)
    assert "PRIVATE INSTRUCTION" not in serialized_audit
    assert "PRIVATE SOURCE" not in serialized_audit


async def test_wiki_revision_uses_route_id_and_new_proposal_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _WikiEditingServiceStub()
    _audit, enqueue = _configure(monkeypatch, service)
    queue = object()

    response = await api.wiki_revise_handler(
        _request(
            {
                "context": _context(),
                "proposal_id": "body-id-must-not-win",
                "instruction": "PRIVATE REVISION DIRECTION",
            },
            path=f"/wiki/updates/{_PROPOSAL_ID}/revise",
            queue=queue,
        ),
        _PROPOSAL_ID,
    )

    assert response.status_code == 202
    assert getattr(service.revise_payload, "proposal_id") == _PROPOSAL_ID
    enqueue.assert_called_once_with(
        queue=queue,
        fn=api.author_wiki_edit_proposal_job,
        args=(_REVISION_ID, "guild-1"),
        settings=api.settings,
        idempotency_key=f"wiki-author:{_REVISION_ID}",
    )


async def test_wiki_enqueue_failure_becomes_a_reviewable_failed_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _WikiEditingServiceStub()
    _audit, enqueue = _configure(monkeypatch, service)
    enqueue.side_effect = RuntimeError("broker unavailable")

    response = await api.wiki_create_handler(
        _request(_create_payload(), queue=object())
    )

    assert response.status_code == 202
    assert _response_json(response)["status"] == "failed"
    assert getattr(service.action_payload, "proposal_id") == _PROPOSAL_ID


async def test_wiki_acknowledgement_binds_review_to_the_route_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _WikiEditingServiceStub()
    _audit, _enqueue = _configure(monkeypatch, service)
    review_id = "a" * 16

    response = await api.wiki_acknowledge_review_handler(
        _request(
            {
                "context": _context(),
                "proposal_id": "body-id-must-not-win",
                "review_id": review_id,
            },
            path=f"/wiki/updates/{_PROPOSAL_ID}/acknowledge-review",
        ),
        _PROPOSAL_ID,
    )

    assert response.status_code == 200
    assert getattr(service.action_payload, "proposal_id") == _PROPOSAL_ID
    assert getattr(service.action_payload, "review_id") == review_id


@pytest.mark.parametrize(
    ("error", "status_code", "error_name"),
    [
        (
            WikiEditingConfigurationError("private config"),
            503,
            "wiki_editing_unavailable",
        ),
        (WikiEditPermissionError("private permission"), 403, "forbidden"),
        (WikiEditNotFoundError("private missing"), 404, "wiki_proposal_not_found"),
        (WikiEditConflictError("private conflict"), 409, "wiki_edit_conflict"),
        (WikiEditStateError("private state"), 409, "wiki_edit_state_conflict"),
        (WikiEditingValidationError("private validation"), 422, "invalid_wiki_update"),
    ],
)
async def test_wiki_action_maps_domain_errors_without_exposing_details(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    error_name: str,
) -> None:
    service = _WikiEditingServiceStub()
    service.error = error
    audit, _enqueue = _configure(monkeypatch, service)

    response = await api.wiki_status_handler(
        _request(
            {"context": _context()},
            path=f"/wiki/updates/{_PROPOSAL_ID}/status",
        ),
        _PROPOSAL_ID,
    )

    assert response.status_code == status_code
    assert _response_json(response) == {"error": error_name}
    metadata = audit.call_args.kwargs["metadata"]
    assert set(metadata) <= {
        "proposal_id",
        "request_id",
        "status",
        "action",
        "source_count",
    }
    assert "private" not in json.dumps(metadata)


def test_wiki_routes_are_registered() -> None:
    paths = {
        getattr(route, "path", None)
        for route in api.create_app(run_lifespan=False).routes
    }

    assert {
        "/wiki/updates",
        "/wiki/updates/{proposal_id}/status",
        "/wiki/updates/{proposal_id}/revise",
        "/wiki/updates/{proposal_id}/acknowledge-review",
        "/wiki/updates/{proposal_id}/publish",
        "/wiki/updates/{proposal_id}/cancel",
    } <= paths


def test_worker_builds_remote_sandboxed_omp_authoring_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_settings = SimpleNamespace(
        wiki_authoring_configured=True,
        resolved_wiki_omp_sandbox_url="http://wiki_omp_sandbox:8080",
        wiki_omp_sandbox_token="sandbox-token",
        wiki_omp_model="openrouter/model",
        wiki_omp_thinking="high",
        wiki_omp_startup_timeout_seconds=12.0,
        wiki_omp_authoring_timeout_seconds=45.0,
        wiki_outline_collection_id="collection-1",
    )
    captured: dict[str, Any] = {}
    store = object()
    knowledge_store = object()
    runner = object()
    writer = object()

    monkeypatch.setattr(jobs, "settings", worker_settings)
    monkeypatch.setattr(jobs, "PostgresWikiEditingStore", lambda value: store)
    monkeypatch.setattr(jobs, "PostgresKnowledgeStore", lambda value: knowledge_store)
    monkeypatch.setattr(jobs, "build_outline_writer_client", lambda value: writer)

    def build_runner(**kwargs: Any) -> object:
        captured.update(kwargs)
        return runner

    monkeypatch.setattr(jobs, "SandboxedOmpWikiAuthoringRunner", build_runner)

    service = jobs._build_wiki_editing_service()

    assert service.store is store
    assert service.authoring_runner is runner
    knowledge_search = captured.pop("knowledge_search")
    outline_client_factory = captured.pop("outline_client_factory")
    assert callable(knowledge_search)
    assert callable(outline_client_factory)
    assert captured == {
        "sandbox_url": "http://wiki_omp_sandbox:8080",
        "sandbox_token": "sandbox-token",
        "model": "openrouter/model",
        "thinking": "high",
        "startup_timeout_seconds": 12.0,
        "authoring_timeout_seconds": 45.0,
        "allowed_collection_id": "collection-1",
    }
    assert service.outline_client_factory() is writer


def test_worker_org_knowledge_callback_requires_fresh_high_trust_evidence() -> None:
    store = Mock()
    updated_at = datetime.now(timezone.utc)
    store.search_evidence.return_value = [
        KnowledgeEvidence(
            evidence_id="org-evidence",
            source_type="memory",
            source_ref="memory:org",
            title="Shared deployment decision",
            excerpt="Use the shared release checklist.",
            url="https://knowledge.example/org",
            visibility="org",
            authority=0.9,
            stale=False,
            updated_at=updated_at,
        ),
        KnowledgeEvidence(
            evidence_id="private-evidence",
            source_type="memory",
            source_ref="memory:private",
            title="Private note",
            excerpt="Do not expose this.",
            visibility="private",
        ),
        KnowledgeEvidence(
            evidence_id="project-evidence",
            source_type="memory",
            source_ref="memory:project",
            title="Project note",
            excerpt="Do not expose this either.",
            visibility="project",
            authority=1.0,
            stale=False,
            updated_at=updated_at,
        ),
        KnowledgeEvidence(
            evidence_id="low-trust-evidence",
            source_type="memory",
            source_ref="memory:low-trust",
            title="Unverified shared note",
            excerpt="Do not expose this unverified note.",
            visibility="org",
            authority=0.8,
            stale=False,
            updated_at=updated_at,
        ),
        KnowledgeEvidence(
            evidence_id="stale-evidence",
            source_type="memory",
            source_ref="memory:stale",
            title="Stale shared note",
            excerpt="Do not expose this stale note.",
            visibility="org",
            authority=1.0,
            stale=True,
            updated_at=updated_at,
        ),
        KnowledgeEvidence(
            evidence_id="missing-metadata-evidence",
            source_type="memory",
            source_ref="memory:missing-metadata",
            title="Metadata-free shared note",
            excerpt="Do not expose this metadata-free note.",
            visibility="org",
            authority=1.0,
            # Deliberately omit ``stale``. A source adapter that only gets a
            # default value is not trusted as having supplied freshness state.
            updated_at=updated_at,
        ),
        KnowledgeEvidence(
            evidence_id="missing-timestamp-evidence",
            source_type="memory",
            source_ref="memory:missing-timestamp",
            title="Timestamp-free shared note",
            excerpt="Do not expose this timestamp-free note.",
            visibility="org",
            authority=1.0,
            stale=False,
        ),
        KnowledgeEvidence(
            evidence_id="non-memory-evidence",
            source_type="outline",
            source_ref="outline:shared",
            title="Unexpected source type",
            excerpt="Do not expose this unexpected source type.",
            visibility="org",
            authority=1.0,
            stale=False,
            updated_at=updated_at,
        ),
    ]
    search = jobs._build_wiki_org_knowledge_search(store)
    work = cast(
        WikiAuthoringWorkItem,
        SimpleNamespace(
            request=SimpleNamespace(
                organization_id="guild-1",
                actor_id="writer-1",
            )
        ),
    )

    materials = search("Which release checklist applies?", work)

    store.search_evidence.assert_called_once_with(
        question="Which release checklist applies?",
        organization_id="guild-1",
        actor_id="writer-1",
        project_ids=(),
        allow_private=False,
        allow_project=False,
        allow_org=True,
        limit=4,
        semantic_candidate_limit=0,
    )
    assert len(materials) == 1
    assert materials[0].source.source_type == "memory_fact"
    assert materials[0].source.source_ref == "memory:org"
    assert materials[0].source.source_url == "https://knowledge.example/org"
    assert materials[0].source.title == "Shared deployment decision"
    assert materials[0].text == "Use the shared release checklist."
    assert materials[0].visibility == "org"
    assert materials[0].knowledge_authority == 0.9
    assert materials[0].knowledge_stale is False
    assert materials[0].knowledge_updated_at == updated_at


def test_worker_authoring_job_never_calls_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Mock()
    service.author_proposal.return_value = _response(
        proposal_id=_PROPOSAL_ID,
        status="proposed",
    )
    monkeypatch.setattr(jobs, "_build_wiki_editing_service", Mock(return_value=service))

    result = jobs.author_wiki_edit_proposal_job(_PROPOSAL_ID, "guild-1")

    assert result["status"] == "proposed"
    service.author_proposal.assert_called_once_with(
        _PROPOSAL_ID,
        organization_id="guild-1",
    )
    service.publish.assert_not_called()
