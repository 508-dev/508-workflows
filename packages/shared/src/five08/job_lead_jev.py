"""Shared Jev contract and OpenRouter transport for job-lead classification."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import requests

from five08.job_channels import JobPostingType
from five08.tls import default_ca_bundle_path

DEFAULT_JOB_LEAD_JEV_MODEL = "typesafe/jev-1.13"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class JobLeadJevRequestError(RuntimeError):
    """Carry provider attempt metadata while retaining a safe root cause."""

    def __init__(self, cause: Exception, *, request_attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.request_attempts = request_attempts


@dataclass(frozen=True)
class JobLeadJevDecision:
    """Normalized Jev decision without retaining the raw provider response."""

    requested_model: str
    resolved_model: str | None
    provider: str | None
    is_contractor_friendly: bool
    contractor_probability: float
    posting_type: JobPostingType
    posting_confidence: float | None
    posting_probabilities: dict[str, float]
    latency_ms: int
    request_attempts: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None


def job_lead_jev_questions() -> dict[str, dict[str, Any]]:
    """Return the versioned Jev decision contract used by eval and production."""

    return {
        "contractor_friendly": {
            "type": "noul",
            "instructions": (
                "Is this a direct employer or recruiter job posting that explicitly "
                "offers contract, contractor, freelance, consulting, fractional, "
                "1099, B2B contracting, or part-time work? Answer false for "
                "full-time employee-only roles, people seeking work, replies, closed "
                "roles, and company products or customer contracts."
            ),
        },
        "posting_type": {
            "type": "choice",
            "instructions": (
                "What employment arrangement does this direct job posting explicitly offer?"
            ),
            "criteria": {
                "part_time": (
                    "Contract, contractor, freelance, consulting, fractional, 1099, "
                    "B2B contracting, or part-time work, without a full-time option."
                ),
                "full_time": (
                    "Full-time or permanent employee work only, with no contract or "
                    "part-time option."
                ),
                "part_time_or_full_time": (
                    "Explicitly offers both full-time employment and contract, "
                    "freelance, consulting, or part-time work."
                ),
                "unknown": (
                    "Not a direct current job posting, or the employment arrangement "
                    "is not stated clearly."
                ),
            },
        },
    }


def classify_job_lead_with_jev(
    *,
    session: requests.Session,
    api_key: str,
    comment_text: str,
    model: str = DEFAULT_JOB_LEAD_JEV_MODEL,
    timeout_seconds: float = 4.0,
    max_attempts: int = 2,
    request_title: str = "508.dev Job Lead Shadow",
) -> JobLeadJevDecision:
    """Classify one job lead through OpenRouter's Jev Decisions endpoint."""

    started = time.perf_counter()
    body, attempts = _post_json_with_retries(
        session=session,
        api_key=api_key,
        payload={
            "model": model,
            "state": comment_text,
            "questions": job_lead_jev_questions(),
        },
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        request_title=request_title,
    )
    answers = _mapping(body.get("answers"), name="answers")
    contractor_answer = _mapping(
        answers.get("contractor_friendly"),
        name="answers.contractor_friendly",
    )
    posting_answer = _mapping(
        answers.get("posting_type"),
        name="answers.posting_type",
    )
    contractor_probability = _probability(
        contractor_answer.get("noul"),
        name="answers.contractor_friendly.noul",
    )
    posting_type = _posting_type(
        posting_answer.get("choice"),
        name="answers.posting_type.choice",
    )
    posting_probabilities = _probabilities(
        posting_answer.get("probabilities"),
        name="answers.posting_type.probabilities",
    )
    usage = _usage(body.get("usage"))
    return JobLeadJevDecision(
        requested_model=model,
        resolved_model=_optional_text(body.get("model")),
        provider=_optional_text(body.get("provider")),
        is_contractor_friendly=contractor_probability >= 0.5,
        contractor_probability=contractor_probability,
        posting_type=posting_type,
        posting_confidence=_optional_probability(posting_answer.get("confidence")),
        posting_probabilities=posting_probabilities,
        latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        request_attempts=attempts,
        input_tokens=usage["input_tokens"],
        cached_input_tokens=usage["cached_input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        cost_usd=usage["cost_usd"],
    )


def _post_json_with_retries(
    *,
    session: requests.Session,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    max_attempts: int,
    request_title: str,
) -> tuple[dict[str, Any], int]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    response: requests.Response | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = session.post(
                OPENROUTER_DECISIONS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-OpenRouter-Title": request_title,
                },
                json=payload,
                timeout=timeout_seconds,
                verify=default_ca_bundle_path(),
            )
        except requests.RequestException as exc:
            if attempt == max_attempts:
                raise JobLeadJevRequestError(
                    exc,
                    request_attempts=attempt,
                ) from exc
            time.sleep(min(float(2 ** (attempt - 1)), 8.0))
            continue
        if (
            response.status_code not in _RETRYABLE_STATUS_CODES
            or attempt == max_attempts
        ):
            break
        time.sleep(_retry_delay(response, attempt))
    if response is None:
        raise JobLeadJevRequestError(
            RuntimeError("OpenRouter request did not produce a response"),
            request_attempts=max_attempts,
        )
    try:
        body = response.json()
    except ValueError as exc:
        cause = ValueError(f"OpenRouter returned non-JSON HTTP {response.status_code}")
        raise JobLeadJevRequestError(cause, request_attempts=attempt) from exc
    if not response.ok:
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = _optional_text(error.get("message")) or "unknown error"
        else:
            message = _optional_text(error) or "unknown error"
        raise JobLeadJevRequestError(
            RuntimeError(f"OpenRouter HTTP {response.status_code}: {message[:300]}"),
            request_attempts=attempt,
        )
    if not isinstance(body, dict):
        raise JobLeadJevRequestError(
            ValueError("OpenRouter response must be a JSON object"),
            request_attempts=attempt,
        )
    return body, attempt


def _retry_delay(response: requests.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 15.0))
        except ValueError:
            pass
    return min(float(2 ** (attempt - 1)), 8.0)


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _posting_type(value: Any, *, name: str) -> JobPostingType:
    try:
        return JobPostingType(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} has unsupported value: {value!r}") from exc


def _probability(value: Any, *, name: str) -> float:
    probability = _optional_probability(value)
    if probability is None:
        raise ValueError(f"{name} must be a probability")
    return probability


def _optional_probability(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    probability = float(value)
    return probability if 0.0 <= probability <= 1.0 else None


def _probabilities(value: Any, *, name: str) -> dict[str, float]:
    source = _mapping(value, name=name)
    probabilities = {
        str(key): probability
        for key, raw in source.items()
        if (probability := _optional_probability(raw)) is not None
    }
    expected = {posting_type.value for posting_type in JobPostingType}
    if not expected.issubset(probabilities):
        raise ValueError(f"{name} must include all posting types")
    return probabilities


def _usage(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    input_tokens = _integer(source.get("input_tokens", source.get("prompt_tokens")))
    raw_details = source.get("input_tokens_details") or source.get(
        "prompt_tokens_details"
    )
    details = raw_details if isinstance(raw_details, dict) else {}
    cached_input_tokens = _integer(details.get("cached_tokens"))
    output_tokens = _integer(
        source.get("output_tokens", source.get("completion_tokens"))
    )
    total_tokens = _integer(source.get("total_tokens")) or input_tokens + output_tokens
    cost = source.get("cost")
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": _optional_float(cost),
    }


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
