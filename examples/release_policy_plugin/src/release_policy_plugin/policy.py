"""Release-focused external doctor policy."""

from __future__ import annotations

from ese.policy_checks import POLICY_ERROR, PolicyCheckDefinition

_SCOPE_MARKERS = ("release", "rollout", "deploy", "deployment")


def _check_release_safety(context):
    scope = context.scope.lower()
    if not any(marker in scope for marker in _SCOPE_MARKERS):
        return []

    configured_roles = {role.lower() for role in context.role_names}
    has_release_owner = any(
        role in configured_roles
        for role in {"release_manager", "release_reviewer", "release_planner", "devops_sre"}
    ) or any(role.startswith("release_") for role in configured_roles)

    if has_release_owner:
        return []

    return [
        {
            "severity": POLICY_ERROR,
            "message": "Release-sensitive scope requires a release-focused role.",
            "hint": "Add a release role such as release_manager, release_planner, or release_reviewer before running.",
        }
    ]


def load_policy():
    """Return the external release-safety policy definition."""
    return PolicyCheckDefinition(
        key="release-safety",
        title="Release Safety",
        summary="Require a release-focused role when the scope is release-sensitive.",
        check=_check_release_safety,
    )
