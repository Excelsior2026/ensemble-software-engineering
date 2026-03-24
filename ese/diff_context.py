"""Helpers for building file-aware diff excerpts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiffExcerpt:
    text: str
    truncated: bool
    included_file_patches: int
    total_file_patches: int


def _split_patch_by_file(patch: str) -> list[str]:
    if not patch:
        return []

    lines = patch.splitlines(keepends=True)
    segments: list[str] = []
    current: list[str] = []
    saw_git_header = False

    for line in lines:
        if line.startswith("diff --git "):
            saw_git_header = True
            if current:
                segments.append("".join(current))
            current = [line]
            continue
        current.append(line)

    if current:
        segments.append("".join(current))

    if saw_git_header:
        return segments
    return [patch]


def build_file_aware_diff_excerpt(
    patch: str,
    *,
    limit: int,
    truncated_label: str,
) -> DiffExcerpt:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    trailer = f"\n\n[{truncated_label}]\n"
    segments = _split_patch_by_file(patch)
    total_file_patches = len(segments)

    if len(patch) <= limit:
        return DiffExcerpt(
            text=patch,
            truncated=False,
            included_file_patches=total_file_patches,
            total_file_patches=total_file_patches,
        )

    available = max(limit - len(trailer), 0)
    if available == 0:
        return DiffExcerpt(
            text=trailer[:limit],
            truncated=True,
            included_file_patches=0,
            total_file_patches=total_file_patches,
        )

    if not segments:
        trimmed = patch[:available].rstrip() + trailer
        return DiffExcerpt(
            text=trimmed,
            truncated=True,
            included_file_patches=0,
            total_file_patches=0,
        )

    included: list[str] = []
    used = 0
    for segment in segments:
        segment_length = len(segment)
        if included and used + segment_length > available:
            break
        if not included and segment_length > available:
            included.append(segment[:available])
            used = len(included[0])
            break
        included.append(segment)
        used += segment_length

    trimmed = "".join(included).rstrip() + trailer
    return DiffExcerpt(
        text=trimmed,
        truncated=True,
        included_file_patches=len(included),
        total_file_patches=total_file_patches,
    )
