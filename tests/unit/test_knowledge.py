"""Unit tests for source-grounded organizational knowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from five08.agent.models import AgentIdentityContext
from five08.knowledge.models import (
    KnowledgeCaptureRequest,
    KnowledgeDiscordMessage,
    KnowledgeDiscordSource,
    KnowledgeQueryRequest,
)
from five08.knowledge.service import KnowledgeService
from five08.knowledge.store import InMemoryKnowledgeStore


class _NoExternalSources:
    def resolve_actor_emails(self, _discord_user_id: str) -> list[str]:
        return []

    def accessible_project_ids(
        self,
        *,
        actor_emails: list[str],
        include_all: bool,
    ) -> list[str]:
        return []

    def search_outline(self, _question: str) -> list[object]:
        return []

    def search_erp_projects(
        self,
        _question: str,
        *,
        actor_emails: list[str],
        include_all: bool,
    ) -> list[object]:
        return []

    def search_crm(self, _question: str) -> list[object]:
        return []


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        knowledge_enabled=True,
        knowledge_capture_max_messages=50,
        knowledge_capture_max_characters=20_000,
        knowledge_capture_max_age_days=7,
        knowledge_capture_draft_ttl_seconds=600,
        knowledge_review_after_days=180,
        knowledge_query_max_evidence=8,
    )


def _context(
    *, user_id: str = "caleb", roles: list[str] | None = None
) -> AgentIdentityContext:
    return AgentIdentityContext(
        discord_user_id=user_id,
        organization_id="guild-1",
        guild_id="guild-1",
        channel_id="channel-1",
        roles=["Member"] if roles is None else roles,
    )


def _capture_request(
    *,
    actor_id: str = "caleb",
    answer: str = "Yes, it auto-deploys using Cloudflare Pages.",
) -> KnowledgeCaptureRequest:
    now = datetime.now(timezone.utc)
    return KnowledgeCaptureRequest(
        context=_context(user_id=actor_id),
        source=KnowledgeDiscordSource(
            source_type="discord_thread",
            source_ref="https://discord.example/thread/1",
            title="Website deployment",
            guild_id="guild-1",
            channel_id="channel-1",
            source_visibility="org",
        ),
        messages=[
            KnowledgeDiscordMessage(
                message_id="100",
                author_id="caleb",
                author_name="Caleb",
                content="Does our main website auto deploy now?",
                created_at=now,
                jump_url="https://discord.example/message/100",
            ),
            KnowledgeDiscordMessage(
                message_id="101",
                author_id="michael",
                author_name="Michael",
                content=answer,
                created_at=now,
                jump_url="https://discord.example/message/101",
            ),
        ],
    )


def _service(store: InMemoryKnowledgeStore) -> KnowledgeService:
    return KnowledgeService(
        settings=_settings(),  # type: ignore[arg-type]
        store=store,
        sources=_NoExternalSources(),  # type: ignore[arg-type]
    )


def test_capture_requires_confirmation_then_answers_with_provenance() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)

    preview = service.create_capture(_capture_request())

    assert preview.status == "requires_confirmation"
    assert preview.draft_id is not None
    assert preview.visibility == "org"
    assert preview.candidates[0].question == "Does our main website auto deploy now?"
    assert preview.candidates[0].answer == (
        "Yes, it auto-deploys using Cloudflare Pages."
    )
    assert (
        store.search_evidence(
            question="website deploy",
            organization_id="guild-1",
            actor_id="caleb",
        )
        == []
    )

    confirmed = service.confirm_capture(
        preview.draft_id,
        context=_context(),
        confirm=True,
    )
    answer = service.answer(
        KnowledgeQueryRequest(
            question="Does the main website auto deploy?",
            context=_context(user_id="another-member"),
        )
    )

    assert confirmed.status == "saved"
    assert confirmed.facts[0].verification_status == "source_recorded"
    assert answer.status == "answered"
    assert answer.answer == "Yes, it auto-deploys using Cloudflare Pages."
    assert answer.public_safe is True
    assert answer.citations[0].source_type == "memory"
    assert answer.citations[0].url == "https://discord.example/message/101"


def test_capture_confirmation_rejects_a_different_actor() -> None:
    service = _service(InMemoryKnowledgeStore())
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None

    with pytest.raises(PermissionError, match="another actor"):
        service.confirm_capture(
            preview.draft_id,
            context=_context(user_id="michael"),
            confirm=True,
        )


def test_capture_confirmation_rejects_a_different_organization() -> None:
    service = _service(InMemoryKnowledgeStore())
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    other_org_context = _context()
    other_org_context.organization_id = "guild-2"
    other_org_context.guild_id = "guild-2"

    with pytest.raises(PermissionError, match="original organization"):
        service.confirm_capture(
            preview.draft_id,
            context=other_org_context,
            confirm=True,
        )


def test_secret_like_org_capture_is_forced_to_private_user_scope() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)

    preview = service.create_capture(
        _capture_request(answer="The deploy token=do-not-store-in-org-memory")
    )

    assert preview.status == "requires_confirmation"
    assert preview.scope_type == "user"
    assert preview.scope_id == "caleb"
    assert preview.visibility == "private"
    assert preview.draft_id is not None
    service.confirm_capture(preview.draft_id, context=_context(), confirm=True)

    answer = service.answer(
        KnowledgeQueryRequest(
            question="What is the deploy token?",
            context=_context(),
        )
    )

    assert answer.status == "answered"
    assert answer.visibility == "private"
    assert answer.public_safe is False


def test_repeated_question_supersedes_changed_answer() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    first = service.create_capture(_capture_request(answer="It deploys with Coolify."))
    assert first.draft_id is not None
    service.confirm_capture(first.draft_id, context=_context(), confirm=True)

    second = service.create_capture(
        _capture_request(answer="It now deploys with Cloudflare Pages.")
    )
    assert second.draft_id is not None
    confirmed = service.confirm_capture(
        second.draft_id,
        context=_context(),
        confirm=True,
    )
    evidence = store.search_evidence(
        question="website deploy",
        organization_id="guild-1",
        actor_id="caleb",
    )

    assert confirmed.facts[0].supersedes_id is not None
    assert [item.excerpt for item in evidence] == [
        "It now deploys with Cloudflare Pages."
    ]


def test_author_saving_their_own_answer_marks_author_confirmed() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    request = _capture_request(actor_id="michael")
    preview = service.create_capture(request)
    assert preview.draft_id is not None

    confirmed = service.confirm_capture(
        preview.draft_id,
        context=_context(user_id="michael"),
        confirm=True,
    )

    assert confirmed.facts[0].verification_status == "author_confirmed"


def test_non_member_cannot_capture_or_query_organization_knowledge() -> None:
    service = _service(InMemoryKnowledgeStore())
    capture = _capture_request()
    capture.context.roles = []

    preview = service.create_capture(capture)
    answer = service.answer(
        KnowledgeQueryRequest(
            question="Does the website auto deploy?",
            context=_context(roles=[]),
        )
    )

    assert preview.status == "denied"
    assert answer.status == "denied"
