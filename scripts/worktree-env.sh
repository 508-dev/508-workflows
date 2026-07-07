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

worktree_env_decimal_value() {
  decimal_value=$(printf '%s' "$1" | sed 's/^0*//')
  if [ -z "$decimal_value" ]; then
    decimal_value=0
  fi
  printf '%s' "$decimal_value"
}

worktree_env_resolve_conductor_port_base() {
  [ -n "${CONDUCTOR_PORT-}" ] || return 1

  worktree_env_validate_port_number "$CONDUCTOR_PORT" "CONDUCTOR_PORT" || return 2
  conductor_port_base=$(worktree_env_decimal_value "$CONDUCTOR_PORT")

  if [ "$conductor_port_base" -gt 65526 ]; then
    echo "CONDUCTOR_PORT must leave room for a 10-port range, got '$CONDUCTOR_PORT'." >&2
    return 2
  fi

  printf '%s' "$conductor_port_base"
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

  port_number=$(worktree_env_decimal_value "$port_value")
  if [ "$port_number" -lt 1 ] || [ "$port_number" -gt 65535 ]; then
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
  raw_resolved_value=$resolved_value

  worktree_env_validate_port_number "$resolved_value" "$port_label" || return 1
  resolved_value=$(worktree_env_decimal_value "$resolved_value")

  if [ "$source_label" = "default" ]; then
    if [ "${WORKTREE_ENV_PORT_DEFAULT_SOURCE-}" = "conductor" ] && worktree_env_is_browser_unsafe_port "$resolved_value"; then
      echo "$port_label defaults to browser-unsafe port '$resolved_value' from CONDUCTOR_PORT; set $port_label explicitly or use a different CONDUCTOR_PORT." \
        >&2
      return 1
    fi
    resolved_value=$(worktree_env_next_browser_safe_port "$resolved_value")
  elif worktree_env_is_browser_unsafe_port "$resolved_value"; then
    echo "$port_label cannot use browser-unsafe port '$raw_resolved_value'; pick a different port." >&2
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

worktree_env_resolve_browser_safe_port_preferred() {
  preferred_key=$1
  legacy_key=$2
  default_value=$3
  env_file=$4
  port_label=$5
  source_label=default
  eval "preferred_value=\${$preferred_key-}"
  eval "legacy_value=\${$legacy_key-}"

  if [ -n "$preferred_value" ]; then
    resolved_value=$preferred_value
    source_label=environment
  elif file_value=$(worktree_env_get_env_file_value "$preferred_key" "$env_file" 2>/dev/null); then
    resolved_value=$file_value
    source_label=.env
  elif [ -n "$legacy_value" ]; then
    resolved_value=$legacy_value
    source_label=environment
  elif file_value=$(worktree_env_get_env_file_value "$legacy_key" "$env_file" 2>/dev/null); then
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

worktree_env_resolve_browser_safe_shell_or_default_preferred() {
  preferred_key=$1
  legacy_key=$2
  default_value=$3
  port_label=$4
  source_label=default
  eval "preferred_value=\${$preferred_key-}"
  eval "legacy_value=\${$legacy_key-}"

  if [ -n "$preferred_value" ]; then
    resolved_value=$preferred_value
    source_label=environment
  elif [ -n "$legacy_value" ]; then
    resolved_value=$legacy_value
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

  WORKTREE_ENV_REPO_ROOT=$(CDPATH= cd "$script_dir/.." && pwd -P)
  WORKTREE_ENV_FILE="$WORKTREE_ENV_REPO_ROOT/.env"

  hash_value=$(printf '%s' "$WORKTREE_ENV_REPO_ROOT" | cksum | awk '{print $1}')
  WORKTREE_ENV_SLOT=$((hash_value % 2000))

  project_name=$(basename "$WORKTREE_ENV_REPO_ROOT" | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z' | sed 's/^-*//; s/-*$//')
  if [ -z "$project_name" ]; then
    project_name="worktree"
  fi

  COMPOSE_PROJECT_NAME=$(worktree_env_resolve_value COMPOSE_PROJECT_NAME "${project_name}-$(printf '%04d' "$WORKTREE_ENV_SLOT")" "$WORKTREE_ENV_FILE")
  if conductor_port_base=$(worktree_env_resolve_conductor_port_base); then
    WORKTREE_ENV_PORT_DEFAULT_SOURCE=conductor
    REDIS_HOST_PORT_DEFAULT=$conductor_port_base
    POSTGRES_HOST_PORT_DEFAULT=$((conductor_port_base + 1))
    WEB_HOST_PORT_DEFAULT=$((conductor_port_base + 2))
    MINIO_API_HOST_PORT_DEFAULT=$((conductor_port_base + 3))
    MINIO_CONSOLE_HOST_PORT_DEFAULT=$((conductor_port_base + 4))
    WEB_PORT_DEFAULT=$((conductor_port_base + 5))
    HEALTHCHECK_PORT_DEFAULT=$((conductor_port_base + 6))
  else
    conductor_port_status=$?
    if [ "$conductor_port_status" -ne 1 ]; then
      return "$conductor_port_status"
    fi

    WORKTREE_ENV_PORT_DEFAULT_SOURCE=worktree
    REDIS_HOST_PORT_DEFAULT=$((12000 + WORKTREE_ENV_SLOT))
    POSTGRES_HOST_PORT_DEFAULT=$((15432 + WORKTREE_ENV_SLOT))
    WEB_HOST_PORT_DEFAULT=$((20080 + WORKTREE_ENV_SLOT))
    MINIO_API_HOST_PORT_DEFAULT=$((24000 + WORKTREE_ENV_SLOT))
    MINIO_CONSOLE_HOST_PORT_DEFAULT=$((28000 + WORKTREE_ENV_SLOT))
    WEB_PORT_DEFAULT=$((18080 + WORKTREE_ENV_SLOT))
    HEALTHCHECK_PORT_DEFAULT=$((30000 + WORKTREE_ENV_SLOT))
  fi

  REDIS_HOST_PORT=$(worktree_env_resolve_value REDIS_HOST_PORT "$REDIS_HOST_PORT_DEFAULT" "$WORKTREE_ENV_FILE")
  POSTGRES_HOST_PORT=$(worktree_env_resolve_value POSTGRES_HOST_PORT "$POSTGRES_HOST_PORT_DEFAULT" "$WORKTREE_ENV_FILE")
  POSTGRES_USER=$(worktree_env_resolve_value POSTGRES_USER "postgres" "$WORKTREE_ENV_FILE")
  POSTGRES_PASSWORD=$(worktree_env_resolve_value POSTGRES_PASSWORD "postgres" "$WORKTREE_ENV_FILE")
  POSTGRES_DB=$(worktree_env_resolve_value POSTGRES_DB "workflows" "$WORKTREE_ENV_FILE")
  WEB_HOST_PORT=$(worktree_env_resolve_browser_safe_port_preferred WEB_HOST_PORT WEBHOOK_INGEST_HOST_PORT "$WEB_HOST_PORT_DEFAULT" "$WORKTREE_ENV_FILE" "WEB_HOST_PORT")
  MINIO_API_HOST_PORT=$(worktree_env_resolve_browser_safe_port MINIO_API_HOST_PORT "$MINIO_API_HOST_PORT_DEFAULT" "$WORKTREE_ENV_FILE" "MINIO_API_HOST_PORT")
  MINIO_CONSOLE_HOST_PORT=$(worktree_env_resolve_browser_safe_port MINIO_CONSOLE_HOST_PORT "$MINIO_CONSOLE_HOST_PORT_DEFAULT" "$WORKTREE_ENV_FILE" "MINIO_CONSOLE_HOST_PORT")

  export WORKTREE_ENV_REPO_ROOT
  export WORKTREE_ENV_FILE
  export WORKTREE_ENV_SLOT
  export COMPOSE_PROJECT_NAME
  export REDIS_HOST_PORT
  export POSTGRES_HOST_PORT
  export POSTGRES_USER
  export POSTGRES_PASSWORD
  export POSTGRES_DB
  export WEB_HOST_PORT
  export WEBHOOK_INGEST_HOST_PORT=$WEB_HOST_PORT
  export MINIO_API_HOST_PORT
  export MINIO_CONSOLE_HOST_PORT

  if [ "$mode" = "host" ]; then
    WEB_PORT=$(worktree_env_resolve_browser_safe_shell_or_default_preferred WEB_PORT WEBHOOK_INGEST_PORT "$WEB_PORT_DEFAULT" "WEB_PORT")
    HEALTHCHECK_PORT=$(worktree_env_resolve_browser_safe_shell_or_default HEALTHCHECK_PORT "$HEALTHCHECK_PORT_DEFAULT" "HEALTHCHECK_PORT")
    export WEB_PORT
    export WEBHOOK_INGEST_PORT=$WEB_PORT
    export HEALTHCHECK_PORT
  else
    unset WEB_PORT
    unset WEBHOOK_INGEST_PORT
    unset HEALTHCHECK_PORT
  fi
}

worktree_env_print_port_summary() {
  cat <<EOF
Assigned worktree ports:
  Redis:    127.0.0.1:${REDIS_HOST_PORT}
  Postgres: 127.0.0.1:${POSTGRES_HOST_PORT}
  MinIO:    127.0.0.1:${MINIO_API_HOST_PORT}
  Console:  127.0.0.1:${MINIO_CONSOLE_HOST_PORT}
EOF

  if [ -n "${WEB_PORT-}" ]; then
    cat <<EOF
  Web/API:  127.0.0.1:${WEB_PORT}
EOF
  fi

  if [ -n "${HEALTHCHECK_PORT-}" ]; then
    cat <<EOF
  Bot:      127.0.0.1:${HEALTHCHECK_PORT}
EOF
  fi
}
