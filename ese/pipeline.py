"""ESE pipeline runner.

This module runs the ensemble pipeline and writes artifacts for downstream consumption.

It is intentionally lightweight and model-agnostic. Users can plug in model providers
via configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import textwrap
from typing import Any, Dict


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run_pipeline(cfg: Dict[str, Any], artifacts_dir: str = "artifacts") -> str:
    """Run the ESE pipeline and write summary artifacts.

    This is the place where role-specific execution hooks will live. The initial
    production-ready version keeps it minimal:
      - makes artifacts dir
      - writes a summary with mode + role model mapping

    Returns the path to the summary file.
    """

    os.makedirs(artifacts_dir, exist_ok=True)

    # Write role model mapping into an artifact
    roles = cfg.get("roles", {}) or {}
    provider = (cfg.get("provider") or {}).get("name", "unknown")

    summary_lines = [
        "# ESE Summary",
        "",
        f"Mode: {cfg.get('mode', 'ensemble')}",
        f"Provider: {provider}",
        "",
        "This is the ESE pipeline placeholder output. Role execution hooks will evolve",
        "as model adapters are plugged in. The purpose of the initial version is to create",
        "a reproducible artifact pipeline and enforce constraints like role separation.",
    ]

    summary_path = os.path.join(artifacts_dir, "ese_summary.md")
    _write(summary_path, "\n".join(summary_lines) + "\n")

    # Keep a JSON artifact for machines
    state_path = os.path.join(artifacts_dir, "pipeline_state.json")
    _write(state_path, json.dumps({
        "mode": cfg.get("mode", "ensemble"),
        "provider": provider,
        "roles": roles,
    }, indent=2))

    return summary_path
