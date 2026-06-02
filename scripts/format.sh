#!/usr/bin/env sh
set -eu

echo "Running ruff format..."
uv run ruff format apps/api/src/five08 apps/discord_bot/src/five08 apps/worker/src/five08 packages/shared/src/five08 tests

echo
echo "Running admin dashboard format..."
(
  cd apps/admin_dashboard
  bun run format
)
