# 508.dev Integrations Monorepo

Monorepo for the 508.dev Discord bot and job processing stack.

## Architecture

This repository follows a service-oriented monorepo layout:

```text
.
├── apps/
│   ├── discord_bot/        # Discord gateway process
│   │   └── src/five08/discord_bot/
│   ├── api/                # Backend API + dashboard code
│   │   └── src/five08/backend/
│   └── worker/             # Async queue worker
│       └── src/five08/worker/
├── packages/
│   └── shared/
│       └── src/five08/      # Shared settings, queue helpers, shared clients
├── compose.yaml            # canonical Coolify/base container stack
├── compose.local.yaml      # local infra host port publishing override
├── docker-compose.yml      # compatibility wrapper including compose.yaml
├── tests/                  # Unit and integration tests
└── pyproject.toml          # uv workspace root
```

## Services

- `discord_bot`: Discord gateway process.
- `web`: FastAPI dashboard + ingest service that validates and enqueues jobs.
- `worker`: Dramatiq worker that executes jobs from Redis queue.
- `redis`: queue transport between API and worker.
- `postgres`: job state persistence, retries, idempotency.
- `minio`: internal S3-compatible storage transport.

Migrations:

- `apps/worker/src/five08/worker/migrations` (Alembic)
- `web` runs `run_job_migrations()` during startup to keep DB schema current.

### Job model

- Jobs are persisted in Postgres table `jobs`.
- Job states: `queued`, `running`, `succeeded`, `failed`, `dead`, `canceled`.
- Idempotency key is unique and optional.
- Attempts are stored with `run_after`/retry state so delivery failures are never lost.
- Human audit events are persisted in `audit_events`.
- CRM identity cache is persisted in `people`.

### Backend API Endpoints

See the API service docs: [`apps/api/README.md#backend-api-endpoints`](./apps/api/README.md#backend-api-endpoints).
CLI request examples are documented at [`apps/worker/README.md#cli-usage`](./apps/worker/README.md#cli-usage).
Discord gig tracking and dashboard behavior are documented at
[`docs/discord-gig-dashboard.md`](./docs/discord-gig-dashboard.md).

The operations dashboard is served at `/dashboard`. It is available only to
active dashboard sessions created through the existing OIDC or Discord dashboard
login link flows, and Discord-backed sessions carry the linked CRM contact id
from the local `people` cache. Steering Committee+ Discord-backed sessions can
use CRM people lookup and onboarding assignment. Admin+ sessions can also access
recent jobs, job details/reruns, people-cache sync, and recent human audit
events.

### Current API/queue caveats

- Non-dashboard protected API endpoints use a shared `API_SHARED_SECRET` in `X-API-Secret` today. This includes webhook and secret-backed admin routes until per-webhook/per-route auth is introduced.
- `/dashboard` and `/dashboard/api/*` are browser-facing dashboard routes that use the HttpOnly session cookie from OIDC or Discord dashboard login flows. They do not accept `X-API-Secret`.
- Worker startup uses a single effective queue name for actor registration; keep this explicit if you later add true multi-queue routing.
- Backend rerun/enqueue behavior relies on one shared job-handler set. Add any new worker callable consistently to both backend handler resolution and worker dispatch.

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

### 3. Start local infrastructure

For development, run Redis, Postgres, and MinIO in Docker and run the app
processes on the host:

```bash
./scripts/dev.sh infra
./scripts/dev.sh web
./scripts/dev.sh worker
./scripts/dev.sh discord-bot
```

`infra` brings up only the Docker infra. Use the host-service subcommands to run
the app processes with per-worktree ports and derived localhost URLs. The
DB-using host-service subcommands run Alembic migrations before starting the
service so the bot and worker do not race the API startup schema migration.

Run migrations without starting app services:

```bash
./scripts/dev.sh migrate
```

To launch infra plus all host-run services together with prefixed logs:

```bash
./scripts/dev.sh all
```

Show, export, or stop the local dev environment:

```bash
./scripts/dev.sh ports
./scripts/dev.sh env
./scripts/dev.sh down
```

For workspace archival, stop host-run dev processes and Docker Compose together:

```bash
./scripts/archive-workspace.sh --dry-run
./scripts/archive-workspace.sh
```

`./scripts/dev.sh env` emits shell-safe exports for the current worktree and
avoids printing the resolved Postgres password directly.

### 4. Run app services on the host

```bash
# Discord bot
uv run --package discord_bot discord-bot

# Web/API dashboard and ingest service
uv run --package api backend-api

# Worker queue consumer
uv run --package worker worker-consumer

# Jobs CLI
uv run --package worker jobsctl --help
# recent jobs (past hour by default):
uv run --package worker jobsctl recent

# EspoCRM REPL / search / batch updates
uv run --package five08 crmctl repl
uv run --package five08 crmctl search --where timezone__is_null=true --where location__is_not_null=true
uv run --package five08 crmctl batch-update --where timezone__is_null=true --where location__is_not_null=true --update timezone=@location
```

`./scripts/dev.sh` exports deterministic per-worktree localhost ports and
service URLs so the apps can run on the host without manual overrides. Use
the lower-level Compose wrapper when you want full containerized parity.

For local full-container runs, including deterministic localhost ports:

```bash
./scripts/docker-compose.sh up --build
```

Coolify should use `/compose.yaml` as the base Compose file. A small
`docker-compose.yml` compatibility wrapper includes it for tools still configured
to read the older filename. The `web` service publishes container port `8090`
to `${WEB_HOST_BIND:-127.0.0.1}:${WEB_HOST_PORT:-8090}`
so a host-side Cloudflare Tunnel can target the dashboard/API at localhost.
The base file does not publish Redis, Postgres, or MinIO host ports. The app
services also attach to the shared infra network named by `INFRA_DOCKER_NETWORK`
so they can reach
Portainer-managed Bifrost and Langfuse by Docker DNS. The network is declared
as external, so pre-create it before running Compose if it does not already
exist.

```bash
docker network create 508-infra
```

Set `INFRA_DOCKER_NETWORK` if the shared network has a different name.
With Portainer services attached to the same network using aliases like `bifrost`
and `langfuse`, configure:

```env
OPENAI_BASE_URL=http://bifrost:8080/openai
LANGFUSE_BASE_URL=http://langfuse:3000
```

Agent model routing allows this exact internal Docker DNS Bifrost URL for
same-host deployments. Public/provider base URLs remain restricted to HTTPS
allowlisted hosts.

Note: the service Dockerfiles use BuildKit cache mounts, so containerized builds
require BuildKit-capable Docker / `docker compose build` support.

Show the deterministic host ports for the current worktree:

```bash
./scripts/docker-compose.sh print-ports
```

Set `*_HOST_PORT` or `COMPOSE_PROJECT_NAME` in `.env` or the invoking shell if you
want to pin them; otherwise the wrapper computes deterministic per-worktree values.

## License

This repository is licensed under the GNU Affero General Public License v3.0.
See [LICENSE](./LICENSE).

## Environment Variables

Use `.env.example` as the source of truth for defaults.

When running inside Conductor, `CONDUCTOR_PORT` is treated as the first port in
the workspace's 10-port range for unset worktree defaults: Redis uses `+0`,
Postgres `+1`, Compose web `+2`, MinIO API `+3`, MinIO console `+4`, host-run
web/API `+5`, and bot health `+6`. Explicit service port overrides keep their
current precedence rules.

### Core Runtime (Bot + Worker)

- `Required`: `ESPO_BASE_URL`, `ESPO_API_KEY`
- `Optional`: `LOG_LEVEL` (default: `INFO`)
- `Optional`: `ENVIRONMENT` (default: `local`; non-local values require explicit `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`)

### Queue + Job Runtime

- `Optional`: `REDIS_URL` (default: `redis://127.0.0.1:6379/0`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects `redis://redis:6379/0`)
- `Optional`: `REDIS_QUEUE_NAME` (default: `jobs.default`)
- `Optional`: `REDIS_KEY_PREFIX` (default: `jobs`)
- `Optional`: `REDIS_HOST_BIND` (default: `127.0.0.1`)
- `Optional`: `REDIS_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 0` inside Conductor, otherwise deterministic per-worktree value `12000 + WORKTREE_ENV_SLOT`; set `REDIS_HOST_PORT=6379` in your shell or `.env` to pin it to `6379`)
- `Optional`: `JOB_TIMEOUT_SECONDS` (default: `600`)
- `Optional`: `JOB_RESULT_TTL_SECONDS` (default: `3600`)
- `Optional`: `JOB_MAX_ATTEMPTS` (default: `8`)
- `Optional`: `JOB_RETRY_BASE_SECONDS` (default: `5`)
- `Optional`: `JOB_RETRY_MAX_SECONDS` (default: `300`)
- `Optional`: `GIG_RECRUITING_STALE_DAYS` (default: `7`; dashboard warnings and Discord reminders for recruiting gigs with no updates)

### Postgres + Compose Exposure

- `Optional`: `POSTGRES_URL` (default: `postgresql://postgres:postgres@127.0.0.1:5432/workflows`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects a Docker-network URL)
- `Optional` (Compose DB container): `POSTGRES_DB` (default: `workflows`)
- `Optional` (Compose DB container): `POSTGRES_USER` (default: `postgres`)
- `Optional` (Compose DB container): `POSTGRES_PASSWORD` (default: `postgres`)
- `Optional` (Compose host bind): `POSTGRES_HOST_BIND` (default: `127.0.0.1`)
- `Optional` (Compose host port): `POSTGRES_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 1` inside Conductor, otherwise deterministic per-worktree value `15432 + WORKTREE_ENV_SLOT`; use `5432` only if you explicitly pin it, e.g. `POSTGRES_HOST_PORT=5432`; see `./scripts/docker-compose.sh print-ports`)

### MinIO + Internal Transfers

- `Required` in non-local environments: `MINIO_ROOT_PASSWORD`
- `Optional`: `MINIO_ENDPOINT` (default: `http://127.0.0.1:9000`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects `http://minio:9000`)
- `Optional`: `MINIO_INTERNAL_BUCKET` (default: `internal-transfers`)
- `Optional`: `MINIO_ROOT_USER` (default: `internal`)
- `Optional`: `MINIO_HOST_BIND` (default: `127.0.0.1`; set `0.0.0.0` to expose externally)
- `Optional`: `MINIO_API_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 3` inside Conductor, otherwise deterministic per-worktree value `24000 + WORKTREE_ENV_SLOT`; pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)
- `Optional`: `MINIO_CONSOLE_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 4` inside Conductor, otherwise deterministic per-worktree value `28000 + WORKTREE_ENV_SLOT`; pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)
- Note: `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` are `SharedSettings` alias properties (`minio_access_key`, `minio_secret_key`) and are not env-loaded fields.
- Note: use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` as the actual env vars.

### Web/API Service

- `Required` for non-dashboard protected endpoints: `API_SHARED_SECRET` (protected API requests are rejected when unset)
- `Optional`: `WEB_HOST` (default: `0.0.0.0`; direct process bind host, while Compose pins the container bind host to `0.0.0.0`)
- `Optional`: `WEB_HOST_BIND` (default: `127.0.0.1`; Compose host bind for Cloudflare Tunnel/local exposure)
- `Optional`: `WEB_PORT` (direct process listen port; Compose pins the container's internal listen port to `8090`; host-run `./scripts/dev.sh` ignores `.env` for this key and defaults to `CONDUCTOR_PORT + 5` inside Conductor, otherwise a deterministic per-worktree value near `18080 + WORKTREE_ENV_SLOT`)
- `Optional`: `WEB_HOST_PORT` (published host port for Docker/Cloudflare Tunnel; default `8090` when running `docker compose` directly; `./scripts/docker-compose.sh` computes `CONDUCTOR_PORT + 2` inside Conductor, otherwise a deterministic per-worktree value when unset, and pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)
- Deprecated fallback names still work for now: `WEBHOOK_INGEST_HOST`, `WEBHOOK_INGEST_PORT`, `WEBHOOK_INGEST_HOST_BIND`, `WEBHOOK_INGEST_HOST_PORT`.

### Backend API OIDC Session Auth

- `Optional` (required when enabling OIDC login): `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`
- `Optional`: `OIDC_SCOPE` (default: `openid profile email groups`)
- `Optional`: `OIDC_GROUPS_CLAIM` (default: `groups`)
- `Optional`: `OIDC_ADMIN_GROUPS` (default: `authentik Admins`; grants full dashboard admin permissions)
- `Optional`: `OIDC_CALLBACK_PATH` (default: `/auth/callback`)
- `Optional`: `OIDC_REDIRECT_BASE_URL` (default: infer from request base URL)
- `Optional`: `AUTH_SESSION_COOKIE_NAME` (default: `five08_session`)
- `Optional`: `AUTH_SESSION_TTL_SECONDS` (default: `86400`, one day)
- `Optional`: `DASHBOARD_DEFAULT_PATH` (default: `/dashboard`)
- `Optional`: `DASHBOARD_PUBLIC_BASE_URL` (public base URL for generated deep links; set this in production, for example `https://workflows.508.dev`)
- Note: OIDC timeout/cache timings are fixed in code; auth cookies always use `SameSite=Lax` and enable `secure` automatically outside local/dev/test environments.

### Discord Admin Deep-Link Validation

- `Optional`: `DISCORD_SERVER_ID` (required for Discord API fallback role checks)
- `Optional`: `DISCORD_ADMIN_ROLES` (default: `Admin,Owner`; marks Discord roles eligible for admin dashboard permissions)
- `Optional`: `DISCORD_API_TIMEOUT_SECONDS` (default: `8.0`)
- `Optional`: `DISCORD_LINK_TTL_SECONDS` (default: `600`)
- `Optional`: `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS` (code default: `true`; local `.env.example` sets `false` so Discord dashboard links work without OIDC; set `true` in production with Authentik)
- `Optional`: `DISCORD_BOT_TOKEN` (needed only for fallback Discord API checks; DB role check remains primary)
- Note: Discord dashboard links are available to active CRM-linked Discord users
  with Steering Committee role or higher. Steering Committee receives CRM people
  lookup and onboarding permissions. Jobs, reruns, people sync, and audit are
  sensitive admin permissions and require an SSO-validated dashboard session in
  production; local/dev/test environments allow them for development.

### Worker Consumer

- `Optional`: `WORKER_NAME` (default: `worker`)
- `Optional`: `DISCORD_BOT_INTERNAL_BASE_URL` (default: `http://127.0.0.1:3000`; `./scripts/dev.sh worker` overrides it to the worktree bot port, Compose injects `http://discord_bot:3000`)
- `Optional`: `WORKER_QUEUE_NAMES` (default: `jobs.default`, comma-separated)
- `Optional`: `WORKER_BURST` (default: `false`)

### Worker CRM Sync + Skills Extraction

- `Optional`: `CRM_SYNC_ENABLED` (default: `true`)
- `Optional`: `CRM_SYNC_INTERVAL_SECONDS` (default: `900`)
- `Optional`: `CRM_SYNC_PAGE_SIZE` (default: `200`)
- `Optional`: `CHECK_EMAIL_WAIT` (default: `2`; minutes between mailbox polls)
- `Optional`: `MAX_ATTACHMENTS_PER_CONTACT` (default: `3`)
- `Optional`: `MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `ALLOWED_FILE_TYPES` (default: `pdf,doc,docx,txt`)
- `Optional`: `OPENAI_API_KEY` (if unset, heuristic extraction is used)
- `Optional`: `OPENAI_BASE_URL` (set `https://openrouter.ai/api/v1` for OpenRouter)
- `Optional`: `OPENAI_DIRECT_API_KEY` / `OPENAI_API_KEY_DIRECT`, `OPENAI_DIRECT_BASE_URL`, `OPENAI_DIRECT_MODEL` (direct OpenAI fallback when the primary base URL is Bifrost)
- `Optional`: `FIREWORKS_API_KEY` (direct fallback when Bifrost is not routing Fireworks)
- `Optional`: `OPENROUTER_API_KEY` (direct OpenRouter fallback when Bifrost is unavailable or misconfigured)
- `Optional`: `LANGFUSE_BASE_URL` (Langfuse endpoint for LLM tracing/observability)
- `Optional`: `RESUME_AI_API_KEY`, `RESUME_AI_BASE_URL` (resume-specific provider; falls back to `OPENAI_API_KEY` / `OPENAI_BASE_URL` when unset or incomplete)
- `Optional`: `RESUME_AI_MODEL` (default: `gpt-4.1-mini`; use plain names like `gpt-4.1-mini`, OpenRouter gets auto-prefixed to `openai/<model>`)
- Note: resume/profile LLM calls retry matching direct providers after Bifrost request failures. For example, `RESUME_AI_MODEL=openrouter/openai/gpt-4.1-mini` through Bifrost retries direct OpenRouter as `openai/gpt-4.1-mini`, then direct OpenAI when those keys are configured.
- `Optional`: `OPENAI_MODEL` (default: `gpt-5-mini`; fallback/legacy model setting)
- `Optional`: `RESUME_EXTRACTOR_VERSION` (default: `v1`; used in resume processing idempotency/ledger keys)
- `Optional`: `INTAKE_RESUME_FETCH_TIMEOUT_SECONDS` (default: `20.0`; timeout for intake resume URL downloads)
- `Optional`: `INTAKE_RESUME_MAX_REDIRECTS` (default: `3`; max redirects followed for intake resume URL downloads)
- `Optional`: `INTAKE_RESUME_ALLOWED_HOSTS` (default: empty; optional comma-separated host allowlist for intake resume URL downloads)
- `Optional`: `EMAIL_RESUME_INTAKE_ENABLED` (default: `false`; enables worker-side mailbox resume processing loop)
- `Optional`: `EMAIL_RESUME_ALLOWED_EXTENSIONS` (default: `pdf,doc,docx`)
- `Optional`: `EMAIL_RESUME_MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `EMAIL_REQUIRE_SENDER_AUTH_HEADERS` (default: `true`; requires SPF/DKIM/DMARC pass headers)
- `Required when EMAIL_RESUME_INTAKE_ENABLED=true`: `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `IMAP_SERVER`
- Note: worker CRM wiring uses the fixed LinkedIn field `cLinkedIn`, keeps the intake-completed field unset, and matches resume filenames with `resume,cv,curriculum`.

### Discord Bot Core

- `Required`: `DISCORD_BOT_TOKEN`
- `Optional`: `BACKEND_API_BASE_URL` (default: `http://127.0.0.1:8090`; `./scripts/dev.sh` overrides it to the worktree web/API port, Compose injects `http://web:8090`)
- `Optional`: `HEALTHCHECK_PORT` (host-run `./scripts/dev.sh` ignores `.env` for this key and defaults to `CONDUCTOR_PORT + 6` inside Conductor, otherwise a deterministic per-worktree value near `30000 + WORKTREE_ENV_SLOT`; export it in your shell only when you intentionally want a fixed port, and avoid browser-unsafe ports such as `5060`)
- `Optional`: `DISCORD_DEFAULT_JOB_FORUM_CHANNELS` (default: `gigs:part_time,fulltime-roles:full_time`; comma-separated `forum-name:posting_type` list auto-registered and backfilled on bot startup)
- Note: bot message chunking uses Discord's 2000 character limit in code.

### Discord Agent Gateway

- `Optional`: `AGENT_API_TIMEOUT_SECONDS` (default: `8.0`; timeout for synchronous Discord agent gateway calls)
- `Optional`: `AGENT_FAST_MODEL`, `AGENT_FAST_BASE_URL`, `AGENT_FAST_API_KEY`
- `Optional`: `AGENT_STRONG_MODEL`, `AGENT_STRONG_BASE_URL`, `AGENT_STRONG_API_KEY`
- `Optional`: `AGENT_REASONING_MODEL`, `AGENT_REASONING_BASE_URL`, `AGENT_REASONING_API_KEY`
- `Optional`: `AGENT_FALLBACK_MODEL` (default: `gpt-4.1-mini`; uses `OPENAI_API_KEY` / `OPENAI_BASE_URL`)
- `Optional`: `AGENT_INTENT_NORMALIZER_ENABLED` (default: `true`; when deterministic parsing fails, asks the fast agent model to rewrite loose phrasing into one supported command shape)
- `Optional`: `AGENT_INTENT_NORMALIZER_TIMEOUT_SECONDS` (default: `3.0`)
- Note: tier-specific agent models can point at OpenAI-compatible providers such as Bifrost or Fireworks. Agent model base URLs must be HTTPS endpoints on `bifrost.508.dev`, `api.openai.com`, `api.fireworks.ai`, or `openrouter.ai`, except the internal Docker-network Bifrost URL `http://bifrost:8080/openai` is also allowed for same-host deployments. If `OPENAI_BASE_URL` points at Bifrost and tier-specific `AGENT_*` values are unset, the planner defaults to Fireworks Kimi via Bifrost as `fireworks/accounts/fireworks/models/kimi-k2p6`. Explicit Bifrost provider-prefixed planner models, such as `openrouter/openai/gpt-4.1-mini`, are passed through unchanged. If Bifrost is not configured and `FIREWORKS_API_KEY` is set, the planner falls back to direct Fireworks as `accounts/fireworks/models/kimi-k2p6`. If a configured provider is missing its usable API key, it is skipped and the fallback order is `reasoning -> strong -> fast -> AGENT_FALLBACK_MODEL -> gpt-4.1-mini`; `strong` falls back through `fast`, and `fast` falls back through the OpenAI fallback.
- Note: the Discord bot accepts agent requests through `/agent` and explicit bot mentions. Mentioned requests work in server channels and threads; lightweight public-safe clarifications stay in a response thread, while executed mention results and write confirmations are sent by DM so private memory and sensitive tool output can be shown only in the private destination. Sensitive reports point to ephemeral slash commands. Replies in bot-created agent threads continue the agent flow without repeating the mention.
- Agent tools follow the deterministic path: deterministic parsing runs first, the optional LLM intent normalizer can only rewrite unsupported phrasing into supported command shapes, policy authorizes scopes, write tools require confirmation, and the backend executes known-good tool code.
- `Optional`: `GITHUB_API_TOKEN`, `GITHUB_DEFAULT_REPO`, `GITHUB_ALLOWED_REPOS` (comma-separated; GitHub Issues are the canonical code-task backend for agent-created code work, and agent tools only access the default/allowed repositories).
- Existing integration tools also expose CRM contact search/update, DocuSeal member-agreement submission, and Migadu mailbox creation when their normal service env vars are configured.
- Note: the current generic task tool registry is an MVP, process-local in-memory store for non-code/org tasks until the task-management platform is selected. It is not durable across backend restarts or shared across multiple API workers. Task reads require an explicit project filter to avoid guild-wide task enumeration.
- Note: agent audit writes are best-effort. If the audit store is down, agent actions can still execute and should be considered temporarily untraced until audit ingestion recovers.

### Discord CRM Audit Logging (Best Effort)

- `Optional`: `AUDIT_API_BASE_URL` (defaults to `BACKEND_API_BASE_URL`; Compose clears stale `.env` values so the fallback uses the injected backend URL)
- `Optional`: `AUDIT_API_TIMEOUT_SECONDS` (default: `2.0`)
- `Optional`: `DISCORD_LOGS_WEBHOOK_URL` (if set, command and job events are posted to this Discord webhook)
- `Optional`: `DISCORD_LOGS_WEBHOOK_WAIT` (default: `true`; request delivery confirmation from Discord)

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

## CRM REPL And Batch Updates

For EspoCRM cleanup and bulk edits outside the Discord UI, use `crmctl` from the shared package.

The REPL entrypoint is:

```bash
uv run --package five08 crmctl repl
```

Inside the REPL you get:

- `search(**criteria)` for contact lookups
- `get(contact_id)` for a mutable contact object
- `contact.save()` to persist pending changes
- `batch_update(where={...}, update={...}, apply=False)` for preview/apply flows
- `FROM_LOCATION` to infer `cTimezone` from location fields
- `field__operator=value` filters for both CLI `--where` and Python kwargs

Examples:

```bash
uv run --package five08 crmctl search \
  --where timezone__is_null=true \
  --where location__is_not_null=true \
  --where member_type__in=Member,Prospect

uv run --package five08 crmctl search \
  --where roles__contains=developer \
  --where phone__not_like=+1%

uv run --package five08 crmctl batch-update \
  --where timezone__is_null=true \
  --where location__is_not_null=true \
  --update timezone=@location

uv run --package five08 crmctl batch-update \
  --where timezone__is_null=true \
  --where location__is_not_null=true \
  --update timezone=@location \
  --apply
```

For Discord bot docs, see [`Discord Bot`](./apps/discord_bot/README.md).

For local development helper commands, see [`DEVELOPMENT.md`](./DEVELOPMENT.md).


## Deployment

Deploy as a single Compose application.

MinIO is used as the internal transfer mechanism so file handoffs stay inside the stack.
External object storage adapters can be added later for multi-cloud or vendor-specific routing.

This keeps one stack and one shared env set while still allowing independent service scaling/restarts (`discord_bot`, `web`, `worker`).
