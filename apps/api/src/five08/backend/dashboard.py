"""HTML shell for the admin dashboard."""

from __future__ import annotations


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
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-2: #f0f4f8;
      --text: #17202a;
      --muted: #607080;
      --line: #d9e1e8;
      --accent: #1d6f5f;
      --accent-strong: #145346;
      --danger: #b42318;
      --ok: #15703b;
      --warn: #a15c00;
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
      background: var(--panel);
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
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 22px;
      display: grid;
      gap: 18px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      align-items: end;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
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
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }
    button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 12px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button.primary:hover { background: var(--accent-strong); }
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
    }
    .metric span { color: var(--muted); font-size: 12px; font-weight: 700; }
    .metric strong { font-size: 24px; letter-spacing: 0; }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
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
    tr:last-child td { border-bottom: 0; }
    .job-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #edf2f7;
      color: var(--muted);
      font-weight: 800;
      font-size: 11px;
      text-transform: uppercase;
    }
    .badge.succeeded { background: #e6f4ec; color: var(--ok); }
    .badge.failed, .badge.dead { background: #fcebea; color: var(--danger); }
    .badge.running { background: #fff4de; color: var(--warn); }
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
    .actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    @media (max-width: 820px) {
      .topbar {
        align-items: start;
        flex-direction: column;
      }
      .identity { text-align: left; }
      .toolbar, .summary { grid-template-columns: 1fr; }
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
        <p class="status-line">Operations view for authenticated Discord admins.</p>
      </div>
      <div class="identity" aria-live="polite">
        <strong id="userName">Loading user</strong>
        <span id="userMeta">Checking session</span>
      </div>
    </div>
  </header>
  <main>
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
      <button id="syncPeople" type="button">Sync people</button>
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
        <div class="actions">
          <span id="toast" class="toast" role="status"></span>
          <button id="logout" type="button">Log out</button>
        </div>
      </div>
      <div id="emptyState" class="empty" hidden>No jobs match these filters.</div>
      <table id="jobsTable" aria-label="Recent jobs">
        <thead>
          <tr>
            <th style="width: 24%;">Job id</th>
            <th style="width: 26%;">Type</th>
            <th style="width: 12%;">Status</th>
            <th style="width: 12%;">Attempts</th>
            <th style="width: 17%;">Updated</th>
            <th style="width: 9%;">Action</th>
          </tr>
        </thead>
        <tbody id="jobsBody"></tbody>
      </table>
    </section>
  </main>
  <script>
    const state = { jobs: [] };
    const els = {
      userName: document.querySelector("#userName"),
      userMeta: document.querySelector("#userMeta"),
      minutes: document.querySelector("#minutes"),
      status: document.querySelector("#status"),
      jobType: document.querySelector("#jobType"),
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
    };

    function setToast(message, tone = "") {
      els.toast.textContent = message;
      els.toast.className = `toast ${tone}`.trim();
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "Accept": "application/json", ...(options.headers || {}) },
        ...options,
      });
      if (response.status === 401) {
        window.location.assign(`/auth/login?next=${encodeURIComponent("/dashboard")}`);
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

      for (const job of state.jobs) {
        const row = document.createElement("tr");
        const cells = [
          ["Job id", job.job_id, "job-id"],
          ["Type", job.type, ""],
          ["Status", job.status, "status"],
          ["Attempts", `${job.attempts}/${job.max_attempts}`, ""],
          ["Updated", formatDate(job.updated_at), ""],
        ];
        for (const [label, value, className] of cells) {
          const cell = document.createElement("td");
          cell.dataset.label = label;
          if (className === "status") {
            const badge = document.createElement("span");
            badge.className = `badge ${job.status}`;
            badge.textContent = job.status;
            cell.appendChild(badge);
          } else {
            cell.textContent = value || "";
            if (className) cell.className = className;
          }
          row.appendChild(cell);
        }

        const actionCell = document.createElement("td");
        actionCell.dataset.label = "Action";
        const rerun = document.createElement("button");
        rerun.type = "button";
        rerun.textContent = "Rerun";
        rerun.dataset.jobId = job.job_id;
        rerun.addEventListener("click", () => rerunJob(job.job_id, rerun));
        actionCell.appendChild(rerun);
        row.appendChild(actionCell);
        els.jobsBody.appendChild(row);
      }
      updateMetrics();
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
          window.location.assign("/auth/login?next=/dashboard");
        }
      } catch (error) {
        setToast(error.message || "Unable to log out", "error");
        els.logout.disabled = false;
      }
    }

    els.refreshJobs.addEventListener("click", loadJobs);
    els.syncPeople.addEventListener("click", syncPeople);
    els.logout.addEventListener("click", logout);
    els.status.addEventListener("change", loadJobs);
    els.minutes.addEventListener("change", loadJobs);
    els.jobType.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadJobs();
    });

    Promise.all([loadUser(), loadJobs()]).catch((error) => {
      setToast(error.message || "Dashboard failed to load", "error");
    });
  </script>
</body>
</html>
"""
