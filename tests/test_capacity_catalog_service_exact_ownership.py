"""Exact existing capacity-pool ownership for catalog member selection."""
from __future__ import annotations

import copy

import pytest
from test_capacity_inputs import input_plan

from genshin_app.equipment.schema import PlanError, validate_plan


def _two_abilities():
    plan = input_plan()
    second = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    second["id"] = "e2"
    plan["rotation_model"]["abilities"].append(second)
    validate_plan(plan)
    return plan


def _extract(plan, members):
    from genshin_app.equipment.action_capacity_catalog import catalog_filter_from_plan

    return catalog_filter_from_plan(plan, "a", members)


def test_members_from_two_disjoint_existing_capacity_pools_are_rejected():
    plan = _two_abilities()
    second = copy.deepcopy(plan["action_capacity_sources"][0])
    second.update(id="capacity.e2", ability_ids=["e2"])
    plan["action_capacity_sources"].append(second)
    validate_plan(plan)

    with pytest.raises(PlanError) as exc:
        _extract(plan, ["e", "e2"])
    assert exc.value.reason == "capacity_catalog_input"


def test_bound_and_unbound_members_cannot_be_mixed():
    plan = _two_abilities()

    with pytest.raises(PlanError) as exc:
        _extract(plan, ["e", "e2"])
    assert exc.value.reason == "capacity_catalog_input"


def test_subset_of_an_existing_capacity_pool_is_rejected():
    plan = _two_abilities()
    plan["action_capacity_sources"][0]["ability_ids"] = ["e", "e2"]
    validate_plan(plan)

    with pytest.raises(PlanError) as exc:
        _extract(plan, ["e"])
    assert exc.value.reason == "capacity_catalog_input"


def test_exact_existing_pool_is_allowed_regardless_of_member_order():
    plan = _two_abilities()
    plan["action_capacity_sources"][0]["ability_ids"] = ["e2", "e"]
    validate_plan(plan)

    result = _extract(plan, ["e", "e2"])
    assert result["observed"]["target"] == {
        "skill_depot_id": 9001,
        "skill_id": 90012,
        "variant": "tap",
    }


def test_selection_disjoint_from_existing_pool_remains_unbound():
    plan = _two_abilities()

    result = _extract(plan, ["e2"])
    assert result["observed"]["target"] == {"variant": "tap"}
    assert {"target.skill_depot_id", "target.skill_id"} <= set(result["unknown_paths"])
