from __future__ import annotations

import json
from pathlib import Path

import pytest

from ese.pipeline import PipelineError, _role_prompt, run_pipeline


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
    assert (artifacts_dir / "ese_config.snapshot.yaml").exists()
    assert (artifacts_dir / "01_architect.json").exists()
    assert (artifacts_dir / "02_implementer.json").exists()
    assert (artifacts_dir / "03_adversarial_reviewer.json").exists()

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["config_snapshot"] == str(artifacts_dir / "ese_config.snapshot.yaml")
    executed_roles = [item["role"] for item in state["execution"]]
    assert executed_roles == ["architect", "implementer", "adversarial_reviewer"]


def test_pipeline_context_chaining_visible_in_dry_run_outputs(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    implementer_output = json.loads((artifacts_dir / "02_implementer.json").read_text(encoding="utf-8"))
    reviewer_output = json.loads((artifacts_dir / "03_adversarial_reviewer.json").read_text(encoding="utf-8"))

    assert implementer_output["metadata"]["context_keys"] == ["architect"]
    assert reviewer_output["metadata"]["context_keys"] == ["architect", "implementer"]


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


def test_pipeline_uses_configured_artifacts_dir_when_not_overridden(tmp_path: Path) -> None:
    cfg = _cfg()
    configured_dir = tmp_path / "configured-artifacts"
    cfg["output"] = {"artifacts_dir": str(configured_dir), "enforce_json": True}

    summary_path = run_pipeline(cfg)

    assert summary_path == str(configured_dir / "ese_summary.md")
    assert (configured_dir / "01_architect.json").exists()


def test_pipeline_blocks_on_high_severity_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    artifacts_dir = tmp_path / "artifacts"

    def _gating_adapter(**kwargs) -> str:  # noqa: ANN003
        role = kwargs["role"]
        if role == "architect":
            return json.dumps(
                {
                    "summary": "Architecture complete.",
                    "findings": [],
                    "artifacts": [],
                    "next_steps": [],
                },
            )
        return json.dumps(
            {
                "summary": "Reviewer found a release blocker.",
                "findings": [
                    {
                        "severity": "HIGH",
                        "title": "Release blocker",
                        "details": "A critical defect must be fixed before continuing.",
                    },
                ],
                "artifacts": [],
                "next_steps": ["Fix the blocker."],
            },
        )

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("test-gating", _gating_adapter))

    with pytest.raises(PipelineError) as exc:
        run_pipeline(cfg, artifacts_dir=str(artifacts_dir))

    assert "Pipeline gated by HIGH severity findings" in str(exc.value)

    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert "Release blocker" in state["failure"]


def test_pipeline_can_rerun_from_a_specific_role(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    artifacts_dir = tmp_path / "artifacts"
    calls: list[str] = []

    def _tracking_adapter(**kwargs) -> str:  # noqa: ANN003
        role = kwargs["role"]
        calls.append(role)
        return json.dumps(
            {
                "summary": f"{role} finished.",
                "findings": [],
                "artifacts": [],
                "next_steps": [],
            },
        )

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("tracking", _tracking_adapter))

    run_pipeline(cfg, artifacts_dir=str(artifacts_dir))
    assert calls == ["architect", "implementer", "adversarial_reviewer"]

    calls.clear()
    run_pipeline(cfg, artifacts_dir=str(artifacts_dir), start_role="implementer")

    assert calls == ["implementer", "adversarial_reviewer"]
    state = json.loads((artifacts_dir / "pipeline_state.json").read_text(encoding="utf-8"))
    executed_roles = [item["role"] for item in state["execution"]]
    assert executed_roles == ["architect", "implementer", "adversarial_reviewer"]


def test_pipeline_rejects_non_json_output_when_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts_dir = tmp_path / "artifacts"

    def _bad_adapter(**kwargs) -> str:  # noqa: ANN003
        return "not json"

    monkeypatch.setattr("ese.pipeline._resolve_adapter", lambda cfg: ("bad-adapter", _bad_adapter))

    with pytest.raises(PipelineError) as exc:
        run_pipeline(_cfg(), artifacts_dir=str(artifacts_dir))

    assert "must be valid JSON" in str(exc.value)


def test_pipeline_requires_explicit_scope(tmp_path: Path) -> None:
    cfg = _cfg()
    cfg.pop("input")

    with pytest.raises(PipelineError) as exc:
        run_pipeline(cfg, artifacts_dir=str(tmp_path / "artifacts"))

    assert "Set input.scope in the config or pass --scope" in str(exc.value)


def test_pipeline_rejects_empty_role_configuration(tmp_path: Path) -> None:
    cfg = _cfg()
    cfg["roles"] = {}

    with pytest.raises(PipelineError) as exc:
        run_pipeline(cfg, artifacts_dir=str(tmp_path / "artifacts"))

    assert "No roles configured" in str(exc.value)


def test_documentation_writer_prompt_is_specialized() -> None:
    prompt = _role_prompt(
        role="documentation_writer",
        scope="Document a new authentication flow",
        outputs={
            "architect": "Introduce auth middleware and session contracts.",
            "implementer": "Added login handlers and token refresh support.",
        },
        enforce_json=True,
    )

    lowered = prompt.lower()
    assert "documentation deliverables" in lowered
    assert "readme updates" in lowered
    assert "migration guidance" in lowered


def test_release_manager_prompt_is_specialized() -> None:
    prompt = _role_prompt(
        role="release_manager",
        scope="Launch a staged rollout for feature flags",
        outputs={
            "architect": "Use a staged rollout with metrics checkpoints.",
            "implementer": "Implemented flag checks and telemetry hooks.",
        },
        enforce_json=True,
    )

    lowered = prompt.lower()
    assert "assess release readiness" in lowered
    assert "rollback readiness" in lowered
