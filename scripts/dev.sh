#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/worktree-env.sh"
worktree_env_load "$script_dir"

# dev.sh owns host-run service URLs so every launched process shares the same
# worktree-local infra and app ports.
export REDIS_URL="redis://127.0.0.1:${REDIS_HOST_PORT}/0"
export POSTGRES_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_HOST_PORT}/${POSTGRES_DB}"
export MINIO_ENDPOINT="http://127.0.0.1:${MINIO_API_HOST_PORT}"
export BACKEND_API_BASE_URL="http://127.0.0.1:${WEB_PORT}"
export WORKER_API_BASE_URL="http://127.0.0.1:${WEB_PORT}"
export DISCORD_BOT_INTERNAL_BASE_URL="http://127.0.0.1:${HEALTHCHECK_PORT}"

shell_quote() {
  python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$1"
}

emit_export() {
  key=$1
  value=$2
  printf 'export %s=%s\n' "$key" "$(shell_quote "$value")"
}

start_infra() {
  "$script_dir/docker-compose.sh" up -d --wait redis postgres minio
  "$script_dir/docker-compose.sh" up minio-init
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
  Web/API:  127.0.0.1:${WEB_PORT} (hot reload)
  Bot:      127.0.0.1:${HEALTHCHECK_PORT}

Run app services on the host with:
  ./scripts/dev.sh web  # ./scripts/dev.sh api also works
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
WEB_PORT=$WEB_PORT
HEALTHCHECK_PORT=$HEALTHCHECK_PORT
EOF
    ;;
  env)
    emit_export REDIS_HOST_PORT "$REDIS_HOST_PORT"
    emit_export POSTGRES_HOST_PORT "$POSTGRES_HOST_PORT"
    emit_export POSTGRES_USER "$POSTGRES_USER"
    emit_export POSTGRES_DB "$POSTGRES_DB"
    emit_export MINIO_API_HOST_PORT "$MINIO_API_HOST_PORT"
    emit_export MINIO_CONSOLE_HOST_PORT "$MINIO_CONSOLE_HOST_PORT"
    emit_export WEB_PORT "$WEB_PORT"
    emit_export HEALTHCHECK_PORT "$HEALTHCHECK_PORT"
    emit_export REDIS_URL "$REDIS_URL"
    emit_export MINIO_ENDPOINT "$MINIO_ENDPOINT"
    emit_export BACKEND_API_BASE_URL "$BACKEND_API_BASE_URL"
    emit_export WORKER_API_BASE_URL "$WORKER_API_BASE_URL"
    emit_export DISCORD_BOT_INTERNAL_BASE_URL "$DISCORD_BOT_INTERNAL_BASE_URL"
    printf 'export POSTGRES_URL="$('%s' print-postgres-url)"\n' "$(shell_quote "$script_dir/dev.sh")"
    ;;
  print-postgres-url)
    printf '%s\n' "$POSTGRES_URL"
    ;;
  web|api)
    exec uv run --package api uvicorn five08.backend.api:create_app \
      --factory \
      --host "${WEB_HOST:-${WEBHOOK_INGEST_HOST:-0.0.0.0}}" \
      --port "$WEB_PORT" \
      --reload \
      --reload-dir apps/api/src \
      --reload-dir apps/worker/src \
      --reload-dir packages/shared/src
    ;;
  worker)
    exec uv run watchfiles \
      --filter python \
      --sigint-timeout 5 \
      --sigkill-timeout 10 \
      'uv run --package worker worker-consumer' \
      apps/worker/src \
      packages/shared/src
    ;;
  discord-bot|bot)
    exec uv run --package discord_bot discord-bot
    ;;
  *)
    echo "Usage: ./scripts/dev.sh [infra|all|down|ports|env|web|api|worker|discord-bot]" >&2
    exit 1
    ;;
esac
