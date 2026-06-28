# Development Guide

This guide covers local setup and day-to-day workflows for the 508.dev
integrations monorepo.

## Prerequisites

- Python 3.12+
- `python3` on PATH
- [uv](https://docs.astral.sh/uv/)
- Docker with Compose support
- Bun for the admin dashboard frontend

Only `python3` is assumed to exist locally; use `uv run`, package scripts, or
repo-provided entrypoints for project commands.

## First-Time Setup

```bash
uv sync
cp .env.example .env
```

Edit `.env` for the integrations you plan to exercise. At minimum, set
`API_SHARED_SECRET` when you need protected API routes or local dashboard login
links.

## Local Dev Stack

`./scripts/dev.sh` is the primary local-dev entrypoint. It assigns deterministic
per-worktree localhost ports, exports service URLs, starts Docker-managed infra,
and runs migrations before DB-using app services.

Recommended dashboard/worker loop without the Discord bot:

```bash
./scripts/dev.sh no-bot
```

Aliases:

```bash
./scripts/dev.sh dashboard
./scripts/dev.sh web-worker
```

Run everything, including the Discord bot:

```bash
./scripts/dev.sh all
```

Run individual pieces:

```bash
./scripts/dev.sh infra
./scripts/dev.sh web      # ./scripts/dev.sh api also works
./scripts/dev.sh worker
./scripts/dev.sh discord-bot
```

Run migrations without starting app services:

```bash
./scripts/dev.sh migrate
```

Show or stop the local environment:

```bash
./scripts/dev.sh ports
./scripts/dev.sh env
./scripts/dev.sh down
```

`./scripts/dev.sh env` emits shell-safe exports for the current worktree and
avoids printing the resolved Postgres password or API shared secret directly.

## Dashboard Login In Development

When the API is running, create a local/dev one-time dashboard login link without
starting the Discord bot:

```bash
./scripts/dev.sh login
```

The command calls the same `/auth/discord/links` endpoint used by the Discord
`/dashboard-login` command, but supplies trusted local/dev role context from the
CLI. It requires `API_SHARED_SECRET` in `.env` or the shell.

Optional overrides:

```bash
DEV_DASHBOARD_DISCORD_USER_ID=dev-user \
DEV_DASHBOARD_DISPLAY_NAME="Local Admin" \
DEV_DASHBOARD_ROLES=Admin \
./scripts/dev.sh login /dashboard/projects
```

The local/dev fallback only works when `ENVIRONMENT` is `local`, `dev`,
`development`, or `test`. Production still requires normal dashboard identity
validation.

## Host-Run Entrypoints

You can run service entrypoints directly when you already have infra running:

```bash
uv run --package discord_bot discord-bot
uv run --package api backend-api
uv run --package worker worker-consumer
```

Prefer the `dev.sh` wrappers for normal work because they derive the right
worktree-local URLs and ports automatically.

## Admin Dashboard Frontend

The dashboard source lives in `apps/admin_dashboard` and builds into
`apps/api/src/five08/backend/static/dashboard`, which the FastAPI app serves.

```bash
cd apps/admin_dashboard
bun install --frozen-lockfile
bun run format
bun run lint
bun run typecheck
bun run test
bun run build
```

Run `bun run build` after dashboard source changes when the checked-in FastAPI
static bundle should be updated.

## Docker Compose Workflow

For day-to-day development, prefer host-run app services through `dev.sh`.
Use Compose when you need container parity with deployment:

```bash
./scripts/docker-compose.sh up --build
./scripts/docker-compose.sh down
./scripts/docker-compose.sh print-ports
```

`compose.yaml` is the canonical Coolify/base stack. `compose.local.yaml` adds
local host port publishing for the helper wrapper. `docker-compose.yml` is a
compatibility wrapper for tools that still look for that filename.

The `web` service publishes container port `8090` to
`${WEB_HOST_BIND:-127.0.0.1}:${WEB_HOST_PORT:-8090}` so a host-side Cloudflare
Tunnel can target the dashboard/API at localhost. Redis, Postgres, and MinIO are
not published by the base file.

The app services attach to the external infra network named by
`INFRA_DOCKER_NETWORK`. Pre-create it if needed:

```bash
docker network create 508-infra
```

BuildKit-capable Docker / `docker compose build` support is required because the
service Dockerfiles use BuildKit cache mounts.

## Testing And Quality

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/format.sh
./scripts/typecheck.sh # Python Pyrefly + dashboard TypeScript
./scripts/pyrefly.sh   # Python-only typecheck
./scripts/check-all.sh
```

These top-level scripts include the admin dashboard where applicable. For
dashboard-only checks:

```bash
cd apps/admin_dashboard
bun run check
```

## Useful CLIs

Jobs CLI:

```bash
uv run --package worker jobsctl --help
uv run --package worker jobsctl recent
```

EspoCRM cleanup and bulk edits:

```bash
uv run --package five08 crmctl repl
uv run --package five08 crmctl search --where timezone__is_null=true --where location__is_not_null=true
uv run --package five08 crmctl batch-update --where timezone__is_null=true --where location__is_not_null=true --update timezone=@location
```

Inside `crmctl repl`, contacts are mutable Python objects:

```python
contacts = search(timezone__is_null=True, location__is_not_null=True)
contact = contacts[0]
contact.timezone = contact.infer_timezone()
contact.save()
```

## Feature Patterns

Bot features live as Discord.py cogs in
`apps/discord_bot/src/five08/discord_bot/cogs/`:

```python
from discord.ext import commands

class MyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyCog(bot))
```

Worker jobs live in `apps/worker/src/five08/worker/jobs.py`. API-triggered jobs
should persist state first, enqueue second, and keep long processing inside the
worker.

## More Reference

- [Architecture](./ARCHITECTURE.md)
- [Configuration Reference](./docs/configuration.md)
- [API Service](./apps/api/README.md)
- [Worker Service](./apps/worker/README.md)
- [Discord Bot](./apps/discord_bot/README.md)
