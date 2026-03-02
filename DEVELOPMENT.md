# Development Guide

## Environment setup

```bash
brew install uv  # or use your package manager
uv --version
uv sync
cp .env.example .env
# edit .env as needed
```

## First-time setup

1. Install dependencies with `uv sync`.
2. Copy and populate environment:
   - `cp .env.example .env`
   - set required values from `ENVIRONMENT.md` (`ESPO_BASE_URL`, `ESPO_API_KEY`, `API_SHARED_SECRET`, `MINIO_ROOT_PASSWORD` for non-local, `DISCORD_BOT_TOKEN`, `CHANNEL_ID`).
3. Start supporting services:
   - `docker compose up --build`
4. Run one app at a time for local iteration:
   - `uv run --package discord-bot-app discord-bot`
   - `uv run --package integrations-worker backend-api`
   - `uv run --package integrations-worker worker-consumer`

## Local runtime commands

```bash
uv run --package discord-bot-app discord-bot
uv run --package integrations-worker backend-api
uv run --package integrations-worker worker-consumer
docker compose up --build
```

## Validation commands

```bash
./scripts/test.sh
./scripts/lint.sh
./scripts/format.sh
./scripts/mypy.sh
```

## Reference docs

- Environment settings: `ENVIRONMENT.md`
- Core project docs and usage: `README.md`
- Discord bot feature docs: `DISCORD_BOT.md`
