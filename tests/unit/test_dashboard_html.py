from pathlib import Path

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
