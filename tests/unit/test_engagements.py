"""Unit tests for local engagement helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import five08.engagements as engagements
from five08.engagements import (
    DiscordEngagementInput,
    EngagementStatus,
    add_crm_application_to_engagement,
    engagement_event_exists,
    get_gig_thread_interest_backfill_marker,
    list_dashboard_engagements,
    list_dashboard_notifications,
    list_due_status_reminders,
    normalize_engagement_status,
    parse_status_from_title,
    strip_status_from_title,
    upsert_discord_interest_application,
    upsert_suggested_applications,
    upsert_discord_engagement,
    update_engagement_status_by_discord_thread,
    upsert_gig_thread_interest_backfill_marker,
)
from five08.settings import SharedSettings


def test_parse_status_from_bracketed_gig_title() -> None:
    assert parse_status_from_title("[LEAD] Sourced contractor") is EngagementStatus.LEAD
    assert (
        parse_status_from_title("[RECRUITING] Senior Webflow Build")
        is EngagementStatus.RECRUITING
    )
    assert (
        parse_status_from_title("[CONTACTED] Senior Webflow Build")
        is EngagementStatus.CONTACTED
    )
    assert parse_status_from_title("(FILLED) CRM cleanup") is EngagementStatus.FILLED
    assert parse_status_from_title("[OUTDATED] Old lead") is EngagementStatus.OUTDATED
    assert parse_status_from_title("[LOST] Not moving forward") is EngagementStatus.LOST


def test_parse_status_defaults_unknown_for_unmarked_titles() -> None:
    assert parse_status_from_title("Need a backend person") is EngagementStatus.UNKNOWN
    assert parse_status_from_title("[REMOTE] Backend role") is EngagementStatus.UNKNOWN
    assert (
        parse_status_from_title("LEAD Data Engineer | Remote")
        is EngagementStatus.UNKNOWN
    )
    assert (
        parse_status_from_title("RECRUITING Senior Webflow Build")
        is EngagementStatus.RECRUITING
    )
    assert normalize_engagement_status("potential lead") is EngagementStatus.LEAD
    assert normalize_engagement_status("cancelled") is EngagementStatus.LOST


def test_unavailable_is_supported_gig_application_status() -> None:
    assert engagements.EngagementApplicationStatus("unavailable").value == "unavailable"


def test_strip_status_from_title_removes_visible_marker() -> None:
    assert (
        strip_status_from_title("[RECRUITING] Senior Webflow Build")
        == "Senior Webflow Build"
    )
    assert strip_status_from_title("[LEAD] Sourced contractor") == "Sourced contractor"
    assert (
        strip_status_from_title("LEAD Data Engineer | Remote")
        == "LEAD Data Engineer | Remote"
    )
    assert strip_status_from_title("Need a backend person") == "Need a backend person"
    assert strip_status_from_title("[URGENT] Webflow build") == "[URGENT] Webflow build"


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
            refresh_activity=False,
        ),
    )

    assert engagement_id == "engagement-1"
    query, params = executed[0]
    assert "COALESCE(%s, NOW()), COALESCE(%s, NOW())" in query
    assert "WHEN %s THEN engagements.status" in query
    assert "WHEN EXCLUDED.status = 'unknown' THEN engagements.status" in query
    assert "WHEN %s THEN NOW()" in query
    assert (
        "EXCLUDED.status <> 'unknown'\n"
        "                    AND NOT %s\n"
        "                    AND engagements.status IS DISTINCT FROM EXCLUDED.status"
        in query
    )
    assert params[-4:] == (True, False, True, True)


def test_update_engagement_status_by_discord_thread_resolves_engagement(
    monkeypatch,
) -> None:
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

    updated_calls: list[dict[str, object]] = []

    def update_stub(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        updated_calls.append(kwargs)
        return {"id": kwargs["engagement_id"], "status": kwargs["status"].value}

    monkeypatch.setattr(
        engagements,
        "get_postgres_connection",
        lambda _settings: connection_stub(),
    )
    monkeypatch.setattr(engagements, "update_engagement_status", update_stub)

    result = update_engagement_status_by_discord_thread(
        SharedSettings(),
        discord_thread_id="thread-1",
        status=EngagementStatus.RECRUITING,
        actor_discord_user_id="steering-1",
    )

    assert result == {"id": "engagement-1", "status": "recruiting"}
    assert executed[0][1] == ("thread-1",)
    assert updated_calls == [
        {
            "engagement_id": "engagement-1",
            "status": EngagementStatus.RECRUITING,
            "actor_discord_user_id": "steering-1",
        }
    ]


def test_engagement_event_exists_checks_event_marker(monkeypatch) -> None:
    executed: list[tuple[str, tuple]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, int]:
            return {"exists": 1}

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

    assert engagement_event_exists(
        SharedSettings(),
        engagement_id="engagement-1",
        event_type="gig_thread_interest_backfilled",
    )
    query, params = executed[0]
    assert "FROM engagement_events" in query
    assert params == ("engagement-1", "gig_thread_interest_backfilled")


def test_dashboard_engagements_hide_historical_statuses_by_default(
    monkeypatch,
) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        limit=10,
    )

    assert rows == []
    query, params = executed[0]
    assert (
        "e.status IN ('lead', 'recruiting', 'contacted', 'filled', 'unknown')" in query
    )
    assert "CASE e.status" in query
    assert params == ["poster-1", 10]


def test_dashboard_engagements_can_include_historical_statuses(
    monkeypatch,
) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id=None,
        include_all=True,
        include_historical=True,
        limit=10,
    )

    assert rows == []
    query, params = executed[0]
    assert "e.status IN ('recruiting', 'filled', 'unknown')" not in query
    assert params == [10]


def test_dashboard_engagements_searches_gig_text_tags_and_poster(
    monkeypatch,
) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        query="Web_flow%",
        limit=10,
    )

    assert rows == []
    query, params = executed[0]
    assert "coalesce(e.title, '') || ' '" in query
    assert "coalesce(e.body_raw, '') || ' '" in query
    assert "coalesce(array_to_string(e.required_skills, ' '), '')" in query
    assert "coalesce(e.posted_by_discord_user_id, '')" in query
    assert "coalesce(e.discord_channel_name" not in query
    assert "FROM engagement_applications search_a" not in query
    assert params == [
        "poster-1",
        "%Web\\_flow\\%%",
        "%Web\\_flow\\%%",
        "%Web\\_flow\\%%",
        "%Web\\_flow\\%%",
        10,
    ]


def test_dashboard_engagements_searches_hash_tags_without_hash(
    monkeypatch,
) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        query="#react",
        limit=10,
    )

    assert rows == []
    assert executed[0][1] == [
        "poster-1",
        "%#react%",
        "%react%",
        "%react%",
        "%#react%",
        10,
    ]

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        query="#",
        limit=10,
    )

    assert rows == []
    assert executed[1][1] == ["poster-1", "%#%", "%#%", "%#%", "%#%", 10]


def test_dashboard_engagements_searches_poster_mentions_without_at(
    monkeypatch,
) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        query="@1234",
        limit=10,
    )

    assert rows == []
    assert executed[0][1] == ["poster-1", "%@1234%", "%@1234%", "%@1234%", "%1234%", 10]

    rows = list_dashboard_engagements(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        query="@",
        limit=10,
    )

    assert rows == []
    assert executed[1][1] == ["poster-1", "%@%", "%@%", "%@%", "%@%", 10]


def test_due_recruiting_reminders_exclude_very_old_gigs(monkeypatch) -> None:
    executed: list[tuple[str, tuple]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_due_status_reminders(
        SharedSettings(),
        stale_days=7,
        contacted_reminder_days=5,
        max_age_days=90,
        limit=5,
    )

    assert rows == []
    query, params = executed[0]
    assert "COALESCE(e.posted_at, e.created_at)" in query
    assert "e.status = 'contacted'" in query
    assert params == (7, 5, 90, 5, 7, 5)


def test_dashboard_notifications_exclude_very_old_gigs(monkeypatch) -> None:
    executed: list[tuple[str, list[object]]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: list[object]) -> None:
            executed.append((query, params))

        def fetchall(self) -> list[dict[str, str]]:
            return []

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

    rows = list_dashboard_notifications(
        SharedSettings(),
        viewer_discord_user_id="poster-1",
        include_all=False,
        stale_days=7,
        contacted_reminder_days=5,
        max_age_days=90,
        limit=5,
    )

    assert rows == []
    query, params = executed[0]
    assert "COALESCE(e.posted_at, e.created_at)" in query
    assert "e.status = 'contacted'" in query
    assert params == [7, 5, 90, "poster-1", 5]


def test_get_gig_thread_interest_backfill_marker_returns_payload(monkeypatch) -> None:
    executed: list[tuple[str, tuple]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, dict[str, str]]:
            return {"payload": {"last_scanned_message_created_at": "2026-05-10"}}

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

    assert get_gig_thread_interest_backfill_marker(
        SharedSettings(),
        engagement_id="engagement-1",
    ) == {"last_scanned_message_created_at": "2026-05-10"}
    query, params = executed[0]
    assert "ORDER BY created_at DESC" in query
    assert params == ("engagement-1", "gig_thread_interest_backfilled")


def test_upsert_gig_thread_interest_backfill_marker_uses_conflict_update(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

    class ConnectionStub:
        def cursor(self) -> CursorStub:
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

    upsert_gig_thread_interest_backfill_marker(
        SharedSettings(),
        engagement_id="engagement-1",
        actor_discord_user_id="actor-1",
        payload={"scanned_count": 1},
    )

    query, params = executed[0]
    assert "ON CONFLICT (engagement_id, event_type)" in query
    assert "gig_thread_interest_backfilled" in query
    assert params[1:4] == (
        "engagement-1",
        "gig_thread_interest_backfilled",
        "actor-1",
    )


def test_upsert_discord_interest_application_uses_historical_activity_timestamp(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter([None, {"id": "application-1"}])
    occurred_at = datetime(2026, 5, 10, 12, 30, tzinfo=timezone.utc)

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    application_id = upsert_discord_interest_application(
        SharedSettings(),
        engagement_id="engagement-1",
        discord_user_id="discord-1",
        discord_username="jamie",
        message_id="message-1",
        message_content="I'm interested",
        activity_at=occurred_at,
        event_created_at=occurred_at,
    )

    assert application_id == "application-1"
    activity_query, activity_params = executed[2]
    assert "GREATEST" in activity_query
    assert activity_params == (occurred_at, "engagement-1")
    event_query, event_params = executed[3]
    assert "created_at" in event_query
    assert event_params[-1] == occurred_at


def test_upsert_discord_interest_application_can_skip_activity_refresh(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter([None, {"id": "application-1"}])

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    application_id = upsert_discord_interest_application(
        SharedSettings(),
        engagement_id="engagement-1",
        discord_user_id="discord-1",
        discord_username="jamie",
        refresh_activity=False,
    )

    assert application_id == "application-1"
    assert all("UPDATE engagements" not in query for query, _ in executed)


def test_upsert_suggested_applications_merges_existing_discord_row(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter(
        [
            {"id": "person-1"},
            {"id": "application-1"},
            {"id": "application-1"},
        ]
    )

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    count = upsert_suggested_applications(
        SharedSettings(),
        engagement_id="engagement-1",
        candidates=[
            SimpleNamespace(
                crm_contact_id="crm-1",
                discord_user_id="discord-1",
                match_score=42.0,
                llm_fit_score=88.0,
            )
        ],
    )

    assert count == 1
    update_query = executed[2][0]
    update_params = executed[2][1]
    assert "UPDATE engagement_applications" in update_query
    assert "WHERE id = %s" in update_query
    assert update_params[-1:] == ("application-1",)
    assert len(executed) == 3


def test_upsert_suggested_applications_persists_discord_only_candidate(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter(
        [
            None,
            None,
            None,
            {"id": "application-1"},
        ]
    )

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    count = upsert_suggested_applications(
        SharedSettings(),
        engagement_id="engagement-1",
        candidates=[
            SimpleNamespace(
                crm_contact_id=None,
                discord_user_id="discord-1",
                match_score=12.0,
                llm_fit_score=None,
            )
        ],
    )

    assert count == 1
    insert_query, insert_params = executed[3]
    assert "ON CONFLICT (engagement_id, discord_user_id)" in insert_query
    assert insert_params[3:5] == (None, "discord-1")


def test_add_crm_application_to_engagement_uses_verified_contact_payload(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter(
        [
            {"id": "engagement-1"},
            {"id": "person-1", "discord_user_id": None},
            None,
            {
                "id": "application-1",
                "engagement_id": "engagement-1",
                "status": "suggested",
                "source": "crm",
                "crm_contact_id": "crm-1",
                "updated_at": datetime(2026, 6, 1, 1, 2, tzinfo=timezone.utc),
            },
        ]
    )

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    result = add_crm_application_to_engagement(
        SharedSettings(),
        engagement_id="engagement-1",
        crm_contact_id="crm-1",
        contact_payload={"name": "Casey Candidate", "emailAddress": "casey@508.dev"},
        actor_discord_user_id="actor-1",
    )

    assert result is not None
    assert result["id"] == "application-1"
    assert result["name"] == "Casey Candidate"
    update_query, update_params = executed[2]
    assert "UPDATE engagement_applications" in update_query
    assert update_params[0:2] == ("person-1", "crm-1")
    insert_query, insert_params = executed[3]
    assert "INSERT INTO engagement_applications" in insert_query
    assert "ON CONFLICT (engagement_id, crm_contact_id)" in insert_query
    assert insert_params[2:4] == ("person-1", "crm-1")
    event_query, event_params = executed[5]
    assert "candidate_added" in event_query
    assert event_params[2] == "actor-1"


def test_add_crm_application_to_engagement_merges_existing_discord_interest(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple]] = []
    fetches = iter(
        [
            {"id": "engagement-1"},
            {"id": "person-1", "discord_user_id": "discord-1"},
            {
                "id": "application-1",
                "engagement_id": "engagement-1",
                "status": "interested",
                "source": "direct_interest",
                "crm_contact_id": "crm-1",
                "updated_at": datetime(2026, 6, 1, 1, 2, tzinfo=timezone.utc),
            },
        ]
    )

    class CursorStub:
        def __enter__(self) -> "CursorStub":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        def execute(self, query: str, params: tuple) -> None:
            executed.append((query, params))

        def fetchone(self) -> dict[str, str] | None:
            return next(fetches, None)

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

    result = add_crm_application_to_engagement(
        SharedSettings(),
        engagement_id="engagement-1",
        crm_contact_id="crm-1",
        contact_payload={"name": "Casey Candidate", "emailAddress": "casey@508.dev"},
        actor_discord_user_id="actor-1",
    )

    assert result is not None
    assert result["id"] == "application-1"
    update_query, update_params = executed[2]
    assert "UPDATE engagement_applications" in update_query
    assert "discord_user_id = %s" in update_query
    assert update_params[6:8] == ("discord-1", "discord-1")
    assert all(
        "INSERT INTO engagement_applications" not in query for query, _ in executed
    )
    event_query, event_params = executed[4]
    assert "candidate_added" in event_query
    assert event_params[2] == "actor-1"
