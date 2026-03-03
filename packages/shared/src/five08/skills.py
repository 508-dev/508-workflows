"""Skill normalization helpers shared across bot and worker services."""

from __future__ import annotations

import re
from typing import Any

# Canonicalization map tuned for Discord-friendly search terms and CRM consistency.
SKILL_ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "node.js": "node",
    "nodejs": "node",
    "node js": "node",
    "golang": "go",
    "py": "python",
    "postgres": "postgresql",
    "k8s": "kubernetes",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "aws": "amazon web services",
    "g suite": "google workspace",
    "ab testing": "ab testing",
    "a/b testing": "ab testing",
    "a b testing": "ab testing",
    "experimentation": "ab testing",
    "product mgmt": "product management",
    "product manager": "product management",
    "pm": "product management",
    "gtm": "go to market",
    "go-to-market": "go to market",
    "go to market": "go to market",
    "seo": "search engine optimization",
    "sem": "search engine marketing",
    "crm": "customer relationship management",
    "ga4": "google analytics",
    "google analytics 4": "google analytics",
}

DISALLOWED_RESUME_SKILLS: frozenset[str] = frozenset(
    {
        "code review",
        "debugging",
        "performance optimization",
        "testing",
        "code quality",
        "bug tracking",
        "bugtracking",
        "bug-tracking",
    }
)

_INLINE_STRENGTH_PATTERN = re.compile(r"^(.*)\(\s*(\d*)\s*\)\s*$")


def normalize_skill(value: str) -> str:
    """Normalize one skill string into a canonical, punctuation-light form."""
    normalized = value.strip().lower()
    if not normalized:
        return ""

    normalized = re.sub(r"\s+", " ", normalized).strip(" .,_-:/")
    if normalized in SKILL_ALIASES:
        return SKILL_ALIASES[normalized]

    punctuation_light = re.sub(r"[./_-]+", " ", normalized)
    punctuation_light = re.sub(r"\s+", " ", punctuation_light).strip()
    if punctuation_light in SKILL_ALIASES:
        return SKILL_ALIASES[punctuation_light]

    return punctuation_light or normalized


def normalize_skill_list(values: list[str]) -> list[str]:
    """Normalize and de-duplicate skills while preserving first-seen order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        skill = normalize_skill(raw)
        if not skill:
            continue
        key = skill.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(skill)
    return normalized


def normalize_strength(value: Any) -> int | None:
    """Normalize optional strength values into the [1, 5] range."""
    raw: Any = value
    if isinstance(raw, dict):
        raw = raw.get("strength")
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        numeric = int(float(raw))
    except Exception:
        return None
    if not 1 <= numeric <= 5:
        return None
    return numeric


def parse_skill_with_strength(value: str) -> tuple[str, int | None]:
    """Parse one skill token, allowing optional inline `(strength)` suffixes."""
    raw = value.strip()
    match = _INLINE_STRENGTH_PATTERN.match(raw)
    if match is None:
        return normalize_skill(raw), None

    base = normalize_skill(match.group(1).strip())
    if not base:
        return "", None
    return base, normalize_strength(match.group(2))


def normalize_skill_payload(
    skills_value: Any,
    skill_attrs_value: Any,
    *,
    disallowed: set[str] | frozenset[str] | None = None,
) -> tuple[list[str], dict[str, int]]:
    """Normalize skills + optional strengths into one canonical payload."""
    blocked = {item.casefold() for item in (disallowed or set())}

    if isinstance(skills_value, str):
        raw_skills = [
            item.strip()
            for item in skills_value.replace(";", ",").split(",")
            if item.strip()
        ]
    elif isinstance(skills_value, (list, tuple, set)):
        raw_skills = [str(item).strip() for item in skills_value if str(item).strip()]
    else:
        raw_skills = []

    normalized_skills: list[str] = []
    seen: set[str] = set()
    attrs: dict[str, int] = {}

    for raw_skill in raw_skills:
        skill, strength = parse_skill_with_strength(raw_skill)
        if not skill:
            continue
        key = skill.casefold()
        if key in blocked:
            continue
        if key not in seen:
            seen.add(key)
            normalized_skills.append(skill)
        if strength is not None:
            attrs[key] = max(attrs.get(key, 0), strength)

    if isinstance(skill_attrs_value, dict):
        for raw_skill, raw_payload in skill_attrs_value.items():
            skill = normalize_skill(str(raw_skill))
            if not skill:
                continue
            key = skill.casefold()
            if key in blocked:
                continue
            strength = normalize_strength(raw_payload)
            if strength is None:
                continue
            attrs[key] = max(attrs.get(key, 0), strength)
            if key not in seen:
                seen.add(key)
                normalized_skills.append(skill)

    return normalized_skills, {
        skill: attrs[skill.casefold()]
        for skill in normalized_skills
        if attrs.get(skill.casefold(), 0) > 0
    }
