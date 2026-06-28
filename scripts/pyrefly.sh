#!/usr/bin/env sh
set -eu

echo "Running Pyrefly..."
if [ -x .venv/bin/pyrefly ]; then
  .venv/bin/pyrefly check "$@"
else
  uv run pyrefly check "$@"
fi
