# API Service

## Auth

- Protected ingest/job endpoints require `API_SHARED_SECRET` to be configured on the API.
- Send the secret in header `X-API-Secret`.
- Header name is exactly `X-API-Secret` (not `X-API-Secret-Key`).
- `GET /health` and most OIDC session routes (`/auth/login`, `/auth/callback`, `/auth/me`, `/auth/logout`) do not use `X-API-Secret`.
- `POST /auth/discord/links` does use `X-API-Secret` because it is called by trusted backend/bot components.

Example:

```bash
curl -X GET "http://localhost:8090/jobs/<job_id>" \
  -H "X-API-Secret: $API_SHARED_SECRET"
```

## Backend API Endpoints

- `GET /health`: Redis/Postgres/worker health check.
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
- `GET /auth/discord/link/{token}`: Resolve Discord deep link into authenticated dashboard redirect.
- Auth flows emit best-effort human audit events (`auth.login`, `auth.logout`) under source `admin_dashboard`.

Discord deep-link identity policy:

- `DISCORD_ADMIN_ROLES` controls who can mint/use Discord deep links (`Admin,Owner` recommended).
- `OIDC_ADMIN_GROUPS` controls normal OIDC dashboard admin membership (`authentik Admins` recommended).
- `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=true` (default): Discord deep links also require OIDC admin group + OIDC email linked to Discord admin identity.
- `DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=false`: bootstrap mode; Discord deep links skip OIDC group/email-link checks after successful OIDC authentication.

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
