"""Tests for sourced job lead persistence helpers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone

import five08.job_leads as job_leads
import pytest
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
        "staged_discord_guild_id": None,
        "staged_discord_channel_id": None,
        "staged_discord_thread_id": None,
        "staged_at": None,
        "staging_reservation_token": None,
        "staging_reserved_at": None,
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
    assert "WHERE job_leads.status IN ('pending', 'rejected')" in query
    assert "apply_url = EXCLUDED.apply_url" in query
    assert "\n            status =" not in query.split("DO UPDATE SET", 1)[1]
    for staging_column in (
        "staged_discord_guild_id",
        "staged_discord_channel_id",
        "staged_discord_thread_id",
        "staged_at",
        "staging_reservation_token",
        "staging_reserved_at",
    ):
        assert f"{staging_column} = CASE" in query
    assert "_staging_source_fingerprint" in query
    assert query.count("WHEN job_leads.status IN ('pending', 'rejected')") == 6
    assert params[15] == ["1099", "contract-to-hire"]
    assert params[-1].obj[
        job_leads._STAGING_SOURCE_FINGERPRINT_METADATA_KEY
    ] == job_leads._job_lead_staging_source_fingerprint(
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
        posting_type=job_leads.JobPostingType.PART_TIME,
        tags=["1099", "contract-to-hire"],
    )


def test_staging_source_fingerprint_tracks_holding_thread_content() -> None:
    base = JobLeadInput(
        source_key="hackernews_who_is_hiring",
        source_type="hackernews",
        external_id="48392586",
        source_url="https://news.ycombinator.com/item?id=48392586",
        title="Contract role",
        body_raw="raw",
        body_normalized="Original description",
        organization="508 Dev",
        location="Remote",
        remote=True,
        apply_url="https://example.com/apply",
        tags=["remote", "contract"],
        metadata={"source_observed_at": "2026-08-20T00:00:00Z"},
    )
    same_thread_content = replace(
        base,
        metadata={"source_observed_at": "2026-08-20T01:00:00Z"},
    )
    changed_thread_content = replace(
        base,
        body_normalized="Corrected description",
    )

    fingerprint = job_leads._job_lead_staging_source_fingerprint(
        base,
        posting_type=job_leads.JobPostingType.PART_TIME,
        tags=["contract", "remote"],
    )

    assert (
        job_leads._job_lead_staging_source_fingerprint(
            same_thread_content,
            posting_type=job_leads.JobPostingType.PART_TIME,
            tags=["contract", "remote"],
        )
        == fingerprint
    )
    assert (
        job_leads._job_lead_staging_source_fingerprint(
            changed_thread_content,
            posting_type=job_leads.JobPostingType.PART_TIME,
            tags=["contract", "remote"],
        )
        != fingerprint
    )


def test_upsert_job_lead_skips_reviewed_conflict(monkeypatch) -> None:
    cursor = _CursorStub()
    _install_connection_stub(monkeypatch, cursor)

    lead_id, created = job_leads.upsert_job_lead(
        job_leads.SharedSettings(),
        JobLeadInput(
            source_key="hackernews_who_is_hiring",
            source_type="hackernews",
            external_id="48392586",
            source_url="https://news.ycombinator.com/item?id=48392586",
            title="Contract role",
            body_raw="raw",
            body_normalized="normalized",
        ),
    )

    assert lead_id is None
    assert created is False


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
    assert "WITH incoming AS" in query
    assert "FROM incoming" in query
    assert "status IN ('pending', 'rejected')" in query
    assert "apply_url = %s" in query
    assert "apply_url = COALESCE" not in query
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
    for staging_column in (
        "staged_discord_guild_id",
        "staged_discord_channel_id",
        "staged_discord_thread_id",
        "staged_at",
        "staging_reservation_token",
        "staging_reserved_at",
    ):
        assert f"{staging_column} = CASE" in query
    assert query.count("WHEN job_leads.status IN ('pending', 'rejected')") == 6
    assert (
        params[0] == params[-3].obj[job_leads._STAGING_SOURCE_FINGERPRINT_METADATA_KEY]
    )
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
    cursor = _CursorStub(rows=[_lead_row(engagement_id="engagement-1")])
    _install_connection_stub(monkeypatch, cursor)

    result = job_leads.list_job_leads(
        job_leads.SharedSettings(),
        status=JobLeadStatus.PENDING,
        limit=5,
    )

    assert len(result) == 1
    assert result[0].status is JobLeadStatus.PENDING
    assert result[0].engagement_id == "engagement-1"
    assert job_leads.job_lead_display_payload(result[0])["engagement_id"] == (
        "engagement-1"
    )
    assert "LEFT JOIN LATERAL" in cursor.executed[0][0]
    assert "FROM engagements" in cursor.executed[0][0]
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


def test_review_job_lead_can_qualify_without_holding_thread(monkeypatch) -> None:
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
        "approved",
        "11111111-1111-1111-1111-111111111111",
        ["pending", "approved"],
    )
    assert "staged_discord_thread_id" not in cursor.executed[1][0]
    assert "staging_reservation_token = NULL" in cursor.executed[1][0]


def test_review_job_lead_restores_rejected_lead_to_pending(monkeypatch) -> None:
    cursor = _CursorStub(
        rows=[
            [_lead_row(status="rejected", reviewed_by_discord_user_id="42")],
            _lead_row(
                status="pending", reviewed_by_discord_user_id=None, reviewed_at=None
            ),
        ]
    )
    _install_connection_stub(monkeypatch, cursor)

    restored = job_leads.review_job_lead(
        job_leads.SharedSettings(),
        lead_id="11111111",
        status=JobLeadStatus.PENDING,
        reviewer_discord_user_id="42",
    )

    assert restored is not None
    assert restored.status is JobLeadStatus.PENDING
    assert restored.reviewed_by_discord_user_id is None
    assert restored.reviewed_at is None
    query, params = cursor.executed[1]
    assert "status = ANY(%s)" in query
    assert params == (
        "pending",
        None,
        "pending",
        "11111111-1111-1111-1111-111111111111",
        ["rejected"],
    )


def test_reserve_job_lead_staging_claims_pending_lead_atomically(monkeypatch) -> None:
    cursor = _CursorStub(rows=[_lead_row(staging_reservation_token="attempt-1")])
    _install_connection_stub(monkeypatch, cursor)

    reserved = job_leads.reserve_job_lead_staging(
        job_leads.SharedSettings(),
        lead_id="11111111-1111-1111-1111-111111111111",
        reservation_token="attempt-1",
    )

    assert reserved is not None
    query, params = cursor.executed[0]
    assert "staging_reservation_token = %s" in query
    assert "staging_reservation_token IS NULL" in query
    assert "staging_reserved_at IS NULL" in query
    assert "staging_reserved_at < NOW() - (%s * INTERVAL '1 second')" in query
    assert "staged_discord_thread_id IS NULL" in query
    assert "metadata -> '_staging_cleanup_required' IS NULL" in query
    assert params == (
        "attempt-1",
        "11111111-1111-1111-1111-111111111111",
        job_leads.JOB_LEAD_STAGING_RESERVATION_TTL_SECONDS,
    )


def test_release_job_lead_staging_reservation_only_releases_own_attempt(
    monkeypatch,
) -> None:
    cursor = _CursorStub(rows=[{"id": "11111111-1111-1111-1111-111111111111"}])
    _install_connection_stub(monkeypatch, cursor)

    released = job_leads.release_job_lead_staging_reservation(
        job_leads.SharedSettings(),
        lead_id="11111111-1111-1111-1111-111111111111",
        reservation_token="attempt-1",
    )

    assert released is True
    query, params = cursor.executed[0]
    assert "staging_reservation_token = NULL" in query
    assert "staging_reservation_token = %s" in query
    assert "staged_discord_thread_id IS NULL" in query
    assert params == ("11111111-1111-1111-1111-111111111111", "attempt-1")


def test_mark_job_lead_staged_records_reserved_holding_thread(monkeypatch) -> None:
    cursor = _CursorStub(
        rows=[
            _lead_row(
                staged_discord_guild_id="123",
                staged_discord_channel_id="456",
                staged_discord_thread_id="789",
                staged_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            )
        ]
    )
    _install_connection_stub(monkeypatch, cursor)

    staged = job_leads.mark_job_lead_staged(
        job_leads.SharedSettings(),
        lead_id="11111111-1111-1111-1111-111111111111",
        reservation_token="attempt-1",
        source_fingerprint="source-fingerprint",
        guild_id="123",
        channel_id="456",
        thread_id="789",
    )

    assert staged is not None
    assert staged.staged_discord_thread_id == "789"
    query, params = cursor.executed[0]
    assert "status = 'pending'" in query
    assert "staged_discord_thread_id IS NULL" in query
    assert "staging_reservation_token = %s" in query
    assert "metadata = metadata || jsonb_build_object" in query
    assert params == (
        "123",
        "456",
        "789",
        "source-fingerprint",
        "11111111-1111-1111-1111-111111111111",
        "attempt-1",
    )


def test_record_job_lead_staging_cleanup_required_blocks_restaging(monkeypatch) -> None:
    cursor = _CursorStub(rows=[{"id": "11111111-1111-1111-1111-111111111111"}])
    _install_connection_stub(monkeypatch, cursor)

    recorded = job_leads.record_job_lead_staging_cleanup_required(
        job_leads.SharedSettings(),
        lead_id="11111111-1111-1111-1111-111111111111",
        guild_id="123",
        channel_id="456",
        thread_id="789",
    )

    assert recorded is True
    query, params = cursor.executed[0]
    assert "metadata = metadata || %s" in query
    assert "status IN ('pending', 'rejected')" in query
    assert "staged_discord_thread_id IS NULL" not in query
    assert params[0].obj == {
        job_leads._STAGING_CLEANUP_REQUIRED_METADATA_KEY: {
            "guild_id": "123",
            "channel_id": "456",
            "thread_id": "789",
        }
    }
    assert params[1] == "11111111-1111-1111-1111-111111111111"


def test_job_lead_staging_recovery_details_returns_orphaned_thread_metadata() -> None:
    lead = job_leads._as_lead(
        _lead_row(
            metadata={
                job_leads._STAGING_CLEANUP_REQUIRED_METADATA_KEY: {
                    "guild_id": " 123 ",
                    "channel_id": "456",
                    "thread_id": "789",
                }
            }
        )
    )

    assert job_leads.job_lead_staging_recovery_details(lead) == {
        "guild_id": "123",
        "channel_id": "456",
        "thread_id": "789",
    }


def test_mark_job_lead_posted_allows_direct_qualified_promotion(monkeypatch) -> None:
    cursor = _CursorStub(
        rows=[
            _lead_row(
                status="posted",
                reviewed_by_discord_user_id="42",
                discord_guild_id="123",
                discord_channel_id="456",
                discord_thread_id="789",
            )
        ]
    )
    _install_connection_stub(monkeypatch, cursor)

    posted = job_leads.mark_job_lead_posted(
        job_leads.SharedSettings(),
        lead_id="11111111-1111-1111-1111-111111111111",
        reviewer_discord_user_id="42",
        guild_id="123",
        channel_id="456",
        thread_id="789",
    )

    assert posted is not None
    assert posted.status is JobLeadStatus.POSTED
    query, params = cursor.executed[0]
    assert "status = 'approved'" in query
    assert "staged_discord_thread_id" not in query
    assert params == (
        "42",
        "123",
        "456",
        "789",
        "11111111-1111-1111-1111-111111111111",
    )


def test_review_job_lead_rejects_unknown_status_before_lookup(monkeypatch) -> None:
    cursor = _CursorStub()
    _install_connection_stub(monkeypatch, cursor)

    with pytest.raises(
        ValueError,
        match="Job lead review status must be pending, approved, or rejected.",
    ):
        job_leads.review_job_lead(
            job_leads.SharedSettings(),
            lead_id="11111111",
            status="approve",
            reviewer_discord_user_id="42",
        )

    assert cursor.executed == []
