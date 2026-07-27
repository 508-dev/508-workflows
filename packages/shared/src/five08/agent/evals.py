"""Fixture-driven eval harness for the Discord agent orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections.abc import Iterable, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import requests
from pydantic import BaseModel, Field, ValidationError

from five08.agent.models import (
    AgentContextSnippet,
    AgentIdentityContext,
    AgentModelSelection,
    AgentPlan,
    AgentResponse,
    AgentToolAction,
    ModelTier,
)
from five08.agent.model_routing import AgentModelConfig, AgentTierModelConfig
from five08.agent.orchestrator import AgentOrchestrator
from five08.agent.planner import (
    PLANNER_SYSTEM_PROMPT,
    build_planner_user_prompt,
    parse_planner_draft,
)
from five08.agent.tools import InMemoryTaskStore, ToolRegistry, ToolRuntimeConfig
from five08.model_catalog import (
    model_chat_completion_options,
    model_cost_per_1m,
    model_pricing_source,
)
from five08.tls import default_ca_bundle_path

EvalSuite = Literal["canonical", "weekly"]
EvalStatus = Literal["passed", "failed", "known_failure"]
EvalMode = Literal["deterministic", "live_planner"]
LiveProvider = Literal["openai_compatible", "anthropic"]

_LIVE_PLANNER_SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT


class AgentEvalSuiteConfig(BaseModel):
    """Fixture suite membership flags."""

    canonical: bool = True
    weekly: bool = True


class AgentEvalContext(BaseModel):
    """Actor context used to run a fixture through the orchestrator."""

    discord_user_id: str = "123"
    internal_user_id: str | None = None
    organization_id: str | None = "org-1"
    workspace_id: str | None = None
    project_id: str | None = None
    guild_id: str | None = "org-1"
    channel_id: str | None = "agent-eval"
    operation_id: str | None = "agent-eval-operation"
    thread_id: str | None = None
    parent_message_id: str | None = None
    response_destination_visibility: Literal["private", "public", "restricted"] = (
        "private"
    )
    roles: list[str] = Field(default_factory=lambda: ["Member"])
    scopes: list[str] = Field(default_factory=list)
    impersonation: bool = False
    interaction_id: str | None = "agent-eval-interaction"
    message_id: str | None = "agent-eval-message"
    context_snippets: list[AgentContextSnippet] = Field(default_factory=list)

    def to_identity_context(self) -> AgentIdentityContext:
        """Convert fixture context into the production request context model."""
        return AgentIdentityContext(**self.model_dump())


class AgentEvalSeedTask(BaseModel):
    """Seed task inserted before a fixture runs."""

    title: str
    project: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    organization_id: str | None = None
    created_by: str | None = None


class AgentEvalSeed(BaseModel):
    """Optional deterministic store seed for one fixture."""

    tasks: list[AgentEvalSeedTask] = Field(default_factory=list)


class AgentEvalThreadMessage(BaseModel):
    """One message in a Discord thread context fixture."""

    role: Literal["user", "assistant", "bot"]
    content: str


class AgentEvalRequest(BaseModel):
    """Request payload for an agent eval fixture."""

    message: str | None = None
    thread: list[AgentEvalThreadMessage] = Field(default_factory=list)

    def current_message(self) -> str:
        """Return the message under test, using the latest user thread turn."""
        if self.message is not None:
            return self.message
        for message in reversed(self.thread):
            if message.role == "user":
                return message.content
        return ""


class AgentEvalExpectedAction(BaseModel):
    """Expected action details for a fixture."""

    tool_name: str
    arguments: dict[str, Any] | None = None
    arguments_contains: dict[str, Any] | None = None
    required_scopes: list[str] | None = None
    requires_confirmation: bool | None = None


class AgentEvalExpect(BaseModel):
    """Deterministic expectations checked against an observed response."""

    status: str
    intent: str | None = None
    model_tier: str | None = None
    requires_confirmation: bool | None = None
    actions: list[AgentEvalExpectedAction] | None = None
    result_statuses: list[str] | None = None
    clarification_question: str | None = None
    message_contains: str | None = None


class AgentEvalKnownFailure(BaseModel):
    """Non-blocking failure marker for accepted gaps."""

    reason: str
    issue: str | None = None


class AgentEvalFixture(BaseModel):
    """Versioned Discord agent eval scenario."""

    version: Literal["discord-agent-trajectory.v1"]
    id: str
    description: str
    tags: list[str] = Field(default_factory=list)
    suite: AgentEvalSuiteConfig = Field(default_factory=AgentEvalSuiteConfig)
    context: AgentEvalContext = Field(default_factory=AgentEvalContext)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    stub_results: dict[str, dict[str, Any]] = Field(default_factory=dict)
    seed: AgentEvalSeed = Field(default_factory=AgentEvalSeed)
    request: AgentEvalRequest
    expect: AgentEvalExpect
    known_failure: AgentEvalKnownFailure | None = None


class AgentEvalCatalog(BaseModel):
    """Catalog of fixture filenames."""

    version: Literal["discord-agent-fixtures.v1"]
    schema_path: str = Field(alias="schema")
    scenarios: list[str]


class AgentEvalObservedAction(BaseModel):
    """Normalized observed action metadata."""

    tool_name: str
    arguments: dict[str, Any]
    risk: str
    requires_confirmation: bool
    required_scopes: list[str]
    summary: str


class AgentEvalObservedResult(BaseModel):
    """Normalized observed execution result."""

    tool_name: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AgentEvalObserved(BaseModel):
    """Normalized response observation for reports and comparisons."""

    id: str
    fixture_id: str
    description: str
    tags: list[str]
    request_message: str
    status: str
    message: str
    clarification_question: str | None = None
    intent: str | None = None
    model_tier: str | None = None
    model: dict[str, Any] | None = None
    latency_ms: int | None = None
    parse_success: bool | None = None
    parse_error: str | None = None
    raw_model_output: str | None = None
    token_usage: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    requires_confirmation: bool | None = None
    actions: list[AgentEvalObservedAction] = Field(default_factory=list)
    results: list[AgentEvalObservedResult] = Field(default_factory=list)


class AgentEvalCheck(BaseModel):
    """One deterministic check outcome."""

    name: str
    passed: bool
    expected: Any = None
    observed: Any = None


class AgentEvalScenarioResult(BaseModel):
    """One fixture's observed output and check list."""

    id: str
    fixture_id: str
    status: EvalStatus
    known_failure: AgentEvalKnownFailure | None = None
    checks: list[AgentEvalCheck]
    observed: AgentEvalObserved


class AgentEvalReport(BaseModel):
    """Top-level eval report."""

    version: Literal["discord-agent-observed.v1"] = "discord-agent-observed.v1"
    mode: EvalMode = "deterministic"
    generated_at: str
    suite: EvalSuite
    model: str
    summary: dict[str, int]
    metrics: dict[str, Any] = Field(default_factory=dict)
    scenarios: list[AgentEvalScenarioResult]


class AgentEvalModelProfile(BaseModel):
    """Model/provider profile used for eval comparison runs."""

    id: str
    label: str
    configured: bool
    notes: str | None = None
    live_provider: LiveProvider = "openai_compatible"
    live_model: str | None = None
    live_base_url: str | None = None
    live_api_key: str | None = Field(default=None, exclude=True)
    agent_model_config: AgentModelConfig


class LivePlannerActionDraft(BaseModel):
    """One tool action drafted by a live model."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None


class LivePlannerDraft(BaseModel):
    """Structured planner output requested from live models."""

    status: Literal["planned", "needs_clarification"]
    intent: str | None = None
    clarification_question: str | None = None
    actions: list[LivePlannerActionDraft] = Field(default_factory=list)


class LivePlannerCallResult(BaseModel):
    """Raw live-model planner call result."""

    draft: LivePlannerDraft | None = None
    raw_output: str | None = None
    latency_ms: int
    parse_success: bool
    error: str | None = None
    token_usage: dict[str, int] | None = None
    estimated_cost_usd: float | None = None


class _EvalToolRegistry(ToolRegistry):
    """Tool registry with deterministic fixture payloads for external tools."""

    def __init__(
        self,
        *args: Any,
        stub_results: dict[str, dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._stub_results = stub_results or {}

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        organization_id: str | None,
        actor_id: str | None,
        project_id: str | None = None,
        actor_scopes: set[str] | None = None,
    ) -> dict[str, Any]:
        if tool_name in self._stub_results:
            return deepcopy(self._stub_results[tool_name])
        return super().execute(
            tool_name,
            arguments,
            organization_id=organization_id,
            actor_id=actor_id,
            project_id=project_id,
            actor_scopes=actor_scopes,
        )


def default_eval_root() -> Path:
    """Return the repo-local Discord agent eval fixture root."""
    cwd_root = _find_repo_root(Path.cwd())
    if cwd_root is not None:
        return cwd_root / "tests" / "evals" / "discord-agent"
    package_root = _find_repo_root(Path(__file__).resolve())
    if package_root is not None:
        return package_root / "tests" / "evals" / "discord-agent"
    return Path.cwd() / "tests" / "evals" / "discord-agent"


def _find_repo_root(start: Path) -> Path | None:
    """Find a repository checkout root from a starting path."""
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
            return candidate
        if (candidate / "tests" / "evals").is_dir() and (
            candidate / "pyproject.toml"
        ).exists():
            return candidate
    return None


def load_fixture_catalog(eval_root: Path | None = None) -> AgentEvalCatalog:
    """Load the fixture catalog."""
    root = eval_root or default_eval_root()
    catalog_path = root / "fixtures" / "v1" / "index.json"
    return AgentEvalCatalog.model_validate_json(catalog_path.read_text())


def load_fixtures(
    *,
    eval_root: Path | None = None,
    suite: EvalSuite = "canonical",
    ids: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> list[AgentEvalFixture]:
    """Load and filter fixtures from disk."""
    root = eval_root or default_eval_root()
    catalog = load_fixture_catalog(root)
    fixture_root = root / "fixtures" / "v1"
    id_filter = set(ids or [])
    tag_filter = set(tags or [])
    fixtures: list[AgentEvalFixture] = []
    for filename in catalog.scenarios:
        fixture = AgentEvalFixture.model_validate_json(
            (fixture_root / filename).read_text()
        )
        if suite == "canonical" and not fixture.suite.canonical:
            continue
        if suite == "weekly" and not fixture.suite.weekly:
            continue
        if id_filter and fixture.id not in id_filter:
            continue
        if tag_filter and not tag_filter.intersection(fixture.tags):
            continue
        fixtures.append(fixture)
    return fixtures


def run_eval_suite(
    *,
    eval_root: Path | None = None,
    suite: EvalSuite = "canonical",
    model: str = "primary",
    ids: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> AgentEvalReport:
    """Run a fixture suite and return a normalized report."""
    suite_started = time.perf_counter()
    fixtures = load_fixtures(eval_root=eval_root, suite=suite, ids=ids, tags=tags)
    profile = resolve_eval_model_profile(model)
    scenario_results: list[AgentEvalScenarioResult] = []
    time_to_first_turn_ms: int | None = None
    for fixture in fixtures:
        result = run_fixture_with_profile(
            fixture=fixture,
            model_config=profile.agent_model_config,
        )
        if time_to_first_turn_ms is None:
            time_to_first_turn_ms = _elapsed_ms(suite_started)
        scenario_results.append(result)
    summary = {
        "scenarios": len(scenario_results),
        "passed": sum(1 for result in scenario_results if result.status == "passed"),
        "failed": sum(1 for result in scenario_results if result.status == "failed"),
        "known_failures": sum(
            1 for result in scenario_results if result.status == "known_failure"
        ),
    }
    latencies = _scenario_latencies(scenario_results)
    return AgentEvalReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        suite=suite,
        model=model,
        summary=summary,
        metrics={
            "total_elapsed_ms": _elapsed_ms(suite_started),
            "time_to_first_turn_ms": time_to_first_turn_ms,
            "avg_latency_ms": _average_int(latencies),
            "max_latency_ms": max(latencies) if latencies else None,
            "estimated_cost_usd": _sum_estimated_costs(scenario_results),
            "pricing_source": model_pricing_source(),
        },
        scenarios=scenario_results,
    )


def run_live_planner_eval_suite(
    *,
    eval_root: Path | None = None,
    suite: EvalSuite = "weekly",
    model: str = "primary",
    ids: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    timeout_seconds: float = 45.0,
) -> AgentEvalReport:
    """Run fixtures through a live model planner and deterministic policy/tools."""
    suite_started = time.perf_counter()
    fixtures = load_fixtures(eval_root=eval_root, suite=suite, ids=ids, tags=tags)
    profile = resolve_eval_model_profile(model)
    if not profile.configured:
        raise RuntimeError(f"Eval profile {model} is missing credentials")
    scenario_results: list[AgentEvalScenarioResult] = []
    time_to_first_turn_ms: int | None = None
    retries = 0
    for fixture in fixtures:
        result = run_fixture_with_live_planner(
            fixture=fixture,
            profile=profile,
            timeout_seconds=timeout_seconds,
        )
        if time_to_first_turn_ms is None:
            time_to_first_turn_ms = _elapsed_ms(suite_started)
        if result.status == "failed":
            retry_result = run_fixture_with_live_planner(
                fixture=fixture,
                profile=profile,
                timeout_seconds=timeout_seconds,
            )
            retries += 1
            if retry_result.status != "failed":
                result = retry_result
        scenario_results.append(result)
    passed = sum(1 for result in scenario_results if result.status == "passed")
    failed = sum(1 for result in scenario_results if result.status == "failed")
    known_failures = sum(
        1 for result in scenario_results if result.status == "known_failure"
    )
    parse_successes = sum(
        1 for result in scenario_results if result.observed.parse_success is True
    )
    latencies = _scenario_latencies(scenario_results)
    return AgentEvalReport(
        mode="live_planner",
        generated_at=datetime.now(timezone.utc).isoformat(),
        suite=suite,
        model=model,
        summary={
            "scenarios": len(scenario_results),
            "passed": passed,
            "failed": failed,
            "known_failures": known_failures,
        },
        metrics={
            "total_elapsed_ms": _elapsed_ms(suite_started),
            "time_to_first_turn_ms": time_to_first_turn_ms,
            "parse_successes": parse_successes,
            "parse_failures": len(scenario_results) - parse_successes,
            "parse_success_rate": _rate(parse_successes, len(scenario_results)),
            "bad_plans": failed,
            "bad_plan_rate": _rate(failed, len(scenario_results)),
            "avg_latency_ms": _average_int(latencies),
            "max_latency_ms": max(latencies) if latencies else None,
            "estimated_cost_usd": _sum_estimated_costs(scenario_results),
            "pricing_source": model_pricing_source(),
            "retries": retries,
        },
        scenarios=scenario_results,
    )


def run_fixture(
    *, fixture: AgentEvalFixture, model: str = "primary"
) -> AgentEvalScenarioResult:
    """Run one fixture through the production orchestrator surface."""
    return run_fixture_with_profile(
        fixture=fixture,
        model_config=resolve_eval_model_profile(model).agent_model_config,
    )


def run_fixture_with_profile(
    *,
    fixture: AgentEvalFixture,
    model_config: AgentModelConfig,
) -> AgentEvalScenarioResult:
    """Run one fixture with an already resolved model profile."""
    task_store = InMemoryTaskStore()
    context = fixture.context.to_identity_context()
    for task in fixture.seed.tasks:
        task_store.create_task(
            title=task.title,
            project=task.project,
            assignee=task.assignee,
            due_date=task.due_date,
            organization_id=task.organization_id or context.organization_id,
            created_by=task.created_by or context.discord_user_id,
        )

    runtime_config = ToolRuntimeConfig(**fixture.runtime_config)
    orchestrator = AgentOrchestrator(
        registry=_EvalToolRegistry(
            task_store=task_store,
            runtime_config=runtime_config,
            stub_results=fixture.stub_results,
        ),
        model_config=model_config,
    )
    message = fixture.request.current_message()
    started = time.perf_counter()
    response = orchestrator.plan(message, context)
    observed = observe_response(
        fixture=fixture,
        message=message,
        response=response,
        latency_ms=_elapsed_ms(started),
    )
    checks = evaluate_observed(fixture.expect, observed)
    passed = all(check.passed for check in checks)
    status: EvalStatus
    if passed:
        status = "passed"
    elif fixture.known_failure is not None:
        status = "known_failure"
    else:
        status = "failed"
    return AgentEvalScenarioResult(
        id=observed.id,
        fixture_id=fixture.id,
        status=status,
        known_failure=fixture.known_failure,
        checks=checks,
        observed=observed,
    )


def run_fixture_with_live_planner(
    *,
    fixture: AgentEvalFixture,
    profile: AgentEvalModelProfile,
    timeout_seconds: float,
) -> AgentEvalScenarioResult:
    """Run one fixture by asking a live model to draft the tool plan."""
    task_store = InMemoryTaskStore()
    context = fixture.context.to_identity_context()
    for task in fixture.seed.tasks:
        task_store.create_task(
            title=task.title,
            project=task.project,
            assignee=task.assignee,
            due_date=task.due_date,
            organization_id=task.organization_id or context.organization_id,
            created_by=task.created_by or context.discord_user_id,
        )

    runtime_config = ToolRuntimeConfig(**fixture.runtime_config)
    orchestrator = AgentOrchestrator(
        registry=_EvalToolRegistry(
            task_store=task_store,
            runtime_config=runtime_config,
            stub_results=fixture.stub_results,
        ),
        model_config=profile.agent_model_config,
    )
    message = fixture.request.current_message()
    call = _call_live_planner(
        profile=profile,
        fixture=fixture,
        message=message,
        timeout_seconds=timeout_seconds,
    )
    response = _response_from_live_draft(
        orchestrator=orchestrator,
        draft=call.draft,
        message=message,
        context=context,
        profile=profile,
        parse_error=call.error,
    )
    observed = observe_response(
        fixture=fixture,
        message=message,
        response=response,
        latency_ms=call.latency_ms,
        parse_success=call.parse_success,
        parse_error=call.error,
        raw_model_output=call.raw_output,
        token_usage=call.token_usage,
        estimated_cost_usd=call.estimated_cost_usd,
    )
    checks = evaluate_observed(fixture.expect, observed, strict=False)
    if not call.parse_success:
        checks.append(
            AgentEvalCheck(
                name="live_planner.parse_success",
                passed=False,
                expected=True,
                observed=call.error,
            )
        )
    passed = all(check.passed for check in checks)
    status: EvalStatus
    if passed:
        status = "passed"
    elif fixture.known_failure is not None:
        status = "known_failure"
    else:
        status = "failed"
    return AgentEvalScenarioResult(
        id=observed.id,
        fixture_id=fixture.id,
        status=status,
        known_failure=fixture.known_failure,
        checks=checks,
        observed=observed,
    )


def list_eval_model_profiles() -> list[AgentEvalModelProfile]:
    """Return built-in model profiles for local comparison runs."""
    return [
        resolve_eval_model_profile(profile_id)
        for profile_id in [
            "primary",
            "openai-direct",
            "fireworks-kimi",
            "openrouter",
            "anthropic",
        ]
    ]


def resolve_eval_model_profile(profile_id: str) -> AgentEvalModelProfile:
    """Resolve an eval profile from environment variables."""
    normalized = profile_id.strip().lower() or "primary"
    api_key: str | None
    if normalized == "primary":
        configured_base_url = _env("OPENAI_BASE_URL")
        provider_api_key = _env("OPENAI_API_KEY")
        direct_api_key = _env("OPENAI_API_KEY_DIRECT")
        if configured_base_url and provider_api_key:
            api_key = provider_api_key
            base_url = configured_base_url
        else:
            api_key = direct_api_key or provider_api_key
            base_url = "https://api.openai.com/v1"
        default_model = (
            "openai/gpt-4.1-mini"
            if "openrouter.ai" in base_url.casefold()
            else "gpt-4.1-mini"
        )
        model = _env("AGENT_EVAL_OPENAI_MODEL") or default_model
        return AgentEvalModelProfile(
            id="primary",
            label="Primary OpenAI-compatible profile",
            configured=bool(api_key),
            notes=(
                "Uses OPENAI_API_KEY with OPENAI_BASE_URL when configured; "
                "otherwise uses OPENAI_API_KEY_DIRECT or OPENAI_API_KEY against "
                "the direct OpenAI endpoint."
            ),
            live_model=model,
            live_base_url=base_url,
            live_api_key=api_key,
            agent_model_config=AgentModelConfig(
                openai_model=model,
                openai_base_url=base_url,
                openai_api_key=api_key,
            ),
        )
    if normalized == "openai-direct":
        api_key = _env("OPENAI_API_KEY_DIRECT")
        model = _env("AGENT_EVAL_OPENAI_MODEL") or "gpt-4.1-mini"
        return AgentEvalModelProfile(
            id="openai-direct",
            label="OpenAI direct",
            configured=bool(api_key),
            notes="Uses OPENAI_API_KEY_DIRECT only.",
            live_model=model,
            live_base_url="https://api.openai.com/v1",
            live_api_key=api_key,
            agent_model_config=AgentModelConfig(
                openai_model=model,
                openai_base_url="https://api.openai.com/v1",
                openai_api_key=api_key,
            ),
        )
    if normalized == "fireworks-kimi":
        api_key = _env("FIREWORKS_API_KEY")
        model = (
            _env("AGENT_EVAL_FIREWORKS_MODEL") or "accounts/fireworks/models/kimi-k2p6"
        )
        tier = AgentTierModelConfig(
            model=model,
            base_url="https://api.fireworks.ai/inference/v1",
            api_key=api_key,
        )
        return AgentEvalModelProfile(
            id="fireworks-kimi",
            label="Fireworks Kimi",
            configured=bool(api_key),
            notes="Uses FIREWORKS_API_KEY and an OpenAI-compatible Fireworks endpoint.",
            live_model=model,
            live_base_url="https://api.fireworks.ai/inference/v1",
            live_api_key=api_key,
            agent_model_config=AgentModelConfig(
                fast=tier,
                strong=tier,
                reasoning=tier,
            ),
        )
    if normalized == "openrouter":
        api_key = _env("OPENROUTER_API_KEY")
        model = _env("AGENT_EVAL_OPENROUTER_MODEL") or "openai/gpt-5-mini"
        tier = AgentTierModelConfig(
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        return AgentEvalModelProfile(
            id="openrouter",
            label="OpenRouter",
            configured=bool(api_key),
            notes="Uses OPENROUTER_API_KEY and an OpenAI-compatible OpenRouter endpoint.",
            live_model=model,
            live_base_url="https://openrouter.ai/api/v1",
            live_api_key=api_key,
            agent_model_config=AgentModelConfig(
                fast=tier,
                strong=tier,
                reasoning=tier,
            ),
        )
    if normalized == "anthropic":
        api_key = _env("ANTHROPIC_API_KEY")
        model = _env("AGENT_EVAL_ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
        return AgentEvalModelProfile(
            id="anthropic",
            label="Anthropic",
            configured=bool(api_key),
            notes=(
                "Uses ANTHROPIC_API_KEY through the native Messages API for live planner evals."
            ),
            live_provider="anthropic",
            live_model=model,
            live_base_url="https://api.anthropic.com/v1",
            live_api_key=api_key,
            agent_model_config=AgentModelConfig(),
        )
    raise ValueError(f"Unknown eval model profile: {profile_id}")


def _call_live_planner(
    *,
    profile: AgentEvalModelProfile,
    fixture: AgentEvalFixture,
    message: str,
    timeout_seconds: float,
) -> LivePlannerCallResult:
    started = time.perf_counter()
    try:
        if profile.live_provider == "anthropic":
            raw_output, token_usage, estimated_cost_usd = _call_anthropic_live_planner(
                profile=profile,
                fixture=fixture,
                message=message,
                timeout_seconds=timeout_seconds,
            )
        else:
            (
                raw_output,
                token_usage,
                estimated_cost_usd,
            ) = _call_openai_compatible_live_planner(
                profile=profile,
                fixture=fixture,
                message=message,
                timeout_seconds=timeout_seconds,
            )
        draft = _parse_live_planner_json(raw_output)
        return LivePlannerCallResult(
            draft=draft,
            raw_output=raw_output,
            latency_ms=_elapsed_ms(started),
            parse_success=True,
            token_usage=token_usage,
            estimated_cost_usd=estimated_cost_usd,
        )
    except Exception as exc:
        return LivePlannerCallResult(
            raw_output=None,
            latency_ms=_elapsed_ms(started),
            parse_success=False,
            error=str(exc),
        )


def _call_openai_compatible_live_planner(
    *,
    profile: AgentEvalModelProfile,
    fixture: AgentEvalFixture,
    message: str,
    timeout_seconds: float,
) -> tuple[str, dict[str, int] | None, float | None]:
    if not profile.live_api_key or not profile.live_model or not profile.live_base_url:
        raise RuntimeError(f"Profile {profile.id} is not configured for live evals")
    user_prompt = _live_planner_user_prompt(fixture, message)
    payload: dict[str, Any] = {
        "model": profile.live_model,
        "messages": [
            {"role": "system", "content": _LIVE_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    model_options = model_chat_completion_options(profile.live_model)
    max_tokens_parameter = model_options.get("max_tokens_parameter")
    if isinstance(max_tokens_parameter, str) and max_tokens_parameter:
        payload[max_tokens_parameter] = 1200
        reasoning_effort = model_options.get("reasoning_effort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        verbosity = model_options.get("verbosity")
        if isinstance(verbosity, str) and verbosity:
            payload["verbosity"] = verbosity
        if model_options.get("supports_temperature", True):
            payload["temperature"] = 0
    else:
        payload["max_tokens"] = 1200
        payload["temperature"] = 0
    response = requests.post(
        f"{profile.live_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {profile.live_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_seconds,
        verify=default_ca_bundle_path(),
    )
    if response.status_code >= 400 and "response_format" in payload:
        payload.pop("response_format", None)
        response = requests.post(
            f"{profile.live_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {profile.live_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
            verify=default_ca_bundle_path(),
        )
    response.raise_for_status()
    data = response.json()
    raw_output = str(data["choices"][0]["message"]["content"])
    usage = _usage_from_mapping(data.get("usage"))
    if not usage:
        usage = _estimate_usage_from_text(
            input_text=f"{_LIVE_PLANNER_SYSTEM_PROMPT}\n{user_prompt}",
            output_text=raw_output,
        )
    return (
        raw_output,
        usage or None,
        _estimate_model_cost_usd(profile.live_model, usage),
    )


def _call_anthropic_live_planner(
    *,
    profile: AgentEvalModelProfile,
    fixture: AgentEvalFixture,
    message: str,
    timeout_seconds: float,
) -> tuple[str, dict[str, int] | None, float | None]:
    if not profile.live_api_key or not profile.live_model:
        raise RuntimeError(f"Profile {profile.id} is not configured for live evals")
    user_prompt = _live_planner_user_prompt(fixture, message)
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": profile.live_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": profile.live_model,
            "max_tokens": 1200,
            "temperature": 0,
            "system": _LIVE_PLANNER_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=timeout_seconds,
        verify=default_ca_bundle_path(),
    )
    response.raise_for_status()
    data = response.json()
    parts = [
        block.get("text", "")
        for block in data.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    raw_output = "\n".join(parts)
    usage = _usage_from_mapping(data.get("usage"))
    if not usage:
        usage = _estimate_usage_from_text(
            input_text=f"{_LIVE_PLANNER_SYSTEM_PROMPT}\n{user_prompt}",
            output_text=raw_output,
        )
    return (
        raw_output,
        usage or None,
        _estimate_model_cost_usd(profile.live_model, usage),
    )


def _live_planner_user_prompt(fixture: AgentEvalFixture, message: str) -> str:
    return build_planner_user_prompt(
        message=message,
        context=fixture.context.to_identity_context(),
        runtime_config=ToolRuntimeConfig(**fixture.runtime_config),
        thread=[item.model_dump() for item in fixture.request.thread],
    )


def _parse_live_planner_json(raw_output: str) -> LivePlannerDraft:
    try:
        return LivePlannerDraft.model_validate(
            parse_planner_draft(raw_output).model_dump(mode="python")
        )
    except (ValidationError, ValueError, json.JSONDecodeError):
        payload = json.loads(raw_output)
        normalized = dict(payload)
        status = str(normalized.get("status") or "").strip().lower()
        if status in {"clarify", "clarification", "needs clarification"}:
            normalized["status"] = "needs_clarification"
        if status in {"plan", "planned", "ok"}:
            normalized["status"] = "planned"
        return LivePlannerDraft.model_validate(normalized)


def _response_from_live_draft(
    *,
    orchestrator: AgentOrchestrator,
    draft: LivePlannerDraft | None,
    message: str,
    context: AgentIdentityContext,
    profile: AgentEvalModelProfile,
    parse_error: str | None,
) -> AgentResponse:
    if draft is None:
        return AgentResponse(
            status="failed",
            message=f"Live planner failed: {parse_error or 'unknown error'}",
        )
    resolved_member_agreement = orchestrator._plan_member_agreement_from_crm(
        message,
        context,
        planner="live_model",
    )
    if resolved_member_agreement is not None:
        return resolved_member_agreement
    if draft.status == "needs_clarification" or not draft.actions:
        fallback_action = _deterministic_live_planner_fallback(
            orchestrator=orchestrator,
            message=message,
        )
        if fallback_action is not None:
            actions = [fallback_action]
        else:
            question = draft.clarification_question or "What should I do next?"
            return AgentResponse(
                status="needs_clarification",
                message=question,
                clarification_question=question,
            )
    else:
        actions = [
            AgentToolAction(
                tool_name=action.tool_name,
                arguments=action.arguments,
                summary=action.summary or f"Call {action.tool_name}",
            )
            for action in draft.actions
        ]

    for action in actions:
        try:
            orchestrator.registry.validate_planner_action(
                action.tool_name,
                action.arguments,
            )
        except ValueError:
            return AgentResponse(
                status="needs_clarification",
                message="I need a clearer request before I can safely continue.",
                clarification_question=(
                    "What exact task, issue, contact, or account action should I run?"
                ),
            )
        manifest = orchestrator.registry.get(action.tool_name)
        if manifest is None:
            continue
        action.arguments = orchestrator.registry.normalize_action_arguments(
            action.tool_name,
            action.arguments,
        )
        action.risk = manifest.risk
        action.requires_confirmation = manifest.requires_confirmation
        action.required_scopes = orchestrator.policy.required_scopes_for_action(
            manifest=manifest,
            action=action,
        )
        clarification = orchestrator._planner_action_clarification(
            action,
            context=context,
        )
        if clarification is not None:
            return AgentResponse(
                status="needs_clarification",
                message=clarification,
                clarification_question=clarification,
            )

    model_tier = orchestrator._choose_model_tier(message, actions)
    model_selection = _live_model_selection(profile=profile, tier=model_tier)
    canonical_intent = orchestrator._intent_for_tool(actions[0].tool_name)
    for action in actions:
        manifest = orchestrator.registry.get(action.tool_name)
        decision = orchestrator.policy.authorize(
            context=context,
            manifest=manifest,
            action=action,
        )
        if not decision.allowed:
            action.requires_confirmation = False
            plan = AgentPlan(
                plan_id=f"eval-{uuid4().hex}",
                intent=canonical_intent,
                planner="live_model",
                model_tier=model_tier,
                model=model_selection,
                actions=actions,
                human_summary=orchestrator._human_summary(actions),
                requires_confirmation=False,
            )
            return AgentResponse(status="denied", plan=plan, message=decision.reason)

    requires_confirmation = any(action.requires_confirmation for action in actions)
    plan = AgentPlan(
        plan_id=f"eval-{uuid4().hex}",
        intent=canonical_intent,
        planner="live_model",
        model_tier=model_tier,
        model=model_selection,
        actions=actions,
        human_summary=orchestrator._human_summary(actions),
        requires_confirmation=requires_confirmation,
    )
    if requires_confirmation:
        return AgentResponse(
            status="requires_confirmation",
            plan=plan,
            message="This action needs confirmation before execution.",
        )

    results = orchestrator.execute_plan(plan, context)
    if all(result.status == "succeeded" for result in results):
        return AgentResponse(
            status="executed",
            plan=plan,
            results=results,
            message=orchestrator._execution_message(results),
        )
    return AgentResponse(
        status="failed",
        plan=plan,
        results=results,
        message=orchestrator._execution_message(results),
    )


def _deterministic_live_planner_fallback(
    *,
    orchestrator: AgentOrchestrator,
    message: str,
) -> AgentToolAction | None:
    action = orchestrator._parse_action(message)
    if action is None:
        return None
    if action.tool_name != "task_read.search_tasks":
        return None
    if orchestrator.registry.get(action.tool_name) is None:
        return None
    if _live_action_clarification(orchestrator, action) is not None:
        return None
    action.summary = action.summary or f"Call {action.tool_name}"
    return action


def _live_action_clarification(
    orchestrator: AgentOrchestrator,
    action: AgentToolAction,
) -> str | None:
    """Return a clarification question for malformed live-drafted actions."""
    args = action.arguments
    tool_name = action.tool_name
    if tool_name == "task_read.search_tasks":
        if not _non_empty_arg(args, "project"):
            return "Which project should I search?"
        return None
    if tool_name == "task_write.create_task":
        if not _non_empty_arg(args, "title"):
            return "What should the task be?"
        return None
    if tool_name == "task_write.update_task":
        if not _non_empty_arg(args, "task_id"):
            return "Which task should I update?"
        if not any(
            _non_empty_arg(args, key)
            for key in ("title", "project", "assignee", "due_date", "status")
        ):
            return "What should I change on that task?"
        return None
    if tool_name == "github_issue.search_issues":
        if not _non_empty_arg(args, "query"):
            return "What GitHub issues should I search for?"
        if not _non_empty_arg(args, "repository") and not _non_empty_text(
            orchestrator.registry.runtime_config.github_default_repo
        ):
            return "Which GitHub repository should I search?"
        return None
    if tool_name == "github_issue.create_issue":
        if not _non_empty_arg(args, "title"):
            return "What should be the title of the GitHub issue?"
        if not _non_empty_arg(args, "repository") and not _non_empty_text(
            orchestrator.registry.runtime_config.github_default_repo
        ):
            return "Which GitHub repository should I create the issue in?"
        return None
    if tool_name == "crm_read.search_contacts":
        if not _non_empty_arg(args, "query"):
            return "Who should I look up?"
        return None
    if tool_name == "crm_write.update_contact":
        if not _non_empty_arg(args, "contact_id"):
            return "Which CRM contact should I update?"
        updates = args.get("updates")
        if not isinstance(updates, dict) or not updates:
            return "What should I update on that CRM contact?"
        return None
    if tool_name == "docuseal_write.create_member_agreement_submission":
        if not _non_empty_arg(args, "submitter_email"):
            return "What email address should I use for the member agreement?"
        return None
    if tool_name == "mail_write.create_mailbox":
        if not _non_empty_arg(args, "local_part"):
            return "What mailbox should I create?"
        if not _non_empty_arg(args, "backup_email"):
            return "What backup email should I use?"
        if not _non_empty_arg(args, "name"):
            return "What display name should I use?"
    if tool_name == "sso_write.create_user":
        if not _has_contact_reference(args):
            return "Which CRM contact should I create the SSO user for?"
        return None
    if tool_name == "outline_write.invite_user":
        if not _non_empty_arg(args, "email") and not _has_contact_reference(args):
            return "Who should I invite to Outline?"
        return None
    if tool_name == "account_write.create_user_accounts":
        if not _has_contact_reference(args):
            return "Which CRM contact should I create accounts for?"
        if not _non_empty_arg(args, "mailbox_username"):
            return "What 508 mailbox username should I create?"
    return None


def _non_empty_arg(args: dict[str, Any], key: str) -> bool:
    return _non_empty_text(args.get(key))


def _has_contact_reference(args: dict[str, Any]) -> bool:
    return _non_empty_arg(args, "contact_id") or _non_empty_arg(args, "contact_query")


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _live_model_selection(
    *,
    profile: AgentEvalModelProfile,
    tier: ModelTier,
) -> AgentModelSelection:
    return AgentModelSelection(
        tier=tier,
        model=profile.live_model or profile.id,
        base_url=profile.live_base_url,
        source_tier=tier,
        fallback_used=False,
        api_key_configured=profile.configured,
    )


def observe_response(
    *,
    fixture: AgentEvalFixture,
    message: str,
    response: AgentResponse,
    latency_ms: int | None = None,
    parse_success: bool | None = None,
    parse_error: str | None = None,
    raw_model_output: str | None = None,
    token_usage: dict[str, int] | None = None,
    estimated_cost_usd: float | None = None,
) -> AgentEvalObserved:
    """Normalize an AgentResponse into an eval observation."""
    plan = response.plan
    return AgentEvalObserved(
        id=fixture.id,
        fixture_id=fixture.id,
        description=fixture.description,
        tags=fixture.tags,
        request_message=message,
        status=response.status,
        message=response.message,
        clarification_question=response.clarification_question,
        intent=plan.intent if plan is not None else None,
        model_tier=plan.model_tier if plan is not None else None,
        model=plan.model.model_dump(mode="json") if plan is not None else None,
        latency_ms=latency_ms,
        parse_success=parse_success,
        parse_error=parse_error,
        raw_model_output=raw_model_output,
        token_usage=token_usage,
        estimated_cost_usd=estimated_cost_usd,
        requires_confirmation=(
            plan.requires_confirmation if plan is not None else None
        ),
        actions=[
            AgentEvalObservedAction(
                tool_name=action.tool_name,
                arguments=action.arguments,
                risk=action.risk,
                requires_confirmation=action.requires_confirmation,
                required_scopes=action.required_scopes,
                summary=action.summary,
            )
            for action in (plan.actions if plan is not None else [])
        ],
        results=[
            AgentEvalObservedResult(
                tool_name=result.tool_name,
                status=result.status,
                result=result.result,
                error=result.error,
            )
            for result in response.results
        ],
    )


def evaluate_observed(
    expect: AgentEvalExpect,
    observed: AgentEvalObserved,
    *,
    strict: bool = True,
) -> list[AgentEvalCheck]:
    """Evaluate deterministic expectations against observed output."""
    checks = [
        _check("status", expect.status, observed.status, strict=strict),
    ]
    checks.extend(
        [
            _check_optional("intent", expect.intent, observed.intent, strict=strict),
            _check_optional(
                "model_tier", expect.model_tier, observed.model_tier, strict=strict
            ),
            _check_optional(
                "requires_confirmation",
                expect.requires_confirmation,
                observed.requires_confirmation,
                strict=strict,
            ),
            _check_optional(
                "clarification_question",
                expect.clarification_question,
                observed.clarification_question,
                strict=strict,
            ),
        ]
    )
    if expect.message_contains is not None:
        checks.append(
            AgentEvalCheck(
                name="message_contains",
                passed=expect.message_contains in observed.message,
                expected=expect.message_contains,
                observed=observed.message,
            )
        )
    if expect.result_statuses is not None:
        checks.append(
            _check(
                "result_statuses",
                expect.result_statuses,
                [result.status for result in observed.results],
                strict=strict,
            )
        )
    if expect.actions is not None:
        checks.append(
            _check(
                "action_count",
                len(expect.actions),
                len(observed.actions),
                strict=strict,
            )
        )
        for index, expected_action in enumerate(expect.actions):
            observed_action = (
                observed.actions[index] if index < len(observed.actions) else None
            )
            checks.extend(
                _evaluate_action(index, expected_action, observed_action, strict=strict)
            )
    return [check for check in checks if check.name]


def _evaluate_action(
    index: int,
    expected: AgentEvalExpectedAction,
    observed: AgentEvalObservedAction | None,
    *,
    strict: bool,
) -> list[AgentEvalCheck]:
    prefix = f"actions[{index}]"
    if observed is None:
        return [
            AgentEvalCheck(
                name=f"{prefix}.present",
                passed=False,
                expected=expected.model_dump(mode="json"),
                observed=None,
            )
        ]
    checks = [
        _check(
            f"{prefix}.tool_name",
            expected.tool_name,
            observed.tool_name,
            strict=strict,
        ),
        _check_optional(
            f"{prefix}.requires_confirmation",
            expected.requires_confirmation,
            observed.requires_confirmation,
            strict=strict,
        ),
        _check_optional(
            f"{prefix}.required_scopes",
            expected.required_scopes,
            observed.required_scopes,
            strict=strict,
        ),
    ]
    if expected.arguments is not None:
        checks.append(
            _check(
                f"{prefix}.arguments",
                expected.arguments,
                observed.arguments,
                strict=strict,
            )
        )
    if expected.arguments_contains is not None:
        for key, value in expected.arguments_contains.items():
            checks.append(
                _check(
                    f"{prefix}.arguments.{key}",
                    value,
                    observed.arguments.get(key),
                    strict=strict,
                )
            )
    return [check for check in checks if check.name]


def _check(
    name: str, expected: Any, observed: Any, *, strict: bool = True
) -> AgentEvalCheck:
    return AgentEvalCheck(
        name=name,
        passed=_eval_values_match(expected, observed, strict=strict),
        expected=expected,
        observed=observed,
    )


def _check_optional(
    name: str, expected: Any, observed: Any, *, strict: bool = True
) -> AgentEvalCheck:
    if expected is None:
        return AgentEvalCheck(name="", passed=True)
    return _check(name, expected, observed, strict=strict)


def _eval_values_match(expected: Any, observed: Any, *, strict: bool) -> bool:
    if strict:
        return observed == expected
    if isinstance(expected, str) and isinstance(observed, str):
        expected_text = _normalize_eval_text(expected)
        observed_text = _normalize_eval_text(observed)
        return observed_text == expected_text or observed_text.startswith(expected_text)
    if isinstance(expected, dict) and isinstance(observed, dict):
        return all(
            key in observed and _eval_values_match(value, observed[key], strict=False)
            for key, value in expected.items()
        )
    if isinstance(expected, list) and isinstance(observed, list):
        if len(expected) != len(observed):
            return False
        return all(
            _eval_values_match(expected_item, observed_item, strict=False)
            for expected_item, observed_item in zip(expected, observed, strict=True)
        )
    return observed == expected


def _normalize_eval_text(value: str) -> str:
    normalized = value.casefold()
    normalized = re.sub(r"\bdocumentation\b", "docs", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


def write_report(report: AgentEvalReport, *, output_dir: Path) -> None:
    """Write JSON and Markdown reports for local and CI use."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"{report.suite}.{report.model}"
        if report.mode == "deterministic"
        else f"{report.mode}.{report.suite}.{report.model}"
    )
    (output_dir / f"observed.{stem}.json").write_text(
        report.model_dump_json(indent=2) + "\n"
    )
    (output_dir / f"score.{stem}.md").write_text(render_markdown_report(report))
    (output_dir / f"trace.{stem}.md").write_text(render_trace_report(report))
    (output_dir / f"ctrf.{stem}.json").write_text(
        json.dumps(render_ctrf_report(report), indent=2) + "\n"
    )
    write_reports_index(output_dir)


def render_markdown_report(report: AgentEvalReport) -> str:
    """Render a compact Markdown score report."""
    lines = [
        f"# Discord Agent Eval: {report.suite} / {report.model}",
        "",
        f"- Mode: {report.mode}",
        f"- Scenarios: {report.summary['scenarios']}",
        f"- Passed: {report.summary['passed']}",
        f"- Failed: {report.summary['failed']}",
        f"- Known failures: {report.summary['known_failures']}",
    ]
    if report.metrics:
        for key in [
            "total_elapsed_ms",
            "time_to_first_turn_ms",
            "parse_success_rate",
            "parse_failures",
            "bad_plan_rate",
            "avg_latency_ms",
            "max_latency_ms",
            "estimated_cost_usd",
            "retries",
        ]:
            if key in report.metrics:
                lines.append(f"- {key}: {report.metrics[key]}")
        if report.metrics.get("pricing_source"):
            lines.append(f"- Pricing source: {report.metrics['pricing_source']}")
    lines.extend(
        [
            "",
            "| Scenario | Status | Failed checks | Latency ms | Parse |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for scenario in report.scenarios:
        failed_checks = [check.name for check in scenario.checks if not check.passed]
        lines.append(
            "| "
            + " | ".join(
                [
                    scenario.fixture_id,
                    scenario.status,
                    ", ".join(failed_checks) if failed_checks else "-",
                    str(scenario.observed.latency_ms or "-"),
                    str(scenario.observed.parse_success)
                    if scenario.observed.parse_success is not None
                    else "-",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_trace_report(report: AgentEvalReport) -> str:
    """Render a per-scenario debugging trace."""
    lines = [
        f"# Discord Agent Eval Trace: {report.suite} / {report.model}",
        "",
        f"- Mode: {report.mode}",
        f"- Generated at: {report.generated_at}",
        "",
    ]
    for scenario in report.scenarios:
        failed_checks = [check for check in scenario.checks if not check.passed]
        lines.extend(
            [
                f"## {scenario.fixture_id}",
                "",
                f"- Status: {scenario.status}",
                f"- Description: {scenario.observed.description}",
                f"- Request: `{scenario.observed.request_message}`",
                f"- Response status: `{scenario.observed.status}`",
                f"- Intent: `{scenario.observed.intent or '-'}`",
                f"- Model tier: `{scenario.observed.model_tier or '-'}`",
                f"- Latency ms: `{scenario.observed.latency_ms or '-'}`",
                f"- Estimated cost USD: `{scenario.observed.estimated_cost_usd if scenario.observed.estimated_cost_usd is not None else '-'}`",
                "",
                "### Failed Checks",
                "",
            ]
        )
        if failed_checks:
            for check in failed_checks:
                lines.extend(
                    [
                        f"- `{check.name}`",
                        f"  - expected: `{_compact_json(check.expected)}`",
                        f"  - observed: `{_compact_json(check.observed)}`",
                    ]
                )
        else:
            lines.append("- none")
        lines.extend(
            [
                "",
                "### Actions",
                "",
            ]
        )
        if scenario.observed.actions:
            for action in scenario.observed.actions:
                lines.append(
                    "- "
                    + _compact_json(
                        {
                            "tool_name": action.tool_name,
                            "arguments": action.arguments,
                            "requires_confirmation": action.requires_confirmation,
                            "required_scopes": action.required_scopes,
                        }
                    )
                )
        else:
            lines.append("- none")
        lines.extend(["", "### Message", "", scenario.observed.message or "-", ""])
    return "\n".join(lines)


def render_ctrf_report(report: AgentEvalReport) -> dict[str, Any]:
    """Render a compact CTRF-compatible report for CI artifact consumers."""
    tests: list[dict[str, Any]] = []
    for scenario in report.scenarios:
        failed_checks = [check.name for check in scenario.checks if not check.passed]
        status = (
            "passed" if scenario.status in {"passed", "known_failure"} else "failed"
        )
        tests.append(
            {
                "name": scenario.fixture_id,
                "status": status,
                "duration": scenario.observed.latency_ms or 0,
                "message": ", ".join(failed_checks) if failed_checks else None,
                "suite": ["discord-agent", report.suite, report.model],
                "tags": scenario.observed.tags,
                "rawStatus": scenario.status,
                "filePath": f"tests/evals/discord-agent/fixtures/v1/{scenario.fixture_id}.json",
            }
        )
    return {
        "results": {
            "tool": {"name": "five08-discord-agent-eval", "version": "v1"},
            "summary": {
                "tests": len(tests),
                "passed": sum(1 for test in tests if test["status"] == "passed"),
                "failed": sum(1 for test in tests if test["status"] == "failed"),
                "skipped": 0,
                "pending": 0,
                "other": 0,
                "start": 0,
                "stop": int(report.metrics.get("total_elapsed_ms") or 0),
            },
            "tests": tests,
        }
    }


def write_reports_index(output_dir: Path) -> None:
    """Write a static HTML index for generated eval reports."""
    report_files = sorted(output_dir.glob("observed.*.json"))
    rows: list[str] = []
    for observed_path in report_files:
        try:
            report = AgentEvalReport.model_validate_json(observed_path.read_text())
        except (OSError, ValidationError):
            continue
        stem = observed_path.name.removeprefix("observed.").removesuffix(".json")
        failed = report.summary.get("failed", 0)
        status = "pass" if failed == 0 else "fail"
        rows.append(
            "<tr>"
            f"<td><span class='status {status}'>{status.upper()}</span></td>"
            f"<td>{_html_escape(report.suite)}</td>"
            f"<td>{_html_escape(report.mode)}</td>"
            f"<td>{_html_escape(report.model)}</td>"
            f"<td>{report.summary.get('passed', 0)} / {report.summary.get('scenarios', 0)}</td>"
            f"<td>{report.summary.get('failed', 0)}</td>"
            f"<td>{_html_escape(str(report.metrics.get('total_elapsed_ms') or '-'))}</td>"
            f"<td>{_html_escape(str(report.metrics.get('estimated_cost_usd') or '-'))}</td>"
            "<td>"
            f"<a href='{_html_escape(observed_path.name)}'>observed</a> "
            f"<a href='score.{_html_escape(stem)}.md'>score</a> "
            f"<a href='trace.{_html_escape(stem)}.md'>trace</a> "
            f"<a href='ctrf.{_html_escape(stem)}.json'>ctrf</a>"
            "</td>"
            "</tr>"
        )
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Discord Agent Eval Reports</title>
  <style>
    :root { color-scheme: light dark; }
    body {
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 32px;
      line-height: 1.45;
    }
    h1 { font-size: 24px; margin: 0 0 18px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #d0d7de; padding: 9px 10px; text-align: left; vertical-align: top; }
    th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #57606a; }
    a { margin-right: 8px; }
    .status { display: inline-block; border-radius: 4px; padding: 2px 6px; font-size: 12px; font-weight: 700; }
    .status.pass { background: #dafbe1; color: #116329; }
    .status.fail { background: #ffebe9; color: #82071e; }
    .empty { color: #57606a; }
  </style>
</head>
<body>
  <h1>Discord Agent Eval Reports</h1>
  <table>
    <thead>
      <tr>
        <th>Status</th>
        <th>Suite</th>
        <th>Mode</th>
        <th>Model</th>
        <th>Passed</th>
        <th>Failed</th>
        <th>Total ms</th>
        <th>Cost USD</th>
        <th>Files</th>
      </tr>
    </thead>
    <tbody>
"""
    if rows:
        html += "\n".join(rows)
    else:
        html += "<tr><td class='empty' colspan='9'>No observed reports found.</td></tr>"
    html += """
    </tbody>
  </table>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for running Discord agent eval fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["canonical", "weekly"], default="canonical")
    parser.add_argument("--model", "--profile", dest="model", default="primary")
    parser.add_argument(
        "--live-planner",
        action="store_true",
        help="Call the configured provider to draft structured plans.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=45.0,
        help="Per-scenario live planner provider timeout.",
    )
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--eval-root", type=Path, default=default_eval_root())
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_eval_root() / "reports",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--list-profiles", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_env_file:
        load_env_file(args.env_file)

    if args.list_profiles:
        for profile in list_eval_model_profiles():
            status = "configured" if profile.configured else "missing credentials"
            print(f"{profile.id}\t{status}\t{profile.label}")
            if profile.live_model:
                print(f"  model: {profile.live_model}")
            if profile.notes:
                print(f"  {profile.notes}")
        return 0

    requested_models = _resolve_requested_models(
        args.model, live_only=args.live_planner
    )
    if not requested_models:
        print("No configured live planner profiles found.")
        return 2

    reports: list[AgentEvalReport] = []
    for model in requested_models:
        if args.live_planner:
            report = run_live_planner_eval_suite(
                eval_root=args.eval_root,
                suite=args.suite,
                model=model,
                ids=_split_csv(args.scenarios),
                tags=_split_csv(args.tags),
                timeout_seconds=args.timeout_seconds,
            )
        else:
            report = run_eval_suite(
                eval_root=args.eval_root,
                suite=args.suite,
                model=model,
                ids=_split_csv(args.scenarios),
                tags=_split_csv(args.tags),
            )
        write_report(report, output_dir=args.output_dir)
        reports.append(report)

    if args.json:
        if len(reports) == 1:
            print(reports[0].model_dump_json(indent=2))
        else:
            print(
                json.dumps(
                    [report.model_dump(mode="json") for report in reports], indent=2
                )
            )
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            print(render_markdown_report(report))
    return 1 if any(report.summary["failed"] for report in reports) else 0


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_requested_models(value: str, *, live_only: bool = False) -> list[str]:
    requested = _split_csv(value)
    if requested and requested != ["all"]:
        return requested
    profiles = list_eval_model_profiles()
    if live_only:
        return [profile.id for profile in profiles if profile.configured]
    return [profile.id for profile in profiles]


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
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


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _scenario_latencies(results: Sequence[AgentEvalScenarioResult]) -> list[int]:
    return [
        result.observed.latency_ms
        for result in results
        if result.observed.latency_ms is not None
    ]


def _average_int(values: Sequence[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _sum_estimated_costs(results: Sequence[AgentEvalScenarioResult]) -> float | None:
    costs = [
        result.observed.estimated_cost_usd
        for result in results
        if result.observed.estimated_cost_usd is not None
    ]
    if not costs:
        return None
    return round(sum(costs), 8)


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
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return {}
    return {
        "requests": 1,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _estimate_usage_from_text(*, input_text: str, output_text: str) -> dict[str, int]:
    input_tokens = _rough_token_count(input_text)
    output_tokens = _rough_token_count(output_text)
    return {
        "requests": 1,
        "input_tokens": input_tokens,
        "cached_input_tokens": 0,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": 1,
    }


def _rough_token_count(value: str) -> int:
    if not value:
        return 0
    return max(1, round(len(value) / 4))


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


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _html_escape(value: object) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
