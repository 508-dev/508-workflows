"""Shared resume text extraction utilities for candidate fields."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

try:
    from openai import OpenAI as OpenAIClient
except Exception:  # pragma: no cover
    OpenAIClient = None  # type: ignore[misc,assignment]


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


def _normalize_website_links(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        candidate = raw_value.strip().strip(")]},.;:")
        if not candidate:
            continue
        if candidate.lower().startswith("www."):
            candidate = f"https://{candidate}"
        if not candidate.startswith(("http://", "https://")):
            continue
        if "@" in candidate:
            continue
        lower = candidate.lower()
        if lower in seen:
            continue
        seen.add(lower)
        normalized.append(candidate.rstrip("/"))

    return normalized


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
        "principal",
        "principal engineer",
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
    address_country: str | None = None
    seniority_level: str | None = None
    skills: list[str] = Field(default_factory=list)
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

    def extract(self, resume_text: str) -> ResumeExtractedProfile:
        """Return extracted fields from resume text."""
        text = (resume_text or "").strip()
        if not text:
            return self._heuristic_extract("")

        if self.client is None:
            return self._heuristic_extract(text)

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
                    {"role": "user", "content": self._build_prompt(text)},
                ],
                temperature=0.1,
                max_tokens=self.max_tokens,
            )
            raw_content = response.choices[0].message.content
            if not raw_content:
                raise ValueError("LLM returned empty content")

            parsed = _parse_json_object(raw_content)
            return ResumeExtractedProfile(
                name=_normalize_name(parsed.get("name")),
                email=_normalize_email(parsed.get("email")),
                github_username=_normalize_github(parsed.get("github_username")),
                linkedin_url=_normalize_linkedin(parsed.get("linkedin_url")),
                phone=_normalize_phone(parsed.get("phone")),
                website_links=_normalize_website_links(parsed.get("website_links")),
                address_country=_normalize_country(parsed.get("address_country")),
                seniority_level=(
                    _normalize_seniority(parsed.get("seniority_level"))
                    or self._infer_seniority_from_resume(resume_text)
                    or "unknown"
                ),
                skills=_normalize_skills(parsed.get("skills")),
                confidence=_bounded_confidence(
                    parsed.get("confidence", 0.75),
                    fallback=0.75,
                ),
                source=self.model,
            )
        except Exception:
            return self._heuristic_extract(text)

    def _heuristic_extract(self, resume_text: str) -> ResumeExtractedProfile:
        snippet = (resume_text or "").strip()[: self.snippet_chars]
        email_match = re.search(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            snippet,
        )
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
        skills = self._extract_skills(snippet)

        return ResumeExtractedProfile(
            name=name_match,
            email=_normalize_email(email_match.group(0)) if email_match else None,
            github_username=(
                _normalize_github(github_match.group(1)) if github_match else None
            ),
            linkedin_url=(
                _normalize_linkedin(linkedin_match.group(0)) if linkedin_match else None
            ),
            phone=_normalize_phone(phone_match.group(0)) if phone_match else None,
            website_links=self._extract_website_links(snippet),
            address_country=country,
            seniority_level=seniority,
            skills=skills,
            confidence=0.45,
            source="heuristic",
        )

    def _build_prompt(self, resume_text: str) -> str:
        snippet = resume_text[: self.snippet_chars]
        return (
            "Extract candidate profile fields from this resume.\n"
            "Return JSON with exact keys and no extras:\n"
            '{"name": string|null, "email": string|null, '
            '"github_username": string|null, "linkedin_url": string|null, '
            '"phone": string|null, "website_links": string[]|null, '
            '"address_country": string|null, '
            '"seniority_level": string|null, "skills": string[]|null, '
            '"confidence": number}\n'
            "Rules:\n"
            "- prefer explicit values from header/contact sections\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- for phone return digits with optional leading +\n"
            "- infer seniority_level as one of: junior, midlevel, senior, staff\n"
            "- use 4-5 years with ownership and impact cues as senior\n"
            "- use staff for 7+ years, or 5+ years with strong technical ownership/leadership\n"
            "- weight company impact:\n"
            "  - +1 for leadership titles (staff/lead/principal/architect)\n"
            "  - +1 for enterprise-scale impact signals (team ownership, direct reports, cross-team work, large org terms)\n"
            "  - when company signal is ambiguous, return conservative midlevel\n"
            "- use 'unknown' for unknown or ambiguous fields\n"
            "- confidence is 0-1 for overall extraction reliability\n\n"
            f"Resume:\n{snippet}"
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
    def _extract_skills(resume_text: str) -> list[str]:
        match = re.search(
            r"(?im)^\s*(?:skills|technical\s+skills|technologies)\s*[:\-]?\s*$",
            resume_text,
        )
        if not match:
            return []

        line_start = match.end()
        tail = resume_text[line_start : line_start + 500]
        first_line = tail.splitlines()[0] if tail else ""
        if first_line:
            return _normalize_skills(first_line)
        return []

    @staticmethod
    def _extract_website_links(resume_text: str) -> list[str]:
        matches = re.findall(
            r"https?://[^\s\]\[()\"<>]+", resume_text, flags=re.IGNORECASE
        )
        return _normalize_website_links(matches)
