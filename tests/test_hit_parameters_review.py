"""Independent native-plan boundaries for explicit scalar source delivery."""
from __future__ import annotations

import copy
import json

import pytest
from test_elemental_app import native
from test_runtime_action_facts import action_db, fake_repo, stub_fetcher  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder

# ruff: noqa: F811


def _binding(db, hit_id="h0", **selection):
    selected = {"character_id": 90000001, "slot": "normal", "constellation": 0, **selection}
    result = query_runtime(db, {"action": "action_parameter", "param_index": 0, **selected})
    return {"hit_id": hit_id, "version_line": "release", "selection": selected,
            "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier",
            "status": "confirmed", "source": "独立夹具：此参数显式声明为单一攻击倍率",
            "expected_snapshot_id": result["snapshot"]["snapshot_id"],
            "expected_node_sha256": result["source"]["node_sha256"] if result["source"] else None}


def _plan(db):
    plan = native()
    first = plan["manual_template"]["hits"][0]
    assert first["id"] == "h0"
    first.pop("multiplier")
    first["scaling_stat"] = "atk"
    plan["hit_parameter_sources"] = [_binding(db)]
    return plan


def _second(plan):
    hit = plan["manual_template"]["hits"][1]
    hit.pop("multiplier")
    hit["scaling_stat"] = "atk"
    binding = copy.deepcopy(plan["hit_parameter_sources"][0])
    binding["hit_id"] = hit["id"]
    plan["hit_parameter_sources"].append(binding)
    return binding


def test_fresh_calculation_preserves_native_placeholder_and_exact_proud_source(action_db):
    plan = _plan(action_db)
    before = copy.deepcopy(plan)
    output = run_plan_calc(plan, action_db)
    assert plan == before
    assert "multiplier" not in output["plan"]["manual_template"]["hits"][0]
    assert output["report"]["mechanics_input_template"]["hits"][0]["multiplier"] == .5
    assert output["result"]["hits"][0]["expected"] == 22.5
    receipt = output["report"]["hit_parameter_compilations"][0]
    assert receipt["gameplay_interpretation"] == "declared_not_verified"
    assert receipt["source"]["definition"]["exact_key"] == {"proudSkillId": 910020}
    assert receipt["source"]["parameter_pointer"] == "/0/paramList/0"
    assert receipt["operation_execution"] == "not_evaluated"
    assert receipt["action_dispatch"]["status"] == "pending"


def test_saved_native_and_forged_cached_result_are_recompiled(action_db):
    plan = run_plan_calc(_plan(action_db), action_db)["plan"]
    plan["resolved_template"]["hits"][0]["multiplier"] = 999999
    plan["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    plan["resolution"] = {"state": "ready", "hit_parameter_compilations": [{"status": "ready", "multiplier": 999999}]}
    path = action_db.parent / "parameter-native.json"
    path.write_text(json.dumps(plan), encoding="utf8")
    reread = read_plan(path)["plan"]
    result = run_plan_calc(reread, action_db)
    assert result["result"]["hits"][0]["expected"] == 22.5
    assert "multiplier" not in reread["manual_template"]["hits"][0]
    assert result["report"]["hit_parameter_compilations"][0]["multiplier"] == .5


@pytest.mark.parametrize("value", [0, None, .5])
def test_explicit_multiplier_never_coexists_even_zero_or_null(action_db, value):
    plan = _plan(action_db)
    plan["manual_template"]["hits"][0]["multiplier"] = value
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, action_db)


@pytest.mark.parametrize("change", ["unknown_hit", "nonclassic", "missing_scaling", "conflicting_scaling", "duplicate"])
def test_invalid_target_or_interpretation_cannot_compile(action_db, change):
    plan = _plan(action_db)
    hit, binding = plan["manual_template"]["hits"][0], plan["hit_parameter_sources"][0]
    if change == "unknown_hit":
        binding["hit_id"] = "not-a-manual-hit"
    elif change == "nonclassic":
        hit["damage_system"] = "transformative"
    elif change == "missing_scaling":
        hit.pop("scaling_stat")
    elif change == "conflicting_scaling":
        hit["scaling_stat"] = "hp"
    else:
        plan["hit_parameter_sources"].append(copy.deepcopy(binding))
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, action_db)


@pytest.mark.parametrize("field", ["purpose", "status", "scaling_stat", "source", "expected_snapshot_id", "expected_node_sha256"])
def test_required_semantic_and_confirmation_fields_cannot_be_omitted(action_db, field):
    plan = _plan(action_db)
    plan["hit_parameter_sources"][0].pop(field)
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_whitespace_interpretation_basis_is_rejected(action_db):
    plan = _plan(action_db)
    plan["hit_parameter_sources"][0]["source"] = " \t "
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("change", ["character", "line", "constellation"])
def test_one_actor_cannot_mix_incompatible_database_identities(action_db, change):
    plan = _plan(action_db)
    binding = _second(plan)
    if change == "character":
        binding["selection"]["character_id"] += 1
    elif change == "line":
        binding["version_line"] = "beta"
    else:
        binding["selection"]["constellation"] = 1
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, action_db)
    assert error.value.reason == "character_identity"


@pytest.mark.parametrize("ref", [{"db_character_id": 90000002, "version_line": "release"},
                                  {"db_character_id": 90000001, "version_line": "beta"}])
def test_equipment_reference_is_an_independent_identity_constraint(action_db, ref):
    plan = _plan(action_db)
    actor = plan["manual_template"]["hits"][0]["character"]
    plan["equipment_inputs"][actor] = {"mode": "panel", "panel_policy": "static_included",
        "character_ref": ref, "weapon": None, "artifact_sets": [],
        "source_refs": [{"kind": "independent-review"}], "data_version": None}
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, action_db)
    assert error.value.reason == "character_identity"


def test_alias_slot_and_skill_id_cannot_hide_mixed_effective_levels(action_db):
    plan = _plan(action_db)
    second = _second(plan)
    second["selection"].pop("slot")
    second["selection"].update(skill_id=90011, talent_level=13)
    second.update({k: v for k, v in _binding(action_db, talent_level=13).items()
                   if k in ("expected_snapshot_id", "expected_node_sha256")})
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, action_db)
    assert error.value.reason == "parameter_level"


@pytest.mark.parametrize("field", ["expected_snapshot_id", "expected_node_sha256"])
def test_stale_confirmation_blocks_even_a_previously_ready_native_cache(action_db, field):
    plan = run_plan_calc(_plan(action_db), action_db)["plan"]
    plan["hit_parameter_sources"][0][field] = "stale"
    preview = compose_equipment_plan(plan, action_db)
    assert preview["state"] == "pending" and preview["plan"]["resolved_template"] is None
    assert any(i["code"] == "source_changed" for i in preview["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, action_db)


def test_talent_node_confirmation_does_not_confirm_proud_parameter(action_db):
    plan = _plan(action_db)
    facts = query_runtime(action_db, {"action": "action_facts", "character_id": 90000001, "slot": "normal"})
    wrong_node = facts["talents"][0]["definition"]["node_sha256"]
    assert wrong_node != plan["hit_parameter_sources"][0]["expected_node_sha256"]
    plan["hit_parameter_sources"][0]["expected_node_sha256"] = wrong_node
    preview = compose_equipment_plan(plan, action_db)
    assert preview["state"] == "pending"
    assert any(i["code"] == "source_changed" for i in preview["missing_inputs"])


def test_source_value_update_requires_review_then_changes_only_execution_copy(action_db, fake_repo):
    plan = _plan(action_db)
    old = run_plan_calc(plan, action_db)
    path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[0]["paramList"][0] = .75
    path.write_text(json.dumps(rows), encoding="utf8")
    fixture_builder.build_database(action_db.parent)
    preview = compose_equipment_plan(old["plan"], action_db)
    assert preview["state"] == "pending"
    plan["hit_parameter_sources"][0].update(preview["hit_parameter_compilations"][0]["required_confirmation"])
    new = run_plan_calc(plan, action_db)
    assert new["result"]["hits"][0]["expected"] == 33.75
    assert new["result_receipt"]["source_fingerprint"] != old["result_receipt"]["source_fingerprint"]
    assert "multiplier" not in new["plan"]["manual_template"]["hits"][0]


@pytest.mark.parametrize("status", ["pending", "assumed"])
def test_interpretation_status_is_not_erased_by_source_confirmation(action_db, status):
    plan = _plan(action_db)
    plan["hit_parameter_sources"][0]["status"] = status
    preview = compose_equipment_plan(plan, action_db)
    if status == "pending":
        assert preview["state"] == "pending" and preview["plan"]["resolved_template"] is None
    else:
        assert preview["state"] == "ready"
        assert any("假设" in str(item) for item in preview["assumptions"])
