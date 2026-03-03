"""Shared resume text extraction utilities for candidate fields."""

from __future__ import annotations

import json
import re
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


def _normalize_seniority(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in {"jr", "junior", "entry", "entry-level", "entry level"}:
        return "junior"
    if normalized in {"intern", "internship"}:
        return "junior"
    if normalized in {"mid-level", "midlevel", "mid", "intermediate"}:
        return "midlevel"
    if normalized in {"senior", "lead", "principal"}:
        return normalized
    if normalized in {"staff", "staff+"}:
        return "staff"
    return normalized


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
                address_country=_normalize_country(parsed.get("address_country")),
                seniority_level=_normalize_seniority(parsed.get("seniority_level")),
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
            '"phone": string|null, "address_country": string|null, '
            '"seniority_level": string|null, "skills": string[]|null, '
            '"confidence": number}\n'
            "Rules:\n"
            "- prefer explicit values from header/contact sections\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- for phone return digits with optional leading +\n"
            "- use null for unknown or ambiguous fields\n"
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
            return _normalize_seniority(match.group(1))
        return None

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
