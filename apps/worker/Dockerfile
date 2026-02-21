# Pinned from ghcr.io/astral-sh/uv:python3.12-bookworm-slim (verified 2026-02-21).
FROM ghcr.io/astral-sh/uv@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY apps/discord_bot/pyproject.toml apps/discord_bot/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY apps/worker/src apps/worker/src
COPY packages/shared/src packages/shared/src

RUN uv sync --frozen --no-dev --package integrations-worker
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /usr/sbin/nologin appuser
RUN chown -R appuser:appgroup /app

USER appuser

CMD ["uv", "run", "--package", "integrations-worker", "backend-api"]
