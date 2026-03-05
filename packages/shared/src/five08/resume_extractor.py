"""Shared resume text extraction utilities for candidate fields."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field
from five08.crm_normalization import (
    normalize_city as shared_normalize_city,
    normalize_country as shared_normalize_country,
    normalize_role as shared_normalize_role,
    normalize_roles as shared_normalize_roles,
    normalize_seniority as shared_normalize_seniority,
    normalize_state as shared_normalize_state,
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
    "personal blog",
    "portfolio",
    "portfolio site",
    "my website",
    "web site",
    "homepage",
    "website",
)

SOCIAL_LINK_DOMAINS = {
    "bsky.app",
    "facebook.com",
    "fb.com",
    "fb.me",
    "gitlab.com",
    "huggingface.co",
    "instagram.com",
    "kaggle.com",
    "github.com",
    "linkedin.com",
    "mastodon.social",
    "medium.com",
    "pinterest.com",
    "stackoverflow.com",
    "substack.com",
    "telegram.me",
    "threads.net",
    "tiktok.com",
    "t.me",
    "twitch.tv",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtube-nocookie.com",
}
X_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "compose",
        "explore",
        "hashtag",
        "home",
        "i",
        "intent",
        "messages",
        "notifications",
        "search",
        "settings",
        "share",
        "tos",
        "privacy",
    }
)
INSTAGRAM_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "accounts",
        "direct",
        "explore",
        "p",
        "reel",
        "reels",
        "stories",
        "tv",
    }
)
YOUTUBE_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "feed",
        "playlist",
        "results",
        "shorts",
        "watch",
    }
)
FACEBOOK_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "events",
        "groups",
        "help",
        "login",
        "marketplace",
        "pages",
        "privacy",
        "reel",
        "sharer",
        "share.php",
        "story.php",
        "watch",
    }
)
TELEGRAM_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "addstickers",
        "c",
        "joinchat",
        "login",
        "proxy",
        "s",
        "share",
        "share/url",
    }
)
THREADS_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "explore",
        "login",
        "privacy",
        "tos",
    }
)
GITHUB_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "about",
        "apps",
        "collections",
        "contact",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "settings",
        "sponsors",
        "topics",
        "trending",
    }
)
PINTEREST_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "_tools",
        "about",
        "business",
        "categories",
        "discover",
        "explore",
        "ideas",
        "pin",
        "search",
    }
)
TWITCH_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "directory",
        "downloads",
        "jobs",
        "login",
        "p",
        "payments",
        "search",
        "settings",
        "store",
        "subscriptions",
        "turbo",
        "videos",
    }
)
GITLAB_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "-",
        "admin",
        "api",
        "dashboard",
        "explore",
        "groups",
        "help",
        "projects",
        "search",
        "snippets",
        "users",
    }
)
KAGGLE_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "code",
        "competitions",
        "datasets",
        "discussions",
        "docs",
        "learn",
        "models",
        "organizations",
        "search",
    }
)
HUGGING_FACE_RESERVED_PATH_SEGMENTS = frozenset(
    {
        "blog",
        "collections",
        "datasets",
        "docs",
        "learn",
        "models",
        "organizations",
        "spaces",
        "tasks",
    }
)
KNOWN_MASTODON_HOSTS = frozenset(
    {
        "fosstodon.org",
        "hachyderm.io",
        "mas.to",
        "mastodon.online",
        "mastodon.social",
        "mastodon.world",
        "mstdn.social",
    }
)
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
        "express",
        "passport",
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
MAX_PERSONAL_WEBSITE_PATH_COMPONENTS = 1
TOP_BOTTOM_BIAS_WINDOW = 0.1
MIDDLE_WEBSITE_POSITION_SCALE = 0.55
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
UNKNOWN_FIELD_VALUE_TOKENS = frozenset(
    {
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
        "unspecified",
        "not specified",
        "tbd",
        "remote",
        "remote only",
    }
)
# Backward-compatible internal aliases.
_PLACEHOLDER_NAME_TOKENS = RESUME_NAME_PLACEHOLDER_TOKENS
_NAME_HEADING_TOKENS = RESUME_NAME_HEADING_TOKENS
ROLE_NORMALIZATION_MAP = {
    "developer": "developer",
    "data scientist": "data scientist",
    "program manager": "program manager",
    "product manager": "product manager",
    "designer": "designer",
    "user research": "user research",
    "biz dev": "biz dev",
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
    if normalized.isupper():
        return normalized.title()
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


def _is_unknown_field_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return normalized in UNKNOWN_FIELD_VALUE_TOKENS


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
    if _is_unknown_field_value(value):
        return None
    return shared_normalize_country(value)


def _normalize_state(value: Any) -> str | None:
    if _is_unknown_field_value(value):
        return None
    return shared_normalize_state(value)


def _normalize_city(value: Any) -> str | None:
    if _is_unknown_field_value(value):
        return None
    return shared_normalize_city(value, strip_parenthetical=True)


# Mapping of lowercase country name/abbreviation → ITU country calling code.
_COUNTRY_PHONE_CODES: dict[str, str] = {
    "united states": "1",
    "usa": "1",
    "us": "1",
    "canada": "1",
    "united kingdom": "44",
    "uk": "44",
    "great britain": "44",
    "australia": "61",
    "india": "91",
    "germany": "49",
    "france": "33",
    "brazil": "55",
    "mexico": "52",
    "china": "86",
    "japan": "81",
    "south korea": "82",
    "singapore": "65",
    "philippines": "63",
    "nigeria": "234",
    "ghana": "233",
    "kenya": "254",
    "south africa": "27",
    "pakistan": "92",
    "bangladesh": "880",
    "ukraine": "380",
    "poland": "48",
    "netherlands": "31",
    "spain": "34",
    "italy": "39",
    "sweden": "46",
    "norway": "47",
    "denmark": "45",
    "finland": "358",
    "switzerland": "41",
    "austria": "43",
    "belgium": "32",
    "portugal": "351",
    "russia": "7",
    "turkey": "90",
    "israel": "972",
    "egypt": "20",
    "argentina": "54",
    "colombia": "57",
    "chile": "56",
    "peru": "51",
    "new zealand": "64",
    "ireland": "353",
    "indonesia": "62",
    "malaysia": "60",
    "thailand": "66",
    "vietnam": "84",
    "romania": "40",
    "czech republic": "420",
    "czechia": "420",
    "hungary": "36",
    "greece": "30",
    "bulgaria": "359",
    "serbia": "381",
    "croatia": "385",
}


def _normalize_phone_with_country(phone: Any, country: str | None) -> str | None:
    """Normalize phone and prepend country code if missing and country is known."""
    normalized = _normalize_phone(phone)
    if not normalized or normalized.startswith("+") or not country:
        return normalized
    code = _COUNTRY_PHONE_CODES.get(country.strip().lower())
    if not code:
        return normalized
    return f"+{code}{normalized}"


def _normalize_timezone_offset(value: str) -> str | None:
    return shared_normalize_timezone_offset(value)


def _normalize_timezone(value: Any) -> str | None:
    return shared_normalize_timezone(value)


_COUNTRY_TIMEZONE: dict[str, str] = {
    # Americas
    "colombia": "UTC-05:00",
    "ecuador": "UTC-05:00",
    "peru": "UTC-05:00",
    "venezuela": "UTC-04:00",
    "bolivia": "UTC-04:00",
    "chile": "UTC-04:00",
    "paraguay": "UTC-04:00",
    "argentina": "UTC-03:00",
    "uruguay": "UTC-03:00",
    # Europe
    "united kingdom": "UTC+00:00",
    "uk": "UTC+00:00",
    "ireland": "UTC+00:00",
    "portugal": "UTC+00:00",
    "ghana": "UTC+00:00",
    "senegal": "UTC+00:00",
    "ivory coast": "UTC+00:00",
    "germany": "UTC+01:00",
    "france": "UTC+01:00",
    "netherlands": "UTC+01:00",
    "belgium": "UTC+01:00",
    "spain": "UTC+01:00",
    "italy": "UTC+01:00",
    "sweden": "UTC+01:00",
    "norway": "UTC+01:00",
    "denmark": "UTC+01:00",
    "switzerland": "UTC+01:00",
    "austria": "UTC+01:00",
    "poland": "UTC+01:00",
    "czech republic": "UTC+01:00",
    "czechia": "UTC+01:00",
    "hungary": "UTC+01:00",
    "croatia": "UTC+01:00",
    "serbia": "UTC+01:00",
    "nigeria": "UTC+01:00",
    "cameroon": "UTC+01:00",
    "morocco": "UTC+01:00",
    "algeria": "UTC+01:00",
    "tunisia": "UTC+01:00",
    "romania": "UTC+02:00",
    "bulgaria": "UTC+02:00",
    "ukraine": "UTC+02:00",
    "greece": "UTC+02:00",
    "finland": "UTC+02:00",
    "estonia": "UTC+02:00",
    "latvia": "UTC+02:00",
    "lithuania": "UTC+02:00",
    "south africa": "UTC+02:00",
    "israel": "UTC+02:00",
    "egypt": "UTC+02:00",
    "zimbabwe": "UTC+02:00",
    "zambia": "UTC+02:00",
    "rwanda": "UTC+02:00",
    "kenya": "UTC+03:00",
    "tanzania": "UTC+03:00",
    "ethiopia": "UTC+03:00",
    "uganda": "UTC+03:00",
    "saudi arabia": "UTC+03:00",
    "iraq": "UTC+03:00",
    "turkey": "UTC+03:00",
    "iran": "UTC+03:30",
    "uae": "UTC+04:00",
    "united arab emirates": "UTC+04:00",
    "azerbaijan": "UTC+04:00",
    "georgia": "UTC+04:00",
    "armenia": "UTC+04:00",
    "afghanistan": "UTC+04:30",
    "pakistan": "UTC+05:00",
    "uzbekistan": "UTC+05:00",
    "india": "UTC+05:30",
    "sri lanka": "UTC+05:30",
    "nepal": "UTC+05:45",
    "bangladesh": "UTC+06:00",
    "myanmar": "UTC+06:30",
    "thailand": "UTC+07:00",
    "vietnam": "UTC+07:00",
    "cambodia": "UTC+07:00",
    "laos": "UTC+07:00",
    "china": "UTC+08:00",
    "singapore": "UTC+08:00",
    "malaysia": "UTC+08:00",
    "philippines": "UTC+08:00",
    "taiwan": "UTC+08:00",
    "hong kong": "UTC+08:00",
    "mongolia": "UTC+08:00",
    "japan": "UTC+09:00",
    "south korea": "UTC+09:00",
    "korea": "UTC+09:00",
    "new zealand": "UTC+12:00",
}

# Countries that span multiple timezones — skip rather than guess wrong.
_AMBIGUOUS_COUNTRY_TIMEZONE: frozenset[str] = frozenset(
    {
        "united states",
        "usa",
        "us",
        "canada",
        "mexico",
        "brazil",
        "australia",
        "russia",
        "indonesia",
    }
)

_CITY_TIMEZONE: dict[str, str] = {
    # US Pacific
    "san francisco": "UTC-08:00",
    "los angeles": "UTC-08:00",
    "seattle": "UTC-08:00",
    "portland": "UTC-08:00",
    "san jose": "UTC-08:00",
    "san diego": "UTC-08:00",
    # US Mountain
    "denver": "UTC-07:00",
    "phoenix": "UTC-07:00",
    "salt lake city": "UTC-07:00",
    # US Central
    "chicago": "UTC-06:00",
    "dallas": "UTC-06:00",
    "houston": "UTC-06:00",
    "austin": "UTC-06:00",
    "minneapolis": "UTC-06:00",
    # US Eastern
    "new york": "UTC-05:00",
    "boston": "UTC-05:00",
    "atlanta": "UTC-05:00",
    "miami": "UTC-05:00",
    "washington": "UTC-05:00",
    "philadelphia": "UTC-05:00",
    # Canada
    "toronto": "UTC-05:00",
    "montreal": "UTC-05:00",
    "vancouver": "UTC-08:00",
    "calgary": "UTC-07:00",
    # Mexico
    "mexico city": "UTC-06:00",
    "guadalajara": "UTC-06:00",
    "tijuana": "UTC-08:00",
    # Brazil
    "sao paulo": "UTC-03:00",
    "são paulo": "UTC-03:00",
    "rio de janeiro": "UTC-03:00",
    # Australia
    "sydney": "UTC+10:00",
    "melbourne": "UTC+10:00",
    "brisbane": "UTC+10:00",
    "perth": "UTC+08:00",
    # Russia
    "moscow": "UTC+03:00",
    "saint petersburg": "UTC+03:00",
    "st. petersburg": "UTC+03:00",
}


def _infer_timezone_from_location(
    *, country: str | None, city: str | None = None
) -> str | None:
    """Best-effort UTC offset from city or country (heuristic fallback only)."""
    if city:
        city_tz = _CITY_TIMEZONE.get(city.strip().lower())
        if city_tz:
            return city_tz
    if country:
        country_key = country.strip().lower()
        if country_key in _AMBIGUOUS_COUNTRY_TIMEZONE:
            return None
        tz = _COUNTRY_TIMEZONE.get(country_key)
        if tz:
            return tz
    return None


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


def _has_excessive_personal_path_segments(path: str) -> bool:
    stripped = path.strip("/")
    if not stripped:
        return False
    segments = [part for part in stripped.split("/") if part]
    return len(segments) > MAX_PERSONAL_WEBSITE_PATH_COMPONENTS


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


def _website_position_scale(
    text_length: int, start_index: int, end_index: int
) -> float:
    if text_length <= 0:
        return 1.0

    start_ratio = start_index / text_length
    end_ratio = end_index / text_length
    if start_ratio <= TOP_BOTTOM_BIAS_WINDOW or end_ratio >= 1 - TOP_BOTTOM_BIAS_WINDOW:
        return 1.0
    return MIDDLE_WEBSITE_POSITION_SCALE


def _is_top_or_bottom_position(
    *, text_length: int, start_index: int, end_index: int
) -> bool:
    return (
        _website_position_scale(
            text_length=text_length,
            start_index=start_index,
            end_index=end_index,
        )
        == 1.0
    )


def _normalized_host(host: str | None) -> str:
    if not host:
        return ""
    normalized = host.casefold()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


def _host_matches_domain(host: str | None, domain: str) -> bool:
    normalized_host = _normalized_host(host)
    return normalized_host == domain or normalized_host.endswith(f".{domain}")


def _split_path_segments(path: str) -> list[str]:
    normalized_path = re.sub(r"/+", "/", path or "").strip()
    return [segment for segment in normalized_path.split("/") if segment]


def _is_social_host_url(value: str) -> bool:
    try:
        host = urlsplit(value).hostname
    except Exception:
        return False
    normalized_host = _normalized_host(host)
    if not normalized_host:
        return False
    return any(
        normalized_host == domain or normalized_host.endswith(f".{domain}")
        for domain in SOCIAL_LINK_DOMAINS
    )


def _normalize_social_profile_url(value: str) -> str | None:
    normalized_url = _normalize_website_url(value)
    if not normalized_url:
        return None

    try:
        parsed = urlsplit(normalized_url)
    except Exception:
        return None

    host = _normalized_host(parsed.hostname)
    if not host:
        return None
    path_segments = _split_path_segments(parsed.path or "")

    def _canonical(path: str, *, query: str = "") -> str:
        return parsed._replace(path=path, query=query, fragment="").geturl().rstrip("/")

    if host.endswith(".medium.com") and host != "medium.com":
        if path_segments:
            return None
        return _canonical("/")

    if host.endswith(".substack.com") and host != "substack.com":
        # For Substack, canonicalize to the newsletter root.
        return _canonical("/")

    if not path_segments:
        return None

    first_segment = path_segments[0].casefold()

    if _host_matches_domain(host, "linkedin.com"):
        if len(path_segments) < 2:
            return None
        section = first_segment
        if section not in {"in", "pub"}:
            return None
        slug = path_segments[1].strip()
        if not re.fullmatch(r"[A-Za-z0-9_%-]+", slug):
            return None
        if section == "in":
            return _canonical(f"/in/{slug}")
        pub_path = "/".join(path_segments[1:])
        return _canonical(f"/pub/{pub_path}")

    if _host_matches_domain(host, "github.com"):
        if len(path_segments) != 1:
            return None
        username = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
            return None
        if username.casefold() in GITHUB_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{username}")

    if _host_matches_domain(host, "x.com") or _host_matches_domain(host, "twitter.com"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].lstrip("@").strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
            return None
        if handle.casefold() in X_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "instagram.com"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
            return None
        if handle.casefold() in INSTAGRAM_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "threads.net"):
        if len(path_segments) != 1:
            return None
        raw_handle = path_segments[0].strip()
        if not raw_handle.startswith("@"):
            return None
        handle = raw_handle[1:]
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
            return None
        if handle.casefold() in THREADS_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/@{handle}")

    if _host_matches_domain(host, "tiktok.com"):
        if len(path_segments) != 1:
            return None
        raw_handle = path_segments[0].strip()
        if not raw_handle.startswith("@"):
            return None
        handle = raw_handle[1:]
        if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", handle):
            return None
        return _canonical(f"/@{handle}")

    if _host_matches_domain(host, "youtube.com") or _host_matches_domain(
        host, "youtube-nocookie.com"
    ):
        if len(path_segments) == 1 and path_segments[0].startswith("@"):
            handle = path_segments[0][1:].strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", handle):
                return None
            return _canonical(f"/@{handle}")
        if len(path_segments) == 2 and first_segment in {"c", "channel", "user"}:
            slug = path_segments[1].strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", slug):
                return None
            return _canonical(f"/{path_segments[0]}/{slug}")
        if first_segment in YOUTUBE_RESERVED_PATH_SEGMENTS:
            return None
        return None

    if _host_matches_domain(host, "bsky.app"):
        if len(path_segments) != 2 or first_segment != "profile":
            return None
        handle = path_segments[1].strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", handle):
            return None
        return _canonical(f"/profile/{handle}")

    if _host_matches_domain(host, "telegram.me") or _host_matches_domain(host, "t.me"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", handle):
            return None
        if handle.casefold() in TELEGRAM_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "pinterest.com"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", handle):
            return None
        if handle.casefold() in PINTEREST_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "twitch.tv"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{4,25}", handle):
            return None
        if handle.casefold() in TWITCH_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "gitlab.com"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,255}", handle):
            return None
        if handle.casefold() in GITLAB_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "stackoverflow.com"):
        if first_segment == "users" and len(path_segments) >= 2:
            user_id = path_segments[1].strip()
            if not re.fullmatch(r"\d+", user_id):
                return None
            slug = path_segments[2].strip() if len(path_segments) >= 3 else ""
            if slug and re.fullmatch(r"[A-Za-z0-9-]{1,120}", slug):
                return _canonical(f"/users/{user_id}/{slug}")
            return _canonical(f"/users/{user_id}")
        if first_segment == "u" and len(path_segments) >= 2:
            user_id = path_segments[1].strip()
            if re.fullmatch(r"\d+", user_id):
                return _canonical(f"/u/{user_id}")
        return None

    if _host_matches_domain(host, "kaggle.com"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", handle):
            return None
        if handle.casefold() in KAGGLE_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "huggingface.co"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,96}", handle):
            return None
        if handle.casefold() in HUGGING_FACE_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "medium.com"):
        if len(path_segments) != 1:
            return None
        raw_handle = path_segments[0].strip()
        if not raw_handle.startswith("@"):
            return None
        handle = raw_handle[1:]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", handle):
            return None
        return _canonical(f"/@{handle}")

    if (
        len(path_segments) == 1
        and first_segment.startswith("@")
        and (
            host in KNOWN_MASTODON_HOSTS
            or "mastodon" in host
            or "mstdn" in host
            or "toot" in host
            or "pleroma" in host
        )
    ):
        handle = first_segment[1:].strip()
        if not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}(?:@[A-Za-z0-9.-]+\.[A-Za-z]{2,})?",
            handle,
        ):
            return None
        return _canonical(f"/@{handle}")

    if _host_matches_domain(host, "fb.me"):
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9.]{1,50}", handle):
            return None
        return _canonical(f"/{handle}")

    if _host_matches_domain(host, "facebook.com") or _host_matches_domain(
        host, "fb.com"
    ):
        if first_segment == "profile.php":
            profile_id = parse_qs(parsed.query).get("id", [None])[0]
            if profile_id and re.fullmatch(r"[A-Za-z0-9.]+", profile_id):
                return _canonical("/profile.php", query=f"id={profile_id}")
            return None
        if len(path_segments) != 1:
            return None
        handle = path_segments[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9.]{1,50}", handle):
            return None
        if handle.casefold() in FACEBOOK_RESERVED_PATH_SEGMENTS:
            return None
        return _canonical(f"/{handle}")

    return None


def _build_url_search_variants(url: str) -> tuple[str, ...]:
    try:
        parsed = urlsplit(url)
    except Exception:
        return (url.casefold().rstrip("/"),)

    host = _normalized_host(parsed.hostname)
    path = (parsed.path or "").rstrip("/")
    if not host:
        return (url.casefold().rstrip("/"),)

    host_path = f"{host}{path}".rstrip("/")
    variants = {
        url.casefold().rstrip("/"),
        host_path,
        f"https://{host_path}",
        f"http://{host_path}",
        f"www.{host_path}",
    }
    return tuple(sorted((item for item in variants if item), key=len, reverse=True))


def _is_personal_website_position_acceptable(resume_text: str, url: str) -> bool:
    lowered_text = resume_text.casefold()
    text_length = len(resume_text)
    found = False
    for token in _build_url_search_variants(url):
        start_index = lowered_text.find(token)
        while start_index != -1:
            found = True
            end_index = start_index + len(token)
            if _is_top_or_bottom_position(
                text_length=text_length,
                start_index=start_index,
                end_index=end_index,
            ):
                return True
            if _has_personal_website_context(
                resume_text=resume_text,
                start_index=start_index,
                end_index=end_index,
            ):
                return True
            start_index = lowered_text.find(token, start_index + 1)
    return not found


def _build_website_and_social_from_candidates(
    llm_candidates: list[tuple[str, str, float]],
    heuristic_candidates: list[tuple[str, float]],
    *,
    resume_text: str,
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

        if (
            candidate_kind != LLM_URL_CANDIDATE_KIND_SOCIAL
            and not _is_social_url(candidate_url)
            and not _is_personal_website_position_acceptable(
                resume_text=resume_text,
                url=candidate_url,
            )
        ):
            continue

        candidate_key = candidate_url.casefold()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        urls_to_consider.append(candidate_url)

    for candidate_url, candidate_confidence in heuristic_candidates:
        if candidate_confidence < MIDDLE_WEBSITE_POSITION_SCALE:
            continue

        candidate_key = candidate_url.casefold()
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        urls_to_consider.append(candidate_url)

    return _split_social_and_website_links(urls_to_consider)


def _is_social_url(value: str) -> bool:
    return _normalize_social_profile_url(value) is not None


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
        social_profile = _normalize_social_profile_url(candidate)
        if social_profile:
            social_key = social_profile.casefold()
            if social_key in seen_social:
                continue
            seen_social.add(social_key)
            social_links.append(social_profile)
            continue
        if _is_social_host_url(candidate):
            continue
        if _is_personal_website_disallowed(candidate):
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
    return shared_normalize_seniority(value, empty_as_unknown=False)


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
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized == normalized.upper():
        return normalized.title()
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
    address_state: str | None = None
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
        model: str = "gpt-5-mini",
        max_tokens: int = 800,
        snippet_chars: int = 12000,
    ) -> None:
        self.model = model.strip() if model else "gpt-5-mini"
        if not self.model:
            self.model = "gpt-5-mini"
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
                            "You extract structured candidate profile fields for a CRM. "
                            "Return JSON only with no commentary. "
                            "Prefer explicit evidence from the provided text. "
                            "Be conservative for contact/location/website fields, but proactive for role and seniority inference. "
                            "For primary_roles and seniority_level, infer the best fit from titles, summary, and work history even when labels are not explicit. "
                            "Never fabricate details or use outside knowledge. "
                            "Assume candidates are typically technical professionals unless the resume clearly indicates otherwise."
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
                    resume_text=resume_text,
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
            parsed_city = _normalize_city(parsed.get("address_city"))
            parsed_state = _normalize_state(parsed.get("address_state"))
            parsed_country = _normalize_country(parsed.get("address_country"))
            parsed_timezone = _normalize_timezone(parsed.get("timezone"))
            (
                parsed_city,
                parsed_state,
                parsed_country,
                parsed_timezone,
            ) = self._resolve_location_fields(
                resume_text=resume_text,
                city=parsed_city,
                state=parsed_state,
                country=parsed_country,
                timezone=parsed_timezone,
            )
            return ResumeExtractedProfile(
                name=extracted_name,
                first_name=extracted_first_name,
                last_name=extracted_last_name,
                email=parsed_email,
                additional_emails=parsed_emails,
                description=_normalize_description(parsed.get("description")),
                primary_roles=(
                    _normalize_role_collection(
                        parsed.get("primary_roles") or parsed.get("primary_role")
                    )
                    or self._infer_roles_from_resume(resume_text)
                ),
                github_username=github_username,
                linkedin_url=linkedin_url,
                timezone=parsed_timezone,
                address_city=parsed_city,
                address_state=parsed_state,
                phone=_normalize_phone_with_country(
                    parsed.get("phone"),
                    parsed_country,
                ),
                website_links=parsed_website_links,
                social_links=parsed_social_links,
                address_country=parsed_country,
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

    def _resolve_location_fields(
        self,
        *,
        resume_text: str,
        city: str | None,
        state: str | None,
        country: str | None,
        timezone: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        resolved_city = city
        resolved_state = state
        resolved_country = country
        resolved_timezone = timezone

        explicit_city = self._extract_city(resume_text)
        explicit_state = self._extract_state(resume_text)
        explicit_country = self._extract_country(resume_text)
        explicit_timezone = self._extract_timezone(resume_text)
        header_city, header_state, header_country = self._extract_header_location(
            resume_text
        )

        if not resolved_city:
            resolved_city = explicit_city or header_city
        if not resolved_state:
            resolved_state = explicit_state or header_state
        if not resolved_country:
            resolved_country = explicit_country or header_country
        if not resolved_timezone:
            resolved_timezone = explicit_timezone or _infer_timezone_from_location(
                country=resolved_country,
                city=resolved_city,
            )

        return resolved_city, resolved_state, resolved_country, resolved_timezone

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
        state = self._extract_state(snippet)
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
        header_city, header_state, header_country = self._extract_header_location(
            snippet
        )
        if not city:
            city = header_city
        if not state:
            state = header_state
        if not country:
            country = header_country
        if timezone is None:
            timezone = _infer_timezone_from_location(country=country, city=city)
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
        heuristic_name = _normalize_name(name_match)

        return ResumeExtractedProfile(
            name=heuristic_name,
            first_name=first_name,
            last_name=last_name,
            email=extracted_emails[0] if extracted_emails else None,
            additional_emails=extracted_emails[1:],
            primary_roles=self._extract_roles(snippet),
            timezone=timezone,
            address_city=city,
            address_state=state,
            github_username=github_username,
            linkedin_url=linkedin_url,
            phone=_normalize_phone_with_country(
                phone_match.group(0) if phone_match else None,
                country,
            ),
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
            "{\n"
            '  "name": string|null,\n'
            '  "firstName": string|null,\n'
            '  "lastName": string|null,\n'
            '  "email": string|null,\n'
            '  "additional_emails": string[]|null,\n'
            '  "github_username": string|null,\n'
            '  "linkedin_url": string|null,\n'
            '  "primary_roles": string[]|null,\n'
            '  "timezone": string|null,\n'
            '  "address_city": string|null,\n'
            '  "address_state": string|null,\n'
            '  "description": string|null,\n'
            '  "website_url_candidates": [\n'
            '    {"url": string|null, "kind": "personal_website|social_profile|other", '
            '"confidence": number, "reason": string|null}|null\n'
            "  ],\n"
            '  "phone": string|null,\n'
            '  "website_links": string[]|null,\n'
            '  "social_links": string[]|null,\n'
            '  "address_country": string|null,\n'
            '  "seniority_level": string|null,\n'
            '  "availability": string|null,\n'
            '  "rate_range": string|null,\n'
            '  "referred_by": string|null,\n'
            '  "skills": string[]|null,\n'
            '  "skill_attrs": {"<skill>": {"strength": 1-5}}|null,\n'
            '  "confidence": number\n'
            "}\n"
            "Rules:\n"
            "- name is the full name of the candidate as it appears on the resume\n"
            "- firstName and lastName should be split from name\n"
            "- For each explicit website/social URL-like string in the source, emit website_url_candidates entries\n"
            "- each candidate must include a 0-1 confidence score\n"
            "- kind must be: personal_website, social_profile, or other\n"
            "- treat a candidate as personal_website only when confidence is high (>=0.85)\n"
            "- treat a candidate as social_profile when confidence is high (>=0.7)\n"
            "- route candidate urls to website_links and social_links by type and host-level validation\n"
            '- personal_website candidates should be explicit portfolio/homepage/contact signals (for example: "portfolio", "personal website", "homepage", contact header), not technology/framework mentions\n'
            '- trust the explicit source labels and sections (for example lines like "website:", "portfolio:", "my website", "homepage") when selecting personal_website candidates\n'
            "- section confidence ordering for personal websites: explicit website label > contact/header block > footer > body/project bullets\n"
            "- when evaluating personal website candidates, boost confidence if domain/subdomain/path includes candidate identity tokens (first name, last name, first+last, common username handles from email/github/linkedin)\n"
            "- candidate-owned domains or handles in header/footer are strong personal_website signals; body-only project/company links are weak signals unless explicitly labeled personal\n"
            "- never classify employer/company/product/repository/docs links as personal_website unless the resume explicitly marks them as the candidate's own site\n"
            "- if a token can be either a technology name and a URL, default to excluding it from candidates unless context is clearly personal\n"
            "- website_links/social_links should mirror high-confidence candidates; if website_url_candidates are unavailable, use website_links/social_links and heuristics as fallback\n"
            "- prefer explicit values from header/contact sections\n"
            "- treat website_links as personal or portfolio homepage URLs only\n"
            "- if a URL token looks like a language, framework, or package name (for example: asp.net, next.js, node.js, react.js), never emit it as a website candidate\n"
            "- do not include github.com or linkedin.com profile URLs in website_links\n"
            "- if a URL is a social profile, place it into dedicated profile fields (github_username, linkedin_url) or social_links for cSocialLinks\n"
            "- social_links must be direct profile URLs (for example: linkedin.com/in/<slug>, x.com/<handle>, youtube.com/@<handle>, bsky.app/profile/<handle>, instagram.com/<handle>, tiktok.com/@<handle>, threads.net/@<handle>, pinterest.com/<handle>, twitch.tv/<handle>, gitlab.com/<handle>, stackoverflow.com/users/<id>/<slug>, kaggle.com/<handle>, huggingface.co/<handle>, medium.com/@<handle>, <name>.substack.com) and must exclude company/group/post/feed/watch pages\n"
            "- infer URLs from regex-like patterns in the provided text, including markdown links\n"
            "- when in doubt, omit website URLs (be conservative)\n"
            "- only assign high confidence to PERSONAL website candidates when you can verify the link is a person-owned homepage/portfolio and not a technology reference\n"
            "- for github_username return username only (no URL, no @)\n"
            "- for linkedin_url return full linkedin profile URL when available\n"
            "- infer linkedin_url and website_links from bare domains when scheme is missing\n"
            "- for phone return digits with country code and leading + (e.g. +15551234567); if no country code in the source, infer it from address_country or address_city (e.g. United States → +1, India → +91, UK → +44)\n"
            "- if timezone is provided, normalize it to UTC offset form like UTC±HH:MM before output\n"
            "- location/timezone evidence order: explicit location/timezone fields or contact header > explicit current role location > deterministic city/country mapping; otherwise null\n"
            "- infer city/state/country only when there is explicit resume evidence or an unambiguous mapping from a known city; if ambiguous, use null\n"
            "- if timezone is null but address_city or address_country is known or can be inferred from the resume, infer the standard UTC offset (e.g., San Francisco/Los Angeles/Seattle → UTC-08:00, Denver → UTC-07:00, Chicago/Dallas/Houston → UTC-06:00, New York/Boston/Atlanta → UTC-05:00, London/Dublin/Lisbon → UTC+00:00, Paris/Berlin/Amsterdam/Rome/Madrid → UTC+01:00, Bucharest/Athens/Kyiv → UTC+02:00, Nairobi/Istanbul → UTC+03:00, UAE/Dubai → UTC+04:00, India/Mumbai/Bangalore → UTC+05:30, Singapore/Shanghai/Beijing → UTC+08:00, Tokyo/Seoul → UTC+09:00, Sydney/Melbourne → UTC+10:00); omit if location is ambiguous\n"
            "- if country is multi-timezone and city is missing or ambiguous, timezone must be null\n"
            "- for location, first check the resume header/contact block; if missing, infer from the most recent job/experience entry (last position/location line)\n"
            "- if multiple locations are listed, pick the current/primary one; if only remote/ambiguous, return null for city/state/country\n"
            "- if location appears only in older roles and no current location is stated, return null for missing location fields\n"
            "- do not infer location from company headquarters unless explicitly stated in the resume\n"
            "- if address_city is known, infer address_state and address_country when not explicitly stated (e.g. San Francisco → California, United States; London → United Kingdom)\n"
            "- if address_state is known, infer address_country when not explicitly stated (e.g. California → United States)\n"
            "- for primary_roles, these are job functions like developer, data scientist, product manager, program manager, designer, user research, biz dev, marketing; prefer known canonical roles when the input matches those; map variants to the closest known role (e.g. 'software developer' → 'developer', 'product management' → 'product manager'); default to 'developer' unless the resume clearly indicates a non-developer role (e.g. designer, marketer, etc.).\n"
            "- primary_roles must not be empty when resume title/history gives a reasonable signal; infer from titles, summary, and responsibilities even if a dedicated role field is missing\n"
            "- if multiple role signals exist, include up to 2 most relevant canonical roles, ordered by strongest evidence\n"
            "- infer seniority_level as one of: junior, midlevel, senior, staff\n"
            "- use explicit job title as the primary signal when present; years of experience and impact signals are secondary\n"
            "  - junior: titles like 'Junior Engineer', 'Associate Engineer', 'Engineer I', intern/internship, bootcamp grad with <1 year post-graduation\n"
            "  - midlevel: titles like 'Software Engineer', 'Engineer II', 'Developer' with no seniority qualifier\n"
            "  - senior: titles like 'Senior Engineer', 'Senior SWE', 'Engineer III', 'Lead Engineer', 'Tech Lead' (IC track)\n"
            "  - staff: titles like 'Staff Engineer', 'Principal Engineer', 'Architect', 'Distinguished Engineer'\n"
            "- seniority_level should almost never be null for professional resumes; infer from title and tenure even when explicit labels are absent\n"
            "- if explicit seniority is absent but title is a generic professional IC title (e.g., Software Engineer, Developer, Full-Stack Engineer), prefer midlevel\n"
            "- only use null for seniority_level when the resume lacks enough professional context (e.g., student-only profile with no role history)\n"
            "- when title is absent or ambiguous, use total professional experience (excluding education and internships):\n"
            "  - <2 years → junior\n"
            "  - 2-4 years → midlevel\n"
            "  - 4-8 years → senior (lower end requires visible ownership or scope signals)\n"
            "  - 8+ years → senior by default; staff with cross-team scope, explicit staff-level title, mentoring/leading senior engineers, or 4+ years as a senior engineer\n"
            "- staff signals: leading technical direction for a product area, defining architecture across teams, mentoring senior or mid-level engineers, or sustained tenure (4+ years) at senior level\n"
            "- when title and years conflict, prefer title (e.g. someone titled 'Senior Engineer' with 3 years → senior)\n"
            "- visible promotions in job history (e.g. Engineer → Senior Engineer at same company) are a strong signal; use the highest level reached\n"
            "- do not default to midlevel for ambiguous cases; if experience is clearly senior-range (5+ years with engineering output) lean senior\n"
            "- for description, produce 1-2 concise sentences that describe the person and their focus areas, based only on explicit resume details; otherwise null\n"
            "- keep description factual and neutral; avoid marketing/sales phrasing\n"
            "- map strengths from 1-5 where available; omit when unknown\n"
            "- return skills as lowercase canonical names with minimal punctuation\n"
            "- canonicalize known variants like ab testing, go to market, react native\n"
            "- never include generic/disallowed skills: code review, debugging, testing, bug tracking, code quality, performance optimization\n"
            "- copy availability, rate_range, and referred_by if they are provided in source text\n"
            "- use null for unknown or ambiguous fields; only seniority_level may be 'unknown'\n"
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
    def _extract_state(resume_text: str) -> str | None:
        match = re.search(
            r"(?im)^(?:address\s*state|state|province)\s*[:\-]\s*(.+)$",
            resume_text,
        )
        if match:
            normalized = _normalize_state(match.group(1))
            if normalized:
                return normalized

        return None

    @staticmethod
    def _extract_header_location(
        resume_text: str,
    ) -> tuple[str | None, str | None, str | None]:
        lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
        for line in lines[:12]:
            lowered = line.casefold()
            if "@" in line or "http" in lowered:
                continue
            if "linkedin" in lowered or "github" in lowered:
                continue
            if "remote" in lowered:
                continue
            if len(line) > 80:
                continue
            for segment in re.split(r"[|•·]", line):
                candidate = re.sub(r"\([^)]*\)", "", segment).strip()
                if not candidate:
                    continue
                match = re.match(
                    r"^(?P<city>[A-Za-z][A-Za-z .'-]{1,}),\s*"
                    r"(?P<region>[A-Za-z][A-Za-z .'-]{1,}|[A-Z]{2})"
                    r"(?:\s+\d{5})?(?:,\s*(?P<country>[A-Za-z][A-Za-z .'-]{1,}))?$",
                    candidate,
                )
                if not match:
                    continue
                city = _normalize_city(match.group("city"))
                region = match.group("region") or ""
                country = match.group("country") or ""
                if not city:
                    continue
                normalized_state = _normalize_state(region)
                normalized_country = _normalize_country(country) if country else None
                if not normalized_country and not normalized_state:
                    normalized_country = _normalize_country(region)
                elif not normalized_country:
                    inferred_country_from_region = _normalize_country(region)
                    if inferred_country_from_region and not re.fullmatch(
                        r"[A-Z]{2}", region.strip()
                    ):
                        normalized_country = inferred_country_from_region
                        normalized_state = None
                if normalized_country:
                    return city, normalized_state, normalized_country
                if normalized_state:
                    return city, normalized_state, None
        return None, None, None

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
        if roles:
            return roles
        return ResumeProfileExtractor._infer_roles_from_resume(resume_text)

    @staticmethod
    def _infer_roles_from_resume(resume_text: str) -> list[str]:
        lower_text = resume_text.lower()
        inferred: list[str] = []

        def _add(role: str) -> None:
            if role not in inferred:
                inferred.append(role)

        if re.search(
            r"\b(product manager|product management|group product manager|gpm)\b",
            lower_text,
        ):
            _add("product manager")
        if re.search(
            r"\b(program manager|technical program manager|tpm)\b",
            lower_text,
        ):
            _add("program manager")
        if re.search(
            r"\b(data scientist|machine learning scientist|ml scientist)\b",
            lower_text,
        ):
            _add("data scientist")
        if re.search(
            r"\b(product designer|ux designer|ui designer|visual designer|designer)\b",
            lower_text,
        ):
            _add("designer")
        if re.search(
            r"\b(user researcher|ux researcher|user research)\b",
            lower_text,
        ):
            _add("user research")
        if re.search(
            r"\b(biz dev|business development|partnerships?)\b",
            lower_text,
        ):
            _add("biz dev")
        if re.search(
            r"\b(marketing|growth marketer|content marketer|demand generation)\b",
            lower_text,
        ):
            _add("marketing")
        if re.search(
            r"\b("
            r"software engineer|swe|developer|programmer|"
            r"backend engineer|frontend engineer|full[- ]?stack engineer|"
            r"web developer|mobile developer|devops engineer|site reliability engineer|"
            r"machine learning engineer|data engineer"
            r")\b",
            lower_text,
        ):
            _add("developer")

        return inferred

    @staticmethod
    def _infer_seniority_from_resume(resume_text: str) -> str | None:
        lower_text = resume_text.lower()

        # Title-first: honor explicit seniority titles before falling back to years.
        if re.search(
            r"\b("
            r"staff(?:\s+\w+){0,2}\s+engineer|"
            r"principal(?:\s+\w+){0,2}\s+engineer|"
            r"distinguished(?:\s+\w+){0,2}\s+engineer|"
            r"architect"
            r")\b",
            lower_text,
        ):
            return "staff"
        if re.search(
            r"\b("
            r"senior(?:\s+\w+){0,2}\s+engineer|"
            r"senior\s+swe|"
            r"sr\.?(?:\s+\w+){0,2}\s+engineer|"
            r"engineer\s+iii|"
            r"lead(?:\s+\w+){0,2}\s+engineer|"
            r"tech\s+lead|"
            r"technical\s+lead"
            r")\b",
            lower_text,
        ):
            return "senior"
        if re.search(
            r"\b("
            r"engineer\s+ii|"
            r"software\s+engineer\s+ii|"
            r"mid(?:-|\s*)level(?:\s+\w+){0,2}\s+engineer|"
            r"intermediate(?:\s+\w+){0,2}\s+engineer"
            r")\b",
            lower_text,
        ):
            return "midlevel"
        if re.search(
            r"\b("
            r"junior(?:\s+\w+){0,2}\s+engineer|"
            r"associate(?:\s+\w+){0,2}\s+engineer|"
            r"engineer\s+i|"
            r"intern(?:ship)?"
            r")\b",
            lower_text,
        ):
            return "junior"
        if re.search(
            r"\b("
            r"software engineer|swe|developer|programmer|"
            r"backend engineer|frontend engineer|full[- ]?stack engineer|"
            r"web developer|mobile developer|devops engineer|"
            r"site reliability engineer|machine learning engineer|data engineer"
            r")\b",
            lower_text,
        ):
            return "midlevel"

        years = ResumeProfileExtractor._extract_years_of_experience(resume_text)
        if years is None:
            return None

        # Staff signals: cross-team scope, mentoring/leading senior engineers,
        # or long tenure at senior level (proxy: senior title + 10+ years total).
        has_staff_signal = bool(
            re.search(
                r"\b(cross-functional|across teams?|multi-team|org(?:anization)?-wide|"
                r"company-wide|product area|platform team|"
                r"mentor(?:ing|ed)?\s+(?:senior|mid(?:level)?)\s+engineers?|"
                r"lead(?:ing)?\s+(?:senior|mid(?:level)?)\s+engineers?)\b",
                lower_text,
            )
        ) or (bool(re.search(r"\bsenior\b", lower_text)) and years >= 10)

        if years >= 8:
            return "staff" if has_staff_signal else "senior"
        if years >= 4:
            return "senior"
        if years >= 2:
            return "midlevel"
        return "junior"

    @staticmethod
    def _extract_years_of_experience(resume_text: str) -> int | None:
        explicit_years = []
        year_patterns = [
            r"(\d{1,2})\+?\s*years?\s+of\s+(?:software\s+|engineering\s+)?experience",
            r"(?:experience|career)\s*(?:\:\s*)?(\d{1,2})\+?\s*years",
            r"over\s+(\d{1,2})\s+years",
        ]
        for pattern in year_patterns:
            for match in re.finditer(pattern, resume_text, flags=re.IGNORECASE):
                try:
                    explicit_years.append(int(match.group(1)))
                except Exception:
                    pass

        # Prefer explicit statements; only fall back to date ranges if none found.
        # Date ranges include education spans which inflate the max, so we use them
        # only as a last resort and take a conservative estimate (median rather than max).
        if explicit_years:
            return max(explicit_years)

        date_range_pattern = re.compile(
            r"\b((?:19|20)\d{2})\s*-\s*((?:19|20)\d{2}|present|current)\b",
            flags=re.IGNORECASE,
        )
        today_year = datetime.now(timezone.utc).year
        date_years = []
        for match in date_range_pattern.finditer(resume_text):
            start_year = int(match.group(1))
            end_token = match.group(2).lower()
            end_year = (
                today_year if end_token in {"present", "current"} else int(end_token)
            )
            span = max(0, end_year - start_year)
            if span > 0:
                date_years.append(span)

        if not date_years:
            return None
        # Use conservative (lower) median to avoid inflation from education spans.
        date_years.sort()
        return date_years[(len(date_years) - 1) // 2]

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
        matches: list[tuple[str, float, int, int]] = []
        text_length = len(resume_text)
        for match in MARKDOWN_URL_PATTERN.finditer(resume_text):
            raw_url = match.group(1).strip().strip(")]},.;:")
            if not raw_url:
                continue
            confidence = 1.0 * _website_position_scale(
                text_length=text_length,
                start_index=match.start(),
                end_index=match.end(),
            )
            matches.append((raw_url, confidence, match.start(), match.end()))

        for match in SCHEME_URL_PATTERN.finditer(resume_text):
            confidence = 1.0 * _website_position_scale(
                text_length=text_length,
                start_index=match.start(),
                end_index=match.end(),
            )
            matches.append((match.group(0), confidence, match.start(), match.end()))

        for match in BARE_DOMAIN_URL_PATTERN.finditer(resume_text):
            if match.start() > 0 and resume_text[match.start() - 1] == "@":
                continue
            raw_url = match.group(0)
            position_scale = _website_position_scale(
                text_length=len(resume_text),
                start_index=match.start(),
                end_index=match.end(),
            )
            if _has_personal_website_context(
                resume_text,
                match.start(),
                match.end(),
            ):
                confidence = PERSONAL_WEBSITE_CONTEXT_CONFIDENCE
            else:
                confidence = PERSONAL_WEBSITE_MIN_CONFIDENCE * position_scale
            if confidence < PERSONAL_WEBSITE_MIN_CONFIDENCE:
                continue
            matches.append((raw_url, confidence, match.start(), match.end()))

        normalized_links: list[tuple[str, float]] = []
        seen: set[str] = set()
        for raw_link, confidence, start_index, end_index in matches:
            normalized_link = _normalize_website_url(raw_link.strip())
            if not normalized_link:
                continue
            is_social = _is_social_url(normalized_link)
            if not is_social and not _is_top_or_bottom_position(
                text_length=text_length,
                start_index=start_index,
                end_index=end_index,
            ):
                if not _has_personal_website_context(
                    resume_text,
                    start_index,
                    end_index,
                ):
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
