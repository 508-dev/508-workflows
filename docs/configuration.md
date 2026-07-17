# Configuration Reference

Use `.env.example` as the source of truth for defaults and available settings.
This document groups the settings by operational area and calls out the values
that most often matter in local development and deployment.

## Core Runtime

- `ENVIRONMENT`: defaults to `local`. Non-local deployments should provide
  explicit runtime secrets such as `POSTGRES_URL` and `MINIO_ROOT_PASSWORD`.
  Missing runtime dependencies are reported through health/runtime failures
  where possible instead of eager settings-construction errors.
- `LOG_LEVEL`: defaults to `INFO`.
- `API_SHARED_SECRET`: required for protected non-dashboard API routes and for
  `./scripts/dev.sh login`.
- `CONFIG_SECRET_KEY`: environment or `.env` key used to encrypt secret values
  saved from the admin dashboard configuration page. This key is not
  dashboard-managed, and dashboard-managed secret saves are rejected when it is
  unset.
- `WEBHOOK_SHARED_SECRET`: optional separate secret for external `/webhooks/*`
  callers such as DocuSeal, Google Forms, and EspoCRM. When unset, webhook
  routes fall back to `API_SHARED_SECRET` for compatibility.

## Admin Dashboard Configuration

The admin dashboard can store a small allowlist of movable integration
credentials and business knobs in Postgres. Non-empty env or `.env` values
always win and appear as environment-locked in the dashboard. If no env value is
present, the database value overrides the code default.

Secret values saved from the dashboard are encrypted before storage using
`CONFIG_SECRET_KEY`. API responses never include full secret values. Database
secrets return configured state and a short first/last-character mask for
confirmation; env and `.env` secrets return only configured state.

Core bootstrap systems such as EspoCRM, Authentik, and Migadu remain env-managed
and are intentionally not dashboard-configurable. Onboarding email SMTP settings
are dashboard-configurable under the Onboarding category when env overrides are
not set.

Onboarding Tally intake settings are dashboard-configurable under Intake. If
`ONBOARDING_TALLY_API_KEY` or `ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET` is set
in `.env` or the process environment, that env value wins and the dashboard
shows the field as environment-locked. Legacy `TALLY_*` env aliases still work
for compatibility.

Newsletter sync settings are normally set from the admin dashboard
configuration page. A non-empty env or `.env` value locks the matching
dashboard field.

## Tally Intake

- `ONBOARDING_TALLY_API_KEY`: optional Tally API key for future onboarding-form
  backfills. The webhook intake path does not require it.
- `ONBOARDING_TALLY_WEBHOOK_SIGNING_SECRET`: secret used to verify
  `Tally-Signature` on `/webhooks/tally/onboarding`. When unset, the route falls
  back to `WEBHOOK_SHARED_SECRET` / `API_SHARED_SECRET` using the existing
  `X-API-Secret` header path.
- `ONBOARDING_TALLY_ALLOWED_FORM_IDS`: comma-separated allowlist of accepted
  onboarding Tally form IDs. Tally intake fails closed when this is unset.
- `POST /webhooks/tally/onboarding?dry_run=true`: validates auth/signature,
  checks the form allowlist, and returns the normalized intake payload plus the
  job metadata it would enqueue without queueing work, writing to the CRM,
  writing to the DB, or fetching uploaded resumes.
- `POST /webhooks/tally/onboarding?dry_run=worker`: validates and maps the
  webhook, then enqueues an intake worker job with `dry_run=true`. The worker
  performs CRM lookup and builds planned CRM updates, but skips CRM writes and
  intake DB persistence.
- `INTAKE_RESUME_REQUIRE_VIRUS_SCAN`: when true, downloaded resume files are not
  parsed unless the malware scan command succeeds. Defaults to false locally;
  non-local runtimes require scanning for resume parsing even when this is unset.
- `INTAKE_RESUME_VIRUS_SCAN_COMMAND`: command used to scan downloaded resumes.
  Required when `INTAKE_RESUME_REQUIRE_VIRUS_SCAN=true` or when running outside
  local/dev/test. Include `{path}` where the temporary resume filepath should be
  inserted. When `{path}` is omitted, the filepath is appended as the final
  argument. Production Compose uses the ClamAV sidecar command
  `clamdscan --stream --no-summary --config-file=/etc/clamav/clamdscan.conf {path}`.
- `INTAKE_RESUME_VIRUS_SCAN_TIMEOUT_SECONDS`: scan command timeout.

## ERPNext Project Payments

The payment automation consumes ERPNext as the accounting source of truth. It
does not use direct Plaid credentials.

- `ERPNEXT_BANK_TRANSACTION_WEBHOOK_SIGNING_SECRET`: dedicated Frappe Webhook
  Secret for `POST /webhooks/erpnext/bank-transaction`. The endpoint accepts
  only `X-Frappe-Webhook-Signature`, a base64 HMAC-SHA256 of the raw body; it
  intentionally does not fall back to `WEBHOOK_SHARED_SECRET` or
  `API_SHARED_SECRET`.
- `PROJECT_PAYMENT_AUTOMATION_ENABLED`: defaults to `false`. When enabled, a
  matched `automatic` payment-routing rule can create a confirmed local project
  allocation and notification outbox rows. Rules remain typed and limited to
  registered payment facts/action types; they cannot run arbitrary code.
- `PROJECT_PAYMENT_NOTIFICATIONS_ENABLED`: defaults to `false`. When enabled,
  the worker asks the Discord bot to deliver pending payment notifications. The
  worker re-reads the canonical ERP project and Bank Transaction, requiring the
  allocation's captured transaction revision immediately before asking the bot
  to post. The bot
  then rechecks the private-channel mapping, channel privacy, and its
  view/send/read-history permissions immediately before posting.
- `PROJECT_PAYMENT_RECOVERY_INTERVAL_SECONDS`: defaults to `300` (minimum
  `60`). While payment automation is enabled, the API schedules this durable
  sweep to reclaim stale worker/bot leases, retry idempotent learned-suggestion
  derivation from approved feedback, and process actions or notifications
  created while a feature flag was off. Restart the API after changing either
  `PROJECT_PAYMENT_AUTOMATION_ENABLED` or the interval; notification delivery
  itself can be toggled without a scheduler restart.

These three non-secret settings are intentionally omitted from `.env.example`:
when unset, an authorized operator may set them through runtime configuration.
Set one in deployment environment only when it should be locked outside the
dashboard.

Configure a signed Frappe Webhook on **Bank Transaction** for **on_submit**,
with JSON body:

```json
{
  "doctype": "Bank Transaction",
  "event_type": "bank_transaction.posted.v1",
  "name": "{{ doc.name }}",
  "modified": "{{ doc.modified }}",
  "docstatus": {{ doc.docstatus }}
}
```

To observe corrections proactively, configure a second otherwise-identical
webhook for **on_update_after_submit**. Both webhooks use the same event type
and endpoint; the canonical `modified` revision deduplicates replayed hints and
causes a newer submitted revision to be evaluated afresh. This is in addition
to, not a replacement for, the action-time and notification-time canonical
rechecks.

The webhook is an identity hint only: the worker fetches the canonical Bank
Transaction over the ERPNext API before persisting or categorizing it. Register
a target with `/register-project-channel` from a private text channel; the
command requires Steering Committee-or-higher access and validates that the bot
can view, post, and read message history there. Use
`/unregister-project-channel` to disable delivery without deleting history.
Before every payment allocation (automatic or human-approved), the worker also
refreshes that project's ERPNext record and writes it to the local cache; a
closed or unavailable ERP project cannot be routed from stale cache state.

Use the dashboard's **Payments** view to inspect suggestions and create or
disable routing rules. The same protected API remains available to integrations
(requires `configuration:write`): `POST /dashboard/api/project-payment-rules`,
then use the same resource with `PUT` (supplying `expected_version`) or
`DELETE` (soft disable, also supplying `expected_version`). `GET` lists current
rules. The v1 surface permits one `project_payment.route` action per rule and
only the published Bank Transaction fact paths. Its `project_id` and action
payload `project_id` must match an open local ERP project.

Automatic rules are rejected unless they are project-scoped and combine a
nonblank identity match (`counterparty`, `description`, or `reference_number`),
an exact positive amount, and an exact currency. Amount-only, range, and
reconciliation-only rules are suggestions, never automatic allocations. Closed
projects are intentionally blocked from new automatic allocations or alerts;
review late payments manually.

Each suggestion approval or rejection stores a typed decision, reviewer, and
timestamp alongside immutable event/rule evidence. When an approval has an
inbound direction, a three-letter currency, and a meaningful counterparty,
reference, or description for an open local ERP project, it also creates (or
reuses) one low-priority
**learned** rule. Learned rules are deterministic, carry an opaque provenance
fingerprint, and can only create future `suggest` actions; they cannot be
edited into or used as automatic payment routing. They remain visible in the
Payments view and can be disabled there. Rejections remain durable labeled
feedback but do not silently mutate an existing rule.

Discord delivery is at-least-once with a durable bot-side receipt/lease and a
hidden marker recovery check. The bot additionally requires the worker's active
outbox lease with each request, so a notification ID alone cannot bypass its
lifecycle. It avoids normal retry duplicates but cannot make Discord's external
send and the database write one atomic transaction. A delayed notification for
a canceled or revised Bank Transaction is blocked before delivery. When a newer
canonical revision is observed, a prior automatic configured-rule allocation is
marked superseded (with an audit reason) and its pending outbox delivery becomes
ineligible; a fresh event may be evaluated against the new revision. Human-
reviewed, manual, and ERP-reconciled allocations are never superseded by this
automation. This v1 cannot retract a Discord message already accepted, and
cancellations or post-delivery corrections still require accounting
reconciliation.

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
- `GIG_CONTACTED_REMINDER_DAYS`
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
- `CRM_SYNC_ENABLED`: optional, defaults to `true`; the scheduler starts only
  when ESPO credentials are configured.
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
- `JOB_LEAD_CLASSIFIER_ENABLED`
- `JOB_LEAD_CLASSIFIER_MODEL`
- `JOB_LEAD_CLASSIFIER_TIMEOUT_SECONDS`

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
- `AGENT_STRUCTURED_PLANNER_ENABLED`
- `AGENT_STRUCTURED_PLANNER_TIMEOUT_SECONDS`
- `AGENT_INTENT_NORMALIZER_ENABLED`
- `AGENT_INTENT_NORMALIZER_TIMEOUT_SECONDS`
- `GITHUB_API_TOKEN`
- `GITHUB_DEFAULT_REPO`
- `GITHUB_ALLOWED_REPOS`

Agent model base URLs must be HTTPS endpoints on allowed provider hosts, except
the internal Docker-network Bifrost URL `http://bifrost:8080/openai` is allowed
for same-host deployments.

## Migadu Mailbox And Newsletter Sync

- `MIGADU_API_USER`, `MIGADU_API_KEY`: required for `/create-mailbox` and `/create-user-accounts`.
- `MIGADU_MAILBOX_DOMAIN`: optional, defaults to `508.dev`.
- `BREVO_API_KEY`: optional for Brevo newsletter sync.
- `BREVO_API_BASE_URL`: optional, defaults to `https://api.brevo.com/v3`.
- `BREVO_API_TIMEOUT_SECONDS`: optional, defaults to `20.0`.
- `BREVO_508_MEMBERS_NEWSLETTER_LIST_ID`: optional explicit Brevo list ID override; use `4` for the 508 members list when setting it directly.
- `BREVO_508_MEMBERS_NEWSLETTER_LIST_NAME`: optional, defaults to `508 members` and is used to look up the list ID when the explicit ID is unset.
- `KEILA_API_KEY`: optional for Keila contact sync.
- `KEILA_API_BASE_URL`: optional, defaults to `https://app.keila.io`.
- `KEILA_API_TIMEOUT_SECONDS`: optional, defaults to `20.0`.
- `NEWSLETTER_SYNC_ENABLED`: optional, defaults to `false`; dashboard changes require an API restart because the scheduler starts at startup.
- `NEWSLETTER_SYNC_INTERVAL_SECONDS`: optional, defaults to `604800`; dashboard changes require an API restart because the scheduler sleep interval is startup-bound.
- `NEWSLETTER_SYNC_EXCLUDED_MAILBOXES`: optional comma-separated mailbox local-parts or full addresses to skip during Migadu resync.

Mailbox and backup email subscription to configured newsletter tools is best
effort. Failures are reported as warnings and do not block mailbox or account
creation. The periodic sync uses Migadu mailboxes and password recovery emails
as the source of truth for `@508.dev`. When CRM is configured, it only syncs
mailboxes that match a CRM contact; it also skips configured excluded mailboxes
and does not re-add provider-suppressed contacts.
