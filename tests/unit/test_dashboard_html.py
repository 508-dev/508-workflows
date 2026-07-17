from pathlib import Path

from fastapi.testclient import TestClient

from five08.backend import api
from five08.backend import dashboard


def test_dashboard_html_does_not_cache_missing_bundle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A bundle built after first request should be served without process restart."""
    static_dir = tmp_path / "dashboard"
    static_dir.mkdir()
    monkeypatch.setattr(dashboard, "DASHBOARD_STATIC_DIR", static_dir)

    fallback = dashboard.dashboard_html()
    assert "frontend bundle has not been built" in fallback

    (static_dir / "index.html").write_text(
        "<!doctype html><html><body><main>Built dashboard</main></body></html>",
        encoding="utf-8",
    )

    html = dashboard.dashboard_html()

    assert "Built dashboard" in html
    assert "frontend bundle has not been built" not in html
    assert "/dashboard/api/me" in html
    assert "/dashboard/payments" in html


def test_dashboard_assets_mount_serves_bundle_built_after_startup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    assets_dir = tmp_path / "dashboard" / "assets"
    monkeypatch.setattr(api, "dashboard_assets_dir", lambda: assets_dir)

    app = api.create_app(run_lifespan=False)
    client = TestClient(app)

    response = client.get("/dashboard/assets/index.js")
    assert response.status_code == 404

    assets_dir.mkdir(parents=True)
    (assets_dir / "index.js").write_text("console.log('built')\n", encoding="utf-8")

    response = client.get("/dashboard/assets/index.js")

    assert response.status_code == 200
    assert "console.log('built')" in response.text
