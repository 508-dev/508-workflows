#!/usr/bin/env sh
set -eu

echo "Running Pyrefly..."
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
config_path="$repo_root/pyproject.toml"

if [ -x "$repo_root/.venv/bin/pyrefly" ]; then
  "$repo_root/.venv/bin/pyrefly" check --config "$config_path" "$@"
else
  uv run pyrefly check --config "$config_path" "$@"
fi
