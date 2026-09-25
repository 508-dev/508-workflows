# Job-lead classification eval

This harness measures the classifier that decides whether a Hacker News job
post is contractor-friendly and assigns one of four posting types. It compares:

- the production deterministic heuristic
- TypeSafe Jev through OpenRouter's Decisions API
- the production job-lead prompt with GPT-5.6 Luna through direct OpenAI

The checked-in and packaged
`packages/shared/src/five08/data/job-lead-classification-v1.json` corpus contains
synthetic, manually labeled examples. It is balanced across the four posting
types and deliberately includes negation, non-job uses of `contract`, closed
roles, replies, and prompt injection. It is a challenge set, not an estimate of
live Hacker News traffic.

## Run

```bash
uv run job-lead-eval \
  --env-file .env \
  --profiles heuristic,jev,luna \
  --repeats 3
```

`OPENROUTER_API_KEY` is required for Jev. Luna uses the first available direct
OpenAI credential from `OPENAI_DIRECT_API_KEY`, legacy
`OPENAI_API_KEY_DIRECT`, or `OPENAI_API_KEY`. Jev uses the pinned
`typesafe/jev-1.13` request model and the OpenRouter Decisions endpoint. The
runner records dated resolved models returned by the providers.

Reports are written to `tests/evals/job-lead-classification/reports/` and are
gitignored. The JSON report contains normalized observations but not raw model
responses. Use `--summary-path .context/reports/<name>.md` when a reviewed,
durable summary should be committed.

## Metrics

- contractor-friendly accuracy, precision, recall, and F1
- four-way posting-type accuracy and macro F1
- joint exact accuracy across both outputs
- core versus challenge-case accuracy
- repeated-call label stability and probability spread
- symmetric confidence-gate coverage and accepted-decision accuracy
- p50/p95/max latency, tokens, request attempts, and provider-reported cost

All grading is deterministic against golden labels. No model judge is used.
