"""Tests for sourced job lead persistence helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import five08.job_leads as job_leads
from five08.job_leads import JobLeadInput, JobLeadStatus


class _CursorStub:
    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_CursorStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def execute(self, query: str, params: tuple) -> None:
        self.executed.append((query, params))

    def fetchone(self) -> dict | None:
        if not self.rows:
            return None
        row = self.rows.pop(0)
        return row if isinstance(row, dict) else None

    def fetchall(self) -> list[dict]:
        if not self.rows:
            return []
        rows = self.rows.pop(0)
        return list(rows) if isinstance(rows, list) else [rows]


class _ConnectionStub:
    def __init__(self, cursor: _CursorStub):
        self._cursor = cursor

    def cursor(self, row_factory=None) -> _CursorStub:  # noqa: ARG002
        return self._cursor

    def __enter__(self) -> "_ConnectionStub":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def _install_connection_stub(monkeypatch, cursor: _CursorStub) -> None:
    @contextmanager
    def _conn():
        yield _ConnectionStub(cursor)

    monkeypatch.setattr(job_leads, "get_postgres_connection", lambda _: _conn())


def _lead_row(**overrides: object) -> dict:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "pending",
        "source_key": "hackernews_who_is_hiring",
        "source_type": "hackernews",
        "external_id": "48392586",
        "external_parent_id": "48357725",
        "source_url": "https://news.ycombinator.com/item?id=48392586",
        "source_posted_at": now,
        "title": "CO-Ver | Fullstack | 1099 Contract-to-Hire",
        "organization": "CO-Ver",
        "body_raw": "raw",
        "body_normalized": "normalized",
        "posting_type": "part_time",
        "location": "Remote US",
        "remote": True,
        "apply_url": "https://example.com",
        "tags": ["1099", "contract-to-hire"],
        "confidence": 0.65,
        "metadata": {"hn_story_id": 48357725},
        "reviewed_by_discord_user_id": None,
        "reviewed_at": None,
        "discord_guild_id": None,
        "discord_channel_id": None,
        "discord_thread_id": None,
        "posted_at": None,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def test_upsert_job_lead_preserves_review_state_on_conflict(monkeypatch) -> None:
    cursor = _CursorStub(rows=[{"id": "lead-1", "inserted": False}])
    _install_connection_stub(monkeypatch, cursor)

    lead_id, created = job_leads.upsert_job_lead(
        job_leads.SharedSettings(),
        JobLeadInput(
            source_key="hackernews_who_is_hiring",
            source_type="hackernews",
            external_id="48392586",
            source_url="https://news.ycombinator.com/item?id=48392586",
            title="CO-Ver | Fullstack | 1099 Contract-to-Hire",
            body_raw="raw",
            body_normalized="normalized",
            tags=["Contract-to-Hire", "1099"],
            confidence=0.65,
        ),
    )

    assert lead_id == "lead-1"
    assert created is False
    query, params = cursor.executed[0]
    assert "ON CONFLICT (source_key, external_id) DO UPDATE" in query
    assert "status =" not in query.split("DO UPDATE SET", 1)[1]
    assert params[15] == ["1099", "contract-to-hire"]


def test_update_existing_job_lead_never_inserts(monkeypatch) -> None:
    cursor = _CursorStub(rows=[{"id": "lead-1"}])
    _install_connection_stub(monkeypatch, cursor)

    lead_id = job_leads.update_existing_job_lead(
        job_leads.SharedSettings(),
        JobLeadInput(
            source_key="hackernews_who_is_hiring",
            source_type="hackernews",
            external_id="48392586",
            source_url="https://news.ycombinator.com/item?id=48392586",
            title="Full-time role",
            body_raw="raw",
            body_normalized="Full-time role",
            posting_type="full_time",
            apply_url="https://example.com/jobs/role",
            tags=["full-time"],
            confidence=0.85,
        ),
    )

    assert lead_id == "lead-1"
    query, params = cursor.executed[0]
    assert "UPDATE job_leads" in query
    assert "INSERT" not in query
    assert "status IN ('pending', 'rejected')" in query
    update_clause = query.split("SET", 1)[1].split("WHERE", 1)[0]
    assigned_columns = {
        line.strip().split("=", 1)[0].strip()
        for line in update_clause.splitlines()
        if "=" in line
    }
    for preserved_column in (
        "status",
        "reviewed_at",
        "reviewed_by_discord_user_id",
        "discord_guild_id",
        "discord_channel_id",
        "discord_thread_id",
        "posted_at",
    ):
        assert preserved_column not in assigned_columns
    assert params[-2:] == ("hackernews_who_is_hiring", "48392586")


def test_existing_job_lead_external_ids_uses_one_batch_query(monkeypatch) -> None:
    cursor = _CursorStub(rows=[[{"external_id": "11"}, {"external_id": "12"}]])
    _install_connection_stub(monkeypatch, cursor)

    external_ids = job_leads.existing_job_lead_external_ids(
        job_leads.SharedSettings(),
        source_key="hackernews_who_is_hiring",
        external_ids=["12", "11", "12"],
    )

    assert external_ids == {"11", "12"}
    assert len(cursor.executed) == 1
    assert "status IN ('pending', 'rejected')" in cursor.executed[0][0]
    assert cursor.executed[0][1] == (
        "hackernews_who_is_hiring",
        ["11", "12"],
    )


def test_list_job_leads_filters_pending(monkeypatch) -> None:
    cursor = _CursorStub(rows=[_lead_row()])
    _install_connection_stub(monkeypatch, cursor)

    result = job_leads.list_job_leads(
        job_leads.SharedSettings(),
        status=JobLeadStatus.PENDING,
        limit=5,
    )

    assert len(result) == 1
    assert result[0].status is JobLeadStatus.PENDING
    assert cursor.executed[0][1] == ("pending", 5)


def test_display_payload_explains_employment_type_and_contact() -> None:
    row = _lead_row(
        posting_type="part_time",
        metadata={
            "contractor_classification": {
                "is_contractor_friendly": True,
                "posting_type": "part_time",
                "tags": ["contract", "remote"],
                "confidence": 0.82,
                "confidence_label": "high",
                "rationale": "Explicitly offers contract work.",
                "method": "llm",
                "contact_email": "hiring@example.com",
            }
        },
    )

    payload = job_leads.job_lead_display_payload(row)

    assert payload["contractor_classification"]["contact_email"] == (
        "hiring@example.com"
    )
    assert payload["review_summary"] == (
        "LLM: Part-time / contract; evidence: contract, remote - "
        "Explicitly offers contract work."
    )


def test_review_job_lead_uses_exact_id_after_prefix_lookup(monkeypatch) -> None:
    cursor = _CursorStub(
        rows=[
            [_lead_row()],
            _lead_row(status="approved", reviewed_by_discord_user_id="42"),
        ]
    )

    @contextmanager
    def _conn():
        yield _ConnectionStub(cursor)

    monkeypatch.setattr(job_leads, "get_postgres_connection", lambda _: _conn())

    reviewed = job_leads.review_job_lead(
        job_leads.SharedSettings(),
        lead_id="11111111",
        status=JobLeadStatus.APPROVED,
        reviewer_discord_user_id="42",
    )

    assert reviewed is not None
    assert reviewed.status is JobLeadStatus.APPROVED
    assert cursor.executed[1][1] == (
        "approved",
        "42",
        "11111111-1111-1111-1111-111111111111",
    )
