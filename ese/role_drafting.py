"""Helpers for drafting framework-mode role prompts and detecting overlap."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Jaccard similarity threshold for overlap detection (0.0-1.0)
# Roles with similarity >= this value will generate overlap warnings
OVERLAP_SIMILARITY_THRESHOLD = 0.35

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_GENERIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "all",
    "any",
    "each",
    "their",
    "this",
    "that",
    "will",
    "should",
    "can",
    "must",
    "team",
    "member",
    "members",
    "ensemble",
    "workflow",
    "role",
    "roles",
    "project",
    "work",
    "handle",
    "handles",
    "handling",
    "help",
    "helps",
    "support",
    "supports",
    "own",
    "owns",
    "owner",
    "review",
    "reviews",
    "reviewing",
    "analyze",
    "analyzes",
    "analysis",
    "assess",
    "assesses",
    "assessment",
    "check",
    "checks",
    "checking",
    "build",
    "builds",
    "building",
    "create",
    "creates",
    "creating",
    "make",
    "makes",
    "making",
    "ensure",
    "ensures",
    "ensuring",
    "provide",
    "provides",
    "providing",
    "coordinate",
    "coordinates",
    "coordinating",
    "manage",
    "manages",
    "managing",
}

_OUTPUT_TERMS = {
    "artifact",
    "artifacts",
    "brief",
    "checklist",
    "decision",
    "decisions",
    "deliverable",
    "deliverables",
    "diff",
    "document",
    "documents",
    "evidence",
    "finding",
    "findings",
    "plan",
    "plans",
    "register",
    "report",
    "reports",
    "scorecard",
    "spec",
    "specs",
    "summary",
    "test",
    "tests",
}

_EVIDENCE_TERMS = {
    "clause",
    "clauses",
    "code",
    "commit",
    "commits",
    "contract",
    "contracts",
    "data",
    "diff",
    "diffs",
    "document",
    "documents",
    "evidence",
    "log",
    "logs",
    "metric",
    "metrics",
    "repo",
    "repository",
    "requirements",
    "source",
    "sources",
    "test",
    "tests",
}

_BOUNDARY_TERMS = {
    "avoid",
    "boundary",
    "boundaries",
    "except",
    "exclude",
    "excluding",
    "focus",
    "limit",
    "limits",
    "not",
    "only",
    "unless",
    "without",
}

_BROAD_TERMS = {
    "everything",
    "everyone",
    "entire",
    "general",
    "overall",
    "complete",
    "end",
    "full",
    "stack",
}


@dataclass(frozen=True)
class FrameworkRoleInput:
    name: str
    responsibility: str


@dataclass(frozen=True)
class FrameworkRoleDraft:
    name: str
    key: str
    responsibility: str
    prompt: str
    suggestions: tuple[str, ...]
    warnings: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class FrameworkRoleDraftReview:
    drafts: tuple[FrameworkRoleDraft, ...]
    overlap_warnings: tuple[str, ...]


def normalize_role_key(name: str) -> str:
    cleaned = _NON_ALNUM_RE.sub("_", (name or "").strip().lower()).strip("_")
    return cleaned or "role"


def _extract_keywords(*parts: str) -> tuple[str, ...]:
    keywords: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in _TOKEN_RE.findall((part or "").lower()):
            if len(token) < 4 or token in _GENERIC_STOPWORDS:
                continue
            if token in seen:
                continue
            seen.add(token)
            keywords.append(token)
    return tuple(keywords)


def build_framework_role_prompt(*, role_name: str, responsibility: str, scope: str) -> str:
    scope_text = (scope or "").strip()
    role_text = (role_name or "").strip()
    responsibility_text = (responsibility or "").strip()
    lines = [
        f"You are the {role_text} role in an ensemble workflow.",
        f"Primary responsibility: {responsibility_text}",
    ]
    if scope_text:
        lines.append(f"Current scope: {scope_text}")
    lines.extend(
        [
            "Stay specific to this responsibility and avoid absorbing peer responsibilities unless you are escalating a concrete dependency or conflict.",
            "Return a concise summary, evidence-backed findings, the artifacts you contribute, and actionable next_steps.",
            "If required inputs are missing or your boundary is unclear, say so explicitly instead of guessing.",
        ],
    )
    return " ".join(lines)


def draft_framework_roles(
    *,
    scope: str,
    roles: Iterable[FrameworkRoleInput],
) -> FrameworkRoleDraftReview:
    drafts: list[FrameworkRoleDraft] = []
    used_keys: dict[str, int] = {}

    for role in roles:
        raw_name = (role.name or "").strip()
        raw_responsibility = (role.responsibility or "").strip()
        key_base = normalize_role_key(raw_name)
        collision_count = used_keys.get(key_base, 0)
        used_keys[key_base] = collision_count + 1
        key = key_base if collision_count == 0 else f"{key_base}_{collision_count + 1}"

        warnings: list[str] = []
        suggestions: list[str] = []
        keywords = _extract_keywords(raw_name, raw_responsibility)
        words = _TOKEN_RE.findall(raw_responsibility.lower())

        if collision_count:
            warnings.append(
                f"Role name '{raw_name}' normalizes to duplicate key '{key_base}'. It was renamed to '{key}' in config.",
            )
        if len(words) < 8:
            warnings.append("Responsibility is short. Add more detail so the role has a distinct lane.")
        if any(term in words for term in _BROAD_TERMS):
            warnings.append("Responsibility sounds broad. Broad ownership weakens ensemble independence.")
        if not any(token in _OUTPUT_TERMS for token in words):
            suggestions.append("Name a concrete artifact, decision, or handoff this role owns.")
        if not any(token in _EVIDENCE_TERMS for token in words):
            suggestions.append("Specify what evidence, inputs, or source material this role should rely on.")
        if not any(token in _BOUNDARY_TERMS for token in words):
            suggestions.append("Add a boundary such as what this role should avoid or leave to peers.")

        drafts.append(
            FrameworkRoleDraft(
                name=raw_name,
                key=key,
                responsibility=raw_responsibility,
                prompt=build_framework_role_prompt(
                    role_name=raw_name,
                    responsibility=raw_responsibility,
                    scope=scope,
                ),
                suggestions=tuple(suggestions),
                warnings=tuple(warnings),
                keywords=keywords,
            ),
        )

    overlap_warnings = _detect_overlap_warnings(drafts)
    return FrameworkRoleDraftReview(
        drafts=tuple(drafts),
        overlap_warnings=tuple(overlap_warnings),
    )


def _detect_overlap_warnings(drafts: Iterable[FrameworkRoleDraft]) -> list[str]:
    draft_list = list(drafts)
    warnings: list[str] = []
    for index, left in enumerate(draft_list):
        left_keywords = set(left.keywords)
        if not left_keywords:
            continue
        for right in draft_list[index + 1 :]:
            right_keywords = set(right.keywords)
            if not right_keywords:
                continue
            shared = left_keywords & right_keywords
            union = left_keywords | right_keywords
            if len(shared) < 2 or not union:
                continue
            similarity = len(shared) / len(union)
            if similarity < OVERLAP_SIMILARITY_THRESHOLD:
                continue
            shared_text = ", ".join(sorted(shared))
            warnings.append(
                f"'{left.name}' and '{right.name}' appear to overlap on {shared_text}. "
                "Split their ownership more cleanly or state the handoff boundary explicitly.",
            )
    return warnings
