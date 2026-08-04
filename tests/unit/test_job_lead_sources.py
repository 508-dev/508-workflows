"""Tests for external job lead source adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import five08.job_lead_sources as job_lead_sources
from five08.job_lead_sources import (
    HackerNewsThread,
    HackerNewsWhoIsHiringLeadSource,
    JobLeadClassifier,
    JobLeadClassification,
    JobLeadLLMClassificationResponse,
    _build_llm_client,
    _classification_from_llm_response,
    classify_contractor_lead,
    classify_contractor_lead_heuristic,
    html_to_text,
)
from five08.job_channels import JobPostingType
from five08.job_leads import JobLeadInput


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
                        "<p>Website: https://acme.example/"
                        "<p>We need a 1099 contractor for Python APIs."
                        '<p>Apply: <a href="https://acme.example/jobs">link</a>'
                        "<p>Contact: hiring@acme.example"
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
        self.parse_kwargs: dict[str, object] | None = None
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(parse=self._parse),
            )
        )
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _parse(self, **kwargs: object) -> object:
        self.parse_kwargs = kwargs
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


def test_heuristic_rejects_full_time_role_with_customer_contract() -> None:
    classification = classify_contractor_lead_heuristic(
        "Anori Tech | Embedded Rust Engineer | Hamburg, Germany | HYBRID | "
        "Full-time | http://anoritech.com/ Apply here: "
        "https://nice-channel-658.notion.site/Embedded-Rust-Engineer-m-f-d-"
        "3834b6f765ee81078002ffd5f2f5e767 We're funded with our first large "
        "customer contract. Direct contact: stefan.akatyschew@anoritech.com"
    )

    assert classification.is_contractor_friendly is False
    assert classification.posting_type is JobPostingType.FULL_TIME
    assert classification.tags == ["full-time"]
    assert classification.apply_url == (
        "https://nice-channel-658.notion.site/Embedded-Rust-Engineer-m-f-d-"
        "3834b6f765ee81078002ffd5f2f5e767"
    )
    assert classification.contact_email == "stefan.akatyschew@anoritech.com"


def test_heuristic_recognizes_full_time_header_with_smart_contracts() -> None:
    classification = classify_contractor_lead_heuristic(
        "Category Labs | https://www.category.xyz/ | Remote and NYC | Full Time | "
        "$200K USD+\nCategory Labs builds a high-performance EVM for smart "
        "contracts. Senior Software Engineer (C++ / Rust)."
    )

    assert classification.is_contractor_friendly is False
    assert classification.posting_type is JobPostingType.FULL_TIME
    assert classification.tags == ["full-time"]
    assert (
        classification.rationale
        == "Explicit full-time employment with no contract option."
    )


def test_heuristic_accepts_role_open_to_full_time_or_contract() -> None:
    classification = classify_contractor_lead_heuristic(
        "Acme | Engineer | Full-time or contract | Remote"
    )

    assert classification.is_contractor_friendly is True
    assert classification.posting_type is JobPostingType.PART_TIME_OR_FULL_TIME
    assert classification.tags == ["contract", "full-time"]
    assert classification.rationale == (
        "Explicitly allows full-time and part-time or contract work."
    )


def test_heuristic_accepts_common_contract_job_phrasings() -> None:
    for text in (
        "Acme | Engineer | Remote | Contract",
        "Acme | Contract | Remote",
        "Acme | Engineer | 6 month contract | Remote",
        "Acme | Engineer | B2B contract | Remote",
        "Acme | Engineer | Consulting contract | Remote",
        "Acme | Engineer | B2B contracting | Remote",
        "Acme | Engineer | Remote | Contract. We are hiring now.",
        "Acme | Engineer | Remote | CONTRACT - build APIs with us",
        "Acme needs an engineer. Work with us on contract.",
        "Acme is hiring an engineer on a six month contract.",
    ):
        classification = classify_contractor_lead_heuristic(text)
        assert classification.is_contractor_friendly is True, text
        assert classification.posting_type is JobPostingType.PART_TIME, text
        assert any("contract" in tag for tag in classification.tags), text


def test_heuristic_respects_negated_employment_terms() -> None:
    full_time_only = classify_contractor_lead_heuristic(
        "Acme | Full-time only | No contractors"
    )
    contract_only = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Not a full-time role"
    )

    assert full_time_only.is_contractor_friendly is False
    assert full_time_only.posting_type is JobPostingType.FULL_TIME
    assert full_time_only.tags == ["full-time"]
    assert contract_only.is_contractor_friendly is True
    assert contract_only.posting_type is JobPostingType.PART_TIME
    assert contract_only.tags == ["contract"]


def test_heuristic_handles_non_adjacent_employment_negation() -> None:
    for text in (
        "Acme | Contract role | We are not hiring full-time",
        "Acme | Contract role | We do not offer full-time employment",
    ):
        classification = classify_contractor_lead_heuristic(text)
        assert classification.is_contractor_friendly is True
        assert classification.posting_type is JobPostingType.PART_TIME
        assert classification.tags == ["contract"]

    no_contractors = classify_contractor_lead_heuristic(
        "Acme | Full-time role | Contractors will not be considered"
    )
    assert no_contractors.is_contractor_friendly is False
    assert no_contractors.posting_type is JobPostingType.FULL_TIME
    assert no_contractors.tags == ["full-time"]


def test_heuristic_does_not_treat_contrast_or_unrelated_negation_as_exclusion() -> None:
    for text in (
        "Acme is not just hiring contract engineers; full-time roles are open too.",
        "Acme is not only seeking contract engineers.",
        "We can't wait to hire contractors.",
        "We are not sure whether our contract engineer will work remotely.",
        "Not only is this full-time, contract work is also available.",
    ):
        classification = classify_contractor_lead_heuristic(text)
        assert classification.is_contractor_friendly is True, text
        assert "contract" in classification.tags, text


def test_heuristic_rejects_commercial_contract_sentence_variants() -> None:
    for business_context in (
        "we signed a contract with our first customer",
        "we secured our first contract",
        "our customer awarded us a major contract",
    ):
        classification = classify_contractor_lead_heuristic(
            f"Acme | Full-time engineer | {business_context}"
        )
        assert classification.is_contractor_friendly is False
        assert classification.posting_type is JobPostingType.FULL_TIME
        assert classification.tags == ["full-time"]

    service_copy = classify_contractor_lead_heuristic(
        "Acme provides B2B contracting services to enterprise customers."
    )
    assert service_copy.is_contractor_friendly is False
    assert service_copy.tags == []


def test_heuristic_keeps_contract_work_with_unrelated_negation() -> None:
    classification = classify_contractor_lead_heuristic(
        "Acme | Contract work is not limited to US residents"
    )

    assert classification.is_contractor_friendly is True
    assert classification.posting_type is JobPostingType.PART_TIME
    assert classification.tags == ["contract"]


def test_contact_extraction_preserves_case_and_rejects_negative_context() -> None:
    positive = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Contact: Hiring.Team@Example.COM"
    )
    negative = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Do not email hiring@acme.example"
    )
    negative_with_modifier = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Please do not directly email jobs@acme.example"
    )
    negative_application_delivery = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Do not send applications by email to "
        "jobs@acme.example"
    )
    negative_no_email = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | No email: jobs@acme.example"
    )
    passive_negative = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Applications are not accepted by email: "
        "jobs@acme.example"
    )
    cannot_accept = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | We cannot accept applications via email: "
        "jobs@acme.example"
    )
    alternate_application = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Please apply on the site, not by email: "
        "jobs@acme.example"
    )
    negative_resume = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Do not email your resume to jobs@acme.example"
    )
    negative_us = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Don't email us at jobs@acme.example"
    )
    sole_email = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | hiring@acme.example"
    )

    assert positive.contact_email == "Hiring.Team@Example.COM"
    assert negative.contact_email is None
    assert negative_with_modifier.contact_email is None
    assert negative_application_delivery.contact_email is None
    assert negative_no_email.contact_email is None
    assert passive_negative.contact_email is None
    assert cannot_accept.contact_email is None
    assert alternate_application.contact_email is None
    assert negative_resume.contact_email is None
    assert negative_us.contact_email is None
    assert sole_email.contact_email == "hiring@acme.example"


def test_apply_url_extraction_rejects_truncated_candidates() -> None:
    ascii_truncated = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Apply: https://jobs.example/engineer..."
    )
    unicode_truncated = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Apply: https://jobs.example/engineer…"
    )

    assert ascii_truncated.apply_url is None
    assert unicode_truncated.apply_url is None


def test_apply_url_prefers_specific_role_path_over_contextual_homepage() -> None:
    classification = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Apply: https://acme.example/ "
        "Role details: https://acme.example/jobs/engineer"
    )

    assert classification.apply_url == "https://acme.example/jobs/engineer"

    careers_site = classify_contractor_lead_heuristic(
        "Acme | Contract engineer | Careers: https://careers.acme.example/ "
        "About us: https://acme.example/about"
    )
    assert careers_site.apply_url == "https://careers.acme.example/"


def test_llm_link_and_email_proposals_must_match_post_candidates() -> None:
    text = (
        "Acme | Contract Engineer | https://acme.example/ Apply here: "
        "https://jobs.example/acme-engineer Contact: hiring@acme.example"
    )
    response = JobLeadLLMClassificationResponse(
        is_contractor_friendly=True,
        posting_type="part_time",
        tags=["contract"],
        confidence=0.9,
        confidence_label="high",
        rationale="Explicit contract role.",
        apply_url="https://attacker.example/phishing",
        contact_email="attacker@example.com",
    )

    classification = _classification_from_llm_response(response, text)

    assert classification.is_contractor_friendly is True
    assert classification.apply_url == "https://jobs.example/acme-engineer"
    assert classification.contact_email == "hiring@acme.example"


def test_llm_rejection_is_not_overridden_by_contractor_posting_type() -> None:
    response = JobLeadLLMClassificationResponse(
        is_contractor_friendly=False,
        posting_type="part_time",
        tags=["contract"],
        confidence=0.9,
        confidence_label="high",
        rationale="Generic company contract, not a hiring arrangement.",
    )

    classification = _classification_from_llm_response(
        response,
        "Acme sells contract management software.",
    )

    assert classification.is_contractor_friendly is False


def test_llm_can_prefer_valid_in_post_link_and_email_candidates() -> None:
    text = (
        "Acme | Contract Engineer | https://jobs.example/role-a "
        "https://jobs.example/role-b first@acme.example second@acme.example"
    )
    response = JobLeadLLMClassificationResponse(
        is_contractor_friendly=True,
        posting_type="part_time",
        tags=["contract"],
        confidence=0.9,
        confidence_label="high",
        rationale="Explicit contract role.",
        apply_url="https://jobs.example/role-b",
        contact_email="second@acme.example",
    )

    classification = _classification_from_llm_response(response, text)

    assert classification.apply_url == "https://jobs.example/role-b"
    assert classification.contact_email == "second@acme.example"


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
    assert classification["contact_email"] == "hiring@acme.example"


def test_hacker_news_source_reports_thread_and_filter_counts() -> None:
    source = HackerNewsWhoIsHiringLeadSource(client=_FakeHackerNewsClient())

    source.collect()

    assert source.collection_report() == {
        "thread_found": True,
        "threads": [
            {
                "story_id": 48357725,
                "title": "Ask HN: Who is hiring? (June 2026)",
                "url": "https://news.ycombinator.com/item?id=48357725",
                "created_at": "2026-06-01T00:00:00+00:00",
                "comments_reported": 3,
                "potential_gigs_scraped": 2,
                "included": 1,
                "filtered_out": 1,
                "filter_reasons": {
                    "empty": 0,
                    "seeking_work": 1,
                    "not_contractor_friendly": 0,
                },
            }
        ],
        "potential_gigs_scraped": 2,
        "included": 1,
        "filtered_out": 1,
        "filter_reasons": {
            "empty": 0,
            "seeking_work": 1,
            "not_contractor_friendly": 0,
        },
    }


def test_hacker_news_source_reports_no_discovered_thread() -> None:
    source = HackerNewsWhoIsHiringLeadSource(
        client=SimpleNamespace(search_who_is_hiring_threads=lambda **_kwargs: []),  # type: ignore[arg-type]
    )

    assert source.collect() == []
    assert source.collection_report() == {
        "thread_found": False,
        "threads": [],
        "potential_gigs_scraped": 0,
        "included": 0,
        "filtered_out": 0,
        "filter_reasons": {
            "empty": 0,
            "seeking_work": 0,
            "not_contractor_friendly": 0,
        },
    }


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


def test_hacker_news_source_can_include_non_contractor_rows_for_refresh() -> None:
    source = HackerNewsWhoIsHiringLeadSource(
        client=_FakeClassifierHackerNewsClient(),
        classifier=_FakeJobLeadClassifier(),  # type: ignore[arg-type]
        include_non_contractor=True,
    )

    leads = source.collect()

    assert [lead.external_id for lead in leads] == ["10", "11"]
    assert leads[1].posting_type is JobPostingType.FULL_TIME
    classification = leads[1].metadata["contractor_classification"]
    assert classification["is_contractor_friendly"] is False


def test_scrape_refreshes_existing_non_contractor_without_inserting(
    monkeypatch,
) -> None:
    positive = JobLeadInput(
        source_key="hackernews_who_is_hiring",
        source_type="hackernews",
        external_id="10",
        source_url="https://news.ycombinator.com/item?id=10",
        title="Contract role",
        body_raw="Contract role",
        body_normalized="Contract role",
        metadata={"contractor_classification": {"is_contractor_friendly": True}},
    )
    negative = JobLeadInput(
        source_key="hackernews_who_is_hiring",
        source_type="hackernews",
        external_id="11",
        source_url="https://news.ycombinator.com/item?id=11",
        title="Full-time role",
        body_raw="Full-time role",
        body_normalized="Full-time role",
        metadata={"contractor_classification": {"is_contractor_friendly": False}},
    )
    adapter = SimpleNamespace(
        source_key="hackernews_who_is_hiring",
        collect=lambda: [positive, negative],
    )
    monkeypatch.setattr(
        job_lead_sources, "JobLeadClassifier", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        job_lead_sources, "build_job_lead_source", lambda *_args, **_kwargs: adapter
    )
    monkeypatch.setattr(
        job_lead_sources, "upsert_job_lead", lambda *_args: ("lead-10", True)
    )
    monkeypatch.setattr(
        job_lead_sources,
        "existing_job_lead_external_ids",
        lambda *_args, **_kwargs: {"11"},
    )
    refreshed: list[str] = []

    def update_existing(_settings: object, lead: JobLeadInput) -> str:
        refreshed.append(lead.external_id)
        return "lead-11"

    monkeypatch.setattr(job_lead_sources, "update_existing_job_lead", update_existing)

    result = job_lead_sources.scrape_job_leads(SimpleNamespace())  # type: ignore[arg-type]

    assert refreshed == ["11"]
    assert result["created"] == 1
    assert result["updated"] == 1
    assert result["lead_ids"] == ["lead-10", "lead-11"]


def test_scrape_skips_reviewed_contractor_friendly_lead(monkeypatch) -> None:
    lead = JobLeadInput(
        source_key="hackernews_who_is_hiring",
        source_type="hackernews",
        external_id="10",
        source_url="https://news.ycombinator.com/item?id=10",
        title="Contract role",
        body_raw="Contract role",
        body_normalized="Contract role",
        metadata={"contractor_classification": {"is_contractor_friendly": True}},
    )
    adapter = SimpleNamespace(
        source_key="hackernews_who_is_hiring",
        collect=lambda: [lead],
    )
    monkeypatch.setattr(
        job_lead_sources, "JobLeadClassifier", lambda **_kwargs: object()
    )
    monkeypatch.setattr(
        job_lead_sources, "build_job_lead_source", lambda *_args, **_kwargs: adapter
    )
    monkeypatch.setattr(
        job_lead_sources,
        "existing_job_lead_external_ids",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        job_lead_sources, "upsert_job_lead", lambda *_args: (None, False)
    )

    result = job_lead_sources.scrape_job_leads(SimpleNamespace())  # type: ignore[arg-type]

    assert result["created"] == 0
    assert result["updated"] == 0
    assert result["lead_ids"] == []


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
    assert client.parse_kwargs is not None
    assert client.parse_kwargs["temperature"] == 0
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
