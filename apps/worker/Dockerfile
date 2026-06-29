# syntax=docker/dockerfile:1.7
# Python pinned from python:3.12-slim-bookworm and uv pinned from ghcr.io/astral-sh/uv:0.11.18 (verified 2026-06-03).
FROM ghcr.io/astral-sh/uv@sha256:78bc42400d77b0678ba95765305c826652ed5431f399257271dda681d0318f03 AS uv
FROM python@sha256:93ab4b7fa528b25124c97bcc755415e60eb671a86b4dbe0328df2fe2d1c1193d
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends clamdscan \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'TCPSocket 3310\nTCPAddr clamav\n' >/etc/clamav/clamdscan.conf

COPY pyproject.toml uv.lock ./
COPY apps/discord_bot/pyproject.toml apps/discord_bot/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY apps/discord_bot/src apps/discord_bot/src
COPY apps/api/src apps/api/src
COPY apps/worker/src apps/worker/src
COPY packages/shared/src packages/shared/src

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --all-packages
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /usr/sbin/nologin appuser
RUN chown -R appuser:appgroup /app

USER appuser

CMD ["uv", "run", "--package", "worker", "worker-consumer"]
