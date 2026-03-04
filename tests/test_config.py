from __future__ import annotations

import pytest

from ese.config import ConfigValidationError, resolve_role_model, validate_config


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
            "architect": {},
            "implementer": {},
        },
        "constraints": {
            "disallow_same_model_pairs": [["architect", "implementer"]],
        },
        "runtime": {
            "adapter": "dry-run",
            "timeout_seconds": 60,
            "max_retries": 2,
            "retry_backoff_seconds": 1.0,
        },
    }


def test_validate_config_accepts_zero_max_retries() -> None:
    cfg = _base_cfg()
    cfg["runtime"]["max_retries"] = 0

    validated = validate_config(cfg)

    assert validated["runtime"]["max_retries"] == 0


def test_validate_config_rejects_version_mismatch() -> None:
    cfg = _base_cfg()
    cfg["version"] = 2

    with pytest.raises(ConfigValidationError) as exc:
        validate_config(cfg, source="test.yaml")

    assert "unsupported version 2; expected 1" in str(exc.value)
    assert "test.yaml" in str(exc.value)


def test_resolve_role_model_prefers_role_override() -> None:
    cfg = _base_cfg()
    cfg["roles"]["architect"] = {"provider": "openrouter", "model": "openai/gpt-5"}

    model_ref = resolve_role_model(cfg, "architect")

    assert model_ref == "openrouter:openai/gpt-5"


def test_custom_api_contract_requires_base_url() -> None:
    cfg = _base_cfg()
    cfg["provider"] = {
        "name": "my-gateway",
        "model": "my-model",
        "api_key_env": "CUSTOM_GATEWAY_TOKEN",
    }
    cfg["runtime"]["adapter"] = "custom_api"

    with pytest.raises(ConfigValidationError) as exc:
        validate_config(cfg, source="test.yaml")

    assert "runtime.adapter=custom_api requires provider.base_url or runtime.custom_api.base_url" in str(exc.value)
