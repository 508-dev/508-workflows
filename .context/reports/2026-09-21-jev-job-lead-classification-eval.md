# Jev job-lead classification evaluation

- Evaluated (UTC): `2026-09-20T19:57:45.070955+00:00`
- Runtime revision: `3853befc65ddb6e37840084858a64fdb6204e5cf`
- Corpus: `tests/evals/job-lead-classification/fixtures/v1/corpus.json` (48 cases)
- Network repeats per case: 3
- Jev: `typesafe/jev-1.13` through OpenRouter Decisions
- LLM baseline: `gpt-5.6-luna` through direct OpenAI

## Decision

Jev is strong enough to test as a shadow or canary classifier for the binary
"contractor-friendly" decision, but this synthetic corpus is not sufficient
evidence for an immediate production replacement. Across 144 repeated calls,
Jev reached 100.0% binary F1 with stable labels on all 48 cases. Compared with
Luna on the same calls, Jev was 2.9x faster at p50, 3.1x faster at p95, and
8.5x cheaper, while improving joint accuracy from 69.4% to 95.8%.

A reasonable first canary policy is a symmetric `0.80` confidence gate: this
accepted 93.1% of calls at 100.0% binary accuracy in this run and would send the
remaining 6.9% to the existing classifier. Keep the deterministic source,
reply, and seeking-work filters, plus the production output validator, in front
of any model decision. Validate next on a sanitized, held-out sample of
historical posts and then with labeled live shadow traffic before raising
coverage. The OpenRouter Decisions route is currently under `/api/alpha`, so
pin and monitor its request/response contract before production use.

Use Jev only for the binary decision initially. Its only errors were two
four-way posting-type classifications: it labeled a closed role and a
seeking-work post as `part_time`, while still correctly rejecting both as not
contractor-friendly. If the four-way type is operationally required, add an
explicit current-job-post gate or retain the existing normalizer for that
field.

Luna's result measures the actual production prompt, schema, and normalizer,
not unconstrained model capability. Diagnostic output showed cross-field
inconsistency (a contractor-friendly boolean paired with a disallowed
`full_time` type), which the production normalizer correctly rejected. Improve
that contract before using this result to make broader conclusions about Luna.

No production classification path was changed by this evaluation.

## Results

| Profile | Successful calls | Contractor F1 | Posting accuracy | Joint accuracy | Stable cases | Latency p50 / p95 / max | Input / cached / output tokens | Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| heuristic | 48/48 | 79.2% | 62.5% | 62.5% | deterministic | 0 / 0 / 1 ms | 0 / 0 / 0 | $0.000000 |
| jev | 144/144 | 100.0% | 95.8% | 95.8% | 48/48 | 428 / 616 / 3292 ms | 73650 / 0 / 10590 | $0.003093 |
| luna | 144/144 | 56.0% | 70.8% | 69.4% | 43/48 | 1249 / 1919 / 2679 ms | 56109 / 0 / 12563 | $0.026297 |

The heuristic is local code, so its latency and zero cost are not an API-to-API comparison. Joint accuracy requires both the contractor-friendly boolean and the four-way posting type to match the golden label.

## Core versus challenge cases

| Profile | Core joint accuracy | Challenge joint accuracy | False positives | False negatives |
| --- | ---: | ---: | ---: | ---: |
| heuristic | 65.6% | 56.2% | 5 | 5 |
| jev | 96.9% | 93.8% | 0 | 0 |
| luna | 72.9% | 62.5% | 0 | 44 |

## Jev confidence gate

A symmetric gate accepts positive decisions at or above the threshold, negative decisions at or below `1 - threshold`, and falls back for the middle band.

| Threshold | Coverage | Accuracy when accepted | False positives | False negatives |
| ---: | ---: | ---: | ---: | ---: |
| 0.50 | 100.0% | 100.0% | 0 | 0 |
| 0.70 | 97.9% | 100.0% | 0 | 0 |
| 0.80 | 93.1% | 100.0% | 0 | 0 |
| 0.90 | 70.8% | 100.0% | 0 | 0 |
| 0.95 | 50.7% | 100.0% | 0 | 0 |

Jev contractor-probability Brier score: `0.012865`. Lower is better.

## Classification mismatches

### heuristic

| Case | Runs | Expected | Observed | Contractor probability |
| --- | ---: | --- | --- | ---: |
| `both_contract_to_hire_choices_001` | 1 | part_time_or_full_time/true | unknown/false | - |
| `both_employee_or_b2b_001` | 1 | part_time_or_full_time/true | part_time/true | - |
| `both_hours_or_salary_001` | 1 | part_time_or_full_time/true | full_time/false | - |
| `both_permanent_or_fixed_001` | 1 | part_time_or_full_time/true | part_time/true | - |
| `both_staff_and_freelance_001` | 1 | part_time_or_full_time/true | part_time/true | - |
| `both_w2_or_1099_001` | 1 | part_time_or_full_time/true | part_time/true | - |
| `full_time_employee_only_001` | 1 | full_time/false | unknown/false | - |
| `full_time_salaried_001` | 1 | full_time/false | unknown/false | - |
| `full_time_vendor_contract_001` | 1 | full_time/false | unknown/false | - |
| `full_time_w2_001` | 1 | full_time/false | unknown/false | - |
| `part_time_b2b_001` | 1 | part_time/true | unknown/false | - |
| `part_time_consulting_001` | 1 | part_time/true | unknown/false | - |
| `part_time_unrelated_negation_001` | 1 | part_time/true | unknown/false | - |
| `unknown_closed_role_001` | 1 | unknown/false | part_time/true | - |
| `unknown_past_contractors_001` | 1 | unknown/false | part_time/true | - |
| `unknown_prompt_injection_001` | 1 | unknown/false | part_time/true | - |
| `unknown_reply_001` | 1 | unknown/false | part_time/true | - |
| `unknown_terms_unsettled_001` | 1 | unknown/false | part_time/true | - |

### jev

| Case | Runs | Expected | Observed | Contractor probability |
| --- | ---: | --- | --- | ---: |
| `unknown_closed_role_001` | 3 | unknown/false | part_time/false | 0.18 |
| `unknown_seeking_work_001` | 3 | unknown/false | part_time/false | 0.04 |

### luna

| Case | Runs | Expected | Observed | Contractor probability |
| --- | ---: | --- | --- | ---: |
| `both_contract_to_hire_choices_001` | 1 | part_time_or_full_time/true | full_time/false | - |
| `both_employee_or_b2b_001` | 3 | part_time_or_full_time/true | full_time/false | - |
| `both_parenthetical_001` | 2 | part_time_or_full_time/true | full_time/false | - |
| `both_permanent_or_fixed_001` | 1 | part_time_or_full_time/true | full_time/false | - |
| `both_region_specific_001` | 3 | part_time_or_full_time/true | full_time/false | - |
| `both_staff_and_freelance_001` | 2 | part_time_or_full_time/true | full_time/false | - |
| `both_w2_or_1099_001` | 3 | part_time_or_full_time/true | full_time/false | - |
| `part_time_b2b_001` | 3 | part_time/true | unknown/false | - |
| `part_time_cant_wait_001` | 3 | part_time/true | unknown/false | - |
| `part_time_consulting_001` | 3 | part_time/true | unknown/false | - |
| `part_time_contract_explicit_001` | 3 | part_time/true | unknown/false | - |
| `part_time_freelance_001` | 3 | part_time/true | unknown/false | - |
| `part_time_hours_001` | 2 | part_time/true | part_time/false | - |
| `part_time_negated_full_time_001` | 3 | part_time/true | unknown/false | - |
| `part_time_not_only_001` | 3 | part_time/true | unknown/false | - |
| `part_time_project_001` | 3 | part_time/true | unknown/false | - |


## Method and limitations

- The corpus is a balanced, synthetic challenge set derived from the production label contract. It deliberately over-represents negation, commercial uses of the word `contract`, non-posts, and prompt-injection-like text; it does not estimate live HN prevalence.
- Golden labels are exact and scoring is deterministic. No model judges another model.
- The experiment applies the classification-harness pattern described in LangChain's [Jev harness article](https://www.langchain.com/blog/building-a-harness-with-jev).
- Jev uses OpenRouter's `/api/alpha/decisions` endpoint and the pinned [`typesafe/jev-1.13`](https://openrouter.ai/typesafe/jev-1.13/) request ID. The resolved dated snapshot is retained in the JSON observation report.
- The Luna baseline uses the production job-lead prompt and schema through direct OpenAI. A preflight through OpenRouter returned HTTP 403 under provider terms, so the report does not present an unsupported route as a benchmark failure.
- Luna's self-reported classification confidence is retained as diagnostic metadata, but it is not treated as a calibrated contractor probability or used in the Jev confidence-gate analysis.
- Jev cost is provider-reported. Luna cost is estimated from successful retained token usage at the official [$0.20/M input, $0.02/M cached input, and $1.20/M output rates](https://developers.openai.com/api/docs/models/gpt-5.6-luna). Retried failed requests may not expose usage and may be absent.
- Raw observations are generated under the gitignored reports directory; this Markdown summary intentionally excludes provider payloads and secrets.
