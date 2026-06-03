#!/usr/bin/env sh
set -eu

echo "Running ruff check..."
uv run ruff check apps/api/src/five08 apps/discord_bot/src/five08 apps/worker/src/five08 packages/shared/src/five08 tests

echo
echo "Running admin dashboard lint..."
(
  cd apps/admin_dashboard
  bun run lint
)
