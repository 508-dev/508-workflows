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

- `GET /health`: Redis/Postgres/worker health check.
- `GET /jobs/{job_id}`: Fetch queued job status/result payload.
- `POST /jobs/resume-extract`: Enqueue resume profile extraction.
- `POST /jobs/resume-apply`: Enqueue confirmed CRM field apply.
- `POST /webhooks/{source}`: Generic webhook enqueue endpoint.
- `POST /webhooks/espocrm`: EspoCRM webhook endpoint (expects array payload).
- `POST /webhooks/espocrm/people-sync`: EspoCRM contact-change webhook for people cache sync.
- `POST /process-contact/{contact_id}`: Manually enqueue one contact skills job.
- `POST /sync/people`: Manually enqueue a full CRM->people cache sync.
- `POST /audit/events`: Persist one human audit event (`discord` or `admin_dashboard`).
- `GET /auth/login`: Start OIDC Auth Code + PKCE login flow.
- `GET /auth/callback`: Complete OIDC callback and set HttpOnly session cookie.
- `GET /auth/me`: Return active session identity.
- `POST /auth/logout`: Clear active session cookie + server session.
- `POST /auth/discord/links`: Create one-time dashboard deep link from Discord command context.
- `GET /auth/discord/link/{token}`: Resolve Discord deep link into authenticated dashboard redirect.
- Auth flows emit best-effort human audit events (`auth.login`, `auth.logout`) under source `admin_dashboard`.

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
```

Or run the full stack with Docker Compose:

```bash
docker compose up --build
```

## Environment Variables

Use `.env.example` as the source of truth for defaults.

### Core Runtime (Bot + Worker)

- `Required`: `ESPO_BASE_URL`, `ESPO_API_KEY`
- `Optional`: `LOG_LEVEL` (default: `INFO`)
- `Optional`: `RUNTIME_ENV` (default: `local`; non-local values require explicit `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`)

### Queue + Job Runtime

- `Optional`: `REDIS_URL` (default: `redis://redis:6379/0`)
- `Optional`: `REDIS_QUEUE_NAME` (default: `jobs.default`)
- `Optional`: `REDIS_KEY_PREFIX` (default: `jobs`)
- `Optional`: `JOB_TIMEOUT_SECONDS` (default: `600`)
- `Optional`: `JOB_RESULT_TTL_SECONDS` (default: `3600`)
- `Optional`: `JOB_MAX_ATTEMPTS` (default: `8`)
- `Optional`: `JOB_RETRY_BASE_SECONDS` (default: `5`)
- `Optional`: `JOB_RETRY_MAX_SECONDS` (default: `300`)

### Postgres + Compose Exposure

- `Optional`: `POSTGRES_URL` (default: `postgresql://postgres@postgres:5432/workflows`)
- `Optional` (Compose DB container): `POSTGRES_DB` (default: `workflows`)
- `Optional` (Compose DB container): `POSTGRES_USER` (default: `postgres`)
- `Optional` (Compose DB container): `POSTGRES_PASSWORD` (default: `postgres`)
- `Optional` (Compose host bind): `POSTGRES_HOST_BIND` (default: `127.0.0.1`)
- `Optional` (Compose host port): `POSTGRES_PORT` (default: `5432`)

### MinIO + Internal Transfers

- `Required` in non-local environments: `MINIO_ROOT_PASSWORD`
- `Optional`: `MINIO_ENDPOINT` (default: `http://minio:9000`)
- `Optional`: `MINIO_INTERNAL_BUCKET` (default: `internal-transfers`)
- `Optional`: `MINIO_ROOT_USER` (default: `internal`)
- `Optional`: `MINIO_HOST_BIND` (default: `127.0.0.1`; set `0.0.0.0` to expose externally)
- `Optional`: `MINIO_API_PORT` (default: `9000`)
- `Optional`: `MINIO_CONSOLE_PORT` (default: `9001`)
- Note: `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` are `SharedSettings` alias properties (`minio_access_key`, `minio_secret_key`) and are not env-loaded fields.
- Note: use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` as the actual env vars.

### Backend API Ingest

- `Required` for protected endpoints: `API_SHARED_SECRET` (ingest requests are rejected when unset)
- `Optional`: `WEBHOOK_INGEST_HOST` (default: `0.0.0.0`)
- `Optional`: `WEBHOOK_INGEST_PORT` (default: `8090`)

### Backend API OIDC Session Auth

- `Optional` (required when enabling OIDC login): `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`
- `Optional`: `OIDC_SCOPE` (default: `openid profile email groups`)
- `Optional`: `OIDC_GROUPS_CLAIM` (default: `groups`)
- `Optional`: `OIDC_ADMIN_GROUPS` (default: `Admin,Owner,Steering Committee`)
- `Optional`: `OIDC_CALLBACK_PATH` (default: `/auth/callback`)
- `Optional`: `OIDC_REDIRECT_BASE_URL` (default: infer from request base URL)
- `Optional`: `OIDC_HTTP_TIMEOUT_SECONDS` (default: `8.0`)
- `Optional`: `OIDC_JWKS_CACHE_SECONDS` (default: `300`)
- `Optional`: `AUTH_STATE_TTL_SECONDS` (default: `600`)
- `Optional`: `AUTH_SESSION_TTL_SECONDS` (default: `28800`)
- `Optional`: `AUTH_SESSION_COOKIE_NAME` (default: `five08_session`)
- `Optional`: `AUTH_COOKIE_SECURE` (default: `false`)
- `Optional`: `AUTH_COOKIE_SAMESITE` (default: `lax`)
- `Optional`: `DASHBOARD_DEFAULT_PATH` (default: `/dashboard`)
- `Optional`: `DASHBOARD_PUBLIC_BASE_URL` (base URL for generated deep links)

### Discord Admin Deep-Link Validation

- `Optional`: `DISCORD_ADMIN_GUILD_ID` (required for Discord API fallback role checks)
- `Optional`: `DISCORD_ADMIN_ROLES` (default: `Admin,Owner,Steering Committee`)
- `Optional`: `DISCORD_API_TIMEOUT_SECONDS` (default: `8.0`)
- `Optional`: `DISCORD_LINK_TTL_SECONDS` (default: `600`)
- `Optional`: `DISCORD_BOT_TOKEN` (needed only for fallback Discord API checks; DB role check remains primary)

### Worker Consumer

- `Optional`: `WORKER_NAME` (default: `integrations-worker`)
- `Optional`: `WORKER_QUEUE_NAMES` (default: `jobs.default`, comma-separated)
- `Optional`: `WORKER_BURST` (default: `false`)

### Worker CRM Sync + Skills Extraction

- `Optional`: `CRM_SYNC_ENABLED` (default: `true`)
- `Optional`: `CRM_SYNC_INTERVAL_SECONDS` (default: `900`)
- `Optional`: `CRM_SYNC_PAGE_SIZE` (default: `200`)
- `Optional`: `CHECK_EMAIL_WAIT` (default: `2`; minutes between mailbox polls)
- `Optional`: `CRM_LINKEDIN_FIELD` (default: `cLinkedInUrl`)
- `Optional`: `MAX_ATTACHMENTS_PER_CONTACT` (default: `3`)
- `Optional`: `MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `ALLOWED_FILE_TYPES` (default: `pdf,doc,docx,txt`)
- `Optional`: `RESUME_KEYWORDS` (default: `resume,cv,curriculum`)
- `Optional`: `OPENAI_API_KEY` (if unset, heuristic extraction is used)
- `Optional`: `OPENAI_BASE_URL` (set `https://openrouter.ai/api/v1` for OpenRouter)
- `Optional`: `RESUME_AI_MODEL` (default: `gpt-4o-mini`; use plain names like `gpt-4o-mini`, OpenRouter gets auto-prefixed to `openai/<model>`)
- `Optional`: `OPENAI_MODEL` (default: `gpt-4o-mini`; fallback/legacy model setting)
- `Optional`: `RESUME_EXTRACTOR_VERSION` (default: `v1`; used in resume processing idempotency/ledger keys)
- `Optional`: `EMAIL_RESUME_INTAKE_ENABLED` (default: `false`; enables worker-side mailbox resume processing loop)
- `Optional`: `EMAIL_RESUME_ALLOWED_EXTENSIONS` (default: `pdf,doc,docx`)
- `Optional`: `EMAIL_RESUME_MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `EMAIL_REQUIRE_SENDER_AUTH_HEADERS` (default: `true`; requires SPF/DKIM/DMARC pass headers)
- `Required when EMAIL_RESUME_INTAKE_ENABLED=true`: `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `IMAP_SERVER`

### Discord Bot Core

- `Required`: `DISCORD_BOT_TOKEN`
- `Required`: `CHANNEL_ID`
- `Optional`: `BACKEND_API_BASE_URL` (default: `http://backend-api:8090`)
- `Optional`: `HEALTHCHECK_PORT` (default: `3000`)
- `Optional`: `DISCORD_SENDMSG_CHARACTER_LIMIT` (default: `2000`)

### Discord CRM Audit Logging (Best Effort)

- `Optional`: `AUDIT_API_BASE_URL` (when set with `API_SHARED_SECRET`, CRM commands emit best-effort audit events)
- `Optional`: `AUDIT_API_TIMEOUT_SECONDS` (default: `2.0`)

### Kimai (Legacy/Deprecating)

- `Currently required by config model`: `KIMAI_BASE_URL`, `KIMAI_API_TOKEN`

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

This keeps one stack and one shared env set while still allowing independent service scaling/restarts (`bot`, `backend-api`, `worker-consumer`).
