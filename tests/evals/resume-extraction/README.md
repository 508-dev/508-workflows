# Resume Extraction Eval Harness

This directory contains local eval reports for resume profile extraction.

The runner intentionally consumes a local corpus path instead of committing
resume fixtures. The default path is
`tests/evals/resume-extraction/fixtures/local-resumes/`, which is gitignored
except for a `.gitkeep` file. Drop private PDF/DOCX/TXT resumes there for local
analysis, but do not commit real resumes or generated reports.

## Commands

```bash
uv run resume-eval --profile openai-direct
uv run resume-eval --profile all
uv run resume-eval --input-dir /path/to/private/resumes --profile openai-direct
uv run resume-eval --list-profiles
```

Reports are written to `tests/evals/resume-extraction/reports/`, which is
gitignored because observations can contain candidate PII.

Model profile metadata, pricing, request behavior, and observed recommendations
live in `tests/evals/model-profiles.json`. Current local results mark
`gpt-4.1-mini` as the resume extraction default candidate, with `gpt-5.5` kept
as the quality-ceiling baseline.

## Scoring

The score is a lightweight completeness signal, not a golden-label accuracy
score. It checks whether extraction produced core profile fields:

- name
- email
- roles
- skills
- seniority
- location
- profile links
- confidence >= 0.5

The report also tracks LLM success versus heuristic fallback and latency by
provider profile. Native Anthropic is skipped for now because the production
resume extractor uses OpenAI-compatible chat completions.
