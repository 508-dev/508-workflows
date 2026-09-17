"""Focused contracts for the remote-only wiki OMP sandbox boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from five08.worker.wiki_omp_sandbox import SandboxedOmpWikiAuthoringRunner
from five08.wiki_editing.models import (
    WikiAuthoringWorkItem,
    WikiEditRequestInput,
    WikiOmpRunMetadata,
    WikiProposalCreate,
    WikiSourceReference,
)
from five08.wiki_editing.omp import WikiAuthoringError, WikiAuthoringMaterial
from five08.wiki_editing.store import InMemoryWikiEditingStore


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
        transport=lambda *_args: _draft_response(source_ids=["private:99"]),
    )

    with pytest.raises(
        WikiAuthoringError, match="outside the approved material bundle"
    ):
        runner.author(_work_item(), metadata=_metadata())


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
        knowledge_search=knowledge_search,
        transport=transport,
    )

    runner.author(_work_item(), metadata=_metadata())

    materials = captured["materials"]
    assert isinstance(materials, list)
    source_refs = {
        item["source"]["source_ref"]
        for item in materials
        if isinstance(item, dict)
        and isinstance(item.get("source"), dict)
        and isinstance(item["source"].get("source_ref"), str)
    }
    assert "memory:approved" in source_refs
    assert "memory:private" not in source_refs
    assert "memory:low-trust" not in source_refs
