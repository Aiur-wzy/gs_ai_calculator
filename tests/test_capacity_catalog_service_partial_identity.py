"""Fail-closed catalog identity checks for partially covered ability pools."""
from __future__ import annotations

import copy

import pytest
from test_capacity_inputs import input_plan

from genshin_app.equipment.schema import PlanError, validate_plan


def _partial_pool(*, with_capacity=True):
    plan = input_plan()
    first = plan["rotation_model"]["abilities"][0]
    second = copy.deepcopy(first)
    second["id"] = "e2"
    plan["rotation_model"]["abilities"].append(second)
    if with_capacity:
        plan["action_capacity_sources"][0]["ability_ids"] = ["e", "e2"]
    else:
        plan["action_capacity_sources"] = []
    plan["action_resource_sources"] = [{
        "ability_id": "e",
        "version_line": "release",
        "selection": {
            "character_id": 90000001,
            "constellation": 1,
            "skill_id": 90012,
            "skill_depot_id": 9001,
        },
        "purpose": "ordinary_fixed_burst_base_resources",
        "variant": "tap",
        "status": "confirmed",
        "source": "synthetic partial typed identity",
        "expected_snapshot_id": None,
        "expected_resource_fingerprint": None,
        "expected_scope_fingerprint": None,
    }]
    validate_plan(plan)
    return plan


def _extract(plan):
    from genshin_app.equipment.action_capacity_catalog import catalog_filter_from_plan

    return catalog_filter_from_plan(plan, "a", ["e", "e2"])


@pytest.mark.parametrize(("path", "value"), [
    (("version_line",), "beta"),
    (("selection", "character_id"), 90000002),
    (("selection", "constellation"), 2),
    (("selection", "skill_id"), 90013),
    (("selection", "skill_depot_id"), 9002),
    (("variant",), "hold"),
])
def test_partial_resource_source_conflict_with_known_facts_fails_closed(path, value):
    plan = _partial_pool()
    target = plan["action_resource_sources"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    validate_plan(plan)

    with pytest.raises(PlanError) as exc:
        _extract(plan)
    assert exc.value.reason == "capacity_catalog_input"


def test_partial_resource_coverage_without_capacity_does_not_fill_skill_identity():
    result = _extract(_partial_pool(with_capacity=False))

    assert result["observed"]["target"] == {"variant": "tap"}
    assert {"target.skill_depot_id", "target.skill_id"} <= set(result["unknown_paths"])


def test_complete_consistent_resource_coverage_can_fill_skill_identity():
    plan = _partial_pool(with_capacity=False)
    second = copy.deepcopy(plan["action_resource_sources"][0])
    second["ability_id"] = "e2"
    plan["action_resource_sources"].append(second)
    validate_plan(plan)

    result = _extract(plan)
    assert result["observed"]["target"] == {
        "skill_depot_id": 9001,
        "skill_id": 90012,
        "variant": "tap",
    }
    assert "target.skill_depot_id" not in result["unknown_paths"]
    assert "target.skill_id" not in result["unknown_paths"]


def test_compatible_partial_resource_source_keeps_existing_capacity_target():
    result = _extract(_partial_pool())

    assert result["observed"]["target"] == {
        "skill_depot_id": 9001,
        "skill_id": 90012,
        "variant": "tap",
    }
    assert not result["unknown_paths"]
