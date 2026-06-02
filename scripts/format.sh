#!/usr/bin/env sh
set -eu

echo "Running ruff format..."
uv run ruff format apps packages tests

echo
echo "Running admin dashboard format..."
(
  cd apps/admin_dashboard
  bun run format
)
