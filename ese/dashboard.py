"""Local dashboard for creating and reviewing ESE runs."""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ese.config import ConfigValidationError, load_config
from ese.doctor import evaluate_doctor
from ese.pipeline import run_pipeline
from ese.reports import RunReportError, collect_run_report
from ese.templates import list_task_templates, run_task_pipeline


class DashboardJobStore:
    """Thread-safe registry for background dashboard jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def start(self, name: str, func, /, **kwargs) -> str:  # noqa: ANN001
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "name": name,
                "status": "queued",
                "result": None,
                "error": None,
            }

        def _runner() -> None:
            self._update(job_id, status="running")
            try:
                result = func(**kwargs)
            except Exception as err:  # noqa: BLE001
                self._update(job_id, status="failed", error=str(err))
                return
            self._update(job_id, status="completed", result=result)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        return job_id

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(updates)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


def _load_effective_config(config_path: str, scope: str | None = None) -> dict[str, Any]:
    cfg = load_config(config_path)
    effective = dict(cfg or {})
    if scope and scope.strip():
        input_cfg = dict(effective.get("input") or {})
        input_cfg["scope"] = scope.strip()
        effective["input"] = input_cfg
    ok, violations, _ = evaluate_doctor(effective)
    if not ok:
        raise ConfigValidationError("; ".join(violations))
    return effective


def _run_config_job(
    *,
    config_path: str,
    artifacts_dir: str | None,
    scope: str | None,
) -> dict[str, str]:
    effective = _load_effective_config(config_path, scope=scope)
    summary_path = run_pipeline(cfg=effective, artifacts_dir=artifacts_dir)
    final_artifacts_dir = artifacts_dir or str((effective.get("output") or {}).get("artifacts_dir") or "artifacts")
    return {
        "summary_path": summary_path,
        "artifacts_dir": final_artifacts_dir,
    }


def _run_task_job(**kwargs: Any) -> dict[str, str]:
    cfg, summary_path = run_task_pipeline(**kwargs)
    final_artifacts_dir = str((cfg.get("output") or {}).get("artifacts_dir") or "artifacts")
    return {
        "summary_path": summary_path,
        "artifacts_dir": final_artifacts_dir,
    }


def _rerun_job(
    *,
    artifacts_dir: str,
    from_role: str,
    config_path: str | None = None,
    scope: str | None = None,
) -> dict[str, str]:
    report = collect_run_report(artifacts_dir)
    snapshot = str(report.get("config_snapshot") or "")
    effective_config_path = config_path or snapshot
    if not effective_config_path:
        raise ConfigValidationError(
            f"No config path provided and no config snapshot found in {artifacts_dir}.",
        )
    effective = _load_effective_config(effective_config_path, scope=scope)
    summary_path = run_pipeline(
        cfg=effective,
        artifacts_dir=artifacts_dir,
        start_role=from_role,
    )
    return {
        "summary_path": summary_path,
        "artifacts_dir": artifacts_dir,
    }


def _dashboard_html(bootstrap: dict[str, Any]) -> str:
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ESE Dashboard</title>
  <style>
    :root {
      --bg: #f5efe6;
      --panel: rgba(255, 250, 244, 0.92);
      --panel-strong: #fffaf3;
      --ink: #16202a;
      --muted: #5a6470;
      --accent: #b85c38;
      --accent-strong: #8d3c1d;
      --good: #257a5a;
      --warn: #c07d1a;
      --bad: #9e2a2b;
      --line: rgba(22, 32, 42, 0.12);
      --shadow: 0 24px 60px rgba(68, 43, 26, 0.12);
      --font: "Avenir Next", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--font);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(184, 92, 56, 0.22), transparent 34%),
        radial-gradient(circle at bottom right, rgba(37, 122, 90, 0.18), transparent 28%),
        linear-gradient(180deg, #fdf8ef 0%, var(--bg) 55%, #efe4d2 100%);
      min-height: 100vh;
    }
    .shell {
      max-width: 1480px;
      margin: 0 auto;
      padding: 28px 20px 48px;
      display: grid;
      gap: 20px;
      grid-template-columns: 360px minmax(0, 1fr);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .sidebar {
      padding: 24px;
      position: sticky;
      top: 20px;
      align-self: start;
    }
    h1, h2, h3, p { margin-top: 0; }
    h1 {
      font-size: 2rem;
      letter-spacing: -0.04em;
      margin-bottom: 8px;
    }
    .lede {
      color: var(--muted);
      line-height: 1.5;
      margin-bottom: 24px;
    }
    label {
      display: block;
      font-size: 0.9rem;
      font-weight: 700;
      margin-bottom: 6px;
    }
    input, select, textarea, button {
      width: 100%;
      font: inherit;
    }
    input, select, textarea {
      border: 1px solid rgba(22, 32, 42, 0.14);
      border-radius: 14px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.82);
      color: var(--ink);
      margin-bottom: 14px;
    }
    textarea {
      min-height: 128px;
      resize: vertical;
      line-height: 1.45;
    }
    .btn-row {
      display: flex;
      gap: 10px;
    }
    button {
      border: 0;
      border-radius: 14px;
      padding: 12px 16px;
      cursor: pointer;
      background: var(--accent);
      color: white;
      font-weight: 700;
      transition: transform 120ms ease, background 120ms ease;
    }
    button.secondary {
      background: rgba(22, 32, 42, 0.08);
      color: var(--ink);
    }
    button:hover { transform: translateY(-1px); }
    button:disabled {
      opacity: 0.6;
      cursor: not-allowed;
      transform: none;
    }
    .main {
      display: grid;
      gap: 20px;
    }
    .hero {
      padding: 26px;
      background: linear-gradient(135deg, rgba(255, 250, 244, 0.94), rgba(247, 236, 220, 0.92));
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(22, 32, 42, 0.06);
      color: var(--muted);
      font-size: 0.85rem;
      margin-right: 8px;
      margin-bottom: 8px;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .metric {
      padding: 18px;
      border-radius: 18px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
    }
    .metric .kicker {
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 10px;
    }
    .metric .value {
      font-size: 2rem;
      letter-spacing: -0.04em;
      font-weight: 800;
    }
    .sections {
      display: grid;
      gap: 20px;
      grid-template-columns: 1.15fr 0.85fr;
    }
    .section {
      padding: 22px;
    }
    .list {
      display: grid;
      gap: 12px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      background: rgba(255,255,255,0.72);
    }
    .role-grid {
      display: grid;
      gap: 14px;
    }
    .role-header {
      display: flex;
      justify-content: space-between;
      align-items: start;
      gap: 10px;
      margin-bottom: 10px;
    }
    .role-header h3 {
      margin-bottom: 4px;
      font-size: 1.1rem;
    }
    .role-meta {
      color: var(--muted);
      font-size: 0.9rem;
    }
    .finding {
      border-left: 4px solid var(--warn);
      padding-left: 10px;
      margin-top: 10px;
    }
    .finding.bad { border-color: var(--bad); }
    .finding.good { border-color: var(--good); }
    .muted { color: var(--muted); }
    .status-running { color: var(--warn); }
    .status-completed { color: var(--good); }
    .status-failed { color: var(--bad); }
    .empty {
      padding: 18px;
      border-radius: 18px;
      border: 1px dashed rgba(22, 32, 42, 0.2);
      color: var(--muted);
      background: rgba(255,255,255,0.45);
    }
    .mono {
      font-family: "SFMono-Regular", "Consolas", monospace;
      font-size: 0.92rem;
      word-break: break-all;
    }
    .tiny {
      font-size: 0.82rem;
      color: var(--muted);
    }
    @media (max-width: 1100px) {
      .shell, .sections { grid-template-columns: 1fr; }
      .sidebar { position: static; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 680px) {
      .metrics { grid-template-columns: 1fr; }
      .btn-row { flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="panel sidebar">
      <h1>ESE Dashboard</h1>
      <p class="lede">Start task-first runs, inspect blockers, and rerun from any role without leaving the local product.</p>
      <form id="run-form">
        <label for="scope">Task Scope</label>
        <textarea id="scope" name="scope" placeholder="Describe the feature, review target, rollout risk, or engineering question."></textarea>

        <label for="template">Template</label>
        <select id="template" name="template"></select>

        <label for="provider">Provider</label>
        <select id="provider" name="provider">
          <option value="openai">openai</option>
          <option value="anthropic">anthropic</option>
          <option value="google">google</option>
          <option value="xai">xai</option>
          <option value="openrouter">openrouter</option>
          <option value="huggingface">huggingface</option>
          <option value="local">local</option>
          <option value="custom_api">custom_api</option>
        </select>

        <label for="execution_mode">Execution</label>
        <select id="execution_mode" name="execution_mode">
          <option value="auto">auto</option>
          <option value="demo">demo</option>
          <option value="live">live</option>
        </select>

        <label for="model">Model Override</label>
        <input id="model" name="model" placeholder="Optional provider model id">

        <label for="base_url">Base URL</label>
        <input id="base_url" name="base_url" placeholder="Required for custom_api live runs">

        <label for="runtime_adapter">Runtime Adapter</label>
        <input id="runtime_adapter" name="runtime_adapter" placeholder="Optional module:function for advanced live runs">

        <label for="config_path">Config Path</label>
        <input id="config_path" name="config_path" placeholder="Optional path to run an existing config instead">

        <label for="artifacts_dir">Artifacts Directory</label>
        <input id="artifacts_dir" name="artifacts_dir" value="">

        <div class="btn-row">
          <button id="run-button" type="submit">Start Run</button>
          <button id="refresh-button" type="button" class="secondary">Refresh</button>
        </div>
      </form>
      <div style="margin-top:16px" class="tiny">
        The dashboard uses the same artifacts, templates, and rerun logic as the CLI.
      </div>
    </aside>
    <main class="main">
      <section class="panel hero">
        <div id="hero-meta"></div>
        <div id="job-status" class="muted">No active job.</div>
      </section>
      <section class="metrics" id="metrics"></section>
      <section class="sections">
        <div class="panel section">
          <h2>Roles</h2>
          <div id="roles" class="role-grid"></div>
        </div>
        <div class="list">
          <section class="panel section">
            <h2>Blockers</h2>
            <div id="blockers"></div>
          </section>
          <section class="panel section">
            <h2>Next Steps</h2>
            <div id="next-steps"></div>
          </section>
        </div>
      </section>
    </main>
  </div>
  <script>
    window.ESE_BOOTSTRAP = __BOOTSTRAP_JSON__;
  </script>
  <script>
    const bootstrap = window.ESE_BOOTSTRAP;
    const form = document.getElementById('run-form');
    const templateSelect = document.getElementById('template');
    const artifactsInput = document.getElementById('artifacts_dir');
    const configInput = document.getElementById('config_path');
    const heroMeta = document.getElementById('hero-meta');
    const jobStatus = document.getElementById('job-status');
    const metrics = document.getElementById('metrics');
    const blockersEl = document.getElementById('blockers');
    const nextStepsEl = document.getElementById('next-steps');
    const rolesEl = document.getElementById('roles');
    const refreshButton = document.getElementById('refresh-button');
    const runButton = document.getElementById('run-button');
    const state = {
      artifactsDir: bootstrap.artifacts_dir,
      activeJobId: null,
    };

    artifactsInput.value = state.artifactsDir;
    if (bootstrap.config_path) {
      configInput.value = bootstrap.config_path;
    }

    async function request(url, options = {}) {
      const response = await fetch(url, options);
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data.error || data.message || 'Request failed');
      }
      return data;
    }

    function renderTemplates(templates) {
      templateSelect.innerHTML = templates.map((template) => {
        return `<option value="${template.key}">${template.title}</option>`;
      }).join('');
    }

    function statusClass(status) {
      return `status-${String(status || 'unknown').toLowerCase()}`;
    }

    function renderMetrics(report) {
      const counts = report?.severity_counts || {};
      const metricData = [
        ['Status', report?.status || 'missing'],
        ['Roles', String(report?.roles?.length || 0)],
        ['Findings', String(report?.finding_count || 0)],
        ['Blockers', String(report?.blocker_count || 0)],
      ];
      metrics.innerHTML = metricData.map(([label, value]) => `
        <article class="metric">
          <div class="kicker">${label}</div>
          <div class="value">${value}</div>
          ${label === 'Findings' ? `<div class="tiny">critical ${counts.CRITICAL || 0}, high ${counts.HIGH || 0}, medium ${counts.MEDIUM || 0}, low ${counts.LOW || 0}</div>` : ''}
        </article>
      `).join('');
    }

    function renderHero(report) {
      if (!report) {
        heroMeta.innerHTML = `<div class="pill">No report loaded</div>`;
        return;
      }
      const pills = [
        report.status ? `<span class="pill ${statusClass(report.status)}">Status: ${report.status}</span>` : '',
        report.provider ? `<span class="pill">Provider: ${report.provider}</span>` : '',
        report.adapter ? `<span class="pill">Adapter: ${report.adapter}</span>` : '',
        report.scope ? `<span class="pill">Scope captured</span>` : '',
      ].filter(Boolean).join('');
      heroMeta.innerHTML = `
        ${pills}
        ${report.scope ? `<h2 style="margin-top:12px; margin-bottom:8px;">${report.scope}</h2>` : ''}
        <div class="tiny mono">${report.artifacts_dir || ''}</div>
      `;
    }

    function findingClass(severity) {
      if (severity === 'HIGH' || severity === 'CRITICAL') return 'bad';
      if (severity === 'LOW') return 'good';
      return '';
    }

    function renderRoles(report) {
      const roles = report?.roles || [];
      if (!roles.length) {
        rolesEl.innerHTML = `<div class="empty">Run a task or point the dashboard at an artifacts directory with a completed ESE run.</div>`;
        return;
      }
      rolesEl.innerHTML = roles.map((role) => {
        const findings = (role.findings || []).map((finding) => `
          <div class="finding ${findingClass(finding.severity)}">
            <strong>${finding.severity}</strong> ${finding.title || 'Untitled finding'}
            ${finding.details ? `<div class="tiny">${finding.details}</div>` : ''}
          </div>
        `).join('');
        const nextSteps = (role.next_steps || []).map((step) => `<div class="tiny">Next: ${step}</div>`).join('');
        return `
          <article class="card">
            <div class="role-header">
              <div>
                <h3>${role.role}</h3>
                <div class="role-meta">${role.model || ''}</div>
              </div>
              <button type="button" class="secondary" data-rerun-role="${role.role}">Rerun From Here</button>
            </div>
            <p>${role.summary || 'No summary provided.'}</p>
            <div class="tiny mono">${role.artifact}</div>
            ${findings || '<div class="tiny muted">No findings.</div>'}
            ${nextSteps}
          </article>
        `;
      }).join('');

      rolesEl.querySelectorAll('[data-rerun-role]').forEach((button) => {
        button.addEventListener('click', async () => {
          await rerunFromRole(button.dataset.rerunRole);
        });
      });
    }

    function renderBlockers(report) {
      const blockers = report?.blockers || [];
      if (!blockers.length) {
        blockersEl.innerHTML = `<div class="empty">No blockers reported.</div>`;
        return;
      }
      blockersEl.innerHTML = blockers.map((blocker) => `
        <article class="card">
          <div><strong>${blocker.role}</strong> <span class="pill">${blocker.severity}</span></div>
          <div style="margin-top:6px;">${blocker.title || 'Untitled blocker'}</div>
          ${blocker.details ? `<div class="tiny">${blocker.details}</div>` : ''}
        </article>
      `).join('');
    }

    function renderNextSteps(report) {
      const nextSteps = report?.next_steps || [];
      if (!nextSteps.length) {
        nextStepsEl.innerHTML = `<div class="empty">No next steps recorded.</div>`;
        return;
      }
      nextStepsEl.innerHTML = nextSteps.map((item) => `
        <article class="card">
          <strong>${item.role}</strong>
          <div>${item.text}</div>
        </article>
      `).join('');
    }

    async function loadReport() {
      state.artifactsDir = artifactsInput.value.trim() || bootstrap.artifacts_dir;
      try {
        const report = await request(`/api/report?artifacts_dir=${encodeURIComponent(state.artifactsDir)}`);
        if (report.config_snapshot && !configInput.value) {
          configInput.value = report.config_snapshot;
        }
        renderHero(report);
        renderMetrics(report);
        renderRoles(report);
        renderBlockers(report);
        renderNextSteps(report);
      } catch (error) {
        renderHero(null);
        renderMetrics(null);
        renderRoles(null);
        blockersEl.innerHTML = `<div class="empty">${error.message}</div>`;
        nextStepsEl.innerHTML = `<div class="empty">No next steps available.</div>`;
      }
    }

    async function pollJob(jobId) {
      state.activeJobId = jobId;
      runButton.disabled = true;
      while (state.activeJobId === jobId) {
        const job = await request(`/api/jobs/${jobId}`);
        jobStatus.innerHTML = `<span class="${statusClass(job.status)}">Job ${job.name}: ${job.status}</span>${job.error ? ` - ${job.error}` : ''}`;
        if (job.status === 'completed') {
          state.activeJobId = null;
          runButton.disabled = false;
          await loadReport();
          break;
        }
        if (job.status === 'failed') {
          state.activeJobId = null;
          runButton.disabled = false;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    }

    async function rerunFromRole(role) {
      const payload = {
        artifacts_dir: artifactsInput.value.trim() || bootstrap.artifacts_dir,
        from_role: role,
        config_path: configInput.value.trim() || undefined,
      };
      const response = await request('/api/rerun', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await pollJob(response.job_id);
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const scope = form.scope.value.trim();
      const configPath = form.config_path.value.trim();
      if (!scope && !configPath) {
        jobStatus.textContent = 'Provide a task scope or an existing config path.';
        return;
      }

      const payload = {
        scope,
        template_key: form.template.value,
        provider: form.provider.value,
        execution_mode: form.execution_mode.value,
        model: form.model.value.trim() || undefined,
        base_url: form.base_url.value.trim() || undefined,
        runtime_adapter: form.runtime_adapter.value.trim() || undefined,
        config_path: configPath || undefined,
        artifacts_dir: form.artifacts_dir.value.trim() || bootstrap.artifacts_dir,
      };

      const endpoint = configPath ? '/api/run-config' : '/api/run';
      const response = await request(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await pollJob(response.job_id);
    });

    refreshButton.addEventListener('click', async () => {
      await loadReport();
    });

    async function boot() {
      const templates = await request('/api/templates');
      renderTemplates(templates.templates);
      await loadReport();
    }

    boot().catch((error) => {
      jobStatus.textContent = error.message;
    });
  </script>
</body>
</html>
"""
    return html.replace("__BOOTSTRAP_JSON__", json.dumps(bootstrap))


def serve_dashboard(
    *,
    artifacts_dir: str = "artifacts",
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    config_path: str | None = None,
) -> str:
    jobs = DashboardJobStore()
    root_artifacts_dir = str(Path(artifacts_dir))
    bootstrap = {
        "artifacts_dir": root_artifacts_dir,
        "config_path": config_path,
    }

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "ESEDashboard/1.0"

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, body: str, content_type: str = "text/html; charset=utf-8") -> None:
            payload = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _request_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object.")
            return data

        def _artifacts_dir_from_query(self) -> str:
            query = parse_qs(urlparse(self.path).query)
            requested = query.get("artifacts_dir", [root_artifacts_dir])[0]
            return str(Path(requested))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_text(_dashboard_html(bootstrap))
                return

            if parsed.path == "/api/templates":
                payload = {
                    "templates": [
                        {
                            "key": template.key,
                            "title": template.title,
                            "summary": template.summary,
                            "roles": list(template.roles),
                        }
                        for template in list_task_templates()
                    ],
                }
                self._send_json(payload)
                return

            if parsed.path == "/api/report":
                try:
                    report = collect_run_report(self._artifacts_dir_from_query())
                except RunReportError as err:
                    self._send_json({"error": str(err)}, status=HTTPStatus.NOT_FOUND)
                    return
                self._send_json(report)
                return

            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = jobs.get(job_id)
                if job is None:
                    self._send_json({"error": f"Unknown job '{job_id}'."}, status=HTTPStatus.NOT_FOUND)
                    return
                self._send_json(job)
                return

            if parsed.path == "/artifact":
                query = parse_qs(parsed.query)
                role = query.get("role", [""])[0]
                artifacts = self._artifacts_dir_from_query()
                try:
                    report = collect_run_report(artifacts)
                except RunReportError as err:
                    self._send_json({"error": str(err)}, status=HTTPStatus.NOT_FOUND)
                    return
                for item in report.get("roles", []):
                    if item.get("role") == role:
                        content = Path(str(item["artifact"])).read_text(encoding="utf-8")
                        self._send_text(content, content_type="text/plain; charset=utf-8")
                        return
                self._send_json({"error": f"No artifact found for role '{role}'."}, status=HTTPStatus.NOT_FOUND)
                return

            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = self._request_json()
            except (ValueError, json.JSONDecodeError) as err:
                self._send_json({"error": str(err)}, status=HTTPStatus.BAD_REQUEST)
                return

            try:
                if parsed.path == "/api/run":
                    job_id = jobs.start(
                        "task-run",
                        _run_task_job,
                        scope=str(payload.get("scope") or ""),
                        template_key=str(payload.get("template_key") or "feature-delivery"),
                        provider=str(payload.get("provider") or "openai"),
                        execution_mode=str(payload.get("execution_mode") or "auto"),
                        artifacts_dir=str(payload.get("artifacts_dir") or root_artifacts_dir),
                        model=str(payload.get("model")) if payload.get("model") else None,
                        runtime_adapter=str(payload.get("runtime_adapter")) if payload.get("runtime_adapter") else None,
                        base_url=str(payload.get("base_url")) if payload.get("base_url") else None,
                        config_path=str(payload.get("config_path")) if payload.get("config_path") else None,
                    )
                    self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                    return

                if parsed.path == "/api/run-config":
                    config_path_value = str(payload.get("config_path") or "").strip()
                    if not config_path_value:
                        raise ConfigValidationError("config_path is required for /api/run-config.")
                    job_id = jobs.start(
                        "config-run",
                        _run_config_job,
                        config_path=config_path_value,
                        artifacts_dir=str(payload.get("artifacts_dir")) if payload.get("artifacts_dir") else None,
                        scope=str(payload.get("scope")) if payload.get("scope") else None,
                    )
                    self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                    return

                if parsed.path == "/api/rerun":
                    job_id = jobs.start(
                        "rerun",
                        _rerun_job,
                        artifacts_dir=str(payload.get("artifacts_dir") or root_artifacts_dir),
                        from_role=str(payload.get("from_role") or ""),
                        config_path=str(payload.get("config_path")) if payload.get("config_path") else None,
                        scope=str(payload.get("scope")) if payload.get("scope") else None,
                    )
                    self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                    return
            except ConfigValidationError as err:
                self._send_json({"error": str(err)}, status=HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"error": "Not found."}, status=HTTPStatus.NOT_FOUND)

    server = ThreadingHTTPServer((host, port), DashboardHandler)
    url = f"http://{host}:{port}"
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url
