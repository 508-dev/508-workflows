FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY apps/discord_bot/pyproject.toml apps/discord_bot/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY apps/discord_bot/src apps/discord_bot/src
COPY packages/shared/src packages/shared/src

RUN uv sync --frozen --no-dev --package discord-bot-app

CMD ["uv", "run", "--package", "discord-bot-app", "discord-bot"]
