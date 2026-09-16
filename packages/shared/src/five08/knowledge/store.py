"""Durable and in-memory storage adapters for organizational knowledge."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, cast
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from five08.agent.memory import DEFAULT_MEMORY_RETENTION_DAYS
from five08.agent.models import (
    AgentContextSourceType,
    MemoryFact,
    MemoryScopeType,
    MemoryVisibility,
)
from five08.knowledge.models import (
    KnowledgeCaptureCandidate,
    KnowledgeCaptureDraft,
    KnowledgeDiscordMessage,
    KnowledgeDiscordSource,
    KnowledgeEvidence,
    KnowledgeFact,
    KnowledgeScopeType,
    KnowledgeVerificationStatus,
    KnowledgeVisibility,
)
from five08.queue import get_postgres_connection
from five08.settings import SharedSettings

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")


class KnowledgeStore(Protocol):
    """Persistence contract owned by the knowledge service."""

    def create_capture_draft(self, draft: KnowledgeCaptureDraft) -> None:
        """Persist one immutable capture preview."""

    def get_capture_draft(self, draft_id: str) -> KnowledgeCaptureDraft | None:
        """Return one capture draft if it exists."""

    def cancel_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> KnowledgeCaptureDraft:
        """Atomically cancel a draft owned by the actor."""

    def confirm_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        review_after: datetime,
        now: datetime | None = None,
    ) -> tuple[KnowledgeCaptureDraft, list[KnowledgeFact]]:
        """Atomically consume a draft and persist its frozen candidates."""

    def search_evidence(
        self,
        *,
        question: str,
        organization_id: str,
        actor_id: str,
        project_ids: Iterable[str] = (),
        limit: int = 8,
        now: datetime | None = None,
    ) -> list[KnowledgeEvidence]:
        """Return visible active knowledge matching a question."""


class InMemoryKnowledgeStore:
    """Thread-safe test adapter implementing knowledge and agent memory stores."""

    def __init__(
        self,
        *,
        facts: Iterable[KnowledgeFact] | None = None,
    ) -> None:
        self._facts = {fact.id: fact for fact in facts or []}
        self._sources: dict[str, KnowledgeEvidence] = {}
        self._drafts: dict[str, KnowledgeCaptureDraft] = {}
        self._memory_values: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create_capture_draft(self, draft: KnowledgeCaptureDraft) -> None:
        with self._lock:
            self._drafts[draft.id] = draft.model_copy(deep=True)

    def get_capture_draft(self, draft_id: str) -> KnowledgeCaptureDraft | None:
        with self._lock:
            draft = self._drafts.get(draft_id)
            return draft.model_copy(deep=True) if draft is not None else None

    def cancel_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> KnowledgeCaptureDraft:
        comparison_time = now or datetime.now(timezone.utc)
        with self._lock:
            draft = self._required_draft(draft_id, actor_id=actor_id)
            if draft.confirmed_fact_ids:
                raise ValueError("knowledge capture draft was already confirmed")
            if draft.consumed_at is not None:
                return draft.model_copy(deep=True)
            canceled = draft.model_copy(update={"consumed_at": comparison_time})
            self._drafts[draft_id] = canceled
            return canceled.model_copy(deep=True)

    def confirm_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        review_after: datetime,
        now: datetime | None = None,
    ) -> tuple[KnowledgeCaptureDraft, list[KnowledgeFact]]:
        comparison_time = now or datetime.now(timezone.utc)
        with self._lock:
            draft = self._required_draft(draft_id, actor_id=actor_id)
            if draft.expires_at <= comparison_time and draft.consumed_at is None:
                raise TimeoutError("knowledge capture draft expired")
            if draft.confirmed_fact_ids:
                facts = [
                    self._facts[fact_id]
                    for fact_id in draft.confirmed_fact_ids
                    if fact_id in self._facts
                ]
                return draft.model_copy(deep=True), [
                    fact.model_copy(deep=True) for fact in facts
                ]
            if draft.consumed_at is not None:
                raise ValueError("knowledge capture draft was already canceled")

            facts: list[KnowledgeFact] = []
            for candidate in draft.candidates:
                fact = self._upsert_candidate(
                    draft=draft,
                    candidate=candidate,
                    review_after=review_after,
                    now=comparison_time,
                )
                facts.append(fact)
            confirmed = draft.model_copy(
                update={
                    "consumed_at": comparison_time,
                    "confirmed_fact_ids": [fact.id for fact in facts],
                }
            )
            self._drafts[draft_id] = confirmed
            return confirmed.model_copy(deep=True), [
                fact.model_copy(deep=True) for fact in facts
            ]

    def search_evidence(
        self,
        *,
        question: str,
        organization_id: str,
        actor_id: str,
        project_ids: Iterable[str] = (),
        limit: int = 8,
        now: datetime | None = None,
    ) -> list[KnowledgeEvidence]:
        comparison_time = now or datetime.now(timezone.utc)
        query_tokens = _search_tokens(question)
        if not query_tokens:
            return []
        visible_projects = set(project_ids)
        scored: list[tuple[float, KnowledgeFact]] = []
        with self._lock:
            for fact in self._facts.values():
                if fact.organization_id != organization_id or fact.status != "active":
                    continue
                if fact.expires_at is not None and fact.expires_at <= comparison_time:
                    continue
                if not _knowledge_fact_visible(
                    fact,
                    actor_id=actor_id,
                    project_ids=visible_projects,
                ):
                    continue
                searchable = " ".join(
                    [
                        fact.key,
                        fact.question or "",
                        fact.answer,
                        *fact.aliases,
                    ]
                ).casefold()
                matched = sum(1 for token in query_tokens if token in searchable)
                if query_tokens and matched == 0:
                    continue
                relevance = matched / max(len(query_tokens), 1)
                scored.append((relevance, fact))

            scored.sort(
                key=lambda item: (
                    item[0],
                    item[1].confidence,
                    item[1].updated_at,
                ),
                reverse=True,
            )
            evidence: list[KnowledgeEvidence] = []
            for relevance, fact in scored[: max(1, min(limit, 20))]:
                source = self._sources.get(fact.id)
                evidence.append(
                    KnowledgeEvidence(
                        evidence_id=f"memory:{fact.id}",
                        source_type="memory",
                        source_ref=(
                            source.source_ref if source is not None else fact.id
                        ),
                        title=(
                            source.title
                            if source is not None
                            else fact.question or fact.key
                        ),
                        excerpt=fact.answer,
                        url=source.url if source is not None else None,
                        visibility=fact.visibility,
                        authority=_verification_authority(
                            fact.verification_status,
                        ),
                        relevance=relevance,
                        updated_at=fact.updated_at,
                        stale=bool(
                            fact.review_after and fact.review_after <= comparison_time
                        ),
                    )
                )
            return evidence

    def remember_fact(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        key: str,
        value_json: dict[str, Any],
        visibility: MemoryVisibility,
        source_type: str,
        source_ref: str,
        source_excerpt: str | None,
        created_by: str,
        verification_status: str,
        organization_id: str | None = None,
        confidence: float = 1.0,
        expires_at: datetime | None = None,
    ) -> MemoryFact:
        now = datetime.now(timezone.utc)
        fact_id = str(uuid4())
        answer = _memory_value_text(value_json)
        effective_expires_at = expires_at or now + timedelta(
            days=DEFAULT_MEMORY_RETENTION_DAYS
        )
        fact = KnowledgeFact(
            id=fact_id,
            organization_id=organization_id or scope_id,
            scope_type=cast(KnowledgeScopeType, scope_type),
            scope_id=scope_id,
            kind="fact",
            key=key,
            answer=answer,
            visibility=cast(KnowledgeVisibility, visibility),
            verification_status=_knowledge_verification(verification_status),
            confidence=confidence,
            created_by=created_by,
            expires_at=effective_expires_at,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._facts[fact.id] = fact
            self._memory_values[fact.id] = dict(value_json)
            self._sources[fact.id] = KnowledgeEvidence(
                evidence_id=f"memory:{fact.id}",
                source_type="memory",
                source_ref=source_ref,
                title=key,
                excerpt=source_excerpt or answer,
                visibility=cast(KnowledgeVisibility, visibility),
            )
        return _memory_fact_from_knowledge(
            fact,
            value_json,
            source_type,
            source_ref,
            source_excerpt_hash=_source_excerpt_hash(source_excerpt),
        )

    def list_facts(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        visible_to_user_id: str,
        visible_to_project_id: str | None,
        visible_to_org_id: str | None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryFact]:
        comparison_time = now or datetime.now(timezone.utc)
        with self._lock:
            facts = []
            for fact in self._facts.values():
                if fact.scope_type != scope_type or fact.scope_id != scope_id:
                    continue
                if not include_deleted and (
                    fact.deleted_at is not None or fact.status != "active"
                ):
                    continue
                if fact.expires_at is not None and fact.expires_at <= comparison_time:
                    continue
                if fact.visibility == "private" and fact.scope_id != visible_to_user_id:
                    continue
                if (
                    fact.visibility == "project"
                    and fact.scope_id != visible_to_project_id
                ):
                    continue
                if fact.visibility == "org" and fact.scope_id != visible_to_org_id:
                    continue
                source = self._sources.get(fact.id)
                facts.append(
                    _memory_fact_from_knowledge(
                        fact,
                        self._memory_values.get(fact.id, {"text": fact.answer}),
                        (
                            "memory_fact"
                            if source is None or source.source_type == "memory"
                            else source.source_type
                        ),
                        source.source_ref if source is not None else fact.id,
                        source_excerpt_hash=(
                            _source_excerpt_hash(source.excerpt)
                            if source is not None
                            else None
                        ),
                    )
                )
            return facts

    def forget_fact(
        self,
        *,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        comparison_time = now or datetime.now(timezone.utc)
        with self._lock:
            fact = self._facts.get(fact_id)
            if fact is None:
                raise KeyError(f"Memory fact {fact_id} was not found")
            if not actor_is_admin and fact.created_by != actor_id:
                raise PermissionError("Memory fact can only be deleted by its creator")
            deleted = fact.model_copy(
                update={
                    "status": "deleted",
                    "deleted_at": comparison_time,
                    "updated_at": comparison_time,
                }
            )
            self._facts[fact_id] = deleted
            source = self._sources.get(fact.id)
            return _memory_fact_from_knowledge(
                deleted,
                self._memory_values.get(fact.id, {"text": fact.answer}),
                (
                    "memory_fact"
                    if source is None or source.source_type == "memory"
                    else source.source_type
                ),
                source.source_ref if source is not None else fact.id,
                source_excerpt_hash=(
                    _source_excerpt_hash(source.excerpt) if source is not None else None
                ),
            )

    def _required_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
    ) -> KnowledgeCaptureDraft:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise KeyError("knowledge capture draft was not found")
        if draft.actor_id != actor_id:
            raise PermissionError("knowledge capture draft belongs to another actor")
        return draft

    def _upsert_candidate(
        self,
        *,
        draft: KnowledgeCaptureDraft,
        candidate: KnowledgeCaptureCandidate,
        review_after: datetime,
        now: datetime,
    ) -> KnowledgeFact:
        dedupe_key = _dedupe_key(candidate.question)
        existing = next(
            (
                fact
                for fact in self._facts.values()
                if fact.organization_id == draft.organization_id
                and fact.scope_type == draft.scope_type
                and fact.scope_id == draft.scope_id
                and fact.status == "active"
                and _dedupe_key(fact.question or fact.key) == dedupe_key
            ),
            None,
        )
        if existing is not None and _normalize(existing.answer) == _normalize(
            candidate.answer
        ):
            refreshed = existing.model_copy(
                update={
                    "review_after": review_after,
                    "updated_at": now,
                    "confidence": max(existing.confidence, candidate.confidence),
                }
            )
            self._facts[existing.id] = refreshed
            fact = refreshed
        else:
            if existing is not None:
                self._facts[existing.id] = existing.model_copy(
                    update={"status": "superseded", "updated_at": now}
                )
            fact = KnowledgeFact(
                organization_id=draft.organization_id,
                scope_type=draft.scope_type,
                scope_id=draft.scope_id,
                key=_fact_key(candidate.question),
                question=candidate.question,
                answer=candidate.answer,
                aliases=candidate.aliases,
                visibility=draft.visibility,
                verification_status=draft.verification_status,
                confidence=candidate.confidence,
                created_by=draft.actor_id,
                review_after=review_after,
                supersedes_id=existing.id if existing is not None else None,
                created_at=now,
                updated_at=now,
            )
            self._facts[fact.id] = fact

        selected = _selected_messages(draft.messages, candidate.source_message_ids)
        self._sources[fact.id] = _source_evidence(
            draft.source,
            selected,
            fact.id,
            visibility=fact.visibility,
        )
        return fact


class PostgresKnowledgeStore:
    """PostgreSQL source of truth for knowledge and existing memory tools."""

    def __init__(self, settings: SharedSettings) -> None:
        self.settings = settings

    def create_capture_draft(self, draft: KnowledgeCaptureDraft) -> None:
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO knowledge_capture_drafts (
                        id,
                        organization_id,
                        actor_id,
                        scope_type,
                        scope_id,
                        visibility,
                        verification_status,
                        source_payload,
                        message_payload,
                        candidate_payload,
                        expires_at,
                        created_at
                    ) VALUES (
                        %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        draft.id,
                        draft.organization_id,
                        draft.actor_id,
                        draft.scope_type,
                        draft.scope_id,
                        draft.visibility,
                        draft.verification_status,
                        Jsonb(draft.source.model_dump(mode="json")),
                        Jsonb(
                            [
                                message.model_dump(mode="json")
                                for message in draft.messages
                            ]
                        ),
                        Jsonb(
                            [
                                candidate.model_dump(mode="json")
                                for candidate in draft.candidates
                            ]
                        ),
                        draft.expires_at,
                        draft.created_at,
                    ),
                )

    def get_capture_draft(self, draft_id: str) -> KnowledgeCaptureDraft | None:
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM knowledge_capture_drafts
                    WHERE id = %s::uuid
                    """,
                    (draft_id,),
                )
                row = cursor.fetchone()
        return _capture_draft_from_row(row) if row is not None else None

    def cancel_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> KnowledgeCaptureDraft:
        comparison_time = now or datetime.now(timezone.utc)
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                draft = self._locked_draft(cursor, draft_id, actor_id=actor_id)
                if draft.confirmed_fact_ids:
                    raise ValueError("knowledge capture draft was already confirmed")
                if draft.consumed_at is None:
                    cursor.execute(
                        """
                        UPDATE knowledge_capture_drafts
                        SET consumed_at = %s
                        WHERE id = %s::uuid
                        RETURNING *
                        """,
                        (comparison_time, draft_id),
                    )
                    row = cursor.fetchone()
                    if row is None:  # pragma: no cover - row is locked above
                        raise RuntimeError("failed canceling knowledge capture draft")
                    return _capture_draft_from_row(row)
                return draft

    def confirm_capture_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        review_after: datetime,
        now: datetime | None = None,
    ) -> tuple[KnowledgeCaptureDraft, list[KnowledgeFact]]:
        comparison_time = now or datetime.now(timezone.utc)
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                draft = self._locked_draft(cursor, draft_id, actor_id=actor_id)
                if draft.expires_at <= comparison_time and draft.consumed_at is None:
                    raise TimeoutError("knowledge capture draft expired")
                if draft.confirmed_fact_ids:
                    return draft, self._facts_by_ids(cursor, draft.confirmed_fact_ids)
                if draft.consumed_at is not None:
                    raise ValueError("knowledge capture draft was already canceled")

                facts = [
                    self._upsert_candidate(
                        cursor,
                        draft=draft,
                        candidate=candidate,
                        review_after=review_after,
                        now=comparison_time,
                    )
                    for candidate in draft.candidates
                ]
                fact_ids = [fact.id for fact in facts]
                cursor.execute(
                    """
                    UPDATE knowledge_capture_drafts
                    SET consumed_at = %s, confirmed_fact_ids = %s::uuid[]
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (comparison_time, fact_ids, draft_id),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - row is locked above
                    raise RuntimeError("failed confirming knowledge capture draft")
                return _capture_draft_from_row(row), facts

    def search_evidence(
        self,
        *,
        question: str,
        organization_id: str,
        actor_id: str,
        project_ids: Iterable[str] = (),
        limit: int = 8,
        now: datetime | None = None,
    ) -> list[KnowledgeEvidence]:
        comparison_time = now or datetime.now(timezone.utc)
        normalized_limit = max(1, min(limit, 20))
        visible_projects = [project_id for project_id in project_ids if project_id]
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT
                        mf.*,
                        ts_rank_cd(
                            mf.search_document,
                            websearch_to_tsquery('english', %s)
                        ) AS relevance,
                        mfs.source_type AS citation_source_type,
                        mfs.source_ref AS citation_source_ref,
                        mfs.source_title AS citation_source_title,
                        mfs.source_url AS citation_source_url,
                        mfs.source_excerpt AS citation_source_excerpt,
                        mfs.source_updated_at AS citation_source_updated_at
                    FROM memory_facts mf
                    LEFT JOIN LATERAL (
                        SELECT *
                        FROM memory_fact_sources
                        WHERE fact_id = mf.id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) mfs ON TRUE
                    WHERE mf.organization_id = %s
                      AND mf.status = 'active'
                      AND mf.deleted_at IS NULL
                      AND (mf.expires_at IS NULL OR mf.expires_at > %s)
                      AND (
                          (mf.visibility = 'org' AND mf.scope_type = 'org')
                          OR (
                              mf.visibility = 'private'
                              AND mf.scope_type = 'user'
                              AND mf.scope_id = %s
                          )
                          OR (
                              mf.visibility = 'project'
                              AND mf.scope_type = 'project'
                              AND mf.scope_id = ANY(%s)
                          )
                      )
                      AND mf.search_document @@ websearch_to_tsquery('english', %s)
                    ORDER BY
                        relevance DESC,
                        CASE mf.verification_status
                            WHEN 'authoritative' THEN 5
                            WHEN 'admin_confirmed' THEN 4
                            WHEN 'author_confirmed' THEN 3
                            WHEN 'user_confirmed' THEN 2
                            ELSE 1
                        END DESC,
                        mf.confidence DESC,
                        mf.updated_at DESC
                    LIMIT %s
                    """,
                    (
                        question,
                        organization_id,
                        comparison_time,
                        actor_id,
                        visible_projects,
                        question,
                        normalized_limit,
                    ),
                )
                rows = cursor.fetchall()
        return [_knowledge_evidence_from_row(row, now=comparison_time) for row in rows]

    def remember_fact(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        key: str,
        value_json: dict[str, Any],
        visibility: MemoryVisibility,
        source_type: str,
        source_ref: str,
        source_excerpt: str | None,
        created_by: str,
        verification_status: str,
        organization_id: str | None = None,
        confidence: float = 1.0,
        expires_at: datetime | None = None,
    ) -> MemoryFact:
        fact_id = str(uuid4())
        now = datetime.now(timezone.utc)
        answer = _memory_value_text(value_json)
        resolved_org_id = organization_id or scope_id
        effective_expires_at = expires_at or now + timedelta(
            days=DEFAULT_MEMORY_RETENTION_DAYS
        )
        normalized_verification = _knowledge_verification(verification_status)
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO memory_facts (
                        id,
                        organization_id,
                        scope_type,
                        scope_id,
                        kind,
                        key,
                        answer,
                        value_json,
                        visibility,
                        created_by,
                        verification_status,
                        confidence,
                        expires_at,
                        search_document,
                        created_at,
                        updated_at
                    ) VALUES (
                        %s::uuid, %s, %s, %s, 'fact', %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        to_tsvector('english', %s),
                        %s, %s
                    )
                    RETURNING *
                    """,
                    (
                        fact_id,
                        resolved_org_id,
                        scope_type,
                        scope_id,
                        key,
                        answer,
                        Jsonb(value_json),
                        visibility,
                        created_by,
                        normalized_verification,
                        confidence,
                        effective_expires_at,
                        f"{key} {answer}",
                        now,
                        now,
                    ),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - INSERT RETURNING invariant
                    raise RuntimeError("failed persisting memory fact")
                self._insert_source(
                    cursor,
                    fact_id=fact_id,
                    source_type=source_type,
                    source_ref=source_ref,
                    source_title=key,
                    source_url=None,
                    source_excerpt=source_excerpt,
                    source_updated_at=now,
                    message_ids=[],
                    author_ids=[created_by],
                )
        return _memory_fact_from_row(
            row,
            source_type=source_type,
            source_ref=source_ref,
            source_excerpt_hash=_source_excerpt_hash(source_excerpt),
        )

    def list_facts(
        self,
        *,
        scope_type: MemoryScopeType,
        scope_id: str,
        visible_to_user_id: str,
        visible_to_project_id: str | None,
        visible_to_org_id: str | None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> list[MemoryFact]:
        comparison_time = now or datetime.now(timezone.utc)
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT
                        mf.*,
                        mfs.source_type AS citation_source_type,
                        mfs.source_ref AS citation_source_ref,
                        mfs.source_excerpt_hash AS citation_source_excerpt_hash
                    FROM memory_facts mf
                    LEFT JOIN LATERAL (
                        SELECT source_type, source_ref, source_excerpt_hash
                        FROM memory_fact_sources
                        WHERE fact_id = mf.id
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) mfs ON TRUE
                    WHERE mf.scope_type = %s
                      AND mf.scope_id = %s
                      AND (
                          %s
                          OR (mf.deleted_at IS NULL AND mf.status = 'active')
                      )
                      AND (mf.expires_at IS NULL OR mf.expires_at > %s)
                      AND (
                          (mf.visibility = 'private' AND mf.scope_id = %s)
                          OR (mf.visibility = 'project' AND mf.scope_id = %s)
                          OR (mf.visibility = 'org' AND mf.scope_id = %s)
                      )
                    ORDER BY mf.updated_at DESC
                    """,
                    (
                        scope_type,
                        scope_id,
                        include_deleted,
                        comparison_time,
                        visible_to_user_id,
                        visible_to_project_id,
                        visible_to_org_id,
                    ),
                )
                rows = cursor.fetchall()
        return [
            _memory_fact_from_row(
                row,
                source_type=str(row.get("citation_source_type") or "memory_fact"),
                source_ref=str(row.get("citation_source_ref") or row["id"]),
                source_excerpt_hash=row.get("citation_source_excerpt_hash"),
            )
            for row in rows
        ]

    def forget_fact(
        self,
        *,
        fact_id: str,
        actor_id: str,
        actor_is_admin: bool = False,
        now: datetime | None = None,
    ) -> MemoryFact:
        comparison_time = now or datetime.now(timezone.utc)
        with get_postgres_connection(self.settings) as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM memory_facts
                    WHERE id = %s::uuid
                    FOR UPDATE
                    """,
                    (fact_id,),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise KeyError(f"Memory fact {fact_id} was not found")
                if not actor_is_admin and str(existing["created_by"]) != actor_id:
                    raise PermissionError(
                        "Memory fact can only be deleted by its creator"
                    )
                cursor.execute(
                    """
                    UPDATE memory_facts
                    SET
                        status = 'deleted',
                        deleted_at = %s,
                        updated_at = %s
                    WHERE id = %s::uuid
                    RETURNING *
                    """,
                    (comparison_time, comparison_time, fact_id),
                )
                row = cursor.fetchone()
                if row is None:  # pragma: no cover - row is locked above
                    raise RuntimeError("failed deleting memory fact")
        return _memory_fact_from_row(
            row,
            source_type="memory_fact",
            source_ref=fact_id,
        )

    def _locked_draft(
        self,
        cursor: Any,
        draft_id: str,
        *,
        actor_id: str,
    ) -> KnowledgeCaptureDraft:
        cursor.execute(
            """
            SELECT *
            FROM knowledge_capture_drafts
            WHERE id = %s::uuid
            FOR UPDATE
            """,
            (draft_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError("knowledge capture draft was not found")
        draft = _capture_draft_from_row(row)
        if draft.actor_id != actor_id:
            raise PermissionError("knowledge capture draft belongs to another actor")
        return draft

    def _facts_by_ids(self, cursor: Any, fact_ids: list[str]) -> list[KnowledgeFact]:
        if not fact_ids:
            return []
        cursor.execute(
            """
            SELECT *
            FROM memory_facts
            WHERE id = ANY(%s::uuid[])
            """,
            (fact_ids,),
        )
        rows = {str(row["id"]): row for row in cursor.fetchall()}
        return [_knowledge_fact_from_row(rows[fact_id]) for fact_id in fact_ids]

    def _upsert_candidate(
        self,
        cursor: Any,
        *,
        draft: KnowledgeCaptureDraft,
        candidate: KnowledgeCaptureCandidate,
        review_after: datetime,
        now: datetime,
    ) -> KnowledgeFact:
        dedupe_key = _dedupe_key(candidate.question)
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (
                ":".join(
                    [
                        draft.organization_id,
                        draft.scope_type,
                        draft.scope_id,
                        dedupe_key,
                    ]
                ),
            ),
        )
        cursor.execute(
            """
            SELECT *
            FROM memory_facts
            WHERE organization_id = %s
              AND scope_type = %s
              AND scope_id = %s
              AND dedupe_key = %s
              AND status = 'active'
              AND deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            (
                draft.organization_id,
                draft.scope_type,
                draft.scope_id,
                dedupe_key,
            ),
        )
        existing = cursor.fetchone()
        if existing is not None and _normalize(str(existing["answer"])) == _normalize(
            candidate.answer
        ):
            cursor.execute(
                """
                UPDATE memory_facts
                SET
                    review_after = %s,
                    confidence = GREATEST(confidence, %s),
                    updated_at = %s
                WHERE id = %s::uuid
                RETURNING *
                """,
                (review_after, candidate.confidence, now, str(existing["id"])),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - row is locked above
                raise RuntimeError("failed refreshing knowledge fact")
        else:
            supersedes_id = str(existing["id"]) if existing is not None else None
            if supersedes_id is not None:
                cursor.execute(
                    """
                    UPDATE memory_facts
                    SET status = 'superseded', updated_at = %s
                    WHERE id = %s::uuid
                    """,
                    (now, supersedes_id),
                )
            fact_id = str(uuid4())
            key = _fact_key(candidate.question)
            search_text = " ".join(
                [key, candidate.question, candidate.answer, *candidate.aliases]
            )
            cursor.execute(
                """
                INSERT INTO memory_facts (
                    id,
                    organization_id,
                    scope_type,
                    scope_id,
                    kind,
                    key,
                    question,
                    answer,
                    aliases,
                    value_json,
                    visibility,
                    created_by,
                    verification_status,
                    confidence,
                    status,
                    review_after,
                    supersedes_id,
                    dedupe_key,
                    search_document,
                    created_at,
                    updated_at
                ) VALUES (
                    %s::uuid, %s, %s, %s, 'qa', %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 'active', %s, %s::uuid, %s,
                    to_tsvector('english', %s), %s, %s
                )
                RETURNING *
                """,
                (
                    fact_id,
                    draft.organization_id,
                    draft.scope_type,
                    draft.scope_id,
                    key,
                    candidate.question,
                    candidate.answer,
                    candidate.aliases,
                    Jsonb(
                        {
                            "question": candidate.question,
                            "answer": candidate.answer,
                            "aliases": candidate.aliases,
                        }
                    ),
                    draft.visibility,
                    draft.actor_id,
                    draft.verification_status,
                    candidate.confidence,
                    review_after,
                    supersedes_id,
                    dedupe_key,
                    search_text,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
            if row is None:  # pragma: no cover - INSERT RETURNING invariant
                raise RuntimeError("failed persisting knowledge fact")

        fact = _knowledge_fact_from_row(row)
        selected = _selected_messages(draft.messages, candidate.source_message_ids)
        excerpt = _message_excerpt(selected)
        source_url = next(
            (message.jump_url for message in reversed(selected) if message.jump_url),
            None,
        )
        self._insert_source(
            cursor,
            fact_id=fact.id,
            source_type=draft.source.source_type,
            source_ref=draft.source.source_ref,
            source_title=draft.source.title,
            source_url=source_url,
            source_excerpt=excerpt,
            source_updated_at=max(
                (message.created_at for message in selected),
                default=now,
            ),
            message_ids=[message.message_id for message in selected],
            author_ids=[message.author_id for message in selected],
        )
        return fact

    @staticmethod
    def _insert_source(
        cursor: Any,
        *,
        fact_id: str,
        source_type: str,
        source_ref: str,
        source_title: str,
        source_url: str | None,
        source_excerpt: str | None,
        source_updated_at: datetime | None,
        message_ids: list[str],
        author_ids: list[str],
    ) -> None:
        excerpt_hash = (
            hashlib.sha256(source_excerpt.encode("utf-8")).hexdigest()
            if source_excerpt
            else None
        )
        cursor.execute(
            """
            INSERT INTO memory_fact_sources (
                id,
                fact_id,
                source_type,
                source_ref,
                source_title,
                source_url,
                source_excerpt,
                source_excerpt_hash,
                source_updated_at,
                message_ids,
                author_ids
            ) VALUES (
                %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (fact_id, source_ref, source_excerpt_hash) DO NOTHING
            """,
            (
                str(uuid4()),
                fact_id,
                source_type,
                source_ref,
                source_title,
                source_url,
                source_excerpt,
                excerpt_hash,
                source_updated_at,
                message_ids,
                author_ids,
            ),
        )


def _capture_draft_from_row(row: dict[str, Any]) -> KnowledgeCaptureDraft:
    return KnowledgeCaptureDraft(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        actor_id=str(row["actor_id"]),
        scope_type=row["scope_type"],
        scope_id=str(row["scope_id"]),
        visibility=row["visibility"],
        verification_status=row["verification_status"],
        source=KnowledgeDiscordSource.model_validate(row["source_payload"]),
        messages=[
            KnowledgeDiscordMessage.model_validate(message)
            for message in row["message_payload"] or []
        ],
        candidates=[
            KnowledgeCaptureCandidate.model_validate(candidate)
            for candidate in row["candidate_payload"] or []
        ],
        expires_at=row["expires_at"],
        consumed_at=row.get("consumed_at"),
        confirmed_fact_ids=[
            str(fact_id) for fact_id in row.get("confirmed_fact_ids") or []
        ],
        created_at=row["created_at"],
    )


def _knowledge_fact_from_row(row: dict[str, Any]) -> KnowledgeFact:
    return KnowledgeFact(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        scope_type=row["scope_type"],
        scope_id=str(row["scope_id"]),
        kind=row["kind"],
        key=str(row["key"]),
        question=row.get("question"),
        answer=str(row["answer"]),
        aliases=list(row.get("aliases") or []),
        visibility=row["visibility"],
        verification_status=row["verification_status"],
        confidence=float(row["confidence"]),
        status=row["status"],
        created_by=str(row["created_by"]),
        review_after=row.get("review_after"),
        expires_at=row.get("expires_at"),
        deleted_at=row.get("deleted_at"),
        supersedes_id=(str(row["supersedes_id"]) if row.get("supersedes_id") else None),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _memory_fact_from_row(
    row: dict[str, Any],
    *,
    source_type: str,
    source_ref: str,
    source_excerpt_hash: str | None = None,
) -> MemoryFact:
    return MemoryFact(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        scope_type=row["scope_type"],
        scope_id=str(row["scope_id"]),
        kind=row["kind"],
        key=str(row["key"]),
        value_json=dict(row.get("value_json") or {"text": row["answer"]}),
        question=row.get("question"),
        answer=str(row["answer"]),
        aliases=list(row.get("aliases") or []),
        visibility=row["visibility"],
        source_type=_agent_source_type(source_type),
        source_ref=source_ref,
        source_excerpt_hash=(
            source_excerpt_hash or row.get("citation_source_excerpt_hash")
        ),
        created_by=str(row["created_by"]),
        verification_status=row["verification_status"],
        confidence=float(row["confidence"]),
        status=row["status"],
        review_after=row.get("review_after"),
        expires_at=row.get("expires_at"),
        deleted_at=row.get("deleted_at"),
        supersedes_id=(str(row["supersedes_id"]) if row.get("supersedes_id") else None),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _knowledge_evidence_from_row(
    row: dict[str, Any],
    *,
    now: datetime,
) -> KnowledgeEvidence:
    title = str(
        row.get("citation_source_title")
        or row.get("question")
        or row.get("key")
        or "Remembered answer"
    )
    return KnowledgeEvidence(
        evidence_id=f"memory:{row['id']}",
        source_type="memory",
        source_ref=str(row.get("citation_source_ref") or row["id"]),
        title=title[:300],
        excerpt=str(row["answer"]),
        url=row.get("citation_source_url"),
        visibility=row["visibility"],
        authority=_verification_authority(str(row["verification_status"])),
        relevance=max(float(row.get("relevance") or 0.0), 0.0),
        updated_at=row.get("citation_source_updated_at") or row.get("updated_at"),
        stale=bool(row.get("review_after") and row["review_after"] <= now),
    )


def _memory_fact_from_knowledge(
    fact: KnowledgeFact,
    value_json: dict[str, Any],
    source_type: str,
    source_ref: str,
    source_excerpt_hash: str | None = None,
) -> MemoryFact:
    return MemoryFact(
        id=fact.id,
        organization_id=fact.organization_id,
        scope_type=cast(MemoryScopeType, fact.scope_type),
        scope_id=fact.scope_id,
        kind=fact.kind,
        key=fact.key,
        value_json=value_json,
        question=fact.question,
        answer=fact.answer,
        aliases=fact.aliases,
        visibility=cast(MemoryVisibility, fact.visibility),
        source_type=_agent_source_type(source_type),
        source_ref=source_ref,
        source_excerpt_hash=source_excerpt_hash,
        created_by=fact.created_by,
        verification_status=fact.verification_status,
        confidence=fact.confidence,
        status=fact.status,
        review_after=fact.review_after,
        expires_at=fact.expires_at,
        deleted_at=fact.deleted_at,
        supersedes_id=fact.supersedes_id,
        created_at=fact.created_at,
        updated_at=fact.updated_at,
    )


def _selected_messages(
    messages: Iterable[KnowledgeDiscordMessage],
    selected_ids: Iterable[str],
) -> list[KnowledgeDiscordMessage]:
    selected = set(selected_ids)
    return [message for message in messages if message.message_id in selected]


def _source_evidence(
    source: KnowledgeDiscordSource,
    messages: list[KnowledgeDiscordMessage],
    fact_id: str,
    *,
    visibility: KnowledgeVisibility,
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        evidence_id=f"memory:{fact_id}",
        source_type="memory",
        source_ref=source.source_ref,
        title=source.title,
        excerpt=_message_excerpt(messages) or "Captured Discord answer",
        url=next(
            (message.jump_url for message in reversed(messages) if message.jump_url),
            None,
        ),
        visibility=visibility,
        updated_at=max(
            (message.created_at for message in messages),
            default=None,
        ),
    )


def _message_excerpt(messages: Iterable[KnowledgeDiscordMessage]) -> str:
    parts = [f"{message.author_name}: {message.content}" for message in messages]
    return "\n".join(parts)[:2000]


def _knowledge_fact_visible(
    fact: KnowledgeFact,
    *,
    actor_id: str,
    project_ids: set[str],
) -> bool:
    if fact.visibility == "org":
        return fact.scope_type == "org"
    if fact.visibility == "private":
        return fact.scope_type == "user" and fact.scope_id == actor_id
    return fact.scope_type == "project" and fact.scope_id in project_ids


def _verification_authority(status: str) -> float:
    return {
        "authoritative": 1.0,
        "admin_confirmed": 0.9,
        "author_confirmed": 0.8,
        "user_confirmed": 0.7,
        "source_recorded": 0.6,
        "inferred": 0.4,
    }.get(status, 0.5)


def _knowledge_verification(value: str) -> KnowledgeVerificationStatus:
    if value == "inferred":
        return "inferred"
    if value == "authoritative":
        return "authoritative"
    if value == "admin_confirmed":
        return "admin_confirmed"
    if value == "author_confirmed":
        return "author_confirmed"
    if value == "source_recorded":
        return "source_recorded"
    return "user_confirmed"


def _agent_source_type(value: str) -> AgentContextSourceType:
    if value in {
        "discord_thread",
        "discord_channel",
        "discord_message",
        "memory_fact",
        "crm",
        "docs",
        "request",
    }:
        return cast(AgentContextSourceType, value)
    if value == "memory":
        return "memory_fact"
    if value == "outline":
        return "docs"
    return "request"


def _memory_value_text(value: dict[str, Any]) -> str:
    text = value.get("text")
    if isinstance(text, str) and text.strip():
        return " ".join(text.split())[:3000]
    return json.dumps(value, sort_keys=True, default=str)[:3000]


def _source_excerpt_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dedupe_key(question: str) -> str:
    return hashlib.sha256(_normalize(question).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return _SPACE_RE.sub(" ", _NON_WORD_RE.sub(" ", value.casefold())).strip()


def _fact_key(question: str) -> str:
    normalized = _NON_WORD_RE.sub("_", question.casefold()).strip("_")
    return normalized[:128] or "remembered_answer"


def _search_tokens(question: str) -> set[str]:
    ignored = {
        "a",
        "an",
        "and",
        "are",
        "does",
        "do",
        "how",
        "i",
        "is",
        "it",
        "our",
        "the",
        "to",
        "what",
        "when",
        "where",
        "who",
        "why",
    }
    return {
        token
        for token in _normalize(question).split()
        if len(token) > 1 and token not in ignored
    }
