"""Compound authoring produces ordinary input, never its own executable state."""
import copy

import pytest

from genshin_app.authoring_assistance import authoring_assistance
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.input_editing import prepare_input_edit
from tests.test_authoring_assistance import attack_case, confirm_synthetic_rule, request
from tests.test_calculation_overrides_execution import native_plan


def test_batch_matches_sequential_handwritten_execution(tmp_path):
    plan, payload = attack_case()
    first = {k: v for k, v in payload.items() if k != "mechanics_model"}
    second = {"mechanics_model": payload["mechanics_model"]}
    result = authoring_assistance(request(plan, "batch", {"steps": [
        {"id": "attack", "kind": "attack_bundle", "payload": first},
        {"id": "resource", "kind": "mechanics_fragment", "payload": second}]}), plan=plan)
    assert result["execution_validation"]["status"] == "structurally_valid"
    candidate = prepare_input_edit(plan, result["edit_request"])
    assert candidate["effect_bindings"][0]["coverage"]["state"] == "unreviewed"
    actual = run_plan_calc(confirm_synthetic_rule(candidate, tmp_path / "absent.db"), tmp_path / "absent.db")
    assert [r["expected"] for r in actual["result"]["hits"]] == pytest.approx([67.5, 90, 45, 90])
    assert len(result["edit_request"]["operations"]) < 5
    assert result["plan_modified"] is False


def test_late_batch_failure_does_not_commit_earlier_fragment():
    plan, payload = attack_case()
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as caught:
        authoring_assistance(request(plan, "batch", {"steps": [
            {"id": "attack", "kind": "attack_bundle", "payload": payload},
            {"id": "bad", "kind": "action_move", "payload": {"action_id": "absent", "start": 1}}]}), plan=plan)
    assert caught.value.details["step_id"] == "bad"
    assert caught.value.details["writes"] is False
    assert plan == before


def test_action_move_shifts_only_explicit_time_relations():
    plan = native_plan()
    plan["rotation_model"]["actions"][0]["switch_allowed_at"] = .08
    plan["rotation_model"]["events"] = [
        {"id": "credit", "type": "energy_direct", "action_id": "cast", "t": .5},
        {"id": "untouched", "type": "energy_direct", "action_id": "cast", "t": .7}]
    before = copy.deepcopy(plan)
    output = authoring_assistance(request(plan, "action_move", {"action_id": "cast", "start": 1,
        "relative_bindings": [{"collection": "/rotation_model/events", "id": "credit", "fields": ["t"]}]}), plan=plan)
    prepared = prepare_input_edit(plan, output["edit_request"])
    action = prepared["rotation_model"]["actions"][0]
    assert action["start"] == 1 and action["occupation_end"] == 1.1 and action["switch_allowed_at"] == 1.08
    assert [r["t"] for r in prepared["rotation_model"]["events"]] == [1.5, .7]
    assert prepared["manual_template"]["hits"][0]["t"] == .2
    assert any(r["code"] == "absolute_time_not_moved" and r["path"].endswith("/1/t") for r in output["gaps"])
    assert plan == before


def test_action_move_rejects_double_shift_and_protected_results():
    plan = native_plan()
    with pytest.raises(PlanError):
        authoring_assistance(request(plan, "action_move", {"action_id": "cast", "start": 1,
            "relative_bindings": [{"collection": "/rotation_model/actions", "id": "cast", "fields": ["start"]}]}), plan=plan)
    plan["resolved_template"] = copy.deepcopy(plan["manual_template"])
    with pytest.raises(PlanError) as caught:
        authoring_assistance(request(plan, "action_move", {"action_id": "cast", "start": 1,
            "relative_bindings": [{"collection": "/resolved_template/hits", "id": "h0", "fields": ["t"]}]}), plan=plan)
    assert caught.value.reason == "agent_patch_forbidden"


def test_fragment_can_build_action_and_ability_without_new_runtime():
    plan = native_plan()
    action = copy.deepcopy(plan["rotation_model"]["actions"][0])
    action.update(id="second", start=1, occupation_end=1.1)
    ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ability["id"] = action["ability_id"] = "second.skill"
    output = authoring_assistance(request(plan, "mechanics_fragment", {
        "rotation_actions": [action], "rotation_abilities": [ability]}), plan=plan)
    assert output["counts"]["delta"]["actions"] == 1
    assert output["execution_validation"]["status"] == "structurally_valid"


@pytest.mark.parametrize("periodic", [False, True])
def test_batch_materialization_and_explicit_rebind_execute_generic_families(tmp_path, periodic):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from tests.test_agent_execution_materialization import (
        _runtime_base,
        _runtime_document,
        _runtime_topology,
    )

    source, _, rotation, mechanics = _runtime_base()
    plan = enable_mechanics(new_plan(source["manual_template"]))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    if periodic:
        plan["mechanics_model"].pop("elemental_model")
    document = _runtime_document(plan, _runtime_topology(periodic=periodic), periodic=periodic)
    output = authoring_assistance(request(plan, "batch", {"steps": [
        {"id": "family", "kind": "materialization_entries", "payload": {
            "sources": document["sources"], "entries": document["entries"]}},
        {"id": "links", "kind": "binding_refresh", "payload": {"materialization": True}}]}), plan=plan)
    actual = run_plan_calc(prepare_input_edit(plan, output["edit_request"]), tmp_path / "absent.db")
    assert actual["report"]["agent_execution_runtime_receipt"]["status"] == "complete"
    if periodic:
        assert [hit["t"] for hit in actual["result"]["hits"][:3]] == [.2, .7, 1.2]
    else:
        assert any(row["type"] == "energy_direct" for row in actual["result"]["rotation_check"]["event_log"])
