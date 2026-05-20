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
if [ -z "${API_SHARED_SECRET-}" ]; then
  API_SHARED_SECRET=$(worktree_env_resolve_value API_SHARED_SECRET "" "$WORKTREE_ENV_FILE")
  export API_SHARED_SECRET
fi
if [ -z "${DISCORD_ADMIN_ROLES-}" ]; then
  DISCORD_ADMIN_ROLES=$(worktree_env_resolve_value DISCORD_ADMIN_ROLES "Admin,Owner" "$WORKTREE_ENV_FILE")
  export DISCORD_ADMIN_ROLES
fi
if [ -z "${AUDIT_API_BASE_URL-}" ]; then
  export AUDIT_API_BASE_URL="$BACKEND_API_BASE_URL"
fi

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

run_migrations() {
  uv run --package worker python3 -c 'from five08.worker.db_migrations import run_job_migrations; run_job_migrations()'
}

create_dashboard_login_link() {
  next_path=${1:-/dashboard}
  if [ -z "${API_SHARED_SECRET-}" ]; then
    cat >&2 <<EOF
API_SHARED_SECRET is required to create a dashboard login link.
Set it in .env or export it in your shell, then make sure the API is running:
  ./scripts/dev.sh no-bot
EOF
    return 1
  fi

  python3 - "$BACKEND_API_BASE_URL" "$API_SHARED_SECRET" "$next_path" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

base_url, api_secret, next_path = sys.argv[1:4]
roles = [
    role.strip()
    for role in os.environ.get(
        "DEV_DASHBOARD_ROLES",
        os.environ.get("DISCORD_ADMIN_ROLES", "Admin,Owner"),
    ).split(",")
    if role.strip()
]
payload = {
    "discord_user_id": os.environ.get(
        "DEV_DASHBOARD_DISCORD_USER_ID", "dev-dashboard-user"
    ),
    "discord_display_name": os.environ.get(
        "DEV_DASHBOARD_DISPLAY_NAME", "Local Developer"
    ),
    "discord_roles": roles,
    "next_path": next_path,
}
request = urllib.request.Request(
    f"{base_url.rstrip('/')}/auth/discord/links",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-API-Secret": api_secret,
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=10) as response:
        raw_body = response.read().decode("utf-8")
        status = response.status
except urllib.error.HTTPError as exc:
    raw_body = exc.read().decode("utf-8", errors="replace")
    print(
        f"Dashboard login link request failed: HTTP {exc.code} {raw_body}",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc
except urllib.error.URLError as exc:
    print(
        "Dashboard API is not reachable. Start it first with "
        "./scripts/dev.sh no-bot, then rerun ./scripts/dev.sh login.",
        file=sys.stderr,
    )
    print(f"URL error: {exc.reason}", file=sys.stderr)
    raise SystemExit(1) from exc

try:
    body = json.loads(raw_body)
except json.JSONDecodeError as exc:
    print(f"Dashboard login link response was not JSON: {raw_body}", file=sys.stderr)
    raise SystemExit(1) from exc

if status != 201 or not isinstance(body, dict) or not body.get("link_url"):
    print(
        f"Dashboard login link request failed: HTTP {status} {raw_body}",
        file=sys.stderr,
    )
    raise SystemExit(1)

print("Created local dashboard login link:")
print(body["link_url"])
expires = body.get("expires_in_seconds")
if isinstance(expires, int):
    print(f"Expires in {expires} seconds.")
print()
print("Open the link in your browser to create the dashboard session.")
PY
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
  ./scripts/dev.sh no-bot  # web dashboard + worker, no Discord bot
  ./scripts/dev.sh web-worker  # alias for no-bot
  ./scripts/dev.sh login   # create a local/dev dashboard login link
  ./scripts/dev.sh all
EOF
    ;;
  all)
    start_infra
    run_migrations
    exec python3 "$script_dir/dev_mux.py"
    ;;
  no-bot|web-worker|dashboard)
    start_infra
    run_migrations
    exec python3 "$script_dir/dev_mux.py" web worker
    ;;
  login|dashboard-login)
    shift
    create_dashboard_login_link "${1:-/dashboard}"
    ;;
  migrate|migrations)
    start_infra
    run_migrations
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
    emit_export AUDIT_API_BASE_URL "$AUDIT_API_BASE_URL"
    emit_export WORKER_API_BASE_URL "$WORKER_API_BASE_URL"
    emit_export DISCORD_BOT_INTERNAL_BASE_URL "$DISCORD_BOT_INTERNAL_BASE_URL"
    printf 'export POSTGRES_URL="$('%s' print-postgres-url)"\n' "$(shell_quote "$script_dir/dev.sh")"
    ;;
  print-postgres-url)
    printf '%s\n' "$POSTGRES_URL"
    ;;
  web|api)
    run_migrations
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
    run_migrations
    exec uv run watchfiles \
      --filter python \
      --sigint-timeout 5 \
      --sigkill-timeout 10 \
      'uv run --package worker worker-consumer' \
      apps/worker/src \
      packages/shared/src
    ;;
  discord-bot|bot)
    run_migrations
    exec uv run --package discord_bot discord-bot
    ;;
  *)
    echo "Usage: ./scripts/dev.sh [infra|all|no-bot|web-worker|dashboard|login|migrate|down|ports|env|web|api|worker|discord-bot]" >&2
    exit 1
    ;;
esac
