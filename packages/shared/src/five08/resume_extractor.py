"""Shared resume text extraction utilities for candidate fields."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from five08.crm_normalization import (
    normalize_city as shared_normalize_city,
    normalize_country as shared_normalize_country,
    normalize_role as shared_normalize_role,
    normalize_roles as shared_normalize_roles,
    normalize_seniority as shared_normalize_seniority,
    normalize_timezone as shared_normalize_timezone,
    normalize_timezone_offset as shared_normalize_timezone_offset,
    normalize_website_url as shared_normalize_website_url,
)
from five08.skills import (
    DISALLOWED_RESUME_SKILLS,
    normalize_skill_payload,
)

try:
    from openai import OpenAI as OpenAIClient
except Exception:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]


DEFAULT_SKILL_STRENGTH = 3
EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
PERSONAL_WEBSITE_CONTEXT_CONFIDENCE = 0.85
PERSONAL_WEBSITE_CONTEXT_KEYWORDS = (
    "personal website",
    "portfolio",
    "portfolio site",
    "my website",
    "web site",
    "homepage",
    "website",
)

SOCIAL_LINK_DOMAINS = {
    "facebook.com",
    "fb.com",
    "instagram.com",
    "github.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "tiktok.com",
    "youtube.com",
    "youtube-nocookie.com",
}
PERSONAL_WEBSITE_DISALLOWED_HOSTS = {
    "node.js",
    "asp.net",
    "next.js",
}
TECH_STACK_WEBSITE_DISALLOWED_PREFIXES = frozenset(
    {
        "asp",
        "next",
        "node",
        "react",
        "vue",
        "angular",
        "nuxt",
        "svelte",
        "tailwind",
        "gatsby",
    }
)
PERSONAL_WEBSITE_MIN_CONFIDENCE = 0.7
LLM_WEBSITE_URL_MIN_CONFIDENCE = 0.45
LLM_SOCIAL_URL_MIN_CONFIDENCE = 0.7
LLM_PERSONAL_URL_MIN_CONFIDENCE = 0.85
LLM_URL_CANDIDATE_KIND_PERSONAL = "personal_website"
LLM_URL_CANDIDATE_KIND_SOCIAL = "social_profile"
LLM_URL_CANDIDATE_KIND_OTHER = "other"
MARKDOWN_URL_PATTERN = re.compile(r"\[[^\]]+\]\(\s*([^)]+?)\s*\)")
SCHEME_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)[^\s\]\[()\"<>]+")
BARE_DOMAIN_URL_PATTERN = re.compile(
    r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s\]\[()\"<>]*)?"
)
LINKEDIN_PROFILE_PATTERN = re.compile(
    r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/[A-Za-z0-9_%-]+/?",
    flags=re.IGNORECASE,
)
DEFAULT_FALLBACK_FIRST_NAME = "Resume"
DEFAULT_FALLBACK_LAST_NAME = "Candidate"
SINGLE_NAME_FALLBACK_LAST_NAME = "Unknown"
RESUME_NAME_PLACEHOLDER_TOKENS = frozenset(
    {"unknown", "n/a", "na", "none", "null", "resume candidate"}
)
RESUME_NAME_HEADING_TOKENS = frozenset(
    {
        "resume",
        "curriculum vitae",
        "cv",
        "contact",
        "summary",
        "profile",
        "experience",
        "skills",
    }
)
# Backward-compatible internal aliases.
_PLACEHOLDER_NAME_TOKENS = RESUME_NAME_PLACEHOLDER_TOKENS
_NAME_HEADING_TOKENS = RESUME_NAME_HEADING_TOKENS
ROLE_NORMALIZATION_MAP = {
    "developer": "developer",
    "data scientist": "data_scientist",
    "program manager": "program_manager",
    "product manager": "product_manager",
    "designer": "designer",
    "user research": "user_research",
    "biz dev": "biz_dev",
    "marketing": "marketing",
}
NAME_PREFIXES = {
    "dr",
    "mr",
    "mrs",
    "ms",
    "prof",
    "miss",
    "mx",
}
NAME_SUFFIXES = {
    "jr",
    "sr",
    "iii",
    "ii",
    "iv",
    "v",
}


def normalize_resume_name_token(value: str) -> str:
    """Normalize candidate heading/name tokens for robust identity checks."""
    normalized = re.sub(r"\s+", " ", value).strip()
    normalized = re.sub(r"\s*:\s*$", "", normalized)
    return normalized.casefold()


def is_reserved_resume_name_token(value: str) -> bool:
    """Return True when a value is a non-name heading or placeholder token."""
    normalized = normalize_resume_name_token(value)
    return (
        normalized in RESUME_NAME_PLACEHOLDER_TOKENS
        or normalized in RESUME_NAME_HEADING_TOKENS
    )


def _bounded_confidence(value: Any, fallback: float) -> float:
    """Clamp confidence values to [0, 1]."""
    try:
        parsed = float(value)
    except Exception:
        return fallback
    return max(0.0, min(1.0, parsed))


def _normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _normalize_name_part(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not any(char.isalpha() for char in normalized):
        return None
    return normalized


def _coerce_email_list(value: Any) -> list[str]:
    if value is None:
        return []

    raw_values: list[str]
    if isinstance(value, str):
        raw_values = re.findall(EMAIL_REGEX, value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = []
        for item in value:
            if isinstance(item, str):
                raw_values.extend(re.findall(EMAIL_REGEX, item))
            elif isinstance(item, (bytes, bytearray)):
                try:
                    raw_values.extend(
                        re.findall(EMAIL_REGEX, item.decode("utf-8", errors="ignore"))
                    )
                except Exception:
                    continue
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_email in raw_values:
        normalized_email = _normalize_email(raw_email)
        if not normalized_email:
            continue
        if re.fullmatch(EMAIL_REGEX, normalized_email) is None:
            continue
        if normalized_email in seen:
            continue
        seen.add(normalized_email)
        normalized.append(normalized_email)
    return normalized


def _extract_emails(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return _coerce_email_list(value)


def _normalize_scalar(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_description(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", value.strip())
    return normalized[:2000] or None


def _normalize_github(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().strip("/")
    if not candidate:
        return None

    github_match = re.search(
        r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
        candidate,
        flags=re.IGNORECASE,
    )
    if github_match:
        candidate = github_match.group(1)
    elif candidate.startswith("@"):
        candidate = candidate[1:]
    elif not re.fullmatch(r"[A-Za-z0-9-]{1,39}", candidate):
        return None

    candidate = candidate.lstrip("@").strip().strip("/")
    return candidate or None


def _normalize_linkedin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if "linkedin.com" not in candidate.lower():
        return None
    if not candidate.lower().startswith(("http://", "https://")):
        candidate = f"https://{candidate}"
    return candidate.rstrip("/")


def _linkedin_profile_key(value: Any) -> str | None:
    """Return a canonical key for LinkedIn profile identity comparison."""
    normalized = _normalize_linkedin(value)
    if not normalized:
        return None

    try:
        parsed = urlsplit(normalized)
    except Exception:
        return None

    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None

    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    if not path:
        return None
    profile_path = path.casefold()
    if profile_path.startswith("/in/") or profile_path.startswith("/pub/"):
        return profile_path
    return None


def _normalize_phone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 7:
        return None
    if candidate.startswith("+"):
        return f"+{digits}"
    return digits


def _normalize_country(value: Any) -> str | None:
    return shared_normalize_country(value)


def _normalize_city(value: Any) -> str | None:
    return shared_normalize_city(value, strip_parenthetical=True)


def _normalize_timezone_offset(value: str) -> str | None:
    return shared_normalize_timezone_offset(value)


def _normalize_timezone(value: Any) -> str | None:
    return shared_normalize_timezone(value)


def _normalize_role(value: Any) -> str | None:
    return shared_normalize_role(value, ROLE_NORMALIZATION_MAP)


def _normalize_role_collection(value: Any) -> list[str]:
    return shared_normalize_roles(value, ROLE_NORMALIZATION_MAP)


def _normalize_website_url(value: str) -> str:
    normalized = shared_normalize_website_url(
        value,
        allow_scheme_less=True,
        disallowed_host_predicate=_is_disallowed_personal_website_host,
    )
    return normalized or ""


def _is_disallowed_personal_website_host(host: str) -> bool:
    normalized_host = host.casefold()
    if normalized_host.startswith("www."):
        normalized_host = normalized_host[4:]

    if normalized_host in PERSONAL_WEBSITE_DISALLOWED_HOSTS:
        return True

    match = re.fullmatch(r"([a-z0-9-]+)\.js", normalized_host)
    if match and match.group(1) in TECH_STACK_WEBSITE_DISALLOWED_PREFIXES:
        return True
    if normalized_host in {
        f"{prefix}.net" for prefix in TECH_STACK_WEBSITE_DISALLOWED_PREFIXES
    }:
        return True

    return False


def _normalize_website_links(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        return []

    normalized_links: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        candidate = raw_value.strip().strip(")]},.;:")
        if not candidate:
            continue
        normalized_link = _normalize_website_url(candidate)
        if not normalized_link:
            continue
        lower = normalized_link.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized_links.append(normalized_link)

    return normalized_links


def _normalize_url_candidate_kind(value: Any) -> str:
    if not isinstance(value, str):
        return LLM_URL_CANDIDATE_KIND_OTHER
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in {
        "personal",
        "personal_website",
        "portfolio",
        "homepage",
        "website",
    }:
        return LLM_URL_CANDIDATE_KIND_PERSONAL
    if normalized in {
        "social",
        "social_profile",
        "social_url",
        "social_link",
        "social_profile_url",
    }:
        return LLM_URL_CANDIDATE_KIND_SOCIAL
    return LLM_URL_CANDIDATE_KIND_OTHER


def _extract_website_url_candidates(
    value: Any,
) -> list[tuple[str, str, float]]:
    if not isinstance(value, (list, tuple)):
        return []

    normalized: dict[str, tuple[str, str, float]] = {}
    for raw_candidate in value:
        if not isinstance(raw_candidate, Mapping):
            continue

        raw_url = raw_candidate.get("url")
        if not isinstance(raw_url, str):
            continue

        normalized_url = _normalize_website_url(raw_url)
        if not normalized_url:
            continue

        kind = _normalize_url_candidate_kind(raw_candidate.get("kind"))
        confidence = _bounded_confidence(
            raw_candidate.get("confidence"),
            LLM_WEBSITE_URL_MIN_CONFIDENCE,
        )
        if confidence < LLM_WEBSITE_URL_MIN_CONFIDENCE:
            continue

        key = normalized_url.casefold()
        prior = normalized.get(key)
        if prior is not None and prior[2] >= confidence:
            continue
        normalized[key] = (normalized_url, kind, confidence)

    return list(normalized.values())


def _has_personal_website_context(
    resume_text: str,
    start_index: int,
    end_index: int,
) -> bool:
    context_start = max(0, start_index - 50)
    context_end = min(len(resume_text), end_index + 50)
    context = resume_text[context_start:context_end].casefold()
    return any(keyword in context for keyword in PERSONAL_WEBSITE_CONTEXT_KEYWORDS)


def _build_website_and_social_from_candidates(
    llm_candidates: list[tuple[str, str, float]],
    heuristic_candidates: list[tuple[str, float]],
) -> tuple[list[str], list[str]]:
    urls_to_consider: list[str] = []
    seen: set[str] = set()

    for candidate_url, candidate_kind, candidate_confidence in llm_candidates:
        if candidate_kind == LLM_URL_CANDIDATE_KIND_PERSONAL:
            if candidate_confidence < LLM_PERSONAL_URL_MIN_CONFIDENCE:
                continue
        elif candidate_kind == LLM_URL_CANDIDATE_KIND_SOCIAL:
            if candidate_confidence < LLM_SOCIAL_URL_MIN_CONFIDENCE:
                continue
        elif candidate_confidence < PERSONAL_WEBSITE_MIN_CONFIDENCE:
            continue

        candidate_key = candidate_url.casefold()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        urls_to_consider.append(candidate_url)

    for candidate_url, candidate_confidence in heuristic_candidates:
        if candidate_confidence < PERSONAL_WEBSITE_MIN_CONFIDENCE:
            continue

        candidate_key = candidate_url.casefold()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        urls_to_consider.append(candidate_url)

    return _split_social_and_website_links(urls_to_consider)


def _is_social_url(value: str) -> bool:
    try:
        host = urlsplit(value).hostname
    except Exception:
        return False
    if not host:
        return False
    normalized_host = host.casefold().lstrip("www.")
    return any(
        normalized_host == domain or normalized_host.endswith(f".{domain}")
        for domain in SOCIAL_LINK_DOMAINS
    )


def _split_social_and_website_links(
    website_links: list[str],
) -> tuple[list[str], list[str]]:
    social_links: list[str] = []
    normal_links: list[str] = []
    seen_social: set[str] = set()
    seen_normal: set[str] = set()

    for raw_link in website_links:
        if not isinstance(raw_link, str):
            continue
        candidate = raw_link.strip().rstrip("/")
        if not candidate:
            continue
        if _is_personal_website_disallowed(candidate) and not _is_social_url(candidate):
            continue
        if _is_social_url(candidate):
            social_key = candidate.casefold()
            if social_key in seen_social:
                continue
            seen_social.add(social_key)
            social_links.append(candidate)
            continue

        normal_key = candidate.casefold()
        if normal_key in seen_normal:
            continue
        seen_normal.add(normal_key)
        normal_links.append(candidate)

    return normal_links, social_links


def _is_personal_website_disallowed(url: str) -> bool:
    host = urlsplit(url).hostname
    if not host:
        return False
    return _is_disallowed_personal_website_host(host)


def _extract_github_username(links: list[str]) -> str | None:
    for raw_link in links:
        if not isinstance(raw_link, str):
            continue
        username = _normalize_github(raw_link)
        if username:
            return username
    return None


def _extract_linkedin_url_from_links(links: list[str]) -> str | None:
    for raw_link in links:
        if not isinstance(raw_link, str):
            continue
        match = LINKEDIN_PROFILE_PATTERN.search(raw_link)
        if match is None:
            continue
        linked_in_url = _normalize_linkedin(match.group(0))
        if linked_in_url is not None:
            return linked_in_url
    return None


def _normalize_seniority(value: Any) -> str | None:
    return shared_normalize_seniority(value, empty_as_unknown=True)


def _normalize_skills(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_skills = [str(skill).strip() for skill in value]
    elif isinstance(value, str):
        raw_skills = [item.strip() for item in value.replace(";", ",").split(",")]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_skill in raw_skills:
        skill = re.sub(r"\s+", " ", raw_skill).strip()
        if not skill:
            continue
        lowered = skill.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(skill)
    return normalized


def _normalize_skill_payload(
    skills_value: Any,
    skill_attrs_value: Any,
) -> tuple[list[str], dict[str, int]]:
    return normalize_skill_payload(
        skills_value,
        skill_attrs_value,
        disallowed=DISALLOWED_RESUME_SKILLS,
    )


def _normalize_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if is_reserved_resume_name_token(normalized):
        return None
    return normalized


def _is_placeholder_name(value: str | None) -> bool:
    if not isinstance(value, str):
        return False

    normalized = value.strip().casefold()
    return normalized in _PLACEHOLDER_NAME_TOKENS


def _parse_json_object(content: str) -> dict[str, Any]:
    raw = content.strip()
    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.startswith("```")]
        raw = "\n".join(lines).strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Model output was not a JSON object")
    return parsed


class ResumeExtractedProfile(BaseModel):
    """Normalized profile fields extracted from resume text."""

    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    primary_roles: list[str] = Field(default_factory=list)
    github_username: str | None = None
    linkedin_url: str | None = None
    timezone: str | None = None
    address_city: str | None = None
    description: str | None = None
    phone: str | None = None
    website_links: list[str] = Field(default_factory=list)
    social_links: list[str] = Field(default_factory=list)
    address_country: str | None = None
    seniority_level: str | None = None
    additional_emails: list[str] = Field(default_factory=list)
    availability: str | None = None
    rate_range: str | None = None
    referred_by: str | None = None
    skills: list[str] = Field(default_factory=list)
    skill_attrs: dict[str, int] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str


class ResumeProfileExtractor:
    """Extract candidate profile fields from resume text."""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        model: str = "5o-mini",
        max_tokens: int = 800,
        snippet_chars: int = 12000,
    ) -> None:
        self.model = model.strip() if model else "5o-mini"
        if not self.model:
            self.model = "5o-mini"
        self.max_tokens = max_tokens
        self.snippet_chars = max(1000, snippet_chars)
        self.client: Any = None

        if api_key and OpenAIClient is not None:
            self.client = OpenAIClient(
                api_key=api_key,
                base_url=base_url,
            )

    def extract(
        self,
        resume_text: str,
        *,
        extra_sources: Mapping[str, str] | None = None,
    ) -> ResumeExtractedProfile:
        """Return extracted fields from resume text."""
        source_texts = self._build_source_inputs(
            resume_text=resume_text,
            extra_sources=extra_sources,
        )
        text = source_texts.get("resume", "")
        if not text:
            return self._heuristic_extract(source_texts)

        if self.client is None:
            return self._heuristic_extract(source_texts)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract candidate profile fields from resumes for a CRM. "
                            "Return JSON only with no commentary. Be conservative: when unsure, use null."
                        ),
                    },
                    {
                        "role": "user",
                        "content": self._build_prompt(
                            source_texts=source_texts,
                            primary_text=text,
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
            raw_content = response.choices[0].message.content
            if not raw_content:
                raise ValueError("LLM returned empty content")

            parsed = _parse_json_object(raw_content)
            raw_first_name = parsed.get("firstName")
            if raw_first_name is None:
                raw_first_name = parsed.get("first_name")
            raw_last_name = parsed.get("lastName")
            if raw_last_name is None:
                raw_last_name = parsed.get("last_name")
            extracted_name = _normalize_name(parsed.get("name"))
            extracted_first_name, extracted_last_name = self.split_name(
                full_name=extracted_name,
                first_name_hint=raw_first_name,
                last_name_hint=raw_last_name,
            )
            parsed_url_candidates = _extract_website_url_candidates(
                parsed.get("website_url_candidates")
            )
            legacy_website_links = _normalize_website_links(parsed.get("website_links"))
            legacy_social_links = _normalize_website_links(parsed.get("social_links"))
            heuristic_candidates = (
                ResumeProfileExtractor._extract_website_link_candidates(resume_text)
            )
            parsed_website_links, parsed_social_links = (
                _build_website_and_social_from_candidates(
                    parsed_url_candidates,
                    heuristic_candidates,
                )
            )
            if not parsed_url_candidates and (
                legacy_website_links or legacy_social_links
            ):
                legacy_website_links, legacy_social_links = (
                    _split_social_and_website_links(
                        [*legacy_website_links, *legacy_social_links]
                    )
                )
                parsed_website_set = {u.casefold() for u in parsed_website_links}
                parsed_social_set = {u.casefold() for u in parsed_social_links}
                for item in legacy_website_links:
                    if item.casefold() not in parsed_website_set:
                        parsed_website_set.add(item.casefold())
                        parsed_website_links.append(item)
                for item in legacy_social_links:
                    if item.casefold() not in parsed_social_set:
                        parsed_social_set.add(item.casefold())
                        parsed_social_links.append(item)
            derived_links = [*parsed_website_links, *parsed_social_links]
            github_username = _normalize_github(parsed.get("github_username"))
            if not github_username:
                github_username = _extract_github_username(derived_links)
            parsed_skills, parsed_skill_attrs = _normalize_skill_payload(
                parsed.get("skills"),
                parsed.get("skill_attrs"),
            )
            parsed_emails = _coerce_email_list(parsed.get("additional_emails"))
            parsed_email = _normalize_email(parsed.get("email"))
            if not parsed_email and parsed_emails:
                parsed_email = parsed_emails[0]
                parsed_emails = parsed_emails[1:]
            linkedin_url = _normalize_linkedin(parsed.get("linkedin_url")) or (
                self._extract_linkedin_url(resume_text)
                or _extract_linkedin_url_from_links(derived_links)
            )
            if github_username:
                parsed_website_links = [
                    item
                    for item in parsed_website_links
                    if _normalize_github(item) != github_username
                ]
                parsed_social_links = [
                    item
                    for item in parsed_social_links
                    if _normalize_github(item) != github_username
                ]
            linkedin_profile_key = _linkedin_profile_key(linkedin_url)
            if linkedin_profile_key:
                parsed_website_links = [
                    item
                    for item in parsed_website_links
                    if _linkedin_profile_key(item) != linkedin_profile_key
                ]
                parsed_social_links = [
                    item
                    for item in parsed_social_links
                    if _linkedin_profile_key(item) != linkedin_profile_key
                ]
            return ResumeExtractedProfile(
                name=extracted_name,
                first_name=extracted_first_name,
                last_name=extracted_last_name,
                email=parsed_email,
                additional_emails=parsed_emails,
                description=_normalize_description(parsed.get("description")),
                primary_roles=_normalize_role_collection(
                    parsed.get("primary_roles") or parsed.get("primary_role")
                ),
                github_username=github_username,
                linkedin_url=linkedin_url,
                timezone=_normalize_timezone(parsed.get("timezone")),
                address_city=_normalize_city(parsed.get("address_city")),
                phone=_normalize_phone(parsed.get("phone")),
                website_links=parsed_website_links,
                social_links=parsed_social_links,
                address_country=_normalize_country(parsed.get("address_country")),
                seniority_level=(
                    _normalize_seniority(parsed.get("seniority_level"))
                    or self._infer_seniority_from_resume(resume_text)
                    or "unknown"
                ),
                availability=_normalize_scalar(parsed.get("availability"))
                or _normalize_scalar(source_texts.get("availability", "")),
                rate_range=_normalize_scalar(parsed.get("rate_range"))
                or _normalize_scalar(source_texts.get("rate_range", "")),
                referred_by=_normalize_scalar(parsed.get("referred_by"))
                or _normalize_scalar(source_texts.get("referred_by", "")),
                skills=parsed_skills,
                skill_attrs=parsed_skill_attrs,
                confidence=_bounded_confidence(
                    parsed.get("confidence", 0.75),
                    fallback=0.75,
                ),
                source=self.model,
            )
        except Exception:
            return self._heuristic_extract(source_texts)

    def _heuristic_extract(
        self,
        source_texts: Mapping[str, str] | dict[str, str],
    ) -> ResumeExtractedProfile:
        snippet = self._build_source_blob(source_texts).strip()[: self.snippet_chars]
        extracted_emails = _extract_emails(snippet)
        github_match = re.search(
            r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
            snippet,
            flags=re.IGNORECASE,
        )
        linkedin_url = self._extract_linkedin_url(snippet)
        phone_match = re.search(
            r"(?:\+?\d[\d\s().-]{7,}\d)",
            snippet,
        )
        name_match = self._extract_name(snippet)
        country = self._extract_country(snippet)
        seniority = self._extract_seniority(snippet)
        skills, skill_attrs = self._extract_skills(snippet)
        website_and_social = self._extract_website_links(snippet)
        website_links, social_links = _split_social_and_website_links(
            website_and_social
        )
        github_username = (
            _normalize_github(github_match.group(1)) if github_match else None
        )
        if not github_username:
            github_username = _extract_github_username(website_and_social)
        if not linkedin_url:
            linkedin_url = _extract_linkedin_url_from_links(
                [*website_links, *social_links]
            )
        timezone = self._extract_timezone(snippet)
        city = self._extract_city(snippet)
        linkedin_profile_key = _linkedin_profile_key(linkedin_url)
        if linkedin_profile_key:
            website_links = [
                item
                for item in website_links
                if _linkedin_profile_key(item) != linkedin_profile_key
            ]
            social_links = [
                item
                for item in social_links
                if _linkedin_profile_key(item) != linkedin_profile_key
            ]
        if github_username:
            website_links = [
                item
                for item in website_links
                if _normalize_github(item) != github_username
            ]
            social_links = [
                item
                for item in social_links
                if _normalize_github(item) != github_username
            ]
        availability = _normalize_scalar(source_texts.get("availability"))
        if not availability:
            availability = _normalize_scalar(source_texts.get("rate"))
        rate_range = _normalize_scalar(source_texts.get("rate_range"))
        referred_by = _normalize_scalar(source_texts.get("referred_by"))
        first_name, last_name = self.split_name(full_name=name_match)

        return ResumeExtractedProfile(
            name=name_match,
            first_name=first_name,
            last_name=last_name,
            email=extracted_emails[0] if extracted_emails else None,
            additional_emails=extracted_emails[1:],
            primary_roles=self._extract_roles(snippet),
            timezone=timezone,
            address_city=city,
            github_username=github_username,
            linkedin_url=linkedin_url,
            phone=_normalize_phone(phone_match.group(0)) if phone_match else None,
            website_links=website_links,
            social_links=social_links,
            address_country=country,
            seniority_level=seniority,
            availability=availability,
            rate_range=rate_range,
            referred_by=referred_by,
            skills=skills,
            skill_attrs=skill_attrs,
            confidence=0.45,
            source="heuristic",
        )

    def _build_source_inputs(
        self,
        *,
        resume_text: str,
        extra_sources: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        sources: dict[str, str] = {}
        if resume_text:
            sources["resume"] = resume_text.strip()
        if extra_sources:
            for label, value in extra_sources.items():
                if not isinstance(label, str):
                    continue
                normalized_label = label.strip().lower()
                if not normalized_label:
                    continue
                if not isinstance(value, str):
                    continue
                normalized_value = value.strip()
                if not normalized_value:
                    continue
                sources[normalized_label] = normalized_value
        return sources

    @staticmethod
    def _build_source_blob(sources: Mapping[str, str]) -> str:
        return "\n\n".join(
            f"{label}:\n{text}" for label, text in sources.items() if text.strip()
        )

    def _build_prompt(
        self,
        source_texts: Mapping[str, str] | None = None,
        primary_text: str = "",
    ) -> str:
        merged_sources = source_texts or self._build_source_inputs(
            resume_text=primary_text
        )
        snippet = self._build_source_blob(merged_sources)[: self.snippet_chars]
        return (
            "Extract candidate profile fields from all provided sources.\n"
            "Return JSON with exact keys and no extras:\n"
            '{"name": string|null, "firstName": string|null, "lastName": string|null, '
            '"email": string|null, "additional_emails": string[]|null, '
            '"github_username": string|null, "linkedin_url": string|null, '
            '"primary_roles": string[]|null, '
            '"timezone": string|null, "address_city": string|null, '
            '"description": string|null, '
            '"website_url_candidates": ['
            '{"url": string|null, "kind": "personal_website|social_profile|other", '
            '"confidence": number, "reason": string|null}|null], '
            '"phone": string|null, "website_links": string[]|null, '
            '"social_links": string[]|null, '
            '"address_country": string|null, '
            '"seniority_level": string|null, "availability": string|null, '
            '"rate_range": string|null, "referred_by": string|null, '
            '"skills": string[]|null, '
            '"skill_attrs": {"<skill>": {"strength": 1-5}}|null, '
            '"confidence": number}\n'
            "Rules:\n"
            "- For each explicit website/social URL-like string in the source, emit website_url_candidates entries\n"
            "- each candidate must include a 0-1 confidence score\n"
            "- kind must be: personal_website, social_profile, or other\n"
            "- treat a candidate as personal_website only when confidence is high (>=0.85)\n"
            "- treat a candidate as social_profile when confidence is high (>=0.7)\n"
            "- route candidate urls to website_links and social_links by type and host-level validation\n"
            '- personal_website candidates should be explicit portfolio/homepage signals (for example: "portfolio", "personal website", "homepage", contact header), not technology/framework mentions\n'
            "- website_links/social_links should mirror high-confidence candidates; if website_url_candidates are unavailable, use website_links/social_links and heuristics as fallback\n"
            "- prefer explicit values from header/contact sections\n"
            "- treat website_links as personal or portfolio homepage URLs only\n"
            "- if a URL token looks like a language, framework, or package name (for example: asp.net, next.js, node.js, react.js), never emit it as a website candidate\n"
            "- do not include github.com or linkedin.com profile URLs in website_links\n"
            "- if a URL is a social profile, place it into dedicated profile fields (github_username, linkedin_url) or social_links for cSocialLinks\n"
            "- infer URLs from regex-like patterns in the provided text, including markdown links\n"
            "- when in doubt, omit website URLs (be conservative)\n"
            "- only assign high confidence to PERSONAL website candidates when you can verify the link is a person-owned homepage/portfolio and not a technology reference\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- infer linkedin_url and website_links from bare domains when scheme is missing\n"
            "- for phone return digits with optional leading +\n"
            "- if timezone is provided, normalize it to UTC offset form like UTC±HH:MM before output\n"
            "- infer seniority_level as one of: junior, midlevel, senior, staff\n"
            "- for description, produce 1-2 concise sentences that describe the person and their focus areas, based only on explicit resume details; otherwise null\n"
            "- keep description factual and neutral; avoid marketing/sales phrasing\n"
            "- map strengths from 1-5 where available; omit when unknown\n"
            "- return skills as lowercase canonical names with minimal punctuation\n"
            "- canonicalize known variants like ab testing, go to market, react native\n"
            "- never include generic/disallowed skills: code review, debugging, testing, bug tracking, code quality, performance optimization\n"
            "- use 4-5 years with ownership and impact cues as senior\n"
            "- use staff for 7+ years, or 5+ years with strong technical ownership/leadership\n"
            "- weight company impact:\n"
            "  - +1 for leadership titles (staff/lead/principal/architect)\n"
            "  - +1 for enterprise-scale impact signals (team ownership, direct reports, cross-team work, large org terms)\n"
            "  - when company signal is ambiguous, return conservative midlevel\n"
            "- copy availability, rate_range, and referred_by if they are provided in source text\n"
            "- use 'unknown' for unknown or ambiguous fields\n"
            "- confidence is 0-1 for overall extraction reliability\n\n"
            f"Sources:\n{snippet}"
        )

    def split_name(
        self,
        full_name: str | None,
        *,
        first_name_hint: str | None = None,
        last_name_hint: str | None = None,
    ) -> tuple[str, str]:
        """Return CRM-safe first/last-name pairs for a profile name."""
        first_name = _normalize_name_part(first_name_hint)
        last_name = _normalize_name_part(last_name_hint)
        if first_name and _is_placeholder_name(first_name):
            first_name = None
        if last_name and _is_placeholder_name(last_name):
            last_name = None
        normalized_full_name = _normalize_name(full_name)
        if normalized_full_name and _is_placeholder_name(normalized_full_name):
            normalized_full_name = None

        if first_name and last_name:
            return first_name, last_name

        if not normalized_full_name:
            return (
                first_name or DEFAULT_FALLBACK_FIRST_NAME,
                last_name or DEFAULT_FALLBACK_LAST_NAME,
            )

        inferred_first: str | None = first_name
        inferred_last: str | None = last_name
        if normalized_full_name:
            inferred = None
            if self.client is not None and not _is_placeholder_name(
                normalized_full_name
            ):
                try:
                    inferred = self._split_name_with_llm(normalized_full_name)
                except Exception:
                    inferred = None
            if inferred is None:
                inferred = self._split_name_heuristically(normalized_full_name)
            if inferred:
                inferred_first, inferred_last = inferred
                if not first_name:
                    first_name = inferred_first
                if not last_name:
                    last_name = inferred_last

        return (
            first_name or DEFAULT_FALLBACK_FIRST_NAME,
            last_name or inferred_last or SINGLE_NAME_FALLBACK_LAST_NAME,
        )

    def _split_name_with_llm(self, full_name: str) -> tuple[str, str] | None:
        """Ask the model to split a display name into first/last."""
        if self.client is None:
            return None

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Split person names into firstName and lastName for CRM fields. "
                        "Return JSON only with no extra keys."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Name: {full_name}. "
                        'If this is a single name, set lastName to "Unknown".'
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=80,
        )
        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("LLM returned empty name split content")

        parsed = _parse_json_object(raw_content)
        split_first = _normalize_name_part(parsed.get("firstName"))
        split_last = _normalize_name_part(parsed.get("lastName"))
        split_first = split_first or _normalize_name_part(parsed.get("first_name"))
        split_last = split_last or _normalize_name_part(parsed.get("last_name"))
        if split_first and _is_placeholder_name(split_first):
            split_first = None
        if split_last and _is_placeholder_name(split_last):
            split_last = None
        if not split_first and not split_last:
            return None
        if not split_first or not split_last:
            heuristic_first, heuristic_last = self._split_name_heuristically(full_name)
            split_first = split_first or heuristic_first
            split_last = split_last or heuristic_last
        return (
            split_first or DEFAULT_FALLBACK_FIRST_NAME,
            split_last or SINGLE_NAME_FALLBACK_LAST_NAME,
        )

    @staticmethod
    def _split_name_heuristically(full_name: str) -> tuple[str, str]:
        normalized_input = full_name.strip()
        if not normalized_input:
            return (
                DEFAULT_FALLBACK_FIRST_NAME,
                DEFAULT_FALLBACK_LAST_NAME,
            )

        comma_left, comma, comma_right = normalized_input.partition(",")
        if comma and comma_left.strip() and comma_right.strip():
            full_name = f"{comma_right.strip()} {comma_left.strip()}"

        parts = [
            token.strip() for token in re.split(r"\s+", full_name) if token.strip()
        ]
        if not parts:
            return (
                DEFAULT_FALLBACK_FIRST_NAME,
                DEFAULT_FALLBACK_LAST_NAME,
            )

        while parts and parts[0].lower().strip(".") in NAME_PREFIXES:
            parts = parts[1:]

        if not parts:
            return (
                DEFAULT_FALLBACK_FIRST_NAME,
                DEFAULT_FALLBACK_LAST_NAME,
            )

        if len(parts) == 1:
            return (
                parts[0],
                SINGLE_NAME_FALLBACK_LAST_NAME,
            )

        if len(parts) >= 2 and parts[-1].lower().strip(".") in NAME_SUFFIXES:
            last = parts[-2]
            if len(parts) < 3:
                return (
                    parts[0],
                    SINGLE_NAME_FALLBACK_LAST_NAME,
                )
            normalized_last = _normalize_name_part(last)
            return (
                parts[0],
                normalized_last or SINGLE_NAME_FALLBACK_LAST_NAME,
            )

        last = _normalize_name_part(parts[-1])
        if not last:
            return (
                parts[0],
                SINGLE_NAME_FALLBACK_LAST_NAME,
            )

        return (
            parts[0],
            last,
        )

    @staticmethod
    def _extract_name(resume_text: str) -> str | None:
        lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
        for line in lines[:40]:
            if len(line) < 2 or len(line) > 70:
                continue
            if "@" in line or "http" in line.lower():
                continue
            if not any(char.isalpha() for char in line):
                continue
            if is_reserved_resume_name_token(line):
                continue
            return line
        return None

    @staticmethod
    def _extract_linkedin_url(resume_text: str) -> str | None:
        match = LINKEDIN_PROFILE_PATTERN.search(resume_text)
        if not match:
            return None
        return _normalize_linkedin(match.group(0))

    @staticmethod
    def _extract_country(resume_text: str) -> str | None:
        match = re.search(
            r"(?im)^(?:address\s*country|country)\s*[:\-]\s*(.+)$",
            resume_text,
        )
        if match:
            normalized = _normalize_country(match.group(1))
            if normalized:
                return normalized

        return None

    @staticmethod
    def _extract_timezone(resume_text: str) -> str | None:
        match = re.search(
            r"(?im)^(?:timezone|time\s*zone|tz)\s*[:\-]\s*(.+)$",
            resume_text,
        )
        if match:
            normalized = _normalize_timezone(match.group(1))
            if normalized:
                return normalized

        inline_matches = re.findall(
            r"(?i)\b(?:utc|gmt)\s*([+-]\s*\d{1,2}(?:[:.]\d{1,2})?)\b",
            resume_text,
        )
        for raw_offset in inline_matches:
            normalized = _normalize_timezone_offset(raw_offset)
            if normalized:
                return normalized

        return None

    @staticmethod
    def _extract_city(resume_text: str) -> str | None:
        match = re.search(
            r"(?im)^(?:address\s*city|current\s*city|city)\s*[:\-]\s*(.+)$",
            resume_text,
        )
        if match:
            return _normalize_city(match.group(1))

        return None

    @staticmethod
    def _extract_seniority(resume_text: str) -> str | None:
        match = re.search(
            r"(?im)^\s*seniority\s*[:\-]\s*(.+)$",
            resume_text,
        )
        if match:
            parsed = _normalize_seniority(match.group(1))
            if parsed:
                return parsed

        inferred = ResumeProfileExtractor._infer_seniority_from_resume(resume_text)
        if inferred:
            return inferred

        return "unknown"

    @staticmethod
    def _extract_roles(resume_text: str) -> list[str]:
        roles: list[str] = []
        for match in re.finditer(
            r"(?im)^\s*(?:primary\s*roles?|roles?|role)\s*[:\-]\s*(.+)$",
            resume_text,
        ):
            roles.extend(_normalize_role_collection(match.group(1)))
        return roles

    @staticmethod
    def _infer_seniority_from_resume(resume_text: str) -> str | None:
        lower_text = resume_text.lower()
        years = ResumeProfileExtractor._extract_years_of_experience(resume_text)
        if years is None:
            return None

        impact_score = 0
        if re.search(
            r"\b(staff|principal|lead engineer|principal engineer)\b", lower_text
        ):
            impact_score += 2
        if re.search(
            r"\b(architect|engineering lead|tech lead|lead dev|leading|led a team|team lead)\b",
            lower_text,
        ):
            impact_score += 1
        if re.search(
            r"(?:\bteam of\s+\d+\b|\bmanaged\b|\bmentored\b|\bcross-functional\b|"
            r"\benterprise\b|\bglobal\b|\bseries [abcd]\b|\b500\+?(?!\d)|\b1000\+?(?!\d)|"
            r"\b10[0-9]{2,}\s+employees\b)",
            lower_text,
        ):
            impact_score += 1

        if years >= 7:
            return "staff" if impact_score >= 1 else "senior"
        if years >= 5:
            return "senior"
        if years >= 4:
            return "senior" if impact_score >= 1 else "midlevel"
        if years >= 2:
            return "midlevel"
        return "junior"

    @staticmethod
    def _extract_years_of_experience(resume_text: str) -> int | None:
        years = []
        year_patterns = [
            r"(\d{1,2})\+?\s*years?\s+of\s+(?:software\s+|engineering\s+)?experience",
            r"(?:experience|career)\s*(?:\:\s*)?(\d{1,2})\+?\s*years",
            r"over\s+(\d{1,2})\s+years",
        ]
        for pattern in year_patterns:
            for match in re.finditer(pattern, resume_text, flags=re.IGNORECASE):
                try:
                    years.append(int(match.group(1)))
                except Exception:
                    pass

        date_range_pattern = re.compile(
            r"\b((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2}|present|current)\b",
            flags=re.IGNORECASE,
        )
        today_year = datetime.now(timezone.utc).year
        for match in date_range_pattern.finditer(resume_text):
            start_year = int(match.group(1))
            end_token = match.group(2).lower()
            end_year = (
                today_year if end_token in {"present", "current"} else int(end_token)
            )
            years.append(max(0, end_year - start_year))

        if not years:
            return None
        return max(years)

    @staticmethod
    def _extract_skills(resume_text: str) -> tuple[list[str], dict[str, int]]:
        match = re.search(
            r"(?im)^\s*(?:skills|technical\s+skills|technologies)\s*[:\-]?\s*$",
            resume_text,
        )
        if not match:
            return [], {}

        line_start = match.end()
        tail = resume_text[line_start : line_start + 500]
        first_line = tail.splitlines()[0] if tail else ""
        if first_line:
            skills, attrs = _normalize_skill_payload(
                [item.strip() for item in first_line.split(",") if item.strip()],
                None,
            )
            if skills and not attrs:
                attrs = {skill.casefold(): DEFAULT_SKILL_STRENGTH for skill in skills}
            return skills, attrs
        return [], {}

    @staticmethod
    def _extract_website_link_candidates(
        resume_text: str,
    ) -> list[tuple[str, float]]:
        matches: list[tuple[str, float]] = []
        for match in MARKDOWN_URL_PATTERN.finditer(resume_text):
            raw_url = match.group(1).strip().strip(")]},.;:")
            if not raw_url:
                continue
            matches.append((raw_url, 1.0))

        for match in SCHEME_URL_PATTERN.finditer(resume_text):
            matches.append((match.group(0), 1.0))

        for match in BARE_DOMAIN_URL_PATTERN.finditer(resume_text):
            if match.start() > 0 and resume_text[match.start() - 1] == "@":
                continue
            raw_url = match.group(0)
            confidence = PERSONAL_WEBSITE_MIN_CONFIDENCE
            if _has_personal_website_context(
                resume_text,
                match.start(),
                match.end(),
            ):
                confidence = PERSONAL_WEBSITE_CONTEXT_CONFIDENCE
            matches.append((raw_url, confidence))

        normalized_links: list[tuple[str, float]] = []
        seen: set[str] = set()
        for raw_link, confidence in matches:
            normalized_link = _normalize_website_url(raw_link.strip())
            if not normalized_link:
                continue
            normalized_key = normalized_link.casefold()
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            normalized_links.append((normalized_link, confidence))
        return normalized_links

    @staticmethod
    def _extract_website_links(resume_text: str) -> list[str]:
        return [
            link
            for link, _ in ResumeProfileExtractor._extract_website_link_candidates(
                resume_text
            )
        ]
