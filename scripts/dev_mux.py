#!/usr/bin/env python3
"""Run host services concurrently with prefixed logs."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from urllib.parse import urlparse


PORT_SERVICES: list[tuple[str, str]] = [
    ("web", "BACKEND_API_BASE_URL"),
    ("discord-bot", "DISCORD_BOT_INTERNAL_BASE_URL"),
]


def _service_commands(env: dict[str, str]) -> list[tuple[str, list[str]]]:
    return [
        (
            "web",
            [
                "uv",
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
                "uv",
                "run",
                "watchfiles",
                "--filter",
                "python",
                "--sigint-timeout",
                "5",
                "--sigkill-timeout",
                "10",
                "uv run --package worker worker-consumer",
                "apps/worker/src",
                "packages/shared/src",
            ],
        ),
        (
            "discord-bot",
            [
                "uv",
                "run",
                "watchfiles",
                "--filter",
                "python",
                "--sigint-timeout",
                "5",
                "--sigkill-timeout",
                "10",
                "uv run --package discord_bot discord-bot",
                "apps/discord_bot/src",
                "packages/shared/src",
            ],
        ),
    ]


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


def _stop_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _ensure_ports_available(env: dict[str, str]) -> tuple[bool, str | None]:
    worktree_root = env.get("WORKTREE_ENV_REPO_ROOT", "")

    for service_name, env_key in PORT_SERVICES:
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

        commands = {pid: _pid_command(pid) for pid in pids}
        stale_pids = [
            pid
            for pid, command in commands.items()
            if worktree_root and worktree_root in command
        ]
        if stale_pids and len(stale_pids) == len(pids):
            print(
                f"{service_name} port {port} is already in use by stale "
                "same-worktree process(es); reclaiming it."
            )
            for pid in stale_pids:
                _stop_pid(pid)
            if _port_is_free(port):
                continue
            pids = _listening_pids(port)
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


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    print("Launching host-run services with shared worktree env:")
    print(f"  Web/API listener:     {env.get('BACKEND_API_BASE_URL', '')}")
    print(f"  Bot health listener:  {env.get('DISCORD_BOT_INTERNAL_BASE_URL', '')}")
    print("  Worker listener:      none (queue consumer)")
    print()

    ports_ok, port_error = _ensure_ports_available(env)
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
        for name, command in _service_commands(env):
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
