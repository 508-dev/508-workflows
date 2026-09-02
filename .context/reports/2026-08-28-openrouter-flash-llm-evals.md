# OpenRouter Planner Model Evaluation

- Initial flash-model sweep: 2026-08-28
- Qwen3.8 27B follow-up: 2026-08-30
- Repository commit: `cb2bce89e545c97cf19db18ae5b39230aaa91622`
- Runner: canonical Discord-agent live-planner eval, 27 scenarios, one harness retry after an unclassified production failure (`status == "failed"`) or any provider-draft failure; a production `known_failure` alone does not trigger a retry

## Decision

Do not change the production planner model based on these runs.

`deepseek/deepseek-v4-flash-0731` remains the best candidate for a guarded text-planner canary. It had the highest retained provider-draft pass rate, tied for the fewest non-`intent` failures, had a bounded observed latency tail, and had a much lower retained-usage rate-card estimate than Qwen3.8 27B. It was still not strong enough for an unguarded rollout: 8 of 27 retained provider drafts failed the harness after retry, including 4 failures beyond the free-form `intent` label check.

`z-ai/glm-5.3-flash` ranked second on availability but last on semantic quality among responses that parsed, and it had an extreme 383.8-second retained tail latency. `qwen/qwen3.8-flash` cannot be ranked fairly for production quality from this run because OpenRouter returned repeated HTTP 429 responses. Its valid JSON action drafts were promising, but the availability and latency observed here are disqualifying for an interactive Discord planner today.

`qwen/qwen3.8-27b` is the strongest second canary candidate, especially if multimodal input becomes a requirement. It parsed 27/27 retained responses and tied DeepSeek at 4 non-`intent` failures, with a faster average and median retained latency. Its disadvantages were a higher 80.59-second maximum, 12 retry triggers, and a $0.04996 retained-usage rate-card estimate—about 16.2 times DeepSeek's corresponding estimate. This run was text-only, so it did not evaluate the model's advertised image or video understanding.

The production safety result was good: all four runs produced 27/27 expected production outcomes with zero production failures. That is mainly evidence for the deterministic routing, validation, authorization, and confirmation boundaries—not model quality.

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
- `qwen/qwen3.8-27b`

Each run used OpenRouter's default provider routing, temperature 0, a 1,200-token completion cap, and a 90-second Requests timeout. No provider was pinned. The first HTTP request in each planner attempt included JSON-object `response_format`; after any HTTP error, including 429, the client immediately sent one more request without `response_format`. A subsequent scenario-level harness retry could repeat that pair, so one triggered scenario could make up to four provider requests. The report's `retries` metric counts only harness retries, not these inner HTTP fallback requests. Because these new IDs are absent from the repository model catalog, the runner did not send model-specific reasoning-effort options; OpenRouter/model defaults therefore influenced latency and token use.

The Qwen3.8 27B follow-up used the same text-only fixtures and configuration as the initial sweep. The canonical harness has no image or video fixtures, so this result says nothing about vision quality.

## Full-sweep results

The provider-draft score checks parse success; planned-versus-clarification status; expected `intent` and clarification text; required clarification presence; every drafted action's schema validity; expected action count; exact tool names; and expected argument values. Status, counts, schemas, and tool names use exact matching. Intent, clarification, and argument strings use tolerant matching: text is case-folded, punctuation and spacing are normalized, `documentation` is normalized to `docs`, and an observed string passes if it equals or starts with the expected string. Expected mappings may be subsets of observed mappings; lists must have equal length and match positionally. Results below are the selected outcome after at most one harness retry.

| Model | Production | Provider-draft pass | Parse success | Non-`intent` failures | Retry triggers | Retained latency p50 / p95 / max | Full wall time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek V4 Flash 0731 | 27/27 | 19/27 (70.4%) | 26/27 (96.3%) | 4/27 (14.8%) | 11/27 | 5.37s / 37.84s / 43.08s | 435.66s |
| GLM-5.3-Flash | 27/27 | 16/27 (59.3%) | 26/27 (96.3%) | 5/27 (18.5%) | 13/27 | 5.92s / 38.75s / 383.81s | 733.39s |
| Qwen3.8-Flash | 27/27 | 13/27 (48.1%) | 19/27 (70.4%) | 8/27 (29.6%) | 19/27 | 10.68s / 57.31s / 60.15s | 579.70s |
| Qwen3.8 27B | 27/27 | 18/27 (66.7%) | 27/27 (100%) | 4/27 (14.8%) | 12/27 | 5.27s / 21.85s / 80.59s | 347.44s |

“Non-`intent` failures” removes cases whose only failed check was the free-form `intent` label. It still includes provider/parse failures. “Retry triggers” is the number of scenarios whose first harness attempt had `status == "failed"` or a non-passing provider draft. A production `known_failure` does not trigger a retry unless its provider draft also fails. The retry replaces the first result only when both its production and provider-draft checks pass; otherwise the first result remains selected. The retained pass rates therefore overstate first-attempt reliability, while retained failures describe the first attempt rather than the failed retry.

Retained average latencies were 13.61 seconds for DeepSeek, 23.29 seconds for GLM, 13.92 seconds for Qwen3.8 Flash, and 9.45 seconds for Qwen3.8 27B. These averages use the selected result and exclude the unselected harness attempt. A selected attempt's latency can include its inner no-`response_format` fallback, while full wall time includes all harness retry and HTTP fallback work.

### Token and estimated cost snapshot

| Model | Retained input / cached / output tokens | Retained total tokens | Input / cache-read / output rate ($/1M) | Rate snapshot (UTC) | Retained-usage estimate |
| --- | ---: | ---: | ---: | ---: | ---: |
| DeepSeek V4 Flash 0731 | 33,127 / 11,264 / 7,988 | 41,115 | $0.070 / $0.017 / $0.170 | 2026-08-28 | $0.00308 |
| GLM-5.3-Flash | 32,155 / 9,344 / 6,750 | 38,905 | $0.075 / $0.015 / $0.250 | 2026-08-28 | $0.00354 |
| Qwen3.8-Flash | 25,184 / 18,688 / 4,717 | 29,901 | $0.160 / $0.016 / $0.470 | 2026-08-28 | $0.00356 |
| Qwen3.8 27B | 36,200 / 4,384 / 14,063 | 50,263 | $0.350 / $0.035 / $2.750 | 2026-08-29 | $0.04996 |

The estimate formula is `((input - cached) × input rate + cached × cache-read rate + output × output rate) / 1,000,000`. The rates above are the OpenRouter model-page values recorded when each estimate was calculated; the linked pages are mutable, and default routing could select a provider with different rates. These are retained-usage rate-card estimates, not lower bounds or billing totals: routed-provider prices could make the retained calls cheaper or more expensive than shown. They also exclude the unselected harness attempt, failed HTTP and fallback calls that returned no usage, provider-specific or routing premiums, and cache-creation or cache-storage charges. No separate routing charge was added. The harness itself reported cost as `None` because these new model IDs are absent from the packaged model profile catalog.

Pricing references: [DeepSeek V4 Flash 0731](https://openrouter.ai/deepseek/deepseek-v4-flash-0731), [GLM-5.3-Flash](https://openrouter.ai/z-ai/glm-5.3-flash), [Qwen3.8-Flash](https://openrouter.ai/qwen/qwen3.8-flash), and [Qwen3.8 27B](https://openrouter.ai/qwen/qwen3.8-27b).

## Failure analysis

### Cross-model scorer and contract issue

DeepSeek had 4, GLM had 6, Qwen3.8 Flash had 6, and Qwen3.8 27B had 5 retained failures where the only mismatch was `provider_draft.intent`. Examples include `search_crm_contacts` versus `lookup_contact`, `crm_contact_lookup`, or `lookup_caleb_contact` while the tool and arguments were correct.

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

- Seven of the eight initial parse failures were OpenRouter HTTP 429 scenario outcomes; the eighth returned `message.content = None`. Because every initial HTTP error caused an immediate second request without `response_format`, each 429 attempt represents two failed HTTP requests, although the artifacts record only the resulting scenario attempt.
- A spaced recovery pass reran the seven 429 scenarios individually with a 15-second gap. The selected results contain two passes, one valid JSON response that used `state: all` instead of `open`, and four HTTP 429 outcomes. For those four 429 selections, the unrecorded retry outcome is unknown unless it fully passed, because a failed retry does not replace the original result.
- One successful recovery call took 62.89 seconds.
- In the original full sweep, every valid parsed failure was only an `intent` label mismatch. The recovery pass nevertheless found a substantive GitHub state mismatch, so valid-output quality is promising but not yet established.

### Qwen3.8 27B

- All 27 retained responses parsed successfully; there were no provider or parse failures.
- Five of its nine provider-draft failures were only free-form `intent` label differences.
- It asked for a concrete date instead of planning the task because “Friday” was ambiguous. This is defensible safety behavior but does not satisfy the fixture contract.
- It used `state: all` or omitted state in two GitHub searches where the fixture expects `open`.
- For the member-agreement scenario that requires resolving an email, it drafted a CRM lookup instead of returning the fixture's expected clarification. That is a reasonable first step, but the current one-shot provider-draft contract cannot chain the read result into a subsequent DocuSeal action.
- Its retained p95 was 21.85 seconds, but the maximum was 80.59 seconds. Retained output usage was 14,063 tokens, contributing most of its approximately $0.04996 retained-usage rate-card estimate.

## Coverage caveat

The canonical suite is no longer a broad end-to-end LLM-routing benchmark. Direct inspection of the runtime ownership boundary showed that 26 of 27 fixtures are handled by `_plan_deterministic_workflow`; only `crm_contact_info_lookup_001` currently depends on the live draft. All four models chose the correct CRM search tool and arguments in that live-dependent case. Their only provider-probe mismatch there was the ignored free-form `intent` label.

Accordingly:

- 27/27 production validates deterministic ownership and containment.
- The provider-draft probe is still useful for comparing raw model semantics across all fixtures.
- It does not demonstrate that the models can reliably own 27 production workflows.
- More canonical fixtures should exercise genuinely model-owned phrasing and fallback boundaries, and the report schema should record the runtime owner for each scenario.

## Recommended next steps

1. Keep the current production default. If a canary is desired, test DeepSeek V4 Flash 0731 behind the existing deterministic gates, a hard deadline, and immediate fallback. Qwen3.8 27B is the second candidate, but its higher retained cost and latency tail need explicit acceptance.
2. Rerun all candidates at least three times and include the current production baseline on the same commit and 27-scenario suite. Do not compare these numbers directly with the older 13-scenario results in `tests/evals/model-summary.md`.
3. Fix provider-probe scoring so free-form `intent` synonyms do not fail an otherwise correct executable plan. Prefer stable action IDs and tool/argument contracts.
4. Add paced 429 backoff, restrict the no-`response_format` fallback to format-related errors, enforce a hard suite/call deadline, and record every harness attempt and HTTP fallback with latency, status, provider, token usage, and cost. The current selection rule keeps a successful retry but otherwise keeps the original failure, hiding the unselected attempt and its billing either way.
5. Add the four model profiles and current OpenRouter pricing to the model catalog, or capture provider-reported cost directly.
6. Before selecting Qwen3.8 27B for multimodal work, add sanitized image and video fixtures with assertions for extraction accuracy, tool arguments, and refusal behavior. The current suite does not exercise vision.
7. Add sanitized golden corpora for resume/profile and skill extraction, HN lead classification, job requirement extraction, and candidate reranking. Score field accuracy and ranking quality, not only parse/completeness success.

## Verification and artifacts

- Focused harness tests: `22 passed` in `tests/unit/test_agent_evals.py`.
- Deterministic canonical replay: 26 passed, 0 failed, 1 known failure.
- Live runs: four complete 27-scenario sweeps plus the seven-scenario Qwen3.8 Flash recovery pass.
- Raw machine reports and traces are retained locally in the ignored [eval artifact directory](../../tests/evals/discord-agent/reports/llm-workflow-flash-comparison-2026-08-28/) rather than committed as long-lived `.context` transcripts.
