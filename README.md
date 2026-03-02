# 508.dev Integrations Monorepo

Monorepo for the 508.dev Discord bot and job processing stack.

## Architecture

This repository follows a service-oriented monorepo layout:

```text
.
├── apps/
│   ├── discord_bot/        # Discord gateway process
│   │   └── src/five08/discord_bot/
│   └── worker/             # Backend API + async queue worker
│       └── src/five08/{backend,worker}/
├── packages/
│   └── shared/
│       └── src/five08/      # Shared settings, queue helpers, shared clients
├── docker-compose.yml      # bot + backend-api + worker-consumer + redis + postgres + minio
├── tests/                  # Unit and integration tests
└── pyproject.toml          # uv workspace root
```

## Services

- `bot`: Discord gateway process.
- `backend-api`: FastAPI dashboard + ingest service that validates and enqueues jobs.
- `worker-consumer`: Dramatiq worker that executes jobs from Redis queue.
- `redis`: queue transport between API and worker.
- `postgres`: job state persistence, retries, idempotency.
- `minio`: internal S3-compatible storage transport.

Migrations:

- `apps/worker/src/five08/worker/migrations` (Alembic)
- `backend-api` runs `run_job_migrations()` during startup to keep DB schema current.

### Job model

- Jobs are persisted in Postgres table `jobs`.
- Job states: `queued`, `running`, `succeeded`, `failed`, `dead`, `canceled`.
- Idempotency key is unique and optional.
- Attempts are stored with `run_after`/retry state so delivery failures are never lost.
- Human audit events are persisted in `audit_events`.
- CRM identity cache is persisted in `people`.

### Backend API Endpoints

See the worker service docs: [`apps/worker/README.md#backend-api-endpoints`](apps/worker/README.md#backend-api-endpoints).
CLI request examples are documented at [`apps/worker/README.md#cli-usage`](apps/worker/README.md#cli-usage).

## Local Development

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# then edit .env
```

### 3. Run services

Run directly with uv:

```bash
# Discord bot
uv run --package discord-bot-app discord-bot

# Worker ingest API
uv run --package integrations-worker backend-api

# Worker queue consumer
uv run --package integrations-worker worker-consumer

# Jobs CLI
uv run --package integrations-worker jobsctl --help
```

Or run the full stack with Docker Compose:

```bash
docker compose up --build
```

## Environment Variables

Use `.env.example` as the source of truth for defaults.

### Core Runtime (Bot + Worker)

- `Required`: `ESPO_BASE_URL`, `ESPO_API_KEY`
- `Required` for protected endpoints: `API_SHARED_SECRET` (ingest requests are rejected when unset)
- `Required` in non-local environments: `MINIO_ROOT_PASSWORD`

See [`ENVIRONMENT.md`](ENVIRONMENT.md) for optional and non-critical environment variables.

## Commands

```bash
uv run --package discord-bot-app discord-bot
uv run --package integrations-worker backend-api
uv run --package integrations-worker worker-consumer
docker compose up --build
```

For Discord bot docs, see [`Discord Bot`](DISCORD_BOT.md).

For local development helper commands, see [`development.md`](development.md).

## Deployment

Deploy as a single Compose application.

MinIO is used as the internal transfer mechanism so file handoffs stay inside the stack.
External object storage adapters can be added later for multi-cloud or vendor-specific routing.

This keeps one stack and one shared env set while still allowing independent service scaling/restarts (`bot`, `backend-api`, `worker-consumer`).
