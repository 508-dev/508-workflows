"""Unit tests for the Discord agent eval harness."""

from __future__ import annotations

from pathlib import Path

from five08.agent.evals import (
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
    assert all(scenario.status == "passed" for scenario in report.scenarios)


def test_discord_agent_eval_weekly_suite_is_larger_and_passes() -> None:
    canonical = run_eval_suite(suite="canonical")
    weekly = run_eval_suite(suite="weekly")

    assert weekly.summary["scenarios"] > canonical.summary["scenarios"]
    assert weekly.summary["failed"] == 0
    assert all(scenario.status == "passed" for scenario in weekly.scenarios)


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
    markdown = (tmp_path / "score.canonical.primary.md").read_text()
    assert "missing_project_clarification_001" in markdown
    assert "Failed: 0" in markdown


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
                ]
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
    assert calls
    assert (tmp_path / "observed.live_planner.canonical.openai-direct.json").exists()


def test_eval_primary_profile_uses_direct_openai_key_only(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "bifrost-key")
    monkeypatch.delenv("OPENAI_API_KEY_DIRECT", raising=False)

    profile = resolve_eval_model_profile("primary")

    assert profile.configured is False
    assert profile.agent_model_config.openai_api_key is None

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
