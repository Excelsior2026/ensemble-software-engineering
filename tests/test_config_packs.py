from __future__ import annotations

import pytest

from ese.config_packs import (
    CONFIG_PACK_ENTRY_POINT_GROUP,
    ConfigPackDefinition,
    ConfigPackLoadFailure,
    PackRoleDefinition,
    discover_config_packs,
    get_config_pack,
    list_config_packs,
    normalize_config_pack_definition,
)


class _FakeEntryPoint:
    def __init__(self, name: str, payload) -> None:  # noqa: ANN001
        self.name = name
        self._payload = payload

    def load(self):  # noqa: ANN201
        return self._payload


def test_list_config_packs_returns_empty_when_none_are_installed(monkeypatch) -> None:
    monkeypatch.setattr("ese.config_packs._config_pack_entry_points", lambda: [])

    assert list_config_packs() == []


def test_list_config_packs_loads_external_entry_points(monkeypatch) -> None:
    payload = {
        "key": "release-ops",
        "title": "Release Operations",
        "summary": "Reusable release-review pack",
        "preset": "strict",
        "goal_profile": "high-quality",
        "roles": [
            {
                "key": "release_planner",
                "responsibility": "Plan the release",
                "prompt": "Plan the release.",
            }
        ],
    }
    monkeypatch.setattr(
        "ese.config_packs._config_pack_entry_points",
        lambda: [_FakeEntryPoint(CONFIG_PACK_ENTRY_POINT_GROUP, payload)],
    )

    packs = list_config_packs()

    assert len(packs) == 1
    assert packs[0].key == "release-ops"
    assert packs[0].contract_version == 1
    assert packs[0].roles[0].key == "release_planner"


def test_discover_config_packs_reports_invalid_entry_points(monkeypatch) -> None:
    monkeypatch.setattr(
        "ese.config_packs._config_pack_entry_points",
        lambda: [_FakeEntryPoint("broken_pack", {"roles": []})],
    )

    packs, failures = discover_config_packs()

    assert packs == []
    assert failures == [ConfigPackLoadFailure(entry_point="broken_pack", error="config pack roles must not be empty")]


def test_get_config_pack_finds_installed_pack(monkeypatch) -> None:
    pack = ConfigPackDefinition(
        key="release-ops",
        title="Release Operations",
        summary="Reusable release-review pack",
        preset="strict",
        goal_profile="high-quality",
        roles=(
            PackRoleDefinition(
                key="release_planner",
                responsibility="Plan the release",
                prompt="Plan the release.",
            ),
        ),
    )
    monkeypatch.setattr("ese.config_packs.list_config_packs", lambda: [pack])

    resolved = get_config_pack("release-ops")

    assert resolved == pack


def test_normalize_config_pack_definition_rejects_duplicate_role_keys() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        normalize_config_pack_definition(
            {
                "key": "release-ops",
                "title": "Release Operations",
                "summary": "Reusable release-review pack",
                "preset": "strict",
                "goal_profile": "high-quality",
                "roles": [
                    {
                        "key": "release_planner",
                        "responsibility": "Plan the release",
                        "prompt": "Plan the release.",
                    },
                    {
                        "key": "release_planner",
                        "responsibility": "Review the release",
                        "prompt": "Review the release.",
                    },
                ],
            }
        )


def test_normalize_config_pack_definition_rejects_unsupported_contract_version() -> None:
    with pytest.raises(ValueError, match="not supported"):
        normalize_config_pack_definition(
            {
                "contract_version": 99,
                "key": "release-ops",
                "title": "Release Operations",
                "summary": "Reusable release-review pack",
                "preset": "strict",
                "goal_profile": "high-quality",
                "roles": [
                    {
                        "key": "release_planner",
                        "responsibility": "Plan the release",
                        "prompt": "Plan the release.",
                    }
                ],
            }
        )
