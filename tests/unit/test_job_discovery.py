"""Job discovery keeps results recent, relevant, and scoped to visible forums."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import five08.job_discovery as discovery
from five08.settings import SharedSettings


def _row(
    title: str,
    body: str,
    *,
    kind: str = "gig",
    posting_type: str = "part_time",
    posted_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "body": body,
        "kind": kind,
        "posting_type": posting_type,
        "posted_at": posted_at or datetime.now(timezone.utc),
        "location": None,
        "remote": None,
        "tags": [],
        "thread_id": "12",
        "channel_id": "10",
    }


def test_rank_job_posts_uses_skills_apac_hours_and_posting_type() -> None:
    rows = [
        _row("React engineer", "US hours"),
        _row("React engineer", "Japan time, remote", posting_type="full_time"),
        _row("Data engineer", "Singapore hours", posting_type="full_time"),
    ]

    matches = discovery.rank_job_posts(
        rows, "Looking for full-time React roles in APAC hours"
    )

    assert [row["title"] for row in matches] == ["React engineer"]
    assert matches[0]["posting_type"] == "full_time"


def test_search_job_posts_limits_guild_channels_status_and_freshness(
    monkeypatch,
) -> None:
    executed: list[tuple[str, tuple[Any, ...]]] = []
    recent = datetime.now(timezone.utc) - timedelta(days=2)

    class Cursor:
        def __enter__(self) -> "Cursor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: Any, params: tuple[Any, ...]) -> None:
            executed.append((str(query), params))

        def fetchall(self) -> list[dict[str, Any]]:
            if len(executed) == 1:
                return [_row("Python developer", "Tokyo hours", posted_at=recent)]
            return [
                _row(
                    "Python contractor",
                    "Singapore hours",
                    kind="unverified_lead",
                    posted_at=recent,
                )
            ]

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self, **_kwargs: object) -> Cursor:
            return Cursor()

    @contextmanager
    def connection(_settings: SharedSettings):
        yield Connection()

    monkeypatch.setattr(discovery, "get_postgres_connection", connection)
    matches = discovery.search_job_posts(
        SharedSettings(),
        guild_id="guild-1",
        active_channel_ids=["10"],
        lead_channel_ids=["20"],
        query="Python jobs in APAC hours",
    )

    assert {row["kind"] for row in matches} == {"gig", "unverified_lead"}
    gig_sql, gig_params = executed[0]
    lead_sql, lead_params = executed[1]
    assert "status = 'recruiting'" in gig_sql
    assert "status = 'pending'" in lead_sql
    assert "staged_discord_thread_id IS NOT NULL" in lead_sql
    assert gig_params[:2] == ("guild-1", ["10"])
    assert lead_params[:2] == ("guild-1", ["20"])
    assert (datetime.now(timezone.utc) - gig_params[2]).days == 45


def test_search_job_posts_never_queries_without_visible_forums(monkeypatch) -> None:
    def fail(_settings: SharedSettings):
        raise AssertionError("database should not be queried")

    monkeypatch.setattr(discovery, "get_postgres_connection", fail)
    assert (
        discovery.search_job_posts(
            SharedSettings(),
            guild_id="guild-1",
            active_channel_ids=[],
            lead_channel_ids=[],
            query="React jobs",
        )
        == []
    )
