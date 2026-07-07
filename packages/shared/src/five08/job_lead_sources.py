"""External job lead source adapters."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from five08.job_channels import JobPostingType
from five08.job_leads import JobLeadInput, upsert_job_lead
from five08.settings import SharedSettings

HN_FIREBASE_BASE_URL = "https://hacker-news.firebaseio.com/v0"
HN_ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"
HN_WHO_IS_HIRING_SOURCE_KEY = "hackernews_who_is_hiring"
HN_WHO_IS_HIRING_SOURCE_TYPE = "hackernews"

_WHO_IS_HIRING_TITLE_RE = re.compile(
    r"^Ask HN: Who is hiring\? \((?P<month>[A-Za-z]+) (?P<year>20\d\d)\)$"
)
_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+")
_SEEKING_WORK_RE = re.compile(r"^\s*SEEKING\s+WORK\b", re.IGNORECASE)
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_CONTRACT_TERMS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("contract-to-hire", re.compile(r"\bcontract\s*-?\s*to\s*-?\s*hire\b", re.I), 0.35),
    ("contract", re.compile(r"\bcontracts?\b|\bcontractors?\b", re.I), 0.30),
    ("1099", re.compile(r"\b1099\b", re.I), 0.30),
    ("freelance", re.compile(r"\bfreelanc(?:e|er|ers|ing)\b", re.I), 0.30),
    ("consulting", re.compile(r"\bconsult(?:ant|ants|ing)\b", re.I), 0.10),
    ("part-time", re.compile(r"\bpart\s*-?\s*time\b", re.I), 0.20),
    ("fractional", re.compile(r"\bfractional\b", re.I), 0.20),
    ("b2b", re.compile(r"\bB2B\b", re.I), 0.15),
    ("deel", re.compile(r"\bDeel\b", re.I), 0.10),
)


class JobLeadSource(Protocol):
    """Contract for source adapters that produce job lead candidates."""

    source_key: str

    def collect(self) -> list[JobLeadInput]:
        """Return current leads from the source."""


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


def _first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,);]")


def _split_header(text: str) -> list[str]:
    first_line = text.splitlines()[0] if text.splitlines() else text
    return [part.strip() for part in first_line.split("|") if part.strip()]


def _contract_tags_and_confidence(text: str) -> tuple[list[str], float]:
    tags: list[str] = []
    confidence = 0.0
    for tag, pattern, weight in _CONTRACT_TERMS:
        if pattern.search(text):
            tags.append(tag)
            confidence += weight
    if len(tags) >= 2:
        confidence += 0.15
    return sorted(set(tags)), min(confidence, 1.0)


def classify_contractor_lead(comment_text: str) -> tuple[bool, list[str], float]:
    """Return whether a HN job post looks contractor-friendly."""
    if _SEEKING_WORK_RE.search(comment_text):
        return False, [], 0.0
    tags, confidence = _contract_tags_and_confidence(comment_text)
    return bool(tags) and confidence >= 0.20, tags, confidence


def _lead_from_hn_comment(
    *,
    story_id: int,
    story_title: str,
    comment: dict[str, Any],
) -> JobLeadInput | None:
    text = html_to_text(comment.get("text"))
    if not text:
        return None
    is_lead, tags, confidence = classify_contractor_lead(text)
    if not is_lead:
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
        posting_type=JobPostingType.PART_TIME,
        location=location,
        remote=bool(_REMOTE_RE.search(text)) if text else None,
        apply_url=_first_url(text),
        tags=tags,
        confidence=confidence,
        metadata=metadata,
    )


class HackerNewsWhoIsHiringLeadSource:
    """Scrape contractor-friendly posts from monthly HN Who is hiring threads."""

    source_key = HN_WHO_IS_HIRING_SOURCE_KEY

    def __init__(
        self,
        *,
        client: HackerNewsClient | None = None,
        story_id: int | None = None,
        include_latest: bool = True,
    ) -> None:
        self.client = client or HackerNewsClient()
        self.story_id = story_id
        self.include_latest = include_latest

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
                )
                if lead is not None:
                    leads.append(lead)
        return leads


def build_job_lead_source(
    source: str,
    *,
    story_id: int | None = None,
) -> JobLeadSource:
    """Construct a source adapter by stable source id."""
    normalized = source.strip().casefold()
    if normalized in {"hn", "hackernews", HN_WHO_IS_HIRING_SOURCE_KEY}:
        return HackerNewsWhoIsHiringLeadSource(story_id=story_id)
    raise ValueError(f"Unsupported job lead source: {source}")


def scrape_job_leads(
    settings: SharedSettings,
    *,
    source: str = HN_WHO_IS_HIRING_SOURCE_KEY,
    story_id: int | None = None,
) -> dict[str, Any]:
    """Collect leads from a source and upsert them for review."""
    adapter = build_job_lead_source(source, story_id=story_id)
    created = 0
    updated = 0
    lead_ids: list[str] = []
    for lead in adapter.collect():
        lead_id, was_created = upsert_job_lead(settings, lead)
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
