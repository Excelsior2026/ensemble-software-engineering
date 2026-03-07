"""Opinionated task templates and config builders for ESE."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ese.config import ConfigValidationError, validate_config, write_config
from ese.init_wizard import (
    COMMON_MODELS_BY_PROVIDER,
    GOAL_DEFAULT_ROLES,
    PROVIDER_SUPPORT,
    RECOMMENDED_MODEL_BY_PROVIDER_GOAL,
    _apply_simple_mode_model_diversity,
    _default_api_key_env,
    _ensemble_constraints,
    _roles_for_preset,
)
from ese.pipeline import run_pipeline

AUTO_EXECUTION_MODE = "auto"
DEMO_EXECUTION_MODE = "demo"
LIVE_EXECUTION_MODE = "live"
SUPPORTED_EXECUTION_MODES = {
    AUTO_EXECUTION_MODE,
    DEMO_EXECUTION_MODE,
    LIVE_EXECUTION_MODE,
}


@dataclass(frozen=True)
class TaskTemplate:
    key: str
    title: str
    summary: str
    preset: str
    goal_profile: str
    roles: tuple[str, ...]
    fail_on_high: bool = True
    mode: str = "ensemble"


TASK_TEMPLATES: dict[str, TaskTemplate] = {
    "feature-delivery": TaskTemplate(
        key="feature-delivery",
        title="Feature Delivery",
        summary="Balanced implementation plan with reviewer, security, testing, and performance coverage.",
        preset="balanced",
        goal_profile="balanced",
        roles=tuple(GOAL_DEFAULT_ROLES["balanced"]),
    ),
    "release-readiness": TaskTemplate(
        key="release-readiness",
        title="Release Readiness",
        summary="Adds rollout, operational, documentation, and go/no-go checks before shipping.",
        preset="paranoid",
        goal_profile="security-heavy",
        roles=(
            "architect",
            "implementer",
            "adversarial_reviewer",
            "security_auditor",
            "test_generator",
            "performance_analyst",
            "documentation_writer",
            "devops_sre",
            "release_manager",
        ),
    ),
    "security-hardening": TaskTemplate(
        key="security-hardening",
        title="Security Hardening",
        summary="Heavier security, release, and operational scrutiny for risky changes.",
        preset="paranoid",
        goal_profile="security-heavy",
        roles=tuple(GOAL_DEFAULT_ROLES["security-heavy"]),
    ),
    "performance-pass": TaskTemplate(
        key="performance-pass",
        title="Performance Pass",
        summary="Focuses the ensemble on bottlenecks, scaling risk, and test coverage for hot paths.",
        preset="strict",
        goal_profile="high-quality",
        roles=(
            "architect",
            "implementer",
            "adversarial_reviewer",
            "test_generator",
            "performance_analyst",
        ),
    ),
    "documentation-refresh": TaskTemplate(
        key="documentation-refresh",
        title="Documentation Refresh",
        summary="Generates implementation guidance plus docs and release notes for adoption-heavy changes.",
        preset="strict",
        goal_profile="high-quality",
        roles=(
            "architect",
            "implementer",
            "adversarial_reviewer",
            "documentation_writer",
            "release_manager",
        ),
        fail_on_high=False,
    ),
}


def list_task_templates() -> list[TaskTemplate]:
    return list(TASK_TEMPLATES.values())


def resolve_task_template(template_key: str) -> TaskTemplate:
    key = (template_key or "").strip().lower()
    template = TASK_TEMPLATES.get(key)
    if template is None:
        available = ", ".join(sorted(TASK_TEMPLATES))
        raise ConfigValidationError(f"Unknown task template '{template_key}'. Choose one of: {available}")
    return template


def recommended_model_for(provider: str, goal_profile: str) -> str | None:
    return RECOMMENDED_MODEL_BY_PROVIDER_GOAL.get(provider, {}).get(goal_profile)


def _default_model_for(provider: str, goal_profile: str) -> str:
    recommended = recommended_model_for(provider, goal_profile)
    if recommended:
        return recommended

    common = COMMON_MODELS_BY_PROVIDER.get(provider, [])
    if common:
        return common[0]

    if provider == "custom_api":
        return "custom-model"

    return "model"


def _supports_builtin_live(provider: str) -> bool:
    support = PROVIDER_SUPPORT.get(provider, {})
    return bool(support.get("supports_live"))


def _resolve_execution_mode(
    *,
    provider: str,
    requested_mode: str,
    runtime_adapter: str | None,
    base_url: str | None,
) -> str:
    mode = (requested_mode or AUTO_EXECUTION_MODE).strip().lower()
    if mode not in SUPPORTED_EXECUTION_MODES:
        available = ", ".join(sorted(SUPPORTED_EXECUTION_MODES))
        raise ConfigValidationError(f"Unsupported execution mode '{requested_mode}'. Choose one of: {available}")

    if mode == AUTO_EXECUTION_MODE:
        if provider == "custom_api":
            api_key_present = bool(os.getenv(_default_api_key_env(provider)))
            return LIVE_EXECUTION_MODE if api_key_present and bool(base_url) else DEMO_EXECUTION_MODE
        if _supports_builtin_live(provider) and os.getenv(_default_api_key_env(provider)):
            return LIVE_EXECUTION_MODE
        if runtime_adapter:
            return LIVE_EXECUTION_MODE
        return DEMO_EXECUTION_MODE

    return mode


def build_task_config(
    *,
    scope: str,
    template_key: str,
    provider: str = "openai",
    execution_mode: str = AUTO_EXECUTION_MODE,
    artifacts_dir: str = "artifacts",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
    enforce_json: bool = True,
    fail_on_high: bool | None = None,
) -> dict[str, Any]:
    clean_scope = (scope or "").strip()
    if not clean_scope:
        raise ConfigValidationError("Task scope is required.")

    clean_provider = (provider or "openai").strip().lower()
    template = resolve_task_template(template_key)
    effective_mode = _resolve_execution_mode(
        provider=clean_provider,
        requested_mode=execution_mode,
        runtime_adapter=runtime_adapter,
        base_url=base_url,
    )
    selected_model = (model or "").strip() or _default_model_for(clean_provider, template.goal_profile)

    provider_cfg: dict[str, Any] = {
        "name": (provider_name or clean_provider).strip() if clean_provider == "custom_api" else clean_provider,
        "model": selected_model,
    }
    if base_url and clean_provider == "custom_api":
        provider_cfg["base_url"] = base_url.strip()

    roles_cfg = _roles_for_preset(template.preset, list(template.roles))

    cfg: dict[str, Any] = {
        "version": 1,
        "mode": template.mode,
        "provider": provider_cfg,
        "preset": template.preset,
        "roles": roles_cfg,
        "input": {
            "scope": clean_scope,
        },
        "output": {
            "artifacts_dir": artifacts_dir,
            "enforce_json": enforce_json,
        },
        "gating": {
            "fail_on_high": template.fail_on_high if fail_on_high is None else fail_on_high,
        },
        "runtime": {
            "timeout_seconds": 60,
            "max_retries": 2,
            "retry_backoff_seconds": 1.0,
        },
    }

    if template.mode == "ensemble":
        cfg["constraints"] = _ensemble_constraints(selected_roles=list(template.roles))
        _apply_simple_mode_model_diversity(
            cfg,
            provider=clean_provider,
            selected_roles=list(template.roles),
        )

    if effective_mode == DEMO_EXECUTION_MODE:
        cfg["runtime"]["adapter"] = "dry-run"
    elif runtime_adapter:
        cfg["runtime"]["adapter"] = runtime_adapter.strip()
    elif clean_provider in {"openai", "custom_api"}:
        cfg["runtime"]["adapter"] = clean_provider
    else:
        raise ConfigValidationError(
            f"Live execution for provider '{clean_provider}' requires runtime_adapter in module:function format.",
        )

    if cfg["runtime"]["adapter"] in {"openai", "custom_api"} or clean_provider == "custom_api":
        cfg["provider"]["api_key_env"] = (api_key_env or _default_api_key_env(clean_provider)).strip()

    if cfg["runtime"]["adapter"] == "openai":
        cfg["runtime"]["openai"] = {
            "base_url": "https://api.openai.com/v1",
        }

    if cfg["runtime"]["adapter"] == "custom_api":
        if not base_url:
            raise ConfigValidationError("custom_api live runs require base_url.")
        cfg["runtime"]["custom_api"] = {
            "base_url": base_url.strip(),
        }
        cfg["provider"]["base_url"] = base_url.strip()

    return validate_config(cfg, source="<task>")


def run_task_pipeline(
    *,
    scope: str,
    template_key: str,
    provider: str = "openai",
    execution_mode: str = AUTO_EXECUTION_MODE,
    artifacts_dir: str = "artifacts",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
    enforce_json: bool = True,
    fail_on_high: bool | None = None,
    config_path: str | None = None,
) -> tuple[dict[str, Any], str]:
    cfg = build_task_config(
        scope=scope,
        template_key=template_key,
        provider=provider,
        execution_mode=execution_mode,
        artifacts_dir=artifacts_dir,
        model=model,
        api_key_env=api_key_env,
        runtime_adapter=runtime_adapter,
        provider_name=provider_name,
        base_url=base_url,
        enforce_json=enforce_json,
        fail_on_high=fail_on_high,
    )

    if config_path:
        write_config(config_path, cfg)

    summary_path = run_pipeline(cfg=cfg, artifacts_dir=artifacts_dir)
    return cfg, summary_path
