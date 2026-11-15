"""Source-bound action resources use explicit existing actions and spend events."""
import copy
import json

import pytest

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.action_resources import compile_action_resources
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import plan_capabilities
from genshin_app.rotation_check import check_document
from tests.test_mechanics_execution import EVIDENCE, fixture
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


def resource_plan():
    template, rotation, mechanics = fixture()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan["rotation_model"], plan["mechanics_model"] = rotation, mechanics
    rotation["characters"][0].update(element="geo", energy_capacity=60, initial_energy=60)
    rotation["abilities"][0].update(id="q", action_type_id="action.elemental_burst", variant="普通爆发",
        cooldown={"group": "q", "start": "action_start", "recovery": "sequential"}, energy={})
    rotation["actions"][0].update(ability_id="q", repeat={"mode": "once"})
    rotation["initial_cooldowns"] = [{"character": "a", "group": "q", "ready_in": []}]
    rotation["events"] = [{"id": "spend", "type": "energy_spend", "action_id": "cast", "character": "a",
                            "t": 0, "order": 1, "repeat": {"mode": "once"}, **EVIDENCE}]
    plan["action_resource_sources"] = [{"ability_id": "q", "version_line": "release",
        "selection": {"character_id": 90000001, "slot": "burst", "constellation": 0},
        "purpose": "ordinary_fixed_burst_base_resources", "variant": "普通爆发", **EVIDENCE,
        "expected_snapshot_id": None, "expected_resource_fingerprint": None, "expected_scope_fingerprint": None}]
    return plan


def confirm(plan, db):
    report = compose_equipment_plan(plan, db)
    for binding, record in zip(plan["action_resource_sources"], report["action_resource_compilations"], strict=True):
        binding.update(record["required_confirmation"])
    return plan


def test_native_confirmation_calculation_and_save_keep_numbers_omitted(resource_db):
    plan = resource_plan()
    validate_plan(plan)
    original = copy.deepcopy(plan)
    pending = compose_equipment_plan(plan, resource_db)
    assert pending["state"] == "pending" and plan == original
    assert len(pending["action_resource_compilations"][0]["target_paths"]) == 6
    confirm(plan, resource_db)
    original = copy.deepcopy(plan)
    out = run_plan_calc(plan, resource_db)
    assert plan == original
    assert out["plan"]["rotation_model"] == original["rotation_model"]
    actual = out["report"]["compiled_rotation_model"]
    assert actual["abilities"][0]["cooldown"]["duration"] == 15
    assert actual["abilities"][0]["cooldown"]["charges"] == 1
    assert actual["abilities"][0]["energy"] == {"required": 60, "amount": 60}
    assert actual["events"][0]["amount"] == actual["events"][0]["required"] == 60
    assert out["result"]["summary"]["total_damage"] == 135
    assert out["result"]["rotation_check"]["resolved_rotation_model"]["abilities"][0]["energy"]["amount"] == 60
    assert out["report"]["compiled_rotation_model_fingerprint"] == fingerprint(actual)
    receipt = out["result_receipt"]["action_resources"]
    assert receipt["compilations_fingerprint"] == fingerprint(out["result"]["action_resource_compilations"])
    reopened = json.loads(json.dumps(out["plan"]))
    assert run_plan_calc(reopened, resource_db)["result"] == out["result"]
    assert check_document(plan, resource_db) == out["result"]["rotation_check"]


@pytest.mark.parametrize("place,key,value", [("cooldown", "duration", 0), ("cooldown", "charges", None),
    ("energy", "required", 60), ("energy", "amount", None), ("spend", "amount", 60), ("spend", "required", 0)])
def test_bound_numbers_cannot_be_delivered_twice(resource_db, place, key, value):
    plan = resource_plan()
    obj = plan["rotation_model"]["events"][0] if place == "spend" else plan["rotation_model"]["abilities"][0][place]
    obj[key] = value
    with pytest.raises(PlanError) as exc:
        compose_equipment_plan(plan, resource_db)
    assert exc.value.reason == "duplicate_delivery"


@pytest.mark.parametrize("change", [
    lambda p: p["action_resource_sources"][0]["selection"].update(constellation=1),
    lambda p: p["action_resource_sources"][0].update(purpose="unresolved_resource_mode"),
    lambda p: p["action_resource_sources"][0].update(status="pending"),
    lambda p: p["rotation_model"]["abilities"][0].update(action_type_id="action.elemental_skill"),
])
def test_unproven_modes_stay_pending_after_source_confirmation(resource_db, change):
    plan = resource_plan()
    change(plan)
    confirm(plan, resource_db)
    report = compose_equipment_plan(plan, resource_db)
    assert report["state"] == "pending"
    assert report["action_resource_compilations"][0]["status"] == "pending"
    assert "amount" not in report["compiled_rotation_model"]["events"][0]


def test_scope_changes_but_unrelated_panel_does_not(resource_db):
    plan = confirm(resource_plan(), resource_db)
    before = compose_equipment_plan(plan, resource_db)["action_resource_compilations"][0]["scope_fingerprint"]
    plan["manual_template"]["characters"][0]["stats"]["atk"] = 200
    report = compose_equipment_plan(plan, resource_db)
    assert report["state"] == "ready"
    assert report["action_resource_compilations"][0]["scope_fingerprint"] == before
    plan["rotation_model"]["abilities"][0]["cooldown"]["start"] = "occupation_end"
    report = compose_equipment_plan(plan, resource_db)
    assert report["state"] == "pending"
    assert report["action_resource_compilations"][0]["scope_fingerprint"] != before


def test_binding_diagnostic_distinguishes_material_and_current_scope(resource_db):
    plan = confirm(resource_plan(), resource_db)
    source = plan["action_resource_sources"][0]
    source["expected_scope_fingerprint"] = source["expected_resource_fingerprint"]
    report = compose_equipment_plan(plan, resource_db)
    row = report["action_resource_compilations"][0]
    issue = next(i for i in row["issues"] if i["code"] == "source_changed")
    assert issue["matched_fields"] == ["expected_snapshot_id", "expected_resource_fingerprint"]
    assert issue["differences"] == [{"field": "expected_scope_fingerprint",
        "actual": source["expected_resource_fingerprint"], "expected": row["scope_fingerprint"],
        "input_ref": "/action_resource_sources/0/expected_scope_fingerprint",
        "expected_ref": "/action_resource_compilations/0/required_confirmation/expected_scope_fingerprint"}]
    assert issue["user_action"] is None
    assert report["state"] == "pending"


def test_explicit_loadout_constellation_cannot_be_relabelled_to_use_auto_source():
    from genshin_app.equipment.action_resources import _identity_evidence
    plan = resource_plan()
    plan["equipment_inputs"]["a"] = {"character_ref": {"db_character_id": 90000001, "version_line": "release"},
                                       "progression": {"constellation": 6}}
    with pytest.raises(PlanError) as caught:
        _identity_evidence(plan, {"q": plan["rotation_model"]["abilities"][0]})
    assert caught.value.reason == "character_identity"
    assert caught.value.details["differences"] == [{"field": "constellation", "actual": 0, "expected": 6}]


def test_multiple_actions_query_once_and_preserve_explicit_modifiers(resource_db, monkeypatch):
    from genshin_db import runtime_action_resource
    plan = resource_plan()
    rotation = plan["rotation_model"]
    rotation["actions"].append({**copy.deepcopy(rotation["actions"][0]), "id": "cast2", "start": 5, "occupation_end": 5.1})
    rotation["events"].append({**copy.deepcopy(rotation["events"][0]), "id": "spend2", "action_id": "cast2", "t": 5})
    rotation["events"].extend([
        {"id": "refund", "type": "energy_direct", "amounts": {"a": 60}, "t": 1, "order": 2, "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "reset", "type": "cooldown_change", "character": "a", "group": "q", "operation": "reset", "t": 1, "order": 3, "repeat": {"mode": "once"}, **EVIDENCE}])
    confirm(plan, resource_db)
    query, calls = runtime_action_resource.get_runtime_action_resource_profile, []

    def counted(*args, **kwargs):
        calls.append(1)
        return query(*args, **kwargs)

    monkeypatch.setattr(runtime_action_resource, "get_runtime_action_resource_profile", counted)
    out = run_plan_calc(plan, resource_db)
    assert calls == [1]
    record = out["result"]["action_resource_compilations"][0]
    assert record["explicit_modifiers"] == [{"id": "refund", "type": "energy_direct"}, {"id": "reset", "type": "cooldown_change"}]
    assert len(record["action_spend_links"]) == 2
    assert out["report"]["compiled_rotation_model"]["events"][2:] == rotation["events"][2:]


def test_typed_identity_conflict_and_alias_split_rejected(resource_db):
    plan = resource_plan()
    plan["hit_parameter_sources"] = [{"hit_id": "h0", "selection": {"character_id": 90000001, "slot": "normal", "constellation": 1},
        "version_line": "release", "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", **EVIDENCE,
        "expected_snapshot_id": None, "expected_node_sha256": None}]
    with pytest.raises(PlanError) as exc:
        compile_action_resources(plan, resource_db, lambda *a: None)
    assert exc.value.reason == "character_identity"
    plan = resource_plan()
    alias = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    alias["id"], alias["cooldown"]["group"] = "alias", "different"
    plan["rotation_model"]["abilities"].append(alias)
    binding = copy.deepcopy(plan["action_resource_sources"][0])
    binding["ability_id"] = "alias"
    plan["action_resource_sources"].append(binding)
    with pytest.raises(PlanError) as exc:
        compile_action_resources(plan, resource_db, lambda *a: None)
    assert exc.value.reason == "resource_alias_conflict"


def test_no_bindings_keep_old_report_shape(resource_db):
    plan = resource_plan()
    del plan["action_resource_sources"]
    report = compose_equipment_plan(plan, resource_db)
    # No resource source is compiled. App-owned execution-policy metadata is
    # the only derivation and must not be written into the author's axis.
    expected = copy.deepcopy(plan["rotation_model"])
    expected["direct_energy_policy"] = "energy-direct-base-er-v1"
    assert report["compiled_rotation_model"] == expected
    assert report["plan"]["rotation_model"] == plan["rotation_model"]
    assert "action_resource_compilations" not in report
    assert plan_capabilities()["action_resource_source_contract"]["identity_coverage"] == "explicit_sources_only"


def test_equipment_burst_gate_consumes_fresh_compilation_without_second_query(resource_db, monkeypatch):
    from genshin_app.equipment.upstream import _burst_actions
    from genshin_db import queries

    plan = confirm(resource_plan(), resource_db)
    report = compose_equipment_plan(plan, resource_db)
    working = copy.deepcopy(plan)
    working["rotation_model"] = report["compiled_rotation_model"]
    working["equipment_inputs"] = {"a": {"character_ref": {"db_character_id": 90000001, "version_line": "release"}}}
    effect = {"binding_id": "artifact", "provider_character_id": "a", "upstream_recipe": {
        "ability_refs": [{"ability_id": "q", "skill_id": 90013}]}}
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: pytest.fail("bound resource queried curated skill twice"))
    ids, proof = _burst_actions(working, effect, resource_db, lambda *a, **k: pytest.fail(str(a)),
                               resource_compilations=report["action_resource_compilations"])
    assert ids == ["cast"] and proof["skills"] == []
    assert proof["action_resource_compilations"][0]["resource_fingerprint"] == report["action_resource_compilations"][0]["resource_fingerprint"]
