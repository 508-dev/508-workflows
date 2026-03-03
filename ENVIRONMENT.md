# Environment Variables

Use `.env.example` as the source of defaults.

## Required

- `ESPO_BASE_URL`
- `ESPO_API_KEY`
- `API_SHARED_SECRET` (required for protected endpoints)
- `MINIO_ROOT_PASSWORD` (required in non-local environments)
- `DISCORD_BOT_TOKEN` (Discord bot runtime)

## Core Runtime (Bot + Worker)

- `Optional` (non-local): `ENVIRONMENT` (default: `local`; non-local environments must set explicit `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`)
- `Optional`: `SENTRY_DSN` (default: unset; set to enable Sentry event capture)
- `Optional`: `SENTRY_ENVIRONMENT` (defaults to `ENVIRONMENT`)
- `Optional`: `SENTRY_RELEASE` (optional release identifier for Sentry)
- `Optional`: `SENTRY_SAMPLE_RATE` (default: `1.0`)
- `Optional`: `SENTRY_TRACES_SAMPLE_RATE` (default: `0.0`)
- `Optional`: `SENTRY_PROFILES_SAMPLE_RATE` (default: `0.0`)
- `Optional`: `SENTRY_SEND_DEFAULT_PII` (default: `false`)
- `Optional`: `SENTRY_DEBUG` (default: `false`)

## Queue + Job Runtime

- `Optional`: `LOG_LEVEL` (default: `INFO`)
- `Optional`: `REDIS_URL` (default: `redis://redis:6379/0`)
- `Optional`: `REDIS_QUEUE_NAME` (default: `jobs.default`)
- `Optional`: `REDIS_KEY_PREFIX` (default: `jobs`)
- `Optional`: `JOB_TIMEOUT_SECONDS` (default: `600`)
- `Optional`: `JOB_RESULT_TTL_SECONDS` (default: `3600`)
- `Optional`: `JOB_MAX_ATTEMPTS` (default: `8`)
- `Optional`: `JOB_RETRY_BASE_SECONDS` (default: `5`)
- `Optional`: `JOB_RETRY_MAX_SECONDS` (default: `300`)

## Postgres + Compose Exposure

- `Optional`: `POSTGRES_URL` (default: `postgresql://postgres@postgres:5432/workflows`)
- `Optional` (Compose DB container): `POSTGRES_DB` (default: `workflows`)
- `Optional` (Compose DB container): `POSTGRES_USER` (default: `postgres`)
- `Optional` (Compose DB container): `POSTGRES_PASSWORD` (default: `postgres`)
- `Optional` (Compose host bind): `POSTGRES_HOST_BIND` (default: `127.0.0.1`)
- `Optional` (Compose host port): `POSTGRES_PORT` (default: `5432`)

## MinIO + Internal Transfers

- `Optional`: `MINIO_ENDPOINT` (default: `http://minio:9000`)
- `Optional`: `MINIO_INTERNAL_BUCKET` (default: `internal-transfers`)
- `Optional`: `MINIO_ROOT_USER` (default: `internal`)
- `Optional`: `MINIO_HOST_BIND` (default: `127.0.0.1`; set `0.0.0.0` to expose externally)
- `Optional`: `MINIO_API_PORT` (default: `9000`)
- `Optional`: `MINIO_CONSOLE_PORT` (default: `9001`)

### Notes

- `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are `SharedSettings` alias properties (`minio_access_key`, `minio_secret_key`) and are not env-loaded fields.
- Use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` as the actual env vars.

## Backend API Ingest

- `Optional`: `WEBHOOK_INGEST_HOST` (default: `0.0.0.0`)
- `Optional`: `WEBHOOK_INGEST_PORT` (default: `8090`)
- `Required`: `API_SHARED_SECRET` (global shared secret for protected endpoints and webhooks)

## Backend API OIDC Session Auth

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

## Authentication + Dashboard

- `Optional`: `AUTH_SESSION_COOKIE_NAME` (default: `five08_session`)
- `Optional`: `AUTH_COOKIE_SECURE` (default: `false`)
- `Optional`: `AUTH_COOKIE_SAMESITE` (default: `lax`)
- `Optional`: `DASHBOARD_DEFAULT_PATH` (default: `/dashboard`)
- `Optional`: `DASHBOARD_PUBLIC_BASE_URL` (base URL for generated deep links)

## Discord Admin Deep-Link Validation

- `Optional`: `DISCORD_ADMIN_GUILD_ID` (required for Discord API fallback role checks)
- `Optional`: `DISCORD_ADMIN_ROLES` (default: `Admin,Owner,Steering Committee`)
- `Optional`: `DISCORD_API_TIMEOUT_SECONDS` (default: `8.0`)
- `Optional`: `DISCORD_LINK_TTL_SECONDS` (default: `600`)
- `Optional`: `DISCORD_BOT_TOKEN` (needed only for fallback Discord API checks; DB role check remains primary)

## Worker Consumer

- `Optional`: `WORKER_NAME` (default: `worker`)
- `Required`: `WORKER_QUEUE_NAMES` (default: `jobs.default`)
- `Required` (single queue): only one queue value is currently supported. Configure one name only, without commas, to keep worker actor registration and consumer consumption aligned.
- `Optional`: `WORKER_BURST` (default: `false`)

## Worker CRM Sync + Skills Extraction

- `Optional`: `CRM_SYNC_ENABLED` (default: `true`)
- `Optional`: `CRM_SYNC_INTERVAL_SECONDS` (default: `900`)
- `Optional`: `CRM_SYNC_PAGE_SIZE` (default: `200`)
- `Optional`: `CHECK_EMAIL_WAIT` (default: `2`; minutes between mailbox polls)
- `Optional`: `CRM_LINKEDIN_FIELD` (default: `cLinkedIn`)
- `Optional`: `MAX_ATTACHMENTS_PER_CONTACT` (default: `3`)
- `Optional`: `MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `ALLOWED_FILE_TYPES` (default: `pdf,doc,docx,txt`)
- `Optional`: `RESUME_KEYWORDS` (default: `resume,cv,curriculum`)
- `Optional`: `OPENAI_API_KEY` (if unset, heuristic extraction is used)
- `Optional`: `OPENAI_BASE_URL` (set `https://openrouter.ai/api/v1` for OpenRouter)
- `Optional`: `RESUME_AI_MODEL` (default: `5o-mini`; use plain names like `5o-mini`, OpenRouter gets auto-prefixed to `openai/<model>`)
- `Optional`: `OPENAI_MODEL` (default: `5o-mini`; fallback/legacy model setting)
- `Optional`: `RESUME_EXTRACTOR_VERSION` (default: `v1`; used in resume processing idempotency/ledger keys)
- `Optional`: `INTAKE_RESUME_FETCH_TIMEOUT_SECONDS` (default: `20.0`; timeout for intake resume URL downloads)
- `Optional`: `INTAKE_RESUME_MAX_REDIRECTS` (default: `3`; max redirects followed for intake resume URL downloads)
- `Optional`: `INTAKE_RESUME_ALLOWED_HOSTS` (default: empty; optional comma-separated host allowlist for intake resume URL downloads)
- `Optional`: `EMAIL_RESUME_INTAKE_ENABLED` (default: `false`; enables worker-side mailbox resume processing loop)
- `Optional`: `EMAIL_RESUME_ALLOWED_EXTENSIONS` (default: `pdf,doc,docx`)
- `Optional`: `EMAIL_RESUME_MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `EMAIL_REQUIRE_SENDER_AUTH_HEADERS` (default: `true`; requires SPF/DKIM/DMARC pass headers)
- `Required when EMAIL_RESUME_INTAKE_ENABLED=true`: `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `IMAP_SERVER`

## Discord Bot Core

- `Optional`: `BACKEND_API_BASE_URL` (default: `http://api:8090`)
- `Optional`: `HEALTHCHECK_PORT` (default: `3000`)
- `Optional`: `DISCORD_SENDMSG_CHARACTER_LIMIT` (default: `2000`)

## Migadu Mailbox Automation

- `Required for /create-mailbox`: `MIGADU_API_USER`, `MIGADU_API_KEY`
- `Optional`: `MIGADU_MAILBOX_DOMAIN` (default: `508.dev`)

## Discord CRM Audit Logging (Best Effort)

- `Optional`: `AUDIT_API_BASE_URL` (when set with `API_SHARED_SECRET`, CRM commands emit best-effort audit events)
- `Optional`: `AUDIT_API_TIMEOUT_SECONDS` (default: `2.0`)
- `Optional`: `DISCORD_LOGS_WEBHOOK_URL` (if set, command and job events are posted to this Discord webhook)
- `Optional`: `DISCORD_LOGS_WEBHOOK_WAIT` (default: `true`; appends `wait=true` unless already present in the webhook URL)

## Kimai (Legacy/Deprecating)

- `Currently required by config model`: `KIMAI_BASE_URL`, `KIMAI_API_TOKEN`
