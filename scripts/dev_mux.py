#!/usr/bin/env python3
"""Run host services concurrently with prefixed logs."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from typing import NamedTuple
from urllib.parse import urlparse


PORT_SERVICES: dict[str, str] = {
    "web": "BACKEND_API_BASE_URL",
    "discord-bot": "DISCORD_BOT_INTERNAL_BASE_URL",
}

ALL_SERVICES = ("web", "worker", "discord-bot")


class ProcessInfo(NamedTuple):
    pid: int
    ppid: int
    command: str


def _service_commands(
    env: dict[str, str], selected_services: set[str] | None = None
) -> list[tuple[str, list[str]]]:
    selected_services = selected_services or set(ALL_SERVICES)
    uv_bin = env.get("UV_BIN") or shutil.which("uv") or "uv"
    uv_command = shlex.quote(uv_bin)
    commands = [
        (
            "web",
            [
                uv_bin,
                "run",
                "--package",
                "api",
                "uvicorn",
                "five08.backend.api:create_app",
                "--factory",
                "--host",
                env.get("WEB_HOST", env.get("WEBHOOK_INGEST_HOST", "0.0.0.0")),
                "--port",
                env.get("WEB_PORT", env.get("WEBHOOK_INGEST_PORT", "8090")),
                "--reload",
                "--reload-dir",
                "apps/api/src",
                "--reload-dir",
                "apps/worker/src",
                "--reload-dir",
                "packages/shared/src",
            ],
        ),
        (
            "worker",
            [
                uv_bin,
                "run",
                "watchfiles",
                "--filter",
                "python",
                "--sigint-timeout",
                "5",
                "--sigkill-timeout",
                "10",
                f"{uv_command} run --package worker worker-consumer",
                "apps/worker/src",
                "packages/shared/src",
            ],
        ),
        (
            "discord-bot",
            [
                uv_bin,
                "run",
                "watchfiles",
                "--filter",
                "python",
                "--sigint-timeout",
                "5",
                "--sigkill-timeout",
                "10",
                f"{uv_command} run --package discord_bot discord-bot",
                "apps/discord_bot/src",
                "packages/shared/src",
            ],
        ),
    ]
    return [(name, command) for name, command in commands if name in selected_services]


def _stream_output(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()
    process.stdout.close()


def _terminate_process_group(process: subprocess.Popen[str], sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def _stop_processes(processes: Iterable[subprocess.Popen[str]]) -> None:
    active = [process for process in processes if process.poll() is None]
    for process in active:
        _terminate_process_group(process, signal.SIGTERM)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if all(process.poll() is not None for process in active):
            return
        time.sleep(0.1)

    for process in active:
        _terminate_process_group(process, signal.SIGKILL)


def _service_port(env: dict[str, str], env_key: str) -> int | None:
    value = env.get(env_key)
    if not value:
        return None
    parsed = urlparse(value)
    try:
        return parsed.port
    except ValueError as exc:
        raise ValueError(
            f"{env_key} must include a valid numeric port: {value!r}"
        ) from exc


def _can_bind_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _port_is_free(port: int) -> bool:
    pids = _listening_pids(port)
    if pids is None:
        return _can_bind_port(port)
    return not pids


def _listening_pids(port: int) -> list[int] | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]


def _pid_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _pid_cwd(pid: int) -> str:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return os.path.realpath(line[1:])
    return ""


def _process_table() -> dict[int, ProcessInfo]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    processes: dict[int, ProcessInfo] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        processes[pid] = ProcessInfo(
            pid=pid,
            ppid=ppid,
            command=parts[2] if len(parts) == 3 else "",
        )
    return processes


def _child_index(processes: dict[int, ProcessInfo]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process.pid)
    return children


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_pids(pids: Iterable[int]) -> None:
    unique_pids = sorted(set(pids))
    for pid in unique_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if all(not _is_running(pid) for pid in unique_pids):
            return
        time.sleep(0.1)

    for pid in unique_pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def _conductor_workspace_group(worktree_root: str) -> str:
    marker = "/conductor/workspaces/"
    marker_index = worktree_root.find(marker)
    if marker_index < 0:
        return ""
    group_start = marker_index + len(marker)
    group_end = worktree_root.find("/", group_start)
    if group_end < 0:
        return ""
    return worktree_root[:group_end]


def _is_under_path(path: str, root: str) -> bool:
    if not path or not root:
        return False
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def _command_mentions_path(command: str, path: str) -> bool:
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


def _command_service(command: str) -> str | None:
    if "five08.backend.api:create_app" in command or "backend-api" in command:
        return "web"
    if "discord-bot" in command:
        return "discord-bot"
    return None


def _process_in_path_scope(
    process: ProcessInfo,
    *,
    worktree_root: str,
    conductor_group: str,
    cwd: str = "",
) -> bool:
    if worktree_root and (
        _command_mentions_path(process.command, worktree_root)
        or _is_under_path(cwd, worktree_root)
    ):
        return True
    return bool(
        conductor_group
        and (
            _command_mentions_path(process.command, conductor_group)
            or _is_under_path(cwd, conductor_group)
        )
    )


def _service_context_chain(
    owner_pid: int,
    *,
    service_name: str,
    worktree_root: str,
    conductor_group: str,
    commands: dict[int, str],
    cwds: dict[int, str],
    processes: dict[int, ProcessInfo],
) -> set[int]:
    chain: list[ProcessInfo] = []
    process = processes.get(owner_pid)
    if process is None:
        process = ProcessInfo(owner_pid, 0, commands.get(owner_pid, ""))

    while process is not None:
        command = commands.get(process.pid, process.command)
        current = ProcessInfo(process.pid, process.ppid, command)
        cwd = cwds.setdefault(current.pid, _pid_cwd(current.pid))
        if not _process_in_path_scope(
            current,
            worktree_root=worktree_root,
            conductor_group=conductor_group,
            cwd=cwd,
        ):
            break
        chain.append(current)
        process = processes.get(current.ppid)

    service_indexes = [
        index
        for index, process_info in enumerate(chain)
        if _command_service(process_info.command) == service_name
    ]
    if not service_indexes:
        return set()
    return {process_info.pid for process_info in chain[: max(service_indexes) + 1]}


def _related_reclaim_pids(
    owner_pids: Iterable[int],
    *,
    service_name: str,
    worktree_root: str,
    conductor_group: str,
    commands: dict[int, str],
    cwds: dict[int, str],
    processes: dict[int, ProcessInfo],
) -> set[int]:
    selected: set[int] = set()
    for owner_pid in owner_pids:
        selected.update(
            _service_context_chain(
                owner_pid,
                service_name=service_name,
                worktree_root=worktree_root,
                conductor_group=conductor_group,
                commands=commands,
                cwds=cwds,
                processes=processes,
            )
        )

    children = _child_index(processes)
    stack = list(selected)
    while stack:
        parent_pid = stack.pop()
        for child_pid in children.get(parent_pid, []):
            if child_pid in selected:
                continue
            child = processes.get(child_pid)
            if child is None:
                continue
            child_cwd = cwds.setdefault(child_pid, _pid_cwd(child_pid))
            if not _process_in_path_scope(
                child,
                worktree_root=worktree_root,
                conductor_group=conductor_group,
                cwd=child_cwd,
            ):
                continue
            selected.add(child_pid)
            stack.append(child_pid)

    return selected


def _ensure_ports_available(
    env: dict[str, str], selected_services: set[str] | None = None
) -> tuple[bool, str | None]:
    selected_services = selected_services or set(ALL_SERVICES)
    worktree_root = env.get("WORKTREE_ENV_REPO_ROOT", "")
    conductor_group = _conductor_workspace_group(worktree_root)

    for service_name, env_key in PORT_SERVICES.items():
        if service_name not in selected_services:
            continue

        try:
            port = _service_port(env, env_key)
        except ValueError as exc:
            return False, str(exc)

        if port is None:
            continue

        pids = _listening_pids(port)
        if pids is None:
            if _can_bind_port(port):
                continue
            return False, (
                f"{service_name} port {port} is already in use; "
                "install lsof for owner details or stop the existing listener and retry."
            )

        if not pids:
            continue

        processes = _process_table()
        commands = {pid: _pid_command(pid) for pid in pids}
        cwds = {pid: _pid_cwd(pid) for pid in pids}
        related_pids = _related_reclaim_pids(
            pids,
            service_name=service_name,
            worktree_root=worktree_root,
            conductor_group=conductor_group,
            commands=commands,
            cwds=cwds,
            processes=processes,
        )
        if related_pids and all(pid in related_pids for pid in pids):
            print(
                f"{service_name} port {port} is already in use by "
                "same-workspace service process(es); reclaiming it."
            )
            _stop_pids(related_pids)
            if _port_is_free(port):
                continue
            pids = _listening_pids(port)
            if pids is None:
                pids = []
            commands = {pid: _pid_command(pid) for pid in pids}

        owners = (
            ", ".join(
                f"pid={pid} command={command or '<unknown>'}"
                for pid, command in commands.items()
            )
            or "<unknown>"
        )
        return False, (
            f"{service_name} port {port} is already in use; "
            f"stop the existing listener and retry. Owners: {owners}"
        )

    return True, None


def _ensure_selected_ports(env: dict[str, str], services: set[str]) -> int:
    ports_ok, port_error = _ensure_ports_available(env, services)
    if not ports_ok:
        print(port_error, file=sys.stderr)
        return 1
    return 0


def _selected_services(argv: list[str]) -> set[str] | None:
    if not argv:
        return set(ALL_SERVICES)

    aliases = {
        "api": "web",
        "bot": "discord-bot",
        "discord_bot": "discord-bot",
    }
    selected: set[str] = set()
    invalid: list[str] = []
    for value in argv:
        service = aliases.get(value, value)
        if service not in ALL_SERVICES:
            invalid.append(value)
            continue
        selected.add(service)

    if invalid or not selected:
        services = "|".join(ALL_SERVICES)
        program = os.path.basename(sys.argv[0]) or "dev_mux.py"
        print(
            f"Usage: {program} [{services} ...]",
            file=sys.stderr,
        )
        if invalid:
            print(f"Unknown service(s): {', '.join(invalid)}", file=sys.stderr)
        return None

    return selected


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    if argv and argv[0] == "--ensure-port":
        if len(argv) == 2 and argv[1] in PORT_SERVICES:
            return _ensure_selected_ports(env, {argv[1]})
        program = os.path.basename(sys.argv[0]) or "dev_mux.py"
        print(
            f"Usage: {program} [--ensure-port web|discord-bot]",
            file=sys.stderr,
        )
        return 2

    selected_services = _selected_services(argv)
    if selected_services is None:
        return 2

    print("Launching host-run services with shared worktree env:")
    if "web" in selected_services:
        print(f"  Web/API dashboard:    {env.get('BACKEND_API_BASE_URL', '')}")
    else:
        print("  Web/API dashboard:    skipped")
    if "discord-bot" in selected_services:
        print(f"  Bot health listener:  {env.get('DISCORD_BOT_INTERNAL_BASE_URL', '')}")
    else:
        print("  Bot health listener:  skipped")
    print(
        "  Worker listener:      none (queue consumer)"
        if "worker" in selected_services
        else "  Worker listener:      skipped"
    )
    print()

    ports_ok, port_error = _ensure_ports_available(env, selected_services)
    if not ports_ok:
        print(port_error, file=sys.stderr)
        return 1

    processes: list[subprocess.Popen[str]] = []
    threads: list[threading.Thread] = []
    interrupted = False

    def handle_signal(signum: int, _frame: object) -> None:
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        sys.stdout.write(f"\nReceived signal {signum}, stopping services...\n")
        sys.stdout.flush()
        _stop_processes(processes)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        for name, command in _service_commands(env, selected_services):
            process = subprocess.Popen(
                command,
                cwd=env.get("WORKTREE_ENV_REPO_ROOT") or None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            processes.append(process)
            thread = threading.Thread(
                target=_stream_output,
                args=(name, process),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        exit_code = 0
        while True:
            if interrupted:
                exit_code = 130
                break

            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    exit_code = (
                        128 + abs(return_code) if return_code < 0 else return_code
                    )
                    _stop_processes(processes)
                    interrupted = True
                    break
            if interrupted:
                break
            time.sleep(0.2)
    finally:
        _stop_processes(processes)
        for thread in threads:
            thread.join(timeout=1.0)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
