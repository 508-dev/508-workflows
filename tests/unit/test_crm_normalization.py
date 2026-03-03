"""Unit tests for shared CRM normalization helpers."""

from five08.crm_normalization import (
    normalize_city,
    normalize_country,
    normalize_role,
    normalize_roles,
    normalize_seniority,
    normalize_timezone,
    normalize_website_url,
)


def test_normalize_timezone_parses_utc_offsets() -> None:
    assert normalize_timezone("UTC+5:30") == "UTC+05:30"
    assert normalize_timezone("gmt-4") == "UTC-04:00"
    assert normalize_timezone("+09") == "UTC+09:00"
    assert normalize_timezone("America/Los_Angeles") is None


def test_normalize_role_and_roles_dedupe() -> None:
    role_map = {"biz dev": "biz dev"}
    assert normalize_role(" Biz Dev ", role_map) == "biz dev"
    assert normalize_role("Staff Engineering", role_map) == "staff engineering"
    assert normalize_roles(
        "Developer, Biz Dev, developer, Staff Engineering", role_map
    ) == ["developer", "biz dev", "staff engineering"]


def test_normalize_website_url_scheme_less_and_cleanup() -> None:
    assert normalize_website_url("www.Example.com/path/") == "https://Example.com/path"
    assert (
        normalize_website_url("portfolio.example.com")
        == "https://portfolio.example.com"
    )
    assert normalize_website_url("mailto:test@example.com") is None


def test_normalize_website_url_respects_disallowed_host_predicate() -> None:
    assert (
        normalize_website_url(
            "node.js",
            disallowed_host_predicate=lambda host: host.casefold() == "node.js",
        )
        is None
    )


def test_normalize_country_and_city() -> None:
    assert normalize_country(" united states ") == "United States"
    assert normalize_city("  new york, ny  ") == "New York"
    assert (
        normalize_city("San Francisco (Bay Area)", strip_parenthetical=True)
        == "San Francisco"
    )


def test_normalize_seniority_modes() -> None:
    assert normalize_seniority("principal engineer") == "staff"
    assert normalize_seniority("senior engineer") == "senior"
    assert normalize_seniority("  ", empty_as_unknown=False) is None
    assert normalize_seniority("  ", empty_as_unknown=True) == "unknown"


def test_normalize_roles_skips_non_string_items() -> None:
    assert normalize_roles(["Developer", None, 42, " Biz Dev "]) == [
        "developer",
        "biz dev",
    ]
