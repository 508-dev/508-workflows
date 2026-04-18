#!/bin/sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd)
env_file="$repo_root/.env"

hash_value=$(printf '%s' "$repo_root" | cksum | awk '{print $1}')
slot=$((hash_value % 2000))

project_name=$(basename "$repo_root" | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z' | sed 's/^-*//; s/-*$//')
if [ -z "$project_name" ]; then
  project_name="worktree"
fi

get_env_file_value() {
  key=$1
  [ -f "$env_file" ] || return 1

  awk -F= -v key="$key" '
    function parse_value(raw,    first, quote, value, i, c, escaped) {
      sub(/^[[:space:]]*/, "", raw)

      first = substr(raw, 1, 1)
      if (first == "\"" || first == "'\''") {
        quote = first
        value = ""
        escaped = 0

        for (i = 2; i <= length(raw); i++) {
          c = substr(raw, i, 1)

          if (quote == "\"" && escaped) {
            value = value c
            escaped = 0
            continue
          }

          if (quote == "\"" && c == "\\") {
            escaped = 1
            continue
          }

          if (c == quote) {
            return value
          }

          value = value c
        }

        return value
      }

      sub(/[[:space:]]+#.*$/, "", raw)
      sub(/[[:space:]]*$/, "", raw)
      return raw
    }

    /^[[:space:]]*#/ { next }
    $0 ~ "^[[:space:]]*" key "=" {
      value = parse_value(substr($0, index($0, "=") + 1))
      print value
      found = 1
      exit
    }
    END { if (!found) exit 1 }
  ' "$env_file"
}

resolve_value() {
  key=$1
  default_value=$2
  eval "shell_value=\${$key-}"

  if [ -n "$shell_value" ]; then
    printf '%s' "$shell_value"
    return
  fi

  if file_value=$(get_env_file_value "$key" 2>/dev/null); then
    printf '%s' "$file_value"
    return
  fi

  printf '%s' "$default_value"
}

COMPOSE_PROJECT_NAME=$(resolve_value COMPOSE_PROJECT_NAME "${project_name}-$(printf '%04d' "$slot")")
POSTGRES_HOST_PORT=$(resolve_value POSTGRES_HOST_PORT "$((15432 + slot))")
WEBHOOK_INGEST_HOST_PORT=$(resolve_value WEBHOOK_INGEST_HOST_PORT "$((20080 + slot))")
MINIO_API_HOST_PORT=$(resolve_value MINIO_API_HOST_PORT "$((24000 + slot))")
MINIO_CONSOLE_HOST_PORT=$(resolve_value MINIO_CONSOLE_HOST_PORT "$((28000 + slot))")

export COMPOSE_PROJECT_NAME
export POSTGRES_HOST_PORT
export WEBHOOK_INGEST_HOST_PORT
export MINIO_API_HOST_PORT
export MINIO_CONSOLE_HOST_PORT

if [ "${1:-}" = "print-ports" ]; then
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
