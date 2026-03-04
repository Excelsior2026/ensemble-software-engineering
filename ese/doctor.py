"""ESE doctor checks.

Validates config and enforces ensemble role separation constraints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ese.config import ConfigValidationError, load_config, resolve_role_model

DEFAULT_ROLE_NAMES = [
    "architect",
    "implementer",
    "adversarial_reviewer",
    "security_auditor",
    "test_generator",
    "performance_analyst",
]


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

    if roles:
        return roles
    return DEFAULT_ROLE_NAMES.copy()


def run_doctor(config_path: str) -> Tuple[bool, List[str], Dict[str, str]]:
    try:
        cfg = load_config(config_path)
    except ConfigValidationError as err:
        return False, [str(err)], {}

    mode = (cfg.get("mode") or "ensemble").strip().lower()

    role_names = _collect_role_names(cfg)
    role_models = {r: resolve_role_model(cfg, r) for r in role_names}

    if mode == "solo":
        return True, ["SOLO MODE: reduced independence; higher self-confirmation risk."], role_models

    constraints = cfg.get("constraints") or {}
    pairs = constraints.get("disallow_same_model_pairs") or []

    violations: List[str] = []
    for a, b in pairs:
        if role_models.get(a) == role_models.get(b):
            violations.append(f"{a} and {b} share model {role_models[a]}")

    ok = len(violations) == 0
    return ok, violations, role_models
