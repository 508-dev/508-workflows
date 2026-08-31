#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/archive-workspace.sh [--dry-run] [--skip-docker]

Stops host-run dev processes for this workspace and runs Docker Compose down.

Options:
  -n, --dry-run     Print the processes and Compose command without stopping them.
  --skip-docker    Do not run ./scripts/docker-compose.sh down --remove-orphans.
  -h, --help       Show this help.
EOF
}

dry_run=0
skip_docker=0

normalize_decimal() {
  local value
  value=$(printf '%s' "$1" | sed 's/^0*//')
  if [ -z "$value" ]; then
    value=0
  fi
  printf '%s' "$value"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -n|--dry-run)
      dry_run=1
      ;;
    --skip-docker)
      skip_docker=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ -n "${CONDUCTOR_WORKSPACE_PATH:-}" ]; then
  workspace=$CONDUCTOR_WORKSPACE_PATH
elif git_root=$(git rev-parse --show-toplevel 2>/dev/null); then
  workspace=$git_root
else
  workspace=$(pwd -P)
fi

if [ ! -d "$workspace" ]; then
  echo "Workspace does not exist: $workspace" >&2
  exit 1
fi

cd "$workspace"
workspace=$(pwd -P)

if [ "$skip_docker" -eq 0 ] && [ ! -x "$workspace/scripts/docker-compose.sh" ]; then
  echo "Expected executable wrapper not found: $workspace/scripts/docker-compose.sh" >&2
  exit 1
fi

pid_file=$(mktemp)
trap 'rm -f "$pid_file"' EXIT

discover_pids() {
  WORKSPACE="$workspace" ARCHIVE_SCRIPT_PID=$$ python3 <<'PY'
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    command: str


WORKSPACE = os.path.realpath(os.environ["WORKSPACE"])
ARCHIVE_SCRIPT_PID = int(os.environ["ARCHIVE_SCRIPT_PID"])

COMMAND_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"(^|/)scripts/dev\.sh(\s|$)",
        r"(^|/)scripts/dev_mux\.py(\s|$)",
        r"(^|/)scripts/docker-compose\.sh(\s|$)",
        r"\buvicorn\b.*\bfive08\.backend\.api:create_app\b",
        r"\bbackend-api\b",
        r"\bwatchfiles\b.*\bworker-consumer\b",
        r"\bwatchfiles\b.*\bdiscord-bot\b",
        r"\bworker-consumer\b",
        r"\bdiscord-bot\b",
    )
]


def command_matches(command: str) -> bool:
    return any(pattern.search(command) for pattern in COMMAND_PATTERNS)


def list_processes() -> dict[int, Proc]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    processes: dict[int, Proc] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        command = parts[2] if len(parts) == 3 else ""
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        if pid in {os.getpid(), ARCHIVE_SCRIPT_PID}:
            continue
        processes[pid] = Proc(pid=pid, ppid=ppid, command=command)
    return processes


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cwd_for_pid(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return os.path.realpath(line[1:])
    return None


def is_under_workspace(path: str | None) -> bool:
    if not path:
        return False
    path = os.path.realpath(path)
    return path == WORKSPACE or path.startswith(WORKSPACE + os.sep)


def command_mentions_path(command: str, path: str) -> bool:
    if not path:
        return False
    start = 0
    while True:
        index = command.find(path, start)
        if index < 0:
            return False
        end = index + len(path)
        leading_boundary = index == 0 or command[index - 1] in " \t\n'\"=:("
        trailing_boundary = end == len(command) or command[end] == os.sep
        if leading_boundary and trailing_boundary:
            return True
        start = index + 1


def command_mentions_workspace(command: str) -> bool:
    return command_mentions_path(command, WORKSPACE)


def command_mentions_other_conductor_workspace(command: str) -> bool:
    return "/conductor/workspaces/" in command and not command_mentions_workspace(
        command
    )


def child_index(processes: dict[int, Proc]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process.pid)
    return children


def add_descendants(
    pid: int,
    processes: dict[int, Proc],
    children: dict[int, list[int]],
    selected: dict[int, str],
    reason: str,
) -> None:
    stack = list(children.get(pid, []))
    while stack:
        child_pid = stack.pop()
        if child_pid in selected:
            continue
        child = processes.get(child_pid)
        if child is not None and command_mentions_other_conductor_workspace(
            child.command
        ):
            continue
        selected[child_pid] = reason
        stack.extend(children.get(child_pid, []))


def listening_pids_for_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]


def allocated_port_range() -> tuple[str, list[int]]:
    paseo_base = os.environ.get("PASEO_PORT_BASE")
    paseo_end = os.environ.get("PASEO_PORT_END")
    if paseo_base and paseo_end:
        try:
            base = int(paseo_base)
            end = int(paseo_end)
        except ValueError:
            return "", []
        if 1 <= base <= end <= 65535 and end >= base + 6:
            return "PASEO_PORT range", list(range(base, end + 1))

    conductor_port = os.environ.get("CONDUCTOR_PORT")
    if not conductor_port:
        return "", []
    try:
        base = int(conductor_port)
    except ValueError:
        return "", []
    if base < 1 or base > 65526:
        return "", []
    return "CONDUCTOR_PORT range", [base + offset for offset in range(10)]


processes = list_processes()
children = child_index(processes)
selected: dict[int, str] = {}
cwd_cache: dict[int, str | None] = {}


def cached_cwd(pid: int) -> str | None:
    if pid not in cwd_cache:
        cwd_cache[pid] = cwd_for_pid(pid)
    return cwd_cache[pid]


for process in processes.values():
    if command_mentions_other_conductor_workspace(process.command):
        continue
    if not command_matches(process.command):
        continue
    if command_mentions_workspace(process.command):
        selected[process.pid] = "command references workspace"
    elif is_under_workspace(cached_cwd(process.pid)):
        selected[process.pid] = "cwd is inside workspace"

for pid in list(selected):
    add_descendants(
        pid,
        processes,
        children,
        selected,
        "descendant of workspace dev process",
    )

port_range_name, allocated_ports = allocated_port_range()
for port in allocated_ports:
    for pid in listening_pids_for_port(port):
        process = processes.get(pid)
        if process is None or not is_alive(pid):
            continue
        if command_mentions_other_conductor_workspace(process.command):
            continue
        if command_mentions_workspace(process.command) or is_under_workspace(
            cached_cwd(pid)
        ):
            selected.setdefault(pid, f"listening on {port_range_name} port {port}")
            add_descendants(
                pid,
                processes,
                children,
                selected,
                f"descendant of listener on {port_range_name} port {port}",
            )

for pid in sorted(selected):
    process = processes.get(pid)
    if process is None or not is_alive(pid):
        continue
    command = process.command.replace("\t", " ")
    print(f"{pid}\t{selected[pid]}\t{command}")
PY
}

print_processes() {
  if [ ! -s "$pid_file" ]; then
    echo "No matching workspace host processes found."
    return
  fi

  echo "Workspace host processes:"
  while IFS="$(printf '\t')" read -r pid reason command; do
    printf '  pid=%s reason=%s command=%s\n' "$pid" "$reason" "$command"
  done <"$pid_file"
}

signal_processes() {
  signal=$1
  [ -s "$pid_file" ] || return 0

  while IFS="$(printf '\t')" read -r pid _reason _command; do
    if [ "$dry_run" -eq 1 ]; then
      printf '[dry-run] kill -%s %s\n' "$signal" "$pid"
    else
      kill "-$signal" "$pid" 2>/dev/null || true
    fi
  done <"$pid_file"
}

echo "Archiving workspace: $workspace"
if [[ "${PASEO_PORT_BASE:-}" =~ ^[0-9]+$ && "${PASEO_PORT_END:-}" =~ ^[0-9]+$ ]]; then
  paseo_port_base=$(normalize_decimal "$PASEO_PORT_BASE")
  paseo_port_end=$(normalize_decimal "$PASEO_PORT_END")
  if [ "$paseo_port_base" -ge 1 ] && [ "$paseo_port_end" -le 65535 ] && [ "$paseo_port_end" -ge $((paseo_port_base + 6)) ]; then
    echo "PASEO port range: ${paseo_port_base}..${paseo_port_end}"
  fi
elif [[ "${CONDUCTOR_PORT:-}" =~ ^[0-9]+$ ]]; then
  conductor_port_base=$(normalize_decimal "$CONDUCTOR_PORT")
  echo "Conductor port range: ${conductor_port_base}..$((conductor_port_base + 9))"
fi

discover_pids >"$pid_file"
print_processes
signal_processes TERM

if [ "$dry_run" -eq 0 ] && [ -s "$pid_file" ]; then
  sleep 2
  discover_pids >"$pid_file"
fi

signal_processes KILL

if [ "$skip_docker" -eq 1 ]; then
  echo "Skipping Docker Compose shutdown."
elif [ "$dry_run" -eq 1 ]; then
  echo "[dry-run] ./scripts/docker-compose.sh down --remove-orphans"
else
  ./scripts/docker-compose.sh down --remove-orphans
fi
