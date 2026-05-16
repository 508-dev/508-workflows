"""Unit tests for local engagement helpers."""

from __future__ import annotations

from five08.engagements import (
    EngagementStatus,
    normalize_engagement_status,
    parse_status_from_title,
    strip_status_from_title,
)


def test_parse_status_from_bracketed_gig_title() -> None:
    assert (
        parse_status_from_title("[RECRUITING] Senior Webflow Build")
        is EngagementStatus.RECRUITING
    )
    assert parse_status_from_title("(FILLED) CRM cleanup") is EngagementStatus.FILLED
    assert parse_status_from_title("[OUTDATED] Old lead") is EngagementStatus.OUTDATED
    assert parse_status_from_title("[LOST] Not moving forward") is EngagementStatus.LOST


def test_parse_status_defaults_unknown_for_unmarked_titles() -> None:
    assert parse_status_from_title("Need a backend person") is EngagementStatus.UNKNOWN
    assert normalize_engagement_status("cancelled") is EngagementStatus.LOST


def test_strip_status_from_title_removes_visible_marker() -> None:
    assert (
        strip_status_from_title("[RECRUITING] Senior Webflow Build")
        == "Senior Webflow Build"
    )
    assert strip_status_from_title("Need a backend person") == "Need a backend person"
