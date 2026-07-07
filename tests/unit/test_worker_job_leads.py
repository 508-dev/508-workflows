"""Tests for job lead worker jobs."""

from __future__ import annotations

from five08.worker import jobs


def test_scrape_job_leads_job_is_registered() -> None:
    assert jobs.JOB_FUNCTIONS["scrape_job_leads_job"] is jobs.scrape_job_leads_job


def test_scrape_job_leads_job_delegates_to_shared_scraper(monkeypatch) -> None:
    calls: list[tuple[object, str, int | None]] = []

    def _scrape(settings: object, *, source: str, story_id: int | None):
        calls.append((settings, source, story_id))
        return {"source": source, "created": 1, "updated": 0, "total": 1}

    monkeypatch.setattr(jobs, "scrape_job_leads", _scrape)

    result = jobs.scrape_job_leads_job(source="hackernews_who_is_hiring", story_id=1)

    assert result == {
        "source": "hackernews_who_is_hiring",
        "created": 1,
        "updated": 0,
        "total": 1,
    }
    assert calls == [(jobs.settings, "hackernews_who_is_hiring", 1)]
