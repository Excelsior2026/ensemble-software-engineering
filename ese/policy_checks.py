"""Discovery and execution helpers for external ESE policy checks."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from ese.extension_contracts import (
    maybe_invoke_entrypoint_loader,
    normalize_contract_version,
    normalize_non_empty,
    title_from_key,
)

POLICY_CHECK_ENTRY_POINT_GROUP = "ese.policy_checks"
POLICY_CHECK_CONTRACT_VERSION = 1
POLICY_ERROR = "error"
POLICY_WARNING = "warning"
_POLICY_SEVERITIES = {POLICY_ERROR, POLICY_WARNING}


@dataclass(frozen=True)
class PolicyCheckContext:
    cfg: Mapping[str, Any]
    mode: str
    scope: str
    role_names: tuple[str, ...]
    role_models: Mapping[str, str]
    role_identities: Mapping[str, str]
    role_providers: Mapping[str, str]


@dataclass(frozen=True)
class PolicyCheckMessage:
    policy_key: str
    severity: str
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class PolicyCheckDefinition:
    key: str
    title: str
    summary: str
    check: Callable[[PolicyCheckContext], Any]
    contract_version: int = POLICY_CHECK_CONTRACT_VERSION


@dataclass(frozen=True)
class PolicyCheckLoadFailure:
    entry_point: str
    error: str


def _policy_check_entry_points() -> list[Any]:
    discovered = metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=POLICY_CHECK_ENTRY_POINT_GROUP))
    return list(discovered.get(POLICY_CHECK_ENTRY_POINT_GROUP, []))


def _normalize_policy_severity(value: Any) -> str:
    severity = normalize_non_empty(value, label="policy severity").lower()
    if severity not in _POLICY_SEVERITIES:
        choices = ", ".join(sorted(_POLICY_SEVERITIES))
        raise ValueError(f"policy severity must be one of: {choices}")
    return severity


def normalize_policy_check_message(value: Any, *, policy_key: str) -> PolicyCheckMessage:
    if isinstance(value, PolicyCheckMessage):
        return PolicyCheckMessage(
            policy_key=normalize_non_empty(value.policy_key, label="policy key"),
            severity=_normalize_policy_severity(value.severity),
            message=normalize_non_empty(value.message, label="policy message"),
            hint=str(value.hint).strip() if isinstance(value.hint, str) and value.hint.strip() else None,
        )

    if isinstance(value, str):
        return PolicyCheckMessage(
            policy_key=policy_key,
            severity=POLICY_ERROR,
            message=normalize_non_empty(value, label="policy message"),
        )

    if not isinstance(value, Mapping):
        raise TypeError("Policy checks must return a message mapping, PolicyCheckMessage, string, or iterable of those")

    hint = value.get("hint")
    clean_hint = str(hint).strip() if isinstance(hint, str) and hint.strip() else None
    return PolicyCheckMessage(
        policy_key=normalize_non_empty(value.get("policy_key") or policy_key, label="policy key"),
        severity=_normalize_policy_severity(value.get("severity", POLICY_ERROR)),
        message=normalize_non_empty(value.get("message"), label="policy message"),
        hint=clean_hint,
    )


def _iter_policy_messages(value: Any, *, policy_key: str) -> Iterable[PolicyCheckMessage]:
    if value is None:
        return []
    if isinstance(value, (PolicyCheckMessage, Mapping, str)):
        return [normalize_policy_check_message(value, policy_key=policy_key)]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, str)):
        return [
            normalize_policy_check_message(item, policy_key=policy_key)
            for item in value
        ]
    raise TypeError("Policy checks must return a message, iterable of messages, or None")


def _normalize_policy_check_definition(value: Any, *, fallback_key: str) -> PolicyCheckDefinition:
    value = maybe_invoke_entrypoint_loader(value)
    if isinstance(value, PolicyCheckDefinition):
        definition = value
    elif isinstance(value, Mapping):
        raw_check = value.get("check")
        if not callable(raw_check):
            raise TypeError("Policy check definitions must provide a callable 'check'")
        definition = PolicyCheckDefinition(
            key=normalize_non_empty(value.get("key") or fallback_key, label="policy key"),
            title=normalize_non_empty(value.get("title") or title_from_key(fallback_key), label="policy title"),
            summary=normalize_non_empty(value.get("summary"), label="policy summary"),
            check=raw_check,
            contract_version=normalize_contract_version(
                value.get("contract_version"),
                extension_name="policy check",
                expected_version=POLICY_CHECK_CONTRACT_VERSION,
            ),
        )
    elif callable(value):
        definition = PolicyCheckDefinition(
            key=fallback_key,
            title=title_from_key(fallback_key),
            summary=((value.__doc__ or "").strip() or f"External policy check '{fallback_key}'."),
            check=value,
        )
    else:
        raise TypeError("Policy providers must return PolicyCheckDefinition, a mapping, or a callable")

    if not callable(definition.check):
        raise TypeError("Policy check definitions must provide a callable 'check'")

    return PolicyCheckDefinition(
        key=normalize_non_empty(definition.key, label="policy key"),
        title=normalize_non_empty(definition.title, label="policy title"),
        summary=normalize_non_empty(definition.summary, label="policy summary"),
        check=definition.check,
        contract_version=normalize_contract_version(
            definition.contract_version,
            extension_name="policy check",
            expected_version=POLICY_CHECK_CONTRACT_VERSION,
        ),
    )


def discover_policy_checks() -> tuple[list[PolicyCheckDefinition], list[PolicyCheckLoadFailure]]:
    checks_by_key: dict[str, PolicyCheckDefinition] = {}
    failures: list[PolicyCheckLoadFailure] = []
    for entry_point in _policy_check_entry_points():
        entry_name = normalize_non_empty(getattr(entry_point, "name", "policy-check"), label="entry point name")
        fallback_key = entry_name.replace("_", "-").lower()
        try:
            loaded = entry_point.load()
            definition = _normalize_policy_check_definition(loaded, fallback_key=fallback_key)
        except Exception as err:  # noqa: BLE001
            failures.append(
                PolicyCheckLoadFailure(
                    entry_point=entry_name,
                    error=str(err),
                )
            )
            continue
        checks_by_key.setdefault(definition.key, definition)

    return [checks_by_key[key] for key in sorted(checks_by_key)], failures


def list_policy_checks() -> list[PolicyCheckDefinition]:
    checks, _failures = discover_policy_checks()
    return checks


def render_policy_message(message: PolicyCheckMessage) -> str:
    return f"[policy:{message.policy_key}] {message.message}"


def evaluate_policy_checks(context: PolicyCheckContext) -> list[PolicyCheckMessage]:
    checks, failures = discover_policy_checks()
    findings: list[PolicyCheckMessage] = [
        PolicyCheckMessage(
            policy_key=failure.entry_point,
            severity=POLICY_ERROR,
            message=f"Failed to load policy check '{failure.entry_point}': {failure.error}",
            hint="Fix or uninstall the broken policy package before relying on doctor results.",
        )
        for failure in failures
    ]

    for definition in checks:
        try:
            findings.extend(_iter_policy_messages(definition.check(context), policy_key=definition.key))
        except Exception as err:  # noqa: BLE001
            findings.append(
                PolicyCheckMessage(
                    policy_key=definition.key,
                    severity=POLICY_ERROR,
                    message=f"Policy check '{definition.key}' crashed: {err}",
                    hint="Fix the installed policy check implementation or remove it from the environment.",
                )
            )
    return findings
