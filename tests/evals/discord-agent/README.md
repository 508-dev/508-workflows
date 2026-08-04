# Discord Agent Eval Harness

This directory contains fixture-driven evals for the Discord agent gateway.

The shape follows the same generic eval-harness pattern as the Voy share-chat
suite, adapted for this Python/`uv` repo:

- versioned JSON fixtures under `fixtures/v1`
- an explicit fixture catalog in `fixtures/v1/index.json`
- normalized observed output in `reports/observed.<suite>.<model>.json`
- compact Markdown summaries in `reports/score.<suite>.<model>.md`
- detailed traces in `reports/trace.<suite>.<model>.md`
- CTRF output in `reports/ctrf.<suite>.<model>.json`
- deterministic checks that are suitable for PR gating

For this agent flow, multi-turn replay is not the first-class concern. Discord
thread context can be represented in a fixture with `request.thread`, and the
runner uses the latest user message as the turn under test. The initial suite
focuses on planner/router correctness, policy outcomes, confirmation gates, and
known-good deterministic tool behavior.

`canonical` is the PR-gating suite. CI always runs the deterministic parser path
so pull requests do not depend on provider secrets, but the job first checks the
changed files and skips eval execution for unrelated changes. The CI job is
attached to the GitHub `test` environment; CI also runs `--live-planner` when
matching credentials are available. If `OPENAI_BASE_URL` is set for an
OpenAI-compatible provider such as OpenRouter or Bifrost, CI pairs it with
`OPENAI_API_KEY`; otherwise it uses `OPENAI_API_KEY_DIRECT` first and falls back
to `OPENAI_API_KEY` against the direct OpenAI endpoint. Fixture-level
`stub_results` keep read-only external tools from hitting CRM, GitHub, or other
live systems.

## Commands

```bash
uv run python scripts/agent_eval.py --suite canonical --live-planner --profile openai-direct
uv run python scripts/agent_eval.py --list-profiles
uv run python scripts/agent_eval.py --suite canonical --scenarios create_task_confirmation_001 --json
uv run python scripts/agent_eval.py --suite canonical --tags task
uv run python scripts/agent_eval.py --suite canonical
```

Reports are written to `tests/evals/discord-agent/reports/`.
The CLI loads `.env` by default without overriding exported environment values.
Use `--no-env-file` to disable that behavior or `--env-file <path>` to point at
another file.

## Viewing Results

Open `tests/evals/discord-agent/reports/index.html` after a run. GitHub Actions
also uploads the whole reports directory as the `discord-agent-eval-reports`
artifact, including that static viewer.

That directory is plain static HTML/JSON/Markdown. If we want a deployed viewer,
the simplest path is to publish the latest artifact or copied reports directory
behind any static host. A richer app can come later if we want run history,
filtering, or PR comparison across commits.

## Model Profiles

With `--live-planner`, the configured provider returns a structured tool-call
draft. The harness still does not let the model authorize users or perform side
effects: deterministic policy checks scopes, write actions stop at confirmation,
and read actions use fixture stubs when provided.
For a request that production can route deterministically, the harness evaluates
that same production route while retaining the provider draft in
`raw_model_output` for inspection.

Without `--live-planner`, the runner uses the deterministic parser path. That is
useful for local no-key debugging, but it is not the main PR gate.

Built-in profiles:

- `primary`: `OPENAI_API_KEY` with `OPENAI_BASE_URL` when a custom provider is configured; otherwise `OPENAI_API_KEY_DIRECT` or `OPENAI_API_KEY` against direct OpenAI. Defaults to `openai/gpt-4.1-mini` for OpenRouter and `gpt-4.1-mini` otherwise
- `openai-direct`: `OPENAI_API_KEY_DIRECT`, default model `gpt-4.1-mini`
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

## PR Gate

GitHub Actions runs the canonical suite on every pull request commit:

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary --no-env-file
```

If matching live planner credentials are configured in the GitHub `test`
environment, CI also runs:

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary --live-planner --no-env-file
```

The generated report directory is uploaded as a CI artifact. Open
`tests/evals/discord-agent/reports/index.html` from the artifact for a simple
static viewer.
