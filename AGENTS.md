# AI Agent Development Guide

Guidance for agents working in the 508.dev integrations monorepo.

## Project Context

This repo contains multiple services:

- Discord bot (`apps/discord_bot`)
- Worker ingest + consumer (`apps/worker`)
- Shared package (`packages/shared`)

## Architecture Principles

1. Feature modularity in bot cogs
- Bot features live in `apps/discord_bot/src/five08/discord_bot/cogs/*.py`.
- Cogs auto-load from the `five08.discord_bot.cogs` package.

2. Shared runtime code
- Cross-service settings/queue/job logic lives in `packages/shared/src/five08/`.
- Keep service-specific behavior in each app package.

3. Service separation
- `apps/discord_bot`: Discord gateway and bot commands/cogs
- `apps/worker`: webhook ingest API and queue consumer
- `docker-compose.yml`: stack orchestration with Redis, Postgres, and MinIO

## Common Paths

- Bot core: `apps/discord_bot/src/five08/discord_bot/bot.py`
- Bot config: `apps/discord_bot/src/five08/discord_bot/config.py`
- Worker API: `apps/worker/src/five08/worker/api.py`
- Worker consumer: `apps/worker/src/five08/worker/consumer.py`
- Shared settings: `packages/shared/src/five08/settings.py`
- Shared queue helpers: `packages/shared/src/five08/queue.py`

## Development Commands

```bash
uv sync
./scripts/test.sh
./scripts/lint.sh
./scripts/format.sh
./scripts/mypy.sh
```

Run services directly:

```bash
uv run --package discord-bot-app discord-bot
uv run --package integrations-worker worker-api
uv run --package integrations-worker worker-consumer
```

Run stack with Compose:

```bash
docker compose up --build
```

## Bot Feature Pattern

```python
from discord.ext import commands

class MyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyCog(bot))
```

## Worker Pattern

- Keep ingest endpoints fast: validate input, persist jobs, enqueue, return 202.
- Run long processing in worker consumer jobs (Dramatiq actors), with Postgres as source-of-truth job state and Redis as delivery transport.
- Internal file movement is routed through MinIO (`internal-transfers`) inside the stack; this is explicitly the internal transfer path, with external object store adapters kept separate for future needs.
- Worker schema is managed with Alembic migrations in `apps/worker/src/five08/worker/migrations` and applied at worker-api startup.

## Data Model Note

- Shared job state is persisted in Postgres table `jobs` with job type, status (`queued`, `running`, `succeeded`, `failed`, `dead`, `canceled`), payload, idempotency key, attempt counters, scheduling, and lock metadata.

## Configuration Rules

- Add shared env/config in `packages/shared/src/five08/settings.py`.
- Add service-specific settings in local service `config.py` by subclassing shared settings.
- Keep secrets in env vars, not code.

## Agent Guidelines

- Prefer adding new bot features as isolated cogs.
- Prefer adding reusable helpers/clients to `packages/shared/src/five08/`.
- Update docs and `.env.example` when introducing new config.
- Keep changes incremental and testable.
