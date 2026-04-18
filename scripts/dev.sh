#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/worktree-env.sh"
worktree_env_load "$script_dir"

export REDIS_URL="redis://127.0.0.1:${REDIS_HOST_PORT}/0"
if [ -z "${POSTGRES_URL-}" ]; then
  export POSTGRES_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_DB}"
fi
export MINIO_ENDPOINT="http://127.0.0.1:${MINIO_API_HOST_PORT}"
export BACKEND_API_BASE_URL="http://127.0.0.1:${WEBHOOK_INGEST_PORT}"
export WORKER_API_BASE_URL="http://127.0.0.1:${WEBHOOK_INGEST_PORT}"
export DISCORD_BOT_INTERNAL_BASE_URL="http://127.0.0.1:${HEALTHCHECK_PORT}"

start_infra() {
  "$script_dir/docker-compose.sh" up -d --wait redis postgres minio minio-init
}

command=${1:-infra}
case "$command" in
  infra)
    start_infra
    cat <<EOF

Infrastructure is running in Docker on localhost:
  Redis:    127.0.0.1:${REDIS_HOST_PORT}
  Postgres: 127.0.0.1:${POSTGRES_HOST_PORT}
  MinIO:    127.0.0.1:${MINIO_API_HOST_PORT}
  Console:  127.0.0.1:${MINIO_CONSOLE_HOST_PORT}

Host-run app ports for this worktree:
  API:      127.0.0.1:${WEBHOOK_INGEST_PORT}
  Bot:      127.0.0.1:${HEALTHCHECK_PORT}

Run app services on the host with:
  ./scripts/dev.sh api
  ./scripts/dev.sh worker
  ./scripts/dev.sh discord-bot
  ./scripts/dev.sh all
EOF
    ;;
  all)
    start_infra
    exec python3 "$script_dir/dev_mux.py"
    ;;
  down)
    "$script_dir/docker-compose.sh" down
    ;;
  ports)
    cat <<EOF
REDIS_HOST_PORT=$REDIS_HOST_PORT
POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT
MINIO_API_HOST_PORT=$MINIO_API_HOST_PORT
MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT
WEBHOOK_INGEST_PORT=$WEBHOOK_INGEST_PORT
HEALTHCHECK_PORT=$HEALTHCHECK_PORT
EOF
    ;;
  env)
    cat <<EOF
export REDIS_HOST_PORT=$REDIS_HOST_PORT
export POSTGRES_HOST_PORT=$POSTGRES_HOST_PORT
export POSTGRES_USER=$POSTGRES_USER
export POSTGRES_PASSWORD=$POSTGRES_PASSWORD
export POSTGRES_DB=$POSTGRES_DB
export MINIO_API_HOST_PORT=$MINIO_API_HOST_PORT
export MINIO_CONSOLE_HOST_PORT=$MINIO_CONSOLE_HOST_PORT
export WEBHOOK_INGEST_PORT=$WEBHOOK_INGEST_PORT
export HEALTHCHECK_PORT=$HEALTHCHECK_PORT
export REDIS_URL=$REDIS_URL
export POSTGRES_URL=$POSTGRES_URL
export MINIO_ENDPOINT=$MINIO_ENDPOINT
export BACKEND_API_BASE_URL=$BACKEND_API_BASE_URL
export WORKER_API_BASE_URL=$WORKER_API_BASE_URL
export DISCORD_BOT_INTERNAL_BASE_URL=$DISCORD_BOT_INTERNAL_BASE_URL
EOF
    ;;
  api)
    exec uv run --package api backend-api
    ;;
  worker)
    exec uv run --package worker worker-consumer
    ;;
  discord-bot|bot)
    exec uv run --package discord_bot discord-bot
    ;;
  *)
    echo "Usage: ./scripts/dev.sh [infra|all|down|ports|env|api|worker|discord-bot]" >&2
    exit 1
    ;;
esac
