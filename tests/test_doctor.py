from __future__ import annotations

import yaml

from ese.doctor import run_doctor


def _write_cfg(path, cfg: dict) -> str:
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


def test_doctor_detects_shared_model_violation(tmp_path) -> None:
    cfg = _base_cfg()
    cfg["roles"]["implementer"]["model"] = "gpt-5"
    path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    ok, violations, role_models = run_doctor(path)

    assert not ok
    assert "architect and implementer share model openai:gpt-5" in violations
    assert role_models["architect"] == "openai:gpt-5"


def test_doctor_uses_dynamic_role_list(tmp_path) -> None:
    cfg = _base_cfg()
    cfg["roles"] = {
        "architect": {"model": "gpt-5"},
        "documentation_writer": {"model": "gpt-5-mini"},
    }
    cfg["constraints"]["disallow_same_model_pairs"] = [["architect", "documentation_writer"]]
    path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    ok, violations, role_models = run_doctor(path)

    assert ok
    assert violations == []
    assert set(role_models.keys()) == {"architect", "documentation_writer"}


def test_doctor_reports_config_validation_error(tmp_path) -> None:
    cfg = _base_cfg()
    cfg["version"] = 9
    path = _write_cfg(tmp_path / "ese.config.yaml", cfg)

    ok, violations, role_models = run_doctor(path)

    assert not ok
    assert role_models == {}
    assert len(violations) == 1
    assert "unsupported version 9; expected 1" in violations[0]
