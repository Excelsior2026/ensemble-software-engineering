from __future__ import annotations

import json
from pathlib import Path

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
            "scope": "Build a to-do CLI",
        },
    }


def test_pipeline_writes_expected_artifacts_and_state(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"

    summary_path = run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    assert summary_path == str(artifacts_dir / "ese_summary.md")
    assert (artifacts_dir / "01_architect.md").exists()
    assert (artifacts_dir / "02_implementer.md").exists()
    assert (artifacts_dir / "03_adversarial_reviewer.md").exists()

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    executed_roles = [item["role"] for item in state["execution"]]
    assert executed_roles == ["architect", "implementer", "adversarial_reviewer"]


def test_pipeline_context_chaining_visible_in_dry_run_outputs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    implementer_output = (artifacts_dir / "02_implementer.md").read_text(encoding="utf-8")
    reviewer_output = (artifacts_dir / "03_adversarial_reviewer.md").read_text(encoding="utf-8")

    assert "Context keys:" in implementer_output
    assert "architect" in implementer_output
    assert "Context keys:" in reviewer_output
    assert "implementer" in reviewer_output


def test_pipeline_orders_custom_roles_after_builtin_order(tmp_path: Path) -> None:
    cfg = _cfg()
    cfg["roles"] = {
        "custom_role": {},
        "implementer": {},
        "architect": {},
    }

    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(cfg, artifacts_dir=str(artifacts_dir))

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    executed_roles = [item["role"] for item in state["execution"]]
    assert executed_roles == ["architect", "implementer", "custom_role"]
