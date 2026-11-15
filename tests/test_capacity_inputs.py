# ruff: noqa: F811 - pytest fixture injection uses imported names.
import copy

import pytest
from capacity_fixture import context
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    ready,
    source_db,
)

from genshin_app.equipment.action_capacities import (
    compile_action_capacities,
    pool_material,
)
from genshin_app.equipment.action_capacity_inputs import actual_selection
from genshin_app.equipment.action_capacity_schema import SLOTS
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import fixture

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def input_plan():
    template, rotation, mechanics = fixture()
    actor = template["characters"][0]["id"]
    rotation["abilities"][0].update(id="e", character=actor, action_type_id="action.elemental_skill", variant="tap",
        cooldown={"group": "e", "duration": 10, "start": "action_start", "recovery": "sequential"}, energy=None)
    rotation["initial_cooldowns"] = [{"character": actor, "group": "e", "ready_in": []}]
    rotation["actions"][0]["ability_id"] = "e"
    binding = {"id": "capacity.e", "actor": actor, "ability_ids": ["e"], "version_line": "release",
               "model_ref": {"id": "fixture.capacity.model", "revision": 1}, "accepted_fingerprint": None,
               "target": {"character_id": 90000001, "skill_depot_id": 9001, "skill_id": 90012, "variant": "tap"}}
    template["characters"][0]["level"] = 90
    template["characters"][0]["stats"]["energy_recharge"] = 1
    gear = {"mode": "panel", "panel_policy": "static_included", "panel_basis_mode": "manual", "data_version": None, "source_refs": [{"kind": "manual", "note": "engineering fixture"}], "character_ref": {"db_character_id": 90000001, "version_line": "release"},
            "weapon": {"weapon_id": 11401, "level": 1, "ascension": 0, "refinement": 1, "refinements": [], "selected_by": "user"},
            "artifact_sets": [], "artifact_presence": dict.fromkeys(SLOTS, "empty"),
            "progression": {"ascension": 6, "constellation": 1, "talent_base_levels": {"skill": 10}, "status": "confirmed", "source": "engineering"}}
    from genshin_app.equipment.schema import new_plan
    return {**new_plan(template), "plan_version": 3, "manual_template": template, "rotation_model": rotation, "mechanics_model": mechanics,
            "equipment_inputs": {actor: gear}, "effect_bindings": [], "action_capacity_sources": [binding], "action_capacity_context": context()}


def test_existing_compiler_piece_confirms_once_and_owns_only_capacity(ready):
    plan = input_plan()
    original = copy.deepcopy(plan)
    issues = []
    _, first, sources = compile_action_capacities(plan, ready[0], lambda *args: issues.append(args))
    assert plan == original and sources == [] and first[0]["required_confirmation"], first
    plan["action_capacity_sources"][0].update(first[0]["required_confirmation"])
    compiled, records, sources = compile_action_capacities(plan, ready[0], lambda *args: pytest.fail(str(args)))
    assert records[0]["status"] == "ready" and records[0]["source_fingerprint"] == first[0]["source_fingerprint"]
    cd = compiled["abilities"][0]["cooldown"]
    assert cd["charges"] == 2 and cd["duration"] == 10 and len(sources) == 1
    assert "charges" not in plan["rotation_model"]["abilities"][0]["cooldown"]


@pytest.mark.parametrize("kind", ["action.normal_attack", "action.elemental_burst", None])
def test_unbound_non_skill_ability_cannot_borrow_pool(kind):
    plan = input_plan()
    hidden = copy.deepcopy(plan["rotation_model"]["abilities"][0]); hidden["id"] = "hidden"
    hidden["cooldown"]["charges"] = 2
    if kind is None: hidden.pop("action_type_id")
    else: hidden["action_type_id"] = kind
    plan["rotation_model"]["abilities"].append(hidden)
    with pytest.raises(PlanError, match="未绑定能力"):
        pool_material(plan, plan["action_capacity_sources"][0])


@pytest.mark.parametrize("change", ["presence_missing", "presence_unknown", "has_set", "refinement_missing", "constellation_missing", "base_level_missing"])
def test_actual_equipment_missing_is_not_inferred(change):
    plan = input_plan(); binding = plan["action_capacity_sources"][0]; gear = plan["equipment_inputs"][binding["actor"]]
    if change == "presence_missing": gear.pop("artifact_presence")
    elif change == "presence_unknown": gear["artifact_presence"][SLOTS[0]] = "unknown"
    elif change == "has_set": gear["artifact_sets"] = [{"set_id": 1, "count": 2}]
    elif change == "refinement_missing": gear["weapon"].pop("refinement")
    elif change == "constellation_missing": gear["progression"].pop("constellation")
    else: gear["progression"].pop("talent_base_levels")
    with pytest.raises(PlanError): actual_selection(plan, binding)


def test_initial_queue_is_not_silently_created():
    plan = input_plan(); plan["rotation_model"].pop("initial_cooldowns")
    with pytest.raises(PlanError, match="显式填写"):
        pool_material(plan, plan["action_capacity_sources"][0])


@pytest.mark.parametrize('field,value', [('skill_id', 90011), ('skill_depot_id', 9002), ('character_id', 90000002), ('slot', 'burst')])
def test_typed_hit_parent_must_match_capacity_target(field, value):
    plan = input_plan()
    selected = {'character_id': 90000001, 'skill_depot_id': 9001, 'skill_id': 90012, 'constellation': 1}
    selected[field] = value
    plan['hit_parameter_sources'] = [{'hit_id': 'h0', 'version_line': 'release', 'selection': selected}]
    with pytest.raises(PlanError, match='来源矛盾'):
        pool_material(plan, plan['action_capacity_sources'][0])


def test_variant_cannot_disagree_with_typed_action_dispatch():
    plan = input_plan()
    plan['action_event_profiles'] = [{'id': 'p', 'variant': 'hold', 'prototype_hit_id': 'h0'}]
    plan['action_event_bindings'] = [{'ability_id': 'e', 'profile_id': 'p'}]
    with pytest.raises(PlanError, match='变体矛盾'):
        pool_material(plan, plan['action_capacity_sources'][0])
