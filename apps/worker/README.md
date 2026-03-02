# Worker Service

## Webhooks

### `POST /webhooks/docuseal`

Enqueues DocuSeal agreement-signing jobs.

- Job input contract for queueing: `completed_at` is a UTC string using `YYYY-MM-DD HH:mm:ss`.
- Example value: `2026-03-02 10:02:30`.

### `POST /webhooks/{source}`

Generic webhook enqueue endpoint.

### `POST /webhooks/espocrm`

EspoCRM webhook endpoint (expects array payload).

### `POST /webhooks/espocrm/people-sync`

EspoCRM contact-change webhook for people cache sync.
