# Configuration Reference

Use `.env.example` as the source of truth for defaults and available settings.
This document groups the settings by operational area and calls out the values
that most often matter in local development and deployment.

## Core Runtime

- `ENVIRONMENT`: defaults to `local`. Non-local values require explicit runtime
  secrets such as `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`.
- `LOG_LEVEL`: defaults to `INFO`.
- `API_SHARED_SECRET`: required for protected non-dashboard API routes and for
  `./scripts/dev.sh login`.
- `CONFIG_SECRET_KEY`: env-only key used to encrypt secret values saved from the
  admin dashboard configuration page. Dashboard-managed secret saves are
  rejected when this is unset.
- `WEBHOOK_SHARED_SECRET`: optional separate secret for external `/webhooks/*`
  callers such as DocuSeal, Google Forms, and EspoCRM. When unset, webhook
  routes fall back to `API_SHARED_SECRET` for compatibility.

## Admin Dashboard Configuration

The admin dashboard can store allowlisted integration credentials and business
knobs in Postgres. Non-empty env or `.env` values always win and appear as
environment-locked in the dashboard. If no env value is present, the database
value overrides the code default.

Secret values saved from the dashboard are encrypted before storage using
`CONFIG_SECRET_KEY`. API responses never include full secret values; they
return only configured state and a short first/last-character mask for
confirmation.

## Queue And Jobs

- `REDIS_URL`
- `REDIS_QUEUE_NAME`
- `REDIS_KEY_PREFIX`
- `JOB_TIMEOUT_SECONDS`
- `JOB_RESULT_TTL_SECONDS`
- `JOB_MAX_ATTEMPTS`
- `JOB_RETRY_BASE_SECONDS`
- `JOB_RETRY_MAX_SECONDS`
- `GIG_RECRUITING_STALE_DAYS`
- `GIG_RECRUITING_REMINDER_MAX_AGE_DAYS`

`./scripts/dev.sh` overrides local Redis settings to deterministic per-worktree
localhost ports. Compose injects Docker-network URLs.

## Postgres

- `POSTGRES_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST_BIND`
- `POSTGRES_HOST_PORT`

For local dev, `./scripts/dev.sh` and `./scripts/docker-compose.sh` compute
deterministic per-worktree ports unless values are pinned in `.env` or the
invoking shell.

Inside Conductor, `CONDUCTOR_PORT` is treated as the first port in the
workspace's 10-port range for unset worktree defaults: Redis uses `+0`,
Postgres `+1`, Compose web `+2`, MinIO API `+3`, MinIO console `+4`,
host-run web/API `+5`, and bot health `+6`. Explicit service port overrides keep
their current precedence rules.

## MinIO

- `MINIO_ENDPOINT`
- `MINIO_INTERNAL_BUCKET`
- `MINIO_ROOT_USER`
- `MINIO_ROOT_PASSWORD`
- `MINIO_HOST_BIND`
- `MINIO_API_HOST_PORT`
- `MINIO_CONSOLE_HOST_PORT`

Use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` as the actual env vars.
`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are settings aliases, not env-loaded
fields.

## Web/API Service

- `WEB_HOST`
- `WEB_PORT`
- `WEB_HOST_BIND`
- `WEB_HOST_PORT`
- `DASHBOARD_DEFAULT_PATH`
- `DASHBOARD_PUBLIC_BASE_URL` (public browser origin for generated dashboard links and dashboard write CSRF checks)

Deprecated fallback names still work for now:

- `WEBHOOK_INGEST_HOST`
- `WEBHOOK_INGEST_PORT`
- `WEBHOOK_INGEST_HOST_BIND`
- `WEBHOOK_INGEST_HOST_PORT`

Host-run `./scripts/dev.sh` ignores `.env` for `WEB_PORT` and computes a
deterministic per-worktree value unless `WEB_PORT` is exported in the shell.

## Dashboard Auth

OIDC settings:

- `OIDC_ISSUER_URL`
- `OIDC_CLIENT_ID`
- `OIDC_CLIENT_SECRET`
- `OIDC_SCOPE`
- `OIDC_GROUPS_CLAIM`
- `OIDC_ADMIN_GROUPS`
- `OIDC_CALLBACK_PATH`
- `OIDC_REDIRECT_BASE_URL`
- `AUTH_SESSION_COOKIE_NAME`
- `AUTH_SESSION_TTL_SECONDS`

Discord dashboard-link settings:

- `DISCORD_SERVER_ID`
- `DISCORD_ADMIN_ROLES`
- `DISCORD_API_TIMEOUT_SECONDS`
- `DISCORD_LINK_TTL_SECONDS`
- `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS`
- `DISCORD_BOT_TOKEN`

Local `.env.example` sets `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=false` so
Discord dashboard links and CLI-created dev links can create sessions without an
OIDC roundtrip. Production should keep identity checks enabled.

## Worker

- `WORKER_NAME`
- `WORKER_QUEUE_NAMES`
- `WORKER_BURST`
- `WORKER_API_BASE_URL`
- `DISCORD_BOT_INTERNAL_BASE_URL`

Worker startup currently resolves to one effective queue name. Keep this explicit
if true multi-queue routing is introduced later.

## CRM And Resume Processing

- `ESPO_BASE_URL`
- `ESPO_API_KEY`
- `CRM_SYNC_ENABLED`
- `CRM_SYNC_INTERVAL_SECONDS`
- `CRM_SYNC_PAGE_SIZE`
- `CHECK_EMAIL_WAIT`
- `MAX_ATTACHMENTS_PER_CONTACT`
- `MAX_FILE_SIZE_MB`
- `ALLOWED_FILE_TYPES`
- `RESUME_EXTRACTOR_VERSION`
- `INTAKE_RESUME_FETCH_TIMEOUT_SECONDS`
- `INTAKE_RESUME_MAX_REDIRECTS`
- `INTAKE_RESUME_ALLOWED_HOSTS`

Worker CRM wiring uses the fixed LinkedIn field `cLinkedIn`, keeps the
intake-completed field unset, and matches resume filenames with
`resume`, `cv`, and `curriculum`.

## LLM And Observability

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `OPENAI_DIRECT_API_KEY` / `OPENAI_API_KEY_DIRECT`
- `OPENAI_DIRECT_BASE_URL`
- `OPENAI_DIRECT_MODEL`
- `FIREWORKS_API_KEY`
- `OPENROUTER_API_KEY`
- `LANGFUSE_BASE_URL`
- `RESUME_AI_API_KEY`
- `RESUME_AI_BASE_URL`
- `RESUME_AI_MODEL`

Resume/profile LLM calls retry matching direct providers after Bifrost request
failures when direct provider credentials are configured.

## Discord Bot And Agent Gateway

Discord bot:

- `DISCORD_BOT_TOKEN`
- `BACKEND_API_BASE_URL`
- `HEALTHCHECK_PORT`
- `DISCORD_DEFAULT_JOB_FORUM_CHANNELS`
- `DISCORD_LOGS_WEBHOOK_URL`
- `DISCORD_LOGS_WEBHOOK_WAIT`

Agent gateway:

- `AGENT_API_TIMEOUT_SECONDS`
- `AGENT_FAST_MODEL`, `AGENT_FAST_BASE_URL`, `AGENT_FAST_API_KEY`
- `AGENT_STRONG_MODEL`, `AGENT_STRONG_BASE_URL`, `AGENT_STRONG_API_KEY`
- `AGENT_REASONING_MODEL`, `AGENT_REASONING_BASE_URL`, `AGENT_REASONING_API_KEY`
- `AGENT_FALLBACK_MODEL`
- `AGENT_INTENT_NORMALIZER_ENABLED`
- `AGENT_INTENT_NORMALIZER_TIMEOUT_SECONDS`
- `GITHUB_API_TOKEN`
- `GITHUB_DEFAULT_REPO`
- `GITHUB_ALLOWED_REPOS`

Agent model base URLs must be HTTPS endpoints on allowed provider hosts, except
the internal Docker-network Bifrost URL `http://bifrost:8080/openai` is allowed
for same-host deployments.

## Legacy/Deprecating Integrations

- `KIMAI_BASE_URL`
- `KIMAI_API_TOKEN`

Kimai settings are still required by the current config model.
