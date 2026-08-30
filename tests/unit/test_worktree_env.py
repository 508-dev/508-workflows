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
    env.pop("PASEO_PORT_BASE", None)
    env.pop("PASEO_PORT_END", None)
    env.pop("WORKTREE_ENV_PORT_DEFAULT_SOURCE", None)
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


def test_resolve_browser_safe_port_rejects_leading_zero_unsafe_override() -> None:
    env = _base_env()
    env["TEST_PORT"] = "05060"

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
        "TEST_PORT cannot use browser-unsafe port '05060'; pick a different port."
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
                f"printf '%s' '{repo_root.resolve()}' | cksum | awk '{{print $1}}'",
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


def test_worktree_env_load_canonicalizes_symlinked_worktree_root() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        base_dir = Path(tmp_dir)
        repo_root = base_dir / "real-worktree"
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir(parents=True)
        symlink_root = base_dir / "worktree-alias"
        symlink_root.symlink_to(repo_root, target_is_directory=True)

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {symlink_root / "scripts"} host
            printf '%s\\n' "$WORKTREE_ENV_REPO_ROOT"
            """,
        )

        assert result.returncode == 0
        assert result.stdout.strip() == str(repo_root.resolve())


def test_worktree_env_load_uses_conductor_port_range_for_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir)
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir()
        env = _base_env()
        env["CONDUCTOR_PORT"] = "45000"

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {scripts_dir} host
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


def test_worktree_env_load_uses_paseo_port_range_for_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir)
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir()
        env = _base_env()
        env["CONDUCTOR_PORT"] = "45000"
        env["PASEO_PORT_BASE"] = "46000"
        env["PASEO_PORT_END"] = "46006"

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {scripts_dir} host
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
        "46000",
        "46001",
        "46002",
        "46003",
        "46004",
        "46005",
        "46006",
    ]


def test_worktree_env_load_rejects_incomplete_paseo_port_range() -> None:
    env = _base_env()
    env["PASEO_PORT_BASE"] = "46000"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        """,
        env=env,
    )

    assert result.returncode != 0
    assert "PASEO_PORT_BASE and PASEO_PORT_END must be set together." in result.stderr


def test_worktree_env_load_rejects_too_small_paseo_port_range() -> None:
    env = _base_env()
    env["PASEO_PORT_BASE"] = "46000"
    env["PASEO_PORT_END"] = "46005"

    result = _run_shell(
        f"""
        set -eu
        . {SCRIPT_PATH}
        worktree_env_load {REPO_ROOT / "scripts"} host
        """,
        env=env,
    )

    assert result.returncode != 0
    assert "PASEO_PORT_BASE through PASEO_PORT_END must include at least seven ports" in result.stderr


def test_worktree_env_print_port_summary_includes_host_service_ports() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir)
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir()
        env = _base_env()
        env["CONDUCTOR_PORT"] = "45000"

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {scripts_dir} host
            worktree_env_print_port_summary
            """,
            env=env,
        )

        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "Assigned worktree ports:",
            "  Redis:    127.0.0.1:45000",
            "  Postgres: 127.0.0.1:45001",
            "  MinIO:    127.0.0.1:45003",
            "  Console:  127.0.0.1:45004",
            "  Web/API:  127.0.0.1:45005",
            "  Bot:      127.0.0.1:45006",
        ]


def test_worktree_env_load_normalizes_leading_zero_conductor_port() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir)
        scripts_dir = repo_root / "scripts"
        scripts_dir.mkdir()
        env = _base_env()
        env["CONDUCTOR_PORT"] = "045000"

        result = _run_shell(
            f"""
            set -eu
            . {SCRIPT_PATH}
            worktree_env_load {scripts_dir} host
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
