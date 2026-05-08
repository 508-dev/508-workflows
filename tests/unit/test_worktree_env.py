from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "worktree-env.sh"


def _run_shell(
    script: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_resolve_browser_safe_port_skips_chrome_unsafe_defaults() -> None:
    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_resolve_browser_safe_port TEST_PORT 5060 /dev/null TEST_PORT
        """
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "5062"


def test_resolve_browser_safe_port_rejects_explicit_unsafe_override() -> None:
    env = os.environ.copy()
    env["TEST_PORT"] = "5060"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_resolve_browser_safe_port TEST_PORT 3000 /dev/null TEST_PORT
        """,
        env=env,
    )

    assert result.returncode != 0
    assert (
        "TEST_PORT cannot use browser-unsafe port '5060'; pick a different port."
        in result.stderr
    )


def test_worktree_env_load_rejects_unsafe_compose_http_port_override() -> None:
    env = os.environ.copy()
    env["WEBHOOK_INGEST_HOST_PORT"] = "5060"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} compose
        """,
        env=env,
    )

    assert result.returncode != 0
    assert (
        "WEBHOOK_INGEST_HOST_PORT cannot use browser-unsafe port '5060'; "
        "pick a different port." in result.stderr
    )
