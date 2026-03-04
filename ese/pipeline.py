"""ESE pipeline runner with pluggable role adapters and artifact chaining."""

from __future__ import annotations

import importlib
import json
import os
import textwrap
from typing import Any, Callable, Dict, Mapping, Protocol

from ese.adapters import AdapterExecutionError, BUILTIN_ADAPTERS
from ese.config import resolve_role_model

PIPELINE_ORDER = [
    "architect",
    "implementer",
    "adversarial_reviewer",
    "security_auditor",
    "test_generator",
    "performance_analyst",
]


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


def _normalize_role_order(cfg: Dict[str, Any]) -> list[str]:
    roles_cfg = cfg.get("roles") or {}
    configured_roles = list(roles_cfg.keys()) if isinstance(roles_cfg, dict) else []
    if not configured_roles:
        return PIPELINE_ORDER.copy()

    ordered: list[str] = [role for role in PIPELINE_ORDER if role in configured_roles]
    ordered.extend(role for role in configured_roles if role not in ordered)
    return ordered


def _build_scope(cfg: Dict[str, Any]) -> str:
    input_cfg = cfg.get("input") or {}
    candidates = [
        input_cfg.get("scope"),
        cfg.get("scope"),
        input_cfg.get("prompt"),
        cfg.get("prompt"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "No project scope supplied. Proceed with generic role behavior."


def _role_prompt(role: str, scope: str, outputs: Mapping[str, str]) -> str:
    architect_output = outputs.get("architect", "").strip()
    implementer_output = outputs.get("implementer", "").strip()

    if role == "architect":
        return textwrap.dedent(
            f"""
            You are the Architect.
            Produce a concise implementation plan for this scope:

            {scope}
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


def run_pipeline(cfg: Dict[str, Any], artifacts_dir: str = "artifacts") -> str:
    """Run the ESE pipeline and write per-role artifacts plus summary outputs."""
    os.makedirs(artifacts_dir, exist_ok=True)

    provider = (cfg.get("provider") or {}).get("name", "unknown")
    mode = cfg.get("mode", "ensemble")
    scope = _build_scope(cfg)
    role_order = _normalize_role_order(cfg)
    adapter_name, adapter = _resolve_adapter(cfg)

    role_outputs: dict[str, str] = {}
    role_artifacts: dict[str, str] = {}
    role_models: dict[str, str] = {}
    execution: list[dict[str, str]] = []

    for index, role in enumerate(role_order, start=1):
        model_ref = resolve_role_model(cfg, role)
        prompt = _role_prompt(role=role, scope=scope, outputs=role_outputs)
        context = _role_context(role=role, outputs=role_outputs)
        output = _invoke_adapter(
            adapter,
            role=role,
            model=model_ref,
            prompt=prompt,
            context=context,
            cfg=cfg,
        )

        artifact_name = f"{index:02d}_{role}.md"
        artifact_path = os.path.join(artifacts_dir, artifact_name)
        _write(artifact_path, output)

        role_outputs[role] = output
        role_artifacts[role] = artifact_path
        role_models[role] = model_ref
        execution.append(
            {
                "role": role,
                "model": model_ref,
                "artifact": artifact_path,
            },
        )

    summary_lines = [
        "# ESE Summary",
        "",
        f"Mode: {mode}",
        f"Provider: {provider}",
        f"Adapter: {adapter_name}",
        "",
        "Executed roles:",
    ]
    summary_lines.extend(f"- {item['role']} ({item['model']}) -> {item['artifact']}" for item in execution)

    summary_path = os.path.join(artifacts_dir, "ese_summary.md")
    _write(summary_path, "\n".join(summary_lines) + "\n")

    state_path = os.path.join(artifacts_dir, "pipeline_state.json")
    _write(
        state_path,
        json.dumps(
            {
                "mode": mode,
                "provider": provider,
                "adapter": adapter_name,
                "scope": scope,
                "role_models": role_models,
                "artifacts": role_artifacts,
                "execution": execution,
            },
            indent=2,
        ),
    )

    return summary_path
