"""Shared normalization helpers for CRM resume/intake flows."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

ROLE_NORMALIZATION_MAP: dict[str, str] = {
    "developer": "developer",
    "data scientist": "data_scientist",
    "program manager": "program_manager",
    "product manager": "product_manager",
    "designer": "designer",
    "user research": "user_research",
    "biz dev": "biz_dev",
    "marketing": "marketing",
}

SENIORITY_MAP: dict[str, str] = {
    "junior": "junior",
    "mid-level": "midlevel",
    "midlevel": "midlevel",
    "senior": "senior",
    "principal": "staff",
    "principal engineer": "staff",
    "staff": "staff",
    "staff and beyond": "staff",
    "staff+": "staff",
}


def normalize_timezone_offset(value: str) -> str | None:
    raw = value.strip().replace(" ", "")
    if not raw:
        return None

    if raw.lower() in {"utc", "gmt"}:
        return "UTC+00:00"

    raw = re.sub(r"(?i)\b(?:utc|gmt)\b", "", raw).strip()
    if not raw:
        return "UTC+00:00"

    match = re.match(r"([+-])\s*(\d{1,2})(?:[:.]([0-9]{1,2}))?$", raw)
    if match is None:
        return None

    sign = match.group(1)
    try:
        hours = int(match.group(2))
    except Exception:
        return None
    if not 0 <= hours <= 14:
        return None

    minutes = match.group(3)
    if minutes is None:
        minutes_value = 0
    else:
        try:
            minutes_value = int(minutes)
        except Exception:
            return None
        if minutes_value > 59:
            return None

    return f"UTC{sign}{hours:02d}:{minutes_value:02d}"


def normalize_timezone(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None

    patterns = [
        r"(?im)^(?:timezone|time\s*zone|tz|utc|gmt)\s*[:\-]\s*(.+)$",
        r"(?i)\b(?:utc|gmt)\s*([+-]\s*\d{1,2}(?:[:.]\d{1,2})?)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            normalized = normalize_timezone_offset(match.group(1))
            if normalized:
                return normalized

    return normalize_timezone_offset(raw)


def normalize_country(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized.title() if normalized else None


def normalize_city(value: Any, *, strip_parenthetical: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if strip_parenthetical:
        normalized = normalized.split("(")[0].strip()
    normalized = normalized.split(",")[0].strip()
    if not normalized:
        return None
    return " ".join(part.strip().title() for part in normalized.split())


def normalize_seniority(value: Any, *, empty_as_unknown: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("_", "-")
    if not normalized:
        return "unknown" if empty_as_unknown else None
    if normalized in {
        "jr",
        "junior",
        "intern",
        "internship",
        "entry",
        "entry-level",
        "entry level",
    }:
        return "junior"
    if normalized in {"mid", "mid-level", "midlevel", "intermediate"}:
        return "midlevel"
    if normalized in {
        "senior",
        "sr",
        "sr. engineer",
        "senior engineer",
        "lead",
        "lead engineer",
        "lead engineer/tech lead",
        "tech lead",
    }:
        return "senior"
    if normalized in {
        "staff",
        "staff+",
        "staff and beyond",
        "principal",
        "principal engineer",
    }:
        return "staff"
    if "staff" in normalized:
        return "staff"
    if "senior" in normalized:
        return "senior"
    if "mid" in normalized:
        return "midlevel"
    if "junior" in normalized:
        return "junior"
    if "lead " in normalized and "engineer" in normalized:
        return "senior"
    if normalized.startswith("lead "):
        return "senior"
    return "unknown"


def normalize_role(value: Any, role_map: dict[str, str] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    mapped = (role_map or ROLE_NORMALIZATION_MAP).get(normalized)
    if mapped is not None:
        return mapped
    normalized = "_".join(normalized.split())
    normalized = "".join(ch for ch in normalized if ch.isalnum() or ch in {"_", "-"})
    return normalized or None


def normalize_roles(value: Any, role_map: dict[str, str] | None = None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = [item.strip() for item in re.split(r"[,\n;]+", value)]
    elif isinstance(value, (list, tuple, set)):
        raw_values = [item.strip() for item in value if isinstance(item, str)]
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        normalized_value = normalize_role(raw_value, role_map)
        if not normalized_value or normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized.append(normalized_value)
    return normalized


def normalize_website_url(
    value: str,
    *,
    allow_scheme_less: bool = True,
    disallowed_host_predicate: Callable[[str], bool] | None = None,
) -> str | None:
    candidate = unicodedata.normalize("NFKC", value)
    # Strip Unicode format characters (e.g. zero-width spaces) before ASCII check.
    candidate = "".join(ch for ch in candidate if unicodedata.category(ch) != "Cf")
    if any(ord(ch) > 127 for ch in candidate):
        return None
    candidate = candidate.strip().strip(")]},.;:")
    if not candidate:
        return None

    lower_candidate = candidate.lower()
    if lower_candidate.startswith("www."):
        candidate = f"https://{candidate}"
    elif not lower_candidate.startswith(("http://", "https://")):
        if not allow_scheme_less:
            return None
        if not re.match(
            r"(?i)^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?:[/?#].*)?$",
            candidate,
        ):
            return None
        candidate = f"https://{candidate}"

    try:
        parsed = urlsplit(candidate)
    except Exception:
        return None

    if "@" in parsed.netloc:
        return None

    host = parsed.hostname or ""
    if host.lower().startswith("www."):
        host = host[4:]
    if not host:
        return None

    if disallowed_host_predicate and disallowed_host_predicate(host):
        return None

    normalized_netloc = parsed.netloc
    lower_netloc = parsed.netloc.lower()
    if lower_netloc.startswith("www."):
        normalized_netloc = parsed.netloc[4:]
    elif host and lower_netloc.startswith(f"www.{host}"):
        normalized_netloc = parsed.netloc.replace(parsed.netloc[:4], "", 1)

    parsed = parsed._replace(netloc=normalized_netloc)
    normalized = parsed.geturl().rstrip("/")
    if normalized.startswith("https://www."):
        normalized = normalized.replace("https://www.", "https://", 1)
    elif normalized.startswith("http://www."):
        normalized = normalized.replace("http://www.", "http://", 1)
    return normalized
