from __future__ import annotations

from ese.diff_context import build_file_aware_diff_excerpt


def test_build_file_aware_diff_excerpt_preserves_file_boundaries_when_possible() -> None:
    first_patch = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-print('old')\n"
        "+print('new')\n"
    )
    second_patch = (
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n"
        "+++ b/tests/test_app.py\n"
        "@@ -1 +1 @@\n"
        "-assert old\n"
        "+assert new\n"
    )
    patch = first_patch + second_patch

    excerpt = build_file_aware_diff_excerpt(
        patch,
        limit=len(first_patch) + 40,
        truncated_label="diff truncated by ESE",
    )

    assert excerpt.truncated is True
    assert excerpt.included_file_patches == 1
    assert excerpt.total_file_patches == 2
    assert "diff --git a/app.py b/app.py" in excerpt.text
    assert "diff --git a/tests/test_app.py b/tests/test_app.py" not in excerpt.text
    assert "[diff truncated by ESE]" in excerpt.text
