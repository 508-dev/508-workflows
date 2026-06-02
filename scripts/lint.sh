#!/usr/bin/env sh
set -eu

echo "Running ruff check..."
uv run ruff check apps packages tests

echo
echo "Running admin dashboard lint..."
(
  cd apps/admin_dashboard
  bun run lint
)
