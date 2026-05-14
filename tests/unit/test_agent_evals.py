"""Unit tests for the Discord agent eval harness."""

from __future__ import annotations

from pathlib import Path

from five08.agent.evals import (
    AgentEvalExpect,
    AgentEvalExpectedAction,
    AgentEvalObserved,
    AgentEvalObservedAction,
    evaluate_observed,
    load_env_file,
    list_eval_model_profiles,
    resolve_eval_model_profile,
    run_live_planner_eval_suite,
    run_eval_suite,
    write_report,
)


def test_discord_agent_eval_canonical_suite_passes() -> None:
    report = run_eval_suite(suite="canonical")

    assert report.summary["scenarios"] >= 1
    assert report.summary["failed"] == 0
    assert report.metrics["total_elapsed_ms"] is not None
    assert report.metrics["time_to_first_turn_ms"] is not None
    assert all(
        scenario.status in {"passed", "known_failure"} for scenario in report.scenarios
    )


def test_discord_agent_eval_can_filter_scenarios() -> None:
    report = run_eval_suite(
        suite="canonical",
        ids=["missing_project_clarification_001"],
    )

    assert report.summary == {
        "scenarios": 1,
        "passed": 1,
        "failed": 0,
        "known_failures": 0,
    }
    assert report.scenarios[0].fixture_id == "missing_project_clarification_001"


def test_discord_agent_eval_writes_reports(tmp_path: Path) -> None:
    report = run_eval_suite(
        suite="canonical",
        ids=["missing_project_clarification_001"],
    )

    write_report(report, output_dir=tmp_path)

    assert (tmp_path / "observed.canonical.primary.json").exists()
    assert (tmp_path / "trace.canonical.primary.md").exists()
    assert (tmp_path / "ctrf.canonical.primary.json").exists()
    assert (tmp_path / "index.html").exists()
    markdown = (tmp_path / "score.canonical.primary.md").read_text()
    assert "missing_project_clarification_001" in markdown
    assert "Failed: 0" in markdown
    trace = (tmp_path / "trace.canonical.primary.md").read_text()
    assert "## missing_project_clarification_001" in trace
    index = (tmp_path / "index.html").read_text()
    assert "Discord Agent Eval Reports" in index
    assert "observed.canonical.primary.json" in index


def test_live_planner_eval_uses_provider_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"status":"needs_clarification",'
                                '"intent":null,'
                                '"clarification_question":"Which project should I search?",'
                                '"actions":[]}'
                            )
                        }
                    }
                ],
            }

    calls: list[dict[str, object]] = []

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        calls.append({"args": args, "kwargs": kwargs})
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    monkeypatch.setattr("five08.agent.evals.requests.post", fake_post)

    report = run_live_planner_eval_suite(
        suite="canonical",
        model="openai-direct",
        ids=["missing_project_clarification_001"],
        timeout_seconds=1,
    )
    write_report(report, output_dir=tmp_path)

    assert report.mode == "live_planner"
    assert report.summary["passed"] == 1
    assert report.metrics["parse_success_rate"] == 1.0
    assert report.metrics["estimated_cost_usd"] == 0.000072
    assert calls
    kwargs = calls[0]["kwargs"]
    assert isinstance(kwargs, dict)
    payload = kwargs["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["max_tokens"] == 1200
    assert payload["temperature"] == 0
    assert (tmp_path / "observed.live_planner.canonical.openai-direct.json").exists()


def test_live_planner_eval_retries_one_bad_plan(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        def __init__(self, content: str) -> None:
            self._content = content

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
                "choices": [{"message": {"content": self._content}}],
            }

    responses = [
        '{"status":"needs_clarification","intent":null,'
        '"clarification_question":"Who should I look up?","actions":[]}',
        '{"status":"planned","intent":"search_crm_contacts",'
        '"clarification_question":null,'
        '"actions":[{"tool_name":"crm_read.search_contacts",'
        '"arguments":{"query":"Sarah","limit":5},'
        '"summary":"Search CRM contacts matching Sarah"}]}',
    ]
    calls: list[str] = []

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        calls.append("call")
        return FakeResponse(responses.pop(0))

    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    monkeypatch.setattr("five08.agent.evals.requests.post", fake_post)

    report = run_live_planner_eval_suite(
        suite="canonical",
        model="openai-direct",
        ids=["crm_contact_search_001"],
        timeout_seconds=1,
    )

    assert len(calls) == 2
    assert report.summary["passed"] == 1
    assert report.summary["failed"] == 0
    assert report.metrics["retries"] == 1
    assert report.scenarios[0].status == "passed"


def test_live_eval_matching_allows_harmless_text_variants() -> None:
    expect = AgentEvalExpect(
        status="requires_confirmation",
        intent="create_task",
        requires_confirmation=True,
        actions=[
            AgentEvalExpectedAction(
                tool_name="task_write.create_task",
                arguments_contains={"title": "refresh onboarding docs"},
            )
        ],
    )
    observed = AgentEvalObserved(
        id="scenario-1",
        fixture_id="scenario-1",
        description="",
        tags=[],
        request_message="Create a task to refresh onboarding docs",
        status="requires_confirmation",
        message="This action needs confirmation before execution.",
        intent="create_task",
        requires_confirmation=True,
        actions=[
            AgentEvalObservedAction(
                tool_name="task_write.create_task",
                arguments={"title": "Refresh onboarding documentation"},
                risk="medium",
                requires_confirmation=True,
                required_scopes=["task:create"],
                summary="Create task",
            )
        ],
    )

    assert any(
        not check.passed
        for check in evaluate_observed(expect, observed)
        if check.name == "actions[0].arguments.title"
    )
    assert all(
        check.passed for check in evaluate_observed(expect, observed, strict=False)
    )


def test_eval_primary_profile_uses_openai_compatible_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY_DIRECT", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    profile = resolve_eval_model_profile("primary")

    assert profile.configured is False
    assert profile.agent_model_config.openai_api_key is None

    monkeypatch.setenv("OPENAI_API_KEY", "openai-compatible-key")
    profile = resolve_eval_model_profile("primary")

    assert profile.configured is True
    assert profile.live_base_url == "https://api.openai.com/v1"
    assert profile.agent_model_config.openai_api_key == "openai-compatible-key"

    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    profile = resolve_eval_model_profile("primary")

    assert profile.live_base_url == "https://openrouter.ai/api/v1"
    assert profile.live_model == "openai/gpt-4.1-mini"

    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    profile = resolve_eval_model_profile("primary")

    assert profile.configured is True
    assert profile.agent_model_config.openai_api_key == "direct-key"


def test_eval_profiles_include_openai_compatible_providers() -> None:
    profile_ids = {profile.id for profile in list_eval_model_profiles()}

    assert {
        "primary",
        "openai-direct",
        "fireworks-kimi",
        "openrouter",
        "anthropic",
    }.issubset(profile_ids)


def test_eval_env_file_loader_does_not_override_existing_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY_DIRECT=from-file",
                "FIREWORKS_API_KEY='fireworks-file'",
            ]
        )
        + "\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "from-process")
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)

    load_env_file(env_file)

    assert resolve_eval_model_profile("primary").agent_model_config.openai_api_key == (
        "from-process"
    )
    assert resolve_eval_model_profile("fireworks-kimi").configured is True
