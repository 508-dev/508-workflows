#!/usr/bin/env sh
set -eu

echo "Running all checks..."
echo

./scripts/lint.sh
echo

echo "Checking Python formatting..."
uv run ruff format --check apps packages tests
echo

./scripts/typecheck.sh
echo

./scripts/test.sh
echo

echo "Building admin dashboard..."
(
  cd apps/admin_dashboard
  bun run build
)
echo

echo "All checks passed."
