"""Fixture-driven eval harness for the Discord agent orchestrator."""

from __future__ import annotations

import argparse
import os
from copy import deepcopy
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from five08.agent.models import AgentIdentityContext, AgentResponse
from five08.agent.model_routing import AgentModelConfig, AgentTierModelConfig
from five08.agent.orchestrator import AgentOrchestrator
from five08.agent.tools import InMemoryTaskStore, ToolRegistry, ToolRuntimeConfig

EvalSuite = Literal["canonical", "weekly"]
EvalStatus = Literal["passed", "failed", "known_failure"]


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
    roles: list[str] = Field(default_factory=lambda: ["Member"])
    scopes: list[str] = Field(default_factory=list)
    impersonation: bool = False
    interaction_id: str | None = "agent-eval-interaction"
    message_id: str | None = "agent-eval-message"

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
    generated_at: str
    suite: EvalSuite
    model: str
    summary: dict[str, int]
    scenarios: list[AgentEvalScenarioResult]


class AgentEvalModelProfile(BaseModel):
    """Model/provider profile used for eval comparison runs."""

    id: str
    label: str
    configured: bool
    notes: str | None = None
    agent_model_config: AgentModelConfig


def default_eval_root() -> Path:
    """Return the repo-local Discord agent eval fixture root."""
    return Path(__file__).resolve().parents[5] / "tests" / "evals" / "discord-agent"


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
    fixtures = load_fixtures(eval_root=eval_root, suite=suite, ids=ids, tags=tags)
    profile = resolve_eval_model_profile(model)
    scenario_results = [
        run_fixture_with_profile(
            fixture=fixture,
            model_config=profile.agent_model_config,
        )
        for fixture in fixtures
    ]
    summary = {
        "scenarios": len(scenario_results),
        "passed": sum(1 for result in scenario_results if result.status == "passed"),
        "failed": sum(1 for result in scenario_results if result.status == "failed"),
        "known_failures": sum(
            1 for result in scenario_results if result.status == "known_failure"
        ),
    }
    return AgentEvalReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        suite=suite,
        model=model,
        summary=summary,
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

    class EvalToolRegistry(ToolRegistry):
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
            actor_scopes: set[str] | None = None,
        ) -> dict[str, Any]:
            if tool_name in self._stub_results:
                return deepcopy(self._stub_results[tool_name])
            return super().execute(
                tool_name,
                arguments,
                organization_id=organization_id,
                actor_id=actor_id,
                actor_scopes=actor_scopes,
            )

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
        registry=EvalToolRegistry(
            task_store=task_store,
            runtime_config=runtime_config,
            stub_results=fixture.stub_results,
        ),
        model_config=model_config,
    )
    message = fixture.request.current_message()
    response = orchestrator.plan(message, context)
    observed = observe_response(
        fixture=fixture,
        message=message,
        response=response,
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
    if normalized == "primary":
        api_key = _env("OPENAI_API_KEY_DIRECT")
        model = _env("AGENT_EVAL_OPENAI_MODEL") or _env("OPENAI_MODEL") or "gpt-5-mini"
        return AgentEvalModelProfile(
            id="primary",
            label="Primary OpenAI-compatible profile",
            configured=bool(api_key),
            notes="Uses OPENAI_API_KEY_DIRECT only; OPENAI_API_KEY may route through Bifrost.",
            agent_model_config=AgentModelConfig(
                openai_model=model,
                openai_base_url="https://api.openai.com/v1",
                openai_api_key=api_key,
            ),
        )
    if normalized == "openai-direct":
        api_key = _env("OPENAI_API_KEY_DIRECT")
        model = _env("AGENT_EVAL_OPENAI_MODEL") or "gpt-5-mini"
        return AgentEvalModelProfile(
            id="openai-direct",
            label="OpenAI direct",
            configured=bool(api_key),
            notes="Uses OPENAI_API_KEY_DIRECT only.",
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
            agent_model_config=AgentModelConfig(
                fast=tier,
                strong=tier,
                reasoning=tier,
            ),
        )
    if normalized == "anthropic":
        return AgentEvalModelProfile(
            id="anthropic",
            label="Anthropic",
            configured=bool(_env("ANTHROPIC_API_KEY")),
            notes=(
                "Reserved for a future Anthropic adapter; the current agent model "
                "router only supports OpenAI-compatible base URLs."
            ),
            agent_model_config=AgentModelConfig(),
        )
    raise ValueError(f"Unknown eval model profile: {profile_id}")


def observe_response(
    *,
    fixture: AgentEvalFixture,
    message: str,
    response: AgentResponse,
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
) -> list[AgentEvalCheck]:
    """Evaluate deterministic expectations against observed output."""
    checks = [
        _check("status", expect.status, observed.status),
    ]
    checks.extend(
        [
            _check_optional("intent", expect.intent, observed.intent),
            _check_optional("model_tier", expect.model_tier, observed.model_tier),
            _check_optional(
                "requires_confirmation",
                expect.requires_confirmation,
                observed.requires_confirmation,
            ),
            _check_optional(
                "clarification_question",
                expect.clarification_question,
                observed.clarification_question,
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
            )
        )
    if expect.actions is not None:
        checks.append(
            _check("action_count", len(expect.actions), len(observed.actions))
        )
        for index, expected_action in enumerate(expect.actions):
            observed_action = (
                observed.actions[index] if index < len(observed.actions) else None
            )
            checks.extend(_evaluate_action(index, expected_action, observed_action))
    return [check for check in checks if check.name]


def _evaluate_action(
    index: int,
    expected: AgentEvalExpectedAction,
    observed: AgentEvalObservedAction | None,
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
        _check(f"{prefix}.tool_name", expected.tool_name, observed.tool_name),
        _check_optional(
            f"{prefix}.requires_confirmation",
            expected.requires_confirmation,
            observed.requires_confirmation,
        ),
        _check_optional(
            f"{prefix}.required_scopes",
            expected.required_scopes,
            observed.required_scopes,
        ),
    ]
    if expected.arguments is not None:
        checks.append(
            _check(f"{prefix}.arguments", expected.arguments, observed.arguments)
        )
    if expected.arguments_contains is not None:
        for key, value in expected.arguments_contains.items():
            checks.append(
                _check(
                    f"{prefix}.arguments.{key}",
                    value,
                    observed.arguments.get(key),
                )
            )
    return [check for check in checks if check.name]


def _check(name: str, expected: Any, observed: Any) -> AgentEvalCheck:
    return AgentEvalCheck(
        name=name,
        passed=observed == expected,
        expected=expected,
        observed=observed,
    )


def _check_optional(name: str, expected: Any, observed: Any) -> AgentEvalCheck:
    if expected is None:
        return AgentEvalCheck(name="", passed=True)
    return _check(name, expected, observed)


def write_report(report: AgentEvalReport, *, output_dir: Path) -> None:
    """Write JSON and Markdown reports for local and CI use."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.suite}.{report.model}"
    (output_dir / f"observed.{stem}.json").write_text(
        report.model_dump_json(indent=2) + "\n"
    )
    (output_dir / f"score.{stem}.md").write_text(render_markdown_report(report))


def render_markdown_report(report: AgentEvalReport) -> str:
    """Render a compact Markdown score report."""
    lines = [
        f"# Discord Agent Eval: {report.suite} / {report.model}",
        "",
        f"- Scenarios: {report.summary['scenarios']}",
        f"- Passed: {report.summary['passed']}",
        f"- Failed: {report.summary['failed']}",
        f"- Known failures: {report.summary['known_failures']}",
        "",
        "| Scenario | Status | Failed checks |",
        "| --- | --- | --- |",
    ]
    for scenario in report.scenarios:
        failed_checks = [check.name for check in scenario.checks if not check.passed]
        lines.append(
            "| "
            + " | ".join(
                [
                    scenario.fixture_id,
                    scenario.status,
                    ", ".join(failed_checks) if failed_checks else "-",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for running Discord agent eval fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["canonical", "weekly"], default="canonical")
    parser.add_argument("--model", "--profile", dest="model", default="primary")
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
            if profile.notes:
                print(f"  {profile.notes}")
        return 0

    report = run_eval_suite(
        eval_root=args.eval_root,
        suite=args.suite,
        model=args.model,
        ids=_split_csv(args.scenarios),
        tags=_split_csv(args.tags),
    )
    write_report(report, output_dir=args.output_dir)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_markdown_report(report))
    return 1 if report.summary["failed"] else 0


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
