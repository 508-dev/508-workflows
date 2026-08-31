from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "archive-workspace.sh"


def test_archive_workspace_prefers_paseo_port_range() -> None:
    env = os.environ.copy()
    env["CONDUCTOR_PORT"] = "45000"
    env["PASEO_PORT_BASE"] = "46000"
    env["PASEO_PORT_END"] = "46006"

    result = subprocess.run(
        [str(SCRIPT_PATH), "--dry-run", "--skip-docker"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "PASEO port range: 46000..46006" in result.stdout
    assert "Conductor port range" not in result.stdout
