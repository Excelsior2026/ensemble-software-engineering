"""Helpers for deriving and persisting run evidence states."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_STATES = ("draft", "ready", "approved", "blocked", "released")


def normalize_evidence_state(value: Any) -> str:
    clean_value = str(value or "").strip().lower()
    if clean_value not in EVIDENCE_STATES:
        choices = ", ".join(EVIDENCE_STATES)
        raise ValueError(f"evidence state must be one of: {choices}")
    return clean_value


def coerce_evidence_state(value: Any) -> str | None:
    clean_value = str(value or "").strip().lower()
    if not clean_value:
        return None
    return normalize_evidence_state(clean_value)


def derive_evidence_state(
    *,
    status: str,
    blocker_count: int,
    assurance_level: str,
) -> tuple[str, str]:
    clean_status = str(status or "unknown").strip().lower()
    clean_assurance = str(assurance_level or "standard").strip().lower()

    if clean_status == "failed":
        return "blocked", "Run failed, so evidence cannot advance."
    if blocker_count > 0:
        return "blocked", "High-severity blockers are still open."
    if clean_assurance == "degraded":
        return "draft", "Degraded assurance requires human review before evidence can advance."
    if clean_status == "completed":
        return "ready", "Run completed without blockers under standard assurance."
    return "draft", "Evidence is incomplete until the run completes."


def normalize_evidence_history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    history: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        state = coerce_evidence_state(entry.get("state"))
        if not state:
            continue
        item: dict[str, Any] = {"state": state}
        for key in ("previous_state", "actor", "note", "reason", "source", "updated_at"):
            raw_value = entry.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                item[key] = raw_value.strip()
        history.append(item)
    return history


def update_pipeline_evidence_state(
    artifacts_dir: str | Path,
    *,
    state: str,
    previous_state: str | None = None,
    actor: str | None = None,
    note: str | None = None,
    reason: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    clean_state = normalize_evidence_state(state)
    state_path = Path(artifacts_dir) / "pipeline_state.json"
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise ValueError(f"Run artifacts not found: {state_path}") from err
    except json.JSONDecodeError as err:
        raise ValueError(f"Run state is not valid JSON: {state_path}") from err

    if not isinstance(payload, dict):
        raise ValueError(f"Run state must be a JSON object: {state_path}")

    history = normalize_evidence_history(payload.get("evidence_state_history"))
    entry: dict[str, Any] = {
        "state": clean_state,
        "source": (source or "manual").strip() or "manual",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if previous_state:
        entry["previous_state"] = previous_state
    if actor and actor.strip():
        entry["actor"] = actor.strip()
    if note and note.strip():
        entry["note"] = note.strip()
    if reason and reason.strip():
        entry["reason"] = reason.strip()

    history.append(entry)
    payload["evidence_state"] = clean_state
    payload["evidence_state_history"] = history
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
