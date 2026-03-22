"""Shared normalization helpers for CRM resume/intake flows."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

ROLE_NORMALIZATION_MAP: dict[str, str] = {
    "developer": "developer",
    "data scientist": "data scientist",
    "program manager": "program manager",
    "product manager": "product manager",
    "designer": "designer",
    "user research": "user research",
    "biz dev": "biz dev",
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

_US_STATE_ABBREVIATIONS: dict[str, str] = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District Of Columbia",
}
_US_STATE_NAMES: dict[str, str] = {
    value.casefold(): value for value in _US_STATE_ABBREVIATIONS.values()
}
_CANADA_PROVINCE_ABBREVIATIONS: dict[str, str] = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland And Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}
_CANADA_PROVINCE_NAMES: dict[str, str] = {
    value.casefold(): value for value in _CANADA_PROVINCE_ABBREVIATIONS.values()
}
_LOCATION_STOPWORDS = frozenset(
    {
        "account",
        "accounts",
        "api",
        "backend",
        "creation",
        "developer",
        "development",
        "django",
        "engineer",
        "engineering",
        "frontend",
        "fullstack",
        "handles",
        "intern",
        "internship",
        "javascript",
        "management",
        "manager",
        "python",
        "react",
        "senior",
        "software",
    }
)
_CANONICAL_COUNTRY_NAMES: tuple[str, ...] = (
    "Afghanistan",
    "Albania",
    "Algeria",
    "Andorra",
    "Angola",
    "Antigua And Barbuda",
    "Argentina",
    "Armenia",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahamas",
    "Bahrain",
    "Bangladesh",
    "Barbados",
    "Belarus",
    "Belgium",
    "Belize",
    "Benin",
    "Bhutan",
    "Bolivia",
    "Bosnia And Herzegovina",
    "Botswana",
    "Brazil",
    "Brunei",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cambodia",
    "Cameroon",
    "Canada",
    "Cape Verde",
    "Central African Republic",
    "Chad",
    "Chile",
    "China",
    "Colombia",
    "Comoros",
    "Congo",
    "Costa Rica",
    "Croatia",
    "Cuba",
    "Cyprus",
    "Czech Republic",
    "Denmark",
    "Djibouti",
    "Dominica",
    "Dominican Republic",
    "Democratic Republic Of The Congo",
    "Ecuador",
    "Egypt",
    "El Salvador",
    "Equatorial Guinea",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Fiji",
    "Finland",
    "France",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Greece",
    "Grenada",
    "Guatemala",
    "Guinea",
    "Guinea-Bissau",
    "Guyana",
    "Haiti",
    "Honduras",
    "Hungary",
    "Iceland",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Ireland",
    "Israel",
    "Italy",
    "Ivory Coast",
    "Jamaica",
    "Japan",
    "Jordan",
    "Kazakhstan",
    "Kenya",
    "Kiribati",
    "Kuwait",
    "Kyrgyzstan",
    "Laos",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Madagascar",
    "Malawi",
    "Malaysia",
    "Maldives",
    "Mali",
    "Malta",
    "Marshall Islands",
    "Mauritania",
    "Mauritius",
    "Mexico",
    "Micronesia",
    "Moldova",
    "Monaco",
    "Mongolia",
    "Montenegro",
    "Morocco",
    "Mozambique",
    "Myanmar",
    "Namibia",
    "Nauru",
    "Nepal",
    "Netherlands",
    "New Zealand",
    "Nicaragua",
    "Niger",
    "Nigeria",
    "North Korea",
    "North Macedonia",
    "Norway",
    "Oman",
    "Pakistan",
    "Palau",
    "Palestine",
    "Panama",
    "Papua New Guinea",
    "Paraguay",
    "Peru",
    "Philippines",
    "Poland",
    "Portugal",
    "Qatar",
    "Romania",
    "Republic Of The Congo",
    "Russia",
    "Rwanda",
    "Saint Kitts And Nevis",
    "Saint Lucia",
    "Saint Vincent And The Grenadines",
    "Samoa",
    "San Marino",
    "Sao Tome And Principe",
    "Saudi Arabia",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Singapore",
    "Slovakia",
    "Slovenia",
    "Solomon Islands",
    "Somalia",
    "South Africa",
    "South Korea",
    "South Sudan",
    "Spain",
    "Sri Lanka",
    "Sudan",
    "Suriname",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Tajikistan",
    "Tanzania",
    "Thailand",
    "Timor-Leste",
    "Togo",
    "Tonga",
    "Trinidad And Tobago",
    "Tunisia",
    "Turkey",
    "Turkmenistan",
    "Tuvalu",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United Kingdom",
    "United States",
    "Uruguay",
    "Uzbekistan",
    "Vanuatu",
    "Vatican City",
    "Venezuela",
    "Vietnam",
    "Yemen",
    "Zambia",
    "Zimbabwe",
)
_RAW_COUNTRY_ALIASES: dict[str, str] = {
    "america": "United States",
    "britain": "United Kingdom",
    "cote d ivoire": "Ivory Coast",
    "cote d'ivoire": "Ivory Coast",
    "congo brazzaville": "Republic Of The Congo",
    "congo drc": "Democratic Republic Of The Congo",
    "congo kinshasa": "Democratic Republic Of The Congo",
    "czechia": "Czech Republic",
    "democratic republic of congo": "Democratic Republic Of The Congo",
    "democratic republic of the congo": "Democratic Republic Of The Congo",
    "england": "United Kingdom",
    "great britain": "United Kingdom",
    "holland": "Netherlands",
    "korea south": "South Korea",
    "korea north": "North Korea",
    "republic of korea": "South Korea",
    "republic of china": "Taiwan",
    "russian federation": "Russia",
    "scotland": "United Kingdom",
    "south korea": "South Korea",
    "taiwan roc": "Taiwan",
    "u k": "United Kingdom",
    "u s": "United States",
    "uae": "United Arab Emirates",
    "uk": "United Kingdom",
    "united states of america": "United States",
    "u s a": "United States",
    "usa": "United States",
    "us": "United States",
    "vatican": "Vatican City",
    "wales": "United Kingdom",
}


def _normalize_location_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,.")


def _location_lookup_key(value: str) -> str:
    normalized = _normalize_location_text(value)
    normalized = normalized.replace("’", "'").replace("‘", "'")
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    ).casefold()
    collapsed = "".join(ch if ch.isalnum() else " " for ch in normalized)
    return re.sub(r"\s+", " ", collapsed).strip()


_COUNTRY_CANONICAL_MAP: dict[str, str] = {
    _location_lookup_key(value): value for value in _CANONICAL_COUNTRY_NAMES
}
_COUNTRY_ALIASES: dict[str, str] = {
    _location_lookup_key(key): value for key, value in _RAW_COUNTRY_ALIASES.items()
}


def _is_plausible_location_phrase(
    value: str,
    *,
    max_words: int,
    max_length: int,
) -> bool:
    if not value or len(value) > max_length:
        return False
    tokens = [
        token.casefold()
        for token in re.findall(r"[^\W\d_][^\W\d_'-]*", value, flags=re.UNICODE)
    ]
    if not tokens or len(tokens) > max_words:
        return False
    if any(token in _LOCATION_STOPWORDS for token in tokens):
        return False
    for ch in value:
        if ch.isalpha() or ch in {" ", "-", "'", ".", "(", ")"}:
            continue
        return False
    return True


def _title_case_location(value: str) -> str:
    return " ".join(part.strip().title() for part in value.split())


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
    normalized = _normalize_location_text(value)
    if not normalized:
        return None
    lookup_key = _location_lookup_key(normalized)
    if not lookup_key:
        return None

    alias = _COUNTRY_ALIASES.get(lookup_key)
    if alias:
        return alias

    return _COUNTRY_CANONICAL_MAP.get(lookup_key)


def normalize_state(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact_abbreviation = re.sub(r"[^A-Za-z]", "", value).upper()
    if compact_abbreviation:
        us_state = _US_STATE_ABBREVIATIONS.get(compact_abbreviation)
        if us_state:
            return us_state
        canada_province = _CANADA_PROVINCE_ABBREVIATIONS.get(compact_abbreviation)
        if canada_province:
            return canada_province
    normalized = _normalize_location_text(value)
    if not normalized:
        return None

    us_state = _US_STATE_ABBREVIATIONS.get(normalized.upper())
    if us_state:
        return us_state

    canonical_us_state = _US_STATE_NAMES.get(normalized.casefold())
    if canonical_us_state:
        return canonical_us_state

    canada_province = _CANADA_PROVINCE_ABBREVIATIONS.get(normalized.upper())
    if canada_province:
        return canada_province

    canonical_canada_province = _CANADA_PROVINCE_NAMES.get(normalized.casefold())
    if canonical_canada_province:
        return canonical_canada_province

    if not _is_plausible_location_phrase(normalized, max_words=4, max_length=40):
        return None

    letter_count = sum(1 for ch in normalized if ch.isalpha())
    if letter_count <= 2:
        return None

    return _title_case_location(normalized)


def normalize_city(value: Any, *, strip_parenthetical: bool = False) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _normalize_location_text(value)
    if not normalized:
        return None
    if strip_parenthetical:
        normalized = normalized.split("(")[0].strip()
    normalized = normalized.split(",")[0].strip()
    if not normalized:
        return None
    if not _is_plausible_location_phrase(normalized, max_words=4, max_length=40):
        return None
    letter_count = sum(1 for ch in normalized if ch.isalpha())
    if letter_count <= 2:
        return None
    return _title_case_location(normalized)


_COUNTRY_TIMEZONE: dict[str, str] = {
    "australia": "UTC+10:00",
    "china": "UTC+08:00",
    "france": "UTC+01:00",
    "germany": "UTC+01:00",
    "india": "UTC+05:30",
    "japan": "UTC+09:00",
    "kenya": "UTC+03:00",
    "nepal": "UTC+05:45",
    "singapore": "UTC+08:00",
    "south korea": "UTC+09:00",
    "spain": "UTC+01:00",
    "taiwan": "UTC+08:00",
    "ukraine": "UTC+02:00",
    "united kingdom": "UTC+00:00",
    "united states": None,  # type: ignore[dict-item]
}
_STATE_TIMEZONE: dict[str, str] = {
    "california": "UTC-08:00",
    "texas": "UTC-06:00",
    "new york": "UTC-05:00",
    "north carolina": "UTC-05:00",
    "washington": "UTC-08:00",
}
_CITY_TIMEZONE: dict[str, str] = {
    "berlin": "UTC+01:00",
    "beijing": "UTC+08:00",
    "boston": "UTC-05:00",
    "chicago": "UTC-06:00",
    "dallas": "UTC-06:00",
    "denver": "UTC-07:00",
    "dubai": "UTC+04:00",
    "garland": "UTC-06:00",
    "houston": "UTC-06:00",
    "london": "UTC+00:00",
    "madrid": "UTC+01:00",
    "melbourne": "UTC+10:00",
    "mumbai": "UTC+05:30",
    "nairobi": "UTC+03:00",
    "new york": "UTC-05:00",
    "paris": "UTC+01:00",
    "san francisco": "UTC-08:00",
    "seattle": "UTC-08:00",
    "seoul": "UTC+09:00",
    "singapore": "UTC+08:00",
    "sydney": "UTC+10:00",
    "tokyo": "UTC+09:00",
}
_AMBIGUOUS_COUNTRY_TIMEZONE = frozenset({"canada", "united states"})


def infer_timezone_from_location(
    *, country: str | None, state: str | None = None, city: str | None = None
) -> str | None:
    """Best-effort UTC offset from normalized city/state/country values."""
    if city:
        city_tz = _CITY_TIMEZONE.get(city.strip().lower())
        if city_tz:
            return city_tz
    if state:
        state_tz = _STATE_TIMEZONE.get(state.strip().lower())
        if state_tz:
            return state_tz
    if country:
        country_key = country.strip().lower()
        if country_key in _AMBIGUOUS_COUNTRY_TIMEZONE:
            return None
        return _COUNTRY_TIMEZONE.get(country_key)
    return None


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


def format_seniority_label(
    value: Any, *, default: str | None = "Unknown"
) -> str | None:
    """Return a human-friendly seniority label from a raw or normalized value."""
    if value is None:
        return default
    normalized = str(value).strip().lower().replace("_", "-")
    if not normalized:
        return default
    labels = {
        "junior": "Junior",
        "midlevel": "Mid-level",
        "mid-level": "Mid-level",
        "senior": "Senior",
        "staff": "Staff",
        "unknown": "Unknown",
    }
    if normalized in labels:
        return labels[normalized]
    return normalized.title()


def normalize_role(value: Any, role_map: dict[str, str] | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    # Normalize separators to spaces for consistent matching
    normalized = re.sub(r"[-_]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None
    effective_map = role_map or ROLE_NORMALIZATION_MAP
    # Exact match first
    mapped = effective_map.get(normalized)
    if mapped is not None:
        return mapped
    # Bias towards known roles: substring match (longer keys checked first)
    for known_key in sorted(effective_map, key=len, reverse=True):
        if known_key in normalized:
            return effective_map[known_key]
    # Fallback: lowercase space-separated, alphanumeric only
    result = "".join(ch for ch in normalized if ch.isalnum() or ch == " ")
    return result.strip() or None


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


def normalized_website_identity_key(normalized_url: str) -> str | None:
    try:
        parsed = urlsplit(normalized_url)
    except Exception:
        return normalized_url.casefold()

    netloc = parsed.netloc.casefold()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path or "").rstrip("/").casefold()
    query = parsed.query.casefold()
    key = f"{netloc}{path}"
    if query:
        key = f"{key}?{query}"
    return key


def website_identity_key(
    value: str,
    *,
    allow_scheme_less: bool = True,
    disallowed_host_predicate: Callable[[str], bool] | None = None,
) -> str | None:
    normalized = normalize_website_url(
        value,
        allow_scheme_less=allow_scheme_less,
        disallowed_host_predicate=disallowed_host_predicate,
    )
    if normalized is None:
        return None
    return normalized_website_identity_key(normalized)
