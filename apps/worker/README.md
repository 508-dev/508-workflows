# Worker Service

## Overview

- Package: `apps/worker`
- Entrypoint: `uv run --package worker worker-consumer`
- CLI: `uv run --package worker jobsctl`

## Jobs CLI

The `jobsctl` utility can inspect and rerun jobs by id.

Defaults:

- Base URL: `http://localhost:8090` (or `$WORKER_API_BASE_URL`)
- API secret: `$API_SHARED_SECRET` (sent as `X-API-Secret`)
- Timeout: `10.0` seconds

Usage:

```bash
uv run --package worker jobsctl --help
uv run --package worker jobsctl status <job_id>
uv run --package worker jobsctl rerun <job_id>
```

If needed, pass overrides explicitly:

```bash
uv run --package worker jobsctl \
  --api-url http://localhost:8090 \
  --secret "$API_SHARED_SECRET" \
  rerun job-123
```
