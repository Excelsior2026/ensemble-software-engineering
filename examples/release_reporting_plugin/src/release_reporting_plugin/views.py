"""Example external ESE artifact view."""

from __future__ import annotations

from ese.artifact_views import ArtifactViewDefinition


def _render_release_brief(report: dict) -> str:
    blockers = report.get("blockers", [])
    next_steps = report.get("next_steps", [])
    lines = [
        "# Release Brief",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Assurance: {report.get('assurance_level', 'standard')}",
        f"- Blockers: {len(blockers)}",
        "",
        "## Scope",
        "",
        str(report.get("scope") or "No scope recorded."),
    ]
    if blockers:
        lines.extend(["", "## Blockers", ""])
        for blocker in blockers[:5]:
            lines.append(f"- {blocker.get('role')}: {blocker.get('title')}")
    if next_steps:
        lines.extend(["", "## Next Steps", ""])
        for step in next_steps[:5]:
            lines.append(f"- {step.get('role')}: {step.get('text')}")
    return "\n".join(lines) + "\n"


def load_view():
    """Return the example release brief artifact view."""
    return ArtifactViewDefinition(
        key="release-brief",
        title="Release Brief",
        summary="Generated markdown brief for release and rollout review.",
        format="md",
        render=_render_release_brief,
    )
