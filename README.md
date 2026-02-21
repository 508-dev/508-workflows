# 508.dev Integrations Monorepo

Monorepo for the 508.dev Discord bot and job processing stack.

## Architecture

This repository follows a service-oriented monorepo layout:

```text
.
├── apps/
│   ├── discord_bot/        # Discord gateway process
│   │   └── src/five08/discord_bot/
│   └── worker/             # Webhook ingest API + async queue worker
│       └── src/five08/worker/
├── packages/
│   └── shared/
│       └── src/five08/      # Shared settings, queue helpers, shared clients
├── docker-compose.yml      # bot + worker-api + worker-consumer + redis + postgres + minio
├── tests/                  # Unit and integration tests
└── pyproject.toml          # uv workspace root
```

## Services

- `bot`: Discord gateway process.
- `worker-api`: lightweight HTTP ingest service that validates and enqueues jobs.
- `worker-consumer`: Dramatiq worker that executes jobs from Redis queue.
- `redis`: queue transport between API and worker.
- `postgres`: job state persistence, retries, idempotency.
- `minio`: internal S3-compatible storage transport.

Migrations:

- `apps/worker/src/five08/worker/migrations` (Alembic)
- `worker-api` runs `run_job_migrations()` during startup to keep DB schema current.

### Job model

- Jobs are persisted in Postgres table `jobs`.
- Job states: `queued`, `running`, `succeeded`, `failed`, `dead`, `canceled`.
- Idempotency key is unique and optional.
- Attempts are stored with `run_after`/retry state so delivery failures are never lost.

### Worker API Endpoints

- `GET /health`: Redis/Postgres/worker health check.
- `POST /webhooks/{source}`: Generic webhook enqueue endpoint.
- `POST /webhooks/espocrm`: EspoCRM webhook endpoint (expects array payload).
- `POST /process-contact/{contact_id}`: Manually enqueue one contact skills job.

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
uv run --package integrations-worker worker-api

# Worker queue consumer
uv run --package integrations-worker worker-consumer
```

Or run the full stack with Docker Compose:

```bash
docker compose up --build
```

## Environment Variables

### Shared (bot + worker)

- `REDIS_URL` (default: `redis://redis:6379/0`)
- `REDIS_QUEUE_NAME` (default: `jobs.default`)
- `REDIS_KEY_PREFIX` (default: `jobs`)
- `ESPO_BASE_URL` (required by both bot and worker)
- `ESPO_API_KEY` (required by both bot and worker)
- `JOB_TIMEOUT_SECONDS` (default: `600`)
- `JOB_RESULT_TTL_SECONDS` (default: `3600`)
- `WEBHOOK_SHARED_SECRET` (required; requests are rejected when unset)
- `POSTGRES_URL` (default: `postgresql://postgres:postgres@postgres:5432/workflows`)
- `POSTGRES_DB` (default: `jobs`)
- `POSTGRES_USER` (default: `jobs`)
- `POSTGRES_PASSWORD` (default: `jobs`)
- `JOB_MAX_ATTEMPTS` (default: `8`)
- `JOB_RETRY_BASE_SECONDS` (default: `5`)
- `JOB_RETRY_MAX_SECONDS` (default: `300`)
- `WEBHOOK_INGEST_HOST` (default: `0.0.0.0`)
- `WEBHOOK_INGEST_PORT` (default: `8090`)
- `LOG_LEVEL` (default: `INFO`)
- `MINIO_ENDPOINT` (default: `http://minio:9000`)
- `MINIO_INTERNAL_BUCKET` (default: `internal-transfers`)
- `MINIO_ROOT_USER` (default: `internal`)
- `MINIO_ROOT_PASSWORD`
- `MINIO_HOST_BIND` (default: `127.0.0.1`; set to `0.0.0.0` to expose MinIO)
- `MINIO_API_PORT` (default: `9000`)
- `MINIO_CONSOLE_PORT` (default: `9001`)
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` (compatibility aliases; use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` by default for internal transfers)

### Discord Bot

- `DISCORD_BOT_TOKEN`
- `CHANNEL_ID`
- `EMAIL_USERNAME`
- `EMAIL_PASSWORD`
- `IMAP_SERVER`
- `SMTP_SERVER`
- `KIMAI_BASE_URL`
- `KIMAI_API_TOKEN`
- Optional: `CHECK_EMAIL_WAIT`, `DISCORD_SENDMSG_CHARACTER_LIMIT`, `HEALTHCHECK_PORT`

### Worker Consumer

- `WORKER_NAME` (default: `integrations-worker`)
- `WORKER_QUEUE_NAMES` (default: `jobs.default`, comma-separated)
- `WORKER_BURST` (default: `false`)
- `MAX_ATTACHMENTS_PER_CONTACT` (default: `3`)
- `MAX_FILE_SIZE_MB` (default: `10`)
- `ALLOWED_FILE_TYPES` (default: `pdf,doc,docx,txt`)
- `RESUME_KEYWORDS` (default: `resume,cv,curriculum`)
- `OPENAI_API_KEY` (optional; if unset, heuristic extraction is used)
- `OPENAI_BASE_URL` (optional)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

## Commands

```bash
# tests
./scripts/test.sh

# lint
./scripts/lint.sh

# format
./scripts/format.sh

# type check
./scripts/mypy.sh
```

## Deployment

Deploy as a single Compose application.

MinIO is used as the internal transfer mechanism so file handoffs stay inside the stack.
External object storage adapters can be added later for multi-cloud or vendor-specific routing.

This keeps one stack and one shared env set while still allowing independent service scaling/restarts (`bot`, `worker-api`, `worker-consumer`).
