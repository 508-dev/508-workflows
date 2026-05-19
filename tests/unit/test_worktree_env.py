from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "worktree-env.sh"


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CONDUCTOR_PORT", None)
    for key in (
        "REDIS_HOST_PORT",
        "POSTGRES_HOST_PORT",
        "WEB_HOST_PORT",
        "WEBHOOK_INGEST_HOST_PORT",
        "MINIO_API_HOST_PORT",
        "MINIO_CONSOLE_HOST_PORT",
        "WEB_PORT",
        "WEBHOOK_INGEST_PORT",
        "HEALTHCHECK_PORT",
        "TEST_PORT",
    ):
        env.pop(key, None)
    return env


def _run_shell(
    script: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=REPO_ROOT,
        env=_base_env() if env is None else env,
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
    env = _base_env()
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
    env = _base_env()
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
    env = _base_env()
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
        env = _base_env()
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


def test_worktree_env_load_uses_conductor_port_range_for_defaults() -> None:
    env = _base_env()
    env["CONDUCTOR_PORT"] = "45000"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        printf '%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n' \\
          "$REDIS_HOST_PORT" \\
          "$POSTGRES_HOST_PORT" \\
          "$WEB_HOST_PORT" \\
          "$MINIO_API_HOST_PORT" \\
          "$MINIO_CONSOLE_HOST_PORT" \\
          "$WEB_PORT" \\
          "$HEALTHCHECK_PORT"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "45000",
        "45001",
        "45002",
        "45003",
        "45004",
        "45005",
        "45006",
    ]


def test_worktree_env_load_keeps_explicit_port_overrides_with_conductor_port() -> None:
    env = _base_env()
    env["CONDUCTOR_PORT"] = "45000"
    env["WEB_PORT"] = "47000"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        printf '%s\\n%s\\n' "$WEB_HOST_PORT" "$WEB_PORT"
        """,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["45002", "47000"]


def test_worktree_env_load_rejects_conductor_port_without_full_range() -> None:
    env = _base_env()
    env["CONDUCTOR_PORT"] = "65527"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        """,
        env=env,
    )

    assert result.returncode != 0
    assert "CONDUCTOR_PORT must leave room for a 10-port range" in result.stderr


def test_worktree_env_load_rejects_browser_unsafe_conductor_default() -> None:
    env = _base_env()
    env["CONDUCTOR_PORT"] = "5058"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        """,
        env=env,
    )

    assert result.returncode != 0
    assert (
        "WEB_HOST_PORT defaults to browser-unsafe port '5060' from CONDUCTOR_PORT"
        in result.stderr
    )
