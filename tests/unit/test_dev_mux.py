from __future__ import annotations

import importlib.util
import socket
from pathlib import Path


def _load_dev_mux_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "dev_mux.py"
    spec = importlib.util.spec_from_file_location("test_dev_mux_module", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_ports_available_reports_invalid_url_port() -> None:
    module = _load_dev_mux_module()

    ok, error = module._ensure_ports_available(
        {"BACKEND_API_BASE_URL": "http://127.0.0.1:abc"}
    )

    assert ok is False
    assert error == (
        "BACKEND_API_BASE_URL must include a valid numeric port: 'http://127.0.0.1:abc'"
    )


def test_ensure_ports_available_handles_missing_lsof_gracefully(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("lsof")

        monkeypatch.setattr(module.subprocess, "run", fake_run)

        ok, error = module._ensure_ports_available(
            {"BACKEND_API_BASE_URL": f"http://127.0.0.1:{port}"}
        )

    assert ok is False
    assert error == (
        f"web port {port} is already in use; "
        "install lsof for owner details or stop the existing listener and retry."
    )


def test_ensure_ports_available_reclaims_same_service_from_conductor_workspace(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    owner_command = (
        "/Users/michaelwu/conductor/workspaces/508-workflows/missoula-v3/"
        ".venv/bin/python3 "
        "/Users/michaelwu/conductor/workspaces/508-workflows/missoula-v3/"
        ".venv/bin/discord-bot"
    )

    monkeypatch.setattr(module, "_listening_pids", lambda port: [67428])
    monkeypatch.setattr(module, "_pid_command", lambda pid: owner_command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: "")
    monkeypatch.setattr(
        module,
        "_process_table",
        lambda: {67428: module.ProcessInfo(67428, 1, owner_command)},
    )
    monkeypatch.setattr(module, "_port_is_free", lambda port: True)
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": (
                "/Users/michaelwu/conductor/workspaces/508-workflows/victoria-v1"
            ),
            "DISCORD_BOT_INTERNAL_BASE_URL": "http://127.0.0.1:30054",
        },
        {"discord-bot"},
    )

    assert ok is True
    assert error is None
    assert stopped == {67428}


def test_ensure_ports_available_does_not_reclaim_different_service(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    owner_command = (
        "/Users/michaelwu/conductor/workspaces/508-workflows/missoula-v3/"
        ".venv/bin/python3 "
        "/Users/michaelwu/conductor/workspaces/508-workflows/missoula-v3/"
        ".venv/bin/discord-bot"
    )

    monkeypatch.setattr(module, "_listening_pids", lambda port: [67428])
    monkeypatch.setattr(module, "_pid_command", lambda pid: owner_command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: "")
    monkeypatch.setattr(
        module,
        "_process_table",
        lambda: {67428: module.ProcessInfo(67428, 1, owner_command)},
    )
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": (
                "/Users/michaelwu/conductor/workspaces/508-workflows/victoria-v1"
            ),
            "BACKEND_API_BASE_URL": "http://127.0.0.1:30054",
        },
        {"web"},
    )

    assert ok is False
    assert error is not None
    assert "web port 30054 is already in use" in error
    assert stopped == set()


def test_ensure_ports_available_does_not_reclaim_unrelated_same_worktree_listener(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    workspace = "/Users/michaelwu/conductor/workspaces/508-workflows/victoria-v1"
    owner_command = f"{workspace}/.venv/bin/python3 {workspace}/scripts/other.py"

    monkeypatch.setattr(module, "_listening_pids", lambda port: [67428])
    monkeypatch.setattr(module, "_pid_command", lambda pid: owner_command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: workspace)
    monkeypatch.setattr(
        module,
        "_process_table",
        lambda: {67428: module.ProcessInfo(67428, 1, owner_command)},
    )
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": workspace,
            "DISCORD_BOT_INTERNAL_BASE_URL": "http://127.0.0.1:30054",
        },
        {"discord-bot"},
    )

    assert ok is False
    assert error is not None
    assert "discord-bot port 30054 is already in use" in error
    assert stopped == set()


def test_ensure_ports_available_reclaims_same_service_parent_tree(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    workspace = "/Users/michaelwu/conductor/workspaces/508-workflows/missoula-v3"

    processes = {
        100: module.ProcessInfo(100, 1, "uv run watchfiles discord-bot"),
        101: module.ProcessInfo(101, 100, "uv run --package discord_bot discord-bot"),
        102: module.ProcessInfo(
            102,
            101,
            f"{workspace}/.venv/bin/python3 {workspace}/.venv/bin/discord-bot",
        ),
    }

    monkeypatch.setattr(module, "_listening_pids", lambda port: [102])
    monkeypatch.setattr(module, "_pid_command", lambda pid: processes[pid].command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: workspace)
    monkeypatch.setattr(module, "_process_table", lambda: processes)
    monkeypatch.setattr(module, "_port_is_free", lambda port: True)
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": (
                "/Users/michaelwu/conductor/workspaces/508-workflows/victoria-v1"
            ),
            "DISCORD_BOT_INTERNAL_BASE_URL": "http://127.0.0.1:30054",
        },
        {"discord-bot"},
    )

    assert ok is True
    assert error is None
    assert stopped == {100, 101, 102}


def test_ensure_ports_available_does_not_reclaim_prefix_sibling_workspace(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    owner_command = (
        "/tmp/508-workflows/foo-bar/.venv/bin/python3 "
        "/tmp/508-workflows/foo-bar/.venv/bin/discord-bot"
    )

    monkeypatch.setattr(module, "_listening_pids", lambda port: [67428])
    monkeypatch.setattr(module, "_pid_command", lambda pid: owner_command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: "")
    monkeypatch.setattr(
        module,
        "_process_table",
        lambda: {67428: module.ProcessInfo(67428, 1, owner_command)},
    )
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": "/tmp/508-workflows/foo",
            "DISCORD_BOT_INTERNAL_BASE_URL": "http://127.0.0.1:30054",
        },
        {"discord-bot"},
    )

    assert ok is False
    assert error is not None
    assert "discord-bot port 30054 is already in use" in error
    assert stopped == set()


def test_ensure_ports_available_does_not_reclaim_superpath_prefix_workspace(
    monkeypatch,
) -> None:
    module = _load_dev_mux_module()
    stopped: set[int] = set()
    owner_command = (
        "/var/tmp/508-workflows/foo/.venv/bin/python3 "
        "/var/tmp/508-workflows/foo/.venv/bin/discord-bot"
    )

    monkeypatch.setattr(module, "_listening_pids", lambda port: [67428])
    monkeypatch.setattr(module, "_pid_command", lambda pid: owner_command)
    monkeypatch.setattr(module, "_pid_cwd", lambda pid: "")
    monkeypatch.setattr(
        module,
        "_process_table",
        lambda: {67428: module.ProcessInfo(67428, 1, owner_command)},
    )
    monkeypatch.setattr(module, "_stop_pids", lambda pids: stopped.update(pids))

    ok, error = module._ensure_ports_available(
        {
            "WORKTREE_ENV_REPO_ROOT": "/tmp/508-workflows/foo",
            "DISCORD_BOT_INTERNAL_BASE_URL": "http://127.0.0.1:30054",
        },
        {"discord-bot"},
    )

    assert ok is False
    assert error is not None
    assert "discord-bot port 30054 is already in use" in error
    assert stopped == set()


def test_service_commands_accept_legacy_webhook_ingest_port() -> None:
    module = _load_dev_mux_module()

    commands = module._service_commands(
        {
            "WEBHOOK_INGEST_HOST": "127.0.0.1",
            "WEBHOOK_INGEST_PORT": "19090",
        }
    )

    web_command = commands[0][1]

    assert web_command[web_command.index("--host") + 1] == "127.0.0.1"
    assert web_command[web_command.index("--port") + 1] == "19090"
