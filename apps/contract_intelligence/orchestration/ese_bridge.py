from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.contract_intelligence.ingestion.document_classifier import missing_required_documents
from apps.contract_intelligence.ingestion.project_loader import iter_project_documents
from apps.contract_intelligence.orchestration.pipeline import bid_review_pipeline
from apps.contract_intelligence.orchestration.role_catalog import BID_REVIEW_ROLE_CATALOG
from ese.config import validate_config, write_config
from ese.pipeline import run_pipeline
from ese.provider_runtime import builtin_runtime_adapter, default_api_key_env
from ese.templates import DEMO_EXECUTION_MODE, resolve_execution_mode, provider_runtime_summary


DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-5-mini",
    "local": "qwen2.5-coder:14b",
    "custom_api": "custom-model",
    "anthropic": "claude-sonnet",
    "google": "gemini-flash",
    "xai": "grok",
    "openrouter": "openrouter-model",
    "huggingface": "hf-model",
}

ROLE_PROMPT_GUIDANCE = {
    "document_intake_analyst": (
        "You are the document_intake_analyst for a contractor-side construction bid review. "
        "Classify the supplied package, identify missing expected documents, and summarize the package quality. "
        "Use findings for missing inputs, ambiguity, and intake risk. Use next_steps for concrete follow-ups. "
        "Use artifacts to name the domain deliverables this role contributes to, such as document_inventory.json. "
        "Use findings for contract issues, not software defects."
    ),
    "contract_risk_analyst": (
        "You are the contract_risk_analyst for a contractor-side construction bid review. "
        "Focus on payment, indemnity, delay, claims, change-order, termination, flow-down, and liability risk. "
        "Cite source file and clause location inside finding.details whenever possible. "
        "Use findings for material contract issues, next_steps for negotiation actions, and artifacts for risk_findings.json."
    ),
    "insurance_requirements_analyst": (
        "You are the insurance_requirements_analyst for a contractor-side construction bid review. "
        "Focus on additional insured wording, waiver of subrogation, primary and noncontributory language, "
        "completed operations, unusual limits, and endorsements. "
        "Cite source file and clause location inside finding.details whenever possible. "
        "Use findings for insurance anomalies and artifacts for insurance_findings.json."
    ),
    "funding_compliance_analyst": (
        "You are the funding_compliance_analyst for a contractor-side construction bid review. "
        "Focus on public-funding overlays such as Davis-Bacon, certified payroll, DBE, domestic sourcing, "
        "and procurement conditions that materially affect execution. "
        "Use findings for compliance issues and artifacts for compliance_findings.json."
    ),
    "relationship_strategy_analyst": (
        "You are the relationship_strategy_analyst for a contractor-side construction bid review. "
        "Assess owner posture, negotiation sensitivity, politically rigid issues, and leverage points. "
        "Use findings only for material relationship risks, and use next_steps for negotiation posture advice."
    ),
    "adversarial_reviewer": (
        "You are the adversarial_reviewer for a contractor-side construction bid review. "
        "Challenge optimistic assumptions, hunt for missed hazards, and surface contradictions across analysts. "
        "Use findings for missed risks and contradictions, and use next_steps to require human review when needed."
    ),
    "bid_decision_analyst": (
        "You are the bid_decision_analyst for a contractor-side construction bid review. "
        "Produce an executive recommendation of go, go-with-conditions, or no-go. "
        "Make the recommendation explicit in summary and next_steps, and use findings for the reasons that justify it. "
        "Use artifacts to name decision_summary.json."
    ),
    "obligation_register_builder": (
        "You are the obligation_register_builder for a contractor-side construction bid review. "
        "Identify notice deadlines, reporting duties, pre-start requirements, and other trackable obligations. "
        "Use findings for obligations that could be missed operationally and use artifacts to name obligations_register.json."
    ),
}


def _ordered_role_keys() -> list[str]:
    ordered: list[str] = []
    for stage in bid_review_pipeline():
        ordered.extend(stage.roles)
    return ordered


def _default_model_for(provider: str) -> str:
    return DEFAULT_MODEL_BY_PROVIDER.get(provider, "model")


def _prompt_for_role(role_key: str, output_artifact: str) -> str:
    guidance = ROLE_PROMPT_GUIDANCE[role_key]
    return (
        f"{guidance} "
        f"Contribute to {output_artifact}. "
        "Return the standard ESE JSON report format only."
    )


def _render_project_context(project_dir: Path, *, max_clauses: int = 18, max_clause_chars: int = 400) -> str:
    documents = iter_project_documents(project_dir)
    missing_docs = missing_required_documents([document.document_type for document in documents])

    lines = [
        "Document Inventory:",
    ]
    for document in documents:
        lines.append(
            "- "
            f"{document.relative_path} "
            f"(type={document.document_type.value}, text_source={document.text_source}, clauses={len(document.clauses)})"
        )
    if missing_docs:
        lines.append("Missing required documents: " + ", ".join(item.value for item in missing_docs))

    lines.extend(["", "Clause Excerpts:"])
    emitted = 0
    for document in documents:
        for clause in document.clauses[:3]:
            excerpt = " ".join(clause.text.split())
            if len(excerpt) > max_clause_chars:
                excerpt = excerpt[: max_clause_chars - 3] + "..."
            lines.append(f"- {clause.location} [{document.document_type.value}] {excerpt}")
            emitted += 1
            if emitted >= max_clauses:
                lines.append("- Additional clauses omitted for brevity.")
                return "\n".join(lines)
    return "\n".join(lines)


def build_bid_review_ese_config(
    *,
    project_dir: str | Path,
    provider: str = "local",
    execution_mode: str = DEMO_EXECUTION_MODE,
    artifacts_dir: str = "artifacts/contract_intelligence_ese",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
    fail_on_high: bool = False,
) -> dict[str, Any]:
    project_path = Path(project_dir).expanduser().resolve()
    clean_provider = (provider or "local").strip().lower()
    effective_mode = resolve_execution_mode(
        provider=clean_provider,
        requested_mode=execution_mode,
        runtime_adapter=runtime_adapter,
        base_url=base_url,
    )
    selected_model = (model or "").strip() or _default_model_for(clean_provider)

    role_definitions = {role.key: role for role in BID_REVIEW_ROLE_CATALOG}
    roles_cfg: dict[str, Any] = {}
    for role_key in _ordered_role_keys():
        role_def = role_definitions[role_key]
        roles_cfg[role_key] = {
            "temperature": 0.2,
            "prompt": _prompt_for_role(role_key, role_def.output_artifact),
        }

    provider_cfg: dict[str, Any] = {
        "name": (provider_name or clean_provider).strip() if clean_provider == "custom_api" else clean_provider,
        "model": selected_model,
    }
    if clean_provider == "custom_api" and base_url:
        provider_cfg["base_url"] = base_url.strip()

    cfg: dict[str, Any] = {
        "version": 1,
        "mode": "ensemble",
        "template_key": "contract-bid-review",
        "template_title": "Contract Bid Review",
        "role_order": _ordered_role_keys(),
        "provider": provider_cfg,
        "roles": roles_cfg,
        "constraints": {
            "disallow_same_model_pairs": [],
        },
        "input": {
            "scope": (
                "Evaluate this construction contract package from the contractor perspective and produce "
                "intake, risk, insurance, compliance, challenge, decision, and obligation outputs."
            ),
            "prompt": _render_project_context(project_path),
            "project_dir": str(project_path),
        },
        "output": {
            "artifacts_dir": artifacts_dir,
            "enforce_json": True,
        },
        "gating": {
            "fail_on_high": fail_on_high,
        },
        "runtime": {
            "timeout_seconds": 60,
            "max_retries": 2,
            "retry_backoff_seconds": 1.0,
        },
    }

    if effective_mode == DEMO_EXECUTION_MODE:
        cfg["runtime"]["adapter"] = "dry-run"
    elif runtime_adapter:
        cfg["runtime"]["adapter"] = runtime_adapter.strip()
    else:
        builtin_adapter = builtin_runtime_adapter(clean_provider)
        if builtin_adapter:
            cfg["runtime"]["adapter"] = builtin_adapter
        else:
            raise ValueError(
                f"Live execution for provider '{clean_provider}' requires runtime_adapter in module:function format.",
            )

    if cfg["runtime"]["adapter"] in {"openai", "custom_api"} or clean_provider == "custom_api":
        cfg["provider"]["api_key_env"] = (api_key_env or default_api_key_env(clean_provider)).strip()

    if cfg["runtime"]["adapter"] == "openai":
        cfg["runtime"]["openai"] = {"base_url": "https://api.openai.com/v1"}

    if cfg["runtime"]["adapter"] == "local":
        cfg["runtime"]["local"] = {
            "base_url": (base_url or "http://localhost:11434/v1").strip(),
            "use_openai_compat_auth": True,
        }

    if cfg["runtime"]["adapter"] == "custom_api":
        if not base_url:
            raise ValueError("custom_api live runs require base_url.")
        cfg["runtime"]["custom_api"] = {"base_url": base_url.strip()}
        cfg["provider"]["base_url"] = base_url.strip()

    cfg["provider_runtime"] = provider_runtime_summary(
        clean_provider,
        execution_mode=effective_mode,
        runtime_adapter=str(cfg["runtime"].get("adapter") or ""),
    )
    cfg["provider_runtime"]["domain_pack"] = "construction_contract_intelligence"
    cfg["provider_runtime"]["ese_bridge"] = True

    return validate_config(cfg, source="<contract-bid-review>")


def run_bid_review_with_ese(
    *,
    project_dir: str | Path,
    provider: str = "local",
    execution_mode: str = DEMO_EXECUTION_MODE,
    artifacts_dir: str = "artifacts/contract_intelligence_ese",
    model: str | None = None,
    api_key_env: str | None = None,
    runtime_adapter: str | None = None,
    provider_name: str | None = None,
    base_url: str | None = None,
    fail_on_high: bool = False,
    config_path: str | None = None,
) -> tuple[dict[str, Any], str]:
    cfg = build_bid_review_ese_config(
        project_dir=project_dir,
        provider=provider,
        execution_mode=execution_mode,
        artifacts_dir=artifacts_dir,
        model=model,
        api_key_env=api_key_env,
        runtime_adapter=runtime_adapter,
        provider_name=provider_name,
        base_url=base_url,
        fail_on_high=fail_on_high,
    )
    if config_path:
        write_config(config_path, cfg)
    summary_path = run_pipeline(cfg=cfg, artifacts_dir=artifacts_dir)
    return cfg, summary_path
