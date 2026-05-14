# Discord Agent Eval Harness

This is the PR-gated eval harness for the Discord agent planner/router.

## Goals

- Replay realistic Discord agent requests from checked-in fixtures.
- Score live planner routing plus deterministic policy, confirmation, and tool argument behavior.
- Keep external tool side effects out of PR CI with fixture stubs.
- Preserve deterministic no-key runs as a local diagnostic fallback.

## Layout

- `tests/evals/discord-agent/fixtures/v1/index.json`: fixture catalog.
- `tests/evals/discord-agent/fixtures/v1/*.json`: versioned scenarios.
- `tests/evals/discord-agent/schema/trajectory.v1.schema.json`: fixture contract.
- `tests/evals/discord-agent/schema/observed.v1.schema.json`: observed report contract.
- `tests/evals/discord-agent/reports/`: generated reports, ignored by git.
- `scripts/agent_eval.py`: thin CLI wrapper around `five08.agent.evals`.

## PR Command

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary --no-env-file
```

The canonical PR run executes the deterministic parser path so pull requests do
not fail just because provider secrets are unavailable. If `OPENAI_API_KEY_DIRECT`
or `OPENAI_API_KEY` is present in CI, the workflow also runs:

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary --live-planner --no-env-file
```

The live planner run asks the configured model to draft a structured plan, then
executes the same deterministic policy and tool validation used by the agent
harness. The model does not get to authorize users or perform side effects.
Write tools stop at confirmation, and read tools use deterministic in-memory
state or fixture `stub_results`.

For no-key local debugging, omit `--live-planner`:

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary
```

## Reports

Each run writes:

- `observed.canonical.<profile>.json`: normalized machine-readable observations.
- `score.canonical.<profile>.md`: compact pass/fail summary.
- `trace.canonical.<profile>.md`: per-scenario debugging trace.
- `ctrf.canonical.<profile>.json`: CTRF test-report output for CI consumers.
- `index.html`: a static report index you can open locally or publish as static files.

Live planner reports include total elapsed time, time to first scenario, provider
latency, parse success, token usage when the provider returns it, and estimated
cost from the packaged model profile catalog.

In CI, download the `discord-agent-eval-reports` artifact and open `index.html`.
For a deployed viewer, publish that reports directory to any static host. The
current harness does not require a backend to view a single run.

## Fixture Guidance

Fixtures should describe behavior boundaries, not implementation details. Prefer
small single-purpose scenarios with explicit expected action names and argument
subsets. Use `stub_results` for external read tools so the PR gate stays
deterministic.

For Discord thread behavior, set `request.thread` and put the latest user
message last. The harness treats that message as the current turn.

Use `known_failure` only for accepted gaps that should remain visible in the
report without blocking the PR.

## Model Benchmarking

Model comparisons run the same canonical fixtures through another live model
draft and then through the deterministic policy/tool layer:

```bash
uv run python scripts/agent_eval.py --suite canonical --live-planner --profile openai-direct
uv run python scripts/agent_eval.py --suite canonical --live-planner --profile fireworks-kimi
uv run python scripts/agent_eval.py --suite canonical --live-planner --profile all
```

These runs require the relevant provider keys in the environment or `.env`.
