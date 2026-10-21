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


def test_rank_job_posts_handles_sentence_punctuation_and_skill_boundaries() -> None:
    rows = [
        _row("JavaScript developer", "US hours"),
        _row("Java developer", "US hours"),
        _row("React engineer", "US hours"),
        _row("Node.js developer", "US hours"),
    ]

    assert [
        row["title"] for row in discovery.rank_job_posts(rows, "Find Java jobs.")
    ] == ["Java developer"]
    assert [
        row["title"] for row in discovery.rank_job_posts(rows, "Find React jobs.")
    ] == ["React engineer"]
    assert [
        row["title"] for row in discovery.rank_job_posts(rows, "Find Node.js jobs.")
    ] == ["Node.js developer"]


def test_rank_job_posts_ignores_job_search_phrasing() -> None:
    rows = [_row("React developer", "APAC hours")]

    assert discovery.rank_job_posts(rows, "Find a job post") == rows
    assert discovery.rank_job_posts(rows, "Send me any React jobs") == rows


def test_rank_job_posts_avoids_accidental_apac_and_remote_matches() -> None:
    rows = [
        _row("Designer", "Aesthetic work in Indiana, non-remote"),
        _row("Designer", "Japan hours, remote"),
    ]

    assert [
        row["body"]
        for row in discovery.rank_job_posts(rows, "Find remote designer jobs in APAC")
    ] == ["Japan hours, remote"]
    assert discovery.rank_job_posts(rows[:1], "Find remote designer jobs") == []
    assert discovery.rank_job_posts(rows[:1], "Find designer jobs in APAC") == []


def test_rank_job_posts_treats_alternative_employment_types_as_union() -> None:
    rows = [
        _row("React gig", "", posting_type="part_time"),
        _row("React role", "", posting_type="full_time"),
        _row("React opening", "", posting_type="part_time_or_full_time"),
    ]

    matches = discovery.rank_job_posts(rows, "Find full-time or part-time React jobs")

    assert {row["posting_type"] for row in matches} == {
        "part_time",
        "full_time",
        "part_time_or_full_time",
    }


def test_rank_job_posts_preserves_single_letter_programming_languages() -> None:
    rows = [
        _row("C systems developer", ""),
        _row("R data analyst", ""),
        _row("Python developer", ""),
    ]

    assert [row["title"] for row in discovery.rank_job_posts(rows, "Find C jobs")] == [
        "C systems developer"
    ]
    assert [row["title"] for row in discovery.rank_job_posts(rows, "Find R jobs")] == [
        "R data analyst"
    ]


def test_rank_job_posts_filters_contract_and_freelance_work() -> None:
    rows = [
        _row("React developer", "", posting_type="full_time"),
        _row("React developer", "", posting_type="part_time"),
    ]

    assert [
        row["posting_type"]
        for row in discovery.rank_job_posts(rows, "Find contract React jobs")
    ] == ["part_time"]
    assert [
        row["posting_type"]
        for row in discovery.rank_job_posts(rows, "Find freelance React jobs")
    ] == ["part_time"]


def test_search_job_posts_returns_larger_bounded_candidate_pool(monkeypatch) -> None:
    rows = [_row(f"React job {index}", "") for index in range(60)]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args):
            return None

        def fetchall(self):
            return rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self, **_kwargs):
            return Cursor()

    monkeypatch.setattr(
        discovery, "get_postgres_connection", lambda _settings: Connection()
    )

    matches = discovery.search_job_posts(
        SharedSettings(),
        guild_id="guild-1",
        active_channel_ids=["10"],
        lead_channel_ids=[],
        query="React jobs",
        limit=50,
    )

    assert len(matches) == 50


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
