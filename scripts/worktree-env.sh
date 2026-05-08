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

worktree_env_validate_port_number() {
  port_value=$1
  port_label=$2

  case $port_value in
    ''|*[!0-9]*)
      echo "$port_label must be a numeric TCP port, got '$port_value'." >&2
      return 1
      ;;
  esac

  if [ "$port_value" -lt 1 ] || [ "$port_value" -gt 65535 ]; then
    echo "$port_label must be between 1 and 65535, got '$port_value'." >&2
    return 1
  fi
}

worktree_env_is_browser_unsafe_port() {
  case " $1 " in
    *" 1 "*|*" 7 "*|*" 9 "*|*" 11 "*|*" 13 "*|*" 15 "*|*" 17 "*|*" 19 "*|*" 20 "*|*" 21 "*|*" 22 "*|*" 23 "*|*" 25 "*|*" 37 "*|*" 42 "*|*" 43 "*|*" 53 "*|*" 69 "*|*" 77 "*|*" 79 "*|*" 87 "*|*" 95 "*|*" 101 "*|*" 102 "*|*" 103 "*|*" 104 "*|*" 109 "*|*" 110 "*|*" 111 "*|*" 113 "*|*" 115 "*|*" 117 "*|*" 119 "*|*" 123 "*|*" 135 "*|*" 137 "*|*" 139 "*|*" 143 "*|*" 161 "*|*" 179 "*|*" 389 "*|*" 427 "*|*" 465 "*|*" 512 "*|*" 513 "*|*" 514 "*|*" 515 "*|*" 526 "*|*" 530 "*|*" 531 "*|*" 532 "*|*" 540 "*|*" 548 "*|*" 554 "*|*" 556 "*|*" 563 "*|*" 587 "*|*" 601 "*|*" 636 "*|*" 989 "*|*" 990 "*|*" 993 "*|*" 995 "*|*" 1719 "*|*" 1720 "*|*" 1723 "*|*" 2049 "*|*" 3659 "*|*" 4045 "*|*" 5060 "*|*" 5061 "*|*" 6000 "*|*" 6566 "*|*" 6665 "*|*" 6666 "*|*" 6667 "*|*" 6668 "*|*" 6669 "*|*" 6697 "*|*" 10080 "*)
      return 0
      ;;
  esac
  return 1
}

worktree_env_next_browser_safe_port() {
  port_value=$1
  while worktree_env_is_browser_unsafe_port "$port_value"; do
    port_value=$((port_value + 1))
  done
  printf '%s' "$port_value"
}

worktree_env_finalize_browser_safe_port() {
  resolved_value=$1
  source_label=$2
  port_label=$3

  worktree_env_validate_port_number "$resolved_value" "$port_label" || return 1

  if [ "$source_label" = "default" ]; then
    resolved_value=$(worktree_env_next_browser_safe_port "$resolved_value")
  elif worktree_env_is_browser_unsafe_port "$resolved_value"; then
    echo "$port_label cannot use browser-unsafe port '$resolved_value'; pick a different port." >&2
    return 1
  fi

  printf '%s' "$resolved_value"
}

worktree_env_resolve_browser_safe_port() {
  key=$1
  default_value=$2
  env_file=$3
  port_label=$4
  source_label=default
  eval "shell_value=\${$key-}"

  if [ -n "$shell_value" ]; then
    resolved_value=$shell_value
    source_label=environment
  elif file_value=$(worktree_env_get_env_file_value "$key" "$env_file" 2>/dev/null); then
    resolved_value=$file_value
    source_label=.env
  else
    resolved_value=$default_value
  fi

  worktree_env_finalize_browser_safe_port \
    "$resolved_value" \
    "$source_label" \
    "$port_label"
}

worktree_env_resolve_browser_safe_shell_or_default() {
  key=$1
  default_value=$2
  port_label=$3
  source_label=default
  eval "shell_value=\${$key-}"

  if [ -n "$shell_value" ]; then
    resolved_value=$shell_value
    source_label=environment
  else
    resolved_value=$default_value
  fi

  worktree_env_finalize_browser_safe_port \
    "$resolved_value" \
    "$source_label" \
    "$port_label"
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
  WEBHOOK_INGEST_HOST_PORT=$(worktree_env_resolve_browser_safe_port WEBHOOK_INGEST_HOST_PORT "$((20080 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE" "WEBHOOK_INGEST_HOST_PORT")
  MINIO_API_HOST_PORT=$(worktree_env_resolve_browser_safe_port MINIO_API_HOST_PORT "$((24000 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE" "MINIO_API_HOST_PORT")
  MINIO_CONSOLE_HOST_PORT=$(worktree_env_resolve_browser_safe_port MINIO_CONSOLE_HOST_PORT "$((28000 + WORKTREE_ENV_SLOT))" "$WORKTREE_ENV_FILE" "MINIO_CONSOLE_HOST_PORT")

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
    WEBHOOK_INGEST_PORT=$(worktree_env_resolve_browser_safe_shell_or_default WEBHOOK_INGEST_PORT "$((18080 + WORKTREE_ENV_SLOT))" "WEBHOOK_INGEST_PORT")
    HEALTHCHECK_PORT=$(worktree_env_resolve_browser_safe_shell_or_default HEALTHCHECK_PORT "$((30000 + WORKTREE_ENV_SLOT))" "HEALTHCHECK_PORT")
    export WEBHOOK_INGEST_PORT
    export HEALTHCHECK_PORT
  else
    unset WEBHOOK_INGEST_PORT
    unset HEALTHCHECK_PORT
  fi
}
