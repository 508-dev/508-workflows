#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/worktree-env.sh"
worktree_env_load "$script_dir" compose
repo_root=$WORKTREE_ENV_REPO_ROOT

export COMPOSE_PROJECT_NAME
export REDIS_HOST_PORT
export POSTGRES_HOST_PORT
export WEB_HOST_PORT
export WEBHOOK_INGEST_HOST_PORT
export MINIO_API_HOST_PORT
export MINIO_CONSOLE_HOST_PORT

# Host-run-only app ports must not leak into Compose interpolation, or the web
# container can start on a high worktree port while peers still target :8090.
unset WEB_PORT
unset WEBHOOK_INGEST_PORT
unset HEALTHCHECK_PORT

if [ "${1:-}" = "print-ports" ]; then
  cat <<EOF
WORKTREE=$repo_root
COMPOSE_PROJECT_NAME=$COMPOSE_PROJECT_NAME
REDIS_HOST_PORT=$REDIS_HOST_PORT
POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT
WEB_HOST_PORT=$WEB_HOST_PORT
MINIO_API_HOST_PORT=$MINIO_API_HOST_PORT
MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT
EOF
  exit 0
fi

cd "$repo_root"
exec docker compose -f compose.yaml -f compose.local.yaml "$@"
