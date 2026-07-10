"""External job lead source adapters."""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from five08.job_channels import JobPostingType
from five08.job_leads import (
    JobLeadInput,
    existing_job_lead_external_ids,
    update_existing_job_lead,
    upsert_job_lead,
)
from five08.openai_fallback import (
    FallbackOpenAIClient,
    build_openai_compatible_provider_attempts,
)
from five08.settings import SharedSettings

try:  # pragma: no cover - dependency is provided by worker runtime.
    from openai import OpenAI as OpenAIClient
except ImportError:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

HN_FIREBASE_BASE_URL = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"
HN_WHO_IS_HIRING_SOURCE_KEY = "hackernews_who_is_hiring"
HN_WHO_IS_HIRING_SOURCE_TYPE = "hackernews"
DEFAULT_JOB_LEAD_CLASSIFIER_MODEL = "gpt-4.1-mini"

_WHO_IS_HIRING_TITLE_RE = re.compile(
    r"^Ask HN: Who is hiring\? \((?P<month>[A-Za-z]+) (?P<year>20\d\d)\)$"
)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SEEKING_WORK_RE = re.compile(r"^\s*SEEKING\s+WORK\b", re.IGNORECASE)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_FULL_TIME_RE = re.compile(r"\bfull\s*-?\s*time\b", re.IGNORECASE)
_APPLICATION_CONTEXT_RE = re.compile(
    r"\b(?:apply(?:\s+here)?|application(?:\s+link)?|job(?:\s+(?:posting|description))?|"
    r"opening|role)\s*:?\s*$",
    re.IGNORECASE,
)
_CONTACT_CONTEXT_RE = re.compile(
    r"\b(?:apply|(?:direct\s+)?contact(?:\s+(?:to\s+me|us|me))?(?:\s+at)?|"
    r"email(?:\s+(?:us|me|your\s+resume))?(?:\s+(?:at|to))?|"
    r"reach\s+out(?:\s+to)?)\s*:?\s*$",
    re.IGNORECASE,
)
_NEGATED_CONTACT_CONTEXT_RE = re.compile(
    r"\b(?:(?:please\s+)?(?:do\s+not|don't|not\s+to)\s+"
    r"(?:\w+\s+){0,3}(?:contact|email|reach\s+out(?:\s+to)?)|"
    r"(?:please\s+)?(?:do\s+not|don't)\s+email(?:\s+\w+){0,4}"
    r"(?:\s+(?:to|at))?|"
    r"(?:do\s+not|don't)\s+send\s+(?:\w+\s+){0,4}(?:by|via)\s+email\s+(?:to\s+)?|"
    r"(?:cannot|can't|do\s+not|don't)\s+accept\s+applications?\s+"
    r"(?:by|via)\s+email|"
    r"applications?\s+(?:are\s+)?not\s+(?:accepted|received)\s+"
    r"(?:by|via)\s+email|"
    r"not\s+(?:by|via)\s+email|"
    r"no\s+email)\s*:?\s*$",
    re.IGNORECASE,
)
_NEGATED_EMPLOYMENT_PREFIX_RE = re.compile(
    r"\b(?:"
    r"(?:no|without)\s+(?:\w+\s+){0,2}|"
    r"not\s+(?:an?\s+)?|"
    r"(?:not|isn't|aren't|won't|cannot|can't|do\s+not|don't)\s+"
    r"(?:currently\s+)?(?:hire|hiring|offer|offering|accept|consider|allow|"
    r"seek|seeking|look\s+for|looking\s+for|open\s+to)(?:\s+\w+){0,3}|"
    r"no\s+longer\s+(?:hire|hiring|offer|offering|accept|consider|allow|"
    r"seek|seeking|look\s+for|looking\s+for|open\s+to)(?:\s+\w+){0,3}"
    r")\s*$",
    re.IGNORECASE,
)
_NEGATED_EMPLOYMENT_SUFFIX_RE = re.compile(
    r"^(?:\s+\w+){0,4}\s+(?:(?:is|are|will|would|can)\s+)?"
    r"(?:not(?:\s+be)?\s+(?:available|offered|accepted|considered|allowed|open)|"
    r"unavailable|closed)\b",
    re.IGNORECASE,
)
_EMPLOYMENT_CONTRACT_PREFIX_RE = re.compile(
    r"\b(?:open\s+to|available\s+for|seeking|hiring|hire|looking\s+for|either|or)"
    r"(?:\s+\w+){0,3}\s*$",
    re.IGNORECASE,
)
_EMPLOYMENT_CONTRACT_SUFFIX_RE = re.compile(
    r"^(?:\s+\w+){0,3}\s+(?:role|position|job|work|basis|opportunity|"
    r"engagement|hire|engineer|developer|employment|welcome|option|available)\b",
    re.IGNORECASE,
)
_JOB_URL_HINTS = (
    "job",
    "jobs",
    "career",
    "careers",
    "position",
    "role",
    "opening",
    "apply",
    "engineer",
    "developer",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "workable.com",
    "smartrecruiters.com",
    "notion.site",
)
_APPLICATION_CONTEXT_WINDOW_CHARS = 100
_CONTACT_CONTEXT_WINDOW_CHARS = 80
_EXPLICIT_CONTEXT_SCORE = 100
_MODEL_PROPOSAL_SCORE = 20
_CONTRACT_TERMS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("contract-to-hire", re.compile(r"\bcontract\s*-?\s*to\s*-?\s*hire\b", re.I), 0.35),
    ("contract", re.compile(r"\bcontracts?\b|\bcontractors?\b", re.I), 0.30),
    ("1099", re.compile(r"\b1099\b", re.I), 0.30),
    ("freelance", re.compile(r"\bfreelanc(?:e|er|ers|ing)\b", re.I), 0.30),
    ("consulting", re.compile(r"\bconsult(?:ant|ants|ing)\b", re.I), 0.10),
    ("part-time", re.compile(r"\bpart\s*-?\s*time\b", re.I), 0.20),
    ("fractional", re.compile(r"\bfractional\b", re.I), 0.20),
    (
        "b2b-contracting",
        re.compile(r"\bB2B\s+(?:contracting|engagement)\b", re.I),
        0.25,
    ),
    ("b2b", re.compile(r"\bB2B\b(?!\s+(?:contracting|engagement))", re.I), 0.15),
    ("deel", re.compile(r"\bDeel\b", re.I), 0.10),
)


class JobLeadSource(Protocol):
    """Contract for source adapters that produce job lead candidates."""

    source_key: str

    def collect(self) -> list[JobLeadInput]:
        """Return current leads from the source."""


@dataclass(frozen=True)
class JobLeadClassification:
    """Contractor-friendliness classification for one external lead."""

    is_contractor_friendly: bool
    posting_type: JobPostingType
    tags: list[str]
    confidence: float
    confidence_label: Literal["high", "medium", "low"]
    rationale: str
    method: Literal["llm", "heuristic"]
    apply_url: str | None = None
    contact_email: str | None = None


class JobLeadLLMClassificationResponse(BaseModel):
    """Schema-backed model response for contractor-friendly lead detection."""

    model_config = ConfigDict(extra="ignore")

    is_contractor_friendly: bool
    posting_type: Literal[
        "part_time",
        "full_time",
        "part_time_or_full_time",
        "unknown",
    ] = "unknown"
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: Literal["high", "medium", "low"] = "low"
    rationale: str = ""
    apply_url: str | None = None
    contact_email: str | None = None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "br", "li"}:
            self._parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._parts.append(f" {href} ")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        text = html.unescape("".join(self._parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


@dataclass(frozen=True)
class HackerNewsThread:
    """HN monthly Who is hiring thread metadata."""

    story_id: int
    title: str
    created_at: datetime | None
    descendants: int | None = None


class HackerNewsClient:
    """Small HN API client using Algolia for search/tree and Firebase for freshness."""

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "508-workflows-job-leads/1.0"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_item(self, item_id: int) -> dict[str, Any]:
        return self._get_json(f"{HN_FIREBASE_BASE_URL}/item/{item_id}.json")

    def get_algolia_item_tree(self, item_id: int) -> dict[str, Any]:
        return self._get_json(f"{HN_ALGOLIA_BASE_URL}/items/{item_id}")

    def search_who_is_hiring_threads(
        self, *, hits_per_page: int = 12
    ) -> list[HackerNewsThread]:
        params = urlencode(
            {
                "tags": "story,author_whoishiring",
                "query": '"Who is hiring?"',
                "hitsPerPage": str(hits_per_page),
            }
        )
        payload = self._get_json(f"{HN_ALGOLIA_BASE_URL}/search_by_date?{params}")
        threads: list[HackerNewsThread] = []
        for hit in payload.get("hits", []):
            title = str(hit.get("title") or "")
            if not _WHO_IS_HIRING_TITLE_RE.match(title):
                continue
            story_id = int(hit["objectID"])
            created_at = _parse_datetime(hit.get("created_at"))
            threads.append(
                HackerNewsThread(
                    story_id=story_id,
                    title=title,
                    created_at=created_at,
                    descendants=hit.get("num_comments"),
                )
            )
        return threads


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def html_to_text(raw_html: str | None) -> str:
    """Convert HN/Algolia comment HTML into normalized text."""
    parser = _TextExtractor()
    parser.feed(raw_html or "")
    return parser.text()


def _url_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for match in _URL_RE.finditer(text):
        raw_candidate = match.group(0)
        if raw_candidate.endswith(("...", "…")):
            continue
        candidate = raw_candidate.rstrip(".,);]}")
        if candidate in candidates:
            continue
        candidates.append(candidate)
    return candidates


def _email_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    normalized_candidates: set[str] = set()
    for match in _EMAIL_RE.finditer(text):
        candidate = match.group(0)
        normalized = candidate.casefold()
        if normalized in normalized_candidates:
            continue
        normalized_candidates.add(normalized)
        candidates.append(candidate)
    return candidates


def _preferred_apply_url(text: str, proposed: str | None = None) -> str | None:
    candidates = _url_candidates(text)
    if not candidates:
        return None
    proposed_value = str(proposed or "").strip().rstrip(".,);]}")
    if proposed_value not in candidates:
        proposed_value = ""

    def score(candidate: str) -> tuple[int, int, int, int]:
        start = text.find(candidate)
        prefix = text[max(0, start - _APPLICATION_CONTEXT_WINDOW_CHARS) : start]
        parsed = urlsplit(candidate)
        searchable = f"{parsed.netloc}{parsed.path}".casefold()
        has_specific_path = parsed.path not in {"", "/"}
        has_job_hint = any(hint in searchable for hint in _JOB_URL_HINTS)
        return (
            int(has_job_hint),
            int(has_specific_path),
            int(candidate == proposed_value),
            int(bool(_APPLICATION_CONTEXT_RE.search(prefix))),
        )

    return max(candidates, key=score)


def _preferred_contact_email(text: str, proposed: str | None = None) -> str | None:
    candidates = _email_candidates(text)
    if not candidates:
        return None
    proposed_value = str(proposed or "").strip().casefold()
    candidate_values = {candidate.casefold() for candidate in candidates}
    if proposed_value not in candidate_values:
        proposed_value = ""

    def score(candidate: str) -> int:
        start = text.casefold().find(candidate.casefold())
        prefix = text[max(0, start - _CONTACT_CONTEXT_WINDOW_CHARS) : start]
        if _NEGATED_CONTACT_CONTEXT_RE.search(prefix):
            return -1
        value = _EXPLICIT_CONTEXT_SCORE if _CONTACT_CONTEXT_RE.search(prefix) else 0
        if candidate.casefold() == proposed_value:
            value += _MODEL_PROPOSAL_SCORE
        return value

    selected = max(candidates, key=score)
    selected_score = score(selected)
    if selected_score > 0 or (selected_score == 0 and len(candidates) == 1):
        return selected
    return None


def _split_header(text: str) -> list[str]:
    first_line = text.splitlines()[0] if text.splitlines() else text
    return [part.strip() for part in first_line.split("|") if part.strip()]


def _employment_match_is_negated(text: str, match: re.Match[str]) -> bool:
    prefix = text[max(0, match.start() - 60) : match.start()]
    suffix = text[match.end() : match.end() + 60]
    return bool(
        _NEGATED_EMPLOYMENT_PREFIX_RE.search(prefix)
        or _NEGATED_EMPLOYMENT_SUFFIX_RE.search(suffix)
    )


def _contract_match_is_employment(text: str, match: re.Match[str]) -> bool:
    if "contractor" in match.group(0).casefold():
        return True

    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    relative_start = match.start() - line_start
    relative_end = match.end() - line_start
    segment_start = line.rfind("|", 0, relative_start) + 1
    segment_end = line.find("|", relative_end)
    if segment_end == -1:
        segment_end = len(line)
    segment = line[segment_start:segment_end].strip()
    if re.match(
        r"b2b\s+(?:contracting|engagement)\s*(?:$|[.:\-–—])",
        segment,
        re.IGNORECASE,
    ):
        return True
    if re.match(
        r"(?:(?:b2b|consulting)\s+)?"
        r"(?:(?:\d+|one|two|three|four|five|six|twelve)\s*[- ]?\s*"
        r"(?:day|week|month|year)s?\s+)?contracts?\s*(?:$|[.:\-–—])",
        segment,
        re.IGNORECASE,
    ):
        return True

    prefix = text[max(0, match.start() - 100) : match.start()]
    suffix = text[match.end() : match.end() + 60]
    return bool(
        _EMPLOYMENT_CONTRACT_PREFIX_RE.search(prefix)
        or _EMPLOYMENT_CONTRACT_SUFFIX_RE.search(suffix)
        or re.search(
            r"\b(?:work(?:ing)?\s+with\s+us|join(?:ing)?\s+us)\s+on"
            r"(?:\s+(?:an?|the))?(?:\s+\w+){0,3}\s*$",
            prefix,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:hire|hiring|seek|seeking|look\s+for|looking\s+for|need|"
            r"needing|want|wanted|wanting)\b(?:(?![.!?|\n]).){0,70}"
            r"\b(?:on|for)(?:\s+(?:an?|the))?(?:\s+\w+){0,5}\s*$",
            prefix,
            re.IGNORECASE,
        )
    )


def _contract_tags_and_confidence(text: str) -> tuple[list[str], float]:
    tags: list[str] = []
    confidence = 0.0
    for tag, pattern, weight in _CONTRACT_TERMS:
        matches = [
            match
            for match in pattern.finditer(text)
            if not _employment_match_is_negated(text, match)
        ]
        if tag in {"contract", "b2b-contracting"}:
            matches = [
                match
                for match in matches
                if _contract_match_is_employment(text, match)
                and (
                    tag != "contract"
                    or not re.search(
                        r"\b(?:customer|client|commercial|sales|government|enterprise)\s+$",
                        text[max(0, match.start() - 40) : match.start()],
                        re.IGNORECASE,
                    )
                )
            ]
        if matches:
            tags.append(tag)
            confidence += weight
    if len(tags) >= 2:
        confidence += 0.15
    return sorted(set(tags)), min(confidence, 1.0)


def _confidence_label(confidence: float) -> Literal["high", "medium", "low"]:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.45:
        return "medium"
    return "low"


def classify_contractor_lead(comment_text: str) -> tuple[bool, list[str], float]:
    """Return whether a HN job post looks contractor-friendly."""
    classification = classify_contractor_lead_heuristic(comment_text)
    return (
        classification.is_contractor_friendly,
        classification.tags,
        classification.confidence,
    )


def classify_contractor_lead_heuristic(comment_text: str) -> JobLeadClassification:
    """Return a deterministic fallback contractor-friendly classification."""
    if _SEEKING_WORK_RE.search(comment_text):
        return JobLeadClassification(
            is_contractor_friendly=False,
            posting_type=JobPostingType.UNKNOWN,
            tags=[],
            confidence=0.0,
            confidence_label="low",
            rationale="Post is a SEEKING WORK comment, not an employer lead.",
            method="heuristic",
            apply_url=_preferred_apply_url(comment_text),
            contact_email=_preferred_contact_email(comment_text),
        )
    tags, confidence = _contract_tags_and_confidence(comment_text)
    has_part_time_or_contract = bool(tags) and confidence >= 0.20
    has_full_time = any(
        not _employment_match_is_negated(comment_text, match)
        for match in _FULL_TIME_RE.finditer(comment_text)
    )
    if has_full_time and has_part_time_or_contract:
        posting_type = JobPostingType.PART_TIME_OR_FULL_TIME
    elif has_full_time:
        posting_type = JobPostingType.FULL_TIME
    elif has_part_time_or_contract:
        posting_type = JobPostingType.PART_TIME
    else:
        posting_type = JobPostingType.UNKNOWN
    if has_full_time:
        tags = sorted({*tags, "full-time"})
        confidence = max(confidence, 0.85)
    is_lead = posting_type in {
        JobPostingType.PART_TIME,
        JobPostingType.PART_TIME_OR_FULL_TIME,
    }
    if posting_type is JobPostingType.FULL_TIME:
        rationale = "Explicit full-time employment with no contract option."
    elif posting_type is JobPostingType.PART_TIME_OR_FULL_TIME:
        rationale = "Explicitly allows full-time and part-time or contract work."
    elif tags:
        rationale = f"Matched part-time or contract terms: {', '.join(tags)}."
    else:
        rationale = "No part-time, contract, or full-time terms were matched."
    return JobLeadClassification(
        is_contractor_friendly=is_lead,
        posting_type=posting_type,
        tags=tags,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        rationale=rationale,
        method="heuristic",
        apply_url=_preferred_apply_url(comment_text),
        contact_email=_preferred_contact_email(comment_text),
    )


class JobLeadClassifier:
    """LLM-first classifier with deterministic keyword fallback."""

    def __init__(
        self,
        *,
        settings: SharedSettings,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.client = client if client is not None else _build_llm_client(settings)

    def classify(self, comment_text: str) -> JobLeadClassification:
        if _SEEKING_WORK_RE.search(comment_text):
            return classify_contractor_lead_heuristic(comment_text)
        if self.client is not None:
            try:
                return self._classify_with_llm(comment_text)
            except Exception as exc:
                logger.warning("Job lead LLM classification failed: %s", exc)
        return classify_contractor_lead_heuristic(comment_text)

    @staticmethod
    def _messages(comment_text: str) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You classify Hacker News 'Who is hiring?' employer posts for "
                    "508.dev job-lead review. Return JSON only. A contractor-friendly "
                    "lead explicitly allows contract, contractor, 1099, freelance, "
                    "consulting, fractional, part-time, B2B contracting, or both "
                    "full-time and contract arrangements. Reject full-time employee-only "
                    "roles, generic company B2B descriptions, replies, and SEEKING WORK "
                    "comments. Prefer explicit evidence over inference. Tags must be "
                    "short lowercase evidence labels such as contract, 1099, freelance, "
                    "consulting, fractional, part-time, full-time, b2b-contracting, "
                    "remote. Also return the best application URL and direct contact "
                    "email copied exactly from the post, or null when absent. Prefer a "
                    "role-specific application page over a company homepage."
                ),
            },
            {
                "role": "user",
                "content": f"Classify this HN comment:\n\n{comment_text[:12000]}",
            },
        ]

    @staticmethod
    def _coerce_message_content_to_text(value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None

    def _classify_with_llm(self, comment_text: str) -> JobLeadClassification:
        client = self.client
        if client is None:
            raise RuntimeError("Job lead LLM client is unavailable")
        messages = self._messages(comment_text)
        if _supports_structured_output(client):
            response = client.beta.chat.completions.parse(
                model=_classifier_model(self.settings),
                messages=messages,
                response_format=JobLeadLLMClassificationResponse,
                max_tokens=700,
                temperature=0,
            )
            parsed_model = _parsed_message_model(response)
            if parsed_model is None:
                raise ValueError("Empty structured job lead classification response")
            return _classification_from_llm_response(parsed_model, comment_text)

        response = client.chat.completions.create(
            model=_classifier_model(self.settings),
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=700,
            temperature=0,
        )
        raw_content = self._coerce_message_content_to_text(
            _first_message_content(response)
        )
        if not raw_content:
            raise ValueError("Empty job lead classification response")
        return _classification_from_llm_response(
            JobLeadLLMClassificationResponse.model_validate_json(raw_content),
            comment_text,
        )


def _clean(value: object) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _classifier_model(settings: SharedSettings) -> str:
    return (
        _clean(getattr(settings, "job_lead_classifier_model", None))
        or _clean(getattr(settings, "agent_fast_model", None))
        or _clean(getattr(settings, "agent_fallback_model", None))
        or _clean(getattr(settings, "openai_model", None))
        or DEFAULT_JOB_LEAD_CLASSIFIER_MODEL
    )


def _fireworks_classifier_model(settings: SharedSettings) -> str | None:
    explicit_fast = _clean(getattr(settings, "agent_fast_model", None))
    if explicit_fast:
        return explicit_fast
    classifier_model = _classifier_model(settings)
    if classifier_model.startswith(("accounts/fireworks/", "fireworks/")):
        return classifier_model
    return None


def _openrouter_classifier_model(settings: SharedSettings) -> str | None:
    explicit_fast = _clean(getattr(settings, "agent_fast_model", None))
    if explicit_fast:
        return explicit_fast
    classifier_model = _classifier_model(settings)
    if classifier_model.startswith(("openai/", "openrouter/")):
        return classifier_model
    return None


def _build_llm_client(settings: SharedSettings) -> Any | None:
    if getattr(settings, "job_lead_classifier_enabled", True) is False:
        return None
    if OpenAIClient is None:
        return None
    providers = build_openai_compatible_provider_attempts(
        primary_model=_classifier_model(settings),
        primary_api_key=_clean(getattr(settings, "agent_fast_api_key", None))
        or _clean(getattr(settings, "openai_api_key", None)),
        primary_base_url=_clean(getattr(settings, "agent_fast_base_url", None))
        or _clean(getattr(settings, "openai_base_url", None)),
        openai_direct_api_key=_clean(getattr(settings, "openai_direct_api_key", None))
        or _clean(getattr(settings, "openai_api_key_direct", None)),
        openai_direct_base_url=_clean(
            getattr(settings, "openai_direct_base_url", None)
        ),
        openai_direct_model=_clean(getattr(settings, "openai_direct_model", None))
        or _clean(getattr(settings, "agent_fallback_model", None)),
        fireworks_api_key=_clean(getattr(settings, "fireworks_api_key", None)),
        fireworks_model=_fireworks_classifier_model(settings),
        openrouter_api_key=_clean(getattr(settings, "openrouter_api_key", None)),
        openrouter_model=_openrouter_classifier_model(settings),
    )
    if not providers:
        return None
    return FallbackOpenAIClient(
        providers=providers,
        client_factory=OpenAIClient,
        timeout_seconds=float(
            getattr(settings, "job_lead_classifier_timeout_seconds", 8.0) or 8.0
        ),
    )


def _supports_structured_output(client: Any) -> bool:
    beta = getattr(client, "beta", None)
    chat = getattr(beta, "chat", None)
    completions = getattr(chat, "completions", None)
    return hasattr(completions, "parse")


def _parsed_message_model(response: Any) -> JobLeadLLMClassificationResponse | None:
    choices = getattr(response, "choices", None)
    first_choice = choices[0] if choices else None
    message = getattr(first_choice, "message", None)
    parsed = getattr(message, "parsed", None) if message else None
    if isinstance(parsed, JobLeadLLMClassificationResponse):
        return parsed
    if parsed is not None:
        return JobLeadLLMClassificationResponse.model_validate(parsed)
    return None


def _first_message_content(response: Any) -> Any:
    choices = getattr(response, "choices", None)
    first_choice = choices[0] if choices else None
    message = getattr(first_choice, "message", None)
    return getattr(message, "content", None) if message else None


def _classification_from_llm_response(
    response: JobLeadLLMClassificationResponse,
    comment_text: str,
) -> JobLeadClassification:
    tags = sorted(
        {
            tag.strip().casefold()
            for tag in response.tags
            if isinstance(tag, str) and tag.strip()
        }
    )
    posting_type = JobPostingType(response.posting_type)
    return JobLeadClassification(
        is_contractor_friendly=response.is_contractor_friendly
        and posting_type
        in {JobPostingType.PART_TIME, JobPostingType.PART_TIME_OR_FULL_TIME},
        posting_type=posting_type,
        tags=tags,
        confidence=max(0.0, min(1.0, float(response.confidence))),
        confidence_label=response.confidence_label,
        rationale=response.rationale.strip()[:500],
        method="llm",
        apply_url=_preferred_apply_url(comment_text, response.apply_url),
        contact_email=_preferred_contact_email(comment_text, response.contact_email),
    )


def _classification_metadata(
    classification: JobLeadClassification,
) -> dict[str, Any]:
    return {
        "contractor_classification": {
            "is_contractor_friendly": classification.is_contractor_friendly,
            "posting_type": classification.posting_type.value,
            "tags": classification.tags,
            "confidence": classification.confidence,
            "confidence_label": classification.confidence_label,
            "rationale": classification.rationale,
            "method": classification.method,
            "contact_email": classification.contact_email,
        }
    }


def _lead_from_hn_comment(
    *,
    story_id: int,
    story_title: str,
    comment: dict[str, Any],
    classifier: JobLeadClassifier | None = None,
    include_non_contractor: bool = False,
) -> JobLeadInput | None:
    text = html_to_text(comment.get("text"))
    if not text:
        return None
    if _SEEKING_WORK_RE.search(text):
        return None
    classification = (
        classifier.classify(text)
        if classifier is not None
        else classify_contractor_lead_heuristic(text)
    )
    if not classification.is_contractor_friendly and not include_non_contractor:
        return None

    header_parts = _split_header(text)
    organization = header_parts[0] if header_parts else None
    title = " | ".join(header_parts[:4]) if header_parts else text[:120]
    if len(title) > 240:
        title = f"{title[:237]}..."
    location = None
    for part in header_parts[1:]:
        if any(
            token in part.casefold()
            for token in ("remote", "onsite", "hybrid", "us", "eu", "nyc", "sf")
        ):
            location = part
            break
    comment_id = str(comment["id"])
    comment_url = f"https://news.ycombinator.com/item?id={comment_id}"
    metadata = {
        "hn_story_id": story_id,
        "hn_story_title": story_title,
        "hn_author": comment.get("author"),
        "hn_parent_id": comment.get("parent_id"),
        **_classification_metadata(classification),
    }
    return JobLeadInput(
        source_key=HN_WHO_IS_HIRING_SOURCE_KEY,
        source_type=HN_WHO_IS_HIRING_SOURCE_TYPE,
        external_id=comment_id,
        external_parent_id=str(story_id),
        source_url=comment_url,
        source_posted_at=_parse_datetime(comment.get("created_at")),
        title=title,
        organization=organization,
        body_raw=str(comment.get("text") or ""),
        body_normalized=text,
        posting_type=classification.posting_type,
        location=location,
        remote=bool(_REMOTE_RE.search(text)) if text else None,
        apply_url=classification.apply_url or _preferred_apply_url(text),
        tags=classification.tags,
        confidence=classification.confidence,
        metadata=metadata,
    )


class HackerNewsWhoIsHiringLeadSource:
    """Scrape contractor-friendly posts from monthly HN Who is hiring threads."""

    source_key = HN_WHO_IS_HIRING_SOURCE_KEY

    def __init__(
        self,
        *,
        client: HackerNewsClient | None = None,
        classifier: JobLeadClassifier | None = None,
        story_id: int | None = None,
        include_latest: bool = True,
        include_non_contractor: bool = False,
    ) -> None:
        self.client = client or HackerNewsClient()
        self.classifier = classifier
        self.story_id = story_id
        self.include_latest = include_latest
        self.include_non_contractor = include_non_contractor

    def discover_threads(self) -> list[HackerNewsThread]:
        if self.story_id is not None:
            item = self.client.get_item(self.story_id)
            return [
                HackerNewsThread(
                    story_id=self.story_id,
                    title=str(item.get("title") or f"HN item {self.story_id}"),
                    created_at=_parse_datetime(item.get("time")),
                    descendants=item.get("descendants"),
                )
            ]
        threads = self.client.search_who_is_hiring_threads(hits_per_page=6)
        return threads[:1] if self.include_latest else threads

    def collect(self) -> list[JobLeadInput]:
        leads: list[JobLeadInput] = []
        for thread in self.discover_threads():
            tree = self.client.get_algolia_item_tree(thread.story_id)
            children = tree.get("children") or []
            for child in children:
                if not isinstance(child, dict):
                    continue
                if child.get("parent_id") != thread.story_id:
                    continue
                lead = _lead_from_hn_comment(
                    story_id=thread.story_id,
                    story_title=thread.title,
                    comment=child,
                    classifier=self.classifier,
                    include_non_contractor=self.include_non_contractor,
                )
                if lead is not None:
                    leads.append(lead)
        return leads


def build_job_lead_source(
    source: str,
    *,
    classifier: JobLeadClassifier | None = None,
    story_id: int | None = None,
    include_non_contractor: bool = False,
) -> JobLeadSource:
    """Construct a source adapter by stable source id."""
    normalized = source.strip().casefold()
    if normalized in {"hn", "hackernews", HN_WHO_IS_HIRING_SOURCE_KEY}:
        return HackerNewsWhoIsHiringLeadSource(
            classifier=classifier,
            story_id=story_id,
            include_non_contractor=include_non_contractor,
        )
    raise ValueError(f"Unsupported job lead source: {source}")


def scrape_job_leads(
    settings: SharedSettings,
    *,
    source: str = HN_WHO_IS_HIRING_SOURCE_KEY,
    story_id: int | None = None,
) -> dict[str, Any]:
    """Collect leads from a source and upsert them for review."""
    classifier = JobLeadClassifier(settings=settings)
    adapter = build_job_lead_source(
        source,
        classifier=classifier,
        story_id=story_id,
        include_non_contractor=True,
    )
    created = 0
    updated = 0
    lead_ids: list[str] = []
    leads = adapter.collect()
    rejected_external_ids = [
        lead.external_id
        for lead in leads
        if (lead.metadata or {})
        .get("contractor_classification", {})
        .get("is_contractor_friendly")
        is False
    ]
    existing_rejected_ids = existing_job_lead_external_ids(
        settings,
        source_key=adapter.source_key,
        external_ids=rejected_external_ids,
    )
    for lead in leads:
        classification = (lead.metadata or {}).get("contractor_classification", {})
        if classification.get("is_contractor_friendly") is False:
            if lead.external_id not in existing_rejected_ids:
                continue
            existing_id = update_existing_job_lead(settings, lead)
            if existing_id is not None:
                lead_ids.append(existing_id)
                updated += 1
            continue
        lead_id, was_created = upsert_job_lead(settings, lead)
        if lead_id is None:
            continue
        lead_ids.append(lead_id)
        if was_created:
            created += 1
        else:
            updated += 1
    return {
        "source": adapter.source_key,
        "created": created,
        "updated": updated,
        "total": len(lead_ids),
        "lead_ids": lead_ids,
    }


def hn_story_url(story_id: int) -> str:
    """Return canonical HN story URL."""
    return f"https://news.ycombinator.com/item?id={quote(str(story_id))}"
