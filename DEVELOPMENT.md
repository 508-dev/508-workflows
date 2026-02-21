# Development Guide

This guide covers setup and workflows for the 508.dev monorepo (`discord bot + worker + shared package`).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker (optional, for Compose-based local runs)

## Monorepo Layout

```text
apps/discord_bot/src/five08/discord_bot/  # Discord bot package
apps/worker/src/five08/worker/            # Worker package
packages/shared/src/five08/     # Shared package
```

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Configure environment:

```bash
cp .env.example .env
```

The worker API process runs Alembic migrations on startup (`apps/worker/src/five08/worker/db_migrations.py`) so the `jobs` table is created or upgraded before requests are accepted.

3. Run services:

```bash
# bot
uv run --package discord-bot-app discord-bot

# webhook ingest API
uv run --package integrations-worker worker-api

# job consumer
uv run --package integrations-worker worker-consumer
```

## Docker Compose Workflow

Start full stack (bot + worker-api + worker-consumer + redis + postgres + minio):

```bash
docker compose up --build
```

Stop stack:

```bash
docker compose down
```

## Testing and Quality

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/format.sh
./scripts/mypy.sh
```

## Adding Bot Features

Bot features remain Discord.py cogs in:

- `apps/discord_bot/src/five08/discord_bot/cogs/`

Pattern:

```python
from discord.ext import commands

class MyCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyCog(bot))
```

## Adding Worker Jobs

1. Add job function in `apps/worker/src/five08/worker/jobs.py`.
2. Enqueue from `apps/worker/src/five08/worker/api.py` (or from bot code if needed).
3. Ensure job type/queue settings and Postgres settings are configured in `.env`.

### Job architecture

- API layer persists jobs first in Postgres with idempotency keys.
- Queue layer uses Dramatiq actors over Redis for delivery.
- MinIO is the current internal transfer mechanism (bucket: `internal-transfers`) and is intended only for stack-internal file movement; external S3 integrations are separate.

## Worker CRM Flow

- EspoCRM webhooks are accepted at `POST /webhooks/espocrm`.
- Each event enqueues `five08.worker.jobs.process_contact_skills_job`.
- Jobs use modules under `apps/worker/src/five08/worker/crm/` to:
  - fetch contact + attachments from EspoCRM
  - extract text from resume-like files
  - extract skills (LLM when configured, heuristic fallback otherwise)
  - update contact skills field in EspoCRM
- Manual queueing is available via `POST /process-contact/{contact_id}`.

## Environment Variables

Use `.env.example` as source of truth. Key categories:

- Shared queue/runtime: `REDIS_URL`, `REDIS_QUEUE_NAME`, `POSTGRES_URL`, `JOB_MAX_ATTEMPTS`, `JOB_RETRY_BASE_SECONDS`, `JOB_RETRY_MAX_SECONDS`, `LOG_LEVEL`, webhook settings
- Bot credentials/integrations: Discord, email, Espo, Kimai
- Worker controls: `WORKER_NAME`, `WORKER_QUEUE_NAMES`, `WORKER_BURST`
- Worker CRM processing: `MAX_ATTACHMENTS_PER_CONTACT`, `MAX_FILE_SIZE_MB`, `ALLOWED_FILE_TYPES`, `RESUME_KEYWORDS`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`

## CI Notes

GitHub Actions runs tests, lint, mypy, and security checks against:

- `apps/discord_bot/src/five08/discord_bot/`
- `apps/worker/src/five08/worker/`
- `packages/shared/src/five08/`
- `tests/`
