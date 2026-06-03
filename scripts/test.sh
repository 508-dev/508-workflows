#!/usr/bin/env sh
set -eu

echo "Running tests..."
uv run pytest tests/ -v --tb=short

echo
echo "Running admin dashboard tests..."
(
  cd apps/admin_dashboard
  bun run test
)
