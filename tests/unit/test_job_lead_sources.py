"""Tests for external job lead source adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from five08.job_lead_sources import (
    HackerNewsThread,
    HackerNewsWhoIsHiringLeadSource,
    JobLeadClassifier,
    JobLeadClassification,
    _build_llm_client,
    classify_contractor_lead,
    html_to_text,
)
from five08.job_channels import JobPostingType


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


class _FakeJobLeadClassifier:
    def classify(self, comment_text: str) -> JobLeadClassification:
        if "Employee-only" in comment_text:
            return JobLeadClassification(
                is_contractor_friendly=False,
                posting_type=JobPostingType.FULL_TIME,
                tags=["employee"],
                confidence=0.9,
                confidence_label="high",
                rationale="Full-time employee-only role.",
                method="llm",
            )
        return JobLeadClassification(
            is_contractor_friendly=True,
            posting_type=JobPostingType.PART_TIME,
            tags=["contract", "remote"],
            confidence=0.88,
            confidence_label="high",
            rationale="Explicitly allows contract work.",
            method="llm",
        )


class _PositiveJobLeadClassifier:
    def classify(self, comment_text: str) -> JobLeadClassification:
        return JobLeadClassification(
            is_contractor_friendly=True,
            posting_type=JobPostingType.PART_TIME,
            tags=["freelance"],
            confidence=0.95,
            confidence_label="high",
            rationale="Injected positive classification.",
            method="llm",
        )


class _FakeClassifierHackerNewsClient(_FakeHackerNewsClient):
    def get_algolia_item_tree(self, item_id: int) -> dict:
        assert item_id == 48357725
        return {
            "id": item_id,
            "children": [
                {
                    "id": 10,
                    "parent_id": item_id,
                    "author": "company",
                    "created_at": "2026-06-01T15:02:00Z",
                    "text": "Acme | Backend Engineer | Remote<p>Contract welcome.",
                },
                {
                    "id": 11,
                    "parent_id": item_id,
                    "author": "company",
                    "created_at": "2026-06-01T15:03:00Z",
                    "text": "Fulltime Co | Employee-only backend role | Remote",
                },
            ],
        }


class _FakeSeekingWorkHackerNewsClient(_FakeHackerNewsClient):
    def get_algolia_item_tree(self, item_id: int) -> dict:
        assert item_id == 48357725
        return {
            "id": item_id,
            "children": [
                {
                    "id": 20,
                    "parent_id": item_id,
                    "author": "person",
                    "created_at": "2026-06-01T15:02:00Z",
                    "text": "SEEKING WORK | Remote | Freelance Python engineer",
                }
            ],
        }


class _FakeFailingStructuredClient:
    def __init__(self) -> None:
        self.chat_create_calls = 0
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(parse=self._parse),
            )
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _parse(self, **_kwargs: object) -> object:
        raise TimeoutError("provider timed out")

    def _create(self, **_kwargs: object) -> object:
        self.chat_create_calls += 1
        raise AssertionError("chat fallback should not run after provider failure")


def test_html_to_text_preserves_links_and_paragraph_breaks() -> None:
    text = html_to_text('Hello<p><a href="https://example.com">Apply</a>')

    assert text == "Hello\n https://example.com Apply"


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
    classification = lead.metadata["contractor_classification"]
    assert classification["method"] == "heuristic"
    assert classification["confidence_label"] == "high"


def test_hacker_news_source_uses_injected_classifier_for_lead_filtering() -> None:
    source = HackerNewsWhoIsHiringLeadSource(
        client=_FakeClassifierHackerNewsClient(),
        classifier=_FakeJobLeadClassifier(),  # type: ignore[arg-type]
    )

    leads = source.collect()

    assert [lead.external_id for lead in leads] == ["10"]
    assert leads[0].tags == ["contract", "remote"]
    assert leads[0].confidence == 0.88
    classification = leads[0].metadata["contractor_classification"]
    assert classification["method"] == "llm"
    assert classification["rationale"] == "Explicitly allows contract work."


def test_hacker_news_source_rejects_seeking_work_before_classifier() -> None:
    source = HackerNewsWhoIsHiringLeadSource(
        client=_FakeSeekingWorkHackerNewsClient(),
        classifier=_PositiveJobLeadClassifier(),  # type: ignore[arg-type]
    )

    assert source.collect() == []


def test_classifier_falls_back_without_second_llm_call_after_provider_failure() -> None:
    client = _FakeFailingStructuredClient()
    classifier = JobLeadClassifier(
        settings=SimpleNamespace(),
        client=client,
    )

    classification = classifier.classify("Acme | Contract API Engineer | Remote")

    assert classification.method == "heuristic"
    assert classification.is_contractor_friendly is True
    assert client.chat_create_calls == 0


def test_build_llm_client_uses_classifier_model_for_fireworks_direct(
    monkeypatch,
) -> None:
    class _OpenAIClient:
        pass

    monkeypatch.setattr(
        "five08.job_lead_sources.OpenAIClient",
        _OpenAIClient,
    )
    settings = SimpleNamespace(
        job_lead_classifier_enabled=True,
        job_lead_classifier_model="accounts/fireworks/models/kimi-k2p6",
        agent_fast_model=None,
        agent_fallback_model=None,
        openai_model=None,
        agent_fast_api_key=None,
        openai_api_key=None,
        agent_fast_base_url=None,
        openai_base_url=None,
        openai_direct_api_key=None,
        openai_api_key_direct=None,
        openai_direct_base_url=None,
        openai_direct_model=None,
        fireworks_api_key="fireworks-key",
        openrouter_api_key=None,
        job_lead_classifier_timeout_seconds=8.0,
    )

    client = _build_llm_client(settings)  # type: ignore[arg-type]

    assert client is not None
    assert client.providers[0].label == "fireworks-direct"
    assert client.providers[0].model == "accounts/fireworks/models/kimi-k2p6"
