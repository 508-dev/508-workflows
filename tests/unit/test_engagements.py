"""Unit tests for local engagement helpers."""

from __future__ import annotations

from contextlib import contextmanager

import five08.engagements as engagements
from five08.engagements import (
    DiscordEngagementInput,
    EngagementStatus,
    normalize_engagement_status,
    parse_status_from_title,
    strip_status_from_title,
    upsert_discord_engagement,
)
from five08.settings import SharedSettings


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


def test_upsert_discord_engagement_can_preserve_existing_status(monkeypatch) -> None:
    executed: list[tuple[str, tuple]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str]:
            return {"id": "engagement-1"}

    class ConnectionStub:
        def cursor(self, row_factory=None) -> CursorStub:  # noqa: ARG002
            return CursorStub()

        def __enter__(self) -> "ConnectionStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

    @contextmanager
    def connection_stub():
        yield ConnectionStub()

    monkeypatch.setattr(
        engagements,
        "get_postgres_connection",
        lambda _settings: connection_stub(),
    )

    engagement_id = upsert_discord_engagement(
        SharedSettings(),
        DiscordEngagementInput(
            guild_id="guild-1",
            channel_id="channel-1",
            message_id="message-1",
            thread_id="thread-1",
            posted_by_discord_user_id="poster-1",
            title="Untitled",
            status=EngagementStatus.UNKNOWN,
            preserve_existing_status=True,
        ),
    )

    assert engagement_id == "engagement-1"
    query, params = executed[0]
    assert "WHEN %s THEN engagements.status" in query
    assert (
        "WHEN NOT %s AND engagements.status IS DISTINCT FROM EXCLUDED.status" in query
    )
    assert params[-2:] == (True, True)
