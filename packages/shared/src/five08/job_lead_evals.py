"""Golden-corpus evals for contractor-friendly job-lead classification."""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import requests
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from five08.job_lead_jev import (
    DEFAULT_JOB_LEAD_JEV_MODEL,
    OPENROUTER_DECISIONS_URL,
    JobLeadJevRequestError,
    classify_job_lead_with_jev,
    job_lead_jev_questions,
)
from five08.job_lead_sources import (
    JobLeadClassifier,
    JobLeadLLMClassificationResponse,
    _classification_from_llm_response,
    classify_contractor_lead_heuristic,
)
from five08.model_catalog import model_chat_completion_options

PostingType = Literal[
    "part_time",
    "full_time",
    "part_time_or_full_time",
    "unknown",
]
EvalProfile = Literal["heuristic", "jev", "luna"]

DEFAULT_CORPUS_PATH = (
    Path(str(files("five08.data"))) / "job-lead-classification-v1.json"
)
DEFAULT_OUTPUT_DIR = Path("tests/evals/job-lead-classification/reports")
DEFAULT_JEV_MODEL = DEFAULT_JOB_LEAD_JEV_MODEL
DEFAULT_LLM_MODEL = "gpt-5.6-luna"
OPENAI_BASE_URL = "https://api.openai.com/v1"
LUNA_INPUT_COST_PER_1M = 0.20
LUNA_CACHED_INPUT_COST_PER_1M = 0.02
LUNA_CACHE_WRITE_COST_PER_1M = 0.25
LUNA_OUTPUT_COST_PER_1M = 1.20
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_POSTING_TYPES: tuple[PostingType, ...] = (
    "part_time",
    "full_time",
    "part_time_or_full_time",
    "unknown",
)


class _RequestFailure(RuntimeError):
    """Carry the number of provider attempts without exposing raw responses."""

    def __init__(self, cause: Exception, *, request_attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.request_attempts = request_attempts


class JobLeadEvalCase(BaseModel):
    """One manually labeled classification example."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    group: Literal["core", "challenge"]
    text: str = Field(min_length=1)
    expected_posting_type: PostingType
    expected_contractor_friendly: bool
    tags: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_derived_label(self) -> JobLeadEvalCase:
        expected = self.expected_posting_type in {
            "part_time",
            "part_time_or_full_time",
        }
        if self.expected_contractor_friendly != expected:
            raise ValueError(
                "expected_contractor_friendly must be derived from expected_posting_type"
            )
        return self


class JobLeadEvalCorpus(BaseModel):
    """Versioned, reviewable job-lead classification corpus."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["job-lead-classification.v1"]
    description: str
    cases: list[JobLeadEvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> JobLeadEvalCorpus:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


class JobLeadEvalObservation(BaseModel):
    """Normalized result from one classifier invocation."""

    model_config = ConfigDict(extra="forbid")

    profile: EvalProfile
    case_id: str
    group: Literal["core", "challenge"]
    repeat: int = Field(ge=1)
    requested_model: str | None = None
    resolved_model: str | None = None
    provider: str | None = None
    expected_posting_type: PostingType
    expected_contractor_friendly: bool
    predicted_posting_type: PostingType | None = None
    predicted_contractor_friendly: bool | None = None
    contractor_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    classification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    posting_probabilities: dict[str, float] = Field(default_factory=dict)
    latency_ms: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    request_attempts: int = Field(default=1, ge=1)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.error is None
            and self.predicted_posting_type is not None
            and self.predicted_contractor_friendly is not None
        )


class JobLeadEvalReport(BaseModel):
    """Serializable eval report."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["job-lead-eval-report.v1"] = "job-lead-eval-report.v1"
    evaluated_at: datetime
    runtime_revision: str | None
    corpus_version: str
    corpus_path: str
    case_count: int
    network_repeats: int
    requested_models: dict[str, str]
    endpoints: dict[str, str]
    summary: dict[str, dict[str, Any]]
    observations: list[JobLeadEvalObservation]


def load_job_lead_eval_corpus(path: Path = DEFAULT_CORPUS_PATH) -> JobLeadEvalCorpus:
    """Load and validate the checked-in golden corpus."""

    return JobLeadEvalCorpus.model_validate_json(path.read_text())


def jev_questions() -> dict[str, dict[str, Any]]:
    """Return the stable Jev decision contract for this eval."""

    return job_lead_jev_questions()


def run_job_lead_eval_suite(
    *,
    corpus: JobLeadEvalCorpus,
    corpus_path: Path = DEFAULT_CORPUS_PATH,
    profiles: Sequence[EvalProfile],
    openrouter_api_key: str | None,
    openai_api_key: str | None = None,
    jev_model: str = DEFAULT_JEV_MODEL,
    llm_model: str = DEFAULT_LLM_MODEL,
    llm_base_url: str = OPENAI_BASE_URL,
    network_repeats: int = 1,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
    progress: Callable[[str], None] | None = None,
) -> JobLeadEvalReport:
    """Run the requested classifiers against the same labeled corpus."""

    if network_repeats < 1:
        raise ValueError("network_repeats must be at least 1")
    if "jev" in profiles and not openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY is required for Jev evals")
    if "luna" in profiles and not openai_api_key:
        raise ValueError("A direct OpenAI API key is required for Luna evals")

    observations: list[JobLeadEvalObservation] = []
    for profile in profiles:
        repeats = 1 if profile == "heuristic" else network_repeats
        total = len(corpus.cases) * repeats
        completed = 0
        client: requests.Session | OpenAI | None
        if profile == "jev":
            client = requests.Session()
        elif profile == "luna":
            client = OpenAI(
                api_key=openai_api_key,
                base_url=llm_base_url,
                timeout=timeout_seconds,
                max_retries=0,
            )
        else:
            client = None
        try:
            for repeat in range(1, repeats + 1):
                for case in corpus.cases:
                    completed += 1
                    if progress and (
                        completed == 1 or completed % 10 == 0 or completed == total
                    ):
                        progress(f"{profile}: {completed}/{total}")
                    observations.append(
                        _run_case(
                            profile=profile,
                            case=case,
                            repeat=repeat,
                            client=client,
                            openrouter_api_key=openrouter_api_key,
                            openai_api_key=openai_api_key,
                            jev_model=jev_model,
                            llm_model=llm_model,
                            timeout_seconds=timeout_seconds,
                            max_attempts=max_attempts,
                        )
                    )
        finally:
            if client is not None:
                client.close()

    summary: dict[str, dict[str, Any]] = {
        profile: summarize_profile(
            [item for item in observations if item.profile == profile],
            case_count=len(corpus.cases),
        )
        for profile in profiles
    }
    return JobLeadEvalReport(
        evaluated_at=datetime.now(timezone.utc),
        runtime_revision=_git_revision(),
        corpus_version=corpus.version,
        corpus_path=str(corpus_path),
        case_count=len(corpus.cases),
        network_repeats=network_repeats,
        requested_models={"jev": jev_model, "luna": llm_model},
        endpoints={
            "jev": OPENROUTER_DECISIONS_URL,
            "luna": f"{llm_base_url.rstrip('/')}/chat/completions",
        },
        summary=summary,
        observations=observations,
    )


def _run_case(
    *,
    profile: EvalProfile,
    case: JobLeadEvalCase,
    repeat: int,
    client: requests.Session | OpenAI | None,
    openrouter_api_key: str | None,
    openai_api_key: str | None,
    jev_model: str,
    llm_model: str,
    timeout_seconds: float,
    max_attempts: int,
) -> JobLeadEvalObservation:
    started = time.perf_counter()
    try:
        if profile == "heuristic":
            return _run_heuristic(case=case, repeat=repeat, started=started)
        if client is None:
            raise RuntimeError("Provider session is unavailable")
        if profile == "jev":
            if openrouter_api_key is None:
                raise RuntimeError("OpenRouter API key is unavailable")
            if not isinstance(client, requests.Session):
                raise RuntimeError("Jev requires a Requests session")
            return _run_jev(
                case=case,
                repeat=repeat,
                session=client,
                api_key=openrouter_api_key,
                model=jev_model,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                started=started,
            )
        if openai_api_key is None:
            raise RuntimeError("OpenAI API key is unavailable")
        if not isinstance(client, OpenAI):
            raise RuntimeError("Luna requires an OpenAI client")
        return _run_luna(
            case=case,
            repeat=repeat,
            client=client,
            model=llm_model,
            max_attempts=max_attempts,
            started=started,
        )
    except Exception as exc:
        return _base_observation(
            profile=profile,
            case=case,
            repeat=repeat,
            latency_ms=_elapsed_ms(started),
            requested_model={"jev": jev_model, "luna": llm_model}.get(profile),
            request_attempts=(
                exc.request_attempts
                if isinstance(exc, _RequestFailure | JobLeadJevRequestError)
                else 1
            ),
            error=_safe_error(exc),
        )


def _run_heuristic(
    *,
    case: JobLeadEvalCase,
    repeat: int,
    started: float,
) -> JobLeadEvalObservation:
    classification = classify_contractor_lead_heuristic(case.text)
    return _base_observation(
        profile="heuristic",
        case=case,
        repeat=repeat,
        latency_ms=_elapsed_ms(started),
        predicted_posting_type=classification.posting_type.value,
        predicted_contractor_friendly=classification.is_contractor_friendly,
        classification_confidence=classification.confidence,
    )


def _run_jev(
    *,
    case: JobLeadEvalCase,
    repeat: int,
    session: requests.Session,
    api_key: str,
    model: str,
    timeout_seconds: float,
    max_attempts: int,
    started: float,
) -> JobLeadEvalObservation:
    decision = classify_job_lead_with_jev(
        session=session,
        api_key=api_key,
        comment_text=case.text,
        model=model,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        request_title="508.dev Job Lead Eval",
    )
    return _base_observation(
        profile="jev",
        case=case,
        repeat=repeat,
        requested_model=model,
        resolved_model=decision.resolved_model,
        provider=decision.provider,
        predicted_posting_type=decision.posting_type.value,
        predicted_contractor_friendly=decision.is_contractor_friendly,
        contractor_probability=decision.contractor_probability,
        classification_confidence=decision.posting_confidence,
        posting_probabilities=decision.posting_probabilities,
        latency_ms=_elapsed_ms(started),
        request_attempts=decision.request_attempts,
        input_tokens=decision.input_tokens,
        cached_input_tokens=decision.cached_input_tokens,
        output_tokens=decision.output_tokens,
        total_tokens=decision.total_tokens,
        cost_usd=decision.cost_usd,
    )


def _run_luna(
    *,
    case: JobLeadEvalCase,
    repeat: int,
    client: OpenAI,
    model: str,
    max_attempts: int,
    started: float,
) -> JobLeadEvalObservation:
    payload: dict[str, Any] = {
        "model": model,
        "messages": JobLeadClassifier._messages(case.text),
        "response_format": JobLeadLLMClassificationResponse,
    }
    options = model_chat_completion_options(model)
    max_tokens_parameter = options.get("max_tokens_parameter")
    if isinstance(max_tokens_parameter, str) and max_tokens_parameter:
        payload[max_tokens_parameter] = 700
    else:
        payload["max_tokens"] = 700
    if options.get("supports_temperature", True):
        payload["temperature"] = 0

    completion, attempts = _openai_parse_with_retries(
        client=client,
        payload=payload,
        max_attempts=max_attempts,
    )
    if not completion.choices:
        raise ValueError("OpenAI chat response has no choices")
    response = completion.choices[0].message.parsed
    if not isinstance(response, JobLeadLLMClassificationResponse):
        raise ValueError("OpenAI structured response did not contain a parsed model")
    classification = _classification_from_llm_response(response, case.text)
    usage_payload = (
        completion.usage.model_dump() if completion.usage is not None else {}
    )
    usage = _usage(usage_payload)
    if usage["cost_usd"] is None and _has_known_luna_pricing(model):
        usage["cost_usd"] = _luna_cost_usd(
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
            output_tokens=usage["output_tokens"],
        )
    return _base_observation(
        profile="luna",
        case=case,
        repeat=repeat,
        requested_model=model,
        resolved_model=_optional_text(completion.model),
        provider="OpenAI",
        predicted_posting_type=classification.posting_type.value,
        predicted_contractor_friendly=classification.is_contractor_friendly,
        classification_confidence=classification.confidence,
        latency_ms=_elapsed_ms(started),
        request_attempts=attempts,
        **usage,
    )


def _base_observation(
    *,
    profile: EvalProfile,
    case: JobLeadEvalCase,
    repeat: int,
    latency_ms: int,
    requested_model: str | None = None,
    resolved_model: str | None = None,
    provider: str | None = None,
    predicted_posting_type: str | None = None,
    predicted_contractor_friendly: bool | None = None,
    contractor_probability: float | None = None,
    classification_confidence: float | None = None,
    posting_probabilities: dict[str, float] | None = None,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float | None = None,
    request_attempts: int = 1,
    error: str | None = None,
) -> JobLeadEvalObservation:
    normalized_posting_type = (
        _posting_type(predicted_posting_type, name="predicted_posting_type")
        if predicted_posting_type is not None
        else None
    )
    return JobLeadEvalObservation(
        profile=profile,
        case_id=case.id,
        group=case.group,
        repeat=repeat,
        requested_model=requested_model,
        resolved_model=resolved_model,
        provider=provider,
        expected_posting_type=case.expected_posting_type,
        expected_contractor_friendly=case.expected_contractor_friendly,
        predicted_posting_type=normalized_posting_type,
        predicted_contractor_friendly=predicted_contractor_friendly,
        contractor_probability=contractor_probability,
        classification_confidence=classification_confidence,
        posting_probabilities=posting_probabilities or {},
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        request_attempts=request_attempts,
        error=error,
    )


def summarize_profile(
    observations: Sequence[JobLeadEvalObservation],
    *,
    case_count: int,
) -> dict[str, Any]:
    """Calculate exact-label, binary, stability, latency, and cost metrics."""

    successful = [item for item in observations if item.succeeded]
    contractor_correct = sum(
        item.predicted_contractor_friendly == item.expected_contractor_friendly
        for item in successful
    )
    posting_correct = sum(
        item.predicted_posting_type == item.expected_posting_type for item in successful
    )
    joint_correct = sum(
        item.predicted_contractor_friendly == item.expected_contractor_friendly
        and item.predicted_posting_type == item.expected_posting_type
        for item in successful
    )
    true_positive = sum(
        item.expected_contractor_friendly and item.predicted_contractor_friendly is True
        for item in successful
    )
    false_positive = sum(
        not item.expected_contractor_friendly
        and item.predicted_contractor_friendly is True
        for item in successful
    )
    false_negative = sum(
        item.expected_contractor_friendly
        and item.predicted_contractor_friendly is False
        for item in successful
    )
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _f1(precision, recall)
    posting_labels = {
        label: _label_metrics(successful, label) for label in _POSTING_TYPES
    }
    posting_macro_f1 = round(
        statistics.fmean(metrics["f1"] for metrics in posting_labels.values()), 4
    )
    latencies = [item.latency_ms for item in observations]
    cost_values = [item.cost_usd for item in successful if item.cost_usd is not None]
    profile = observations[0].profile if observations else None
    expected_api_results = len(successful) if profile in {"jev", "luna"} else 0
    total_cost: float | None
    if profile == "heuristic":
        total_cost = 0.0
    elif (
        expected_api_results > 0
        and len(successful) == len(observations)
        and len(cost_values) == expected_api_results
    ):
        total_cost = round(sum(cost_values), 8)
    else:
        total_cost = None

    by_group: dict[str, dict[str, Any]] = {}
    for group in ("core", "challenge"):
        items = [item for item in successful if item.group == group]
        by_group[group] = {
            "calls": len(items),
            "contractor_accuracy": _ratio(
                sum(
                    item.predicted_contractor_friendly
                    == item.expected_contractor_friendly
                    for item in items
                ),
                len(items),
            ),
            "posting_accuracy": _ratio(
                sum(
                    item.predicted_posting_type == item.expected_posting_type
                    for item in items
                ),
                len(items),
            ),
            "joint_accuracy": _ratio(
                sum(
                    item.predicted_contractor_friendly
                    == item.expected_contractor_friendly
                    and item.predicted_posting_type == item.expected_posting_type
                    for item in items
                ),
                len(items),
            ),
        }

    grouped: dict[str, list[JobLeadEvalObservation]] = defaultdict(list)
    for item in observations:
        grouped[item.case_id].append(item)
    repeated_groups = [items for items in grouped.values() if len(items) > 1]
    stable_cases = sum(
        all(item.succeeded for item in items)
        and len(
            {
                (item.predicted_contractor_friendly, item.predicted_posting_type)
                for item in items
            }
        )
        == 1
        for items in repeated_groups
    )
    incomplete_cases = sum(
        not all(item.succeeded for item in items) for items in repeated_groups
    )
    probability_spans = [
        max(probabilities) - min(probabilities)
        for items in repeated_groups
        if len(
            probabilities := [
                item.contractor_probability
                for item in items
                if item.contractor_probability is not None
            ]
        )
        > 1
    ]
    probability_items: list[JobLeadEvalObservation] = []
    brier_inputs: list[tuple[float, bool]] = []
    for item in successful:
        probability = item.contractor_probability
        if probability is None:
            continue
        probability_items.append(item)
        brier_inputs.append((probability, item.expected_contractor_friendly))
    brier_score = (
        round(
            statistics.fmean(
                (probability - float(expected)) ** 2
                for probability, expected in brier_inputs
            ),
            6,
        )
        if brier_inputs
        else None
    )

    failures = _failure_examples(successful)
    return {
        "case_count": case_count,
        "calls": len(observations),
        "successful_calls": len(successful),
        "hard_failures": len(observations) - len(successful),
        "contractor_accuracy": _ratio(contractor_correct, len(successful)),
        "contractor_precision": precision,
        "contractor_recall": recall,
        "contractor_f1": f1,
        "contractor_false_positives": false_positive,
        "contractor_false_negatives": false_negative,
        "posting_accuracy": _ratio(posting_correct, len(successful)),
        "posting_macro_f1": posting_macro_f1,
        "posting_labels": posting_labels,
        "joint_accuracy": _ratio(joint_correct, len(successful)),
        "by_group": by_group,
        "repeatability": {
            "status": (
                "measured"
                if repeated_groups
                else "deterministic"
                if profile == "heuristic"
                else "unmeasured"
            ),
            "repeated_cases": len(repeated_groups),
            "stable_cases": stable_cases,
            "incomplete_cases": incomplete_cases,
            "stable_rate": (
                _ratio(stable_cases, len(repeated_groups)) if repeated_groups else None
            ),
            "mean_probability_span": (
                round(statistics.fmean(probability_spans), 6)
                if probability_spans
                else None
            ),
            "max_probability_span": (
                round(max(probability_spans), 6) if probability_spans else None
            ),
        },
        "brier_score": brier_score,
        "confidence_thresholds": _confidence_thresholds(observations),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 1) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "usage": {
            "input_tokens": sum(item.input_tokens for item in successful),
            "cached_input_tokens": sum(item.cached_input_tokens for item in successful),
            "cache_write_tokens": sum(item.cache_write_tokens for item in successful),
            "output_tokens": sum(item.output_tokens for item in successful),
            "total_tokens": sum(item.total_tokens for item in successful),
            "cost_usd": total_cost,
            "request_attempts": sum(item.request_attempts for item in observations),
        },
        "resolved_models": sorted(
            {item.resolved_model for item in successful if item.resolved_model}
        ),
        "providers": sorted({item.provider for item in successful if item.provider}),
        "failure_examples": failures,
        "error_examples": [
            {"case_id": item.case_id, "repeat": item.repeat, "error": item.error}
            for item in observations
            if not item.succeeded
        ][:12],
    }


def render_job_lead_eval_report(report: JobLeadEvalReport) -> str:
    """Render a compact, reviewable Markdown report."""

    profiles = set(report.summary)
    jev_model = report.requested_models.get("jev", "unknown")
    llm_model = report.requested_models.get("luna", "unknown")
    jev_endpoint = report.endpoints.get("jev", "unknown")
    llm_endpoint = report.endpoints.get("luna", "unknown")
    lines = [
        "# Job-lead classification evaluation",
        "",
        f"- Evaluated (UTC): `{report.evaluated_at.isoformat()}`",
        f"- Runtime revision: `{report.runtime_revision or 'unknown'}`",
        f"- Corpus: `{report.corpus_path}` ({report.case_count} cases)",
        f"- Network repeats per case: {report.network_repeats}",
    ]
    if "jev" in profiles:
        lines.append(f"- Jev: `{jev_model}` through `{jev_endpoint}`")
    if "luna" in profiles:
        lines.append(f"- LLM baseline: `{llm_model}` through `{llm_endpoint}`")
    lines.extend(
        [
            "",
            "## Results",
            "",
            "| Profile | Successful calls | Contractor F1 | Posting accuracy | Joint accuracy | Stable cases | Latency p50 / p95 / max | Input / cached / cache-write / output tokens | Cost |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for profile, summary in report.summary.items():
        stability = summary["repeatability"]
        stable = (
            f"{stability['stable_cases']}/{stability['repeated_cases']}"
            if stability["repeated_cases"]
            else stability["status"]
        )
        latency = summary["latency_ms"]
        cost = summary["usage"]["cost_usd"]
        lines.append(
            "| "
            + " | ".join(
                [
                    profile,
                    f"{summary['successful_calls']}/{summary['calls']}",
                    _percent(summary["contractor_f1"]),
                    _percent(summary["posting_accuracy"]),
                    _percent(summary["joint_accuracy"]),
                    stable,
                    f"{latency['p50']} / {latency['p95']} / {latency['max']} ms",
                    (
                        f"{summary['usage']['input_tokens']} / "
                        f"{summary['usage']['cached_input_tokens']} / "
                        f"{summary['usage']['cache_write_tokens']} / "
                        f"{summary['usage']['output_tokens']}"
                    ),
                    _money(cost),
                ]
            )
            + " |"
        )

    result_note = (
        "Joint accuracy requires both the contractor-friendly boolean and the "
        "four-way posting type to match the golden label."
    )
    if "heuristic" in profiles:
        result_note = (
            "The heuristic is local code, so its latency and zero cost are not an "
            f"API-to-API comparison. {result_note}"
        )
    lines.extend(
        [
            "",
            result_note,
            "",
            "## Core versus challenge cases",
            "",
            "| Profile | Core joint accuracy | Challenge joint accuracy | False positives | False negatives |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for profile, summary in report.summary.items():
        lines.append(
            f"| {profile} | {_percent(summary['by_group']['core']['joint_accuracy'])} "
            f"| {_percent(summary['by_group']['challenge']['joint_accuracy'])} "
            f"| {summary['contractor_false_positives']} "
            f"| {summary['contractor_false_negatives']} |"
        )

    jev_summary = report.summary.get("jev")
    if jev_summary and jev_summary.get("confidence_thresholds"):
        lines.extend(
            [
                "",
                "## Jev confidence gate",
                "",
                "A symmetric gate accepts positive decisions at or above the threshold, negative decisions at or below `1 - threshold`, and falls back for the middle band.",
                "",
                "| Threshold | Coverage | Accuracy when accepted | False positives | False negatives |",
                "| ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in jev_summary["confidence_thresholds"]:
            lines.append(
                f"| {item['threshold']:.2f} | {_percent(item['coverage'])} "
                f"| {_percent(item['accuracy'])} | {item['false_positives']} "
                f"| {item['false_negatives']} |"
            )
        lines.extend(
            [
                "",
                f"Jev contractor-probability Brier score: `{jev_summary['brier_score']}`. Lower is better.",
            ]
        )

    lines.extend(["", "## Classification mismatches", ""])
    any_failures = False
    for profile, summary in report.summary.items():
        failures = summary["failure_examples"]
        if not failures:
            continue
        any_failures = True
        lines.extend(
            [
                f"### {profile}",
                "",
                "| Case | Runs | Expected | Observed | Contractor probability |",
                "| --- | ---: | --- | --- | ---: |",
            ]
        )
        for item in failures:
            lines.append(
                f"| `{item['case_id']}` | {item['count']} "
                f"| {item['expected']} | {item['observed']} "
                f"| {item['contractor_probability']} |"
            )
        lines.append("")
    if not any_failures:
        lines.append("No classification mismatches were observed.")

    lines.extend(
        [
            "",
            "## Method and limitations",
            "",
            "- The corpus is a balanced, synthetic challenge set derived from the production label contract. It deliberately over-represents negation, commercial uses of the word `contract`, non-posts, and prompt-injection-like text; it does not estimate live HN prevalence.",
            "- Golden labels are exact and scoring is deterministic. No model judges another model.",
        ]
    )
    if "jev" in profiles:
        lines.extend(
            [
                f"- Jev uses the requested `{jev_model}` model through `{jev_endpoint}`. Provider-resolved model IDs are retained in the JSON observation report.",
                "- Jev cost is provider-reported.",
            ]
        )
    if "luna" in profiles:
        lines.extend(
            [
                f"- The LLM baseline uses the requested `{llm_model}` model and the production job-lead prompt and schema through `{llm_endpoint}`.",
                "- The LLM baseline's self-reported classification confidence is retained as diagnostic metadata, but it is not treated as a calibrated contractor probability or used in the Jev confidence-gate analysis.",
                "- For GPT-5.6 Luna only, missing cost is estimated from successful retained token usage at the official [$0.20/M input, $0.02/M cached input, $0.25/M cache-write, and $1.20/M output rates](https://developers.openai.com/api/docs/models/gpt-5.6-luna); missing cost for a custom `--llm-model` or any profile with unpriced failed calls remains unavailable.",
            ]
        )
    lines.extend(
        [
            "- Latency includes successful and failed calls.",
            "- Raw observations are generated under the gitignored reports directory; this Markdown summary intentionally excludes provider payloads and secrets.",
            "",
        ]
    )
    return "\n".join(lines)


def write_job_lead_eval_report(
    report: JobLeadEvalReport,
    *,
    output_dir: Path,
    summary_path: Path | None = None,
) -> None:
    """Write ignored detailed observations and an optional durable summary."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "observed.json").write_text(report.model_dump_json(indent=2) + "\n")
    markdown = render_job_lead_eval_report(report)
    (output_dir / "score.md").write_text(markdown)
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(markdown)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for the job-lead classification eval."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--profiles", default="heuristic,jev,luna")
    parser.add_argument("--jev-model", default=DEFAULT_JEV_MODEL)
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--llm-base-url", default=OPENAI_BASE_URL)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--summary-path", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_env_file:
        load_env_file(args.env_file)
    profiles = _parse_profiles(args.profiles)
    corpus = load_job_lead_eval_corpus(args.corpus)
    report = run_job_lead_eval_suite(
        corpus=corpus,
        corpus_path=args.corpus,
        profiles=profiles,
        openrouter_api_key=_env("OPENROUTER_API_KEY"),
        openai_api_key=_direct_openai_api_key(),
        jev_model=args.jev_model,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        network_repeats=args.repeats,
        timeout_seconds=args.timeout_seconds,
        max_attempts=args.max_attempts,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    if not args.no_write:
        write_job_lead_eval_report(
            report,
            output_dir=args.output_dir,
            summary_path=args.summary_path,
        )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_job_lead_eval_report(report))
    return 1 if any(item["hard_failures"] for item in report.summary.values()) else 0


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding exported values."""

    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def _openai_parse_with_retries(
    *,
    client: OpenAI,
    payload: dict[str, Any],
    max_attempts: int,
) -> tuple[Any, int]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    for attempt in range(1, max_attempts + 1):
        try:
            completion = client.beta.chat.completions.parse(**payload)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            retryable = status_code in _RETRYABLE_STATUS_CODES or type(
                exc
            ).__name__ in {
                "APIConnectionError",
                "APITimeoutError",
            }
            if not retryable or attempt == max_attempts:
                raise _RequestFailure(exc, request_attempts=attempt) from exc
            time.sleep(min(float(2 ** (attempt - 1)), 8.0))
            continue
        return completion, attempt
    raise RuntimeError("OpenAI request did not produce a response")


def _posting_type(value: Any, *, name: str) -> PostingType:
    if value not in _POSTING_TYPES:
        raise ValueError(f"{name} has unsupported value: {value!r}")
    return value


def _usage(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    input_tokens = _integer(source.get("input_tokens", source.get("prompt_tokens")))
    raw_details = source.get("input_tokens_details") or source.get(
        "prompt_tokens_details"
    )
    details = raw_details if isinstance(raw_details, dict) else {}
    cached_input_tokens = _integer(details.get("cached_tokens"))
    cache_write_tokens = _integer(details.get("cache_write_tokens"))
    output_tokens = _integer(
        source.get("output_tokens", source.get("completion_tokens"))
    )
    total_tokens = _integer(source.get("total_tokens")) or input_tokens + output_tokens
    cost = source.get("cost")
    cost_usd = (
        float(cost) if isinstance(cost, int | float | str) and _is_float(cost) else None
    )
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
    }


def _luna_cost_usd(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
) -> float:
    uncached_input_tokens = max(
        0,
        input_tokens - cached_input_tokens - cache_write_tokens,
    )
    return round(
        (
            uncached_input_tokens * LUNA_INPUT_COST_PER_1M
            + cached_input_tokens * LUNA_CACHED_INPUT_COST_PER_1M
            + cache_write_tokens * LUNA_CACHE_WRITE_COST_PER_1M
            + output_tokens * LUNA_OUTPUT_COST_PER_1M
        )
        / 1_000_000,
        10,
    )


def _has_known_luna_pricing(model: str) -> bool:
    model_id = model.rsplit("/", 1)[-1].casefold()
    return model_id == DEFAULT_LLM_MODEL or model_id.startswith(
        f"{DEFAULT_LLM_MODEL}-20"
    )


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return max(0, int(value))
    return 0


def _is_float(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, _RequestFailure | JobLeadJevRequestError):
        exc = exc.cause
    return f"{type(exc).__name__}: {str(exc)[:500]}"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def _label_metrics(
    observations: Sequence[JobLeadEvalObservation], label: PostingType
) -> dict[str, Any]:
    true_positive = sum(
        item.expected_posting_type == label and item.predicted_posting_type == label
        for item in observations
    )
    false_positive = sum(
        item.expected_posting_type != label and item.predicted_posting_type == label
        for item in observations
    )
    false_negative = sum(
        item.expected_posting_type == label and item.predicted_posting_type != label
        for item in observations
    )
    support = sum(item.expected_posting_type == label for item in observations)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    return {
        "support": support,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def _confidence_thresholds(
    observations: Sequence[JobLeadEvalObservation],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for threshold in (0.5, 0.7, 0.8, 0.9, 0.95):
        decisions: list[tuple[JobLeadEvalObservation, bool]] = []
        for item in observations:
            probability = item.contractor_probability
            if probability is None:
                continue
            if probability >= threshold:
                decisions.append((item, True))
            elif probability <= 1.0 - threshold:
                decisions.append((item, False))
        correct = sum(
            prediction == item.expected_contractor_friendly
            for item, prediction in decisions
        )
        false_positives = sum(
            prediction and not item.expected_contractor_friendly
            for item, prediction in decisions
        )
        false_negatives = sum(
            not prediction and item.expected_contractor_friendly
            for item, prediction in decisions
        )
        output.append(
            {
                "threshold": threshold,
                "accepted": len(decisions),
                "coverage": _ratio(len(decisions), len(observations)),
                "accuracy": _ratio(correct, len(decisions)),
                "false_positives": false_positives,
                "false_negatives": false_negatives,
            }
        )
    return output


def _failure_examples(
    observations: Sequence[JobLeadEvalObservation],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[JobLeadEvalObservation]] = defaultdict(list)
    for item in observations:
        if (
            item.predicted_contractor_friendly == item.expected_contractor_friendly
            and item.predicted_posting_type == item.expected_posting_type
        ):
            continue
        grouped[
            (
                item.case_id,
                item.predicted_contractor_friendly,
                item.predicted_posting_type,
            )
        ].append(item)
    failures: list[dict[str, Any]] = []
    for (case_id, predicted_friendly, predicted_type), items in grouped.items():
        probabilities = [
            item.contractor_probability
            for item in items
            if item.contractor_probability is not None
        ]
        failures.append(
            {
                "case_id": case_id,
                "count": len(items),
                "expected": (
                    f"{items[0].expected_posting_type}/"
                    f"{str(items[0].expected_contractor_friendly).lower()}"
                ),
                "observed": f"{predicted_type}/{str(predicted_friendly).lower()}",
                "contractor_probability": (
                    round(statistics.fmean(probabilities), 4) if probabilities else "-"
                ),
            }
        )
    return sorted(failures, key=lambda item: (item["case_id"], item["observed"]))


def _percentile(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _percent(value: Any) -> str:
    return f"{float(value) * 100:.1f}%" if isinstance(value, int | float) else "-"


def _money(value: Any) -> str:
    return f"${float(value):.6f}" if isinstance(value, int | float) else "unavailable"


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _parse_profiles(value: str) -> list[EvalProfile]:
    raw_profiles = [item.strip() for item in value.split(",") if item.strip()]
    allowed = {"heuristic", "jev", "luna"}
    invalid = [item for item in raw_profiles if item not in allowed]
    if invalid:
        raise ValueError(f"Unsupported eval profiles: {', '.join(invalid)}")
    if not raw_profiles:
        raise ValueError("At least one eval profile is required")
    return list(dict.fromkeys(raw_profiles))  # type: ignore[return-value]


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _direct_openai_api_key() -> str | None:
    return (
        _env("OPENAI_DIRECT_API_KEY")
        or _env("OPENAI_API_KEY_DIRECT")
        or _env("OPENAI_API_KEY")
    )


if __name__ == "__main__":
    raise SystemExit(main())
