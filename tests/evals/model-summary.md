# LLM Model Evaluation Summary

Last updated: 2026-05-13 local eval runs.

## Resume Extraction

Recommended default: `gpt-4.1-mini`.

Reason: in the retained 11-resume local fixture run, `gpt-4.1-mini` reached 11/11 LLM success with the strongest observed completeness score and low estimated cost. Timing from these runs should not be used as a deciding signal because the network was unstable during parts of the sweep.

Keep `gpt-5.5` as a quality ceiling or fallback. It was perfect in the retained run, but its cost is too high for bulk default use. `gpt-5-mini` and `gpt-5-nano` are viable cheap fallbacks only after targeted checks against fields that matter for CRM quality: phone, LinkedIn, GitHub, location, seniority enum, and role normalization.

## Live Planner

The original 6/13 Kimi result was mostly an eval/scorer problem, not a useful model-quality signal. The scorer was over-penalizing harmless intent names, casing differences, and equivalent wording such as "docs" vs "documentation"; OpenAI GPT-5 direct calls were also using the wrong chat-completion parameters.

Recommended default: `kimi-k2p6-fireworks` via the `fireworks-kimi` eval profile.

Fixed weekly rerun results:

- `kimi-k2p6-fireworks`: 13/13 passed, 0% bad-plan rate, 1942.6ms average latency.
- `gpt-4.1-mini`: 13/13 passed, 0% bad-plan rate, 2634.5ms average latency.
- `gpt-5.5`: 13/13 passed, 0% bad-plan rate, 2653.5ms average latency.
- `gpt-5-mini`: 11/13 passed, 15.38% bad-plan rate, 2316.9ms average latency.
- `gpt-5-nano`: 9/13 passed, 30.77% bad-plan rate, 2168.8ms average latency.

Interpretation: Kimi K2.6 is the best planner default on current data because it matched the top pass rate with lower latency and lower cost than OpenAI quality-ceiling models. `gpt-4.1-mini` is the best OpenAI fallback and is consistent with the resume-extraction default. `gpt-5.5` is a quality ceiling, not a bulk default. Keep deterministic policy gates and human confirmations for write actions.
