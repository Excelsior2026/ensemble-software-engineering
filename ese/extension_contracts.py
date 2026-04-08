"""Shared helpers for validating ESE external extension contracts."""

from __future__ import annotations

import inspect
from typing import Any


def normalize_non_empty(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def title_from_key(key: str) -> str:
    return " ".join(part.capitalize() for part in key.replace("_", "-").split("-"))


def normalize_contract_version(
    value: Any,
    *,
    extension_name: str,
    expected_version: int,
) -> int:
    if value is None:
        return expected_version
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{extension_name} contract_version must be an integer")
    if value != expected_version:
        raise ValueError(
            f"{extension_name} contract_version {value} is not supported by this ESE build; "
            f"expected {expected_version}"
        )
    return value


def maybe_invoke_entrypoint_loader(value: Any) -> Any:
    """Invoke zero-argument loader callables while leaving runtime callables intact."""
    if not callable(value):
        return value
    try:
        signature = inspect.signature(value)
    except (TypeError, ValueError):
        return value

    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            return value
        if parameter.default is inspect.Parameter.empty:
            return value
    return value()
