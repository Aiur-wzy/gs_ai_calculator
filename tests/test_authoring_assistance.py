"""Author helpers must yield ordinary editable inputs and real causal execution."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.authoring_assistance import authoring_assistance
from genshin_app.calculation_completion import calculation_completion
from genshin_app.cli import main
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_app.input_editing import prepare_input_edit
from tests.test_skill_native_causal_example import draft


def request(plan, kind, payload=None, **extra):
    return {"kind": kind, "version": 1, "request_id": "author.example",
            "expected_revision": plan["revision"], "payload": payload or {}, **extra}


def test_catalog_is_available_on_empty_whiteboard_and_uses_real_schema_pointers():
    from genshin_app.equipment import plan_capabilities
    from genshin_app.whiteboard import new_whiteboard

    board = new_whiteboard()
    response = authoring_assistance(request(board, "catalog"), plan=board)
    assert response["edit_request"] is None
    assert response["plan_modified"] is False
    schema = plan_capabilities()
    for recipe in response["handler_matrix"].values():
        for row in recipe.get("payload_schema_refs", {}).values():
            value = schema
            for segment in row["pointer"].split("/")[1:]:
                value = value[int(segment)] if isinstance(value, list) else value[segment]
            assert isinstance(value, dict)
    with pytest.raises(PlanError) as wrong_representation:
        authoring_assistance(request(board, "attack_bundle"), plan=board)
    assert wrong_representation.value.reason == "authoring_native_plan_required"


@pytest.mark.parametrize("payload", [
    {"mechanics_model": []}, {"mechanics_model": {"rules": [42]}},
    {"rotation_events": "wrong"}, {"calculated_result": {"total_damage": 123}},
])
def test_invalid_fragments_are_actionable_and_never_mutate_input(payload):
    plan = draft()
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as invalid:
        authoring_assistance(request(plan, "mechanics_fragment", payload), plan=plan)
    assert invalid.value.reason == "authoring_fragment_invalid"
    assert invalid.value.details
    assert before == plan


@pytest.mark.parametrize("field,value", [("request_id", "../../escape"),
                                         ("expected_input_fingerprint", "z" * 64)])
def test_public_schema_stays_dsh_compatible_but_service_validates_identity(field, value):
    from genshin_app.authoring_assistance import authoring_request_schema
    assert '"pattern"' not in json.dumps(authoring_request_schema())
    plan = draft()
    with pytest.raises(PlanError) as invalid:
        authoring_assistance(request(plan, "catalog", **{field: value}), plan=plan)
    assert invalid.value.reason == "authoring_request_invalid"
    assert invalid.value.details["path"] == f"request.{field}"
    assert invalid.value.details["expected"]


def attack_case():
    full = draft()
    plan = copy.deepcopy(full)
    plan["manual_template"]["hits"] = [plan["manual_template"]["hits"][1]]
    model = plan["mechanics_model"]
    model["hit_patterns"] = []
    model["rules"] = []
    model["initial_state"]["rules"] = []
    model["resource_ownership"] = []
    model["elemental_model"]["profiles"] = [model["elemental_model"]["profiles"][1]]
    plan["effect_bindings"] = []
    payload = {"prototype_hit": full["manual_template"]["hits"][0],
               "hit_pattern": full["mechanics_model"]["hit_patterns"][0],
               "application_profile": full["mechanics_model"]["elemental_model"]["profiles"][0],
               "mechanics_model": {key: full["mechanics_model"][key]
                                   for key in ("rules", "initial_state", "resource_ownership")}}
    return plan, payload


def confirm_synthetic_rule(plan, db):
    report = compose_equipment_plan(plan, db)
    assert len(report["effect_requirements"]) == 1
    requirement = report["effect_requirements"][0]
    binding = plan["effect_bindings"][0]
    assert binding["binding_id"] == requirement["binding_id"]
    binding["source_fingerprint"] = requirement["source_fingerprint"]
    binding["coverage"] = {"state": "complete", "reviewed_by": "synthetic fixture author",
                           "note": "Known fictional trigger and two particles, no game fact approval",
                           "source_fingerprint": requirement["source_fingerprint"]}
    return plan


def test_combined_attack_application_buff_particles_matches_handwritten_example(tmp_path):
    plan, payload = attack_case()
    before = copy.deepcopy(plan)
    response = authoring_assistance(request(plan, "attack_bundle", payload), plan=plan)
    assert plan == before
    assert response["execution_validation"]["status"] == "structurally_valid", response["execution_validation"]
    assert response["counts"]["delta"]["hit_patterns"] == 1
    prepared = prepare_input_edit(plan, response["edit_request"])
    assert prepared["effect_bindings"][0]["coverage"]["state"] == "unreviewed"
    assert prepared["mechanics_model"]["rules"][0]["status"] == "assumed"
    prepared = json.loads(json.dumps(prepared))  # Export/reimport has no hidden helper state.
    out = run_plan_calc(confirm_synthetic_rule(prepared, tmp_path / "absent.db"), tmp_path / "absent.db")
    assert calculation_completion(out)["task_admission"] == "accepted"
    assert [row["expected"] for row in out["result"]["hits"]] == pytest.approx([67.5, 90, 45, 90])
    # It remains ordinary input: changing travel time through the existing editor changes execution.
    modified = prepare_input_edit(prepared, {"operations": [{"op": "replace",
        "path": "/mechanics_model/rules/0/resources/0/travel_time", "value": 1}]})
    changed = run_plan_calc(confirm_synthetic_rule(modified, tmp_path / "absent.db"), tmp_path / "absent.db")
    assert calculation_completion(changed)["task_admission"] != "accepted"


def test_generated_ids_application_binding_and_stale_preview_are_explicit():
    plan, payload = attack_case()
    del payload["prototype_hit"]["id"]
    del payload["hit_pattern"]["prototype_hit_id"]
    del payload["application_profile"]["id"]
    del payload["hit_pattern"]["application"]["profile_id"]
    response = authoring_assistance(request(plan, "attack_bundle", payload), plan=plan)
    candidate = prepare_input_edit(plan, response["edit_request"])
    pattern = candidate["mechanics_model"]["hit_patterns"][0]
    assert pattern["prototype_hit_id"] == "author.example.prototype"
    assert pattern["application"]["profile_id"] == "author.example.application"
    assert candidate["mechanics_model"]["rules"][0]["trigger"]["event_ids"] == ["skill.ticks:1"]
    changed = copy.deepcopy(plan)
    changed["manual_template"]["characters"][0]["stats"]["atk"] += 1
    with pytest.raises(PlanError, match="内容已变化|输入已变化"):
        prepare_input_edit(changed, response["edit_request"])
    with pytest.raises(PlanError) as stale:
        authoring_assistance(request(plan, "catalog", expected_revision=plan["revision"] + 1), plan=plan)
    assert stale.value.reason == "agent_stale_revision"


def test_helper_does_not_overwrite_conflicting_ids_or_guess_missing_elemental_target():
    plan, payload = attack_case()
    plan["manual_template"]["hits"].append(copy.deepcopy(payload["prototype_hit"]))
    payload["prototype_hit"]["multiplier"] = 50
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as collision:
        authoring_assistance(request(plan, "attack_bundle", payload), plan=plan)
    assert collision.value.reason == "authoring_identity_conflict"
    assert plan == before
    plan, payload = attack_case()
    plan["mechanics_model"].pop("elemental_model")
    with pytest.raises(PlanError) as missing:
        authoring_assistance(request(plan, "attack_bundle", payload), plan=plan)
    assert missing.value.reason == "authoring_elemental_context_missing"
    assert missing.value.details["manual_equivalent"]


def test_binding_refresh_only_updates_hashes_and_refuses_stale_report(tmp_path):
    plan = draft()
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    # The caller uses the actual composed candidate/report; this is not free-form approval text.
    plan = report["plan"]
    original = copy.deepcopy(plan["effect_bindings"][0]["coverage"])
    response = authoring_assistance(request(plan, "binding_refresh", {"binding_ids": ["teaching"]}),
                                    plan=plan, composition_report=report)
    candidate = prepare_input_edit(plan, response["edit_request"])
    coverage = candidate["effect_bindings"][0]["coverage"]
    assert any(row["field"] == "source_fingerprint" and row["before"] is None
               and row["after"] == report["effect_requirements"][0]["source_fingerprint"]
               for row in response["binding_changes"])
    assert coverage["state"] == original["state"] == "unreviewed"
    assert coverage["reviewed_by"] == original["reviewed_by"] is None
    assert candidate["mechanics_model"]["rules"][0]["status"] == "assumed"
    assert coverage["source_fingerprint"] == report["effect_requirements"][0]["source_fingerprint"]
    changed = copy.deepcopy(plan)
    changed["manual_template"]["characters"][0]["stats"]["atk"] = 101
    with pytest.raises(PlanError) as stale:
        authoring_assistance(request(changed, "binding_refresh", {"binding_ids": ["teaching"]}),
                             plan=changed, composition_report=report)
    assert stale.value.reason == "authoring_composition_stale"


def test_refresh_cannot_promote_old_approval_onto_changed_source(tmp_path):
    plan = confirm_synthetic_rule(draft(), tmp_path / "absent.db")
    plan["mechanics_model"]["rules"][0]["source"] = "Changed fictional fact; not reviewed"
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    candidate = report["plan"]
    # Compose can already invalidate a review; either way the helper must not restore it.
    candidate["effect_bindings"][0]["coverage"].update(state="complete", reviewed_by="previous review",
                                                     source_fingerprint="a" * 64)
    report["plan"] = copy.deepcopy(candidate)
    response = authoring_assistance(request(candidate, "binding_refresh", {"binding_ids": ["teaching"]}),
                                    plan=candidate, composition_report=report)
    refreshed = prepare_input_edit(candidate, response["edit_request"])
    assert refreshed["effect_bindings"][0]["coverage"]["state"] == "unreviewed"
    assert refreshed["mechanics_model"]["rules"][0]["status"] == "assumed"


@pytest.mark.parametrize("periodic", [False, True])
def test_materialization_actual_finite_and_periodic_handlers_execute_without_helper_state(tmp_path, periodic):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from tests.test_agent_execution_materialization import (
        _runtime_base,
        _runtime_document,
        _runtime_topology,
    )

    source, _, rotation, mechanics = _runtime_base()
    plan = enable_mechanics(new_plan(source["manual_template"]))
    plan["rotation_model"], plan["mechanics_model"] = rotation, mechanics
    if periodic:
        plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=periodic)
    document = _runtime_document(plan, topology, periodic=periodic)
    response = authoring_assistance(request(plan, "materialization_entries", {
        "sources": document["sources"], "entries": document["entries"]}), plan=plan)
    prepared = prepare_input_edit(plan, response["edit_request"])
    assert prepared["extensions"]["agent_execution_materialization"]["sources"] == document["sources"]
    out = run_plan_calc(json.loads(json.dumps(prepared)), tmp_path / "absent.db")
    assert out["report"]["agent_execution_runtime_receipt"]["status"] == "complete"
    hits = out["result"]["hits"]
    assert hits
    if periodic:
        assert [h["t"] for h in hits[:3]] == [.2, .7, 1.2]
    else:
        assert any(r["type"] == "energy_direct" for r in out["result"]["rotation_check"]["event_log"])


def test_dynamic_attribute_fragment_reuses_current_schema_and_adoption(tmp_path):
    from genshin_app.equipment.attributes import prepare_attribute_review
    from tests.test_attribute_app_review import add_reader
    from tests.test_attribute_app_sources import attribute_plan
    from tests.test_plan_mechanics import review

    plan = attribute_plan()
    add_reader(plan)
    attribute_model = plan["mechanics_model"].pop("attribute_model")
    response = authoring_assistance(request(plan, "mechanics_fragment", {
        "mechanics_model": {"attribute_model": attribute_model}}), plan=plan)
    prepared = prepare_input_edit(plan, response["edit_request"])
    assert prepared["mechanics_model"]["attribute_model"] == attribute_model
    db = tmp_path / "absent.db"
    review(prepared, db)
    adopted = prepare_attribute_review(prepared, "Synthetic attribute basis explicitly verified", db)
    assert adopted["state"] == "ready", adopted["missing_inputs"]
    result = run_plan_calc(adopted["plan"], db)
    assert result["result"]["summary"]["total_damage"] > 0
    assert result["result"]["attribute_ledger"]


def test_special_replacement_uses_real_direct_stellar_handler_and_removes_exact_ordinary_hit():
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_direct_stellar_damage_contract import direct_stellar_profile
    from tests.test_elemental_multiaura_v4 import aura
    from tests.test_special_reaction_mechanics_projection import (
        _application_fixture,
        _enable,
    )

    template, rotation, mechanics = _application_fixture("electro", initial=[aura("cryo", .8, "a")], times=(.2, 1.0))
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    _enable(mechanics, "stellar_superconduct")
    template["hits"][1]["element"] = "cryo"
    mechanics["hit_events"][1]["application"] = {"profile_id": "cryo", "target_id": "enemy", "landed": True,
                                                  "area_id": "battlefield.primary"}
    ordinary = copy.deepcopy(template["hits"][1])
    ordinary.update(id="ordinary.component", t=4.2)
    template["hits"].append(ordinary)
    mechanics["hit_events"].append({"hit_id": ordinary["id"], "action_id": "cast", "order": 20,
        "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True}})
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    profile = direct_stellar_profile(character_id="a", ability_id="e")
    event = {"id": "direct-replacement", "kind": "direct_stellar_damage", "action_id": "cast",
             "target_id": "enemy", "area_id": "battlefield.primary", "t": 4.2, "order": 20,
             "ability_profile": profile, "status": "assumed", "source": "Synthetic explicit replacement"}
    response = authoring_assistance(request(plan, "special_damage", {
        "special_reaction_events": [event], "remove_hit_ids": [ordinary["id"]]}), plan=plan)
    prepared = prepare_input_edit(plan, response["edit_request"])
    assert ordinary["id"] not in [r["id"] for r in prepared["manual_template"]["hits"]]
    assert prepared["mechanics_model"]["special_reaction_events"][0]["ability_profile"] == profile
    output = execute_mechanics(prepared["manual_template"], prepared["rotation_model"], prepared["mechanics_model"])
    assert output["status"] in ("passed", "unproven"), output["issues"]
    assert len([h for h in output["result"]["hits"] if h["damage_system"] == "stellar"]) == 1
    assert not any(h["id"] == ordinary["id"] for h in output["result"]["hits"])


def test_cli_request_is_offline_and_output_is_an_existing_edit_request(tmp_path, capsys):
    plan, payload = attack_case()
    plan_path, request_path, response_path = [tmp_path / name for name in ("plan.json", "request.json", "response.json")]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    request_path.write_text(json.dumps(request(plan, "attack_bundle", payload)), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path / "no-db"), "equipment", "materialization", "--plan", str(plan_path),
                 "--request", str(request_path), "--output", str(response_path)]) == 0
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["database_access"] is False and response["plan_modified"] is False
    assert json.loads(plan_path.read_text(encoding="utf-8")) == plan
    edit_path = tmp_path / "edit.json"
    edit_path.write_text(json.dumps(response["edit_request"]), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path / "no-db"), "equipment", "edit", "--plan", str(plan_path),
                 "--request", str(edit_path), "--output", str(tmp_path / "edited.json")]) == 0
    edited = json.loads((tmp_path / "edited.json").read_text(encoding="utf-8"))
    assert edited["revision"] == plan["revision"] + 1
    assert fingerprint(input_view(edited)) != fingerprint(input_view(plan))
    capsys.readouterr()


def test_current_target_status_and_explicit_target_survive_public_app_composition(tmp_path):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from tests.test_r16_target_status import event, inputs

    template, rotation, model = inputs()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    model.pop("elemental_model")
    for row in model["hit_events"]:
        row.pop("application")
        row["target_id"] = "enemy"
    status = event("declared.status", .1, duration=.3)
    rule = {key: value for key, value in event("reaction.status", 0).items()
            if key not in ("t", "order", "repeat")}
    rule["reaction_types"] = ["melt"]
    conditional = model.pop("fixed_effect_sources")
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    response = authoring_assistance(request(plan, "mechanics_fragment", {
        "mechanics_model": {"target_status_events": [status], "target_status_rules": [rule],
                            "fixed_effect_sources": conditional}}), plan=plan)
    candidate = prepare_input_edit(plan, response["edit_request"])
    report = compose_equipment_plan(candidate, tmp_path / "absent.db")
    assert report["plan"]["mechanics_model"]["target_status_events"] == [status]
    assert report["plan"]["mechanics_model"]["target_status_rules"] == [rule]
    out = run_plan_calc(confirm_synthetic_rule(candidate, tmp_path / "absent.db"), tmp_path / "absent.db")
    assert [row["automatic_value"] for row in out["result"]["target_condition_trace"]] == [True, True, False]


def test_current_cooldown_rebase_is_an_ordinary_rotation_event(tmp_path):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from tests.test_r16_cooldown_and_identity import scenario

    template, rotation = scenario("restore_rebase")
    template["characters"][0]["stats"]["energy_recharge"] = 1
    resource = rotation["events"].pop()
    plan = enable_mechanics(new_plan(template))
    plan["rotation_model"] = rotation
    response = authoring_assistance(request(plan, "mechanics_fragment", {
        "rotation_events": [resource]}), plan=plan)
    candidate = prepare_input_edit(plan, response["edit_request"])
    report = compose_equipment_plan(candidate, tmp_path / "absent.db")
    assert report["plan"]["rotation_model"]["events"][0] == resource
    from genshin_engine.rotation import check_rotation
    checked = check_rotation(candidate["manual_template"], candidate["rotation_model"])
    assert next(row["ready_at"] for row in checked["event_log"] if row["id"] == "restore") == [16.2]


def test_derived_event_selector_remains_author_input_through_app(tmp_path):
    db = tmp_path / "absent.db"
    baseline = run_plan_calc(confirm_synthetic_rule(draft(), db), db)
    ref = baseline["plan"]["resolved_template"]["hits"][1]["extensions"]["execution_ref"]
    plan = draft()
    rule = plan["mechanics_model"]["rules"].pop()
    rule["effects"][0]["filter"] = {"event": "hit", "phase": "pre", "event_refs": [ref]}
    plan["effect_bindings"] = []
    response = authoring_assistance(request(plan, "mechanics_fragment", {
        "mechanics_model": {"rules": [rule]}}), plan=plan)
    candidate = prepare_input_edit(plan, response["edit_request"])
    result = run_plan_calc(confirm_synthetic_rule(candidate, db), db)
    assert result["plan"]["mechanics_model"]["rules"][0]["effects"][0]["filter"]["event_refs"] == [ref]
    assert [h["expected"] for h in result["result"]["hits"]] == pytest.approx([67.5, 90, 45, 90])
