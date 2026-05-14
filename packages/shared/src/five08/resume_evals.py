"""Local eval harness for resume profile extraction."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, Field

from five08.agent.evals import (
    AgentEvalModelProfile,
    list_eval_model_profiles,
    load_env_file,
    resolve_eval_model_profile,
)
from five08.crm_normalization import (
    ROLE_NORMALIZATION_MAP,
    normalize_roles,
    normalize_seniority,
)
from five08.document_text import extract_document_text
from five08.model_catalog import (
    model_chat_completion_options,
    model_cost_per_1m,
    model_pricing_source,
)
from five08.resume_extractor import ResumeExtractedProfile, ResumeProfileExtractor
from five08.tls import default_ca_bundle_path


_BASELINE_SYSTEM_PROMPT = """You are a careful resume extraction judge.
Return only valid JSON. Do not include markdown.

Extract only information supported by the supplied resume text. Include short
evidence snippets for non-trivial fields so a human reviewer can validate your
baseline quickly.

Output schema:
{
  "name": "candidate full name or null",
  "email": "primary email or null",
  "additional_emails": [],
  "phone": "E.164 phone with leading + or null",
  "primary_roles": ["canonical CRM role labels"],
  "seniority_level": "junior|midlevel|senior|staff|unknown",
  "skills": ["important concrete skills"],
  "current_title": "current or most recent title or null",
  "recent_titles": [],
  "current_location_raw": "raw current location string or null",
  "current_location_source": "header|explicit_field|current_role|based_in_statement|other|null",
  "current_location_evidence": "short quote or null",
  "address_city": "city or null",
  "address_state": "state/region or null",
  "address_country": "country or null",
  "timezone": "IANA timezone or null",
  "github_username": "username or null",
  "linkedin_url": "normalized url or null",
  "website_links": [],
  "social_links": [],
  "summary": "brief candidate profile summary",
  "confidence": 0.0,
  "evidence": {"field_name": ["short quote"]}
}

Production extractor contract:
- primary_roles must use our canonical CRM role vocabulary only:
  developer, data scientist, program manager, product manager, designer,
  user research, biz dev, marketing.
- Do not emit language/framework-specific roles like C# Developer, Ruby on
  Rails Developer, React Developer, Backend Engineer, or AI Product Designer.
  Put those details in skills/current_title/recent_titles instead.
- Engineering titles of any kind should usually map to developer. Product
  design titles should map to designer. Product leadership/PM titles should map
  to product manager. Include at most 2 canonical roles.
- seniority_level must use our enum exactly: junior, midlevel, senior, staff,
  unknown. Map lead/principal/executive/director-style engineering scope to
  senior or staff as appropriate; never output lead, principal, or executive.
- Extract LinkedIn profile URLs into linkedin_url when present.
- Extract GitHub profile usernames into github_username when present. Return the
  username only, not the URL and not @.
- Extract phone when present. Normalize to E.164 with a leading + when country
  can be inferred; otherwise preserve the explicit source value and evidence.
- For location, fill address_city/address_state/address_country/timezone when
  supported by resume text. Prefer the header/contact block; e.g. "CA, United
  States" means address_state=California and address_country=United States,
  "Leeds, NY" means address_city=Leeds, address_state=New York,
  address_country=United States, and "Taipei, Taiwan" means address_city=Taipei,
  address_country=Taiwan.
- Include current_location_raw/current_location_source/current_location_evidence
  when any current location is found. Include evidence under each populated
  location field key, not just a generic "location" key.
- Include evidence for populated phone, github_username, linkedin_url,
  primary_roles, seniority_level, and skills.
"""


class ResumeEvalObserved(BaseModel):
    """One resume extraction observation."""

    id: str
    path: str
    filename: str
    model: str
    provider: str
    status: str
    text_length: int
    latency_ms: int
    time_to_first_response_ms: int = 0
    llm_success: bool
    llm_fallback_reason: str | None = None
    token_usage: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    confidence: float | None = None
    score: float
    field_checks: dict[str, bool] = Field(default_factory=dict)
    extracted_profile: dict[str, Any] | None = None
    error: str | None = None


class ResumeEvalReport(BaseModel):
    """Resume extraction eval report for one model/provider profile."""

    version: str = "resume-extraction-observed.v1"
    generated_at: str
    input_dir: str
    model: str
    provider: str
    summary: dict[str, Any]
    resumes: list[ResumeEvalObserved]


class ResumeBaselineObserved(BaseModel):
    """One strong-model baseline annotation for a resume."""

    id: str
    path: str
    filename: str
    judge_model: str
    status: str
    text_length: int
    latency_ms: int
    time_to_first_response_ms: int = 0
    token_usage: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    baseline: dict[str, Any] | None = None
    error: str | None = None


class ResumeBaselineReport(BaseModel):
    """Reviewable strong-model baseline report."""

    version: str = "resume-extraction-baseline.v1"
    generated_at: str
    input_dir: str
    judge_model: str
    summary: dict[str, Any]
    resumes: list[ResumeBaselineObserved]


def run_resume_eval_suite(
    *,
    input_dir: Path,
    model: str = "primary",
    include_profile_payload: bool = True,
    max_tokens: int = 2000,
    timeout_seconds: float | None = None,
) -> ResumeEvalReport:
    """Run resume extraction against a local directory of resume files."""
    profile = resolve_eval_model_profile(model)
    if not profile.configured:
        raise RuntimeError(f"Eval profile {model} is missing credentials")
    if profile.live_provider != "openai_compatible":
        raise RuntimeError(
            f"Resume extraction evals require an OpenAI-compatible profile; "
            f"{model} uses {profile.live_provider}"
        )
    if not profile.live_api_key or not profile.live_model:
        raise RuntimeError(f"Eval profile {model} is not configured for resume evals")

    files = _collect_resume_files(input_dir)
    extractor = ResumeProfileExtractor(
        api_key=profile.live_api_key,
        base_url=profile.live_base_url,
        model=profile.live_model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    started = time.perf_counter()
    observations = [
        _run_resume_file(
            path=path,
            profile=profile,
            extractor=extractor,
            include_profile_payload=include_profile_payload,
        )
        for path in files
    ]
    hard_failures = sum(1 for item in observations if item.status == "failed")
    llm_successes = sum(1 for item in observations if item.llm_success)
    scores = [item.score for item in observations]
    latencies = [item.latency_ms for item in observations]
    costs = [
        item.estimated_cost_usd
        for item in observations
        if item.estimated_cost_usd is not None
    ]
    return ResumeEvalReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        input_dir=str(input_dir),
        model=model,
        provider=profile.label,
        summary={
            "resumes": len(observations),
            "hard_failures": hard_failures,
            "llm_successes": llm_successes,
            "llm_fallbacks": len(observations) - hard_failures - llm_successes,
            "llm_success_rate": _rate(llm_successes, len(observations)),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "total_elapsed_ms": _elapsed_ms(started),
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
            if latencies
            else None,
            "max_latency_ms": max(latencies) if latencies else None,
            "estimated_cost_usd": round(sum(costs), 6) if costs else None,
            "pricing_source": model_pricing_source(),
        },
        resumes=observations,
    )


def generate_resume_baseline_suite(
    *,
    input_dir: Path,
    judge_model: str = "gpt-5.5",
    max_tokens: int = 2500,
    timeout_seconds: float = 60.0,
) -> ResumeBaselineReport:
    """Generate reviewable strong-model baseline annotations for local resumes."""
    api_key = _direct_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY_DIRECT is required for baseline generation")
    files = _collect_resume_files(input_dir)
    started = time.perf_counter()
    observations = [
        _generate_resume_baseline(
            path=path,
            api_key=api_key,
            judge_model=judge_model,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        for path in files
    ]
    failures = sum(1 for item in observations if item.status == "failed")
    latencies = [item.latency_ms for item in observations]
    costs = [
        item.estimated_cost_usd
        for item in observations
        if item.estimated_cost_usd is not None
    ]
    return ResumeBaselineReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        input_dir=str(input_dir),
        judge_model=judge_model,
        summary={
            "resumes": len(observations),
            "succeeded": len(observations) - failures,
            "failed": failures,
            "total_elapsed_ms": _elapsed_ms(started),
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1)
            if latencies
            else None,
            "max_latency_ms": max(latencies) if latencies else None,
            "estimated_cost_usd": round(sum(costs), 6) if costs else None,
            "pricing_source": model_pricing_source(),
        },
        resumes=observations,
    )


def write_resume_report(report: ResumeEvalReport, *, output_dir: Path) -> None:
    """Write JSON and Markdown reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"local.{report.model}"
    (output_dir / f"observed.{stem}.json").write_text(
        report.model_dump_json(indent=2) + "\n"
    )
    (output_dir / f"score.{stem}.md").write_text(render_resume_markdown_report(report))


def write_baseline_report(report: ResumeBaselineReport, *, output_dir: Path) -> None:
    """Write baseline JSON and a compact review Markdown report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    model_slug = _slug(report.judge_model)
    stem = f"baseline.local.{model_slug}"
    (output_dir / f"{stem}.json").write_text(report.model_dump_json(indent=2) + "\n")
    (output_dir / f"{stem}.md").write_text(render_baseline_markdown_report(report))


def render_resume_markdown_report(report: ResumeEvalReport) -> str:
    """Render a compact Markdown resume extraction scorecard."""
    lines = [
        f"# Resume Extraction Eval: {report.model}",
        "",
        f"- Provider: {report.provider}",
        f"- Input dir: `{report.input_dir}`",
        f"- Resumes: {report.summary['resumes']}",
        f"- Hard failures: {report.summary['hard_failures']}",
        f"- LLM successes: {report.summary['llm_successes']}",
        f"- LLM fallbacks: {report.summary['llm_fallbacks']}",
        f"- LLM success rate: {report.summary['llm_success_rate']}",
        f"- Avg score: {report.summary['avg_score']}",
        f"- Estimated cost USD: {_money(report.summary.get('estimated_cost_usd'))}",
        f"- Total elapsed ms: {report.summary.get('total_elapsed_ms', '-')}",
        f"- P50 latency ms: {report.summary.get('p50_latency_ms', '-')}",
        f"- P95 latency ms: {report.summary.get('p95_latency_ms', '-')}",
        f"- Avg latency ms: {report.summary['avg_latency_ms']}",
        f"- Max latency ms: {report.summary['max_latency_ms']}",
        f"- Pricing source: {report.summary.get('pricing_source', '-')}",
        "",
        "| Resume | Status | Score | LLM | Confidence | Missing fields | Cost USD | TTFR ms | Latency ms |",
        "| --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: |",
    ]
    for item in report.resumes:
        missing = [name for name, passed in item.field_checks.items() if not passed]
        lines.append(
            "| "
            + " | ".join(
                [
                    item.filename.replace("|", "\\|"),
                    item.status,
                    f"{item.score:.2f}",
                    "yes" if item.llm_success else "fallback",
                    f"{item.confidence:.2f}" if item.confidence is not None else "-",
                    ", ".join(missing) if missing else "-",
                    _money(item.estimated_cost_usd),
                    str(item.time_to_first_response_ms),
                    str(item.latency_ms),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_baseline_markdown_report(report: ResumeBaselineReport) -> str:
    """Render baselines in a human-reviewable Markdown table."""
    lines = [
        f"# Resume Extraction Baseline: {report.judge_model}",
        "",
        f"- Input dir: `{report.input_dir}`",
        f"- Resumes: {report.summary['resumes']}",
        f"- Succeeded: {report.summary['succeeded']}",
        f"- Failed: {report.summary['failed']}",
        f"- Estimated cost USD: {_money(report.summary.get('estimated_cost_usd'))}",
        f"- Total elapsed ms: {report.summary.get('total_elapsed_ms', '-')}",
        f"- P50 latency ms: {report.summary.get('p50_latency_ms', '-')}",
        f"- P95 latency ms: {report.summary.get('p95_latency_ms', '-')}",
        f"- Avg latency ms: {report.summary['avg_latency_ms']}",
        f"- Pricing source: {report.summary.get('pricing_source', '-')}",
        "",
        "| Resume | Status | Name | Email | Roles | Seniority | Skills | Missing Evidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.resumes:
        baseline = item.baseline or {}
        missing_evidence = _missing_baseline_evidence(baseline)
        lines.append(
            "| "
            + " | ".join(
                [
                    item.filename.replace("|", "\\|"),
                    item.status,
                    _cell(baseline.get("name")),
                    _cell(baseline.get("email")),
                    _cell(baseline.get("primary_roles")),
                    _cell(baseline.get("seniority_level")),
                    _cell(baseline.get("skills"), limit=5),
                    ", ".join(missing_evidence) if missing_evidence else "-",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def list_resume_eval_profiles() -> list[AgentEvalModelProfile]:
    """Return configured profiles supported by the current resume extractor."""
    return [
        profile
        for profile in list_eval_model_profiles()
        if profile.live_provider == "openai_compatible"
    ]


def default_resume_eval_input_dir() -> Path:
    """Return the ignored local resume fixture drop directory."""
    return (
        Path(__file__).resolve().parents[4]
        / "tests"
        / "evals"
        / "resume-extraction"
        / "fixtures"
        / "local-resumes"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for local resume extraction evals."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path, default=default_resume_eval_input_dir()
    )
    parser.add_argument("--model", "--profile", dest="model", default="primary")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/evals/resume-extraction/reports"),
    )
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument(
        "--generate-baseline",
        action="store_true",
        help="Generate reviewable strong-model baseline annotations instead of extractor scores.",
    )
    parser.add_argument("--judge-model", default="gpt-5.5")
    parser.add_argument("--judge-max-tokens", type=int, default=2500)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument(
        "--no-profile-payload",
        action="store_true",
        help="Do not write extracted profile values into JSON reports.",
    )
    args = parser.parse_args(argv)

    if not args.no_env_file:
        load_env_file(args.env_file)

    if args.list_profiles:
        for profile in list_resume_eval_profiles():
            status = "configured" if profile.configured else "missing credentials"
            print(f"{profile.id}\t{status}\t{profile.label}")
            if profile.live_model:
                print(f"  model: {profile.live_model}")
        return 0

    if args.generate_baseline:
        baseline_report = generate_resume_baseline_suite(
            input_dir=args.input_dir,
            judge_model=args.judge_model,
            max_tokens=args.judge_max_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        write_baseline_report(baseline_report, output_dir=args.output_dir)
        if args.json:
            print(baseline_report.model_dump_json(indent=2))
        else:
            print(render_baseline_markdown_report(baseline_report))
        return 1 if baseline_report.summary["failed"] else 0

    requested_models = _resolve_requested_models(args.model)
    if not requested_models:
        print("No configured OpenAI-compatible resume eval profiles found.")
        return 2

    reports: list[ResumeEvalReport] = []
    skipped: list[str] = []
    for model in requested_models:
        profile = resolve_eval_model_profile(model)
        if profile.live_provider != "openai_compatible":
            skipped.append(f"{model} ({profile.live_provider})")
            continue
        report = run_resume_eval_suite(
            input_dir=args.input_dir,
            model=model,
            include_profile_payload=not args.no_profile_payload,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        write_resume_report(report, output_dir=args.output_dir)
        reports.append(report)

    if args.json:
        print(json.dumps([report.model_dump(mode="json") for report in reports]))
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            print(render_resume_markdown_report(report))
        if skipped:
            print("Skipped unsupported profiles: " + ", ".join(skipped))

    return 1 if any(report.summary["hard_failures"] for report in reports) else 0


def _run_resume_file(
    *,
    path: Path,
    profile: AgentEvalModelProfile,
    extractor: ResumeProfileExtractor,
    include_profile_payload: bool,
) -> ResumeEvalObserved:
    started = time.perf_counter()
    try:
        text = _extract_text(path)
        extracted = extractor.extract(text)
        checks = _field_checks(extracted)
        score = _score_checks(checks, extracted)
        latency_ms = _elapsed_ms(started)
        token_usage = extracted.llm_usage
        return ResumeEvalObserved(
            id=path.stem,
            path=str(path),
            filename=path.name,
            model=profile.live_model or profile.id,
            provider=profile.label,
            status="succeeded",
            text_length=len(text),
            latency_ms=latency_ms,
            time_to_first_response_ms=latency_ms,
            llm_success=extracted.llm_fallback_reason is None,
            llm_fallback_reason=extracted.llm_fallback_reason,
            token_usage=token_usage,
            estimated_cost_usd=_estimate_model_cost_usd(
                profile.live_model or profile.id,
                token_usage,
            ),
            confidence=extracted.confidence,
            score=score,
            field_checks=checks,
            extracted_profile=(
                extracted.model_dump(mode="json") if include_profile_payload else None
            ),
        )
    except Exception as exc:
        latency_ms = _elapsed_ms(started)
        return ResumeEvalObserved(
            id=path.stem,
            path=str(path),
            filename=path.name,
            model=profile.live_model or profile.id,
            provider=profile.label,
            status="failed",
            text_length=0,
            latency_ms=latency_ms,
            time_to_first_response_ms=latency_ms,
            llm_success=False,
            score=0.0,
            error=f"{type(exc).__name__}: {exc}",
        )


def _generate_resume_baseline(
    *,
    path: Path,
    api_key: str,
    judge_model: str,
    max_tokens: int,
    timeout_seconds: float,
) -> ResumeBaselineObserved:
    started = time.perf_counter()
    try:
        text = _extract_text(path)
        baseline, token_usage = _call_openai_baseline_judge(
            api_key=api_key,
            model=judge_model,
            text=text,
            filename=path.name,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )
        latency_ms = _elapsed_ms(started)
        return ResumeBaselineObserved(
            id=path.stem,
            path=str(path),
            filename=path.name,
            judge_model=judge_model,
            status="succeeded",
            text_length=len(text),
            latency_ms=latency_ms,
            time_to_first_response_ms=latency_ms,
            token_usage=token_usage,
            estimated_cost_usd=_estimate_model_cost_usd(judge_model, token_usage),
            baseline=baseline,
        )
    except Exception as exc:
        latency_ms = _elapsed_ms(started)
        return ResumeBaselineObserved(
            id=path.stem,
            path=str(path),
            filename=path.name,
            judge_model=judge_model,
            status="failed",
            text_length=0,
            latency_ms=latency_ms,
            time_to_first_response_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


def _call_openai_baseline_judge(
    *,
    api_key: str,
    model: str,
    text: str,
    filename: str,
    max_tokens: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, int]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _BASELINE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "filename": filename,
                        "resume_text": text[:20000],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {"type": "json_object"},
    }
    model_options = model_chat_completion_options(model, purpose="baseline")
    max_tokens_parameter = model_options.get("max_tokens_parameter")
    if isinstance(max_tokens_parameter, str) and max_tokens_parameter:
        payload[max_tokens_parameter] = max_tokens
        reasoning_effort = model_options.get("reasoning_effort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        verbosity = model_options.get("verbosity")
        if isinstance(verbosity, str) and verbosity:
            payload["verbosity"] = verbosity
        if model_options.get("supports_temperature", True):
            payload["temperature"] = 0
    else:
        payload["max_tokens"] = max_tokens
        payload["temperature"] = 0

    response = _post_openai_with_retries(
        payload=payload,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    raw = str(data["choices"][0]["message"]["content"])
    return (
        _normalize_baseline_payload(_parse_json_object(raw)),
        _usage_from_mapping(data.get("usage")),
    )


def _post_openai_with_retries(
    *,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: float,
) -> requests.Response:
    retryable_statuses = {408, 409, 429, 500, 502, 503, 504}
    last_response: requests.Response | None = None
    for attempt_index in range(4):
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
            verify=default_ca_bundle_path(),
        )
        if response.status_code not in retryable_statuses:
            return response
        last_response = response
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 0.0
        else:
            delay = min(2.0 * (2**attempt_index), 15.0)
        time.sleep(delay)
    return last_response if last_response is not None else response


def _collect_resume_files(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Resume input dir does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Resume input path is not a directory: {input_dir}")
    files = [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".txt"}
    ]
    if not files:
        raise FileNotFoundError(f"No resume files found in {input_dir}")
    return files


def _extract_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return path.read_text(errors="replace").strip()
    return extract_document_text(path.read_bytes(), filename=path.name).strip()


def _field_checks(profile: ResumeExtractedProfile) -> dict[str, bool]:
    return {
        "name": bool(profile.name or profile.first_name or profile.last_name),
        "email": bool(profile.email or profile.additional_emails),
        "roles": bool(profile.primary_roles),
        "skills": bool(profile.skills or profile.skill_attrs),
        "seniority": bool(
            profile.seniority_level and profile.seniority_level != "unknown"
        ),
        "location": bool(
            profile.address_city
            or profile.address_state
            or profile.address_country
            or profile.timezone
            or profile.current_location_raw
        ),
        "links": bool(
            profile.github_username
            or profile.linkedin_url
            or profile.website_links
            or profile.social_links
        ),
        "confidence": profile.confidence >= 0.5,
    }


def _score_checks(
    checks: dict[str, bool],
    profile: ResumeExtractedProfile,
) -> float:
    weights = {
        "name": 2.0,
        "email": 2.0,
        "roles": 1.5,
        "skills": 1.5,
        "seniority": 1.0,
        "location": 1.0,
        "links": 1.0,
        "confidence": 1.0,
    }
    total = sum(weights.values())
    earned = sum(weight for key, weight in weights.items() if checks.get(key))
    if profile.llm_fallback_reason is not None:
        earned *= 0.75
    return round(earned / total, 4)


def _resolve_requested_models(value: str) -> list[str]:
    requested = [item.strip() for item in value.split(",") if item.strip()]
    if requested and requested != ["all"]:
        return requested
    return [profile.id for profile in list_resume_eval_profiles() if profile.configured]


def _direct_openai_api_key() -> str | None:
    import os

    value = os.environ.get("OPENAI_API_KEY_DIRECT")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object")
    return payload


def _normalize_baseline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    allowed_roles = set(ROLE_NORMALIZATION_MAP.values())
    normalized_roles = [
        role
        for role in normalize_roles(
            normalized.get("primary_roles"),
            ROLE_NORMALIZATION_MAP,
        )
        if role in allowed_roles
    ]
    normalized["primary_roles"] = normalized_roles[:2]
    normalized["seniority_level"] = (
        normalize_seniority(
            normalized.get("seniority_level"),
            empty_as_unknown=True,
        )
        or "unknown"
    )
    return normalized


def _cell(value: Any, *, limit: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        items = [str(item) for item in value if str(item).strip()]
        if not items:
            return "-"
        suffix = "" if len(items) <= limit else f" +{len(items) - limit}"
        return ", ".join(items[:limit]).replace("|", "\\|") + suffix
    text = str(value).strip()
    return text.replace("|", "\\|") if text else "-"


def _missing_baseline_evidence(baseline: dict[str, Any]) -> list[str]:
    evidence = baseline.get("evidence")
    if not isinstance(evidence, dict):
        return ["all"] if baseline else []

    missing: list[str] = []
    requirements = {
        "primary_roles": ["primary_roles", "role_rationale", "current_title"],
        "seniority_level": ["seniority_level", "current_title", "recent_titles"],
        "skills": ["skills"],
        "phone": ["phone"],
        "github_username": ["github_username"],
        "linkedin_url": ["linkedin_url"],
    }
    for field, evidence_keys in requirements.items():
        if baseline.get(field) and not any(evidence.get(key) for key in evidence_keys):
            missing.append(field)

    has_location = any(
        baseline.get(key)
        for key in [
            "current_location_raw",
            "address_city",
            "address_state",
            "address_country",
            "timezone",
        ]
    )
    has_location_evidence = any(
        evidence.get(key)
        for key in [
            "location",
            "current_location_raw",
            "current_location_evidence",
            "address_city",
            "address_state",
            "address_country",
            "timezone",
        ]
    )
    if has_location and not has_location_evidence:
        missing.append("location")
    return missing


def _money(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{value:.6f}"
    return "-"


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _usage_from_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    input_tokens = _usage_int(value, "prompt_tokens", "input_tokens")
    output_tokens = _usage_int(value, "completion_tokens", "output_tokens")
    total_tokens = _usage_int(value, "total_tokens")
    details = value.get("prompt_tokens_details") or value.get("input_tokens_details")
    cached_tokens = (
        _usage_int(details, "cached_tokens") if isinstance(details, dict) else 0
    )
    return {
        "requests": 1,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _usage_int(source: dict[str, Any], *names: str) -> int:
    for name in names:
        value = source.get(name)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _estimate_model_cost_usd(
    model: str,
    usage: dict[str, int] | None,
) -> float | None:
    if not usage:
        return None
    prices = model_cost_per_1m(model)
    if prices is None:
        return None
    input_tokens = usage.get("input_tokens", 0)
    cached_tokens = min(usage.get("cached_input_tokens", 0), input_tokens)
    billable_input_tokens = max(input_tokens - cached_tokens, 0)
    output_tokens = usage.get("output_tokens", 0)
    cost = (
        billable_input_tokens * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000
    return round(cost, 8)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
