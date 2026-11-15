"""EC22 independent App boundaries; saved real nodes, no formal DB or approval."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_cast_graph import build_mechanism_cast_graph
from genshin_app.equipment.mechanism_cast_scope import (
    qualify_cast_provider,
    reconcile_equipment_coverage,
)
from genshin_app.equipment.mechanism_execution_scope import qualify_scene
from genshin_app.equipment.mechanism_receipt import _replay
from genshin_app.equipment.mechanism_sources import make_replay_context
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from tests.test_mechanism_cast_graph import identity, inputs
from tests.test_mechanism_cast_integration import accepted, compose, scene
from tests.test_mechanism_qualification import qualified_case


def graph(plan, template, binding, material, **kwargs):
    return build_mechanism_cast_graph(plan, template, binding=binding, material=material,
                                     identity_provider=identity, **kwargs)


def replay_context(plan, template, requirements, records):
    return make_replay_context(plan, template, records=records, action_event_compilations=[],
                              attack_metadata_compilations=[], parameter_compilations=[],
                              equipment_requirements=requirements)


@pytest.mark.parametrize("field,value", [("constellation", 1), ("talent_level", 9),
                                         ("slot", "burst"), ("variant", "tap")])
def test_review_source_selection_bounds_cannot_disappear_in_cast_graph(field, value):
    plan, template, binding, material = inputs()
    material["source_binding"]["selection"][field] = value
    with pytest.raises(ValueError):
        graph(plan, template, binding, material)


@pytest.mark.parametrize("field,value", [("skill_id", 92), ("skill_depot_id", 82),
                                         ("character_id", 72), ("constellation", 1)])
def test_review_explicit_fresh_resource_conflicts_cannot_override_caster(field, value):
    plan, template, binding, material = inputs(True)
    receipt = {"ability_id": "e", "actor": "a", "status": "ready",
               "snapshot": copy.deepcopy(material["snapshot"]), "selection": {field: value},
               "resolved_selection": {}, "compiled_values": {"energy": 10}}
    with pytest.raises(ValueError):
        graph(plan, template, binding, material, resource_records=[receipt])


def test_review_alias_cooldown_pool_is_one_even_when_both_actions_are_covered():
    plan, template, binding, material = inputs(True)
    ab = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ab.update(id="second-q")
    ab["cooldown"]["group"] = "false-second-pool"
    plan["rotation_model"]["abilities"].append(ab)
    action = copy.deepcopy(plan["rotation_model"]["actions"][0])
    action.update(id="second-cast", ability_id="second-q", start=2, occupation_end=2.1)
    plan["rotation_model"]["actions"].append(action)
    spend = copy.deepcopy(plan["rotation_model"]["events"][0])
    spend.update(id="second-spend", action_id="second-cast")
    plan["rotation_model"]["events"].append(spend)
    binding["cast_binding"]["ability_ids"].append("second-q")
    with pytest.raises(ValueError, match="冷却"):
        graph(plan, template, binding, material)
    ab["cooldown"] = copy.deepcopy(plan["rotation_model"]["abilities"][0]["cooldown"])
    assert [p["action_id"] for p in graph(plan, template, binding, material)["parents"]] == ["cast", "second-cast"]


@pytest.mark.parametrize("fault", ["zero_cost", "missing_charge", "wrong_spend", "missing_spend"])
def test_review_manual_q_cannot_reduce_or_omit_real_base_resources(fault):
    plan, template, binding, material = inputs(True)
    if fault == "zero_cost":
        plan["rotation_model"]["abilities"][0]["energy"] = {"amount": 0, "required": 0}
    elif fault == "missing_charge":
        del plan["rotation_model"]["abilities"][0]["cooldown"]["charges"]
    elif fault == "wrong_spend":
        plan["rotation_model"]["events"][0]["amount"] = 0
    else:
        plan["rotation_model"]["events"] = []
    with pytest.raises(ValueError):
        graph(plan, template, binding, material)


def test_review_partial_equipment_never_claims_the_complete_pending_requirement():
    plan, template, material, requirements = scene()
    # This direct provider test isolates completeness from source approval.
    material["compiler_result"] = compile_mechanism_descriptor(material["descriptor"],
        source_proof=material["source_proof"], facts=material["facts"], parameters=material["resolved_parameters"])
    material["compiler_result"]["execution_qualification"]["provider"]["requirement_coverage"] = "partial"
    binding = plan["mechanism_sources"][0]
    parents = graph(plan, template, binding, material)
    proof, coverage = qualify_cast_provider(plan, template, binding, material, parents, {}, requirements)
    assert proof["provider_proof"]["equipment"]["count"] == 4
    assert coverage == []
    issues = [{"code": "effect_pending", "path": "effect_bindings.set-four", "binding_id": "set-four"}]
    reconcile_equipment_coverage([{"id": "review", "status": "ready", "equipment_coverage": coverage}], requirements, issues)
    assert len(issues) == 1 and "mechanism_coverage" not in requirements[0]


@pytest.mark.parametrize("fault", ["two_pieces", "wrong_set", "wrong_holder", "wrong_level", "wrong_affix"])
def test_review_equipment_owner_and_selected_affix_are_separate_from_source_subject(fault):
    plan, template, material, requirements = scene()
    if fault == "two_pieces": plan["equipment_inputs"]["a"]["artifact_sets"][0]["count"] = 2
    if fault == "wrong_set": plan["equipment_inputs"]["a"]["artifact_sets"][0]["set_id"] = 15008
    if fault == "wrong_holder": requirements[0]["provider_character_id"] = "other"
    if fault == "wrong_level": requirements[0]["effect"]["source_level"] = 0
    if fault == "wrong_affix": requirements[0]["effect"]["affix_id"] = 2150070
    model, records, _, issues = compose(plan, template, material, requirements)
    assert model is None and issues
    assert records[0]["rules"] == records[0]["initial_rules"] == []


def test_review_no_target_is_explicit_and_cannot_hide_an_invalid_elemental_model():
    plan, _, binding, material = inputs(True)
    qualification = material["compiler_result"]["execution_qualification"]
    assert qualify_scene(plan, binding, qualification)["target_id"] is None
    plan["mechanics_model"]["elemental_model"] = {"ruleset": "invented"}
    with pytest.raises(ValueError):
        qualify_scene(plan, binding, qualification)


def test_review_three_actual_sampling_modes_and_offline_context_no_db(monkeypatch):
    import sqlite3

    plan, template, _, requirements, model, records = accepted()
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("offline replay opened DB"))
    output = execute_mechanics(template, plan["rotation_model"], model)
    # Independent arithmetic from the source scalar, not an engine-generated oracle.
    after = (100 + 100 * 0.20000000298023224) * 0.5 * 0.9
    expected = {"live": after, "old-snapshot": 45, "capture-snapshot": after}
    assert {h["id"]: h["expected"] for h in output["result"]["hits"]} == pytest.approx(expected)
    before = copy.deepcopy((records, plan, template))
    context = replay_context(plan, template, requirements, records)
    assert _replay(records, context) == model
    assert before == (records, plan, template)


@pytest.mark.parametrize("field", ["equipment_requirements", "equipment_evidence", "action_resource_compilations"])
def test_review_v2_replay_requires_explicit_complete_context_shape(field):
    plan, template, _, requirements, _, records = accepted()
    context = replay_context(plan, template, requirements, records)
    del context[field]
    with pytest.raises(ValueError):
        _replay(records, context)


def test_review_old_v1_complete_projection_values_match_preimplementation_baseline():
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/engine-continuation/ec22-research/runtime-v1-baseline.json"
    baseline = json.loads(path.read_text("utf-8"))["outputs"]
    for kind in ("flat", "swirl"):
        descriptor, args = qualified_case(kind)
        value = compile_mechanism_descriptor(descriptor, **args)
        assert fingerprint(value) == fingerprint(baseline[kind])


@pytest.mark.parametrize("cooldown", [None, {"group": "e", "duration": 0, "charges": 1,
    "start": "action_start", "recovery": "sequential"}])
def test_review_successful_e_cannot_mean_an_unchecked_or_zero_cooldown(cooldown):
    plan, template, binding, material = inputs()
    plan["rotation_model"]["abilities"][0]["cooldown"] = copy.deepcopy(cooldown)
    with pytest.raises(ValueError, match="冷却"):
        graph(plan, template, binding, material)


def test_review_explicit_e_cooldown_is_checked_before_a_second_cast_activation():
    from tests.test_mechanics_execution import add, hit, rule

    plan, template, binding, material = inputs()
    rotation, model = plan["rotation_model"], plan["mechanics_model"]
    rotation["abilities"][0]["cooldown"] = {"group": "e", "duration": 15, "charges": 1,
        "start": "action_start", "recovery": "sequential"}
    rotation["initial_cooldowns"] = [{"character": "a", "group": "e", "ready_in": []}]
    second = copy.deepcopy(rotation["actions"][0])
    second.update(id="too-soon", start=1, occupation_end=1.1)
    rotation["actions"].append(second)
    assert len(graph(plan, template, binding, material)["parents"]) == 2
    template["hits"] = [hit("first", .2), hit("second", 1.2)]
    model["hit_events"] = [{"hit_id": "first", "action_id": "cast", "order": 10},
                           {"hit_id": "second", "action_id": "too-soon", "order": 10}]
    add(model, rule(trigger={"event": "action_start", "phase": "pre", "action_ids": ["cast", "too-soon"]}))
    result = execute_mechanics(template, rotation, model)
    assert result["status"] == "failed"
    assert any(r["code"] == "cooldown_unavailable" and r["event_id"] == "too-soon"
               for r in result["rotation_check"]["issues"])
    assert len([r for r in result["mechanics_trace"] if r["type"] == "activation"]) == 1
