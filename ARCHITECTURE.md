# Architecture

The 508.dev integrations repo is a service-oriented monorepo. It keeps Discord
gateway logic, HTTP/API handling, background job execution, and shared runtime
utilities in separate packages while sharing one deployment stack.

## Service Layout

```text
apps/
  discord_bot/        Discord gateway process and bot commands
  api/                FastAPI dashboard, auth, webhooks, and ingest API
  admin_dashboard/    React + Vite dashboard source
  worker/             queue consumer, jobs, CRM processors, migrations
packages/
  shared/             settings, queue helpers, clients, CRM utilities, agent code
```

Primary runtime services:

- `discord_bot`: Discord gateway process. Bot features are loaded as cogs.
- `web`: FastAPI dashboard + ingest service. Validates requests, persists jobs,
  and enqueues work.
- `worker`: background consumer. Executes jobs from Redis and persists outcomes.
- `redis`: queue transport.
- `postgres`: source-of-truth job state, audit events, identity/cache tables.
- `minio`: internal S3-compatible file handoff path.

## Data And Queue Model

The API keeps ingest endpoints fast: validate input, persist a job row, enqueue,
and return. Long-running processing belongs in worker jobs.

Job state is persisted in Postgres table `jobs` with:

- job type
- status: `queued`, `running`, `succeeded`, `failed`, `dead`, `canceled`
- payload/result
- idempotency key
- attempt counters
- retry scheduling through `run_after`
- lock metadata

Redis is the delivery transport. Postgres remains the source of truth for
retries, idempotency, and inspection.

Worker schema is managed by Alembic migrations in
`apps/worker/src/five08/worker/migrations`. The Web/API service applies these
migrations on startup through `run_job_migrations()`.

## Dashboard And Auth

The operations dashboard is served at `/dashboard` by the FastAPI app. The React
source lives in `apps/admin_dashboard` and builds into the API package static
directory.

Dashboard browser routes and `/dashboard/api/*` use an HttpOnly session cookie.
They do not accept `X-API-Secret`.

Dashboard sessions are created by:

- OIDC login routes, when OIDC is configured.
- Discord dashboard deep links created by `/auth/discord/links`.
- Local/dev CLI-generated deep links through `./scripts/dev.sh login`.

Discord-backed sessions carry the linked CRM contact id from the local `people`
cache when available. Steering Committee+ sessions can use broader CRM people
lookup and onboarding views. Admin+ sessions can access jobs, reruns, sync
actions, and audit views. The Discord `Workflows Engineer` role is a
Steering Committee peer for write access, with admin read access to jobs/audit
and dry-run responses for admin-only rerun/sync writes.

Sensitive dashboard permissions require SSO validation in production. Local,
dev, development, and test environments allow trusted dev role context for
faster dashboard testing.

## Protected API Routes

Non-dashboard protected API routes use `API_SHARED_SECRET` in `X-API-Secret`.
This includes webhook and secret-backed operational routes until per-webhook or
per-route auth is introduced.

Human-triggered CRM actions should write best-effort audit events. Audit writes
must not break command execution if the audit path is temporarily unavailable.

## Shared Runtime Code

Cross-service runtime code belongs in `packages/shared/src/five08/`:

- settings
- queue helpers
- integration clients
- CRM utilities
- project/gig helpers
- agent orchestration support

Service-specific behavior should stay inside the owning app package.

## Internal File Movement

MinIO is the internal transfer mechanism for file handoffs inside the stack.
The default internal bucket is `internal-transfers`.

External object-store adapters should remain separate from this internal transfer
path so the deployment can later support multi-cloud or vendor-specific storage
without changing internal job mechanics.

## Deployment Shape

Production deploys as one Compose application with independently restartable
services:

- `discord_bot`
- `web`
- `worker`
- `redis`
- `postgres`
- `minio`

`compose.yaml` is the canonical Coolify/base stack. The root
`docker-compose.yml` is a compatibility wrapper. Local host-port publishing is
handled by `compose.local.yaml` through `./scripts/docker-compose.sh`.

The base stack attaches app services to the external infra network named by
`INFRA_DOCKER_NETWORK`, allowing same-host services such as Bifrost and Langfuse
to be addressed by Docker DNS aliases.

## Extension Rules

- Add bot features as isolated cogs.
- Add shared config in `packages/shared/src/five08/settings.py`.
- Add service-specific config in that service's `config.py`.
- Keep deterministic routing, retries, status-code handling, and validation in
  code rather than LLM calls.
- Register rerunnable job callables consistently for backend rerun resolution and
  worker execution.
- Keep audit logging best effort.
