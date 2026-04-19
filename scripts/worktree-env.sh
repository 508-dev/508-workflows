#!/bin/sh

worktree_env_get_env_file_value() {
  key=$1
  env_file=$2
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

worktree_env_resolve_value() {
  key=$1
  default_value=$2
  env_file=$3
  eval "shell_value=\${$key-}"

  if [ -n "$shell_value" ]; then
    printf '%s' "$shell_value"
    return
  fi

  if file_value=$(worktree_env_get_env_file_value "$key" "$env_file" 2>/dev/null); then
    printf '%s' "$file_value"
    return
  fi

  printf '%s' "$default_value"
}

worktree_env_resolve_shell_or_default() {
  key=$1
  default_value=$2
  eval "shell_value=\${$key-}"

  if [ -n "$shell_value" ]; then
    printf '%s' "$shell_value"
    return
  fi

  printf '%s' "$default_value"
}

worktree_env_load() {
  script_dir=$1
  mode=${2:-host}

  WORKTREE_ENV_REPO_ROOT=$(CDPATH= cd "$script_dir/.." && pwd)
  WORKTREE_ENV_FILE="$WORKTREE_ENV_REPO_ROOT/.env"

  hash_value=$(printf '%s' "$WORKTREE_ENV_REPO_ROOT" | cksum | awk '{print $1}')
  WORKTREE_ENV_SLOT=$((hash_value % 2000))

  project_name=$(basename "$WORKTREE_ENV_REPO_ROOT" | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z' | sed 's/^-*//; s/-*$//')
  if [ -z "$project_name" ]; then
    project_name="worktree"
  fi

  COMPOSE_PROJECT_NAME=$(worktree_env_resolve_value COMPOSE_PROJECT_NAME "${project_name}-$(printf '%04d' "$WORKTREE_ENV_SLOT")" "$WORKTREE_ENV_FILE")
  REDIS_HOST_PORT=$(worktree_env_resolve_value REDIS_HOST_PORT "$((12000 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE")
  POSTGRES_HOST_PORT=$(worktree_env_resolve_value POSTGRES_HOST_PORT "$((15432 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE")
  POSTGRES_USER=$(worktree_env_resolve_value POSTGRES_USER "postgres" "$WORKTREE_ENV_FILE")
  POSTGRES_PASSWORD=$(worktree_env_resolve_value POSTGRES_PASSWORD "postgres" "$WORKTREE_ENV_FILE")
  POSTGRES_DB=$(worktree_env_resolve_value POSTGRES_DB "workflows" "$WORKTREE_ENV_FILE")
  WEBHOOK_INGEST_HOST_PORT=$(worktree_env_resolve_value WEBHOOK_INGEST_HOST_PORT "$((20080 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE")
  MINIO_API_HOST_PORT=$(worktree_env_resolve_value MINIO_API_HOST_PORT "$((24000 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE")
  MINIO_CONSOLE_HOST_PORT=$(worktree_env_resolve_value MINIO_CONSOLE_HOST_PORT "$((28000 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE")

  export WORKTREE_ENV_REPO_ROOT
  export WORKTREE_ENV_FILE
  export WORKTREE_ENV_SLOT
  export COMPOSE_PROJECT_NAME
  export REDIS_HOST_PORT
  export POSTGRES_HOST_PORT
  export POSTGRES_USER
  export POSTGRES_PASSWORD
  export POSTGRES_DB
  export WEBHOOK_INGEST_HOST_PORT
  export MINIO_API_HOST_PORT
  export MINIO_CONSOLE_HOST_PORT

  if [ "$mode" = "host" ]; then
    # Keep host-run app ports below the Linux default ephemeral range
    # (32768-60999) to avoid rare EADDRINUSE races with outbound sockets.
    WEBHOOK_INGEST_PORT=$(worktree_env_resolve_shell_or_default WEBHOOK_INGEST_PORT "$((18080 + WORKTREE_ENV_SLOT))")
    HEALTHCHECK_PORT=$(worktree_env_resolve_shell_or_default HEALTHCHECK_PORT "$((30000 + WORKTREE_ENV_SLOT))")
    export WEBHOOK_INGEST_PORT
    export HEALTHCHECK_PORT
  else
    unset WEBHOOK_INGEST_PORT
    unset HEALTHCHECK_PORT
  fi
}
