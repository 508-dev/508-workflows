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
        f"api port {port} is already in use; "
        "install lsof for owner details or stop the existing listener and retry."
    )
