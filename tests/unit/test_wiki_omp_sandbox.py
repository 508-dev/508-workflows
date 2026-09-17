"""Focused contracts for the remote-only wiki OMP sandbox boundary."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, cast
from unittest.mock import Mock

import pytest

from five08.clients.outline import (
    OutlineClient,
    OutlineDocument,
    OutlineDocumentSummary,
    OutlineSearchResult,
)
from five08.worker.wiki_omp_sandbox import SandboxedOmpWikiAuthoringRunner
from five08.wiki_editing.models import (
    WikiAuthoringWorkItem,
    WikiEditRequestInput,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiSourceReference,
)
from five08.wiki_editing.omp import (
    WikiAuthoringError,
    WikiAuthoringMaterial,
    WikiAuthoringTransientError,
)
from five08.wiki_editing.store import InMemoryWikiEditingStore


class _EmptyOutlineClient:
    def search_documents(
        self,
        *,
        query: str,
        limit: int,
    ) -> list[OutlineSearchResult]:
        del query, limit
        return []

    def get_document(self, *, document_id: str) -> OutlineDocument:
        raise AssertionError(f"unexpected Outline document fetch: {document_id}")


def _empty_outline_client_factory() -> OutlineClient:
    return cast(OutlineClient, _EmptyOutlineClient())


def _work_item() -> WikiAuthoringWorkItem:
    store = InMemoryWikiEditingStore()
    request, _created = store.create_or_get_request(
        WikiEditRequestInput(
            organization_id="guild-1",
            actor_id="writer-1",
            instruction="Document the approved release decision.",
            request_idempotency_key="request-1",
        )
    )
    proposal = store.create_proposal(
        WikiProposalCreate(
            request_id=request.id,
            organization_id="guild-1",
            target_action="create",
        )
    )
    work = store.claim_authoring(
        proposal.id,
        organization_id="guild-1",
        omp_metadata=_metadata(),
    )
    assert work is not None
    return work


def _metadata() -> WikiOmpRunMetadata:
    return WikiOmpRunMetadata(
        session_id="no-session:test",
        model="openrouter/test",
        provider="openrouter",
        run_id="run-1",
    )


def _draft_response(*, source_ids: list[str]) -> dict[str, object]:
    return {
        "protocol_version": "v1",
        "draft": {
            "action": "create",
            "target_document_id": None,
            "title": "Release guide",
            "text": "Use the approved release checklist.",
            "summary": "Captures the approved release decision.",
            "source_ids": source_ids,
        },
    }


def test_remote_sandbox_receives_only_bounded_materials_not_worker_secrets() -> None:
    captured: dict[str, Any] = {}

    def transport(
        endpoint: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        startup_timeout: float,
        authoring_timeout: float,
    ) -> dict[str, object]:
        captured.update(
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            startup_timeout=startup_timeout,
            authoring_timeout=authoring_timeout,
        )
        return _draft_response(source_ids=["request:1"])

    runner = SandboxedOmpWikiAuthoringRunner(
        sandbox_url="http://wiki_omp_sandbox:8080",
        sandbox_token="sandbox-token",
        model="openrouter/test",
        startup_timeout_seconds=7.0,
        authoring_timeout_seconds=21.0,
        outline_client_factory=_empty_outline_client_factory,
        allowed_collection_id="collection-1",
        transport=transport,
    )

    draft = runner.author(_work_item(), metadata=_metadata())

    assert draft.source_refs[0].source_ref.startswith("wiki-request:")
    assert captured["endpoint"] == "http://wiki_omp_sandbox:8080/v1/wiki-authoring/runs"
    assert captured["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer sandbox-token",
        "Content-Type": "application/json",
        "X-Wiki-OMP-Protocol": "v1",
    }
    assert captured["startup_timeout"] == 7.0
    assert captured["authoring_timeout"] == 21.0
    serialized = json.dumps(captured["payload"])
    assert "OPENROUTER_API_KEY" not in serialized
    assert "openrouter-secret" not in serialized
    assert "WIKI_OMP_COMMAND" not in serialized
    assert "outline_admin" not in serialized


def test_remote_sandbox_cannot_cite_unapproved_material() -> None:
    runner = SandboxedOmpWikiAuthoringRunner(
        sandbox_url="http://wiki_omp_sandbox:8080",
        sandbox_token="sandbox-token",
        model="openrouter/test",
        outline_client_factory=_empty_outline_client_factory,
        allowed_collection_id="collection-1",
        transport=lambda *_args: _draft_response(source_ids=["private:99"]),
    )

    with pytest.raises(
        WikiAuthoringError, match="outside the approved material bundle"
    ):
        runner.author(_work_item(), metadata=_metadata())


def test_remote_sandbox_response_uses_a_monotonic_total_deadline() -> None:
    socket = SimpleNamespace(settimeout=Mock())
    response = SimpleNamespace(
        raw=SimpleNamespace(_connection=SimpleNamespace(sock=socket)),
        iter_content=lambda **_kwargs: iter((b'{"protocol_version":"v1"}',)),
    )

    body = SandboxedOmpWikiAuthoringRunner._bounded_response_body(
        response,  # type: ignore[arg-type]
        deadline=time.monotonic() + 1.0,
    )

    assert body == b'{"protocol_version":"v1"}'
    timeout_seconds = [call.args[0] for call in socket.settimeout.call_args_list]
    assert timeout_seconds
    assert all(0 < value <= 1.0 for value in timeout_seconds)

    with pytest.raises(WikiAuthoringTransientError, match="total response deadline"):
        SandboxedOmpWikiAuthoringRunner._bounded_response_body(
            response,  # type: ignore[arg-type]
            deadline=time.monotonic() - 1.0,
        )


def test_remote_sandbox_excludes_untrusted_knowledge_material() -> None:
    captured: dict[str, object] = {}
    updated_at = datetime.now(timezone.utc)

    def knowledge_search(
        _question: str,
        _work: WikiAuthoringWorkItem,
    ) -> list[WikiAuthoringMaterial]:
        return [
            WikiAuthoringMaterial(
                source=WikiSourceReference(
                    source_type="memory_fact",
                    source_ref="memory:private",
                    title="Private note",
                ),
                text="Never disclose this.",
                visibility="request",
            ),
            WikiAuthoringMaterial(
                source=WikiSourceReference(
                    source_type="memory_fact",
                    source_ref="memory:low-trust",
                    title="Unverified note",
                ),
                text="Never disclose this either.",
                visibility="org",
                knowledge_authority=0.8,
                knowledge_stale=False,
                knowledge_updated_at=updated_at,
            ),
            WikiAuthoringMaterial(
                source=WikiSourceReference(
                    source_type="memory_fact",
                    source_ref="memory:approved",
                    title="Approved release decision",
                ),
                text="Use the shared release checklist.",
                visibility="org",
                knowledge_authority=0.9,
                knowledge_stale=False,
                knowledge_updated_at=updated_at,
            ),
        ]

    def transport(
        _endpoint: str,
        _headers: Mapping[str, str],
        payload: Mapping[str, object],
        _startup_timeout: float,
        _authoring_timeout: float,
    ) -> dict[str, object]:
        captured.update(payload)
        return _draft_response(source_ids=["request:1"])

    runner = SandboxedOmpWikiAuthoringRunner(
        sandbox_url="http://wiki_omp_sandbox:8080",
        sandbox_token="sandbox-token",
        model="openrouter/test",
        outline_client_factory=_empty_outline_client_factory,
        allowed_collection_id="collection-1",
        knowledge_search=knowledge_search,
        transport=transport,
    )

    runner.author(_work_item(), metadata=_metadata())

    materials = captured["materials"]
    assert isinstance(materials, list)
    serialized = json.dumps(materials)
    assert "Use the shared release checklist." in serialized
    assert "Never disclose this." not in serialized
    assert "Never disclose this either." not in serialized
    assert "memory:approved" not in serialized
    assert "memory:private" not in serialized
    assert "memory:low-trust" not in serialized


def test_remote_sandbox_receives_only_full_allowed_collection_documents() -> None:
    captured: dict[str, object] = {}

    class OutlineSearchClient:
        def __init__(self) -> None:
            self.search_calls: list[tuple[str, int]] = []
            self.document_calls: list[str] = []
            self.search_results = [
                OutlineSearchResult(
                    document=OutlineDocumentSummary(
                        id="private-doc",
                        title="PRIVATE search title",
                        url="https://outline.example/private-search",
                        updated_at=None,
                    ),
                    context="PRIVATE search excerpt must stay in the worker.",
                    ranking=1.0,
                ),
                OutlineSearchResult(
                    document=OutlineDocumentSummary(
                        id="other-collection-doc",
                        title="OTHER COLLECTION search title",
                        url="https://outline.example/other-search",
                        updated_at=None,
                    ),
                    context="OTHER COLLECTION search excerpt must stay in the worker.",
                    ranking=0.9,
                ),
                OutlineSearchResult(
                    document=OutlineDocumentSummary(
                        id="shared-doc",
                        title="Shared search title",
                        url="https://outline.example/shared-search",
                        updated_at=None,
                    ),
                    context="This selector excerpt is not an authoring source.",
                    ranking=0.8,
                ),
            ]
            self.documents = {
                "private-doc": OutlineDocument(
                    id="private-doc",
                    title="PRIVATE full document title",
                    text="PRIVATE full document text",
                    url="https://outline.example/private-full",
                    collection_id="private-collection",
                    parent_document_id=None,
                    revision=1,
                    updated_at=None,
                ),
                "other-collection-doc": OutlineDocument(
                    id="other-collection-doc",
                    title="OTHER COLLECTION full document title",
                    text="OTHER COLLECTION full document text",
                    url="https://outline.example/other-full",
                    collection_id="other-collection",
                    parent_document_id=None,
                    revision=1,
                    updated_at=None,
                ),
                "shared-doc": OutlineDocument(
                    id="shared-doc",
                    title="Shared release guide",
                    text="The shared release checklist is approved.",
                    url="https://outline.example/shared-full",
                    collection_id="collection-1",
                    parent_document_id=None,
                    revision=1,
                    updated_at=None,
                ),
            }

        def search_documents(
            self,
            *,
            query: str,
            limit: int,
        ) -> list[OutlineSearchResult]:
            self.search_calls.append((query, limit))
            return self.search_results

        def get_document(self, *, document_id: str) -> OutlineDocument:
            self.document_calls.append(document_id)
            return self.documents[document_id]

    outline = OutlineSearchClient()

    def transport(
        _endpoint: str,
        _headers: Mapping[str, str],
        payload: Mapping[str, object],
        _startup_timeout: float,
        _authoring_timeout: float,
    ) -> dict[str, object]:
        captured.update(payload)
        return _draft_response(source_ids=["request:1", "related-outline:2"])

    runner = SandboxedOmpWikiAuthoringRunner(
        sandbox_url="http://wiki_omp_sandbox:8080",
        sandbox_token="sandbox-token",
        model="openrouter/test",
        outline_client_factory=lambda: outline,  # type: ignore[arg-type]
        allowed_collection_id="collection-1",
        transport=transport,
    )

    runner.author(_work_item(), metadata=_metadata())

    assert outline.search_calls == [
        ("Document the approved release decision.", 4),
    ]
    assert outline.document_calls == [
        "private-doc",
        "other-collection-doc",
        "shared-doc",
    ]
    materials = captured["materials"]
    assert isinstance(materials, list)
    serialized = json.dumps(materials)
    assert "Shared release guide" in serialized
    assert "The shared release checklist is approved." in serialized
    assert "shared-doc" not in serialized
    for forbidden in (
        "private-doc",
        "PRIVATE search title",
        "PRIVATE search excerpt",
        "PRIVATE full document title",
        "PRIVATE full document text",
        "private-full",
        "other-collection-doc",
        "OTHER COLLECTION search title",
        "OTHER COLLECTION search excerpt",
        "OTHER COLLECTION full document title",
        "OTHER COLLECTION full document text",
        "other-full",
        "Shared search title",
        "selector excerpt",
        "shared-search",
    ):
        assert forbidden not in serialized
