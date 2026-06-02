#!/usr/bin/env sh
set -eu

./scripts/mypy.sh

echo
echo "Running admin dashboard typecheck..."
(
  cd apps/admin_dashboard
  bun run typecheck
)
