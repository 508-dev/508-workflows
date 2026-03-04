"""Job posting analysis and candidate requirement extraction."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from five08.discord_webhook import DiscordWebhookLogger
from five08.skills import normalize_skill_list

logger = logging.getLogger(__name__)

# Canonical Discord role names used for skill/type classification.
# These match the actual role names in the 508.dev Discord server.
DISCORD_SKILL_ROLE_NAMES: list[str] = [
    "Frontend",
    "Backend",
    "Full Stack",
    "AI Engineer",
    "Blockchain",
    "Mobile",
    "Android",
    "iOS",
    "Data Scientist",
    "Infra / Devops",
    "Product Manager",
    "Copywriter",
    "Designer",
    "Branding Specialist",
    "Logistics Specialist",
]

# Discord roles that are administrative/location/seniority rather than skills.
# These are synced but not used for skill-based role matching.
DISCORD_ROLES_EXCLUDE_FROM_SYNC: frozenset[str] = frozenset(
    {"Bots", "FixTweet", "@everyone"}
)

# ---------------------------------------------------------------------------
# Regex hints — used to pre-scan the posting and inform the LLM prompt.
# They are injected as context into the user message so the LLM can weigh
# them. A few signals (e.g. US-only detection) also act as override guards:
# if regex is confident, it wins over a conflicting LLM value.
# ---------------------------------------------------------------------------
_US_ONLY_RE = re.compile(
    r"\bUS[\s\-]?only\b"
    r"|\bUnited\s+States\s+only\b"
    r"|\bauthorized\s+to\s+work\s+in\s+the\s+(?:US|USA|United\s+States)\b"
    r"|\bUS\s+citizens?\b"
    r"|\bmust\s+be\s+(?:in|based\s+in)\s+(?:the\s+)?(?:US|USA|United\s+States)\b",
    re.IGNORECASE,
)

_SENIORITY_KEYWORDS: dict[str, str] = {
    "junior": "junior",
    "entrylevel": "junior",
    "midlevel": "midlevel",
    "senior": "senior",
    "staff": "staff",
    "principal": "staff",
}

_SENIORITY_RE = re.compile(
    r"\b(junior|entry[\s\-]level|mid[\s\-]level|midlevel|senior|staff|principal)\b",
    re.IGNORECASE,
)

SENIORITY_ORDER = ["junior", "midlevel", "senior", "staff"]


@dataclass(frozen=True)
class JobRequirements:
    """Normalized requirements extracted from a job posting."""

    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    # Subset of DISCORD_SKILL_ROLE_NAMES that apply to this role.
    # Used to match candidates via their discord_roles column.
    discord_role_types: list[str] = field(default_factory=list)
    seniority: str | None = None  # "junior" | "midlevel" | "senior" | "staff"
    location_type: str | None = None  # "us_only" | "timezone_preferred" | "remote_any"
    preferred_timezones: list[str] = field(default_factory=list)
    raw_location_text: str | None = None
    title: str | None = None


def _regex_hints(text: str) -> dict[str, Any]:
    """Extract cheap regex-based signals to include as hints in the LLM prompt."""
    hints: dict[str, Any] = {}

    if _US_ONLY_RE.search(text):
        hints["us_only_detected"] = True

    seniority_match = _SENIORITY_RE.search(text)
    if seniority_match:
        raw = seniority_match.group(1).lower().replace("-", "").replace(" ", "")
        hints["seniority_hint"] = _SENIORITY_KEYWORDS.get(raw)

    return hints


def _build_prompt(posting_text: str, hints: dict[str, Any]) -> str:
    hint_lines: list[str] = []
    if hints.get("us_only_detected"):
        hint_lines.append(
            "Note: regex detected a US-only location restriction in this posting."
        )
    if hints.get("seniority_hint"):
        hint_lines.append(
            f"Note: regex detected seniority keyword suggesting '{hints['seniority_hint']}'."
        )

    hint_block = ("\n".join(hint_lines) + "\n\n") if hint_lines else ""

    role_names_str = ", ".join(f'"{r}"' for r in DISCORD_SKILL_ROLE_NAMES)

    return (
        f"{hint_block}"
        "Analyze the following job posting and return a JSON object with these fields:\n"
        '- "title": string or null — the job title\n'
        '- "required_skills": array of strings — up to 5 most critical TECHNICAL skills, '
        'using concise canonical names (1-3 words, e.g. "effect ts", "typescript", '
        '"react", "postgresql", "solidity"). Order by importance. '
        "EXCLUDE soft skills, work styles, or behavioral traits such as "
        '"self-directed", "independent", "communication skills", "public github profile".\n'
        '- "preferred_skills": array of strings — secondary technical skills, same format, '
        "no soft skills\n"
        f'- "discord_role_types": array — classify using ONLY values from: [{role_names_str}]. '
        "Pick all that apply to this role.\n"
        '- "seniority": one of "junior", "midlevel", "senior", "staff", or null\n'
        '- "location_type": one of "us_only", "timezone_preferred", "remote_any", or null\n'
        '- "preferred_timezones": array of IANA timezone strings (e.g. "America/New_York"), '
        "empty if not specified\n"
        '- "raw_location_text": the location/timezone text from the posting, or null\n\n'
        "Return only the JSON object, no commentary.\n\n"
        "---\n"
        f"{posting_text}"
    )


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    return json.loads(text)


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [s for s in value if isinstance(s, str) and s.strip()]


def extract_job_requirements(
    posting_text: str,
    *,
    api_key: str | None,
    base_url: str | None = None,
    model: str = "gpt-5-mini",
    webhook_url: str | None = None,
) -> JobRequirements:
    """Extract structured job requirements from a posting using OpenAI.

    Raises RuntimeError if OpenAI is not configured (also logs to webhook).
    Uses regex pre-scan as cheap hints injected into the LLM prompt.
    """
    if not api_key:
        DiscordWebhookLogger(webhook_url=webhook_url).send(
            content=(
                "⚠️ **Job match extraction failed**: `OPENAI_API_KEY` is not configured. "
                "Set the key and restart the bot to enable `/match-candidates`."
            ),
        )
        raise RuntimeError(
            "OpenAI API key is not configured — cannot extract job requirements."
        )

    try:
        from openai import OpenAI as _OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    client = _OpenAI(api_key=api_key, base_url=base_url or None)

    hints = _regex_hints(posting_text)
    prompt = _build_prompt(posting_text, hints)

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a recruiting assistant. Extract structured hiring requirements "
                        "from job postings. Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
        )
    except Exception as exc:
        logger.error("OpenAI job extraction call failed: %s", exc)
        raise RuntimeError(f"OpenAI extraction failed: {exc}") from exc

    if not response.choices or not response.choices[0].message.content:
        raise RuntimeError(
            f"OpenAI returned empty or missing response content (finish_reason="
            f"{response.choices[0].finish_reason if response.choices else 'no choices'})."
        )
    raw_content = response.choices[0].message.content.strip()

    try:
        data = _parse_llm_response(raw_content)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse LLM job extraction response: %s", raw_content)
        raise RuntimeError(f"LLM returned unparseable response: {exc}") from exc

    required_skills = normalize_skill_list(
        _coerce_str_list(data.get("required_skills"))
    )
    preferred_skills = normalize_skill_list(
        _coerce_str_list(data.get("preferred_skills"))
    )

    # Only accept values from the known Discord role names list
    valid_role_names = set(DISCORD_SKILL_ROLE_NAMES)
    discord_role_types = [
        r
        for r in _coerce_str_list(data.get("discord_role_types"))
        if r in valid_role_names
    ]

    raw_seniority = data.get("seniority")
    normalized_seniority = (
        raw_seniority.strip().lower() if isinstance(raw_seniority, str) else None
    )
    seniority = (
        normalized_seniority if normalized_seniority in SENIORITY_ORDER else None
    )

    raw_location_type = data.get("location_type")
    normalized_location_type = (
        raw_location_type.strip().lower()
        if isinstance(raw_location_type, str)
        else None
    )
    location_type = (
        normalized_location_type
        if normalized_location_type in ("us_only", "timezone_preferred", "remote_any")
        else None
    )
    # Regex detection always wins — LLM may return "remote_any" despite explicit US-only text
    if hints.get("us_only_detected"):
        location_type = "us_only"

    preferred_timezones = _coerce_str_list(data.get("preferred_timezones"))

    raw_location_text = data.get("raw_location_text")
    title = data.get("title")

    return JobRequirements(
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        discord_role_types=discord_role_types,
        seniority=seniority,
        location_type=location_type,
        preferred_timezones=preferred_timezones,
        raw_location_text=raw_location_text
        if isinstance(raw_location_text, str)
        else None,
        title=title if isinstance(title, str) else None,
    )
