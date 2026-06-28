#!/usr/bin/env sh
set -eu

echo "Running all checks..."
echo

./scripts/lint.sh
echo

echo "Checking Python formatting..."
uv run ruff format --check apps/api/src/five08 apps/discord_bot/src/five08 apps/worker/src/five08 packages/shared/src/five08 tests
echo

./scripts/pyrefly.sh
echo

./scripts/test.sh
echo

echo "Building admin dashboard..."
dashboard_static_dir="apps/api/src/five08/backend/static/dashboard"
dashboard_build_dir=$(mktemp -d "${TMPDIR:-/tmp}/five08-dashboard-build.XXXXXX")
cleanup_dashboard_build_dir() {
  rm -rf "$dashboard_build_dir"
}
trap cleanup_dashboard_build_dir EXIT HUP INT TERM
(
  cd apps/admin_dashboard
  bun run build -- --outDir "$dashboard_build_dir"
)
if ! diff_output=$(diff -qr "$dashboard_static_dir" "$dashboard_build_dir"); then
  echo
  echo "Dashboard build output is stale. Run 'cd apps/admin_dashboard && bun run build' and commit the generated static assets."
  echo "$diff_output"
  exit 1
fi
if [ -n "$(git status --porcelain -- "$dashboard_static_dir")" ]; then
  echo
  echo "Dashboard build output is stale. Run 'cd apps/admin_dashboard && bun run build' and commit the generated static assets."
  git status --short -- "$dashboard_static_dir"
  exit 1
fi
echo

echo "All checks passed."
