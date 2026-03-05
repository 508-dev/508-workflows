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

# Locality-based Discord role names used for geographic classification.
DISCORD_LOCALITY_ROLE_NAMES: list[str] = [
    "Asia",
    "Americas",
    "Europe",
    "USA",
    "Taiwan",
    "Japan",
    "Africa",
]

# Roles that should never be suggested or applied automatically.
DISCORD_ROLES_NEVER_SUGGEST: frozenset[str] = frozenset(
    {"Member", "FixTweet", "Bots", "Admin", "508 Bot"}
)

# Map normalized country name → locality Discord roles to suggest.
_COUNTRY_TO_LOCALITY_ROLES: dict[str, list[str]] = {
    "united states": ["USA", "Americas"],
    "usa": ["USA", "Americas"],
    "us": ["USA", "Americas"],
    "canada": ["Americas"],
    "mexico": ["Americas"],
    "brazil": ["Americas"],
    "argentina": ["Americas"],
    "colombia": ["Americas"],
    "chile": ["Americas"],
    "peru": ["Americas"],
    "venezuela": ["Americas"],
    "ecuador": ["Americas"],
    "bolivia": ["Americas"],
    "paraguay": ["Americas"],
    "uruguay": ["Americas"],
    "japan": ["Japan", "Asia"],
    "taiwan": ["Taiwan", "Asia"],
    "china": ["Asia"],
    "south korea": ["Asia"],
    "korea": ["Asia"],
    "india": ["Asia"],
    "singapore": ["Asia"],
    "hong kong": ["Asia"],
    "thailand": ["Asia"],
    "vietnam": ["Asia"],
    "indonesia": ["Asia"],
    "malaysia": ["Asia"],
    "philippines": ["Asia"],
    "bangladesh": ["Asia"],
    "pakistan": ["Asia"],
    "nepal": ["Asia"],
    "sri lanka": ["Asia"],
    "myanmar": ["Asia"],
    "cambodia": ["Asia"],
    "laos": ["Asia"],
    "mongolia": ["Asia"],
    "united kingdom": ["Europe"],
    "uk": ["Europe"],
    "england": ["Europe"],
    "germany": ["Europe"],
    "france": ["Europe"],
    "spain": ["Europe"],
    "italy": ["Europe"],
    "netherlands": ["Europe"],
    "sweden": ["Europe"],
    "norway": ["Europe"],
    "denmark": ["Europe"],
    "finland": ["Europe"],
    "switzerland": ["Europe"],
    "austria": ["Europe"],
    "belgium": ["Europe"],
    "portugal": ["Europe"],
    "poland": ["Europe"],
    "czech republic": ["Europe"],
    "czechia": ["Europe"],
    "romania": ["Europe"],
    "ukraine": ["Europe"],
    "greece": ["Europe"],
    "ireland": ["Europe"],
    "hungary": ["Europe"],
    "bulgaria": ["Europe"],
    "croatia": ["Europe"],
    "slovakia": ["Europe"],
    "serbia": ["Europe"],
    "turkey": ["Europe"],
    "russia": ["Europe"],
    "nigeria": ["Africa"],
    "kenya": ["Africa"],
    "ghana": ["Africa"],
    "south africa": ["Africa"],
    "ethiopia": ["Africa"],
    "egypt": ["Africa"],
    "tanzania": ["Africa"],
    "uganda": ["Africa"],
    "cameroon": ["Africa"],
    "senegal": ["Africa"],
    "côte d'ivoire": ["Africa"],
    "ivory coast": ["Africa"],
    "rwanda": ["Africa"],
    "zimbabwe": ["Africa"],
    "zambia": ["Africa"],
    "mozambique": ["Africa"],
    "angola": ["Africa"],
    "morocco": ["Africa"],
    "tunisia": ["Africa"],
    "algeria": ["Africa"],
}

# Map normalized skill keyword → Discord skill role name.
# More specific keywords take priority over generic ones.
_SKILL_TO_DISCORD_ROLE: dict[str, str] = {
    # Frontend
    "react": "Frontend",
    "vue": "Frontend",
    "angular": "Frontend",
    "svelte": "Frontend",
    "next.js": "Frontend",
    "nuxt": "Frontend",
    "html": "Frontend",
    "css": "Frontend",
    "sass": "Frontend",
    "tailwind": "Frontend",
    "webpack": "Frontend",
    "vite": "Frontend",
    # Backend
    "node.js": "Backend",
    "express": "Backend",
    "django": "Backend",
    "fastapi": "Backend",
    "flask": "Backend",
    "rails": "Backend",
    "ruby on rails": "Backend",
    "spring": "Backend",
    "laravel": "Backend",
    "graphql": "Backend",
    "postgresql": "Backend",
    "mysql": "Backend",
    "mongodb": "Backend",
    "redis": "Backend",
    "rest api": "Backend",
    # AI / ML
    "machine learning": "AI Engineer",
    "deep learning": "AI Engineer",
    "llm": "AI Engineer",
    "large language model": "AI Engineer",
    "tensorflow": "AI Engineer",
    "pytorch": "AI Engineer",
    "transformers": "AI Engineer",
    "nlp": "AI Engineer",
    "computer vision": "AI Engineer",
    "reinforcement learning": "AI Engineer",
    "langchain": "AI Engineer",
    "openai": "AI Engineer",
    # Data Science
    "data science": "Data Scientist",
    "pandas": "Data Scientist",
    "numpy": "Data Scientist",
    "scikit-learn": "Data Scientist",
    "tableau": "Data Scientist",
    "power bi": "Data Scientist",
    "spark": "Data Scientist",
    "hadoop": "Data Scientist",
    "data analysis": "Data Scientist",
    "statistics": "Data Scientist",
    # Blockchain
    "solidity": "Blockchain",
    "ethereum": "Blockchain",
    "web3": "Blockchain",
    "blockchain": "Blockchain",
    "smart contract": "Blockchain",
    "defi": "Blockchain",
    "nft": "Blockchain",
    "crypto": "Blockchain",
    # Mobile (generic)
    "react native": "Mobile",
    "flutter": "Mobile",
    "xamarin": "Mobile",
    # iOS
    "swift": "iOS",
    "swiftui": "iOS",
    "objective-c": "iOS",
    # Android
    "kotlin": "Android",
    "android studio": "Android",
    # Infra / DevOps
    "kubernetes": "Infra / Devops",
    "docker": "Infra / Devops",
    "aws": "Infra / Devops",
    "gcp": "Infra / Devops",
    "google cloud": "Infra / Devops",
    "azure": "Infra / Devops",
    "terraform": "Infra / Devops",
    "ansible": "Infra / Devops",
    "ci/cd": "Infra / Devops",
    "jenkins": "Infra / Devops",
    "github actions": "Infra / Devops",
    "linux": "Infra / Devops",
    "devops": "Infra / Devops",
    # Design
    "figma": "Designer",
    "sketch": "Designer",
    "adobe xd": "Designer",
    "photoshop": "Designer",
    "illustrator": "Designer",
    "indesign": "Designer",
    "ux design": "Designer",
    "ui design": "Designer",
    "user experience": "Designer",
    # Branding
    "branding": "Branding Specialist",
    "brand identity": "Branding Specialist",
    "brand strategy": "Branding Specialist",
    # Copywriting
    "copywriting": "Copywriter",
    "content writing": "Copywriter",
    "technical writing": "Copywriter",
    # Product Management
    "product management": "Product Manager",
    "product roadmap": "Product Manager",
    "agile": "Product Manager",
    "scrum": "Product Manager",
    "jira": "Product Manager",
    # Logistics
    "logistics": "Logistics Specialist",
    "supply chain": "Logistics Specialist",
    "operations": "Logistics Specialist",
}

# Map normalized CRM role → Discord skill role name.
_CRM_ROLE_TO_DISCORD_ROLE: dict[str, str] = {
    "designer": "Designer",
    "product manager": "Product Manager",
    "data scientist": "Data Scientist",
    "copywriter": "Copywriter",
}


def suggest_technical_discord_roles(
    skills: list[str], primary_roles: list[str]
) -> list[str]:
    """Suggest Discord technical skill roles based on extracted resume data.

    Returns a deduplicated list of canonical Discord role names from
    DISCORD_SKILL_ROLE_NAMES that match the candidate's skills and roles.
    """
    suggestions: list[str] = []
    seen: set[str] = set()

    # Map CRM roles first
    for role in primary_roles:
        discord_role = _CRM_ROLE_TO_DISCORD_ROLE.get(role.strip().casefold())
        if discord_role and discord_role not in seen:
            suggestions.append(discord_role)
            seen.add(discord_role)

    # Map skills via keyword matching
    for skill in skills:
        skill_lower = skill.strip().casefold()
        discord_role = _SKILL_TO_DISCORD_ROLE.get(skill_lower)
        if discord_role and discord_role not in seen:
            suggestions.append(discord_role)
            seen.add(discord_role)
        else:
            # Try partial match for multi-word skills
            for keyword, role_name in _SKILL_TO_DISCORD_ROLE.items():
                if keyword in skill_lower and role_name not in seen:
                    suggestions.append(role_name)
                    seen.add(role_name)
                    break

    return suggestions


def suggest_locality_discord_roles(country: str | None) -> list[str]:
    """Suggest Discord locality roles based on extracted country.

    Returns a list of canonical locality Discord role names that match
    the candidate's country.
    """
    if not country:
        return []
    key = country.strip().casefold()
    return list(_COUNTRY_TO_LOCALITY_ROLES.get(key, []))


# Map known role name variants to canonical Discord skill role names.
_DISCORD_ROLE_CANONICAL_MAP: dict[str, str] = {
    role.casefold(): role for role in DISCORD_SKILL_ROLE_NAMES
}
_DISCORD_ROLE_CANONICAL_MAP.update(
    {
        "fullstack": "Full Stack",
        "full-stack": "Full Stack",
        "infra/devops": "Infra / Devops",
        "devops": "Infra / Devops",
        "dev ops": "Infra / Devops",
    }
)


def _normalize_discord_role_types(values: list[str]) -> list[str]:
    """Normalize and validate discord role types against the canonical list."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = raw.strip().casefold()
        if not key:
            continue
        canonical = _DISCORD_ROLE_CANONICAL_MAP.get(key)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)
    return normalized


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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "discord_role_types",
            _normalize_discord_role_types(self.discord_role_types),
        )


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

    # Let JobRequirements.__post_init__ apply canonicalization + dedupe consistently.
    discord_role_types = _coerce_str_list(data.get("discord_role_types"))

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
