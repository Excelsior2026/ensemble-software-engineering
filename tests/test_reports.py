from __future__ import annotations

from pathlib import Path

from ese.reports import collect_run_report
from ese.pipeline import run_pipeline


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


def test_collect_run_report_summarizes_pipeline_outputs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    report = collect_run_report(str(artifacts_dir))

    assert report["status"] == "completed"
    assert report["finding_count"] == 0
    assert len(report["roles"]) == 3
    assert report["roles"][0]["role"] == "architect"
    assert report["config_snapshot"] == str(artifacts_dir / "ese_config.snapshot.yaml")
