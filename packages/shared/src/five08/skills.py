"""Skill normalization helpers shared across bot and worker services."""

from __future__ import annotations

import re

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
