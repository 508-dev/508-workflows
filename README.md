# 508.dev Integrations

Monorepo for the 508.dev operations automation stack: Discord workflows, the
FastAPI operations dashboard, background jobs, CRM sync, and shared integration
clients.

## What Is In This Repo

| Path | Purpose |
| --- | --- |
| `apps/discord_bot` | Discord gateway process and slash-command cogs. |
| `apps/api` | FastAPI backend, protected ingest endpoints, auth, and `/dashboard`. |
| `apps/admin_dashboard` | React + Vite admin dashboard source. |
| `apps/worker` | Queue consumer, job handlers, CRM processors, and Alembic migrations. |
| `packages/shared` | Shared settings, queue helpers, integration clients, CRM utilities, and agent code. |
| `scripts` | Local development, test, formatting, and Compose helpers. |
| `docs` | Feature and operational documentation. |

## Quickstart

Install dependencies:

```bash
uv sync
```

Create local configuration:

```bash
cp .env.example .env
```

Start the dashboard/API, worker, and local infrastructure without the Discord
bot:

```bash
./scripts/dev.sh no-bot
```

In another terminal, create a local dashboard login link:

```bash
./scripts/dev.sh login
```

Open the printed link, then use the dashboard at `/dashboard`.

To run the Discord bot too:

```bash
./scripts/dev.sh all
```

## Common Commands

```bash
./scripts/dev.sh infra       # Redis, Postgres, MinIO only
./scripts/dev.sh web         # FastAPI dashboard/API with reload
./scripts/dev.sh worker      # worker consumer with reload
./scripts/dev.sh discord-bot # Discord bot process
./scripts/dev.sh no-bot      # infra + dashboard/API + worker
./scripts/dev.sh login       # local/dev dashboard login link
./scripts/dev.sh ports       # deterministic worktree ports
./scripts/dev.sh down        # stop local Docker infra

./scripts/test.sh
./scripts/lint.sh
./scripts/format.sh
./scripts/typecheck.sh # Python Pyrefly + dashboard TypeScript
./scripts/pyrefly.sh   # Python-only typecheck
./scripts/check-all.sh
```

For workspace archival, stop host-run dev processes and Docker Compose together:

```bash
./scripts/archive-workspace.sh --dry-run
./scripts/archive-workspace.sh
```

`./scripts/dev.sh env` emits shell-safe exports for the current worktree and
avoids printing the resolved Postgres password directly.

## Documentation

- [Development Guide](./DEVELOPMENT.md): local setup, service commands, dashboard login, Compose workflow, quality checks, and CLIs.
- [Architecture](./ARCHITECTURE.md): services, job model, auth model, deployment shape, and extension points.
- [Configuration Reference](./docs/configuration.md): environment variable groups and local/runtime configuration notes.
- [API Service](./apps/api/README.md): backend endpoints and dashboard auth routes.
- [Worker Service](./apps/worker/README.md): job CLI, worker endpoints, and queue usage examples.
- [Discord Bot](./apps/discord_bot/README.md): bot commands and Discord-specific workflows.
- [Discord Gig Dashboard](./docs/discord-gig-dashboard.md): gig tracking and dashboard behavior.
- [Discord GitHub Todos and Projects](./docs/discord-github-todos.md): GitHub App setup and Discord access model.
- [Discord Agent Eval Harness](./docs/discord-agent-eval-harness.md): Discord agent eval workflow.

## Deployment

Production runs as a Compose application with independently restartable
`discord_bot`, `web`, and `worker` services plus Redis, Postgres, and MinIO.
See [Architecture](./ARCHITECTURE.md#deployment-shape) and
[Configuration Reference](./docs/configuration.md) before changing runtime
configuration.

## License

This repository is licensed under the GNU Affero General Public License v3.0.
See [LICENSE](./LICENSE).
