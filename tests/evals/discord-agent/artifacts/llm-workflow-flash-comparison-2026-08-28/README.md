# OpenRouter flash comparison audit snapshot

This tracked, immutable snapshot supports the findings in [the OpenRouter planner evaluation report](../../../../../.context/reports/2026-08-28-openrouter-flash-llm-evals.md). It is deliberately separate from `tests/evals/discord-agent/reports/`, which remains ignored because it is a scratch location for newly generated reports.

## Scope

- Evaluated runtime commit: `cb2bce89e545c97cf19db18ae5b39230aaa91622`.
- Full sweeps: the four normalized `*.observed.json` files and compact `*.score.md` summaries in `full-sweeps/`, one for each model in the report.
- Recovery evidence: the seven individually rerun Qwen3.8-Flash scenario observations in `qwen3.8-flash-recovery/`.
- Source scenarios: the versioned, synthetic `fixtures/v1` inputs already tracked beside this directory.

The observed JSON preserves scenario checks, provider-draft failures, selected-attempt latency, token usage, and raw model drafts needed to audit the report's aggregates. The `api_key_configured` boolean was removed from every published observation. No credentials, request headers, HTML viewer, CTRF output, or debug trace is retained here.

These are historical observations, not a claim that a live provider rerun will reproduce the same output. Provider routing was not pinned; rerun the documented live-planner command against the named runtime commit to make a new comparison.

## Integrity check

From the repository root, verify the snapshot with:

```bash
sha256sum -c tests/evals/discord-agent/artifacts/llm-workflow-flash-comparison-2026-08-28/SHA256SUMS
```
