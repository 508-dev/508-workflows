# Discord Agent Eval Harness

This directory contains fixture-driven evals for the Discord agent gateway.

The shape is intentionally inspired by the Voy share-chat evals:

- versioned JSON fixtures under `fixtures/v1`
- an explicit fixture catalog in `fixtures/v1/index.json`
- normalized observed output in `reports/observed.<suite>.<model>.json`
- compact Markdown summaries in `reports/score.<suite>.<model>.md`
- deterministic checks that are suitable for PR gating

For this agent flow, multi-turn replay is not the first-class concern. Discord
thread context can be represented in a fixture with `request.thread`, and the
runner uses the latest user message as the turn under test. The initial suite
focuses on planner/router correctness, policy outcomes, confirmation gates, and
known-good deterministic tool behavior.

`canonical` is the small PR-gating slice. `weekly` is the broader regression
slice and can include fixture-level `stub_results` so read-only external tools
exercise planner, policy, and response shaping without hitting live services.

## Commands

```bash
uv run python scripts/agent_eval.py --suite canonical
uv run python scripts/agent_eval.py --suite weekly --profile primary
uv run python scripts/agent_eval.py --list-profiles
uv run python scripts/agent_eval.py --suite canonical --scenarios create_task_confirmation_001 --json
uv run agent-eval --suite canonical --profile fireworks-kimi
uv run agent-eval --suite weekly --live-planner --profile all
```

Reports are written to `tests/evals/discord-agent/reports/`.
The CLI loads `.env` by default without overriding exported environment values.
Use `--no-env-file` to disable that behavior or `--env-file <path>` to point at
another file.

## Model Profiles

By default the eval runner is deterministic: it does not let a model authorize
or execute tools, and it only swaps the model-routing metadata used by the
planner contract. Use `--live-planner` to call the configured provider for a
structured tool-call draft, then score that draft through the deterministic
policy and tool layer.

Built-in profiles:

- `primary`: `OPENAI_API_KEY_DIRECT`
- `openai-direct`: `OPENAI_API_KEY_DIRECT`
- `fireworks-kimi`: `FIREWORKS_API_KEY`, default model `accounts/fireworks/models/kimi-k2p6`
- `openrouter`: `OPENROUTER_API_KEY`, default model `openai/gpt-5-mini`
- `anthropic`: `ANTHROPIC_API_KEY`, native Messages API for live planner evals

Optional model override env vars:

- `AGENT_EVAL_OPENAI_MODEL`
- `AGENT_EVAL_FIREWORKS_MODEL`
- `AGENT_EVAL_OPENROUTER_MODEL`
- `AGENT_EVAL_ANTHROPIC_MODEL`

## Fixture Contract

- `version`: `discord-agent-trajectory.v1`
- `context`: `AgentIdentityContext`-like actor and tenant data
- `seed.tasks`: deterministic in-memory task records inserted before execution
- `stub_results`: optional deterministic tool payloads keyed by tool name
- `request.message`: single current request, or `request.thread` for a thread snapshot
- `expect`: deterministic checks over response status, plan intent, model tier,
  actions, arguments, scopes, result statuses, and clarification output
- `known_failure`: optional non-blocking marker for a known accepted gap

Write fixtures as behavior contracts. Do not change expected behavior just to
hide a regression.
