#!/usr/bin/env python3
"""Run host services concurrently with prefixed logs."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable


SERVICES: list[tuple[str, list[str]]] = [
    ("api", ["uv", "run", "--package", "api", "backend-api"]),
    ("worker", ["uv", "run", "--package", "worker", "worker-consumer"]),
    ("discord-bot", ["uv", "run", "--package", "discord_bot", "discord-bot"]),
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


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    print("Launching host-run services with shared worktree env:")
    print(f"  API:      {env.get('BACKEND_API_BASE_URL', '')}")
    print(f"  Worker:   {env.get('WORKER_API_BASE_URL', '')}")
    print(f"  Bot:      {env.get('DISCORD_BOT_INTERNAL_BASE_URL', '')}")
    print()

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
        for name, command in SERVICES:
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
