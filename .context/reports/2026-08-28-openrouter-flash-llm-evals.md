# OpenRouter Flash Model Evaluation

- Date: 2026-08-28
- Repository commit: `cb2bce89e545c97cf19db18ae5b39230aaa91622`
- Runner: canonical Discord-agent live-planner eval, 27 scenarios, one automatic retry for any production or provider-draft failure

## Decision

Do not change the production planner model based on this sweep.

Of the three candidates, `deepseek/deepseek-v4-flash-0731` is the best candidate for a guarded canary. It had the highest retained strict provider-draft pass rate, the fewest non-`intent` failures, the best median latency, and no observed OpenRouter HTTP failures. It was still not strong enough for an unguarded rollout: 8 of 27 retained provider drafts failed the harness after retry, including 4 failures beyond the free-form `intent` label check.

`z-ai/glm-5.3-flash` ranked second on availability but last on semantic quality among responses that parsed, and it had an extreme 383.8-second retained tail latency. `qwen/qwen3.8-flash` cannot be ranked fairly for production quality from this run because OpenRouter returned repeated HTTP 429 responses. Its valid JSON action drafts were promising, but the availability and latency observed here are disqualifying for an interactive Discord planner today.

The production safety result was good: all three runs produced 27/27 expected production outcomes with zero production failures. That is mainly evidence for the deterministic routing, validation, authorization, and confirmation boundaries—not model quality.

## What eval coverage exists

The repository has two purpose-built eval harnesses:

1. [Discord-agent trajectory evals](../../docs/discord-agent-eval-harness.md) replay versioned fixtures through the planner boundary, deterministic routing, policy, confirmation gates, and stubbed tools. They generate JSON, Markdown, HTML, trace, and CTRF reports and have a CI path for deterministic and credential-dependent live runs.
2. [Resume-extraction evals](../../tests/evals/resume-extraction/README.md) compare extraction completeness, fallback behavior, latency, tokens, and cost against a private local resume corpus.

The resume corpus in this workspace contains only `.gitkeep`, so it could not be run. Its score is also completeness-oriented rather than golden field-level accuracy.

No checked-in golden/live eval suite was found for these other LLM-backed paths:

- [intent normalization](../../packages/shared/src/five08/agent/intent_normalizer.py)
- [resume skill extraction](../../packages/shared/src/five08/resume_skills_extractor.py)
- [HN job-lead classification](../../packages/shared/src/five08/job_lead_sources.py)
- [job requirement extraction and candidate reranking](../../packages/shared/src/five08/job_match.py)

Those paths have unit tests with mocked model responses, and several have deterministic fallbacks, but they do not have model-vs-model behavioral evals.

## Run configuration

The exact IDs were resolved from OpenRouter's live model catalog. The dated DeepSeek GA revision was pinned instead of using a moving `latest` alias:

- `deepseek/deepseek-v4-flash-0731`
- `z-ai/glm-5.3-flash`
- `qwen/qwen3.8-flash`

Each run used OpenRouter's default provider routing, JSON-object response format, temperature 0, a 1,200-token completion cap, and a 90-second Requests timeout. No provider was pinned. Because these new IDs are absent from the repository model catalog, the runner did not send model-specific reasoning-effort options; OpenRouter/model defaults therefore influenced latency and token use.

## Full-sweep results

The strict provider-draft result checks parsing, status, the free-form `intent` string, tool names, and expected argument values. Results below are the retained outcome after the harness's one automatic retry.

| Model | Production | Strict draft pass | Parse success | Non-`intent` failures | Retry triggers | Retained latency p50 / p95 / max | Full wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek V4 Flash 0731 | 27/27 | 19/27 (70.4%) | 26/27 (96.3%) | 4/27 (14.8%) | 11/27 | 5.37s / 37.84s / 43.08s | 435.66s |
| GLM-5.3-Flash | 27/27 | 16/27 (59.3%) | 26/27 (96.3%) | 5/27 (18.5%) | 13/27 | 5.92s / 38.75s / 383.81s | 733.39s |
| Qwen3.8-Flash | 27/27 | 13/27 (48.1%) | 19/27 (70.4%) | 8/27 (29.6%) | 19/27 | 10.68s / 57.31s / 60.15s | 579.70s |

“Non-`intent` failures” removes cases whose only failed check was the free-form `intent` label. It still includes provider/parse failures. “Retry triggers” is the number of scenarios whose first attempt failed either production or provider-draft checks; the retained pass rates therefore overstate first-attempt reliability.

Retained average latencies were 13.61 seconds for DeepSeek, 23.29 seconds for GLM, and 13.92 seconds for Qwen. These averages exclude discarded attempts, while full wall time includes retry work.

### Token and estimated cost snapshot

| Model | Retained input / cached / output tokens | Retained total tokens | Lower-bound list-price estimate |
| --- | ---: | ---: | ---: |
| DeepSeek V4 Flash 0731 | 33,127 / 11,264 / 7,988 | 41,115 | $0.00308 |
| GLM-5.3-Flash | 32,155 / 9,344 / 6,750 | 38,905 | $0.00354 |
| Qwen3.8-Flash | 25,184 / 18,688 / 4,717 | 29,901 | $0.00356 |

These are lower bounds calculated from retained token usage and OpenRouter's model-page prices at run time. They exclude discarded retry attempts and calls that returned no usage, so they are not billing totals. The harness itself reported cost as `None` because these new model IDs are absent from the packaged model profile catalog.

Pricing references: [DeepSeek V4 Flash 0731](https://openrouter.ai/deepseek/deepseek-v4-flash-0731), [GLM-5.3-Flash](https://openrouter.ai/z-ai/glm-5.3-flash), and [Qwen3.8-Flash](https://openrouter.ai/qwen/qwen3.8-flash).

## Failure analysis

### Cross-model scorer and contract issue

DeepSeek had 4, GLM had 6, and Qwen had 6 retained failures where the only mismatch was `provider_draft.intent`. Examples include `search_crm_contacts` versus `lookup_contact`, `crm_contact_lookup`, or `lookup_caleb_contact` while the tool and arguments were correct.

The planner prompt defines `intent` only as `short_snake_case_or_null`, but the eval compares it with a canonical fixture string. Production ignores the drafted label and derives canonical intent from the validated tool. These failures are useful evidence that `intent` is not a stable contract, but they should not count as bad executable plans. Use a stable intent/action enum in the prompt and schema, derive it deterministically from the tool, or omit it from `provider_expect`.

### DeepSeek V4 Flash 0731

- One malformed JSON-semantic response used an invalid key in place of `status` for the GitHub todo scenario.
- It asked for clarification instead of creating the task because “Friday” was not a concrete date. That is defensible safety behavior, but it does not satisfy the current fixture contract.
- It used `state: all` or omitted state in two GitHub searches where the fixture expects `open`.
- Four other failures were only free-form `intent` label differences.

### GLM-5.3-Flash

- One response had `message.content = None` and could not be parsed.
- It asked which project to use even though the task request explicitly named Atlas.
- It used `state: all` for both GitHub searches that expect `open`.
- For a member agreement with no email, it drafted a CRM lookup plus a DocuSeal write containing placeholder values instead of asking for clarification. Production deterministic routing correctly contained this.
- Six other failures were only free-form `intent` label differences.
- One retained GitHub search call took 383.81 seconds despite the configured 90-second request timeout, indicating that the timeout is not functioning as a hard end-to-end deadline for this provider path.

### Qwen3.8-Flash

- Seven of the eight initial parse failures were OpenRouter HTTP 429 responses; the eighth returned `message.content = None`.
- A spaced recovery pass reran the seven 429 scenarios individually with a 15-second gap. Two passed, one returned valid JSON but used `state: all` instead of `open`, and four still ended in HTTP 429 after the harness retry.
- One successful recovery call took 62.89 seconds.
- In the original full sweep, every valid parsed failure was only an `intent` label mismatch. The recovery pass nevertheless found a substantive GitHub state mismatch, so valid-output quality is promising but not yet established.

## Coverage caveat

The canonical suite is no longer a broad end-to-end LLM-routing benchmark. Direct inspection of the runtime ownership boundary showed that 26 of 27 fixtures are handled by `_plan_deterministic_workflow`; only `crm_contact_info_lookup_001` currently depends on the live draft. All three models chose the correct CRM search tool and arguments in that live-dependent case. Their only provider-probe mismatch there was the ignored free-form `intent` label.

Accordingly:

- 27/27 production validates deterministic ownership and containment.
- The provider-draft probe is still useful for comparing raw model semantics across all fixtures.
- It does not demonstrate that the models can reliably own 27 production workflows.
- More canonical fixtures should exercise genuinely model-owned phrasing and fallback boundaries, and the report schema should record the runtime owner for each scenario.

## Recommended next steps

1. Keep the current production default. If a canary is desired, test DeepSeek V4 Flash 0731 behind the existing deterministic gates, a hard deadline, and immediate fallback.
2. Rerun all candidates at least three times and include the current production baseline on the same commit and 27-scenario suite. Do not compare these numbers directly with the older 13-scenario results in `tests/evals/model-summary.md`.
3. Fix provider-probe scoring so free-form `intent` synonyms do not fail an otherwise correct executable plan. Prefer stable action IDs and tool/argument contracts.
4. Add paced 429 backoff, a hard suite/call deadline, and per-attempt records for latency, status, provider, token usage, and cost. Retaining only the final retry hides first-attempt behavior and billing.
5. Add the three model profiles and current OpenRouter pricing to the model catalog, or capture provider-reported cost directly.
6. Add sanitized golden corpora for resume/profile and skill extraction, HN lead classification, job requirement extraction, and candidate reranking. Score field accuracy and ranking quality, not only parse/completeness success.

## Verification and artifacts

- Focused harness tests: `22 passed` in `tests/unit/test_agent_evals.py`.
- Deterministic canonical replay: 26 passed, 0 failed, 1 known failure.
- Live runs: three complete 27-scenario sweeps plus the seven-scenario Qwen recovery pass.
- Raw machine reports and traces are retained locally in the ignored [eval artifact directory](../../tests/evals/discord-agent/reports/llm-workflow-flash-comparison-2026-08-28/) rather than committed as long-lived `.context` transcripts.
