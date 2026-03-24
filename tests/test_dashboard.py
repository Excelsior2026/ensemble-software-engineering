from __future__ import annotations

import time
from pathlib import Path

from ese.dashboard import DashboardJobStore, _allocate_run_artifacts_dir, _export_report_payload, _task_run_kwargs
from ese.pipeline import run_pipeline


def test_allocate_run_artifacts_dir_uses_requested_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    allocated = _allocate_run_artifacts_dir(str(root), kind="task-run")

    assert Path(allocated).parent == root
    assert Path(allocated).name.endswith("task-run")


def test_allocate_run_artifacts_dir_uses_parent_for_existing_run_dir(tmp_path: Path) -> None:
    existing_run = tmp_path / "artifacts" / "20260308-task-run"
    existing_run.mkdir(parents=True)
    (existing_run / "pipeline_state.json").write_text("{}", encoding="utf-8")

    allocated = _allocate_run_artifacts_dir(str(existing_run), kind="task-run")

    assert Path(allocated).parent == existing_run.parent
    assert Path(allocated) != existing_run


def test_task_run_kwargs_preserve_repo_context_flags(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    kwargs = _task_run_kwargs(
        {
            "scope": "Review a local patch",
            "provider": "openai",
            "repo_path": "/tmp/repo",
            "include_repo_status": "false",
            "include_repo_diff": "true",
            "max_repo_diff_chars": "1200",
        },
        root_artifacts_dir=str(root),
    )

    assert kwargs["repo_path"] == "/tmp/repo"
    assert kwargs["include_repo_status"] is False
    assert kwargs["include_repo_diff"] is True
    assert kwargs["max_repo_diff_chars"] == 1200
    assert Path(kwargs["artifacts_dir"]).parent == root


def test_export_report_payload_returns_requested_format(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(
        {
            "version": 1,
            "mode": "ensemble",
            "provider": {"name": "openai", "model": "gpt-5-mini", "api_key_env": "OPENAI_API_KEY"},
            "roles": {"architect": {}, "implementer": {}},
            "runtime": {"adapter": "dry-run"},
            "input": {"scope": "Generate a small report"},
        },
        artifacts_dir=str(artifacts_dir),
    )

    sarif_body, sarif_type, sarif_name = _export_report_payload(str(artifacts_dir), "sarif")
    junit_body, junit_type, junit_name = _export_report_payload(str(artifacts_dir), "junit")

    assert sarif_type.startswith("application/sarif+json")
    assert sarif_name == "ese_report.sarif.json"
    assert '"version": "2.1.0"' in sarif_body
    assert junit_type.startswith("application/xml")
    assert junit_name == "ese_report.junit.xml"
    assert "<testsuite" in junit_body


def test_dashboard_job_store_persists_jobs(tmp_path: Path) -> None:
    store = DashboardJobStore(storage_dir=tmp_path / "job-store")
    job_id = store.start("unit-job", lambda: {"ok": True})

    for _ in range(50):
        job = store.get(job_id)
        if job and job["status"] == "completed":
            break
        time.sleep(0.01)

    reloaded = DashboardJobStore(storage_dir=tmp_path / "job-store")
    persisted = reloaded.get(job_id)

    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["result"] == {"ok": True}
    assert (tmp_path / "job-store" / f"{job_id}.json").exists()
