"""ESE pipeline runner with pluggable role adapters and artifact chaining."""

from __future__ import annotations

import importlib
import json
import os
import textwrap
from typing import Any, Callable, Dict, Mapping, Protocol

import yaml

from ese.adapters import AdapterExecutionError, BUILTIN_ADAPTERS
from ese.config import resolve_role_model, resolve_scope_text

PIPELINE_ORDER = [
    "architect",
    "implementer",
    "adversarial_reviewer",
    "security_auditor",
    "test_generator",
    "performance_analyst",
    "documentation_writer",
    "devops_sre",
    "database_engineer",
    "release_manager",
]

JSON_REPORT_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
CONFIG_SNAPSHOT_NAME = "ese_config.snapshot.yaml"
SPECIALIST_ROLE_INSTRUCTIONS = {
    "adversarial_reviewer": (
        "Act as an adversarial code reviewer. Hunt for correctness bugs, edge cases, "
        "regressions, unsafe assumptions, and missing validation."
    ),
    "security_auditor": (
        "Perform a security review. Focus on trust boundaries, authz/authn gaps, secrets "
        "handling, injection risks, data exposure, and abuse paths."
    ),
    "test_generator": (
        "Design a pragmatic automated test plan. Focus on missing unit, integration, and "
        "end-to-end coverage, including the highest-risk failure modes."
    ),
    "performance_analyst": (
        "Review performance and scalability. Focus on hot paths, latency risks, query or "
        "algorithmic complexity, memory pressure, and caching opportunities."
    ),
    "documentation_writer": (
        "Produce documentation deliverables. Focus on README updates, API usage notes, "
        "migration guidance, operator runbooks, and any documentation gaps that block adoption."
    ),
    "devops_sre": (
        "Review operational readiness. Focus on CI/CD safety, deployment sequencing, rollback "
        "plans, observability, alerting, and day-2 operability."
    ),
    "database_engineer": (
        "Review data-layer design. Focus on schema correctness, migrations, indexes, query "
        "plans, transaction safety, consistency, and rollback strategy."
    ),
    "release_manager": (
        "Assess release readiness. Focus on blockers, rollout sequencing, rollback readiness, "
        "dependency coordination, and launch sign-off criteria."
    ),
}


class PipelineError(RuntimeError):
    """Raised when pipeline configuration or adapter execution fails."""


class RoleAdapter(Protocol):
    """Callable signature used by external role adapters."""

    def __call__(
        self,
        *,
        role: str,
        model: str,
        prompt: str,
        context: Mapping[str, str],
        cfg: Mapping[str, Any],
    ) -> str:
        ...


def _write(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _write_yaml(path: str, payload: Mapping[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(dict(payload), f, sort_keys=False)


def _normalize_role_order(cfg: Dict[str, Any]) -> list[str]:
    roles_cfg = cfg.get("roles") or {}
    configured_roles = list(roles_cfg.keys()) if isinstance(roles_cfg, dict) else []
    if not configured_roles:
        return []

    ordered: list[str] = [role for role in PIPELINE_ORDER if role in configured_roles]
    ordered.extend(role for role in configured_roles if role not in ordered)
    return ordered


def _require_scope(cfg: Dict[str, Any]) -> str:
    scope = resolve_scope_text(cfg)
    if scope:
        return scope
    raise PipelineError("No project scope supplied. Set input.scope in the config or pass --scope.")


def _output_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    output = cfg.get("output")
    if not isinstance(output, dict):
        return {"artifacts_dir": "artifacts", "enforce_json": True}

    return {
        "artifacts_dir": output.get("artifacts_dir") or "artifacts",
        "enforce_json": bool(output.get("enforce_json", True)),
    }


def _gating_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    gating = cfg.get("gating")
    if not isinstance(gating, dict):
        return {"fail_on_high": True}
    return {"fail_on_high": bool(gating.get("fail_on_high", True))}


def _resolve_artifacts_dir(cfg: Dict[str, Any], artifacts_dir: str | None) -> str:
    if isinstance(artifacts_dir, str) and artifacts_dir.strip():
        return artifacts_dir.strip()
    configured = _output_cfg(cfg).get("artifacts_dir")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return "artifacts"


def _config_snapshot_path(artifacts_dir: str) -> str:
    return os.path.join(artifacts_dir, CONFIG_SNAPSHOT_NAME)


def _json_report_contract() -> str:
    return textwrap.dedent(
        """
        Return valid JSON only, with no Markdown fences or prose outside the JSON object.
        Use this schema exactly:
        {
          "summary": "string",
          "findings": [
            {
              "severity": "LOW | MEDIUM | HIGH | CRITICAL",
              "title": "string",
              "details": "string"
            }
          ],
          "artifacts": ["string"],
          "next_steps": ["string"]
        }
        Use empty arrays when there are no findings, artifacts, or next steps.
        """,
    ).strip()


def _role_prompt(
    role: str,
    scope: str,
    outputs: Mapping[str, str],
    *,
    enforce_json: bool,
) -> str:
    architect_output = outputs.get("architect", "").strip()
    implementer_output = outputs.get("implementer", "").strip()
    json_contract = f"\n\n{_json_report_contract()}" if enforce_json else ""
    artifact_guidance = ""
    if enforce_json:
        artifact_guidance = (
            "\n\nUse `findings` only for actionable issues or gaps. "
            "Use `artifacts` for concrete deliverables such as docs, runbooks, test files, "
            "rollout checklists, or migration notes."
        )

    if role == "architect":
        return textwrap.dedent(
            f"""
            You are the Architect.
            Produce a concise implementation plan for this scope:

            {scope}

            {json_contract}
            """,
        ).strip()

    if role == "implementer":
        return textwrap.dedent(
            f"""
            You are the Implementer.
            Build from the Architect plan and scope.

            Scope:
            {scope}

            Architect Plan:
            {architect_output or "(none provided)"}

            {json_contract}
            """,
        ).strip()

    if role in SPECIALIST_ROLE_INSTRUCTIONS:
        return textwrap.dedent(
            f"""
            You are the {role}.
            {SPECIALIST_ROLE_INSTRUCTIONS[role]}

            Scope:
            {scope}

            Architect Plan:
            {architect_output or "(none provided)"}

            Implementer Output:
            {implementer_output or "(none provided)"}

            {artifact_guidance}

            {json_contract}
            """,
        ).strip()

    return textwrap.dedent(
        f"""
        You are the {role}.
        Review the implementation against scope and report findings.

        Scope:
        {scope}

        Implementer Output:
        {implementer_output or "(none provided)"}

        {json_contract}
        """,
    ).strip()


def _role_context(role: str, outputs: Mapping[str, str]) -> Dict[str, str]:
    if role == "architect":
        return {}
    if role == "implementer":
        return {"architect": outputs.get("architect", "")}
    return {
        "implementer": outputs.get("implementer", ""),
        "architect": outputs.get("architect", ""),
    }


def _load_custom_adapter(reference: str) -> RoleAdapter:
    if ":" not in reference:
        raise PipelineError(
            "runtime.adapter must be one of {'dry-run', 'openai', 'custom_api'} or a Python reference in 'module:function' format",
        )

    module_name, object_name = reference.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as err:
        raise PipelineError(f"Could not import adapter module '{module_name}'") from err

    adapter = getattr(module, object_name, None)
    if adapter is None or not callable(adapter):
        raise PipelineError(f"Adapter '{reference}' is not callable")

    return adapter


def _resolve_adapter(cfg: Dict[str, Any]) -> tuple[str, RoleAdapter]:
    runtime_cfg = cfg.get("runtime") or {}
    reference = (runtime_cfg.get("adapter") or "dry-run").strip()
    if not reference:
        reference = "dry-run"

    builtin = BUILTIN_ADAPTERS.get(reference)
    if builtin is not None:
        return reference, builtin

    return reference, _load_custom_adapter(reference)


def _invoke_adapter(
    adapter: Callable[..., str] | RoleAdapter,
    *,
    role: str,
    model: str,
    prompt: str,
    context: Mapping[str, str],
    cfg: Mapping[str, Any],
) -> str:
    try:
        result = adapter(role=role, model=model, prompt=prompt, context=context, cfg=cfg)
    except AdapterExecutionError as err:
        raise PipelineError(f"Adapter execution failed for role '{role}': {err}") from err
    except Exception as err:  # noqa: BLE001 - preserve adapter stack info in message.
        raise PipelineError(f"Adapter execution failed for role '{role}': {err}") from err

    if not isinstance(result, str):
        raise PipelineError(f"Adapter output for role '{role}' must be a string")
    return result


def _normalize_json_report(*, role: str, model: str, output: str) -> dict[str, Any]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as err:
        raise PipelineError(
            f"Adapter output for role '{role}' must be valid JSON when output.enforce_json=true",
        ) from err

    if not isinstance(parsed, dict):
        raise PipelineError(
            f"Adapter output for role '{role}' must be a JSON object when output.enforce_json=true",
        )

    report = dict(parsed)

    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise PipelineError(
            f"JSON report for role '{role}' must contain a non-empty string field 'summary'",
        )
    report["summary"] = summary.strip()

    findings = report.get("findings")
    if not isinstance(findings, list):
        raise PipelineError(f"JSON report for role '{role}' must contain a list field 'findings'")

    normalized_findings: list[dict[str, str]] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            raise PipelineError(
                f"JSON report for role '{role}' has non-object finding at index {index}",
            )

        title = finding.get("title")
        severity = finding.get("severity")
        details = finding.get("details", "")
        if not isinstance(title, str) or not title.strip():
            raise PipelineError(
                f"JSON report for role '{role}' has finding {index} without a non-empty 'title'",
            )
        if not isinstance(severity, str):
            raise PipelineError(
                f"JSON report for role '{role}' has finding {index} without string 'severity'",
            )
        normalized_severity = severity.strip().upper()
        if normalized_severity not in JSON_REPORT_SEVERITIES:
            allowed = ", ".join(sorted(JSON_REPORT_SEVERITIES))
            raise PipelineError(
                f"JSON report for role '{role}' has invalid severity '{severity}' "
                f"at finding {index}; expected one of {allowed}",
            )
        if not isinstance(details, str):
            raise PipelineError(
                f"JSON report for role '{role}' has finding {index} with non-string 'details'",
            )
        normalized_findings.append(
            {
                **finding,
                "title": title.strip(),
                "severity": normalized_severity,
                "details": details.strip(),
            },
        )
    report["findings"] = normalized_findings

    for key in ("artifacts", "next_steps"):
        raw_value = report.get(key, [])
        if not isinstance(raw_value, list) or any(not isinstance(item, str) for item in raw_value):
            raise PipelineError(
                f"JSON report for role '{role}' must contain a string list field '{key}'",
            )
        report[key] = [item.strip() for item in raw_value if item.strip()]

    report["role"] = role
    report["model"] = model
    return report


def _render_role_output(
    *,
    role: str,
    model: str,
    output: str,
    enforce_json: bool,
) -> tuple[str, str, dict[str, Any] | None]:
    if not enforce_json:
        return "md", output, None

    report = _normalize_json_report(role=role, model=model, output=output)
    rendered = json.dumps(report, indent=2) + "\n"
    return "json", rendered, report


def _high_severity_findings(report: Mapping[str, Any]) -> list[dict[str, str]]:
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    return [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("severity") in {"HIGH", "CRITICAL"}
    ]


def _write_summary_and_state(
    *,
    artifacts_dir: str,
    mode: str,
    provider: str,
    adapter_name: str,
    scope: str,
    role_models: Mapping[str, str],
    role_artifacts: Mapping[str, str],
    execution: list[dict[str, str]],
    status: str,
    config_snapshot: str,
    failure: str | None = None,
) -> str:
    summary_lines = [
        "# ESE Summary",
        "",
        f"Status: {status}",
        f"Mode: {mode}",
        f"Provider: {provider}",
        f"Adapter: {adapter_name}",
        "",
        "Executed roles:",
    ]
    summary_lines.extend(f"- {item['role']} ({item['model']}) -> {item['artifact']}" for item in execution)
    if failure:
        summary_lines.extend(["", f"Failure: {failure}"])

    summary_path = os.path.join(artifacts_dir, "ese_summary.md")
    _write(summary_path, "\n".join(summary_lines) + "\n")

    state: dict[str, Any] = {
        "status": status,
        "mode": mode,
        "provider": provider,
        "adapter": adapter_name,
        "scope": scope,
        "config_snapshot": config_snapshot,
        "role_models": dict(role_models),
        "artifacts": dict(role_artifacts),
        "execution": execution,
    }
    if failure:
        state["failure"] = failure

    state_path = os.path.join(artifacts_dir, "pipeline_state.json")
    _write(state_path, json.dumps(state, indent=2))
    return summary_path


def _load_resume_state(
    *,
    cfg: Dict[str, Any],
    artifacts_dir: str,
    role_order: list[str],
    start_role: str | None,
) -> tuple[int, dict[str, str], dict[str, str], dict[str, str], list[dict[str, str]]]:
    if not start_role:
        return 0, {}, {}, {}, []

    clean_role = start_role.strip()
    if clean_role not in role_order:
        available = ", ".join(role_order)
        raise PipelineError(f"Unknown start role '{start_role}'. Choose one of: {available}")

    start_index = role_order.index(clean_role)
    if start_index == 0:
        return 0, {}, {}, {}, []

    state_path = os.path.join(artifacts_dir, "pipeline_state.json")
    if not os.path.exists(state_path):
        raise PipelineError(
            f"Cannot rerun from role '{clean_role}' without existing pipeline_state.json in {artifacts_dir}",
        )

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError as err:
        raise PipelineError(f"Could not parse existing pipeline state at {state_path}") from err

    if not isinstance(state, dict):
        raise PipelineError(f"Existing pipeline state at {state_path} must be a JSON object")

    state_artifacts = state.get("artifacts")
    state_models = state.get("role_models")
    if not isinstance(state_artifacts, Mapping):
        raise PipelineError(f"Existing pipeline state at {state_path} is missing an artifacts map")

    seeded_outputs: dict[str, str] = {}
    seeded_artifacts: dict[str, str] = {}
    seeded_models: dict[str, str] = {}
    seeded_execution: list[dict[str, str]] = []

    for role in role_order[:start_index]:
        artifact_ref = state_artifacts.get(role)
        if not isinstance(artifact_ref, str) or not artifact_ref.strip():
            raise PipelineError(
                f"Cannot rerun from role '{clean_role}' because prior role '{role}' has no saved artifact",
            )

        artifact_path = artifact_ref
        if not os.path.isabs(artifact_path):
            artifact_path = os.path.join(artifacts_dir, artifact_path)
        if not os.path.exists(artifact_path):
            raise PipelineError(
                f"Cannot rerun from role '{clean_role}' because prior artifact is missing: {artifact_path}",
            )

        with open(artifact_path, "r", encoding="utf-8") as f:
            seeded_outputs[role] = f.read()

        seeded_artifacts[role] = artifact_path
        model_ref = resolve_role_model(cfg, role)
        if isinstance(state_models, Mapping) and isinstance(state_models.get(role), str):
            model_ref = str(state_models[role])
        seeded_models[role] = model_ref
        seeded_execution.append(
            {
                "role": role,
                "model": model_ref,
                "artifact": artifact_path,
            },
        )

    return start_index, seeded_outputs, seeded_artifacts, seeded_models, seeded_execution


def run_pipeline(
    cfg: Dict[str, Any],
    artifacts_dir: str | None = None,
    *,
    start_role: str | None = None,
) -> str:
    """Run the ESE pipeline and write per-role artifacts plus summary outputs."""
    artifacts_dir = _resolve_artifacts_dir(cfg, artifacts_dir)

    provider = (cfg.get("provider") or {}).get("name", "unknown")
    mode = cfg.get("mode", "ensemble")
    scope = _require_scope(cfg)
    role_order = _normalize_role_order(cfg)
    if not role_order:
        raise PipelineError("No roles configured. Add at least one role under roles.")

    os.makedirs(artifacts_dir, exist_ok=True)
    config_snapshot = _config_snapshot_path(artifacts_dir)
    _write_yaml(config_snapshot, cfg)
    adapter_name, adapter = _resolve_adapter(cfg)
    output_cfg = _output_cfg(cfg)
    gating_cfg = _gating_cfg(cfg)
    enforce_json = output_cfg["enforce_json"]
    fail_on_high = gating_cfg["fail_on_high"]

    start_index, role_outputs, role_artifacts, role_models, execution = _load_resume_state(
        cfg=cfg,
        artifacts_dir=artifacts_dir,
        role_order=role_order,
        start_role=start_role,
    )

    for index, role in enumerate(role_order[start_index:], start=start_index + 1):
        model_ref = resolve_role_model(cfg, role)
        prompt = _role_prompt(role=role, scope=scope, outputs=role_outputs, enforce_json=enforce_json)
        context = _role_context(role=role, outputs=role_outputs)
        output = _invoke_adapter(
            adapter,
            role=role,
            model=model_ref,
            prompt=prompt,
            context=context,
            cfg=cfg,
        )

        artifact_extension, rendered_output, structured_report = _render_role_output(
            role=role,
            model=model_ref,
            output=output,
            enforce_json=enforce_json,
        )

        artifact_name = f"{index:02d}_{role}.{artifact_extension}"
        artifact_path = os.path.join(artifacts_dir, artifact_name)
        _write(artifact_path, rendered_output)

        role_outputs[role] = rendered_output
        role_artifacts[role] = artifact_path
        role_models[role] = model_ref
        execution.append(
            {
                "role": role,
                "model": model_ref,
                "artifact": artifact_path,
            },
        )

        if fail_on_high and structured_report is not None:
            high_findings = _high_severity_findings(structured_report)
            if high_findings:
                finding_titles = ", ".join(finding["title"] for finding in high_findings)
                failure = (
                    f"Pipeline gated by HIGH severity findings in role '{role}': {finding_titles}"
                )
                summary_path = _write_summary_and_state(
                    artifacts_dir=artifacts_dir,
                    mode=mode,
                    provider=provider,
                    adapter_name=adapter_name,
                    scope=scope,
                    role_models=role_models,
                    role_artifacts=role_artifacts,
                    execution=execution,
                    status="failed",
                    config_snapshot=config_snapshot,
                    failure=failure,
                )
                raise PipelineError(f"{failure}. Summary: {summary_path}")

    return _write_summary_and_state(
        artifacts_dir=artifacts_dir,
        mode=mode,
        provider=provider,
        adapter_name=adapter_name,
        scope=scope,
        role_models=role_models,
        role_artifacts=role_artifacts,
        execution=execution,
        status="completed",
        config_snapshot=config_snapshot,
    )
