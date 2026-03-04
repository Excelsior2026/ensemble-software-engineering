from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from ese.cli import app


runner = CliRunner()


def _write_cfg(path: Path, cfg: dict) -> str:
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return str(path)


def _base_cfg() -> dict:
    return {
        "version": 1,
        "mode": "ensemble",
        "provider": {
            "name": "openai",
            "model": "gpt-5-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
        "roles": {
            "architect": {"model": "gpt-5"},
            "implementer": {"model": "gpt-5-mini"},
        },
        "constraints": {
            "disallow_same_model_pairs": [["architect", "implementer"]],
        },
        "runtime": {
            "adapter": "dry-run",
        },
    }


def test_roles_command_lists_known_role() -> None:
    result = runner.invoke(app, ["roles"])

    assert result.exit_code == 0
    assert "architect" in result.stdout


def test_doctor_command_exits_nonzero_on_violation(tmp_path: Path) -> None:
    cfg = _base_cfg()
    cfg["roles"]["implementer"]["model"] = "gpt-5"
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    result = runner.invoke(app, ["doctor", "--config", config_path])

    assert result.exit_code == 2
    assert "share model" in result.stdout


def test_start_command_runs_pipeline_and_writes_summary(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["start", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()
    assert "Pipeline completed" in result.stdout


def test_run_alias_still_works(tmp_path: Path) -> None:
    cfg = _base_cfg()
    config_path = _write_cfg(tmp_path / "ese.config.yaml", cfg)
    artifacts_dir = tmp_path / "artifacts"

    result = runner.invoke(
        app,
        ["run", "--config", config_path, "--artifacts-dir", str(artifacts_dir)],
    )

    assert result.exit_code == 0
    assert (artifacts_dir / "ese_summary.md").exists()
