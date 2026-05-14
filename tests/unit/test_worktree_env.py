from __future__ import annotations

import os
import subprocess
import tempfile
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
    env["WEB_HOST_PORT"] = "5060"

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
        "WEB_HOST_PORT cannot use browser-unsafe port '5060'; "
        "pick a different port." in result.stderr
    )


def test_worktree_env_load_accepts_legacy_compose_http_port_override() -> None:
    env = os.environ.copy()
    env.pop("WEB_HOST_PORT", None)
    env["WEBHOOK_INGEST_HOST_PORT"] = "23090"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} compose
        printf '%s\\n%s\\n' "$WEB_HOST_PORT" "$WEBHOOK_INGEST_HOST_PORT"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["23090", "23090"]


def test_worktree_env_load_host_mode_ignores_dotenv_host_port_pins() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir)
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir()
        env = os.environ.copy()
        env.pop("WEB_PORT", None)
        env.pop("WEBHOOK_INGEST_PORT", None)
        env.pop("HEALTHCHECK_PORT", None)
        (repo_root / ".env").write_text(
            "WEB_PORT=8090\nHEALTHCHECK_PORT=3000\n",
            encoding="utf-8",
        )

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {scripts_dir} host
            printf '%s\\n%s\\n%s\\n' "$WEB_PORT" "$WEBHOOK_INGEST_PORT" "$HEALTHCHECK_PORT"
            """,
            env=env,
        )

        assert result.returncode == 0

        hash_value = subprocess.run(
            [
                "/bin/sh",
                "-c",
                f"printf '%s' '{repo_root}' | cksum | awk '{{print $1}}'",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        slot = int(hash_value) % 2000

        assert result.stdout.splitlines() == [
            str(18080 + slot),
            str(18080 + slot),
            str(30000 + slot),
        ]
