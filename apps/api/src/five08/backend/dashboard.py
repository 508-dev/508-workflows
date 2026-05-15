"""HTML shell for the operations dashboard."""

from __future__ import annotations

from pathlib import Path


DASHBOARD_STATIC_DIR = Path(__file__).with_name("static") / "dashboard"
DASHBOARD_ASSETS_DIR = DASHBOARD_STATIC_DIR / "assets"

_DASHBOARD_ROUTE_HINTS = """
<!-- Dashboard client route hints:
/dashboard/api/me
/dashboard/people
/dashboard/onboarding
/dashboard/jobs
/dashboard/agent
/dashboard/audit
-->
"""


def dashboard_assets_dir() -> Path:
    """Return the built dashboard assets directory."""
    return DASHBOARD_ASSETS_DIR


def login_required_html(*, oidc_configured: bool) -> str:
    """Return an unauthenticated dashboard recovery page."""
    sso_action = (
        '<a class="button primary" href="/auth/login?next=/dashboard">Continue with SSO</a>'
        if oidc_configured
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard Login Required</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1110;
      --panel: #181b19;
      --text: #eceee8;
      --muted: #9ca39a;
      --line: #343a33;
      --accent: #3fbfa8;
      --accent-strong: #61d9c5;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(560px, 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 28px;
      display: grid;
      gap: 16px;
    }}
    h1, p {{ margin: 0; }}
    h1 {{ font-size: 22px; letter-spacing: 0; }}
    p {{ color: var(--muted); line-height: 1.5; }}
    code {{
      color: var(--text);
      background: #222720;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 6px;
    }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .button {{
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      color: var(--text);
      text-decoration: none;
      font-weight: 800;
      font-size: 13px;
    }}
    .button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: #071512;
    }}
    .button:hover {{ border-color: var(--accent-strong); }}
  </style>
</head>
<body>
  <main>
    <h1>Log back in to the operations dashboard</h1>
    <p>Your dashboard session is missing or expired. In Discord, run <code>/dashboard-login</code> and open the new one-time link.</p>
    <div class="actions">
      {sso_action}
    </div>
  </main>
</body>
</html>
"""


def oidc_not_configured_html() -> str:
    """Return a browser-friendly page for environments without OIDC."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard SSO Not Configured</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1110;
      --panel: #181b19;
      --text: #eceee8;
      --muted: #9ca39a;
      --line: #343a33;
      --accent: #3fbfa8;
      --accent-strong: #61d9c5;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
    }
    main {
      width: min(640px, 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 28px;
      display: grid;
      gap: 16px;
    }
    h1, p { margin: 0; }
    h1 { font-size: 22px; letter-spacing: 0; }
    p { color: var(--muted); line-height: 1.5; }
    code {
      color: var(--text);
      background: #222720;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 6px;
    }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .button {
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #071512;
      padding: 8px 12px;
      text-decoration: none;
      font-weight: 800;
      font-size: 13px;
    }
    .button:hover { border-color: var(--accent-strong); background: var(--accent-strong); }
  </style>
</head>
<body>
  <main>
    <h1>SSO is not configured for this environment</h1>
    <p>The OIDC login route is disabled because this API does not have OIDC settings. In local development, run <code>/dashboard-login</code> in Discord and make sure <code>DISCORD_LINK_REQUIRE_OIDC_IDENTITY_CHECKS=false</code> is set.</p>
    <p>To enable SSO, configure <code>OIDC_ISSUER_URL</code>, <code>OIDC_CLIENT_ID</code>, and <code>OIDC_CLIENT_SECRET</code>.</p>
    <div class="actions">
      <a class="button" href="/dashboard">Back to dashboard</a>
    </div>
  </main>
</body>
</html>
"""


def dashboard_html() -> str:
    """Return the built dashboard HTML shell, or a temporary build-missing fallback."""
    index_path = DASHBOARD_STATIC_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        if "</body>" in html:
            return html.replace("</body>", f"{_DASHBOARD_ROUTE_HINTS}</body>")
        return f"{html}{_DASHBOARD_ROUTE_HINTS}"

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>508 Operations Dashboard</title>
</head>
<body>
  <main>
    <h1>508 Operations Dashboard</h1>
    <p>The dashboard frontend bundle has not been built. Run <code>bun run build</code> in <code>apps/admin_dashboard</code>.</p>
  </main>
  <!-- /dashboard/api/me /dashboard/people /dashboard/agent -->
</body>
</html>
"""
