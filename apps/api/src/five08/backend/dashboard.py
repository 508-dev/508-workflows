"""HTML shell for the admin dashboard."""

from __future__ import annotations


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
    <h1>Log back in to the admin dashboard</h1>
    <p>Your dashboard session is missing or expired. In Discord, run <code>/dashboard-login</code> and open the new one-time link.</p>
    <p>Discord links expire quickly, but dashboard sessions now last longer once you use a valid link.</p>
    <div class="actions">
      {sso_action}
    </div>
  </main>
</body>
</html>
"""


def dashboard_html() -> str:
    """Return the self-contained dashboard document."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>508 Admin Dashboard</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f1110;
      --panel: #181b19;
      --panel-2: #222720;
      --panel-3: #111411;
      --text: #eceee8;
      --muted: #9ca39a;
      --line: #343a33;
      --line-strong: #485046;
      --accent: #3fbfa8;
      --accent-strong: #61d9c5;
      --accent-muted: rgba(63, 191, 168, 0.16);
      --danger: #ff7b72;
      --danger-bg: rgba(248, 81, 73, 0.16);
      --ok: #56d364;
      --ok-bg: rgba(46, 160, 67, 0.17);
      --warn: #e3b341;
      --warn-bg: rgba(187, 128, 9, 0.2);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-width: 320px;
      background: var(--bg);
      color: var(--text);
    }
    header {
      border-bottom: 1px solid var(--line);
      background: rgba(15, 17, 16, 0.92);
      backdrop-filter: blur(16px);
    }
    .topbar {
      max-width: 1180px;
      margin: 0 auto;
      padding: 18px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
    }
    h1, h2, p { margin: 0; }
    h1 { font-size: 20px; font-weight: 700; letter-spacing: 0; }
    h2 { font-size: 15px; font-weight: 700; letter-spacing: 0; }
    .identity {
      display: grid;
      gap: 3px;
      min-width: 0;
      text-align: right;
      color: var(--muted);
      font-size: 13px;
    }
    .identity strong {
      color: var(--text);
      font-size: 14px;
      overflow-wrap: anywhere;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    main {
      max-width: 1280px;
      margin: 0 auto;
      padding: 22px;
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .sidebar {
      position: sticky;
      top: 16px;
      display: grid;
      gap: 6px;
    }
    .nav-link {
      display: block;
      border: 1px solid transparent;
      border-radius: 6px;
      color: var(--muted);
      padding: 10px 12px;
      text-decoration: none;
      font-size: 13px;
      font-weight: 800;
    }
    .nav-link:hover {
      border-color: var(--line);
      background: var(--panel-2);
      color: var(--text);
    }
    .nav-link[aria-current="page"] {
      border-color: var(--accent);
      background: var(--accent-muted);
      color: var(--accent-strong);
    }
    .content {
      min-width: 0;
      display: grid;
      gap: 18px;
    }
    .view {
      display: grid;
      gap: 18px;
    }
    .view[hidden] {
      display: none;
    }
    .toolbar {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      align-items: end;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 44px rgba(0, 0, 0, 0.22);
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 12px;
      font-weight: 700;
      color: var(--muted);
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-3);
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }
    input::placeholder { color: #6e7681; }
    input:focus, select:focus, button:focus-visible {
      outline: 2px solid var(--accent-strong);
      outline-offset: 2px;
      border-color: var(--accent);
    }
    button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--text);
      padding: 8px 12px;
      font: inherit;
      font-weight: 700;
      white-space: nowrap;
      cursor: pointer;
    }
    button:hover {
      border-color: var(--line-strong);
      background: #28302b;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #061412;
    }
    button.primary:hover {
      border-color: var(--accent-strong);
      background: var(--accent-strong);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
      display: grid;
      gap: 6px;
      box-shadow: 0 12px 34px rgba(0, 0, 0, 0.18);
    }
    .metric span { color: var(--muted); font-size: 12px; font-weight: 700; }
    .metric strong { font-size: 24px; letter-spacing: 0; }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: 0 18px 44px rgba(0, 0, 0, 0.22);
    }
    .panel-head {
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .status-line { color: var(--muted); font-size: 13px; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .jobs-table { table-layout: auto; }
    .jobs-table th:last-child,
    .jobs-table td:last-child {
      width: 168px;
      min-width: 168px;
    }
    th, td {
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    th {
      background: var(--panel-2);
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
    }
    tbody tr:hover { background: rgba(139, 148, 158, 0.08); }
    .compact td, .compact th { padding: 10px 12px; }
    tr:last-child td { border-bottom: 0; }
    .job-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #222821;
      color: var(--muted);
      font-weight: 800;
      font-size: 11px;
      text-transform: uppercase;
    }
    .badge.succeeded { border-color: rgba(86, 211, 100, 0.36); background: var(--ok-bg); color: var(--ok); }
    .badge.failed, .badge.dead { border-color: rgba(255, 123, 114, 0.4); background: var(--danger-bg); color: var(--danger); }
    .badge.running { border-color: rgba(227, 179, 65, 0.38); background: var(--warn-bg); color: var(--warn); }
    .badge.queued { border-color: rgba(47, 158, 143, 0.38); background: var(--accent-muted); color: var(--accent-strong); }
    .badge.missing { border-color: rgba(255, 123, 114, 0.4); background: var(--danger-bg); color: var(--danger); }
    .badge.neutral { border-color: var(--line); background: var(--panel-2); color: var(--muted); }
    .empty {
      padding: 28px 16px;
      color: var(--muted);
      text-align: center;
    }
    .toast {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }
    .toast.error { color: var(--danger); }
    .toast.ok { color: var(--ok); }
    .inline-link {
      color: var(--accent-strong);
      font-size: 13px;
      font-weight: 800;
      text-decoration: none;
    }
    .inline-link:hover { text-decoration: underline; }
    .actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 0.62fr);
      gap: 18px;
      align-items: start;
    }
    .search-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .filter-row {
      display: grid;
      grid-template-columns: minmax(150px, 0.8fr) minmax(160px, 1fr) minmax(160px, 1fr) auto;
      gap: 10px;
      align-items: end;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-3);
    }
    .active-filters {
      grid-column: 1 / -1;
      min-height: 28px;
    }
    .filter-chip {
      min-height: 28px;
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 12px;
    }
    .sort-button {
      min-height: 0;
      border: 0;
      background: transparent;
      color: inherit;
      padding: 0;
      font: inherit;
      font-weight: inherit;
      text-align: left;
    }
    .sort-button:hover {
      border-color: transparent;
      background: transparent;
      color: var(--text);
    }
    .sort-indicator {
      color: var(--accent-strong);
      margin-left: 4px;
    }
    .detail-body {
      padding: 16px;
      display: grid;
      gap: 14px;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .detail-item {
      display: grid;
      gap: 4px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel-3);
      min-width: 0;
    }
    .detail-item span {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .detail-item strong {
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .code-block {
      margin: 0;
      max-height: 260px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0b0d0c;
      color: var(--text);
      padding: 12px;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .chip-list {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    @media (max-width: 820px) {
      .topbar {
        align-items: start;
        flex-direction: column;
      }
      .header-actions {
        width: 100%;
        justify-content: space-between;
      }
      .identity { text-align: left; }
      main, .toolbar, .summary, .split, .detail-grid, .search-row, .filter-row { grid-template-columns: 1fr; }
      .sidebar {
        position: static;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .nav-link { text-align: center; }
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tr { border-bottom: 1px solid var(--line); }
      tr:last-child { border-bottom: 0; }
      td {
        border-bottom: 0;
        display: grid;
        grid-template-columns: 110px minmax(0, 1fr);
        gap: 10px;
      }
      td::before {
        content: attr(data-label);
        color: var(--muted);
        font-weight: 800;
      }
      .actions { justify-content: start; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>508 Admin Dashboard</h1>
        <p class="status-line">Operations view for authenticated admins.</p>
      </div>
      <div class="header-actions">
        <div class="identity" aria-live="polite">
          <strong id="userName">Loading user</strong>
          <span id="userMeta">Checking session</span>
        </div>
        <span id="toast" class="toast" role="status"></span>
        <button id="logout" type="button">Log out</button>
      </div>
    </div>
  </header>
  <main>
    <nav class="sidebar" aria-label="Dashboard sections">
      <a class="nav-link" data-view-link="people" href="/dashboard/people">People</a>
      <a class="nav-link" data-view-link="onboarding" href="/dashboard/onboarding">Onboarding</a>
      <a class="nav-link" data-view-link="jobs" href="/dashboard/jobs">Jobs</a>
      <a class="nav-link" data-view-link="audit" href="/dashboard/audit">Audit</a>
    </nav>
    <div class="content">
    <section id="view-onboarding" class="view" data-view="onboarding" hidden>
      <section class="panel">
        <div class="panel-head">
          <h2>Onboarding queue</h2>
          <span id="onboardingStatus" class="status-line"></span>
        </div>
        <div class="search-row">
          <label>
            Search prospects
            <input id="onboardingQuery" autocomplete="off" placeholder="Name, email, Discord, onboarder">
          </label>
          <button id="searchOnboarding" type="button">Search</button>
        </div>
        <div class="filter-row" aria-label="Onboarding filters">
          <label>
            Status
            <select id="onboardingState">
              <option value="">Any state</option>
              <option value="pending">Needs review</option>
              <option value="selected">Assigned to onboarder</option>
              <option value="reachingout">Reaching out</option>
              <option value="awaitingcontribution">Awaiting contribution</option>
            </select>
          </label>
          <label>
            Onboarder
            <input id="onboarderFilter" autocomplete="off" placeholder="Any onboarder">
          </label>
          <label>
            Add filter
            <select id="onboardingFilterKind"></select>
          </label>
          <label>
            Value
            <select id="onboardingFilterValue"></select>
          </label>
          <button id="addOnboardingFilter" type="button">Add filter</button>
          <div id="activeOnboardingFilters" class="chip-list active-filters" aria-label="Active onboarding filters"></div>
        </div>
        <div id="onboardingEmptyState" class="empty" hidden>No prospects match this queue view.</div>
        <table id="onboardingTable" class="compact" aria-label="Onboarding queue">
          <thead>
            <tr>
              <th style="width: 22%;"><button class="sort-button" data-sort-scope="onboarding" data-sort-key="name" type="button">Name</button></th>
              <th style="width: 14%;"><button class="sort-button" data-sort-scope="onboarding" data-sort-key="onboarding_state" type="button">Status</button></th>
              <th style="width: 16%;"><button class="sort-button" data-sort-scope="onboarding" data-sort-key="onboarder" type="button">Onboarder</button></th>
              <th style="width: 15%;"><button class="sort-button" data-sort-scope="onboarding" data-sort-key="updated" type="button">Updated</button></th>
              <th style="width: 16%;">Links</th>
              <th style="width: 17%;"><button class="sort-button" data-sort-scope="onboarding" data-sort-key="profile_gaps" type="button">Needs</button></th>
            </tr>
          </thead>
          <tbody id="onboardingBody"></tbody>
        </table>
      </section>
    </section>

    <section id="view-jobs" class="view" data-view="jobs">
    <section class="toolbar" aria-label="Job filters">
      <label>
        Window
        <select id="minutes">
          <option value="15">15 minutes</option>
          <option value="60" selected>1 hour</option>
          <option value="360">6 hours</option>
          <option value="1440">24 hours</option>
        </select>
      </label>
      <label>
        Status
        <select id="status">
          <option value="">Any status</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="dead">Dead</option>
          <option value="canceled">Canceled</option>
        </select>
      </label>
      <label>
        Type
        <input id="jobType" autocomplete="off" placeholder="Any type">
      </label>
      <button id="refreshJobs" class="primary" type="button">Refresh jobs</button>
    </section>

    <section class="summary" aria-label="Job summary">
      <div class="metric"><span>Total</span><strong id="metricTotal">0</strong></div>
      <div class="metric"><span>Queued</span><strong id="metricQueued">0</strong></div>
      <div class="metric"><span>Running</span><strong id="metricRunning">0</strong></div>
      <div class="metric"><span>Failed</span><strong id="metricFailed">0</strong></div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Recent jobs</h2>
      </div>
      <div id="emptyState" class="empty" hidden>No jobs match these filters.</div>
      <table id="jobsTable" class="jobs-table" aria-label="Recent jobs">
        <thead>
          <tr>
            <th style="width: 22%;"><button class="sort-button" data-sort-scope="jobs" data-sort-key="job_id" type="button">Job id</button></th>
            <th style="width: 24%;"><button class="sort-button" data-sort-scope="jobs" data-sort-key="type" type="button">Type</button></th>
            <th style="width: 12%;"><button class="sort-button" data-sort-scope="jobs" data-sort-key="status" type="button">Status</button></th>
            <th style="width: 12%;"><button class="sort-button" data-sort-scope="jobs" data-sort-key="attempts" type="button">Attempts</button></th>
            <th style="width: 18%;"><button class="sort-button" data-sort-scope="jobs" data-sort-key="updated_at" type="button">Updated</button></th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="jobsBody"></tbody>
      </table>
    </section>

    <section id="jobDetailPanel" class="panel" hidden>
      <div class="panel-head">
        <h2>Job detail</h2>
        <span id="jobDetailStatus" class="status-line"></span>
      </div>
      <div class="detail-body">
        <div id="jobDetailGrid" class="detail-grid"></div>
        <div>
          <h2>Payload</h2>
          <pre id="jobPayload" class="code-block"></pre>
        </div>
        <div>
          <h2>Result</h2>
          <pre id="jobResult" class="code-block"></pre>
        </div>
      </div>
    </section>
    </section>

    <section id="view-people" class="view" data-view="people">
      <section class="panel">
        <div class="panel-head">
          <h2>People lookup</h2>
          <div class="actions">
            <button id="syncPeople" type="button">Sync people</button>
            <a id="crmHomeLink" class="inline-link" href="#" target="_blank" rel="noreferrer" hidden>Open CRM</a>
            <span id="peopleStatus" class="status-line"></span>
          </div>
        </div>
        <div class="search-row">
          <label>
            Search CRM people cache
            <input id="peopleQuery" autocomplete="off" placeholder="Name, email, CRM id, Discord, resume">
          </label>
          <button id="searchPeople" type="button">Search</button>
        </div>
        <div class="filter-row" aria-label="People filters">
          <label>
            Member
            <select id="peopleMember">
              <option value="">Any</option>
              <option value="true">Member</option>
              <option value="false">Not member</option>
            </select>
          </label>
          <label>
            Add filter
            <select id="peopleFilterKind"></select>
          </label>
          <label>
            Value
            <select id="peopleFilterValue"></select>
          </label>
          <button id="addPeopleFilter" type="button">Add filter</button>
          <div id="activePeopleFilters" class="chip-list active-filters" aria-label="Active people filters"></div>
        </div>
        <div id="peopleEmptyState" class="empty" hidden>No people match this lookup.</div>
        <table id="peopleTable" class="compact" aria-label="People lookup results">
          <thead>
            <tr>
              <th style="width: 27%;"><button class="sort-button" data-sort-scope="people" data-sort-key="name" type="button">Name</button></th>
              <th style="width: 28%;"><button class="sort-button" data-sort-scope="people" data-sort-key="status" type="button">Status</button></th>
              <th style="width: 20%;"><button class="sort-button" data-sort-scope="people" data-sort-key="discord" type="button">Discord</button></th>
              <th style="width: 25%;"><button class="sort-button" data-sort-scope="people" data-sort-key="resume" type="button">Resume / skills</button></th>
            </tr>
          </thead>
          <tbody id="peopleBody"></tbody>
        </table>
      </section>
    </section>

    <section id="view-audit" class="view" data-view="audit" hidden>
      <section class="panel">
        <div class="panel-head">
          <h2>Recent audit</h2>
          <button id="refreshAudit" type="button">Refresh</button>
        </div>
        <div id="auditEmptyState" class="empty" hidden>No audit events found.</div>
        <table id="auditTable" class="compact" aria-label="Recent audit events">
          <thead>
            <tr>
              <th style="width: 24%;"><button class="sort-button" data-sort-scope="audit" data-sort-key="occurred_at" type="button">Time</button></th>
              <th style="width: 28%;"><button class="sort-button" data-sort-scope="audit" data-sort-key="actor" type="button">Actor</button></th>
              <th style="width: 28%;"><button class="sort-button" data-sort-scope="audit" data-sort-key="action" type="button">Action</button></th>
              <th style="width: 20%;"><button class="sort-button" data-sort-scope="audit" data-sort-key="result" type="button">Result</button></th>
            </tr>
          </thead>
          <tbody id="auditBody"></tbody>
        </table>
      </section>
    </section>
    </div>
  </main>
  <script>
    const routes = {
      onboarding: "/dashboard/onboarding",
      jobs: "/dashboard/jobs",
      people: "/dashboard/people",
      audit: "/dashboard/audit",
    };
    const state = {
      jobs: [],
      people: [],
      onboarding: [],
      auditEvents: [],
      crmBaseUrl: "",
      sort: {
        onboarding: { key: "onboarding_state", direction: "asc" },
        jobs: { key: "updated_at", direction: "desc" },
        people: { key: "name", direction: "asc" },
        audit: { key: "occurred_at", direction: "desc" },
      },
      onboardingFilters: {},
      peopleFilters: {},
    };
    const peopleFilterDefinitions = {
      discord: {
        label: "Discord",
        options: [
          ["linked", "Linked"],
          ["missing", "Missing"],
        ],
      },
      email_508: {
        label: "508 email",
        options: [
          ["present", "Present"],
          ["missing", "Missing"],
        ],
      },
      resume: {
        label: "Resume",
        options: [
          ["present", "Present"],
          ["missing", "Missing"],
        ],
      },
      skills: {
        label: "Skills",
        options: [
          ["present", "Parsed"],
          ["missing", "Not parsed"],
        ],
      },
      sync_status: {
        label: "Sync status",
        options: [
          ["active", "Active"],
          ["conflict", "Conflict"],
          ["missing_in_crm", "Missing in CRM"],
        ],
      },
    };
    const onboardingStateLabels = {
      pending: "Needs review",
      selected: "Assigned to onboarder",
      reachingout: "Reaching out",
      awaitingcontribution: "Awaiting contribution",
      onboarded: "Onboarded",
      waitlist: "Waitlist",
      rejected: "Rejected",
    };
    const els = {
      navLinks: document.querySelectorAll("[data-view-link]"),
      views: document.querySelectorAll("[data-view]"),
      userName: document.querySelector("#userName"),
      userMeta: document.querySelector("#userMeta"),
      minutes: document.querySelector("#minutes"),
      status: document.querySelector("#status"),
      jobType: document.querySelector("#jobType"),
      onboardingQuery: document.querySelector("#onboardingQuery"),
      onboardingState: document.querySelector("#onboardingState"),
      onboarderFilter: document.querySelector("#onboarderFilter"),
      onboardingFilterKind: document.querySelector("#onboardingFilterKind"),
      onboardingFilterValue: document.querySelector("#onboardingFilterValue"),
      addOnboardingFilter: document.querySelector("#addOnboardingFilter"),
      activeOnboardingFilters: document.querySelector("#activeOnboardingFilters"),
      searchOnboarding: document.querySelector("#searchOnboarding"),
      onboardingStatus: document.querySelector("#onboardingStatus"),
      onboardingBody: document.querySelector("#onboardingBody"),
      onboardingTable: document.querySelector("#onboardingTable"),
      onboardingEmptyState: document.querySelector("#onboardingEmptyState"),
      refreshJobs: document.querySelector("#refreshJobs"),
      syncPeople: document.querySelector("#syncPeople"),
      logout: document.querySelector("#logout"),
      toast: document.querySelector("#toast"),
      jobsBody: document.querySelector("#jobsBody"),
      jobsTable: document.querySelector("#jobsTable"),
      emptyState: document.querySelector("#emptyState"),
      metricTotal: document.querySelector("#metricTotal"),
      metricQueued: document.querySelector("#metricQueued"),
      metricRunning: document.querySelector("#metricRunning"),
      metricFailed: document.querySelector("#metricFailed"),
      jobDetailPanel: document.querySelector("#jobDetailPanel"),
      jobDetailStatus: document.querySelector("#jobDetailStatus"),
      jobDetailGrid: document.querySelector("#jobDetailGrid"),
      jobPayload: document.querySelector("#jobPayload"),
      jobResult: document.querySelector("#jobResult"),
      peopleQuery: document.querySelector("#peopleQuery"),
      peopleMember: document.querySelector("#peopleMember"),
      peopleFilterKind: document.querySelector("#peopleFilterKind"),
      peopleFilterValue: document.querySelector("#peopleFilterValue"),
      addPeopleFilter: document.querySelector("#addPeopleFilter"),
      activePeopleFilters: document.querySelector("#activePeopleFilters"),
      searchPeople: document.querySelector("#searchPeople"),
      peopleStatus: document.querySelector("#peopleStatus"),
      crmHomeLink: document.querySelector("#crmHomeLink"),
      peopleBody: document.querySelector("#peopleBody"),
      peopleTable: document.querySelector("#peopleTable"),
      peopleEmptyState: document.querySelector("#peopleEmptyState"),
      refreshAudit: document.querySelector("#refreshAudit"),
      auditBody: document.querySelector("#auditBody"),
      auditTable: document.querySelector("#auditTable"),
      auditEmptyState: document.querySelector("#auditEmptyState"),
    };

    function setToast(message, tone = "") {
      els.toast.textContent = message;
      els.toast.className = `toast ${tone}`.trim();
    }

    async function requestJson(url, options = {}) {
      const headers = new Headers(options.headers || {});
      headers.set("Accept", "application/json");
      const response = await fetch(url, {
        credentials: "same-origin",
        ...options,
        headers,
      });
      if (response.status === 401) {
        window.location.assign("/dashboard");
        throw new Error("Session expired");
      }
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const payload = await response.json();
          detail = payload.detail || payload.error || detail;
        } catch (error) {
          detail = response.statusText;
        }
        throw new Error(detail);
      }
      return response.json();
    }

    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    function jsonPreview(value) {
      if (value === null || value === undefined) return "";
      return JSON.stringify(value, null, 2);
    }

    function addTextCell(row, label, value, className = "") {
      const cell = document.createElement("td");
      cell.dataset.label = label;
      cell.textContent = value || "";
      if (className) cell.className = className;
      row.appendChild(cell);
      return cell;
    }

    function createBadge(text, className = "neutral") {
      const badge = document.createElement("span");
      badge.className = `badge ${className}`;
      badge.textContent = text;
      return badge;
    }

    function rawViewFromPath() {
      const parts = window.location.pathname.split("/").filter(Boolean);
      return parts[1] || "";
    }

    function viewFromPath() {
      const view = rawViewFromPath();
      return Object.prototype.hasOwnProperty.call(routes, view) ? view : "people";
    }

    function setView(view, options = {}) {
      const normalizedView = Object.prototype.hasOwnProperty.call(routes, view) ? view : "people";
      for (const section of els.views) {
        section.hidden = section.dataset.view !== normalizedView;
      }
      for (const link of els.navLinks) {
        if (link.dataset.viewLink === normalizedView) {
          link.setAttribute("aria-current", "page");
        } else {
          link.removeAttribute("aria-current");
        }
      }
      if (options.push) {
        window.history.pushState({ view: normalizedView }, "", routes[normalizedView]);
      } else if (!Object.prototype.hasOwnProperty.call(routes, rawViewFromPath())) {
        window.history.replaceState({ view: normalizedView }, "", routes[normalizedView]);
      }
      if (normalizedView === "onboarding") loadOnboarding();
      if (normalizedView === "jobs") loadJobs();
      if (normalizedView === "people") loadPeople();
      if (normalizedView === "audit") loadAuditEvents();
    }

    function crmContactUrl(contactId) {
      if (!state.crmBaseUrl || !contactId) return "";
      return `${state.crmBaseUrl}/#Contact/view/${encodeURIComponent(contactId)}`;
    }

    function crmAttachmentUrl(attachmentId) {
      if (!state.crmBaseUrl || !attachmentId) return "";
      return `${state.crmBaseUrl}/api/v1/Attachment/file/${encodeURIComponent(attachmentId)}`;
    }

    function urlWithProtocol(value) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      if (/^https?:\\/\\//i.test(raw)) return raw;
      return `https://${raw.replace(/^\\/+/, "")}`;
    }

    function linkedinUrl(value) {
      const raw = String(value || "").trim();
      if (!raw) return "";
      if (raw.toLowerCase().includes("linkedin.com")) return urlWithProtocol(raw);
      return `https://www.linkedin.com/in/${encodeURIComponent(raw.replace(/^@/, ""))}`;
    }

    function githubUrl(value) {
      const raw = String(value || "").trim().replace(/^@/, "");
      if (!raw) return "";
      if (raw.toLowerCase().includes("github.com")) return urlWithProtocol(raw);
      if (/^https?:\\/\\//i.test(raw)) return "";
      return `https://github.com/${encodeURIComponent(raw)}`;
    }

    function appendInlineLink(cell, label, url, ariaLabel) {
      if (!url) return false;
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.className = "inline-link";
      link.textContent = label;
      if (ariaLabel) link.setAttribute("aria-label", ariaLabel);
      cell.appendChild(link);
      return true;
    }

    function onboardingStateValue(person) {
      return person.onboarding_state || person.onboardingState || person.cOnboardingState || "";
    }

    function labelForOnboardingState(value) {
      const raw = String(value || "").trim();
      if (!raw) return "No status";
      const normalized = raw.toLowerCase();
      if (onboardingStateLabels[normalized]) return onboardingStateLabels[normalized];
      return raw
        .replace(/[-_]+/g, " ")
        .replace(/\\s+/g, " ")
        .trim()
        .replace(/\\b\\w/g, (character) => character.toUpperCase());
    }

    function toneForOnboardingState(value) {
      const normalized = String(value || "").trim().toLowerCase();
      if (!normalized) return "neutral";
      if (normalized === "pending") return "neutral";
      if (normalized === "selected") return "queued";
      if (normalized === "rejected") return "failed";
      if (normalized === "onboarded") return "succeeded";
      if (normalized === "waitlist") return "running";
      return "queued";
    }

    function valueForSort(scope, item, key) {
      if (scope === "onboarding") {
        const status = item.profile_status || {};
        if (key === "name") return item.name || item.email_508 || item.email || "";
        if (key === "onboarding_state") {
          const state = String(onboardingStateValue(item));
          return state.toLowerCase() === "pending" ? `zzz-${state}` : state;
        }
        if (key === "onboarder") return item.onboarder || "";
        if (key === "updated") return item.onboarding_updated_at || "";
        if (key === "profile_gaps") {
          return [
            !status.discord_linked,
            !status.latest_resume,
            Number(status.skills_count || 0) <= 0,
          ].filter(Boolean).length;
        }
      }
      if (scope === "people") {
        const status = item.profile_status || {};
        if (key === "name") return item.name || item.email_508 || item.email || "";
        if (key === "status") {
          return [
            status.crm_active,
            status.is_member,
            status.discord_linked,
            status.email_508,
            status.latest_resume,
          ].filter(Boolean).length;
        }
        if (key === "discord") return item.discord_username || item.discord_user_id || "";
        if (key === "resume") return item.latest_resume_name || item.latest_resume_id || "";
        if (key === "skills") return Number(status.skills_count || 0);
      }
      if (scope === "audit") {
        if (key === "actor") return item.actor_display_name || item.actor_subject || item.actor_provider || "";
      }
      return item[key] ?? "";
    }

    function sortItems(scope, items) {
      const { key, direction } = state.sort[scope];
      const multiplier = direction === "asc" ? 1 : -1;
      return [...items].sort((a, b) => {
        const left = valueForSort(scope, a, key);
        const right = valueForSort(scope, b, key);
        if (typeof left === "number" && typeof right === "number") {
          return (left - right) * multiplier;
        }
        return String(left).localeCompare(String(right), undefined, { numeric: true }) * multiplier;
      });
    }

    function setSort(scope, key) {
      const current = state.sort[scope];
      const direction = current.key === key && current.direction === "asc" ? "desc" : "asc";
      state.sort[scope] = { key, direction };
      updateSortIndicators(scope);
      if (scope === "onboarding") renderOnboarding();
      if (scope === "jobs") renderJobs();
      if (scope === "people") renderPeople();
      if (scope === "audit") renderAuditEvents();
    }

    function updateSortIndicators(scope) {
      for (const button of document.querySelectorAll(`[data-sort-scope="${scope}"]`)) {
        const baseLabel = button.dataset.sortLabel || button.textContent.trim();
        button.dataset.sortLabel = baseLabel.replace(/ [↑↓]$/, "");
        const active = button.dataset.sortKey === state.sort[scope].key;
        const arrow = state.sort[scope].direction === "asc" ? "↑" : "↓";
        button.textContent = active ? `${button.dataset.sortLabel} ${arrow}` : button.dataset.sortLabel;
        button.setAttribute("aria-sort", active ? state.sort[scope].direction : "none");
      }
    }

    function labelForPeopleFilter(key, value) {
      const definition = peopleFilterDefinitions[key];
      const option = definition.options.find(([candidate]) => candidate === value);
      return `${definition.label}: ${option ? option[1] : value}`;
    }

    function renderPeopleFilterOptions() {
      els.peopleFilterKind.replaceChildren();
      for (const [key, definition] of Object.entries(peopleFilterDefinitions)) {
        if (state.peopleFilters[key]) continue;
        const option = document.createElement("option");
        option.value = key;
        option.textContent = definition.label;
        els.peopleFilterKind.appendChild(option);
      }
      renderPeopleFilterValues();
      els.addPeopleFilter.disabled = els.peopleFilterKind.options.length === 0;
    }

    function renderPeopleFilterValues() {
      els.peopleFilterValue.replaceChildren();
      const definition = peopleFilterDefinitions[els.peopleFilterKind.value];
      if (!definition) return;
      for (const [value, label] of definition.options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        els.peopleFilterValue.appendChild(option);
      }
    }

    function renderActivePeopleFilters() {
      els.activePeopleFilters.replaceChildren();
      for (const [key, value] of Object.entries(state.peopleFilters)) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "filter-chip";
        chip.textContent = `${labelForPeopleFilter(key, value)} x`;
        chip.setAttribute("aria-label", `Remove ${labelForPeopleFilter(key, value)} filter`);
        chip.addEventListener("click", () => {
          delete state.peopleFilters[key];
          renderActivePeopleFilters();
          renderPeopleFilterOptions();
          loadPeople();
        });
        els.activePeopleFilters.appendChild(chip);
      }
    }

    function addPeopleFilter() {
      const key = els.peopleFilterKind.value;
      const value = els.peopleFilterValue.value;
      if (!key || !value) return;
      state.peopleFilters[key] = value;
      renderActivePeopleFilters();
      renderPeopleFilterOptions();
      loadPeople();
    }

    function renderOnboardingFilterOptions() {
      els.onboardingFilterKind.replaceChildren();
      for (const [key, definition] of Object.entries(peopleFilterDefinitions)) {
        if (key === "sync_status" || key === "email_508" || state.onboardingFilters[key]) continue;
        const option = document.createElement("option");
        option.value = key;
        option.textContent = definition.label;
        els.onboardingFilterKind.appendChild(option);
      }
      renderOnboardingFilterValues();
      els.addOnboardingFilter.disabled = els.onboardingFilterKind.options.length === 0;
    }

    function renderOnboardingFilterValues() {
      els.onboardingFilterValue.replaceChildren();
      const definition = peopleFilterDefinitions[els.onboardingFilterKind.value];
      if (!definition) return;
      for (const [value, label] of definition.options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        els.onboardingFilterValue.appendChild(option);
      }
    }

    function renderActiveOnboardingFilters() {
      els.activeOnboardingFilters.replaceChildren();
      for (const [key, value] of Object.entries(state.onboardingFilters)) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "filter-chip";
        chip.textContent = `${labelForPeopleFilter(key, value)} x`;
        chip.setAttribute("aria-label", `Remove ${labelForPeopleFilter(key, value)} onboarding filter`);
        chip.addEventListener("click", () => {
          delete state.onboardingFilters[key];
          renderActiveOnboardingFilters();
          renderOnboardingFilterOptions();
          loadOnboarding();
        });
        els.activeOnboardingFilters.appendChild(chip);
      }
    }

    function addOnboardingFilter() {
      const key = els.onboardingFilterKind.value;
      const value = els.onboardingFilterValue.value;
      if (!key || !value) return;
      state.onboardingFilters[key] = value;
      renderActiveOnboardingFilters();
      renderOnboardingFilterOptions();
      loadOnboarding();
    }

    function updateMetrics() {
      const counts = state.jobs.reduce((acc, job) => {
        acc[job.status] = (acc[job.status] || 0) + 1;
        return acc;
      }, {});
      els.metricTotal.textContent = String(state.jobs.length);
      els.metricQueued.textContent = String(counts.queued || 0);
      els.metricRunning.textContent = String(counts.running || 0);
      els.metricFailed.textContent = String((counts.failed || 0) + (counts.dead || 0));
    }

    function renderJobs() {
      els.jobsBody.replaceChildren();
      els.emptyState.hidden = state.jobs.length !== 0;
      els.jobsTable.hidden = state.jobs.length === 0;

      for (const job of sortItems("jobs", state.jobs)) {
        const row = document.createElement("tr");
        addTextCell(row, "Job id", job.job_id, "job-id");
        addTextCell(row, "Type", job.type);
        const statusCell = addTextCell(row, "Status", "");
        statusCell.appendChild(createBadge(job.status, job.status));
        addTextCell(row, "Attempts", `${job.attempts}/${job.max_attempts}`);
        addTextCell(row, "Updated", formatDate(job.updated_at));

        const actionCell = document.createElement("td");
        actionCell.dataset.label = "Actions";
        const actions = document.createElement("div");
        actions.className = "actions";
        const details = document.createElement("button");
        details.type = "button";
        details.textContent = "Details";
        details.setAttribute("aria-label", `View details for ${job.type} job ${job.job_id}`);
        details.addEventListener("click", () => loadJobDetail(job.job_id, details));
        const rerun = document.createElement("button");
        rerun.type = "button";
        rerun.textContent = "Rerun";
        rerun.setAttribute("aria-label", `Rerun ${job.type} job ${job.job_id}`);
        rerun.dataset.jobId = job.job_id;
        rerun.addEventListener("click", () => rerunJob(job.job_id, rerun));
        actions.appendChild(details);
        actions.appendChild(rerun);
        actionCell.appendChild(actions);
        row.appendChild(actionCell);
        els.jobsBody.appendChild(row);
      }
      updateMetrics();
    }

    function renderJobDetail(detail) {
      els.jobDetailPanel.hidden = false;
      els.jobDetailStatus.textContent = detail.job_id || "";
      els.jobDetailGrid.replaceChildren();
      const fields = [
        ["Type", detail.type],
        ["Status", detail.status],
        ["Attempts", `${detail.attempts}/${detail.max_attempts}`],
        ["Updated", formatDate(detail.updated_at)],
        ["Created", formatDate(detail.created_at)],
        ["Run after", formatDate(detail.run_after)],
        ["Locked by", detail.locked_by],
        ["Last error", detail.last_error],
      ];
      for (const [label, value] of fields) {
        const item = document.createElement("div");
        item.className = "detail-item";
        const name = document.createElement("span");
        name.textContent = label;
        const content = document.createElement("strong");
        content.textContent = value || "None";
        item.appendChild(name);
        item.appendChild(content);
        els.jobDetailGrid.appendChild(item);
      }
      els.jobPayload.textContent = jsonPreview(detail.payload) || "No payload";
      els.jobResult.textContent = jsonPreview(detail.result) || "No result";
      els.jobDetailPanel.scrollIntoView({ block: "nearest" });
    }

    function renderOnboarding() {
      els.onboardingBody.replaceChildren();
      els.onboardingEmptyState.hidden = state.onboarding.length !== 0;
      els.onboardingTable.hidden = state.onboarding.length === 0;

      for (const person of sortItems("onboarding", state.onboarding)) {
        const row = document.createElement("tr");
        const nameCell = addTextCell(row, "Name", "");
        const contactUrl = crmContactUrl(person.crm_contact_id);
        const displayName = person.name || person.email_508 || person.email || "CRM contact";
        const nameLink = document.createElement(contactUrl ? "a" : "strong");
        if (contactUrl) {
          nameLink.href = contactUrl;
          nameLink.target = "_blank";
          nameLink.rel = "noreferrer";
          nameLink.className = "inline-link";
          nameLink.setAttribute("aria-label", `Open ${displayName} in CRM`);
        }
        nameLink.textContent = displayName;
        const meta = document.createElement("div");
        meta.className = "status-line";
        meta.textContent = person.email || person.email_508 || "";
        nameCell.appendChild(nameLink);
        nameCell.appendChild(meta);

        const stateValue = onboardingStateValue(person);
        const stateText = person.onboarding_status_label || labelForOnboardingState(stateValue);
        const stateCell = addTextCell(row, "Status", "");
        stateCell.appendChild(createBadge(stateText, toneForOnboardingState(stateValue)));
        addTextCell(row, "Onboarder", person.onboarder || "Unassigned");
        addTextCell(row, "Updated", formatDate(person.onboarding_updated_at));

        const linksCell = addTextCell(row, "Links", "");
        linksCell.className = "chip-list";
        const resumeUrl = crmAttachmentUrl(person.latest_resume_id);
        const linkCount = [
          appendInlineLink(linksCell, "Resume", resumeUrl, `Open ${displayName} resume`),
          appendInlineLink(linksCell, "LinkedIn", linkedinUrl(person.linkedin), `Open ${displayName} LinkedIn`),
          appendInlineLink(linksCell, person.github_username || "GitHub", githubUrl(person.github_username), `Open ${displayName} GitHub`),
        ].filter(Boolean).length;
        if (linkCount === 0) linksCell.textContent = "None";

        const needsCell = addTextCell(row, "Needs", "");
        needsCell.className = "chip-list";
        const status = person.profile_status || {};
        const skillsParsed = Number(status.skills_count || 0) > 0;
        const gaps = [
          ["Discord", status.discord_linked],
          ["Resume", status.latest_resume],
          ["Skills", skillsParsed],
        ].filter(([, ok]) => !ok);
        for (const [label] of gaps) {
          needsCell.appendChild(createBadge(`Missing ${label}`, "missing"));
        }
        if (gaps.length === 0) needsCell.textContent = "None";
        els.onboardingBody.appendChild(row);
      }
    }

    function renderPeople() {
      els.peopleBody.replaceChildren();
      els.peopleEmptyState.hidden = state.people.length !== 0;
      els.peopleTable.hidden = state.people.length === 0;

      for (const person of sortItems("people", state.people)) {
        const row = document.createElement("tr");
        const personCell = addTextCell(row, "Person", "");
        const contactUrl = crmContactUrl(person.crm_contact_id);
        const personName = document.createElement(contactUrl ? "a" : "strong");
        const displayName = person.name || person.email_508 || person.email || "CRM contact";
        if (contactUrl) {
          personName.href = contactUrl;
          personName.target = "_blank";
          personName.rel = "noreferrer";
          personName.className = "inline-link";
          personName.setAttribute("aria-label", `Open ${displayName} in CRM`);
        }
        personName.textContent = displayName;
        const meta = document.createElement("div");
        meta.className = "status-line";
        meta.textContent = [person.email_508 || person.email, person.contact_type]
          .filter(Boolean)
          .join(" | ");
        personCell.appendChild(personName);
        personCell.appendChild(meta);

        const statusCell = addTextCell(row, "Status", "");
        statusCell.className = "chip-list";
        const status = person.profile_status || {};
        const checks = [
          ["Member", status.is_member],
          ["Discord", status.discord_linked],
          ["508 email", status.email_508],
        ];
        if (!status.crm_active) {
          statusCell.appendChild(createBadge(person.sync_status || "CRM sync issue", "missing"));
        }
        for (const [label, ok] of checks) {
          statusCell.appendChild(createBadge(ok ? label : `Missing ${label}`, ok ? "succeeded" : "missing"));
        }
        if (!status.latest_resume) {
          statusCell.appendChild(createBadge("Missing Resume", "missing"));
        }

        const discord = [person.discord_username, person.discord_user_id]
          .filter(Boolean)
          .join(" | ");
        addTextCell(row, "Discord", discord || "Not linked");

        const resumeCell = addTextCell(row, "Resume / skills", "");
        const resume = person.latest_resume_name || person.latest_resume_id || "No resume";
        const skillsCount = Number(status.skills_count || 0);
        const resumeUrl = crmAttachmentUrl(person.latest_resume_id);
        if (resumeUrl) {
          const resumeLink = document.createElement("a");
          resumeLink.href = resumeUrl;
          resumeLink.target = "_blank";
          resumeLink.rel = "noreferrer";
          resumeLink.className = "inline-link";
          resumeLink.textContent = "Resume";
          resumeLink.setAttribute("aria-label", `Open ${displayName} resume`);
          resumeCell.appendChild(resumeLink);
        } else {
          resumeCell.textContent = resume;
        }
        resumeCell.append(" ");
        resumeCell.appendChild(createBadge(skillsCount > 0 ? "Skills parsed" : "Skills not parsed", skillsCount > 0 ? "succeeded" : "missing"));
        els.peopleBody.appendChild(row);
      }
    }

    function renderAuditEvents() {
      els.auditBody.replaceChildren();
      els.auditEmptyState.hidden = state.auditEvents.length !== 0;
      els.auditTable.hidden = state.auditEvents.length === 0;

      for (const event of sortItems("audit", state.auditEvents)) {
        const row = document.createElement("tr");
        addTextCell(row, "Time", formatDate(event.occurred_at));
        addTextCell(
          row,
          "Actor",
          event.actor_display_name || event.actor_subject || event.actor_provider
        );
        addTextCell(row, "Action", event.action);
        const resultCell = addTextCell(row, "Result", "");
        resultCell.appendChild(createBadge(event.result, event.result === "success" ? "succeeded" : "failed"));
        els.auditBody.appendChild(row);
      }
    }

    function jobsUrl() {
      const params = new URLSearchParams({
        minutes: els.minutes.value,
        limit: "100",
      });
      if (els.status.value) params.set("status", els.status.value);
      const jobType = els.jobType.value.trim();
      if (jobType) params.set("type", jobType);
      return `/dashboard/api/jobs?${params.toString()}`;
    }

    async function loadUser() {
      const user = await requestJson("/dashboard/api/me");
      state.crmBaseUrl = (user.crm_base_url || "").replace(/\\/+$/, "");
      if (state.crmBaseUrl) {
        els.crmHomeLink.href = state.crmBaseUrl;
        els.crmHomeLink.hidden = false;
      }
      els.userName.textContent = user.display_name || user.email || user.subject;
      const pieces = [];
      if (user.email) pieces.push(user.email);
      if (user.crm_contact_id) pieces.push(`CRM ${user.crm_contact_id}`);
      if (user.actor_provider) pieces.push(user.actor_provider);
      els.userMeta.textContent = pieces.join(" | ");
    }

    async function loadJobs() {
      els.refreshJobs.disabled = true;
      setToast("Loading jobs");
      try {
        state.jobs = await requestJson(jobsUrl());
        renderJobs();
        setToast(`Loaded ${state.jobs.length} jobs`, "ok");
      } catch (error) {
        setToast(error.message || "Unable to load jobs", "error");
      } finally {
        els.refreshJobs.disabled = false;
      }
    }

    async function loadJobDetail(jobId, button) {
      button.disabled = true;
      setToast(`Loading ${jobId}`);
      try {
        const detail = await requestJson(`/dashboard/api/jobs/${encodeURIComponent(jobId)}`);
        renderJobDetail(detail);
        setToast(`Loaded ${jobId}`, "ok");
      } catch (error) {
        setToast(error.message || "Unable to load job detail", "error");
      } finally {
        button.disabled = false;
      }
    }

    function onboardingUrl() {
      const params = new URLSearchParams({ limit: "25" });
      const query = els.onboardingQuery.value.trim();
      if (query) params.set("query", query);
      const onboardingState = els.onboardingState.value.trim();
      if (onboardingState) params.set("onboarding_state", onboardingState);
      const onboarder = els.onboarderFilter.value.trim();
      if (onboarder) params.set("onboarder", onboarder);
      for (const [key, value] of Object.entries(state.onboardingFilters)) {
        params.set(key, value);
      }
      return `/dashboard/api/onboarding?${params.toString()}`;
    }

    async function loadOnboarding() {
      els.searchOnboarding.disabled = true;
      els.onboardingStatus.textContent = "Loading";
      try {
        state.onboarding = await requestJson(onboardingUrl());
        renderOnboarding();
        els.onboardingStatus.textContent = `${state.onboarding.length} shown`;
      } catch (error) {
        els.onboardingStatus.textContent = error.message || "Unable to load onboarding";
      } finally {
        els.searchOnboarding.disabled = false;
      }
    }

    function peopleUrl() {
      const params = new URLSearchParams({ limit: "25" });
      const query = els.peopleQuery.value.trim();
      if (query) params.set("query", query);
      if (els.peopleMember.value) params.set("is_member", els.peopleMember.value);
      for (const [key, value] of Object.entries(state.peopleFilters)) {
        params.set(key, value);
      }
      return `/dashboard/api/people?${params.toString()}`;
    }

    async function loadPeople() {
      els.searchPeople.disabled = true;
      els.peopleStatus.textContent = "Loading";
      try {
        state.people = await requestJson(peopleUrl());
        renderPeople();
        els.peopleStatus.textContent = `${state.people.length} shown`;
      } catch (error) {
        els.peopleStatus.textContent = error.message || "Unable to load people";
      } finally {
        els.searchPeople.disabled = false;
      }
    }

    async function loadAuditEvents() {
      els.refreshAudit.disabled = true;
      try {
        state.auditEvents = await requestJson("/dashboard/api/audit-events?limit=25");
        renderAuditEvents();
      } catch (error) {
        setToast(error.message || "Unable to load audit events", "error");
      } finally {
        els.refreshAudit.disabled = false;
      }
    }

    async function rerunJob(jobId, button) {
      button.disabled = true;
      setToast(`Rerunning ${jobId}`);
      try {
        const payload = await requestJson(`/dashboard/api/jobs/${encodeURIComponent(jobId)}/rerun`, {
          method: "POST",
        });
        setToast(`Queued rerun ${payload.job_id}`, "ok");
        await loadJobs();
      } catch (error) {
        setToast(error.message || "Unable to rerun job", "error");
      } finally {
        button.disabled = false;
      }
    }

    async function syncPeople() {
      els.syncPeople.disabled = true;
      setToast("Queueing people sync");
      try {
        const payload = await requestJson("/dashboard/api/sync/people", { method: "POST" });
        setToast(`Queued people sync ${payload.job_id}`, "ok");
      } catch (error) {
        setToast(error.message || "Unable to queue people sync", "error");
      } finally {
        els.syncPeople.disabled = false;
      }
    }

    async function logout() {
      els.logout.disabled = true;
      try {
        const payload = await requestJson("/auth/logout", { method: "POST" });
        if (payload.end_session_url) {
          window.location.assign(payload.end_session_url);
        } else {
          window.location.assign("/dashboard");
        }
      } catch (error) {
        setToast(error.message || "Unable to log out", "error");
        els.logout.disabled = false;
      }
    }

    els.refreshJobs.addEventListener("click", loadJobs);
    els.syncPeople.addEventListener("click", syncPeople);
    els.logout.addEventListener("click", logout);
    els.searchOnboarding.addEventListener("click", loadOnboarding);
    els.searchPeople.addEventListener("click", loadPeople);
    els.refreshAudit.addEventListener("click", loadAuditEvents);
    els.onboardingFilterKind.addEventListener("change", renderOnboardingFilterValues);
    els.addOnboardingFilter.addEventListener("click", addOnboardingFilter);
    els.peopleFilterKind.addEventListener("change", renderPeopleFilterValues);
    els.addPeopleFilter.addEventListener("click", addPeopleFilter);
    for (const button of document.querySelectorAll("[data-sort-scope]")) {
      button.addEventListener("click", () => setSort(button.dataset.sortScope, button.dataset.sortKey));
    }
    updateSortIndicators("onboarding");
    updateSortIndicators("jobs");
    updateSortIndicators("people");
    updateSortIndicators("audit");
    for (const link of els.navLinks) {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        setView(link.dataset.viewLink, { push: true });
      });
    }
    window.addEventListener("popstate", () => setView(viewFromPath()));
    els.status.addEventListener("change", loadJobs);
    els.minutes.addEventListener("change", loadJobs);
    els.jobType.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadJobs();
    });
    els.onboardingQuery.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadOnboarding();
    });
    els.onboardingState.addEventListener("change", loadOnboarding);
    els.onboarderFilter.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadOnboarding();
    });
    els.peopleQuery.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadPeople();
    });
    els.peopleMember.addEventListener("change", loadPeople);

    renderOnboardingFilterOptions();
    renderPeopleFilterOptions();
    loadUser().then(() => {
      setView(viewFromPath());
    }).catch((error) => {
      setToast(error.message || "Dashboard failed to load", "error");
    });
  </script>
</body>
</html>
"""
