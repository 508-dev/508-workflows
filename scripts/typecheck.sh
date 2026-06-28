#!/usr/bin/env sh
set -eu

./scripts/pyrefly.sh

echo
echo "Running admin dashboard typecheck..."
(
  cd apps/admin_dashboard
  bun run typecheck
)
