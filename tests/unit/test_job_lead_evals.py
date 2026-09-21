"""Tests for the job-lead classification eval harness."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from pydantic import ValidationError

from five08.job_lead_evals import (
    DEFAULT_CORPUS_PATH,
    JobLeadEvalCase,
    JobLeadEvalReport,
    JobLeadLLMClassificationResponse,
    JobLeadEvalObservation,
    _direct_openai_api_key,
    _run_case,
    _run_jev,
    _run_luna,
    jev_questions,
    load_env_file,
    load_job_lead_eval_corpus,
    render_job_lead_eval_report,
    run_job_lead_eval_suite,
    summarize_profile,
)


class _FakeResponse:
    status_code = 200
    ok = True
    headers: dict[str, str] = {}

    def json(self) -> dict:
        return {
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "answers": {
                "contractor_friendly": {"type": "noul", "noul": 0.91},
                "posting_type": {
                    "type": "choice",
                    "choice": "part_time",
                    "confidence": 0.98,
                    "probabilities": {
                        "part_time": 0.98,
                        "full_time": 0.01,
                        "part_time_or_full_time": 0.01,
                        "unknown": 0.0,
                    },
                },
            },
            "usage": {
                "input_tokens": 450,
                "output_tokens": 73,
                "cost": 0.000019,
            },
        }


class _FakeSession:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def post(self, _url: str, **kwargs: object) -> _FakeResponse:
        self.payload = kwargs["json"]  # type: ignore[assignment]
        return _FakeResponse()


class _FlakySession(_FakeSession):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def post(self, _url: str, **kwargs: object) -> _FakeResponse:
        self.attempts += 1
        if self.attempts == 1:
            raise requests.Timeout("temporary timeout")
        return super().post(_url, **kwargs)


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.payload: dict | None = None
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(parse=self._parse),
            )
        )

    def _parse(self, **kwargs: object) -> SimpleNamespace:
        self.payload = kwargs
        parsed = JobLeadLLMClassificationResponse(
            is_contractor_friendly=True,
            posting_type="part_time",
            tags=["contract"],
            confidence=0.94,
            confidence_label="high",
            rationale="Explicit contract role.",
        )
        return SimpleNamespace(
            model="gpt-5.6-luna",
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "prompt_tokens": 400,
                    "prompt_tokens_details": {"cached_tokens": 100},
                    "completion_tokens": 50,
                    "total_tokens": 450,
                }
            ),
        )


def _case() -> JobLeadEvalCase:
    return JobLeadEvalCase(
        id="contract_001",
        group="core",
        text="Acme | Contract backend engineer | Remote",
        expected_posting_type="part_time",
        expected_contractor_friendly=True,
        tags=["contract"],
        rationale="Explicit contract role.",
    )


def test_checked_in_corpus_is_balanced_and_versioned() -> None:
    corpus = load_job_lead_eval_corpus(DEFAULT_CORPUS_PATH)

    assert corpus.version == "job-lead-classification.v1"
    assert len(corpus.cases) == 48
    assert Counter(case.expected_posting_type for case in corpus.cases) == {
        "part_time": 12,
        "part_time_or_full_time": 12,
        "full_time": 12,
        "unknown": 12,
    }
    assert Counter(case.group for case in corpus.cases) == {
        "core": 32,
        "challenge": 16,
    }


def test_corpus_rejects_inconsistent_derived_contractor_label() -> None:
    with pytest.raises(ValidationError, match="must be derived"):
        JobLeadEvalCase(
            id="bad_001",
            group="core",
            text="Full-time role",
            expected_posting_type="full_time",
            expected_contractor_friendly=True,
            rationale="Intentionally inconsistent.",
        )


def test_jev_contract_uses_atomic_typed_questions() -> None:
    questions = jev_questions()

    assert questions["contractor_friendly"]["type"] == "noul"
    assert questions["posting_type"]["type"] == "choice"
    assert set(questions["posting_type"]["criteria"]) == {
        "part_time",
        "full_time",
        "part_time_or_full_time",
        "unknown",
    }


def test_jev_response_is_normalized_without_raw_provider_output() -> None:
    session = _FakeSession()

    observation = _run_jev(
        case=_case(),
        repeat=1,
        session=session,  # type: ignore[arg-type]
        api_key="test-key",
        model="typesafe/jev-1.13",
        timeout_seconds=5.0,
        max_attempts=1,
        started=0.0,
    )

    assert session.payload is not None
    assert session.payload["state"] == _case().text
    assert "expected_posting_type" not in session.payload
    assert observation.predicted_contractor_friendly is True
    assert observation.predicted_posting_type == "part_time"
    assert observation.contractor_probability == 0.91
    assert observation.resolved_model == "typesafe/jev-1.13-20260917"
    assert observation.input_tokens == 450
    assert observation.output_tokens == 73
    assert observation.total_tokens == 523
    assert observation.cost_usd == 0.000019


def test_jev_retries_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FlakySession()
    monkeypatch.setattr("five08.job_lead_evals.time.sleep", lambda _delay: None)

    observation = _run_jev(
        case=_case(),
        repeat=1,
        session=session,  # type: ignore[arg-type]
        api_key="test-key",
        model="typesafe/jev-1.13",
        timeout_seconds=5.0,
        max_attempts=2,
        started=0.0,
    )

    assert session.attempts == 2
    assert observation.request_attempts == 2


def test_luna_uses_schema_parse_and_official_rate_estimate() -> None:
    client = _FakeOpenAIClient()

    observation = _run_luna(
        case=_case(),
        repeat=1,
        client=client,  # type: ignore[arg-type]
        model="gpt-5.6-luna",
        max_attempts=1,
        started=0.0,
    )

    assert client.payload is not None
    assert client.payload["response_format"] is JobLeadLLMClassificationResponse
    assert client.payload["max_completion_tokens"] == 700
    assert "temperature" not in client.payload
    assert "reasoning_effort" not in client.payload
    assert "verbosity" not in client.payload
    assert observation.predicted_contractor_friendly is True
    assert observation.predicted_posting_type == "part_time"
    assert observation.cached_input_tokens == 100
    assert observation.cost_usd == 0.000122


def test_luna_does_not_apply_luna_rates_to_custom_model() -> None:
    observation = _run_luna(
        case=_case(),
        repeat=1,
        client=_FakeOpenAIClient(),  # type: ignore[arg-type]
        model="gpt-4.1-mini",
        max_attempts=1,
        started=0.0,
    )

    assert observation.cost_usd is None


def test_exhausted_request_preserves_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = requests.Session()
    attempts = 0

    def timeout_post(*_args: object, **_kwargs: object) -> requests.Response:
        nonlocal attempts
        attempts += 1
        raise requests.Timeout("persistent timeout")

    monkeypatch.setattr(session, "post", timeout_post)
    monkeypatch.setattr("five08.job_lead_evals.time.sleep", lambda _delay: None)

    observation = _run_case(
        profile="jev",
        case=_case(),
        repeat=1,
        client=session,
        openrouter_api_key="test-key",
        openai_api_key=None,
        jev_model="typesafe/jev-1.13",
        llm_model="gpt-5.6-luna",
        timeout_seconds=5.0,
        max_attempts=2,
    )

    assert attempts == 2
    assert observation.request_attempts == 2
    assert observation.error == "Timeout: persistent timeout"
    summary = summarize_profile([observation], case_count=1)
    assert summary["usage"]["request_attempts"] == 2
    assert summary["usage"]["cost_usd"] is None
    assert summary["latency_ms"]["max"] == observation.latency_ms


def test_heuristic_suite_requires_no_provider_key() -> None:
    corpus = SimpleNamespace(
        version="job-lead-classification.v1",
        cases=[_case()],
    )

    report = run_job_lead_eval_suite(
        corpus=corpus,  # type: ignore[arg-type]
        profiles=["heuristic"],
        openrouter_api_key=None,
    )

    assert report.case_count == 1
    assert report.summary["heuristic"]["successful_calls"] == 1
    assert report.summary["heuristic"]["joint_accuracy"] == 1.0
    assert report.summary["heuristic"]["usage"]["cost_usd"] == 0.0


def test_summary_tracks_repeatability_and_confidence_gate() -> None:
    observations = [
        JobLeadEvalObservation(
            profile="jev",
            case_id="positive",
            group="core",
            repeat=repeat,
            expected_posting_type="part_time",
            expected_contractor_friendly=True,
            predicted_posting_type="part_time",
            predicted_contractor_friendly=True,
            contractor_probability=probability,
            latency_ms=200,
            cost_usd=0.00001,
        )
        for repeat, probability in [(1, 0.92), (2, 0.88), (3, 0.91)]
    ]
    observations.extend(
        JobLeadEvalObservation(
            profile="jev",
            case_id="negative",
            group="challenge",
            repeat=repeat,
            expected_posting_type="full_time",
            expected_contractor_friendly=False,
            predicted_posting_type="full_time",
            predicted_contractor_friendly=False,
            contractor_probability=probability,
            latency_ms=220,
            cost_usd=0.00001,
        )
        for repeat, probability in [(1, 0.08), (2, 0.11), (3, 0.09)]
    )

    summary = summarize_profile(observations, case_count=2)

    assert summary["contractor_f1"] == 1.0
    assert summary["posting_accuracy"] == 1.0
    assert summary["repeatability"]["stable_rate"] == 1.0
    assert summary["repeatability"]["max_probability_span"] == 0.04
    assert summary["confidence_thresholds"][-1]["coverage"] == 0.0
    assert summary["usage"]["cost_usd"] == 0.00006


def test_confidence_gate_counts_failed_calls_as_fallbacks() -> None:
    observations = [
        JobLeadEvalObservation(
            profile="jev",
            case_id="success",
            group="core",
            repeat=1,
            expected_posting_type="part_time",
            expected_contractor_friendly=True,
            predicted_posting_type="part_time",
            predicted_contractor_friendly=True,
            contractor_probability=0.9,
            latency_ms=200,
        ),
        JobLeadEvalObservation(
            profile="jev",
            case_id="failure",
            group="core",
            repeat=1,
            expected_posting_type="full_time",
            expected_contractor_friendly=False,
            latency_ms=200,
            error="provider unavailable",
        ),
    ]

    thresholds = summarize_profile(observations, case_count=2)["confidence_thresholds"]

    threshold_80 = next(item for item in thresholds if item["threshold"] == 0.8)
    assert threshold_80["accepted"] == 1
    assert threshold_80["coverage"] == 0.5


def test_failed_repeat_is_not_reported_as_stable() -> None:
    observations = [
        JobLeadEvalObservation(
            profile="jev",
            case_id="sometimes_fails",
            group="challenge",
            repeat=repeat,
            expected_posting_type="part_time",
            expected_contractor_friendly=True,
            predicted_posting_type="part_time",
            predicted_contractor_friendly=True,
            contractor_probability=0.9,
            latency_ms=200,
            cost_usd=0.00001,
        )
        for repeat in (1, 2)
    ]
    observations.append(
        JobLeadEvalObservation(
            profile="jev",
            case_id="sometimes_fails",
            group="challenge",
            repeat=3,
            expected_posting_type="part_time",
            expected_contractor_friendly=True,
            latency_ms=500,
            error="temporary provider failure",
        )
    )

    summary = summarize_profile(observations, case_count=1)
    repeatability = summary["repeatability"]

    assert repeatability["repeated_cases"] == 1
    assert repeatability["stable_cases"] == 0
    assert repeatability["incomplete_cases"] == 1
    assert repeatability["stable_rate"] == 0.0
    assert summary["latency_ms"]["max"] == 500
    assert summary["usage"]["cost_usd"] is None


def test_report_renders_every_mismatch_group() -> None:
    observations = [
        JobLeadEvalObservation(
            profile="heuristic",
            case_id=f"mismatch_{index:02d}",
            group="challenge",
            repeat=1,
            expected_posting_type="part_time",
            expected_contractor_friendly=True,
            predicted_posting_type="full_time",
            predicted_contractor_friendly=False,
            latency_ms=0,
        )
        for index in range(17)
    ]
    report = JobLeadEvalReport(
        evaluated_at=datetime.now(timezone.utc),
        runtime_revision=None,
        corpus_version="job-lead-classification.v1",
        corpus_path="corpus.json",
        case_count=len(observations),
        network_repeats=1,
        requested_models={"jev": "jev", "luna": "luna"},
        endpoints={"jev": "https://example.com", "luna": "https://example.com"},
        summary={
            "heuristic": summarize_profile(
                observations,
                case_count=len(observations),
            )
        },
        observations=observations,
    )

    markdown = render_job_lead_eval_report(report)

    assert "`mismatch_16`" in markdown
    assert "| heuristic |" in markdown
    assert "| deterministic |" in markdown


def test_report_marks_single_network_run_stability_unmeasured() -> None:
    observation = JobLeadEvalObservation(
        profile="jev",
        case_id="success",
        group="core",
        repeat=1,
        expected_posting_type="part_time",
        expected_contractor_friendly=True,
        predicted_posting_type="part_time",
        predicted_contractor_friendly=True,
        contractor_probability=0.9,
        latency_ms=200,
    )
    report = JobLeadEvalReport(
        evaluated_at=datetime.now(timezone.utc),
        runtime_revision=None,
        corpus_version="job-lead-classification.v1",
        corpus_path="corpus.json",
        case_count=1,
        network_repeats=1,
        requested_models={"jev": "jev", "luna": "luna"},
        endpoints={"jev": "https://example.com", "luna": "https://example.com"},
        summary={"jev": summarize_profile([observation], case_count=1)},
        observations=[observation],
    )

    markdown = render_job_lead_eval_report(report)

    assert "| jev |" in markdown
    assert "| unmeasured |" in markdown


def test_env_file_loader_does_not_override_exported_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=from-file\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "exported")

    load_env_file(env_file)

    assert __import__("os").environ["OPENROUTER_API_KEY"] == "exported"


def test_direct_openai_key_prefers_explicit_direct_conventions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "gateway-or-legacy")
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "legacy-direct")
    monkeypatch.setenv("OPENAI_DIRECT_API_KEY", "direct")

    assert _direct_openai_api_key() == "direct"

    monkeypatch.delenv("OPENAI_DIRECT_API_KEY")
    assert _direct_openai_api_key() == "legacy-direct"

    monkeypatch.delenv("OPENAI_API_KEY_DIRECT")
    assert _direct_openai_api_key() == "gateway-or-legacy"
