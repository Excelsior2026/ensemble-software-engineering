"""ESE doctor checks.

Validates config and enforces ensemble role separation constraints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ese.config import ConfigValidationError, load_config, resolve_role_model, resolve_scope_text
from ese.provider_runtime import supports_builtin_live


def _collect_role_names(cfg: Dict[str, Any]) -> List[str]:
    roles: List[str] = []
    seen: set[str] = set()

    def add(role: Any) -> None:
        if not isinstance(role, str):
            return
        cleaned = role.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        roles.append(cleaned)

    for role in (cfg.get("roles") or {}).keys():
        add(role)

    constraints = cfg.get("constraints") or {}
    for pair in constraints.get("disallow_same_model_pairs") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            add(pair[0])
            add(pair[1])

    return roles


def evaluate_doctor(cfg: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, str]]:
    mode = (cfg.get("mode") or "ensemble").strip().lower()

    role_names = _collect_role_names(cfg)
    role_models = {r: resolve_role_model(cfg, r) for r in role_names}

    constraints = cfg.get("constraints") or {}
    pairs = constraints.get("disallow_same_model_pairs") or []

    violations: List[str] = []
    if not resolve_scope_text(cfg):
        violations.append("No project scope supplied. Set input.scope in the config or pass --scope.")

    for a, b in pairs:
        if role_models.get(a) == role_models.get(b):
            violations.append(f"{a} and {b} share model {role_models[a]}")

    if violations:
        return False, violations, role_models

    if mode == "solo":
        return True, ["SOLO MODE: reduced independence; higher self-confirmation risk."], role_models

    return True, [], role_models


def build_doctor_guidance(cfg: Dict[str, Any], violations: List[str]) -> List[str]:
    """Suggest concrete configuration fixes for the current doctor result."""
    guidance: List[str] = []
    provider_cfg = cfg.get("provider") or {}
    runtime_cfg = cfg.get("runtime") or {}
    provider_name = str(provider_cfg.get("name") or "").strip().lower()
    adapter_name = str(runtime_cfg.get("adapter") or "dry-run").strip().lower()
    supports_live = supports_builtin_live(provider_name)

    if any("No project scope supplied" in item for item in violations):
        guidance.append("Set input.scope or use `ese task \"...\"` to start from a concrete task description.")

    if any("share model" in item for item in violations):
        guidance.append("Separate architect and implementer models, or reduce the constrained role set for this run.")

    if adapter_name == "dry-run":
        guidance.append("You are in demo mode. Switch runtime.adapter to a live adapter when you want real model execution.")
    elif adapter_name not in {"openai", "local", "custom_api"} and ":" in adapter_name:
        guidance.append("Custom runtime adapter is configured. Re-run with `ese doctor` after adapter changes to keep validation tight.")

    if provider_name and not supports_live and adapter_name == provider_name:
        guidance.append(
            f"{provider_name} does not have a built-in live adapter here. Use demo mode or set runtime.adapter to module:function.",
        )

    if provider_name == "custom_api" and not str(provider_cfg.get("base_url") or "").strip():
        guidance.append("custom_api needs provider.base_url or runtime.custom_api.base_url before live runs will work.")

    if not guidance:
        guidance.append("Configuration is structurally valid. Next check provider credentials and artifacts_dir conventions.")

    return guidance


def run_doctor(config_path: str) -> Tuple[bool, List[str], Dict[str, str]]:
    try:
        cfg = load_config(config_path)
    except ConfigValidationError as err:
        return False, [str(err)], {}

    return evaluate_doctor(cfg)
