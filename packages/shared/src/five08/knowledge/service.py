"""Backend-owned capture and grounded question-answer orchestration."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Callable

from five08.agent.policy import PolicyEngine
from five08.knowledge.model import KnowledgeModel
from five08.knowledge.models import (
    KnowledgeCaptureCandidate,
    KnowledgeCaptureDraft,
    KnowledgeCaptureRequest,
    KnowledgeCaptureResponse,
    KnowledgeCitation,
    KnowledgeDiscordMessage,
    KnowledgeEvidence,
    KnowledgeQueryRequest,
    KnowledgeQueryResponse,
    KnowledgeScopeType,
    KnowledgeVerificationStatus,
    KnowledgeVisibility,
)
from five08.knowledge.sources import KnowledgeSourceAdapters
from five08.knowledge.store import KnowledgeStore
from five08.settings import SharedSettings

_CAPTURE_REQUEST_RE = re.compile(
    r"\b(?:remember|save)\s+(?:this|the)\s+"
    r"(?:thread|conversation|answer|discussion)\b",
    re.I,
)
_QUESTION_START_RE = re.compile(
    r"^(?:who|what|where|when|why|how|does|do|did|is|are|can|could|has|have)\b",
    re.I,
)
_ACKNOWLEDGEMENTS = frozenset(
    {
        "ah",
        "cool",
        "got it",
        "great",
        "k",
        "nice",
        "oh",
        "oh cool",
        "ohhh right ok cool",
        "ok",
        "okay",
        "thanks",
        "thank you",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+", re.I),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")


class KnowledgeService:
    """Coordinate policy, retrieval, model proposals, and durable persistence."""

    def __init__(
        self,
        *,
        settings: SharedSettings,
        store: KnowledgeStore,
        model: KnowledgeModel | None = None,
        sources: KnowledgeSourceAdapters | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.model = model
        self.sources = sources or KnowledgeSourceAdapters(settings)
        self.policy = policy or PolicyEngine()

    def create_capture(
        self,
        request: KnowledgeCaptureRequest,
    ) -> KnowledgeCaptureResponse:
        """Extract a frozen, reviewable capture without performing a write."""
        if not getattr(self.settings, "knowledge_enabled", True):
            return KnowledgeCaptureResponse(
                status="denied",
                message="Knowledge capture is disabled.",
            )
        context = request.context
        organization_id = context.organization_id or context.guild_id
        if not organization_id:
            return KnowledgeCaptureResponse(
                status="denied",
                message="Knowledge capture requires a resolved organization.",
            )
        scopes = self.policy.scopes_for_context(context)
        scope_type, scope_id, visibility = self._capture_scope(
            request,
            scopes=scopes,
            organization_id=organization_id,
        )
        if scope_type is None or scope_id is None or visibility is None:
            return KnowledgeCaptureResponse(
                status="denied",
                message="Your Discord roles cannot capture knowledge here.",
            )

        messages = self._bounded_messages(request.messages)
        if not messages:
            return KnowledgeCaptureResponse(
                status="needs_clarification",
                message="I could not find any conversation text to remember.",
            )
        candidates = self._extract_candidates(messages)
        if not candidates:
            return KnowledgeCaptureResponse(
                status="needs_clarification",
                message=(
                    "I could not identify a reusable question and answer in that "
                    "conversation. Reply to the answer and try `remember this answer`."
                ),
            )
        if visibility == "org" and any(
            _contains_secret(candidate.question) or _contains_secret(candidate.answer)
            for candidate in candidates
        ):
            scope_type = "user"
            scope_id = context.discord_user_id
            visibility = "private"

        selected_ids = {
            message_id
            for candidate in candidates
            for message_id in candidate.source_message_ids
        }
        selected_messages = [
            message for message in messages if message.message_id in selected_ids
        ]
        verification_status = self._verification_status(
            actor_id=context.discord_user_id,
            candidates=candidates,
            messages=selected_messages,
        )
        now = datetime.now(timezone.utc)
        ttl_seconds = int(
            getattr(self.settings, "knowledge_capture_draft_ttl_seconds", 600)
        )
        draft = KnowledgeCaptureDraft(
            organization_id=organization_id,
            actor_id=context.discord_user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            visibility=visibility,
            verification_status=verification_status,
            source=request.source,
            messages=selected_messages,
            candidates=candidates,
            expires_at=now + timedelta(seconds=max(60, ttl_seconds)),
            created_at=now,
        )
        self.store.create_capture_draft(draft)
        return KnowledgeCaptureResponse(
            status="requires_confirmation",
            message="Review and confirm the frozen knowledge capture.",
            draft_id=draft.id,
            candidates=candidates,
            scope_type=scope_type,
            scope_id=scope_id,
            visibility=visibility,
            expires_at=draft.expires_at,
        )

    def confirm_capture(
        self,
        draft_id: str,
        *,
        context: object,
        confirm: bool,
    ) -> KnowledgeCaptureResponse:
        """Reauthorize and atomically save or cancel one frozen draft."""
        from five08.agent.models import AgentIdentityContext

        identity = AgentIdentityContext.model_validate(context)
        draft = self.store.get_capture_draft(draft_id)
        if draft is None:
            raise KeyError("knowledge capture draft was not found")
        if draft.actor_id != identity.discord_user_id:
            raise PermissionError("knowledge capture draft belongs to another actor")
        confirmation_org_id = identity.organization_id or identity.guild_id
        if confirmation_org_id != draft.organization_id:
            raise PermissionError(
                "knowledge capture must be confirmed in its original organization"
            )
        if not confirm:
            self.store.cancel_capture_draft(
                draft_id,
                actor_id=identity.discord_user_id,
            )
            return KnowledgeCaptureResponse(
                status="canceled",
                message="Knowledge capture canceled.",
                draft_id=draft_id,
            )

        scopes = self.policy.scopes_for_context(identity)
        required_scope = {
            "org": "knowledge:capture_org",
            "project": "knowledge:capture_project",
            "user": "memory:write_self",
        }[draft.scope_type]
        if required_scope not in scopes:
            return KnowledgeCaptureResponse(
                status="denied",
                message="Your current Discord roles cannot confirm this capture.",
                draft_id=draft_id,
            )
        review_days = int(getattr(self.settings, "knowledge_review_after_days", 180))
        _confirmed, facts = self.store.confirm_capture_draft(
            draft_id,
            actor_id=identity.discord_user_id,
            review_after=datetime.now(timezone.utc)
            + timedelta(days=max(1, review_days)),
        )
        return KnowledgeCaptureResponse(
            status="saved",
            message=f"Saved {len(facts)} remembered answer(s).",
            draft_id=draft_id,
            scope_type=draft.scope_type,
            scope_id=draft.scope_id,
            visibility=draft.visibility,
            facts=facts,
        )

    def answer(self, request: KnowledgeQueryRequest) -> KnowledgeQueryResponse:
        """Retrieve authorized evidence and synthesize one grounded answer."""
        if not getattr(self.settings, "knowledge_enabled", True):
            return KnowledgeQueryResponse(
                status="denied",
                answer="Organizational knowledge answers are disabled.",
            )
        context = request.context
        organization_id = context.organization_id or context.guild_id
        if not organization_id:
            return KnowledgeQueryResponse(
                status="denied",
                answer="I need a resolved organization before searching knowledge.",
            )
        scopes = self.policy.scopes_for_context(context)
        if not ({"knowledge:read_org", "memory:read_self"} & scopes):
            return KnowledgeQueryResponse(
                status="denied",
                answer="Your Discord roles cannot search organizational knowledge.",
            )

        source_errors: list[str] = []
        actor_emails: list[str] = []
        accessible_project_ids: list[str] = []
        try:
            actor_emails = self.sources.resolve_actor_emails(context.discord_user_id)
            if "project:read" in scopes:
                accessible_project_ids = self.sources.accessible_project_ids(
                    actor_emails=actor_emails,
                    include_all="knowledge:admin" in scopes,
                )
        except Exception:
            source_errors.append("identity/project access lookup failed")

        searches: dict[str, Callable[[], list[KnowledgeEvidence]]] = {
            "memory": lambda: self.store.search_evidence(
                question=request.question,
                organization_id=organization_id,
                actor_id=context.discord_user_id,
                project_ids=accessible_project_ids,
                limit=int(getattr(self.settings, "knowledge_query_max_evidence", 8)),
            )
        }
        if "knowledge:read_wiki" in scopes:
            searches["wiki"] = lambda: self.sources.search_outline(request.question)
        if "project:read" in scopes:
            searches["erp"] = lambda: self.sources.search_erp_projects(
                request.question,
                actor_emails=actor_emails,
                include_all="knowledge:admin" in scopes,
            )
        if "crm:contact:read" in scopes:
            searches["crm"] = lambda: self.sources.search_crm(request.question)

        evidence: list[KnowledgeEvidence] = []
        executor = ThreadPoolExecutor(max_workers=min(4, len(searches)))
        try:
            futures = {
                executor.submit(search): name for name, search in searches.items()
            }
            completed, pending = wait(
                futures,
                timeout=float(
                    getattr(self.settings, "knowledge_source_timeout_seconds", 6.0)
                ),
            )
            for future in completed:
                name = futures[future]
                try:
                    evidence.extend(future.result())
                except Exception:
                    source_errors.append(f"{name} search failed")
            for future in pending:
                source_errors.append(f"{futures[future]} search timed out")
                future.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        evidence = self._rank_evidence(evidence)
        max_evidence = int(getattr(self.settings, "knowledge_query_max_evidence", 8))
        evidence = evidence[: max(1, min(max_evidence, 20))]
        if not evidence:
            message = "I could not find enough authorized evidence to answer that."
            if source_errors:
                message += " Some sources were temporarily unavailable."
            return KnowledgeQueryResponse(
                status="insufficient",
                answer=message,
                source_errors=source_errors,
            )

        draft = None
        if self.model is not None:
            try:
                draft = self.model.answer(
                    question=request.question,
                    evidence=evidence,
                )
            except Exception:
                source_errors.append("answer synthesis failed")
        if draft is None:
            selected = [evidence[0]]
            answer = _fallback_answer(selected[0])
            confidence = min(0.75, selected[0].authority)
        else:
            selected_ids = set(draft.evidence_ids)
            selected = [item for item in evidence if item.evidence_id in selected_ids]
            if not selected:
                selected = [evidence[0]]
                answer = _fallback_answer(selected[0])
                confidence = min(0.75, selected[0].authority)
            else:
                answer = draft.answer
                confidence = draft.confidence

        visibility = _combined_visibility(selected)
        citations = [
            KnowledgeCitation(
                citation_id=str(index),
                source_type=item.source_type,
                title=item.title,
                source_ref=item.source_ref,
                url=item.url,
                updated_at=item.updated_at,
                stale=item.stale,
            )
            for index, item in enumerate(selected, start=1)
        ]
        public_safe = visibility == "org" and not _contains_sensitive_output(answer)
        return KnowledgeQueryResponse(
            status="answered",
            answer=answer,
            citations=citations,
            confidence=confidence,
            public_safe=public_safe,
            visibility=visibility,
            source_errors=source_errors,
        )

    def _capture_scope(
        self,
        request: KnowledgeCaptureRequest,
        *,
        scopes: set[str],
        organization_id: str,
    ) -> tuple[
        KnowledgeScopeType | None,
        str | None,
        KnowledgeVisibility | None,
    ]:
        if (
            request.source.source_visibility == "org"
            and "knowledge:capture_org" in scopes
        ):
            return "org", organization_id, "org"
        if (
            request.source.source_visibility == "project"
            and request.context.project_id
            and "knowledge:capture_project" in scopes
        ):
            return "project", request.context.project_id, "project"
        if "memory:write_self" in scopes:
            return "user", request.context.discord_user_id, "private"
        return None, None, None

    def _bounded_messages(
        self,
        messages: list[KnowledgeDiscordMessage],
    ) -> list[KnowledgeDiscordMessage]:
        max_messages = int(getattr(self.settings, "knowledge_capture_max_messages", 50))
        max_characters = int(
            getattr(self.settings, "knowledge_capture_max_characters", 20_000)
        )
        max_age_days = int(getattr(self.settings, "knowledge_capture_max_age_days", 7))
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, max_age_days))
        remaining_characters = max(1, max_characters)
        bounded_newest_first: list[KnowledgeDiscordMessage] = []
        recent_messages = sorted(messages, key=lambda item: item.created_at)[
            -max(1, max_messages) :
        ]
        for message in reversed(recent_messages):
            created_at = message.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at < cutoff or message.author_is_bot:
                continue
            if _CAPTURE_REQUEST_RE.search(message.content):
                continue
            if len(message.content) > remaining_characters:
                continue
            bounded_newest_first.append(message)
            remaining_characters -= len(message.content)
            if remaining_characters <= 0:
                break
        return list(reversed(bounded_newest_first))

    def _extract_candidates(
        self,
        messages: list[KnowledgeDiscordMessage],
    ) -> list[KnowledgeCaptureCandidate]:
        if self.model is not None:
            try:
                candidates = self.model.extract_candidates(messages)
            except Exception:
                candidates = []
            allowed_message_ids = {message.message_id for message in messages}
            validated_candidates = [
                candidate
                for candidate in candidates[:3]
                if candidate.source_message_ids
                and set(candidate.source_message_ids).issubset(allowed_message_ids)
            ]
            if validated_candidates:
                return validated_candidates
        return _heuristic_candidates(messages)

    @staticmethod
    def _verification_status(
        *,
        actor_id: str,
        candidates: list[KnowledgeCaptureCandidate],
        messages: list[KnowledgeDiscordMessage],
    ) -> KnowledgeVerificationStatus:
        messages_by_id = {message.message_id: message for message in messages}
        authored_every_answer = bool(candidates) and all(
            any(
                message.author_id == actor_id
                and _normalize(message.content) in _normalize(candidate.answer)
                for message in (
                    messages_by_id[message_id]
                    for message_id in candidate.source_message_ids
                    if message_id in messages_by_id
                )
            )
            for candidate in candidates
        )
        return "author_confirmed" if authored_every_answer else "source_recorded"

    @staticmethod
    def _rank_evidence(
        evidence: list[KnowledgeEvidence],
    ) -> list[KnowledgeEvidence]:
        deduplicated: dict[tuple[str, str], KnowledgeEvidence] = {}
        for item in evidence:
            key = (item.source_type, item.source_ref)
            existing = deduplicated.get(key)
            if existing is None or (item.relevance, item.authority) > (
                existing.relevance,
                existing.authority,
            ):
                deduplicated[key] = item
        return sorted(
            deduplicated.values(),
            key=lambda item: (
                item.relevance,
                item.authority,
                not item.stale,
                _sortable_datetime(item.updated_at),
            ),
            reverse=True,
        )


def _heuristic_candidates(
    messages: list[KnowledgeDiscordMessage],
) -> list[KnowledgeCaptureCandidate]:
    for index in range(len(messages) - 1, -1, -1):
        question_message = messages[index]
        if not _looks_like_question(question_message.content):
            continue
        for answer_message in messages[index + 1 :]:
            normalized_answer = _normalize(answer_message.content).strip(" ?!.")
            if (
                not normalized_answer
                or normalized_answer in _ACKNOWLEDGEMENTS
                or _looks_like_question(answer_message.content)
            ):
                continue
            return [
                KnowledgeCaptureCandidate(
                    question=question_message.content,
                    answer=answer_message.content,
                    aliases=[_question_alias(question_message.content)],
                    source_message_ids=[
                        question_message.message_id,
                        answer_message.message_id,
                    ],
                    confidence=0.8,
                )
            ]
    return []


def _looks_like_question(value: str) -> bool:
    normalized = value.strip()
    return "?" in normalized or _QUESTION_START_RE.search(normalized) is not None


def _question_alias(question: str) -> str:
    normalized = re.sub(
        r"^(?:i\s+(?:forgot|forget),?\s*)",
        "",
        question.strip(),
        flags=re.I,
    )
    return " ".join(normalized.strip(" ?!.").split())[:200]


def _fallback_answer(evidence: KnowledgeEvidence) -> str:
    if evidence.source_type == "memory":
        return evidence.excerpt
    return f"According to {evidence.title}: {evidence.excerpt}"


def _combined_visibility(evidence: list[KnowledgeEvidence]) -> KnowledgeVisibility:
    visibilities = {item.visibility for item in evidence}
    if "private" in visibilities:
        return "private"
    if "project" in visibilities:
        return "project"
    return "org"


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _SECRET_PATTERNS)


def _contains_sensitive_output(value: str) -> bool:
    return (
        _contains_secret(value)
        or _EMAIL_RE.search(value) is not None
        or _PHONE_RE.search(value) is not None
    )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _sortable_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
