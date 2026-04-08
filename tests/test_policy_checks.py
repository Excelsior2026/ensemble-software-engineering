from __future__ import annotations

from ese.policy_checks import (
    POLICY_WARNING,
    PolicyCheckContext,
    PolicyCheckDefinition,
    PolicyCheckMessage,
    discover_policy_checks,
    evaluate_policy_checks,
    list_policy_checks,
    render_policy_message,
)


class _FakeEntryPoint:
    def __init__(self, name: str, payload) -> None:  # noqa: ANN001
        self.name = name
        self._payload = payload

    def load(self):  # noqa: ANN201
        return self._payload


def _context() -> PolicyCheckContext:
    return PolicyCheckContext(
        cfg={"mode": "ensemble"},
        mode="ensemble",
        scope="Review the release rollout",
        role_names=("architect", "release_reviewer"),
        role_models={"architect": "openai:gpt-5", "release_reviewer": "openai:gpt-5-mini"},
        role_identities={"architect": "openai:gpt-5", "release_reviewer": "openai:gpt-5-mini"},
        role_providers={"architect": "openai", "release_reviewer": "openai"},
    )


def test_list_policy_checks_returns_empty_when_none_are_installed(monkeypatch) -> None:
    monkeypatch.setattr("ese.policy_checks._policy_check_entry_points", lambda: [])

    assert list_policy_checks() == []


def test_discover_policy_checks_loads_entry_points(monkeypatch) -> None:
    definition = PolicyCheckDefinition(
        key="release-safety",
        title="Release Safety",
        summary="Require release-focused roles for rollout scopes.",
        check=lambda context: [],
    )
    monkeypatch.setattr(
        "ese.policy_checks._policy_check_entry_points",
        lambda: [_FakeEntryPoint("release_safety", definition)],
    )

    checks, failures = discover_policy_checks()

    assert failures == []
    assert len(checks) == 1
    assert checks[0].key == "release-safety"


def test_discover_policy_checks_loads_loader_callables(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.policy_checks._policy_check_entry_points",
        lambda: [
            _FakeEntryPoint(
                "release_safety",
                lambda: PolicyCheckDefinition(
                    key="release-safety",
                    title="Release Safety",
                    summary="Require release-focused roles for rollout scopes.",
                    check=lambda context: [],
                ),
            )
        ],
    )

    checks, failures = discover_policy_checks()

    assert failures == []
    assert len(checks) == 1
    assert checks[0].key == "release-safety"


def test_evaluate_policy_checks_renders_warning_messages(monkeypatch) -> None:
    definition = PolicyCheckDefinition(
        key="release-safety",
        title="Release Safety",
        summary="Require release-focused roles for rollout scopes.",
        check=lambda context: [
            PolicyCheckMessage(
                policy_key="release-safety",
                severity=POLICY_WARNING,
                message="Review scope lacks an explicit release owner.",
                hint="Add a release-focused role for rollout-sensitive scopes.",
            )
        ],
    )
    monkeypatch.setattr(
        "ese.policy_checks._policy_check_entry_points",
        lambda: [_FakeEntryPoint("release_safety", definition)],
    )

    findings = evaluate_policy_checks(_context())

    assert len(findings) == 1
    assert findings[0].severity == POLICY_WARNING
    assert render_policy_message(findings[0]) == "[policy:release-safety] Review scope lacks an explicit release owner."


def test_evaluate_policy_checks_reports_load_failures(monkeypatch) -> None:
    class _BrokenEntryPoint:
        name = "broken_policy"

        @staticmethod
        def load():  # noqa: ANN205
            raise RuntimeError("boom")

    monkeypatch.setattr("ese.policy_checks._policy_check_entry_points", lambda: [_BrokenEntryPoint()])

    findings = evaluate_policy_checks(_context())

    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "Failed to load policy check" in findings[0].message


def test_discover_policy_checks_rejects_unsupported_contract_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.policy_checks._policy_check_entry_points",
        lambda: [
            _FakeEntryPoint(
                "release_safety",
                {
                    "key": "release-safety",
                    "title": "Release Safety",
                    "summary": "Require release-focused roles for rollout scopes.",
                    "contract_version": 99,
                    "check": lambda context: [],
                },
            )
        ],
    )

    checks, failures = discover_policy_checks()

    assert checks == []
    assert len(failures) == 1
    assert "not supported" in failures[0].error
