"""ESE doctor checks.

Validates config and enforces ensemble role separation constraints.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, cast

from ese.artifact_views import discover_artifact_views
from ese.config import (
    ConfigValidationError,
    load_config,
    resolve_role_identity,
    resolve_role_model,
    resolve_role_provider,
    resolve_scope_text,
)
from ese.config_packs import discover_config_packs
from ese.integrations import discover_integrations
from ese.policy_checks import (
    POLICY_ERROR,
    PolicyCheckContext,
    discover_policy_checks,
    render_policy_message,
)
from ese.policy_checks import (
    evaluate_policy_checks as _evaluate_policy_checks,
)
from ese.provider_runtime import supports_builtin_live
from ese.report_exporters import discover_external_report_exporters

BASELINE_DISALLOW_SAME_MODEL_PAIRS = (
    ("architect", "implementer"),
    ("implementer", "adversarial_reviewer"),
    ("implementer", "security_auditor"),
    ("adversarial_reviewer", "security_auditor"),
    ("implementer", "release_manager"),
)


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
    if isinstance(constraints, dict):
        for key in ("disallow_same_model_pairs", "disallow_same_provider_pairs"):
            for pair in constraints.get(key) or []:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    add(pair[0])
                    add(pair[1])

    return roles


def _normalize_role_pair(pair: Any, *, label: str) -> tuple[str, str]:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError(f"{label} entries must contain exactly two role names")

    left, right = pair
    if not isinstance(left, str) or not isinstance(right, str):
        raise ValueError(f"{label} entries must be strings")

    clean_left = left.strip()
    clean_right = right.strip()
    if not clean_left or not clean_right:
        raise ValueError(f"{label} entries must be non-empty strings")
    if clean_left == clean_right:
        return clean_left, clean_right
    return (clean_left, clean_right) if clean_left < clean_right else (clean_right, clean_left)


def _constraint_pairs(
    constraints: Dict[str, Any],
    *,
    key: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    raw_pairs = constraints.get(key) or []
    if not isinstance(raw_pairs, list):
        return [], [f"constraints.{key} must be a list of role name pairs"]

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    violations: list[str] = []
    for pair in raw_pairs:
        try:
            normalized = _normalize_role_pair(pair, label=f"constraints.{key}")
        except ValueError as err:
            violations.append(str(err))
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        pairs.append(normalized)
    return pairs, violations


def _constraint_roles(
    constraints: Dict[str, Any],
    *,
    key: str,
) -> tuple[list[str], list[str]]:
    raw_roles = constraints.get(key) or []
    if not isinstance(raw_roles, list):
        return [], [f"constraints.{key} must be a list of role names"]

    roles: list[str] = []
    seen: set[str] = set()
    violations: list[str] = []
    for item in raw_roles:
        if not isinstance(item, str):
            violations.append(f"constraints.{key} entries must be strings")
            continue
        cleaned = item.strip()
        if not cleaned:
            violations.append(f"constraints.{key} entries must be non-empty strings")
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        roles.append(cleaned)
    return roles, violations


def evaluate_doctor(cfg: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, str]]:
    mode = str(cfg.get("mode") or "ensemble").strip().lower()
    role_names = _collect_role_names(cfg)
    scope = resolve_scope_text(cfg)
    role_models = {r: resolve_role_model(cfg, r) for r in role_names}
    role_identities = {r: resolve_role_identity(cfg, r) for r in role_names}
    role_providers = {r: resolve_role_provider(cfg, r) for r in role_names}
    constraints = cfg.get("constraints") or {}
    violations: List[str] = []

    if not scope:
        violations.append("No project scope supplied. Set input.scope in the config or pass --scope.")

    if not isinstance(constraints, dict):
        violations.append("constraints must be a mapping when provided")
        return False, violations, role_models

    required_roles, role_violations = _constraint_roles(constraints, key="require_roles")
    violations.extend(role_violations)

    require_json_roles, json_role_violations = _constraint_roles(constraints, key="require_json_for_roles")
    violations.extend(json_role_violations)

    for role in required_roles:
        if role not in role_models:
            violations.append(f"Missing required role '{role}' from constraints.require_roles")

    configured_json_roles = [role for role in require_json_roles if role in role_models]
    output_cfg = cfg.get("output") or {}
    if not isinstance(output_cfg, dict):
        output_cfg = {}
    if configured_json_roles and not bool(output_cfg.get("enforce_json", True)):
        details = ", ".join(configured_json_roles)
        violations.append(
            "output.enforce_json must be true when constraints.require_json_for_roles "
            f"includes configured roles ({details})",
        )

    minimum_specialist_roles = constraints.get("minimum_specialist_roles")
    if minimum_specialist_roles is not None:
        try:
            specialist_minimum = int(minimum_specialist_roles)
        except (TypeError, ValueError):
            violations.append("constraints.minimum_specialist_roles must be an integer")
        else:
            if specialist_minimum < 0:
                violations.append("constraints.minimum_specialist_roles must be >= 0")
                specialist_minimum = 0
            specialist_count = sum(
                1
                for role in role_names
                if role not in {"architect", "implementer"}
            )
            if specialist_count < specialist_minimum:
                violations.append(
                    "Configured specialist roles are below "
                    f"constraints.minimum_specialist_roles={specialist_minimum} "
                    f"(found {specialist_count})",
                )

    if mode == "ensemble":
        model_pairs, pair_violations = _constraint_pairs(constraints, key="disallow_same_model_pairs")
        provider_pairs, provider_pair_violations = _constraint_pairs(constraints, key="disallow_same_provider_pairs")
        violations.extend(pair_violations)
        violations.extend(provider_pair_violations)

        merged_pairs: list[tuple[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for pair in [*_baseline_pairs(), *model_pairs]:
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            merged_pairs.append(pair)

        for a, b in merged_pairs:
            if a not in role_identities or b not in role_identities:
                continue
            if role_identities.get(a) == role_identities.get(b):
                violations.append(f"{a} and {b} share model {role_models[a]}")

        for a, b in provider_pairs:
            if a not in role_providers or b not in role_providers:
                continue
            if role_providers.get(a) == role_providers.get(b):
                violations.append(f"{a} and {b} share provider {role_providers[a]}")

        minimum_distinct_models = constraints.get("minimum_distinct_models")
        if minimum_distinct_models is not None:
            try:
                distinct_minimum = int(minimum_distinct_models)
            except (TypeError, ValueError):
                violations.append("constraints.minimum_distinct_models must be an integer")
            else:
                if distinct_minimum <= 0:
                    violations.append("constraints.minimum_distinct_models must be > 0")
                    distinct_minimum = 0
                distinct_models = len({identity for identity in role_identities.values() if identity})
                if distinct_minimum and distinct_models < distinct_minimum:
                    violations.append(
                        "Ensemble mode requires at least "
                        f"{distinct_minimum} distinct role models, found {distinct_models}",
                    )

    policy_findings = _evaluate_policy_checks(
        PolicyCheckContext(
            cfg=cfg,
            mode=mode,
            scope=scope,
            role_names=tuple(role_names),
            role_models=role_models,
            role_identities=role_identities,
            role_providers=role_providers,
        )
    )
    policy_errors = [
        render_policy_message(finding)
        for finding in policy_findings
        if finding.severity == POLICY_ERROR
    ]
    if policy_errors:
        violations.extend(policy_errors)

    if violations:
        return False, violations, role_models

    policy_warnings = [
        render_policy_message(finding)
        for finding in policy_findings
        if finding.severity != POLICY_ERROR
    ]
    if mode == "solo":
        return True, [
            "SOLO MODE: degraded independence; lower assurance and higher self-confirmation risk.",
            *policy_warnings,
        ], role_models

    return True, policy_warnings, role_models


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
        guidance.append(
            "Baseline ensemble independence requires distinct model assignments across "
            "core implementation and audit roles. Separate those role models before running.",
        )
    if any("share provider" in item for item in violations):
        guidance.append("Separate constrained roles onto different providers or relax constraints.disallow_same_provider_pairs.")
    if any("minimum_distinct_models" in item for item in violations):
        guidance.append("Assign more distinct models across configured ensemble roles to increase independence.")
    if any("output.enforce_json" in item for item in violations):
        guidance.append("Enable output.enforce_json when role contracts depend on deterministic JSON parsing.")

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

    role_names = _collect_role_names(cfg)
    policy_findings = _evaluate_policy_checks(
        PolicyCheckContext(
            cfg=cfg,
            mode=str(cfg.get("mode") or "ensemble").strip().lower(),
            scope=resolve_scope_text(cfg),
            role_names=tuple(role_names),
            role_models={r: resolve_role_model(cfg, r) for r in role_names},
            role_identities={r: resolve_role_identity(cfg, r) for r in role_names},
            role_providers={r: resolve_role_provider(cfg, r) for r in role_names},
        )
    )
    violation_set = set(violations)
    for finding in policy_findings:
        if not finding.hint:
            continue
        if render_policy_message(finding) not in violation_set:
            continue
        if finding.hint not in guidance:
            guidance.append(finding.hint)

    if not guidance:
        guidance.append("Configuration is structurally valid. Next check provider credentials and artifacts_dir conventions.")

    return guidance


def run_doctor(config_path: str) -> Tuple[bool, List[str], Dict[str, str]]:
    try:
        cfg = load_config(config_path)
    except ConfigValidationError as err:
        return False, [str(err)], {}

    return evaluate_doctor(cfg)


def evaluate_doctor_environment() -> tuple[bool, list[str], dict[str, Any]]:
    packs, pack_failures = discover_config_packs()
    policies, policy_failures = discover_policy_checks()
    exporters, exporter_failures = discover_external_report_exporters()
    views, view_failures = discover_artifact_views()
    integrations, integration_failures = discover_integrations()

    report = {
        "config_packs": {
            "installed": [pack.key for pack in packs],
            "failures": [
                {"entry_point": failure.entry_point, "error": failure.error}
                for failure in pack_failures
            ],
        },
        "policy_checks": {
            "installed": [check.key for check in policies],
            "failures": [
                {"entry_point": failure.entry_point, "error": failure.error}
                for failure in policy_failures
            ],
        },
        "report_exporters": {
            "installed": [exporter.key for exporter in exporters],
            "failures": [
                {"entry_point": failure.entry_point, "error": failure.error}
                for failure in exporter_failures
            ],
        },
        "artifact_views": {
            "installed": [view.key for view in views],
            "failures": [
                {"entry_point": failure.entry_point, "error": failure.error}
                for failure in view_failures
            ],
        },
        "integrations": {
            "installed": [integration.key for integration in integrations],
            "failures": [
                {"entry_point": failure.entry_point, "error": failure.error}
                for failure in integration_failures
            ],
        },
    }

    violations: list[str] = []
    for surface_key, label in (
        ("config_packs", "config pack"),
        ("policy_checks", "policy check"),
        ("report_exporters", "report exporter"),
        ("artifact_views", "artifact view"),
        ("integrations", "integration"),
    ):
        surface = cast(dict[str, Any], report[surface_key])
        failures = cast(list[dict[str, str]], surface["failures"])
        for failure in failures:
            violations.append(
                f"[environment:{surface_key}] Failed to load {label} "
                f"'{failure['entry_point']}': {failure['error']}",
            )

    return not violations, violations, report


def render_doctor_environment_text(report: dict[str, Any]) -> str:
    lines = ["Environment Doctor:"]
    for surface_key, title in (
        ("config_packs", "Config Packs"),
        ("policy_checks", "Policy Checks"),
        ("report_exporters", "Report Exporters"),
        ("artifact_views", "Artifact Views"),
        ("integrations", "Integrations"),
    ):
        surface = report.get(surface_key) or {}
        installed = surface.get("installed") or []
        failures = surface.get("failures") or []
        lines.append(
            f"- {title}: {len(installed)} installed, {len(failures)} broken",
        )
        if installed:
            lines.append(f"  installed: {', '.join(installed)}")
        for failure in failures:
            lines.append(
                f"  broken: {failure.get('entry_point')}: {failure.get('error')}",
            )
    return "\n".join(lines)


def _baseline_pairs() -> list[tuple[str, str]]:
    return [
        _normalize_role_pair(pair, label="BASELINE_DISALLOW_SAME_MODEL_PAIRS")
        for pair in BASELINE_DISALLOW_SAME_MODEL_PAIRS
    ]
