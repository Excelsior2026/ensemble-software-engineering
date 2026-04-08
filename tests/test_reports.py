from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ese.feedback import record_feedback
from ese.pipeline import run_pipeline
from ese.reports import (
    build_release_simulation,
    collect_run_report,
    list_recent_runs,
    load_artifact_view,
    render_junit,
    render_report_text,
    render_sarif,
    render_status_text,
)


def _cfg() -> dict:
    return {
        "version": 1,
        "mode": "ensemble",
        "provider": {
            "name": "openai",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
        "roles": {
            "architect": {},
            "implementer": {},
            "adversarial_reviewer": {},
        },
        "runtime": {
            "adapter": "dry-run",
        },
        "input": {
            "scope": "Build a safer deployment checklist",
        },
    }


def _json_report(summary: str, **overrides) -> str:
    payload = {
        "summary": summary,
        "confidence": "MEDIUM",
        "assumptions": [],
        "unknowns": [],
        "findings": [],
        "artifacts": [],
        "next_steps": [],
        "code_suggestions": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_collect_run_report_summarizes_pipeline_outputs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    report = collect_run_report(str(artifacts_dir))

    assert report["status"] == "completed"
    assert report["run_id"]
    assert report["assurance_level"] == "standard"
    assert report["evidence_state"] == "ready"
    assert report["finding_count"] == 0
    assert len(report["roles"]) == 3
    assert report["roles"][0]["role"] == "architect"
    assert report["config_snapshot"] == str(artifacts_dir / "ese_config.snapshot.yaml")
    assert report["documents"][0]["key"] == "summary"
    assert report["updated_at"]


def test_render_status_text_includes_assurance_and_run_id(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    report = collect_run_report(str(artifacts_dir))
    rendered = render_status_text(report)

    assert "Assurance: standard" in rendered
    assert "Evidence State: ready" in rendered
    assert "Run ID:" in rendered


def test_render_report_text_includes_confidence_sections_and_recurring_unknowns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir = tmp_path / "artifacts"

    def _adapter(**kwargs) -> str:  # noqa: ANN003
        role = kwargs["role"]
        if role == "architect":
            return _json_report(
                "Architecture found a blocker.",
                confidence="LOW",
                unknowns=["Traffic patterns are unknown."],
                findings=[
                    {
                        "severity": "HIGH",
                        "title": "Missing rollback path",
                        "details": "Rollback sequencing is not documented.",
                    },
                ],
            )
        if role == "implementer":
            return _json_report(
                "Implementation complete.",
                confidence="MEDIUM",
                unknowns=["Traffic patterns are unknown."],
            )
        return _json_report("Review complete.", confidence="HIGH")

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("reporting", _adapter))
    cfg = _cfg()
    cfg["gating"] = {"fail_on_high": False}
    run_pipeline(cfg, artifacts_dir=str(artifacts_dir))

    report = collect_run_report(str(artifacts_dir))
    rendered = render_report_text(report)

    assert "confidence=LOW" in rendered
    assert "Low-confidence blockers:" in rendered
    assert "Recurring unknowns:" in rendered


def test_degraded_assurance_makes_release_simulation_not_ready(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    cfg = _cfg()
    cfg["mode"] = "solo"
    run_pipeline(cfg, artifacts_dir=str(artifacts_dir))

    report = collect_run_report(str(artifacts_dir))
    release = build_release_simulation(report)

    assert report["assurance_level"] == "degraded"
    assert report["evidence_state"] == "draft"
    assert not release["ready_for_release"]
    assert "degraded assurance" in release["summary"].lower()


def test_manual_evidence_state_is_reported_in_release_simulation(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))
    state_path = artifacts_dir / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["evidence_state"] = "approved"
    state["evidence_state_history"] = [
        {
            "state": "approved",
            "source": "manual",
            "actor": "bill",
            "updated_at": "2026-04-07T10:00:00+00:00",
        }
    ]
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    report = collect_run_report(str(artifacts_dir))
    release = build_release_simulation(report)

    assert report["evidence_state"] == "approved"
    assert report["evidence_state_source"] == "manual"
    assert release["evidence_state"] == "approved"
    assert release["ready_for_release"]


def test_release_simulation_summary_does_not_claim_approval_when_blocked(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))
    state_path = artifacts_dir / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["evidence_state"] = "approved"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    architect_artifact_path = Path(
        next(
            item["artifact"]
            for item in state["execution"]
            if item.get("role") == "architect"
        )
    )
    architect_artifact = json.loads(architect_artifact_path.read_text(encoding="utf-8"))
    architect_artifact["findings"] = [
        {
            "severity": "HIGH",
            "title": "Rollback plan missing",
            "details": "The rollout has no verified rollback sequence.",
        }
    ]
    architect_artifact_path.write_text(
        json.dumps(architect_artifact, indent=2) + "\n",
        encoding="utf-8",
    )

    report = collect_run_report(str(artifacts_dir))
    release = build_release_simulation(report)

    assert report["evidence_state"] == "approved"
    assert not release["ready_for_release"]
    assert "approved for release" not in release["summary"].lower()
    assert "hold release" in release["summary"].lower()


def test_list_recent_runs_discovers_sibling_runs(tmp_path: Path) -> None:
    run_one = tmp_path / "runs" / "20260308-task-run"
    run_two = tmp_path / "runs" / "20260309-pr-review"
    run_pipeline(_cfg(), artifacts_dir=str(run_one))
    run_pipeline(_cfg(), artifacts_dir=str(run_two))

    os.utime(run_one / "pipeline_state.json", (1, 1))
    os.utime(run_two / "pipeline_state.json", (2, 2))

    runs = list_recent_runs(str(run_one))

    assert [item["artifacts_dir"] for item in runs] == [str(run_two), str(run_one)]
    assert runs[0]["status"] == "completed"
    assert runs[0]["role_count"] == 3


def test_load_artifact_view_supports_role_and_document_targets(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    role_view = load_artifact_view(str(artifacts_dir), role="architect")
    summary_view = load_artifact_view(str(artifacts_dir), document="summary")

    assert role_view["kind"] == "role"
    assert role_view["key"] == "architect"
    assert "summary" in role_view["content"]
    assert summary_view["kind"] == "document"
    assert summary_view["key"] == "summary"
    assert "# ESE Summary" in summary_view["content"]


def test_collect_run_report_and_load_artifact_view_support_external_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    monkeypatch.setattr(
        "ese.reports.list_available_artifact_view_documents",
        lambda report: [
            {
                "key": "view:release-brief",
                "title": "Release Brief",
                "path": "view:release-brief",
                "format": "md",
                "source": "external_view",
            }
        ],
    )
    monkeypatch.setattr(
        "ese.reports.render_external_artifact_view",
        lambda report, document, max_chars: {
            "kind": "document",
            "key": document,
            "title": "Release Brief",
            "path": document,
            "format": "md",
            "content": "# Release Brief\n\nAll clear.\n",
            "truncated": False,
            "source": "external_view",
        },
    )

    report = collect_run_report(str(artifacts_dir))
    view = load_artifact_view(str(artifacts_dir), document="view:release-brief")

    assert any(item["key"] == "view:release-brief" for item in report["documents"])
    assert view["source"] == "external_view"
    assert "All clear." in view["content"]


def test_collect_run_report_includes_comparison_feedback_and_code_suggestions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runs"
    run_one = root / "20260308-task-run"
    run_two = root / "20260309-task-run"

    def _clean_adapter(**kwargs) -> str:  # noqa: ANN003
        role = kwargs["role"]
        return _json_report(f"{role} completed cleanly.")

    def _finding_adapter(**kwargs) -> str:  # noqa: ANN003
        role = kwargs["role"]
        if role == "adversarial_reviewer":
            return _json_report(
                "Reviewer found a concrete defect.",
                findings=[
                    {
                        "severity": "HIGH",
                        "title": "Null dereference risk",
                        "details": "Guard the optional config object before dereferencing it in the request path.",
                    },
                ],
                next_steps=["Add a guard clause and cover it with a regression test."],
                code_suggestions=[
                    {
                        "path": "src/request_handler.py",
                        "kind": "patch",
                        "summary": "Guard the optional config before access",
                        "suggestion": "Guard the optional config object before dereferencing it in the request path.",
                        "snippet": "if config is None:\n    return default_response()",
                    },
                ],
            )
        return _clean_adapter(**kwargs)

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("clean", _clean_adapter))
    run_pipeline(_cfg(), artifacts_dir=str(run_one))

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("finding", _finding_adapter))
    cfg = _cfg()
    cfg["gating"] = {"fail_on_high": False}
    run_pipeline(cfg, artifacts_dir=str(run_two))
    record_feedback(
        run_two,
        role="adversarial_reviewer",
        title="Null dereference risk",
        feedback="useful",
    )

    report = collect_run_report(str(run_two))

    assert report["comparison"]["previous_artifacts_dir"] == str(run_one)
    assert len(report["comparison"]["new_blockers"]) == 1
    assert report["feedback"]["counts"]["useful"] == 1
    assert report["code_suggestions"][0]["suggestion"].startswith("Guard the optional config object")
    assert report["code_suggestions"][0]["path"] == "src/request_handler.py"
    assert "default_response" in report["code_suggestions"][0]["snippet"]
    assert report["code_suggestion_groups"]["paths"] == ["src/request_handler.py"]
    assert report["consensus"]["solo_blockers"][0]["title"] == "Null dereference risk"
    assert {"code_suggestions_md", "code_suggestions_json"} <= {
        item["key"]
        for item in report["documents"]
    }

    sarif = render_sarif(report)
    junit = render_junit(report)
    assert "Null dereference risk" in sarif
    assert "<failure" in junit
    assert (run_two / "code_suggestions.md").exists()
    assert (run_two / "code_suggestions.json").exists()
