#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

hash_value=$(printf '%s' "$repo_root" | cksum | awk '{print $1}')
slot=$((hash_value % 2000))

project_name=$(basename "$repo_root" | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z' | sed 's/^-*//; s/-*$//')
if [ -z "$project_name" ]; then
  project_name="worktree"
fi

: "${COMPOSE_PROJECT_NAME:=${project_name}-$(printf '%04d' "$slot")}"
: "${POSTGRES_HOST_PORT:=$((15432 + slot))}"
: "${WEBHOOK_INGEST_HOST_PORT:=$((20080 + slot))}"
: "${MINIO_API_HOST_PORT:=$((24000 + slot))}"
: "${MINIO_CONSOLE_HOST_PORT:=$((28000 + slot))}"

export COMPOSE_PROJECT_NAME
export POSTGRES_HOST_PORT
export WEBHOOK_INGEST_HOST_PORT
export MINIO_API_HOST_PORT
export MINIO_CONSOLE_HOST_PORT

if [ "${1:-}" = "ports" ]; then
  cat <<EOF
WORKTREE=$repo_root
COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME
POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT
WEBHOOK_INGEST_HOST_PORT=$WEBHOOK_INGEST_HOST_PORT
MINIO_API_HOST_PORT=$MINIO_API_HOST_PORT
MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT
EOF
  exit 0
fi

cd "$repo_root"
exec docker compose "$@"
