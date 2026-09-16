"""Unit tests for source-grounded organizational knowledge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from five08.agent.models import AgentIdentityContext
from five08.knowledge.model import GroundedAnswerDraft
from five08.knowledge.models import (
    KnowledgeCaptureCandidate,
    KnowledgeCaptureRequest,
    KnowledgeDiscordMessage,
    KnowledgeDiscordSource,
    KnowledgeEvidence,
    KnowledgeFact,
    KnowledgeQueryRequest,
)
from five08.knowledge.service import KnowledgeService
from five08.knowledge.store import InMemoryKnowledgeStore


class _NoExternalSources:
    def __init__(
        self,
        *,
        capture_project_id: str | None = None,
        accessible_project_ids: list[str] | None = None,
    ) -> None:
        self.capture_project_id = capture_project_id
        self.project_ids = accessible_project_ids or []

    def resolve_actor_emails(self, _discord_user_id: str) -> list[str]:
        return ["member@508.dev"]

    def accessible_project_ids(
        self,
        *,
        actor_emails: list[str],
        include_all: bool,
    ) -> list[str]:
        return list(self.project_ids)

    def resolve_capture_project(
        self,
        *,
        organization_id: str,
        thread_id: str | None,
    ) -> str | None:
        return self.capture_project_id

    def search_outline(self, _question: str) -> list[KnowledgeEvidence]:
        return []

    def search_erp_projects(
        self,
        _question: str,
        *,
        actor_emails: list[str],
        include_all: bool,
    ) -> list[KnowledgeEvidence]:
        return []

    def search_crm(self, _question: str) -> list[KnowledgeEvidence]:
        return []


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "discord_server_id": "guild-1",
        "knowledge_enabled": True,
        "knowledge_capture_max_messages": 50,
        "knowledge_capture_max_characters": 20_000,
        "knowledge_capture_max_age_days": 7,
        "knowledge_capture_draft_ttl_seconds": 600,
        "knowledge_review_after_days": 180,
        "knowledge_query_max_evidence": 8,
        "knowledge_semantic_candidate_limit": 24,
        "knowledge_source_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_capture_is_private_unless_audience_is_explicit():
    store = InMemoryKnowledgeStore()
    request = _capture_request()
    payload = request.model_dump()
    payload.pop("requested_visibility")
    request = KnowledgeCaptureRequest.model_validate(payload)
    service = _service(store)
    preview = service.create_capture(request)
    assert preview.visibility == "private"
    service.confirm_capture(preview.draft_id, context=request.context, confirm=True)
    other = service.answer(
        KnowledgeQueryRequest(
            question="Does the main website auto deploy?",
            context=_context(user_id="another"),
        )
    )
    assert other.status == "insufficient"


def test_selected_discord_evidence_is_cited_privately_and_unselected_sources_are_ignored():
    from five08.knowledge.models import KnowledgeDiscordContext

    request = _capture_request()
    batch = KnowledgeDiscordContext(source=request.source, messages=request.messages)
    service = KnowledgeService(
        settings=_settings(knowledge_discord_channel_ids="channel-1"),
        store=InMemoryKnowledgeStore(),
        sources=_NoExternalSources(),
    )
    question = KnowledgeQueryRequest(
        question="Cloudflare Pages deploy", context=_context(), discord_sources=[batch]
    )
    result = service.answer(question)
    assert result.status == "answered"
    assert result.public_safe is False
    assert (
        result.citations[0].url == "https://discord.com/channels/guild-1/channel-1/101"
    )
    service.settings.knowledge_discord_channel_ids = "other-channel"
    assert service.answer(question).status == "insufficient"


def test_expired_discord_messages_and_cross_guild_snapshots_are_not_used():
    from five08.knowledge.models import KnowledgeDiscordContext

    capture = _capture_request()
    capture.messages[1].created_at = datetime.now(timezone.utc) - timedelta(days=91)
    capture.messages = [capture.messages[1]]
    service = KnowledgeService(
        settings=_settings(knowledge_discord_channel_ids="channel-1"),
        store=InMemoryKnowledgeStore(),
        sources=_NoExternalSources(),
    )
    request = KnowledgeQueryRequest(
        question="Cloudflare Pages deploy",
        context=_context(),
        discord_sources=[
            KnowledgeDiscordContext(source=capture.source, messages=capture.messages)
        ],
    )
    assert service.answer(request).status == "insufficient"
    capture.messages[0].created_at = datetime.now(timezone.utc)
    request.discord_sources[0].source.guild_id = "other-guild"
    assert service.answer(request).status == "insufficient"


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
    roles: list[str] | None = None,
    thread_id: str | None = None,
) -> KnowledgeCaptureRequest:
    now = datetime.now(timezone.utc)
    context = _context(user_id=actor_id, roles=roles)
    context.thread_id = thread_id
    return KnowledgeCaptureRequest(
        context=context,
        requested_visibility="project" if thread_id else "org",
        source=KnowledgeDiscordSource(
            source_type="discord_thread",
            source_ref="https://discord.example/thread/1",
            title="Website deployment",
            guild_id="guild-1",
            channel_id="channel-1",
            thread_id=thread_id,
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


class _SemanticModel:
    def __init__(self, *, abstain: bool = False) -> None:
        self.abstain = abstain
        self.seen_evidence: list[KnowledgeEvidence] = []

    def extract_candidates(
        self,
        _messages: list[KnowledgeDiscordMessage],
    ) -> list[KnowledgeCaptureCandidate]:
        return []

    def answer(
        self,
        *,
        question: str,
        evidence: list[KnowledgeEvidence],
    ) -> GroundedAnswerDraft:
        self.seen_evidence = evidence
        if self.abstain:
            return GroundedAnswerDraft(status="insufficient")
        return GroundedAnswerDraft(
            answer="The main website publishes through Cloudflare Pages.",
            evidence_ids=[evidence[0].evidence_id],
            confidence=0.9,
        )


def _service(
    store: InMemoryKnowledgeStore,
    *,
    settings: SimpleNamespace | None = None,
    sources: _NoExternalSources | None = None,
    model: object | None = None,
) -> KnowledgeService:
    return KnowledgeService(
        settings=settings or _settings(),  # type: ignore[arg-type]
        store=store,
        sources=sources or _NoExternalSources(),  # type: ignore[arg-type]
        model=model,  # type: ignore[arg-type]
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
            allow_private=True,
            allow_project=False,
            allow_org=True,
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


def test_model_cannot_make_private_context_public_by_citing_only_org_evidence() -> None:
    from five08.knowledge.models import KnowledgeDiscordContext

    class OrgCitationModel(_SemanticModel):
        def answer(self, *, question, evidence):
            assert any(item.visibility == "private" for item in evidence)
            public = next(item for item in evidence if item.visibility == "org")
            return GroundedAnswerDraft(
                answer="A private discussion influenced this answer.",
                evidence_ids=[public.evidence_id],
                confidence=0.9,
            )

    store = InMemoryKnowledgeStore()
    service = _service(store)
    capture = _capture_request()
    preview = service.create_capture(capture)
    service.confirm_capture(preview.draft_id, context=capture.context, confirm=True)
    service.model = OrgCitationModel()
    service.settings.knowledge_discord_channel_ids = "channel-1"
    answer = service.answer(
        KnowledgeQueryRequest(
            question="Does the main website auto deploy?",
            context=_context(),
            discord_sources=[
                KnowledgeDiscordContext(
                    source=capture.source, messages=capture.messages
                )
            ],
        )
    )
    assert answer.status == "answered"
    assert answer.public_safe is False
    assert answer.visibility == "private"


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
        allow_private=True,
        allow_project=False,
        allow_org=True,
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


def test_typo_tolerant_question_answers_without_slash_or_model() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    service.confirm_capture(preview.draft_id, context=_context(), confirm=True)

    answer = service.answer(
        KnowledgeQueryRequest(
            question="Does the main wesbite auto depoly?",
            context=_context(user_id="another-member"),
        )
    )

    assert answer.status == "answered"
    assert answer.answer == "Yes, it auto-deploys using Cloudflare Pages."


def test_semantic_model_matches_a_paraphrase_from_authorized_candidates() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    service.confirm_capture(preview.draft_id, context=_context(), confirm=True)
    model = _SemanticModel()
    semantic_service = _service(store, model=model)

    answer = semantic_service.answer(
        KnowledgeQueryRequest(
            question="Which system publishes homepage changes?",
            context=_context(user_id="another-member"),
        )
    )

    assert answer.status == "answered"
    assert answer.answer == "The main website publishes through Cloudflare Pages."
    assert model.seen_evidence
    assert model.seen_evidence[0].relevance == 0


def test_explicit_model_abstention_does_not_fall_back_to_an_excerpt() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    service.confirm_capture(preview.draft_id, context=_context(), confirm=True)

    answer = _service(store, model=_SemanticModel(abstain=True)).answer(
        KnowledgeQueryRequest(
            question="Does the website auto deploy?",
            context=_context(user_id="another-member"),
        )
    )

    assert answer.status == "insufficient"
    assert answer.citations == []


def test_knowledge_is_rejected_outside_the_configured_guild() -> None:
    service = _service(InMemoryKnowledgeStore())
    context = _context()
    context.organization_id = "guild-2"
    context.guild_id = "guild-2"

    answer = service.answer(
        KnowledgeQueryRequest(
            question="Does the website auto deploy?",
            context=context,
        )
    )

    assert answer.status == "denied"
    assert "configured Discord server" in answer.answer


def test_project_thread_capture_uses_trusted_mapping_and_rechecks_membership() -> None:
    sources = _NoExternalSources(
        capture_project_id="project-1",
        accessible_project_ids=["project-1"],
    )
    service = _service(InMemoryKnowledgeStore(), sources=sources)
    preview = service.create_capture(
        _capture_request(
            roles=["Project Manager"],
            thread_id="thread-1",
        )
    )

    assert preview.status == "requires_confirmation"
    assert preview.scope_type == "project"
    assert preview.scope_id == "project-1"
    assert preview.visibility == "project"
    assert preview.draft_id is not None

    sources.project_ids = []
    confirmation_context = _context(roles=["Project Manager"])
    confirmation_context.thread_id = "thread-1"
    confirmed = service.confirm_capture(
        preview.draft_id,
        context=confirmation_context,
        confirm=True,
    )

    assert confirmed.status == "denied"
    assert "no longer have access" in confirmed.message


def test_secret_like_project_capture_is_forced_to_private_scope() -> None:
    sources = _NoExternalSources(
        capture_project_id="project-1",
        accessible_project_ids=["project-1"],
    )
    service = _service(InMemoryKnowledgeStore(), sources=sources)

    preview = service.create_capture(
        _capture_request(
            answer="The deploy token=do-not-store-in-project-memory",
            roles=["Project Manager"],
            thread_id="thread-1",
        )
    )

    assert preview.scope_type == "user"
    assert preview.scope_id == "caleb"
    assert preview.visibility == "private"


def test_confirmation_fails_closed_after_feature_is_disabled() -> None:
    mutable_settings = _settings()
    service = _service(InMemoryKnowledgeStore(), settings=mutable_settings)
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    mutable_settings.knowledge_enabled = False

    confirmed = service.confirm_capture(
        preview.draft_id,
        context=_context(),
        confirm=True,
    )

    assert confirmed.status == "denied"
    assert confirmed.facts == []


def test_conflicting_duplicate_candidates_require_clarification() -> None:
    class _ConflictingModel:
        def extract_candidates(
            self,
            _messages: list[KnowledgeDiscordMessage],
        ) -> list[KnowledgeCaptureCandidate]:
            return [
                KnowledgeCaptureCandidate(
                    question="Does the website auto deploy?",
                    answer="It deploys with Coolify.",
                    source_message_ids=["100", "101"],
                ),
                KnowledgeCaptureCandidate(
                    question="Does the website auto deploy?",
                    answer="It deploys with Cloudflare Pages.",
                    source_message_ids=["100", "101"],
                ),
            ]

        def answer(
            self,
            *,
            question: str,
            evidence: list[KnowledgeEvidence],
        ) -> GroundedAnswerDraft | None:
            return None

    preview = _service(
        InMemoryKnowledgeStore(),
        model=_ConflictingModel(),
    ).create_capture(_capture_request())

    assert preview.status == "needs_clarification"
    assert preview.draft_id is None


def test_authorship_requires_an_exact_normalized_answer_match() -> None:
    message = KnowledgeDiscordMessage(
        message_id="101",
        author_id="michael",
        author_name="Michael",
        content="Yes, using Cloudflare Pages.",
        created_at=datetime.now(timezone.utc),
    )
    candidate = KnowledgeCaptureCandidate(
        question="Does the website auto deploy?",
        answer="Yes, using Cloudflare Pages, with cache invalidation.",
        source_message_ids=["101"],
    )

    status = KnowledgeService._verification_status(
        actor_id="michael",
        candidates=[candidate],
        messages=[message],
    )

    assert status == "source_recorded"


def test_visibility_capabilities_are_enforced_before_semantic_candidates() -> None:
    now = datetime.now(timezone.utc)
    store = InMemoryKnowledgeStore(
        facts=[
            KnowledgeFact(
                id="org-fact",
                organization_id="guild-1",
                scope_type="org",
                scope_id="guild-1",
                key="org_deploy",
                question="How does the org deploy?",
                answer="Organization answer",
                visibility="org",
                created_by="caleb",
                created_at=now,
                updated_at=now,
            ),
            KnowledgeFact(
                id="private-fact",
                organization_id="guild-1",
                scope_type="user",
                scope_id="caleb",
                key="private_deploy",
                question="How do I deploy?",
                answer="Private answer",
                visibility="private",
                created_by="caleb",
                created_at=now,
                updated_at=now,
            ),
            KnowledgeFact(
                id="project-fact",
                organization_id="guild-1",
                scope_type="project",
                scope_id="project-1",
                key="project_deploy",
                question="How does the project deploy?",
                answer="Project answer",
                visibility="project",
                created_by="caleb",
                created_at=now,
                updated_at=now,
            ),
        ]
    )

    evidence = store.search_evidence(
        question="deploy",
        organization_id="guild-1",
        actor_id="caleb",
        project_ids=["project-1"],
        allow_private=False,
        allow_project=True,
        allow_org=False,
        semantic_candidate_limit=24,
    )

    assert [item.evidence_id for item in evidence] == ["memory:project-fact"]


def test_consumed_draft_is_redacted_and_expired_draft_is_purged() -> None:
    store = InMemoryKnowledgeStore()
    service = _service(store)
    preview = service.create_capture(_capture_request())
    assert preview.draft_id is not None
    assert preview.expires_at is not None

    canceled = service.confirm_capture(
        preview.draft_id,
        context=_context(),
        confirm=False,
    )
    stored = store.get_capture_draft(preview.draft_id)

    assert canceled.status == "canceled"
    assert stored is not None
    assert stored.messages == []
    assert stored.candidates == []
    assert (
        store.purge_capture_drafts(now=preview.expires_at + timedelta(seconds=1)) == 1
    )
    assert store.get_capture_draft(preview.draft_id) is None
