# Discord Agent Eval Harness

This is the eval harness for the Discord agent planner/router.

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
not fail just because provider secrets are unavailable. The CI eval job first
checks the changed files and skips dependency install/eval execution unless the
PR touched Discord agent, shared planner, eval fixture/runner, model catalog, or
dependency/workflow files. The job itself still completes successfully so the
aggregate status check has a stable dependency.

The CI eval job is attached to the GitHub `test` environment. If
`OPENAI_API_KEY_DIRECT` or `OPENAI_API_KEY` is present there, the workflow also
runs:

```bash
uv run python scripts/agent_eval.py --suite canonical --model primary --live-planner --no-env-file
```

The live planner run uses the same structured planner contract and system prompt
as the production gateway, then executes the same deterministic policy and tool
validation used by the agent harness. The model does not get to authorize users
or perform side effects.
Write tools stop at confirmation, and read tools use deterministic in-memory
state or fixture `stub_results`. `OPENAI_BASE_URL` from the `test` environment is
passed through for OpenAI-compatible providers such as OpenRouter.

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

Keep the `canonical` suite limited to cases that should gate both deterministic
and live-planner PR runs. When adding a new tool family, first cover it with
unit tests and deterministic `weekly` fixtures; promote those fixtures to
`canonical` only after the live planner prompt/tool schema reliably routes them.

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

## Discord mention routing

Mention routing has its own eval because the planner suite starts after Discord
has selected a handler. The [mention corpus](../tests/evals/discord-agent/routing/mentions.v1.json)
uses synthetic latest-turn messages after the mention eligibility, capture, and
local-reply checks. Its labels identify the intended handler for job matching,
knowledge questions, and backend workflows. The challenge cases preserve
examples of fuzzy phrasing for which rules or candidates can miss the intent.
The regular unit suite enforces the other route labels and checks that an
actual mention about job-posting policy reaches the knowledge handler.

```bash
uv run --locked python scripts/agent_routing_eval.py
uv run --locked python scripts/agent_routing_eval.py --live --threshold 0.85
```

The first command replays the production routing function locally. The second
compares it against Jev 1.14, Clef, and Clef Flash through the
[OpenRouter Decisions API](https://openrouter.ai/docs/guides/features/decisions)
and requires `OPENROUTER_API_KEY`. Repeat `--model` to choose different model
IDs. Only the checked-in synthetic message text goes to the provider. The
runner never invokes a Discord handler or executes a tool.

The JSON report includes each route and probability distribution, corrections
and regressions relative to the rules, workflow requests diverted to a read
handler, deferrals, request errors, p50/p95 request latency, and cost where the
provider supplies it. A low probability, explicit abstention, or provider
failure replays the current rule decision. Errors still make a live run exit
nonzero. The default threshold is an experiment setting. Before adopting a
candidate, compare it on a held-out set of sanitized Discord turns, confirm it
improves coverage with no workflow diversions, and measure the latency it adds
to end-to-end replies.

Jev and Clef can propose a route from these three labels. The backend owns
schema validation, permission checks, confirmation and execution, and the
structured planner drafts action arguments when needed. Cloudflare's Clairic
uses the [Extractions API](https://openrouter.ai/cloudflare/clairic) and would
need a separate field-level eval for a bounded workflow, including missing
values and spans grounded in the request.

See the [November 19, 2026 live comparison](../tests/evals/discord-agent/artifacts/mention-routing-2026-11-19/README.md)
for the first run, including per-case probabilities and the tested revision.

To compare a chat classifier with a decision model on the same corpus, repeat
`--chat-model` for the chat baselines and use `--model` for decision models.

```bash
uv run --locked python scripts/agent_routing_eval.py --live \
  --model typesafe/jev-1.14 \
  --chat-model openai/gpt-4.1-mini \
  --chat-model moonshotai/kimi-k2.6
```

Chat baselines receive the same routing criteria and return one label in a
strict JSON object. Their explicit abstentions and request or validation errors
use the rules. The probability threshold applies to Decisions responses only.
The benchmark measures mention routing through OpenRouter. The live planner
suite above measures workflow drafting and can use the configured provider
directly.

See the [chat baseline comparison](../tests/evals/discord-agent/artifacts/mention-chat-baselines-2026-11-19/README.md)
for the repeat Jev result and the Kimi K2.6 and direct GPT-4.1 mini metrics.

To benchmark GPT-4.1 mini against the direct OpenAI endpoint, run it
separately with `OPENAI_API_KEY` configured:

```bash
uv run --locked python scripts/agent_routing_eval.py --live \
  --openai-chat-model gpt-4.1-mini
```

Direct OpenAI returns token usage. The comparison estimates its cost from
the published rate card and records OpenRouter's reported cost for the other
models.
