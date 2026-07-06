"""Tests for external job lead source adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from five08.job_lead_sources import (
    HackerNewsThread,
    HackerNewsWhoIsHiringLeadSource,
    classify_contractor_lead,
    html_to_text,
)


class _FakeHackerNewsClient:
    def search_who_is_hiring_threads(self, *, hits_per_page: int = 12):  # noqa: ANN201
        return [
            HackerNewsThread(
                story_id=48357725,
                title="Ask HN: Who is hiring? (June 2026)",
                created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                descendants=3,
            )
        ]

    def get_algolia_item_tree(self, item_id: int) -> dict:
        assert item_id == 48357725
        return {
            "id": item_id,
            "children": [
                {
                    "id": 1,
                    "parent_id": item_id,
                    "author": "company",
                    "created_at": "2026-06-01T15:02:00Z",
                    "text": (
                        "Acme | Contract Backend Engineer | Remote US"
                        "<p>We need a 1099 contractor for Python APIs."
                        '<p>Apply: <a href="https://acme.example/jobs">link</a>'
                    ),
                },
                {
                    "id": 2,
                    "parent_id": item_id,
                    "author": "person",
                    "created_at": "2026-06-01T15:03:00Z",
                    "text": "SEEKING WORK | Remote | Freelance engineer",
                },
                {
                    "id": 3,
                    "parent_id": 1,
                    "author": "reply",
                    "created_at": "2026-06-01T15:04:00Z",
                    "text": "Is this contract remote?",
                },
            ],
        }


def test_html_to_text_preserves_links_and_paragraph_breaks() -> None:
    text = html_to_text('Hello<p><a href="https://example.com">Apply</a>')

    assert "Hello" in text
    assert "https://example.com" in text
    assert "Apply" in text


def test_classify_contractor_lead_rejects_seeking_work() -> None:
    is_lead, tags, confidence = classify_contractor_lead(
        "SEEKING WORK | Remote | Freelance developer"
    )

    assert is_lead is False
    assert tags == []
    assert confidence == 0.0


def test_hacker_news_source_extracts_top_level_contractor_posts() -> None:
    source = HackerNewsWhoIsHiringLeadSource(client=_FakeHackerNewsClient())

    leads = source.collect()

    assert len(leads) == 1
    lead = leads[0]
    assert lead.external_id == "1"
    assert lead.external_parent_id == "48357725"
    assert lead.organization == "Acme"
    assert lead.title == "Acme | Contract Backend Engineer | Remote US"
    assert lead.remote is True
    assert lead.apply_url == "https://acme.example/jobs"
    assert {"contract", "1099"}.issubset(set(lead.tags or []))
    assert lead.confidence >= 0.5
