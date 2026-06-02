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
if [ -n "$(git status --porcelain -- apps/api/src/five08/backend/static/dashboard)" ]; then
  echo
  echo "Dashboard build output is stale. Run apps/admin_dashboard build and commit the generated static assets."
  git status --short -- apps/api/src/five08/backend/static/dashboard
  exit 1
fi
echo

echo "All checks passed."
