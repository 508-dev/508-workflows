# GPT-5.6 Luna vs GPT-5.4 Mini planner evaluation

- Evaluation date: 2026-09-09
- Evaluated runtime revision: `816db61555a09c4e0ccafdd7827b6ca43a797bf3`
- Harness: Discord-agent canonical live-planner eval
- Route: direct OpenAI (`https://api.openai.com/v1`) using `OPENAI_API_KEY`
- Models: `gpt-5.6-luna` and `gpt-5.4-mini`
- Detail: summary-only; per-scenario generated observations are intentionally excluded from this PR.

## Run method and selection semantics

The two 27-scenario suites ran serially. The initial request for every planner
attempt used JSON-object `response_format`; Luna used `max_completion_tokens`,
`reasoning_effort=low`, and `verbosity=low`, with `temperature` omitted.

The 90-second timeout applies to each HTTP request, not to an entire scenario.
Any HTTP error causes an immediate second request without `response_format`,
also with a 90-second timeout. A scenario whose production result is
`failed` or whose provider draft fails gets one full harness retry; that retry
repeats the same request behavior and replaces attempt 1 only when both its
production and provider-draft checks pass. An affected scenario can therefore
make up to four provider requests. The `retries` metric counts only the full
scenario retries, and the retained observations record only the selected
attempt; unselected retry and inner-fallback usage is not available.

## Result

Both models preserved the production safety contract and parsed all selected
provider drafts. Mini had three more retained provider-draft passes and was
faster; Luna used fewer output tokens and has a substantially lower
selected-usage rate-card proxy.

| Metric | GPT-5.6 Luna | GPT-5.4 Mini |
| --- | ---: | ---: |
| Production scenarios passed | 27 / 27 | 27 / 27 |
| Provider-draft parses | 27 / 27 | 27 / 27 |
| Retained provider-draft passes | 18 / 27 | 21 / 27 |
| Provider-draft failures | 9 | 6 |
| Average selected latency | 1,973.7 ms | 1,243.2 ms |
| Maximum selected latency | 3,993 ms | 2,188 ms |
| Total wall time | 74,474 ms | 40,418 ms |
| Selected input tokens | 32,630 | 32,630 |
| Selected cached-input tokens | 29,474 | 0 |
| Selected output tokens | 2,464 | 2,982 |
| Harness selected-use cost | not available | $0.0378915 |
| Harness retries | 10 | 6 |

Luna's selected average latency was 58.8% higher and its end-to-end suite time
was 84.3% longer. It generated 17.4% fewer selected output tokens. This table
retains the selected-attempt aggregates; per-scenario generated observations
are intentionally excluded from this PR.

## Cost interpretation

The direct OpenAI billing API was unavailable to this run, and no official
direct Luna price was added to the local catalog. For a consistent comparison,
the public [OpenRouter Luna](https://openrouter.ai/openai/gpt-5.6-luna) and
[OpenRouter Mini](https://openrouter.ai/openai/gpt-5.4-mini) rates captured on
the run date are used only as a rate proxy:

| Model | Input / M | Cache read / M | Output / M | Selected-use proxy |
| --- | ---: | ---: | ---: | ---: |
| GPT-5.6 Luna | $0.20 | $0.02 | $1.20 | $0.00417748 |
| GPT-5.4 Mini | $0.75 | $0.075 | $4.50 | $0.03789150 |

The selected-usage proxy puts Luna at 9.07x lower cost (an 89.0% reduction).
This is neither an invoice nor a lower bound: direct-provider billing can
differ, and the reported selected attempts omit any unselected retry or inner
HTTP-fallback requests.

## What the behavioral result does and does not show

The fresh pair does not support the earlier provider-draft tie: Mini retained
21/27 passes and Luna 18/27. Luna's extra failures include task-assignment and
member-agreement status/action assertions in addition to GitHub query fields;
Mini's only non-intent failure was the default GitHub state assertion. Both
models also have free-form intent-label mismatches, which are useful diagnostics
but are not executable-plan failures.

All 27 production outcomes passed because the canonical suite is primarily a
policy and deterministic-routing regression suite: 26 of its 27 fixtures have
a deterministic ownership path. The result demonstrates that Luna integrates
with the production safety boundary. It is not a broad quality benchmark for
arbitrary planning, job matching, or resume extraction.

## Historical OpenRouter attempt

The initial OpenRouter preflight for both models returned HTTP 403 before any
provider draft or usage. The account-level message cited a provider Terms of
Service restriction and named no provider. That route remains blocked, but it
does not affect this direct-OpenAI evaluation.

## Decision and activation

The workspace is configured for Luna as a cost-first, direct-OpenAI choice at
the user's request. The fresh audit shows this saves substantial proxy cost but
does not establish quality equivalence with Mini; Mini performed better on this
single provider-draft pair. The ignored local `.env` pins these selectors to
`gpt-5.6-luna`:

- `OPENAI_MODEL` for job-requirement extraction and candidate reranking.
- `AGENT_FALLBACK_MODEL` for the Discord agent and lead-classification fallback.
- `RESUME_AI_MODEL` for resume extraction.
- `AGENT_EVAL_OPENAI_MODEL` for future primary eval runs.

This local activation does not modify any deployment dashboard or other
environment. Do not treat the planner-only result as resume-workflow evidence;
mirror the variables in a deployment only after accepting that scope and
monitoring output quality.
