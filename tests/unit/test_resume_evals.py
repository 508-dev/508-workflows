"""Unit tests for the resume extraction eval harness."""

from __future__ import annotations

from pathlib import Path

from five08.resume_extractor import ResumeExtractedProfile
from five08.resume_evals import (
    ResumeBaselineObserved,
    ResumeBaselineReport,
    load_env_file,
    list_resume_eval_profiles,
    render_baseline_markdown_report,
    render_resume_markdown_report,
    resolve_eval_model_profile,
    run_resume_eval_suite,
    write_resume_report,
)


class _FakeResumeProfileExtractor:
    def __init__(self, **_: object) -> None:
        pass

    def extract(self, text: str) -> ResumeExtractedProfile:
        first_line = text.splitlines()[0].strip()
        return ResumeExtractedProfile(
            name=first_line,
            first_name=first_line.split()[0],
            last_name=first_line.split()[-1],
            email="candidate@example.com",
            primary_roles=["Software Engineer"],
            skills=["Python"],
            seniority_level="senior",
            address_country="United States",
            linkedin_url="https://linkedin.com/in/candidate",
            llm_usage={
                "requests": 1,
                "input_tokens": 1000,
                "cached_input_tokens": 200,
                "output_tokens": 100,
                "total_tokens": 1100,
            },
            confidence=0.9,
            source="fake",
        )


def test_resume_eval_runs_against_text_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "resumes"
    input_dir.mkdir()
    (input_dir / "Ada Lovelace Resume.txt").write_text(
        "Ada Lovelace\nada@example.com\nSoftware Engineer\nPython, SQL\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    monkeypatch.setattr(
        "five08.resume_evals.ResumeProfileExtractor",
        _FakeResumeProfileExtractor,
    )

    report = run_resume_eval_suite(
        input_dir=input_dir,
        model="openai-direct",
        include_profile_payload=False,
    )

    assert report.summary["resumes"] == 1
    assert report.summary["hard_failures"] == 0
    assert report.resumes[0].field_checks["name"] is True
    assert report.resumes[0].field_checks["email"] is True
    assert report.resumes[0].estimated_cost_usd == 0.0005
    assert report.summary["estimated_cost_usd"] == 0.0005
    assert report.resumes[0].extracted_profile is None


def test_resume_eval_writes_reports(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "resumes"
    output_dir = tmp_path / "reports"
    input_dir.mkdir()
    (input_dir / "Grace Hopper Resume.txt").write_text(
        "Grace Hopper\ngrace@example.com\nEngineering Leader\nCOBOL\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    monkeypatch.setattr(
        "five08.resume_evals.ResumeProfileExtractor",
        _FakeResumeProfileExtractor,
    )

    report = run_resume_eval_suite(
        input_dir=input_dir,
        model="openai-direct",
        include_profile_payload=False,
    )
    write_resume_report(report, output_dir=output_dir)

    assert (output_dir / "observed.local.openai-direct.json").exists()
    markdown = (output_dir / "score.local.openai-direct.md").read_text()
    assert "Grace Hopper Resume.txt" in markdown
    assert "Hard failures: 0" in markdown


def test_resume_eval_profiles_only_include_openai_compatible_profiles() -> None:
    profile_ids = {profile.id for profile in list_resume_eval_profiles()}

    assert "anthropic" not in profile_ids
    assert {"primary", "openai-direct", "fireworks-kimi", "openrouter"}.issubset(
        profile_ids
    )


def test_resume_eval_env_loader_reuses_agent_eval_profiles(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY_DIRECT=from-file\n")
    monkeypatch.delenv("OPENAI_API_KEY_DIRECT", raising=False)

    load_env_file(env_file)

    assert resolve_eval_model_profile("openai-direct").configured is True


def test_resume_eval_markdown_renders_empty_missing_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "resumes"
    input_dir.mkdir()
    (input_dir / "Lin Resume.txt").write_text(
        "Lin Example\nlin@example.com\nDesigner\nFigma\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY_DIRECT", "direct-key")
    monkeypatch.setattr(
        "five08.resume_evals.ResumeProfileExtractor",
        _FakeResumeProfileExtractor,
    )

    report = run_resume_eval_suite(
        input_dir=input_dir,
        model="openai-direct",
        include_profile_payload=False,
    )
    markdown = render_resume_markdown_report(report)

    assert "# Resume Extraction Eval: openai-direct" in markdown


def test_baseline_markdown_uses_location_field_evidence() -> None:
    report = ResumeBaselineReport(
        generated_at="2026-05-12T00:00:00+00:00",
        input_dir="resumes",
        judge_model="gpt-5.5",
        summary={"resumes": 1, "succeeded": 1, "failed": 0, "avg_latency_ms": 1},
        resumes=[
            ResumeBaselineObserved(
                id="candidate",
                path="resumes/candidate.txt",
                filename="candidate.txt",
                judge_model="gpt-5.5",
                status="succeeded",
                text_length=100,
                latency_ms=1,
                baseline={
                    "name": "Candidate",
                    "email": "candidate@example.com",
                    "primary_roles": ["developer"],
                    "seniority_level": "senior",
                    "skills": ["python"],
                    "address_city": "Leeds",
                    "address_state": "NY",
                    "address_country": "United States",
                    "evidence": {
                        "primary_roles": ["Software Engineer"],
                        "seniority_level": ["Senior Engineer"],
                        "skills": ["Python"],
                        "address_city": ["Leeds, NY"],
                        "address_state": ["Leeds, NY"],
                        "address_country": ["Leeds, NY"],
                    },
                },
            )
        ],
    )

    markdown = render_baseline_markdown_report(report)

    assert "| candidate.txt | succeeded | Candidate | candidate@example.com" in markdown
    assert "| developer | senior | python | - |" in markdown
