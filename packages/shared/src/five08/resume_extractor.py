"""Shared resume text extraction utilities for candidate fields."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from five08.skills import normalize_skill

try:
    from openai import OpenAI as OpenAIClient
except Exception:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]


DISALLOWED_SKILLS = {
    "code review",
    "debugging",
    "performance optimization",
    "testing",
    "code quality",
    "bug tracking",
    "bugtracking",
    "bug-tracking",
}

DEFAULT_SKILL_STRENGTH = 3
EMAIL_REGEX = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"

SOCIAL_LINK_DOMAINS = {
    "facebook.com",
    "fb.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "tiktok.com",
    "youtube.com",
    "youtube-nocookie.com",
}


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


def _normalize_github(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None

    github_match = re.search(
        r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39})",
        candidate,
        flags=re.IGNORECASE,
    )
    if github_match:
        candidate = github_match.group(1)

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
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized.title() if normalized else None


def _normalize_website_url(value: str) -> str:
    candidate = value.strip().strip(")]},.;:")
    if not candidate:
        return ""

    if candidate.lower().startswith("www."):
        candidate = f"https://{candidate}"
    if not candidate.startswith(("http://", "https://")):
        return ""

    try:
        parsed = urlsplit(candidate)
    except Exception:
        return ""

    if "@" in parsed.netloc:
        return ""

    host = parsed.hostname or ""
    if host.lower().startswith("www."):
        host = host[4:]
    if not host:
        return ""

    normalized_netloc = parsed.netloc
    lower_netloc = parsed.netloc.lower()
    if lower_netloc.startswith("www."):
        normalized_netloc = parsed.netloc[4:]
    elif host and lower_netloc.startswith(f"www.{host}"):
        normalized_netloc = parsed.netloc.replace(parsed.netloc[:4], "", 1)

    parsed = parsed._replace(netloc=normalized_netloc)
    return parsed.geturl().rstrip("/")


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


def _normalize_seniority(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return "unknown"
    if normalized in {"jr", "junior", "entry", "entry-level", "entry level"}:
        return "junior"
    if normalized in {"intern", "internship"}:
        return "junior"
    if normalized in {"mid-level", "midlevel", "mid", "intermediate"}:
        return "midlevel"
    if normalized in {"staff", "staff+", "staff and beyond"}:
        return "staff"
    if normalized in {
        "senior",
        "sr",
        "sr. engineer",
        "lead",
        "lead engineer",
        "lead engineer/tech lead",
    }:
        return "senior"
    if "lead" in normalized and ("engineer" in normalized or "lead" == normalized):
        return "senior"
    if "staff" in normalized:
        return "staff"
    if normalized.startswith("sr "):
        return "senior"
    return "unknown"


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


def _normalize_strength(value: Any) -> int | None:
    raw: Any = value
    if isinstance(raw, dict):
        raw = raw.get("strength")
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        strength = int(float(raw))
    except Exception:
        return None
    if not 1 <= strength <= 5:
        return None
    return strength


def _parse_skill_with_strength(raw_skill: str) -> tuple[str, int | None]:
    raw = raw_skill.strip()
    match = re.match(r"^(.*)\(\s*(\d*)\s*\)\s*$", raw)
    if match is None:
        normalized = normalize_skill(raw)
        if not normalized or normalized in DISALLOWED_SKILLS:
            return "", None
        return normalized, None

    base = match.group(1).strip()
    if not base:
        return "", None
    normalized = normalize_skill(base)
    if not normalized or normalized in DISALLOWED_SKILLS:
        return "", None
    return normalized, _normalize_strength(match.group(2))


def _normalize_skill_payload(
    skills_value: Any,
    skill_attrs_value: Any,
) -> tuple[list[str], dict[str, int]]:
    normalized_skills: list[str] = []
    normalized_attrs: dict[str, int] = {}

    raw_skill_items = skills_value if isinstance(skills_value, list) else []
    for raw_skill in raw_skill_items:
        skill, strength = _parse_skill_with_strength(str(raw_skill))
        if not skill:
            continue
        key = skill.casefold()
        if key in normalized_attrs and strength is not None:
            normalized_attrs[key] = max(normalized_attrs[key], strength)
            continue

        normalized_skills.append(skill)
        if strength is not None:
            normalized_attrs[key] = strength

    if isinstance(skill_attrs_value, dict):
        for raw_name, raw_payload in skill_attrs_value.items():
            normalized = normalize_skill(str(raw_name))
            if not normalized or normalized in DISALLOWED_SKILLS:
                continue
            strength = _normalize_strength(raw_payload)
            if strength is None:
                continue
            key = normalized.casefold()
            normalized_attrs[key] = max(normalized_attrs.get(key, 0), strength)
            if not any(existing.casefold() == key for existing in normalized_skills):
                normalized_skills.append(normalized)

    deduped: list[str] = []
    seen: set[str] = set()
    for raw_skill in normalized_skills:
        skill = normalize_skill(raw_skill)
        if not skill or skill in DISALLOWED_SKILLS:
            continue
        key = skill.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(skill)

    return deduped, {
        skill: normalized_attrs[skill.casefold()]
        for skill in deduped
        if normalized_attrs.get(skill.casefold(), 0) > 0
    }


def _normalize_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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
    email: str | None = None
    github_username: str | None = None
    linkedin_url: str | None = None
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
        model: str = "gpt-4o-mini",
        max_tokens: int = 800,
        snippet_chars: int = 12000,
    ) -> None:
        self.model = model.strip() if model else "gpt-4o-mini"
        if not self.model:
            self.model = "gpt-4o-mini"
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
            parsed_website_links = _normalize_website_links(parsed.get("website_links"))
            parsed_website_links, parsed_social_links = _split_social_and_website_links(
                parsed_website_links
            )
            parsed_skills, parsed_skill_attrs = _normalize_skill_payload(
                parsed.get("skills"),
                parsed.get("skill_attrs"),
            )
            parsed_emails = _coerce_email_list(parsed.get("additional_emails"))
            parsed_email = _normalize_email(parsed.get("email"))
            if not parsed_email and parsed_emails:
                parsed_email = parsed_emails[0]
                parsed_emails = parsed_emails[1:]
            return ResumeExtractedProfile(
                name=_normalize_name(parsed.get("name")),
                email=parsed_email,
                additional_emails=parsed_emails,
                github_username=_normalize_github(parsed.get("github_username")),
                linkedin_url=_normalize_linkedin(parsed.get("linkedin_url")),
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
        linkedin_match = re.search(
            r"(?:https?://)?(?:[\w.-]+\.)?linkedin\.com/in/[A-Za-z0-9\\-_%]+/?",
            snippet,
            flags=re.IGNORECASE,
        )
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
        availability = _normalize_scalar(source_texts.get("availability"))
        if not availability:
            availability = _normalize_scalar(source_texts.get("rate"))
        rate_range = _normalize_scalar(source_texts.get("rate_range"))
        referred_by = _normalize_scalar(source_texts.get("referred_by"))

        return ResumeExtractedProfile(
            name=name_match,
            email=extracted_emails[0] if extracted_emails else None,
            additional_emails=extracted_emails[1:],
            github_username=(
                _normalize_github(github_match.group(1)) if github_match else None
            ),
            linkedin_url=(
                _normalize_linkedin(linkedin_match.group(0)) if linkedin_match else None
            ),
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
            '{"name": string|null, "email": string|null, "additional_emails": string[]|null, '
            '"github_username": string|null, "linkedin_url": string|null, '
            '"phone": string|null, "website_links": string[]|null, '
            '"social_links": string[]|null, '
            '"address_country": string|null, '
            '"seniority_level": string|null, "availability": string|null, '
            '"rate_range": string|null, "referred_by": string|null, '
            '"skills": string[]|null, '
            '"skill_attrs": {"<skill>": {"strength": 1-5}}|null, '
            '"confidence": number}\n'
            "Rules:\n"
            "- prefer explicit values from header/contact sections\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- for phone return digits with optional leading +\n"
            "- infer seniority_level as one of: junior, midlevel, senior, staff\n"
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
            return line
        return None

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
            r"\b(team of\s+\d+|managed|mentored|cross-functional|enterprise|global|series [abcd]|"
            r"\b500\+?|\b1000\+?|\b10[0-9]{2,}\s+employees",
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
    def _extract_website_links(resume_text: str) -> list[str]:
        matches = re.findall(
            r"https?://[^\s\]\[()\"<>]+", resume_text, flags=re.IGNORECASE
        )
        return _normalize_website_links(matches)
