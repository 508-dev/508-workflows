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
      .identity { text-align: left; }
      .toolbar, .summary, .split, .detail-grid, .search-row { grid-template-columns: 1fr; }
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
            <th style="width: 9%;">Actions</th>
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

    <section class="split">
      <section class="panel">
        <div class="panel-head">
          <h2>People lookup</h2>
          <span id="peopleStatus" class="status-line"></span>
        </div>
        <div class="search-row">
          <label>
            Search CRM people cache
            <input id="peopleQuery" autocomplete="off" placeholder="Name, email, CRM id, Discord, resume">
          </label>
          <button id="searchPeople" type="button">Search</button>
        </div>
        <div id="peopleEmptyState" class="empty" hidden>No people match this lookup.</div>
        <table id="peopleTable" class="compact" aria-label="People lookup results">
          <thead>
            <tr>
              <th style="width: 25%;">Person</th>
              <th style="width: 28%;">Status</th>
              <th style="width: 22%;">Discord</th>
              <th style="width: 25%;">Resume / skills</th>
            </tr>
          </thead>
          <tbody id="peopleBody"></tbody>
        </table>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>Recent audit</h2>
          <button id="refreshAudit" type="button">Refresh</button>
        </div>
        <div id="auditEmptyState" class="empty" hidden>No audit events found.</div>
        <table id="auditTable" class="compact" aria-label="Recent audit events">
          <thead>
            <tr>
              <th style="width: 24%;">Time</th>
              <th style="width: 28%;">Actor</th>
              <th style="width: 28%;">Action</th>
              <th style="width: 20%;">Result</th>
            </tr>
          </thead>
          <tbody id="auditBody"></tbody>
        </table>
      </section>
    </section>
  </main>
  <script>
    const state = { jobs: [], people: [], auditEvents: [] };
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
      jobDetailPanel: document.querySelector("#jobDetailPanel"),
      jobDetailStatus: document.querySelector("#jobDetailStatus"),
      jobDetailGrid: document.querySelector("#jobDetailGrid"),
      jobPayload: document.querySelector("#jobPayload"),
      jobResult: document.querySelector("#jobResult"),
      peopleQuery: document.querySelector("#peopleQuery"),
      searchPeople: document.querySelector("#searchPeople"),
      peopleStatus: document.querySelector("#peopleStatus"),
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

    function renderPeople() {
      els.peopleBody.replaceChildren();
      els.peopleEmptyState.hidden = state.people.length !== 0;
      els.peopleTable.hidden = state.people.length === 0;

      for (const person of state.people) {
        const row = document.createElement("tr");
        const personCell = addTextCell(row, "Person", "");
        const personName = document.createElement("strong");
        personName.textContent = person.name || person.crm_contact_id;
        const meta = document.createElement("div");
        meta.className = "status-line";
        meta.textContent = [person.email_508 || person.email, person.crm_contact_id]
          .filter(Boolean)
          .join(" | ");
        personCell.appendChild(personName);
        personCell.appendChild(meta);

        const statusCell = addTextCell(row, "Status", "");
        statusCell.className = "chip-list";
        const status = person.profile_status || {};
        const checks = [
          ["CRM", status.crm_active],
          ["Member", status.is_member],
          ["Discord", status.discord_linked],
          ["508 email", status.email_508],
          ["Resume", status.latest_resume],
        ];
        for (const [label, ok] of checks) {
          statusCell.appendChild(createBadge(ok ? label : `Missing ${label}`, ok ? "succeeded" : "missing"));
        }

        const discord = [person.discord_username, person.discord_user_id]
          .filter(Boolean)
          .join(" | ");
        addTextCell(row, "Discord", discord || "Not linked");

        const resumeCell = addTextCell(row, "Resume / skills", "");
        const resume = person.latest_resume_name || person.latest_resume_id || "No resume";
        const skillsCount = Number(status.skills_count || 0);
        resumeCell.textContent = `${resume} | ${skillsCount} skills`;
        els.peopleBody.appendChild(row);
      }
    }

    function renderAuditEvents() {
      els.auditBody.replaceChildren();
      els.auditEmptyState.hidden = state.auditEvents.length !== 0;
      els.auditTable.hidden = state.auditEvents.length === 0;

      for (const event of state.auditEvents) {
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

    function peopleUrl() {
      const params = new URLSearchParams({ limit: "25" });
      const query = els.peopleQuery.value.trim();
      if (query) params.set("query", query);
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
    els.searchPeople.addEventListener("click", loadPeople);
    els.refreshAudit.addEventListener("click", loadAuditEvents);
    els.status.addEventListener("change", loadJobs);
    els.minutes.addEventListener("change", loadJobs);
    els.jobType.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadJobs();
    });
    els.peopleQuery.addEventListener("keydown", (event) => {
      if (event.key === "Enter") loadPeople();
    });

    Promise.all([loadUser(), loadJobs(), loadPeople(), loadAuditEvents()]).catch((error) => {
      setToast(error.message || "Dashboard failed to load", "error");
    });
  </script>
</body>
</html>
"""
