from __future__ import annotations

from ese.init_wizard import _apply_simple_mode_model_diversity, _ensemble_constraints


def test_ensemble_constraints_filters_to_selected_roles() -> None:
    constraints = _ensemble_constraints(["architect", "implementer", "release_manager"])

    assert constraints["disallow_same_model_pairs"] == [
        ["architect", "implementer"],
        ["implementer", "release_manager"],
    ]


def test_simple_mode_model_diversity_overrides_implementer() -> None:
    cfg = {
        "provider": {"name": "openai", "model": "gpt-5"},
        "roles": {
            "architect": {},
            "implementer": {},
        },
    }

    _apply_simple_mode_model_diversity(
        cfg,
        provider="openai",
        selected_roles=["architect", "implementer"],
    )

    assert cfg["roles"]["implementer"]["model"] != "gpt-5"
