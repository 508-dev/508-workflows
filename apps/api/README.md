# API Service

## Auth

- Non-dashboard protected ingest/job and agent endpoints require
  `API_SHARED_SECRET` to be configured on the API.
- Send the secret in header `X-API-Secret`.
- Header name is exactly `X-API-Secret` (not `X-API-Secret-Key`).
- `GET /dashboard` and `/dashboard/api/*` use the HttpOnly session cookie created by OIDC or Discord dashboard login flows. They do not accept `X-API-Secret`.
- Dashboard session cookies are expected to stay `SameSite=Lax` or stricter; dashboard mutating POSTs rely on that cookie policy for CSRF protection.
- `GET /health` and most OIDC session routes (`/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout`) do not use `X-API-Secret`.
- `POST /auth/discord/links` does use `X-API-Secret` because it is called by trusted backend/bot components.

### API auth strategy

- Non-dashboard protected routes (including agent routes and webhooks) use
  `X-API-Secret` with `API_SHARED_SECRET`.
- Agent authorization additionally requires `DISCORD_SERVER_ID` and matching
  per-bundle `AGENT_DISCORD_*_ROLE_IDS` configuration in deployed
  environments. Discord role names are ignored there; missing or unapproved
  guild/role-ID bindings fail closed before planning and at confirmation.
- Billing/ERP agent reads require `AGENT_ERP_ORGANIZATION_ID` to exactly match
  the requesting Discord organization as well as configured ERPNext credentials;
  this intentionally supports one ERP tenant per deployment/credential boundary.
- Dashboard browser routes (`/dashboard` and `/dashboard/api/*`) are session-authenticated dashboard routes and are intentionally exempt from `X-API-Secret`.
- The dashboard UI is a Bun-built React/Tailwind/shadcn bundle committed under the API package and served by this same FastAPI app at `/dashboard` and `/dashboard/assets/*`; there is no separate web service, port, or DNS entry.

Build dashboard assets after frontend changes:

```bash
cd apps/admin_dashboard
bun install
bun run check
bun run build
```

Example:

```bash
curl -X GET "http://localhost:8090/jobs/<job_id>" \
  -H "X-API-Secret: $API_SHARED_SECRET"
```

## Backend API Endpoints

- `GET /health`: Redis/Postgres/worker health check.
- `GET /dashboard`: Session-authenticated operations dashboard. OIDC admins and Discord Steering Committee+ users get the full dashboard; active Members may use the gig-only view for gigs they originally posted. Discord users with the `Workflows Engineer` role get Steering Committee write permissions plus admin read/dry-run access.
- `GET /dashboard/api/me`: Dashboard session identity, including linked CRM contact id when available.
- `GET /dashboard/api/jobs`: Session-authenticated recent jobs list for the dashboard.
- `GET /dashboard/api/jobs/{job_id}`: Session-authenticated dashboard job detail with sensitive payload keys redacted.
- `POST /dashboard/api/jobs/{job_id}/rerun`: Session-authenticated dashboard job rerun.
- `GET /dashboard/api/agent-schedules`: Admin configuration-read list of
  retained recurring agent schedules and dispatcher state.
- `POST /dashboard/api/agent-schedules`,
  `PUT /dashboard/api/agent-schedules/{schedule_id}`, and
  `POST /dashboard/api/agent-schedules/{schedule_id}/run`: create, control,
  and manually queue a schedule from a Discord-linked Admin dashboard session.
- `GET /dashboard/api/gigs`: Session-authenticated Discord gig list with candidate/application summaries.
- `GET /dashboard/api/notifications`: Session-authenticated dashboard notifications, including stale recruiting gigs.
- `POST /dashboard/api/gigs/{engagement_id}/status`: Session-authenticated gig status update for visible pending gigs.
- `POST /dashboard/api/gigs/{engagement_id}/applications/{application_id}/status`: Session-authenticated gig candidate/application status update.
- `GET /dashboard/api/people`: Session-authenticated CRM people-cache lookup with profile/onboarding signals.
- `GET /dashboard/api/onboarding`: Session-authenticated prospect onboarding queue from the CRM people cache.
- `POST /dashboard/api/onboarding/{contact_id}/onboarder`: Session-authenticated CRM onboarder assignment for one prospect.
- `GET /dashboard/api/audit-events`: Session-authenticated recent human audit events.
- `GET /dashboard/api/discord-diagnostics`: Admin-only read-only snapshot of the
  configured Discord server's roles and agent role-ID binding health. The API
  proxies the bot's authenticated internal endpoint; it never changes Discord
  roles or deployment configuration.
- `POST /dashboard/api/sync/people`: Session-authenticated dashboard people-cache sync.
- `GET /jobs/{job_id}`: Fetch queued job status/result payload.
- `POST /jobs/{job_id}/rerun`: Enqueue a duplicate rerun of an existing job id.
- `POST /jobs/resume-extract`: Enqueue resume profile extraction.
- `POST /jobs/resume-apply`: Enqueue confirmed CRM field apply.
- `POST /webhooks/{source}`: Generic webhook enqueue endpoint.
- `POST /webhooks/espocrm`: EspoCRM webhook endpoint (expects array payload).
- `POST /webhooks/espocrm/people-sync`: EspoCRM contact-change webhook for people cache sync.
- `POST /webhooks/docuseal`: Docuseal agreement webhook endpoint.
- `POST /process-contact/{contact_id}`: Manually enqueue one contact skills job.
- `POST /sync/people`: Manually enqueue a full CRM->people cache sync.
- `POST /audit/events`: Persist one human audit event (`discord` or `admin_dashboard`).
- `GET /auth/login`: Start OIDC Auth Code + PKCE login flow.
- `GET /auth/callback`: Complete OIDC callback and set HttpOnly session cookie.
- `GET /auth/me`: Return active session identity.
- `POST /auth/logout`: Clear active session cookie + server session.
- `POST /auth/discord/links`: Create one-time dashboard deep link from Discord command context.
- `GET /auth/discord/link/{token}`: Show a no-store confirmation page for a Discord dashboard deep link without consuming the token.
- `POST /auth/discord/link/{token}/consume`: Consume the one-time Discord dashboard deep link and redirect to the authenticated dashboard session.
- Auth flows emit best-effort human audit events (`auth.login`, `auth.logout`) under source `admin_dashboard`.

### Recurring agent schedules

Recurring schedules are durable worker jobs, not long-lived Discord requests.
New schedules retain a natural-language objective plus an explicit, fixed
catalog of schedule-safe read-only tool IDs. The run-time planner can make a
short bounded loop over that catalog (GitHub, CRM, Billing/ERP, onboarding, or
public web), but cannot select a write, add a capability, or change delivery.
The older frozen GitHub envelope remains supported for `/schedule-github-issues`.

Schedule management requires `agent:schedule:manage`, which is Admin-only. On
every create, control, manual run, and background execution, the API retrieves
a fresh Discord member-role snapshot from the bot. A run requires the owner to
still hold the manager scope and every saved read scope; a role revocation
therefore skips it before a tool call or Discord post. CRM, ERP, billing, and
onboarding output is reduced to bounded aggregate observations before a
follow-up model step and before generic Discord delivery. Legacy public-data
GitHub summaries remain opt-in.

The internal bot-facing management routes live under `/agent/schedules*`; the
worker calls `POST /internal/agent-schedules/runs/{run_id}` using the existing
`API_SHARED_SECRET`. Neither route is a browser-facing authorization surface.

Discord deep-link identity policy:

- Discord deep links are available to active CRM-linked Discord users. Members receive gig-only permissions for their own posted gigs; Steering Committee+ users receive broader dashboard permissions. `Workflows Engineer` users receive Steering Committee write permissions, admin read permissions for jobs/audit, and dry-run access for admin-only dashboard writes such as job reruns and people/project syncs.
- `DISCORD_ADMIN_ROLES` controls which Discord roles can receive admin dashboard permissions (`Admin,Owner` recommended).
- `OIDC_ADMIN_GROUPS` controls normal OIDC dashboard admin membership (`authentik Admins` recommended).
- `AUTH_SESSION_TTL_SECONDS` controls dashboard session lifetime after login (`86400`, one day, by default).
- `DASHBOARD_PUBLIC_BASE_URL` should be set to the public dashboard origin in production, for example `https://workflows.508.dev`, so Discord-created links use the browser-accessible host and dashboard write CSRF checks can accept the public origin when the app is behind a proxy/tunnel.
- `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=true` (default): Discord deep links also require OIDC email identity checks against the linked CRM/Discord dashboard user.
- `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=false`: Discord deep links create a Discord-backed session directly after re-validating active CRM membership + Discord Steering Committee+ role, without forcing an OIDC roundtrip.
- In local/dev/test only, the trusted Discord bot role context can create and consume a dashboard link when the local `people` cache has no matching CRM-linked row. Production still requires the normal CRM/people identity.
- Jobs, reruns, people sync, project sync, and audit are sensitive admin permissions and require an SSO-validated dashboard session even when the user entered through a Discord link. Local/dev/test environments allow these permissions for development. `Workflows Engineer` is the exception for Discord-backed sessions: it can read jobs/audit and receive dry-run responses for rerun/sync writes without receiving real admin write permissions.

### Known handler wiring expectation

- `/jobs/{job_id}/rerun` replays the source job’s stored call arguments; rerunnable job types must only include callables that are also registered for worker execution.

## Jobs

### `GET /jobs/{job_id}`

Returns persisted job status and the latest result payload.

- Path params:
  - `job_id` (string): persisted job id.

### `POST /jobs/{job_id}/rerun`

Creates and enqueues a new duplicate job from the source job's original `args`/`kwargs`.

- The source job is not mutated.
- A new job row is persisted with a new `job_id`.
- Rerun idempotency key format: `manual-rerun:{source_job_id}:{ULID}`.

Example:

```bash
curl -X POST "http://localhost:8090/jobs/<job_id>/rerun" \
  -H "X-API-Secret: $API_SHARED_SECRET"
```

Example success response (`202`):

```json
{
  "status": "queued",
  "source_job_id": "job-old-1",
  "job_id": "job-new-1",
  "type": "process_docuseal_agreement_job",
  "created": true
}
```

### `POST /jobs/resume-extract`

Enqueues one resume extraction job.

- JSON body:
  - `contact_id` (string, required)
  - `attachment_id` (string, required)
  - `filename` (string, required)

Example:

```bash
curl -X POST "http://localhost:8090/jobs/resume-extract" \
  -H "X-API-Secret: $API_SHARED_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "contact_id": "contact-123",
    "attachment_id": "att-456",
    "filename": "resume.pdf"
  }'
```

### `POST /jobs/resume-apply`

Enqueues one CRM apply job after resume update confirmation.

- JSON body:
  - `contact_id` (string, required)
  - `updates` (object[string->string], required): CRM field updates.
  - `link_discord` (object, optional): `{ "user_id": "...", "username": "..." }`

### `POST /process-contact/{contact_id}`

Manually enqueues one contact skills job.

- Path params:
  - `contact_id` (string, required)

### `POST /sync/people`

Manually enqueues a full CRM -> people cache sync.

## Webhooks

### `POST /webhooks/docuseal`

Enqueues DocuSeal agreement-signing jobs.

- Job input contract for queueing: `completed_at` is a UTC string using `YYYY-MM-DD HH:mm:ss`.
- Example value: `2026-03-02 10:02:30`.
- Required payload fields:
  - `event_type` must be `form.completed`
  - `data.email` non-empty signer email
  - `data.completed_at` or top-level `timestamp` (ISO timestamp string)
  - `data.template.id` must match configured `DOCUSEAL_MEMBER_AGREEMENT_TEMPLATE_ID`

### `POST /webhooks/{source}`

Generic webhook enqueue endpoint.

- Path params:
  - `source` (string, required): source label written into job payload.
- JSON body:
  - Any JSON object payload.

### `POST /webhooks/espocrm`

EspoCRM webhook endpoint (expects array payload).

- JSON body:
  - Array of event objects, each with at least `id` (string).

### `POST /webhooks/espocrm/people-sync`

EspoCRM contact-change webhook for people cache sync.

- JSON body:
  - Array of event objects, each with at least `id` (string).
