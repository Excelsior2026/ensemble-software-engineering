"""Local dashboard for creating and reviewing ESE runs."""

from __future__ import annotations

import json
import threading
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ese.config import ConfigValidationError, load_config
from ese.feedback import record_feedback
from ese.doctor import evaluate_doctor
from ese.pipeline import run_pipeline
from ese.pr_review import PullRequestReviewError, run_pr_review
from ese.reports import (
    RunReportError,
    collect_run_report,
    list_recent_runs,
    load_artifact_view,
    render_junit,
    render_sarif,
)
from ese.templates import list_task_templates, recommend_template_for_scope, run_task_pipeline


class DashboardJobStore:
    """Thread-safe registry for background dashboard jobs."""

    def __init__(self, *, storage_dir: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._storage_dir = Path(storage_dir) if storage_dir is not None else None
        if self._storage_dir is not None:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_persisted_jobs()

    def _load_persisted_jobs(self) -> None:
        if self._storage_dir is None:
            return
        for path in sorted(self._storage_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            job_id = str(payload.get("id") or "").strip()
            if not job_id:
                continue
            self._jobs[job_id] = payload

    def _job_path(self, job_id: str) -> Path | None:
        if self._storage_dir is None:
            return None
        return self._storage_dir / f"{job_id}.json"

    def _persist_job(self, job_id: str) -> None:
        path = self._job_path(job_id)
        if path is None:
            return
        payload = self._jobs.get(job_id)
        if payload is None:
            return
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def start(self, name: str, func, /, **kwargs) -> str:  # noqa: ANN001
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "name": name,
                "status": "queued",
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._persist_job(job_id)

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
                updates["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                self._jobs[job_id].update(updates)
                self._persist_job(job_id)

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


def _run_pr_job(**kwargs: Any) -> dict[str, str]:
    context, cfg, summary_path, review_path = run_pr_review(**kwargs)
    final_artifacts_dir = str((cfg.get("output") or {}).get("artifacts_dir") or "artifacts")
    return {
        "summary_path": summary_path,
        "artifacts_dir": final_artifacts_dir,
        "review_path": review_path,
        "head_ref": context.head_ref,
        "base_ref": context.base_ref,
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


def _allocate_run_artifacts_dir(base_dir: str, *, kind: str) -> str:
    requested = Path(base_dir)
    root = requested.parent if (requested / "pipeline_state.json").exists() else requested
    root.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = f"{stamp}-{kind}"
    candidate = root / base_name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_name}-{suffix}"
        suffix += 1
    return str(candidate)


def _coerce_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _task_run_kwargs(payload: dict[str, Any], *, root_artifacts_dir: str) -> dict[str, Any]:
    run_artifacts_dir = _allocate_run_artifacts_dir(
        str(payload.get("artifacts_dir") or root_artifacts_dir),
        kind="task-run",
    )
    return {
        "scope": str(payload.get("scope") or ""),
        "template_key": str(payload.get("template_key") or "feature-delivery"),
        "provider": str(payload.get("provider") or "openai"),
        "execution_mode": str(payload.get("execution_mode") or "auto"),
        "artifacts_dir": run_artifacts_dir,
        "model": str(payload.get("model")) if payload.get("model") else None,
        "runtime_adapter": str(payload.get("runtime_adapter")) if payload.get("runtime_adapter") else None,
        "base_url": str(payload.get("base_url")) if payload.get("base_url") else None,
        "config_path": str(payload.get("config_path")) if payload.get("config_path") else None,
        "repo_path": str(payload.get("repo_path")) if payload.get("repo_path") else None,
        "include_repo_status": _coerce_bool(payload.get("include_repo_status"), default=True),
        "include_repo_diff": _coerce_bool(payload.get("include_repo_diff"), default=True),
        "max_repo_diff_chars": int(payload.get("max_repo_diff_chars") or 8000),
    }


def _export_report_payload(artifacts_dir: str, export_format: str) -> tuple[str, str, str]:
    report = collect_run_report(artifacts_dir)
    clean_format = export_format.strip().lower()
    if clean_format == "sarif":
        return (
            render_sarif(report),
            "application/sarif+json; charset=utf-8",
            "ese_report.sarif.json",
        )
    if clean_format == "junit":
        return (
            render_junit(report),
            "application/xml; charset=utf-8",
            "ese_report.junit.xml",
        )
    raise ConfigValidationError("format must be 'sarif' or 'junit'.")


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
    .viewer {
      display: grid;
      gap: 12px;
    }
    .viewer-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .viewer pre {
      margin: 0;
      max-height: 460px;
      overflow: auto;
      padding: 16px;
      border-radius: 16px;
      background: rgba(18, 24, 30, 0.94);
      color: #f5efe6;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .history-card.active {
      border-color: rgba(184, 92, 56, 0.45);
      box-shadow: inset 0 0 0 1px rgba(184, 92, 56, 0.18);
    }
    .history-card .meta-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }
    .tiny {
      font-size: 0.82rem;
      color: var(--muted);
    }
    .field-group[data-hidden="true"] {
      display: none;
    }
    .feedback-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .suggestion-snippet {
      margin-top: 12px;
      padding: 12px;
      border-radius: 14px;
      background: rgba(18, 24, 30, 0.94);
      color: #f5efe6;
      font-family: "SFMono-Regular", "Consolas", monospace;
      font-size: 0.84rem;
      line-height: 1.45;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
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
      <p class="lede">Start task-first runs, compare independent specialist voices, and carry pluralistic review signal through to release decisions.</p>
      <form id="run-form">
        <label for="interface_mode">Experience</label>
        <select id="interface_mode" name="interface_mode">
          <option value="beginner">beginner</option>
          <option value="expert">expert</option>
        </select>

        <label for="scope">Task Scope</label>
        <textarea id="scope" name="scope" placeholder="Describe the feature, review target, rollout risk, or engineering question."></textarea>

        <div class="field-group" data-advanced="true">
          <label for="review_focus">PR Review Focus</label>
          <input id="review_focus" name="review_focus" placeholder="Optional reviewer guidance for PR mode">
        </div>

        <label for="repo_path">Repo Path</label>
        <input id="repo_path" name="repo_path" placeholder="Path to the Git repo for PR review">

        <div class="field-group" data-advanced="true">
          <label for="pr_ref">PR Number or URL</label>
          <input id="pr_ref" name="pr_ref" placeholder="Optional GitHub PR number or URL">
        </div>

        <div class="field-group" data-advanced="true">
          <label for="base_ref">Base Ref</label>
          <input id="base_ref" name="base_ref" placeholder="Defaults from PR or origin/main">
        </div>

        <div class="field-group" data-advanced="true">
          <label for="head_ref">Head Ref</label>
          <input id="head_ref" name="head_ref" placeholder="Defaults from PR or HEAD">
        </div>

        <label for="template">Template</label>
        <div class="btn-row" style="margin-bottom:14px;">
          <select id="template" name="template"></select>
          <button id="recommend-template" type="button" class="secondary">Recommend</button>
        </div>

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

        <div class="field-group" data-advanced="true">
          <label for="execution_mode">Execution</label>
          <select id="execution_mode" name="execution_mode">
            <option value="auto">auto</option>
            <option value="demo">demo</option>
            <option value="live">live</option>
          </select>
        </div>

        <div class="field-group" data-advanced="true">
          <label for="model">Model Override</label>
          <input id="model" name="model" placeholder="Optional provider model id">
        </div>

        <div class="field-group" data-advanced="true">
          <label for="base_url">Base URL</label>
          <input id="base_url" name="base_url" placeholder="Required for custom_api live runs">
        </div>

        <div class="field-group" data-advanced="true">
          <label for="runtime_adapter">Runtime Adapter</label>
          <input id="runtime_adapter" name="runtime_adapter" placeholder="Optional module:function for advanced live runs">
        </div>

        <label for="include_repo_context">Task Repo Context</label>
        <select id="include_repo_context" name="include_repo_context">
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>

        <div class="field-group" data-advanced="true">
          <label for="include_repo_status">Include Git Status</label>
          <select id="include_repo_status" name="include_repo_status">
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label for="include_repo_diff">Include Working Diff</label>
          <select id="include_repo_diff" name="include_repo_diff">
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </div>

        <div class="field-group" data-advanced="true">
          <label for="config_path">Config Path</label>
          <input id="config_path" name="config_path" placeholder="Optional path to run an existing config instead">
        </div>

        <label for="artifacts_dir">Artifacts Directory</label>
        <input id="artifacts_dir" name="artifacts_dir" value="">

        <div class="btn-row">
          <button id="run-button" type="submit">Start Task Run</button>
          <button id="pr-button" type="button">Review PR</button>
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
      <section class="panel section">
        <h2>Recent Runs</h2>
        <div id="history" class="list"></div>
      </section>
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
            <h2>Artifact Viewer</h2>
            <div id="viewer" class="viewer"></div>
          </section>
          <section class="panel section">
            <h2>Next Steps</h2>
            <div id="next-steps"></div>
          </section>
          <section class="panel section">
            <h2>Code Suggestions</h2>
            <div id="code-suggestions"></div>
          </section>
          <section class="panel section">
            <h2>Pluralism</h2>
            <div id="consensus"></div>
          </section>
          <section class="panel section">
            <h2>Run Delta</h2>
            <div id="comparison"></div>
          </section>
          <section class="panel section">
            <h2>Learning Loop</h2>
            <div id="learning"></div>
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
    const interfaceMode = document.getElementById('interface_mode');
    const recommendTemplateButton = document.getElementById('recommend-template');
    const artifactsInput = document.getElementById('artifacts_dir');
    const configInput = document.getElementById('config_path');
    const repoInput = document.getElementById('repo_path');
    const includeRepoContext = document.getElementById('include_repo_context');
    const includeRepoStatus = document.getElementById('include_repo_status');
    const includeRepoDiff = document.getElementById('include_repo_diff');
    const heroMeta = document.getElementById('hero-meta');
    const jobStatus = document.getElementById('job-status');
    const metrics = document.getElementById('metrics');
    const historyEl = document.getElementById('history');
    const blockersEl = document.getElementById('blockers');
    const nextStepsEl = document.getElementById('next-steps');
    const codeSuggestionsEl = document.getElementById('code-suggestions');
    const consensusEl = document.getElementById('consensus');
    const comparisonEl = document.getElementById('comparison');
    const learningEl = document.getElementById('learning');
    const rolesEl = document.getElementById('roles');
    const viewerEl = document.getElementById('viewer');
    const refreshButton = document.getElementById('refresh-button');
    const runButton = document.getElementById('run-button');
    const prButton = document.getElementById('pr-button');
    const state = {
      artifactsDir: bootstrap.artifacts_dir,
      activeJobId: null,
      history: [],
      selectedArtifact: null,
    };
    const advancedFields = Array.from(document.querySelectorAll('[data-advanced="true"]'));

    artifactsInput.value = state.artifactsDir;
    repoInput.value = bootstrap.repo_path || '';
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

    function escapeHtml(value) {
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
    }

    function renderTemplates(templates) {
      templateSelect.innerHTML = templates.map((template) => {
        return `<option value="${template.key}">${template.title}</option>`;
      }).join('');
    }

    function applyInterfaceMode() {
      const mode = interfaceMode.value;
      const hideAdvanced = mode === 'beginner';
      advancedFields.forEach((element) => {
        element.dataset.hidden = hideAdvanced ? 'true' : 'false';
      });
      prButton.textContent = hideAdvanced ? 'Review PR' : 'Review PR / Diff';
      runButton.textContent = hideAdvanced ? 'Start Task Run' : 'Start Configured Run';
    }

    function parseBool(value) {
      return String(value).trim().toLowerCase() !== 'false';
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
      const documentButtons = (report.documents || []).map((document) => `
        <button type="button" class="secondary" data-open-document="${document.key}">${escapeHtml(document.title)}</button>
      `).join('');
      const exportButtons = `
        <button type="button" class="secondary" data-export-format="sarif">Export SARIF</button>
        <button type="button" class="secondary" data-export-format="junit">Export JUnit</button>
      `;
      const pills = [
        report.status ? `<span class="pill ${statusClass(report.status)}">Status: ${report.status}</span>` : '',
        report.provider ? `<span class="pill">Provider: ${escapeHtml(report.provider)}</span>` : '',
        report.adapter ? `<span class="pill">Adapter: ${escapeHtml(report.adapter)}</span>` : '',
        report.scope ? `<span class="pill">Scope captured</span>` : '',
      ].filter(Boolean).join('');
      heroMeta.innerHTML = `
        ${pills}
        ${report.scope ? `<h2 style="margin-top:12px; margin-bottom:8px;">${escapeHtml(report.scope)}</h2>` : ''}
        <div class="tiny mono">${escapeHtml(report.artifacts_dir || '')}</div>
        ${report.updated_at ? `<div class="tiny">Updated ${escapeHtml(report.updated_at)}</div>` : ''}
        <div class="viewer-actions" style="margin-top:12px;">${documentButtons}${exportButtons}</div>
      `;

      heroMeta.querySelectorAll('[data-open-document]').forEach((button) => {
        button.addEventListener('click', async () => {
          state.selectedArtifact = { document: button.dataset.openDocument };
          await loadArtifactView();
        });
      });
      heroMeta.querySelectorAll('[data-export-format]').forEach((button) => {
        button.addEventListener('click', () => {
          const format = button.dataset.exportFormat;
          const url = `/api/export?artifacts_dir=${encodeURIComponent(state.artifactsDir)}&format=${encodeURIComponent(format)}`;
          window.open(url, '_blank');
        });
      });
    }

    function findingClass(severity) {
      if (severity === 'HIGH' || severity === 'CRITICAL') return 'bad';
      if (severity === 'LOW') return 'good';
      return '';
    }

    function renderHistory(runs) {
      state.history = runs || [];
      if (!state.history.length) {
        historyEl.innerHTML = `<div class="empty">No completed runs found in this workspace yet.</div>`;
        return;
      }

      historyEl.innerHTML = state.history.map((run) => {
        const isActive = run.artifacts_dir === state.artifactsDir ? 'active' : '';
        const scope = run.scope || 'No scope captured';
        return `
          <article class="card history-card ${isActive}">
            <div class="role-header">
              <div>
                <h3>${escapeHtml(scope)}</h3>
                <div class="role-meta">${escapeHtml(run.updated_at || '')}</div>
              </div>
              <button type="button" class="secondary" data-load-run="${run.artifacts_dir}">Load Run</button>
            </div>
            <div class="meta-row">
              <span class="pill ${statusClass(run.status)}">${run.status || 'unknown'}</span>
              <span class="pill">roles ${run.role_count || 0}</span>
              <span class="pill">findings ${run.finding_count || 0}</span>
              <span class="pill">blockers ${run.blocker_count || 0}</span>
            </div>
            ${run.failure ? `<div class="tiny" style="margin-top:10px;">Failure: ${escapeHtml(run.failure)}</div>` : ''}
            <div class="tiny mono" style="margin-top:10px;">${escapeHtml(run.artifacts_dir)}</div>
          </article>
        `;
      }).join('');

      historyEl.querySelectorAll('[data-load-run]').forEach((button) => {
        button.addEventListener('click', async () => {
          state.artifactsDir = button.dataset.loadRun;
          artifactsInput.value = state.artifactsDir;
          state.selectedArtifact = null;
          await loadReport();
        });
      });
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
            <strong>${escapeHtml(finding.severity)}</strong> ${escapeHtml(finding.title || 'Untitled finding')}
            ${finding.details ? `<div class="tiny">${escapeHtml(finding.details)}</div>` : ''}
            <div class="feedback-row">
              <button type="button" class="secondary" data-feedback-role="${role.role}" data-feedback-title="${escapeHtml(finding.title || '')}" data-feedback-rating="useful">Useful</button>
              <button type="button" class="secondary" data-feedback-role="${role.role}" data-feedback-title="${escapeHtml(finding.title || '')}" data-feedback-rating="noisy">Noisy</button>
              <button type="button" class="secondary" data-feedback-role="${role.role}" data-feedback-title="${escapeHtml(finding.title || '')}" data-feedback-rating="wrong">Wrong</button>
            </div>
          </div>
        `).join('');
        const nextSteps = (role.next_steps || []).map((step) => `<div class="tiny">Next: ${escapeHtml(step)}</div>`).join('');
        return `
          <article class="card">
            <div class="role-header">
              <div>
                <h3>${escapeHtml(role.role)}</h3>
                <div class="role-meta">${escapeHtml(role.model || '')}</div>
              </div>
              <div class="viewer-actions">
                <button type="button" class="secondary" data-view-role="${role.role}">View Artifact</button>
                <button type="button" class="secondary" data-rerun-role="${role.role}">Rerun From Here</button>
              </div>
            </div>
            <p>${escapeHtml(role.summary || 'No summary provided.')}</p>
            <div class="tiny mono">${escapeHtml(role.artifact)}</div>
            ${findings || '<div class="tiny muted">No findings.</div>'}
            ${nextSteps}
          </article>
        `;
      }).join('');

      rolesEl.querySelectorAll('[data-view-role]').forEach((button) => {
        button.addEventListener('click', async () => {
          state.selectedArtifact = { role: button.dataset.viewRole };
          await loadArtifactView();
        });
      });
      rolesEl.querySelectorAll('[data-rerun-role]').forEach((button) => {
        button.addEventListener('click', async () => {
          await rerunFromRole(button.dataset.rerunRole);
        });
      });
      rolesEl.querySelectorAll('[data-feedback-role]').forEach((button) => {
        button.addEventListener('click', async () => {
          const payload = {
            artifacts_dir: state.artifactsDir,
            role: button.dataset.feedbackRole,
            title: button.dataset.feedbackTitle,
            feedback: button.dataset.feedbackRating,
          };
          await request('/api/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          await loadReport();
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
          <div><strong>${escapeHtml(blocker.role)}</strong> <span class="pill">${escapeHtml(blocker.severity)}</span></div>
          <div style="margin-top:6px;">${escapeHtml(blocker.title || 'Untitled blocker')}</div>
          ${blocker.details ? `<div class="tiny">${escapeHtml(blocker.details)}</div>` : ''}
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
          <strong>${escapeHtml(item.role)}</strong>
          <div>${escapeHtml(item.text)}</div>
        </article>
      `).join('');
    }

    function renderCodeSuggestions(report) {
      const suggestions = report?.code_suggestions || [];
      if (!suggestions.length) {
        codeSuggestionsEl.innerHTML = `<div class="empty">No concrete code suggestions surfaced yet.</div>`;
        return;
      }
      codeSuggestionsEl.innerHTML = suggestions.slice(0, 8).map((item) => `
        <article class="card">
          <div class="role-header">
            <div>
              <strong>${escapeHtml(item.role || 'role')}</strong>
              <div class="tiny">
                ${escapeHtml(item.source || 'suggestion')}
                ${item.kind ? ` · ${escapeHtml(item.kind)}` : ''}
                ${item.severity ? ` · ${escapeHtml(item.severity)}` : ''}
              </div>
            </div>
          </div>
          ${item.path ? `<div class="tiny mono">${escapeHtml(item.path)}</div>` : ''}
          ${item.title ? `<div style="margin-top:8px;"><strong>${escapeHtml(item.title)}</strong></div>` : ''}
          <div>${escapeHtml(item.suggestion || item.title || 'Suggestion unavailable.')}</div>
          ${item.snippet ? `<pre class="suggestion-snippet">${escapeHtml(item.snippet)}</pre>` : ''}
        </article>
      `).join('');
    }

    function renderConsensus(report) {
      const consensus = report?.consensus || {};
      const agreements = consensus.agreements || [];
      const disagreements = consensus.disagreements || [];
      const solo = consensus.solo_blockers || [];
      if (!agreements.length && !disagreements.length && !solo.length) {
        consensusEl.innerHTML = `<div class="empty">No cross-role consensus or disagreement signal yet.</div>`;
        return;
      }
      const sections = [];
      if (agreements.length) {
        sections.push(...agreements.slice(0, 4).map((item) => `
          <article class="card">
            <strong>${escapeHtml(item.title)}</strong>
            <div class="tiny">Consensus across ${escapeHtml(item.roles.join(', '))}</div>
          </article>
        `));
      }
      if (disagreements.length) {
        sections.push(...disagreements.slice(0, 3).map((item) => `
          <article class="card">
            <strong>${escapeHtml(item.title)}</strong>
            <div class="tiny">${escapeHtml(item.note || 'Severity disagreement')}</div>
          </article>
        `));
      }
      if (solo.length) {
        sections.push(...solo.slice(0, 3).map((item) => `
          <article class="card">
            <strong>${escapeHtml(item.title)}</strong>
            <div class="tiny">Single-role blocker from ${escapeHtml(item.roles.join(', '))}</div>
          </article>
        `));
      }
      consensusEl.innerHTML = sections.join('');
    }

    function renderComparison(report) {
      const comparison = report?.comparison || {};
      if (!comparison.previous_artifacts_dir) {
        comparisonEl.innerHTML = `<div class="empty">No previous run found for comparison.</div>`;
        return;
      }
      comparisonEl.innerHTML = `
        <article class="card">
          <div class="tiny mono">${escapeHtml(comparison.previous_artifacts_dir)}</div>
          <div style="margin-top:10px;">New blockers: ${comparison.new_blockers.length}</div>
          <div>Resolved blockers: ${comparison.resolved_blockers.length}</div>
          <div>Persistent blockers: ${comparison.persistent_blockers.length}</div>
        </article>
      `;
    }

    function renderLearning(report) {
      const feedback = report?.feedback || {};
      const counts = feedback.counts || {};
      const guidance = feedback.guidance || [];
      if (!guidance.length && !Object.values(counts).some((value) => value)) {
        learningEl.innerHTML = `<div class="empty">No feedback recorded yet. Mark findings useful, noisy, or wrong to tune future runs.</div>`;
        return;
      }
      learningEl.innerHTML = `
        <article class="card">
          <div>Useful: ${counts.useful || 0}</div>
          <div>Noisy: ${counts.noisy || 0}</div>
          <div>Wrong: ${counts.wrong || 0}</div>
        </article>
        ${(guidance || []).map((item) => `
          <article class="card"><div>${escapeHtml(item)}</div></article>
        `).join('')}
      `;
    }

    function renderArtifactView(view) {
      if (!view) {
        viewerEl.innerHTML = `<div class="empty">Select a summary document or role artifact to inspect the raw output.</div>`;
        return;
      }
      viewerEl.innerHTML = `
        <div>
          <strong>${escapeHtml(view.title || 'Artifact')}</strong>
          <div class="tiny mono" style="margin-top:6px;">${escapeHtml(view.path || '')}</div>
          ${view.summary ? `<div class="tiny" style="margin-top:8px;">${escapeHtml(view.summary)}</div>` : ''}
          ${view.truncated ? `<div class="tiny" style="margin-top:8px;">Showing the first part of a large artifact.</div>` : ''}
        </div>
        <pre>${escapeHtml(view.content || '')}</pre>
      `;
    }

    async function loadHistory() {
      const response = await request(`/api/history?artifacts_dir=${encodeURIComponent(state.artifactsDir)}`);
      renderHistory(response.runs || []);
      return response.runs || [];
    }

    async function loadArtifactView() {
      if (!state.selectedArtifact) {
        renderArtifactView(null);
        return;
      }
      const query = new URLSearchParams({ artifacts_dir: state.artifactsDir });
      if (state.selectedArtifact.role) {
        query.set('role', state.selectedArtifact.role);
      }
      if (state.selectedArtifact.document) {
        query.set('document', state.selectedArtifact.document);
      }
      try {
        const view = await request(`/api/artifact?${query.toString()}`);
        renderArtifactView(view);
      } catch (error) {
        renderArtifactView({
          title: 'Artifact Unavailable',
          path: '',
          content: error.message,
          truncated: false,
        });
      }
    }

    async function loadReport(options = {}) {
      const { preferLatest = false } = options;
      state.artifactsDir = artifactsInput.value.trim() || state.artifactsDir || bootstrap.artifacts_dir;
      const runs = await loadHistory();
      try {
        const report = await request(`/api/report?artifacts_dir=${encodeURIComponent(state.artifactsDir)}`);
        if (report.config_snapshot && !configInput.value) {
          configInput.value = report.config_snapshot;
        }
        if (!state.selectedArtifact) {
          if ((report.documents || []).length) {
            state.selectedArtifact = { document: report.documents[0].key };
          } else if ((report.roles || []).length) {
            state.selectedArtifact = { role: report.roles[0].role };
          }
        }
        renderHero(report);
        renderMetrics(report);
        renderRoles(report);
        renderBlockers(report);
        renderNextSteps(report);
        renderCodeSuggestions(report);
        renderConsensus(report);
        renderComparison(report);
        renderLearning(report);
        await loadArtifactView();
      } catch (error) {
        if (preferLatest && runs.length && runs[0].artifacts_dir !== state.artifactsDir) {
          state.artifactsDir = runs[0].artifacts_dir;
          artifactsInput.value = state.artifactsDir;
          state.selectedArtifact = null;
          await loadReport();
          return;
        }
        renderHero(null);
        renderMetrics(null);
        renderRoles(null);
        blockersEl.innerHTML = `<div class="empty">${error.message}</div>`;
        nextStepsEl.innerHTML = `<div class="empty">No next steps available.</div>`;
        codeSuggestionsEl.innerHTML = `<div class="empty">No code suggestions available.</div>`;
        consensusEl.innerHTML = `<div class="empty">No consensus data available.</div>`;
        comparisonEl.innerHTML = `<div class="empty">No comparison data available.</div>`;
        learningEl.innerHTML = `<div class="empty">No feedback summary available.</div>`;
        renderArtifactView(null);
      }
    }

    async function pollJob(jobId) {
      state.activeJobId = jobId;
      runButton.disabled = true;
      prButton.disabled = true;
      while (state.activeJobId === jobId) {
        const job = await request(`/api/jobs/${jobId}`);
        jobStatus.innerHTML = `<span class="${statusClass(job.status)}">Job ${job.name}: ${job.status}</span>${job.error ? ` - ${job.error}` : ''}`;
        if (job.status === 'completed') {
          state.activeJobId = null;
          runButton.disabled = false;
          prButton.disabled = false;
          if (job.result?.artifacts_dir) {
            state.artifactsDir = job.result.artifacts_dir;
            artifactsInput.value = state.artifactsDir;
          }
          if (job.result?.config_snapshot && !configInput.value) {
            configInput.value = job.result.config_snapshot;
          }
          state.selectedArtifact = null;
          await loadReport();
          break;
        }
        if (job.status === 'failed') {
          state.activeJobId = null;
          runButton.disabled = false;
          prButton.disabled = false;
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
        repo_path: parseBool(form.include_repo_context.value) ? (form.repo_path.value.trim() || bootstrap.repo_path || '.') : undefined,
        include_repo_status: parseBool(form.include_repo_status.value),
        include_repo_diff: parseBool(form.include_repo_diff.value),
      };

      const endpoint = configPath ? '/api/run-config' : '/api/run';
      const response = await request(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await pollJob(response.job_id);
    });

    prButton.addEventListener('click', async () => {
      const repoPath = form.repo_path.value.trim() || bootstrap.repo_path || '.';
      const reviewFocus = form.review_focus.value.trim() || undefined;
      if (!repoPath) {
        jobStatus.textContent = 'Provide a repository path for PR review.';
        return;
      }

      const payload = {
        repo_path: repoPath,
        pr: form.pr_ref.value.trim() || undefined,
        base: form.base_ref.value.trim() || undefined,
        head: form.head_ref.value.trim() || undefined,
        focus: reviewFocus,
        provider: form.provider.value,
        execution_mode: form.execution_mode.value,
        model: form.model.value.trim() || undefined,
        base_url: form.base_url.value.trim() || undefined,
        runtime_adapter: form.runtime_adapter.value.trim() || undefined,
        artifacts_dir: form.artifacts_dir.value.trim() || bootstrap.artifacts_dir,
      };

      const response = await request('/api/pr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      await pollJob(response.job_id);
    });

    refreshButton.addEventListener('click', async () => {
      await loadReport();
    });

    interfaceMode.addEventListener('change', applyInterfaceMode);
    recommendTemplateButton.addEventListener('click', async () => {
      const scope = form.scope.value.trim();
      const response = await request(`/api/recommend-template?scope=${encodeURIComponent(scope)}`);
      templateSelect.value = response.template_key;
    });

    async function boot() {
      const templates = await request('/api/templates');
      renderTemplates(templates.templates);
      applyInterfaceMode();
      await loadReport({ preferLatest: true });
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
    root_artifacts_dir = str(Path(artifacts_dir))
    jobs = DashboardJobStore(storage_dir=Path(root_artifacts_dir) / ".ese-dashboard-jobs")
    bootstrap = {
        "artifacts_dir": root_artifacts_dir,
        "config_path": config_path,
        "repo_path": str(Path.cwd()),
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

        def _send_text(
            self,
            body: str,
            content_type: str = "text/html; charset=utf-8",
            *,
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
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

            if parsed.path == "/api/history":
                self._send_json({"runs": list_recent_runs(self._artifacts_dir_from_query())})
                return

            if parsed.path == "/api/recommend-template":
                query = parse_qs(parsed.query)
                scope = query.get("scope", [""])[0]
                self._send_json({"template_key": recommend_template_for_scope(scope)})
                return

            if parsed.path == "/api/export":
                query = parse_qs(parsed.query)
                export_format = query.get("format", ["sarif"])[0]
                try:
                    body, content_type, filename = _export_report_payload(
                        self._artifacts_dir_from_query(),
                        export_format,
                    )
                except (ConfigValidationError, RunReportError) as err:
                    self._send_json({"error": str(err)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_text(
                    body,
                    content_type=content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
                return

            if parsed.path == "/api/artifact":
                query = parse_qs(parsed.query)
                role = query.get("role", [""])[0].strip() or None
                document = query.get("document", [""])[0].strip() or None
                try:
                    payload = load_artifact_view(
                        self._artifacts_dir_from_query(),
                        role=role,
                        document=document,
                    )
                except RunReportError as err:
                    self._send_json({"error": str(err)}, status=HTTPStatus.NOT_FOUND)
                    return
                self._send_json(payload)
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
                        **_task_run_kwargs(payload, root_artifacts_dir=root_artifacts_dir),
                    )
                    self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                    return

                if parsed.path == "/api/run-config":
                    config_path_value = str(payload.get("config_path") or "").strip()
                    if not config_path_value:
                        raise ConfigValidationError("config_path is required for /api/run-config.")
                    requested_dir = str(payload.get("artifacts_dir") or root_artifacts_dir)
                    job_id = jobs.start(
                        "config-run",
                        _run_config_job,
                        config_path=config_path_value,
                        artifacts_dir=_allocate_run_artifacts_dir(requested_dir, kind="config-run"),
                        scope=str(payload.get("scope")) if payload.get("scope") else None,
                    )
                    self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
                    return

                if parsed.path == "/api/pr":
                    run_artifacts_dir = _allocate_run_artifacts_dir(
                        str(payload.get("artifacts_dir") or root_artifacts_dir),
                        kind="pr-review",
                    )
                    job_id = jobs.start(
                        "pr-review",
                        _run_pr_job,
                        repo_path=str(payload.get("repo_path") or "."),
                        pr=str(payload.get("pr")) if payload.get("pr") else None,
                        base=str(payload.get("base")) if payload.get("base") else None,
                        head=str(payload.get("head")) if payload.get("head") else None,
                        focus=str(payload.get("focus")) if payload.get("focus") else None,
                        provider=str(payload.get("provider") or "openai"),
                        execution_mode=str(payload.get("execution_mode") or "auto"),
                        artifacts_dir=run_artifacts_dir,
                        model=str(payload.get("model")) if payload.get("model") else None,
                        runtime_adapter=str(payload.get("runtime_adapter")) if payload.get("runtime_adapter") else None,
                        base_url=str(payload.get("base_url")) if payload.get("base_url") else None,
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

                if parsed.path == "/api/feedback":
                    entry = record_feedback(
                        str(payload.get("artifacts_dir") or root_artifacts_dir),
                        role=str(payload.get("role") or ""),
                        title=str(payload.get("title") or ""),
                        feedback=str(payload.get("feedback") or ""),
                        artifacts_dir=str(payload.get("artifacts_dir") or root_artifacts_dir),
                        details=str(payload.get("details")) if payload.get("details") else None,
                    )
                    self._send_json({"entry": entry}, status=HTTPStatus.CREATED)
                    return
            except (ConfigValidationError, PullRequestReviewError, ValueError) as err:
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
