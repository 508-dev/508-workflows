# Resume Extraction Eval Harness

This directory contains local eval reports for resume profile extraction.

The runner intentionally consumes a local corpus path instead of committing
resume fixtures. The default path is `resumes/`, which is expected to contain
private PDF/DOCX/TXT resumes during local analysis.

## Commands

```bash
uv run resume-eval --input-dir resumes --profile openai-direct
uv run resume-eval --input-dir resumes --profile all
uv run resume-eval --list-profiles
```

Reports are written to `tests/evals/resume-extraction/reports/`, which is
gitignored because observations can contain candidate PII.

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
