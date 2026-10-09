# Environment Variables

Use `.env.example` as the source of defaults.

When running inside PASEO, `PASEO_PORT_BASE` and `PASEO_PORT_END` reserve the
inclusive range used for unset worktree defaults. The range must contain at
least seven ports: Redis uses `+0`, Postgres `+1`, the Compose web port `+2`,
MinIO API `+3`, MinIO Console `+4`, host-run web/API `+5`, and the bot health
check `+6`. PASEO takes precedence over Conductor. When running inside
Conductor, `CONDUCTOR_PORT` is treated as the first port in the workspace's
10-port range for unset worktree defaults: Redis uses `+0`,
Postgres `+1`, Compose web `+2`, MinIO API `+3`, MinIO console `+4`, host-run
web/API `+5`, and bot health `+6`. Explicit service port overrides keep their
current precedence rules.

## Required For A Healthy Non-Local Runtime

- `API_SHARED_SECRET` (required for protected endpoints)
- `POSTGRES_URL` (required for database-backed API and worker health)
- `MINIO_ROOT_PASSWORD` (required for internal transfer storage)
- `DISCORD_BOT_TOKEN` (Discord bot runtime)

The app avoids eager settings-construction failures where possible so failed
deployments can still expose logs and health responses. Missing runtime
dependencies should surface as degraded health or route/job failures rather than
Pydantic import errors.

## Core Runtime (Bot + Worker)

- `Optional` (non-local): `ENVIRONMENT` (default: `local`; non-local environments should set explicit `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`)
- `Optional`: `SENTRY_DSN` (default: unset; set to enable Sentry event capture)
- `Optional`: `SENTRY_SEND_DEFAULT_PII` (default: `false`)
- `Optional`: `SENTRY_DEBUG` (default: `false`)
- Note: Sentry environment always follows `ENVIRONMENT`; release/tracing/profiling sampling are fixed in code.

## Queue + Job Runtime

- `Optional`: `LOG_LEVEL` (default: `INFO`)
- `Optional`: `REDIS_URL` (default: `redis://127.0.0.1:6379/0`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects `redis://redis:6379/0`)
- `Optional`: `REDIS_QUEUE_NAME` (default: `jobs.default`)
- `Optional`: `REDIS_KEY_PREFIX` (default: `jobs`)
- `Optional`: `REDIS_HOST_BIND` (default: `127.0.0.1`)
- `Optional`: `REDIS_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 0` inside Conductor, otherwise computed per worktree as `12000 + WORKTREE_ENV_SLOT`; use `6379` only if explicitly pinned via env/.env; see `./scripts/docker-compose.sh print-ports`)
- `Optional`: `JOB_TIMEOUT_SECONDS` (default: `600`, minimum: `6`; the worker
  reserves five seconds before the durable lease expires for cancellation and
  recovery)
- `Optional`: `JOB_RESULT_TTL_SECONDS` (default: `3600`)
- `Optional`: `JOB_MAX_ATTEMPTS` (default: `8`)
- `Optional`: `JOB_RETRY_BASE_SECONDS` (default: `5`)
- `Optional`: `JOB_RETRY_MAX_SECONDS` (default: `300`)

## Postgres + Compose Exposure

- `Required for healthy non-local runtime`: `POSTGRES_URL` (local default: `postgresql://postgres:postgres@127.0.0.1:5432/workflows`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects a Docker-network URL)
- `Optional` (Compose DB container): `POSTGRES_DB` (default: `workflows`)
- `Optional` (Compose DB container): `POSTGRES_USER` (default: `postgres`)
- `Optional` (Compose DB container): `POSTGRES_PASSWORD` (default: `postgres`)
- `Optional` (Compose host bind): `POSTGRES_HOST_BIND` (default: `127.0.0.1`)
- `Optional` (Compose host port): `POSTGRES_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 1` inside Conductor, otherwise deterministic per-worktree value `15432 + WORKTREE_ENV_SLOT`; set `POSTGRES_HOST_PORT=5432` to pin it to `5432`; see `./scripts/docker-compose.sh print-ports`)

## MinIO + Internal Transfers

- `Optional`: `MINIO_ENDPOINT` (default: `http://127.0.0.1:9000`; `./scripts/dev.sh` overrides it to a deterministic per-worktree localhost port, Compose injects `http://minio:9000`)
- `Optional`: `MINIO_INTERNAL_BUCKET` (default: `internal-transfers`)
- `Optional`: `MINIO_ROOT_USER` (default: `internal`)
- `Optional`: `MINIO_HOST_BIND` (default: `127.0.0.1`; set `0.0.0.0` to expose externally)
- `Optional`: `MINIO_API_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 3` inside Conductor, otherwise deterministic per-worktree value `24000 + WORKTREE_ENV_SLOT`; pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)
- `Optional`: `MINIO_CONSOLE_HOST_PORT` (default when unset: `CONDUCTOR_PORT + 4` inside Conductor, otherwise deterministic per-worktree value `28000 + WORKTREE_ENV_SLOT`; pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)

### Notes

- `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are `SharedSettings` alias properties (`minio_access_key`, `minio_secret_key`) and are not env-loaded fields.
- Use `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` as the actual env vars.

## Backend API Ingest

- `Optional`: `WEBHOOK_INGEST_HOST` (default: `0.0.0.0`)
- `Optional`: `WEBHOOK_INGEST_HOST_BIND` (default: `127.0.0.1`; Compose host bind for local exposure)
- `Optional`: `WEBHOOK_INGEST_PORT` (host-run `./scripts/dev.sh` ignores `.env` for this key and defaults to `CONDUCTOR_PORT + 5` inside Conductor, otherwise a deterministic per-worktree value near `18080 + WORKTREE_ENV_SLOT`; export it in your shell only when you intentionally want a fixed port, and avoid browser-unsafe ports such as `5060`)
- `Optional`: `WEBHOOK_INGEST_HOST_PORT` (default: `8090` when running `docker compose` directly; `./scripts/docker-compose.sh` computes `CONDUCTOR_PORT + 2` inside Conductor, otherwise a deterministic per-worktree value when unset, and pinned values must avoid browser-unsafe ports such as `5060`; see `./scripts/docker-compose.sh print-ports`)
- `Required`: `API_SHARED_SECRET` (global shared secret for protected endpoints and webhooks)

## Backend API OIDC Session Auth

- `Optional` (required when enabling OIDC login): `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`
- `Optional`: `OIDC_SCOPE` (default: `openid profile email groups`)
- `Optional`: `OIDC_GROUPS_CLAIM` (default: `groups`)
- `Optional`: `OIDC_ADMIN_GROUPS` (default: `Admin,Owner,Steering Committee`)
- `Optional`: `OIDC_CALLBACK_PATH` (default: `/auth/callback`)
- `Optional`: `OIDC_REDIRECT_BASE_URL` (default: infer from request base URL)
- Note: OIDC HTTP timeout, JWKS cache TTL, auth state TTL, and auth session TTL are fixed in code.

## Authentication + Dashboard

- `Optional`: `AUTH_SESSION_COOKIE_NAME` (default: `five08_session`)
- `Optional`: `DASHBOARD_DEFAULT_PATH` (default: `/dashboard`)
- `Optional`: `DASHBOARD_PUBLIC_BASE_URL` (base URL for generated deep links)
- Note: auth cookies always use `SameSite=Lax`; `secure` is enabled automatically outside local/dev/test environments.

## Discord Admin Deep-Link Validation

- `Optional`: `DISCORD_SERVER_ID` (required for Discord API fallback role checks)
- `Optional`: `DISCORD_ADMIN_ROLES` (default: `Admin,Owner,Steering Committee`)
- `Optional`: `DISCORD_API_TIMEOUT_SECONDS` (default: `8.0`)
- `Optional`: `DISCORD_LINK_TTL_SECONDS` (default: `600`)
- `Optional`: `DISCORD_BOT_TOKEN` (needed only for fallback Discord API checks; DB role check remains primary)
- `Workflows Engineer` is not an admin role. It receives Steering Committee
  write permissions plus jobs/audit read and dry-run access for admin-only
  rerun/sync dashboard writes.

## Worker Consumer

- `Optional`: `WORKER_NAME` (default: `worker`)
- `Required`: `WORKER_QUEUE_NAMES` (default: `jobs.default`)
- `Required` (single queue): only one queue value is currently supported. Configure one name only, without commas, to keep worker actor registration and consumer consumption aligned.
- `Optional`: `WORKER_BURST` (default: `false`)

## Worker CRM Sync + Skills Extraction

- `Optional`: `CRM_SYNC_ENABLED` (default: `true`; scheduler starts only when `ESPO_BASE_URL` and `ESPO_API_KEY` are configured)
- `Required for CRM-backed jobs and sync`: `ESPO_BASE_URL`, `ESPO_API_KEY`
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
- `Optional`: `INTAKE_RESUME_REQUIRE_VIRUS_SCAN` (default: `false` locally; resume parsing requires scanning automatically outside local/dev/test)
- `Required for non-local resume parsing`: `INTAKE_RESUME_VIRUS_SCAN_COMMAND` (Compose default: `clamdscan --stream --no-summary --config-file=/etc/clamav/clamdscan.conf {path}` against the ClamAV sidecar)
- `Optional`: `INTAKE_RESUME_VIRUS_SCAN_TIMEOUT_SECONDS` (default: `30.0`; timeout for the configured scan command)
- `Optional`: `EMAIL_RESUME_INTAKE_ENABLED` (default: `false`; enables worker-side mailbox resume processing loop)
- `Optional`: `EMAIL_RESUME_ALLOWED_EXTENSIONS` (default: `pdf,doc,docx`)
- `Optional`: `EMAIL_RESUME_MAX_FILE_SIZE_MB` (default: `10`)
- `Optional`: `EMAIL_REQUIRE_SENDER_AUTH_HEADERS` (default: `true`; requires SPF/DKIM/DMARC pass headers)
- `Required when EMAIL_RESUME_INTAKE_ENABLED=true`: `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `IMAP_SERVER`
- Note: resume intake writes LinkedIn URLs to `cLinkedIn`, leaves the intake-completed field unset, and matches resume filenames using `resume,cv,curriculum`.

## Onboarding Email Sending

- `Optional`: `ONBOARDING_EMAIL_SMTP_SERVER` (dashboard-configurable; falls back to `SMTP_SERVER`; for Migadu use `smtp.migadu.com`)
- `Optional`: `ONBOARDING_EMAIL_SMTP_PORT` (dashboard-configurable; falls back to `SMTP_PORT`; default: `465`)
- `Optional`: `ONBOARDING_EMAIL_SMTP_USE_SSL` (dashboard-configurable; falls back to `SMTP_USE_SSL`; default: `true`)
- `Optional`: `ONBOARDING_EMAIL_SMTP_STARTTLS` (dashboard-configurable; falls back to `SMTP_STARTTLS`; default: `false`; use only when SSL is disabled)
- `Optional`: `ONBOARDING_EMAIL_SMTP_USERNAME` (dashboard-configurable; falls back to `SMTP_USERNAME`)
- `Optional`: `ONBOARDING_EMAIL_SMTP_PASSWORD` (dashboard-configurable; falls back to `SMTP_PASSWORD`)
- `Optional`: `ONBOARDING_EMAIL_SENDER_EMAIL` (dashboard-configurable; default: `onboarding@508.dev`)
- `Optional`: `ONBOARDING_EMAIL_SMTP_TIMEOUT_SECONDS` (dashboard-configurable; falls back to `SMTP_TIMEOUT_SECONDS`; default: `20.0`)
- Note: `/onboarding-email` is limited to Steering Committee+ or the candidate's designated CRM onboarder. The command always creates an editable draft first; when recipient and Reply-To are resolved, the draft includes a `Send Email` button. Sent emails use the configured sender address with the command sender's name as the display name, set `Reply-To` to the command user's CRM-linked email or the explicit `reply_to_email` option, and CC the sender's 508.dev email when it can be resolved.

## Discord Bot Core

- `Optional`: `BACKEND_API_BASE_URL` (default: `http://127.0.0.1:8090`; `./scripts/dev.sh` overrides it to the worktree web/API port, Compose injects `http://web:8090`)
- `Optional`: `HEALTHCHECK_PORT` (host-run `./scripts/dev.sh` ignores `.env` for this key and defaults to `CONDUCTOR_PORT + 6` inside Conductor, otherwise a deterministic per-worktree value near `30000 + WORKTREE_ENV_SLOT`; export it in your shell only when you intentionally want a fixed port, and avoid browser-unsafe ports such as `5060`)
- `Optional`: `DISCORD_DEFAULT_JOB_FORUM_CHANNELS` (default: `gigs:part_time,fulltime-roles:full_time`; comma-separated `forum-name:posting_type` list auto-registered and backfilled on bot startup)
- `Optional`: `DISCORD_UNQUALIFIED_LEADS_FORUM_CHANNEL` (default: `unqualified-leads`; exact holding-forum name or Discord channel ID for optionally screening sourced leads; it must not be registered for job matching)
- Note: bot message chunking follows Discord's 2000 character limit in code.

## Discord Agent Gateway

- `Optional`: `AGENT_API_TIMEOUT_SECONDS` (default: `8.0`; timeout for synchronous Discord agent gateway calls)
- `Optional`: `AGENT_FAST_MODEL`, `AGENT_FAST_BASE_URL`, `AGENT_FAST_API_KEY`
- `Optional`: `AGENT_STRONG_MODEL`, `AGENT_STRONG_BASE_URL`, `AGENT_STRONG_API_KEY`
- `Optional`: `AGENT_REASONING_MODEL`, `AGENT_REASONING_BASE_URL`, `AGENT_REASONING_API_KEY`
- `Optional`: `AGENT_PLANNER_MODEL` (default: `accounts/fireworks/models/kimi-k2p6`)
- `Optional`: `AGENT_FALLBACK_MODEL` (default: `gpt-4.1-mini`; uses `OPENAI_API_KEY` / `OPENAI_BASE_URL`)
- `Optional`: `AGENT_STRUCTURED_PLANNER_ENABLED` (default: `true`; enables the production proposal-only structured planner when a model provider is configured)
- `Optional`: `AGENT_INTENT_NORMALIZER_ENABLED` (default: `true`; enables the legacy rewrite fallback for unsupported phrasing)
- `Optional`: `AGENT_INTENT_NORMALIZER_TIMEOUT_SECONDS` (default: `3.0`; timeout for legacy normalization calls)
- `Optional`: `AGENT_STRUCTURED_PLANNER_TIMEOUT_SECONDS` (default: `6.0`; timeout for the production structured planner; keep it below `AGENT_API_TIMEOUT_SECONDS` so deterministic fallback can respond)
- Note: tier-specific agent models can point at OpenAI-compatible providers such as Bifrost or Fireworks. Agent model base URLs must be HTTPS endpoints on `bifrost.508.dev`, `api.openai.com`, `api.fireworks.ai`, or `openrouter.ai`, except the internal Docker-network Bifrost URL `http://bifrost:8080/openai` is also allowed for same-host deployments. If `OPENAI_BASE_URL` points at Bifrost and tier-specific `AGENT_*` values are unset, the planner defaults to Fireworks Kimi via Bifrost as `fireworks/accounts/fireworks/models/kimi-k2p6`. Explicit Bifrost provider-prefixed planner models, such as `openrouter/openai/gpt-4.1-mini`, are passed through unchanged. If Bifrost is not configured and `FIREWORKS_API_KEY` is set, the planner falls back to direct Fireworks as `accounts/fireworks/models/kimi-k2p6`. If a configured provider is missing its usable API key, it is skipped and the fallback order is `reasoning -> strong -> fast -> AGENT_FALLBACK_MODEL -> gpt-4.1-mini`; `strong` falls back through `fast`, and `fast` falls back through the OpenAI fallback.
- Agent tools follow a proposal-and-policy path: when configured, the structured planner drafts a bounded typed tool plan using the selected fast or strong tier; every drafted action is allowlisted and shape-validated before deterministic policy authorizes it. Write tools require confirmation and the backend executes only known-good tool code. Provider failures fall back to deterministic parsing, then legacy normalization for unsupported phrasing.
- `Optional`: `GITHUB_API_TOKEN`, `GITHUB_DEFAULT_REPO`, `GITHUB_ALLOWED_REPOS` (comma-separated; GitHub Issues are the canonical code-task backend for agent-created code work, and agent tools only access the default/allowed repositories).
- Existing integration tools also expose CRM contact search/update, DocuSeal member-agreement submission, and Migadu mailbox creation when their normal service env vars are configured.
- Note: the current generic task tool registry is process-local and non-durable for non-code/org tasks until the task-management platform is selected.

## Migadu Mailbox Automation

- `Required for /create-mailbox and /create-user-accounts`: `MIGADU_API_USER`, `MIGADU_API_KEY`
- `Optional`: `MIGADU_MAILBOX_DOMAIN` (default: `508.dev`)
- Newsletter sync settings are normally set from the admin dashboard configuration page. A non-empty env or `.env` value locks the matching dashboard field.
- `Optional for Brevo newsletter sync`: `BREVO_API_KEY`
- `Optional`: `BREVO_API_BASE_URL` (default: `https://api.brevo.com/v3`)
- `Optional`: `BREVO_API_TIMEOUT_SECONDS` (default: `20.0`)
- `Optional for Brevo newsletter sync`: `BREVO_508_MEMBERS_NEWSLETTER_LIST_ID` (explicit Brevo list ID override; use `4` for the 508 members list when setting it directly)
- `Optional`: `BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME` (default: `508 members`; used to look up the list ID when the explicit ID is unset)
- `Optional for Keila contact sync`: `KEILA_API_KEY`
- `Optional`: `KEILA_API_BASE_URL` (default: `https://app.keila.io`)
- `Optional`: `KEILA_API_TIMEOUT_SECONDS` (default: `20.0`)
- `Optional`: `NEWSLETTER_SYNC_ENABLED` (default: `false`)
- `Optional`: `NEWSLETTER_SYNC_INTERVAL_SECONDS` (default: `604800`, one week)
- `Optional`: `NEWSLETTER_SYNC_EXCLUDED_MAILBOXES` (comma-separated mailbox local-parts or full addresses to skip during Migadu resync)
- Note: mailbox and backup email subscription to configured newsletter tools is best effort. Failures are reported as warnings and do not block mailbox or account creation.
- Note: the periodic sync uses Migadu mailboxes and password recovery emails as the source of truth for `@508.dev`. When CRM is configured, it only syncs mailboxes that match a CRM contact; it also skips configured excluded mailboxes and does not re-add provider-suppressed contacts.

## Authentik SSO Provisioning

- `Required for /create-sso-user and /create-user-accounts`: `AUTHENTIK_API_BASE_URL`, `AUTHENTIK_API_TOKEN`
- `Optional`: `AUTHENTIK_API_TIMEOUT_SECONDS` (default: `20.0`)
- `Optional`: `AUTHENTIK_RECOVERY_EMAIL_STAGE_ID` (when unset, the bot resolves by name)
- `Optional`: `AUTHENTIK_RECOVERY_EMAIL_STAGE_NAME` (default: `default-recovery-email`)

## Privileged Outline Integration

- `Required for /create-user-accounts and /invite-outline-user`:
  `OUTLINE_ADMIN_API_KEY`
- Note: `/create-user-accounts` also requires the Migadu and Authentik settings above.
- `Optional`: `OUTLINE_BASE_URL` (default: `https://app.getoutline.com`; root and `/api` URLs are both accepted)
- `Optional`: `OUTLINE_API_TIMEOUT_SECONDS` (default: `20.0`)
- `OUTLINE_API_KEY` remains a compatibility fallback. A non-empty
  `OUTLINE_ADMIN_API_KEY` wins; a blank new value continues to fall back to the
  old value during migration. Add the new variable in the deployment dashboard,
  redeploy, and then remove the old variable.
- The in-app Configuration dashboard stores encrypted database overrides; it
  does not edit deployment environment variables. It displays
  `OUTLINE_ADMIN_API_KEY`, reads an existing `OUTLINE_API_KEY` dashboard value
  as a fallback, and removes that old stored value when the new setting is saved.
  Any non-empty environment value under either name locks the dashboard setting.

## Member-safe Outline Content

- `Required when using /wiki or project wiki matching`:
  `OUTLINE_CONTENTS_API_KEY`
- `Required for /wiki`: `DISCORD_SERVER_ID`. The command refuses DMs and other
  guilds so the dedicated Outline credential is never used outside the co-op
  server.
- Do not reuse `OUTLINE_ADMIN_API_KEY`: it can invite users and may access private
  collections. Create the contents key for a dedicated regular Outline account
  with access only to collections that every Discord `Member` may search.
- Scope the key to `documents.search`, `documents.info`, and `stars.list`.
  `/wiki query:...` searches published documents only; `/wiki` without a query
  shows the dedicated account's starred documents as quick links. The same key
  reads the member-visible project-matching document for the dashboard.
- Add only the exact document-write scopes needed when a write feature is
  introduced; do not grant `documents.*` preemptively.
- The in-app Configuration dashboard stores this encrypted key under
  `OUTLINE_CONTENTS_API_KEY`; a non-empty deployment environment value locks
  the dashboard setting.
- The command always replies ephemerally. It does not log search queries or
  document snippets.

## Discord CRM Audit Logging (Best Effort)

- `Optional`: `AUDIT_API_BASE_URL` (defaults to `BACKEND_API_BASE_URL`; Compose clears stale `.env` values so the fallback uses the injected backend URL)
- `Optional`: `AUDIT_API_TIMEOUT_SECONDS` (default: `2.0`)
- `Optional`: `DISCORD_LOGS_WEBHOOK_URL` (if set, command and job events are posted to this Discord webhook)
- `Optional`: `DISCORD_LOGS_WEBHOOK_WAIT` (default: `true`; appends `wait=true` unless already present in the webhook URL)
