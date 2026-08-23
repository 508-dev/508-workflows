"""Focused tests for onboarding registry normalization and ranking."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from five08 import onboarding


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jane", "jane"),
        ("Jane@508.dev", "jane"),
        (" jane ", "jane"),
        ("jane@example.com", None),
        ("", None),
    ],
)
def test_normalize_onboarder_username(raw: str, expected: str | None) -> None:
    assert onboarding.normalize_onboarder_username(raw) == expected


def test_validate_timezone_rejects_unknown_zone() -> None:
    with pytest.raises(ValueError, match="invalid_timezone"):
        onboarding.validate_timezone("Mars/Olympus")


def test_suggestions_balance_load_before_timezone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        onboarding,
        "list_onboarding_volunteers",
        lambda _settings: [
            {
                "username": "busy",
                "name": "Busy",
                "timezone": "Asia/Tokyo",
                "availability": "available",
                "paused_until": None,
                "max_active_assignments": None,
                "last_assigned_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "active_assignments": 2,
                "successful_onboardings": 4,
            },
            {
                "username": "fresh",
                "name": "Fresh",
                "timezone": "America/New_York",
                "availability": "available",
                "paused_until": None,
                "max_active_assignments": None,
                "last_assigned_at": None,
                "active_assignments": 0,
                "successful_onboardings": 1,
            },
            {
                "username": "paused",
                "name": "Paused",
                "timezone": "Asia/Tokyo",
                "availability": "paused",
                "paused_until": None,
                "max_active_assignments": None,
                "last_assigned_at": None,
                "active_assignments": 0,
                "successful_onboardings": 0,
            },
        ],
    )

    suggestions = onboarding.suggested_onboarders(
        object(),  # type: ignore[arg-type]
        candidate_timezone="Asia/Tokyo",
    )

    assert [item["username"] for item in suggestions] == ["fresh", "busy"]
    assert suggestions[0]["timezone_distance_hours"] > 0
