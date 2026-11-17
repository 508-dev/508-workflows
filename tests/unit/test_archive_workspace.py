from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "archive-workspace.sh"


def _write_fake_lsof(tmp_path: Path) -> Path:
    fake_lsof = tmp_path / "lsof"
    fake_lsof.write_text(
        """#!/bin/sh
listener_pid=$(ps -axo pid=,command= | awk -v marker="$TEST_LISTENER_MARKER" 'index($0, marker) { print $1; exit }')
if [ \"${1:-}\" = \"-a\" ]; then
  if [ \"${3:-}\" = \"$listener_pid\" ]; then
    printf 'n%s\\n' \"$TEST_WORKSPACE\"
  fi
  exit 0
fi
for argument in \"$@\"; do
  case \"$argument\" in
    -tiTCP:*)
      printf '%s\\n' \"${argument#-tiTCP:}\" >>\"$TEST_LSOF_PORT_LOG\"
      if [ \"${argument#-tiTCP:}\" = \"$TEST_LISTENER_PORT\" ]; then
        printf '%s\\n' \"$listener_pid\"
      fi
      ;;
  esac
done
""",
        encoding="utf-8",
    )
    fake_lsof.chmod(0o755)
    return fake_lsof


def _run_archive_with_listener(
    tmp_path: Path,
    *,
    conductor_port: str,
    paseo_port_base: str,
    paseo_port_end: str,
    listener_port: str,
) -> tuple[subprocess.CompletedProcess[str], str, list[str]]:
    env = os.environ.copy()
    listener_marker = "paseo-archive-test-listener"
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            listener_marker,
        ],
        cwd=REPO_ROOT,
    )
    port_log = tmp_path / "lsof-ports.log"
    _write_fake_lsof(tmp_path)
    env.update(
        {
            "CONDUCTOR_PORT": conductor_port,
            "PASEO_PORT_BASE": paseo_port_base,
            "PASEO_PORT_END": paseo_port_end,
            "PATH": f"{tmp_path}:{env['PATH']}",
            "TEST_WORKSPACE": str(REPO_ROOT),
            "TEST_LISTENER_MARKER": listener_marker,
            "TEST_LISTENER_PORT": listener_port,
            "TEST_LSOF_PORT_LOG": str(port_log),
        }
    )

    try:
        result = subprocess.run(
            [str(SCRIPT_PATH), "--dry-run", "--skip-docker"],
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        listener.terminate()
        listener.wait(timeout=5)

    return result, listener_marker, port_log.read_text(encoding="utf-8").splitlines()


def test_archive_workspace_discovers_paseo_only_listener_on_assigned_port(
    tmp_path: Path,
) -> None:
    result, listener_marker, scanned_ports = _run_archive_with_listener(
        tmp_path,
        conductor_port="45000",
        paseo_port_base="46000",
        paseo_port_end="65000",
        listener_port="46006",
    )

    assert result.returncode == 0, result.stderr
    assert "reason=listening on PASEO_PORT range port 46006" in result.stdout
    assert listener_marker in result.stdout
    assert scanned_ports == [str(port) for port in range(46000, 46007)]


def test_archive_workspace_falls_back_to_conductor_for_malformed_paseo_ports(
    tmp_path: Path,
) -> None:
    result, listener_marker, scanned_ports = _run_archive_with_listener(
        tmp_path,
        conductor_port="45000",
        paseo_port_base="+46000",
        paseo_port_end="46006",
        listener_port="45000",
    )

    assert result.returncode == 0, result.stderr
    assert "reason=listening on CONDUCTOR_PORT range port 45000" in result.stdout
    assert listener_marker in result.stdout
    assert scanned_ports == [str(port) for port in range(45000, 45010)]
    assert "PASEO port range" not in result.stdout
    assert "Conductor port range: 45000..45009" in result.stdout


def test_archive_workspace_unsets_malformed_paseo_ports_before_compose_teardown(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    scripts_dir = workspace / "scripts"
    scripts_dir.mkdir(parents=True)
    compose_env = tmp_path / "compose-env.txt"
    compose_script = scripts_dir / "docker-compose.sh"
    compose_script.write_text(
        """#!/bin/sh
printf 'PASEO_PORT_BASE=%s\\nPASEO_PORT_END=%s\\nCONDUCTOR_PORT=%s\\nARGS=%s\\n' \\
  \"${PASEO_PORT_BASE-}\" \"${PASEO_PORT_END-}\" \"${CONDUCTOR_PORT-}\" \"$*\" > \"$TEST_COMPOSE_ENV\"
""",
        encoding="utf-8",
    )
    compose_script.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "CONDUCTOR_WORKSPACE_PATH": str(workspace),
            "CONDUCTOR_PORT": "45000",
            "PASEO_PORT_BASE": "+46000",
            "PASEO_PORT_END": "46006",
            "TEST_COMPOSE_ENV": str(compose_env),
        }
    )
    result = subprocess.run(
        [str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert compose_env.read_text(encoding="utf-8").splitlines() == [
        "PASEO_PORT_BASE=",
        "PASEO_PORT_END=",
        "CONDUCTOR_PORT=45000",
        "ARGS=down --remove-orphans",
    ]
