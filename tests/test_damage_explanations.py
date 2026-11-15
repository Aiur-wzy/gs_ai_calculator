"""Same-execution explanation, override and immutable inspection adversaries."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.calculation_review_batch import review_calculation_batch
from genshin_app.damage_explanation_reporting import (
    assemble_damage_explanations,
    make_explanation_receipt,
    validate_damage_explanations,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import run_plan_calc
from genshin_engine.core import calculate
from genshin_engine.damage_explanations import digest
from genshin_engine.mechanics import execute_mechanics
from tests.test_calculation_delivery import delivery_plan
from tests.test_calculation_review import _saved
from tests.test_engine_core import buff, hit, make_template
from tests.test_external_damage import confirm, external_plan
from tests.test_mechanics_execution import add, fixture, rule


def _event(result, index=0):
    key = result["hits"][index]["explanation_ref"]
    return result["damage_explanations"]["events"][key]


def _without_explanations(result):
    value = copy.deepcopy(result)
    value.pop("damage_explanations", None)
    value.pop("damage_explanation_fingerprint", None)
    for row in value["hits"]:
        row.pop("explanation_ref", None)
    return value


@pytest.mark.parametrize("field", ["description", "hit_name", "name"])
def test_legacy_event_identity_ignores_pure_display_edits_but_preserves_source_text(field):
    template = make_template()
    template["hits"] = [hit(.5, "a")]
    before = calculate(template)
    changed = copy.deepcopy(template)
    changed["hits"][0][field] = "新的中文展示文字"
    after = calculate(changed)
    assert before["hits"][0]["explanation_ref"] == after["hits"][0]["explanation_ref"]
    assert before["summary"] == after["summary"]
    assert {key: value for key, value in before["hits"][0].items() if key != "hit_name"} == {
        key: value for key, value in after["hits"][0].items() if key != "hit_name"}
    assert before["damage_explanations"]["panels"] == after["damage_explanations"]["panels"]
    assert before["damage_explanations"]["formulas"] == after["damage_explanations"]["formulas"]
    if field == "hit_name":
        source = after["damage_explanations"]["sources"][_event(after)["source_ref"]]
        assert source["hit_name"] == "新的中文展示文字"
        assert before["damage_explanations"]["sources"] != after["damage_explanations"]["sources"]


@pytest.mark.parametrize("changes", [{"multiplier": 3}, {"skill": "other.filter.key"}, {"t": .6}])
def test_legacy_event_identity_keeps_math_association_and_time_fields(changes):
    template = make_template()
    template["hits"] = [hit(.5, "a")]
    before = calculate(template)
    template["hits"][0].update(changes)
    assert before["hits"][0]["explanation_ref"] != calculate(template)["hits"][0]["explanation_ref"]


def test_legacy_duplicate_semantic_hits_keep_separate_occurrences_after_rename():
    template = make_template()
    template["hits"] = [hit(.5, "a", hit_name="第一条"), hit(.5, "a", hit_name="第二条")]
    before = calculate(template)
    for index, row in enumerate(template["hits"]):
        row["hit_name"] = f"改名{index}"
    after = calculate(template)
    refs = [row["explanation_ref"] for row in before["hits"]]
    assert len(set(refs)) == 2 and refs[1] == refs[0] + ":occurrence:1"
    assert refs == [row["explanation_ref"] for row in after["hits"]]
    assert before["summary"] == after["summary"]


@pytest.mark.parametrize("system,reaction", [
    ("classic", None), ("classic", {"type": "amplify", "factor": 2}),
    ("classic", {"type": "aggravate"}),
    ("transformative", {"type": "overload"}),
    ("lunar", {"type": "lunar_electro"}),
    ("stellar", {"type": "stellar_superconduct", "layers": 5}),
])
def test_capture_does_not_change_any_numeric_field_or_order(system, reaction):
    template = make_template()
    template["characters"][0]["stats"].update(em=123, crit_rate=.42, crit_dmg=1.3)
    template["hits"] = [hit(.5, "a", damage_system=system, count=2, result_weight=.3,
                            **({"reaction": reaction} if reaction else {}))]
    original = copy.deepcopy(template)
    plain = calculate(template, include_damage_explanations=False)
    explained = calculate(template)
    assert _without_explanations(explained) == plain
    assert template == original
    validate_damage_explanations(explained)
    assert _event(explained)["outcome"]["expected"] == explained["hits"][0]["expected"]


def test_classic_base_flat_and_aggravate_are_different_checkpoints():
    template = make_template()
    template["hits"] = [hit(.5, "a", 2, reaction={"type": "aggravate"})]
    template["buffs"] = [buff("add", [{"zone": "base_mult", "stat": "all", "value": 1.5},
                                      {"zone": "base_add", "stat": "all", "value": 300}])]
    result = calculate(template)
    event = _event(result)
    assert event["checkpoints"]["scaled_base"] == 6000
    assert event["checkpoints"]["base_after_flat"] == 6300
    assert event["checkpoints"]["base_after_aggravate"] > 6300
    assert event["normalization"]["value"] == 3.15
    assert "aggravate_flat" in event["normalization"]["excludes"]


@pytest.mark.parametrize("system", ["stellar", "lunar"])
def test_special_flat_is_outside_em_base_and_large_multiplier(system):
    template = make_template()
    template["hits"] = [hit(.5, "a", 2, damage_system=system,
                            reaction={"type": "stellar_superconduct" if system == "stellar" else "lunar_electro", "factor": 3})]
    template["buffs"] = [buff("special", [
        {"zone": system + "_base", "stat": "all", "value": .5},
        {"zone": system + "_mult", "stat": "all", "value": 2},
        {"zone": "reaction_flat", "stat": "all", "value": 300}])]
    result = calculate(template)
    event = _event(result)
    assert event["checkpoints"]["before_reaction_flat"] == 36000
    assert event["checkpoints"]["after_reaction_flat"] == 36300
    assert event["normalization"]["value"] == 18
    assert "reaction_flat" in event["normalization"]["excludes"]


def test_only_consumed_stats_and_raw_versus_clamped_crit_are_shown():
    template = make_template()
    template["hits"] = [hit(.5, "a")]
    template["buffs"] = [buff("crit", [{"zone": "crit_rate", "stat": "all", "value": 1.2}])]
    result = calculate(template)
    event = _event(result)
    panel = result["damage_explanations"]["panels"][event["panel_ref"]]
    assert "em" not in panel["inputs"] and "def" not in panel["baseline"]
    assert panel["inputs"]["crit_rate_raw"] == 1.2
    assert panel["inputs"]["crit_rate_effective"] == 1
    template["characters"][0]["stats"]["atk"] = 0
    assert _event(calculate(template))["normalization"]["reason"] == "zero_or_unavailable_reference"


def test_unrelated_panel_bonus_is_not_an_actual_consumed_modifier():
    template = make_template()
    template["hits"] = [hit(.5, "a")]
    template["buffs"] = [buff("unrelated", [{"zone": "stat_add", "stat": "def", "value": 500},
                                          {"zone": "em", "stat": "all", "value": 100}])]
    result = calculate(template)
    assert _event(result)["modifier_refs"] == []
    assert "base_atk" not in result["damage_explanations"]["panels"][_event(result)["panel_ref"]]["baseline"]


def test_percentage_panel_term_keeps_its_actual_base_and_numeric_contribution():
    template = make_template()
    template["characters"][0]["stats"]["base_atk"] = 1000
    template["hits"] = [hit(.5, "a")]
    template["buffs"] = [buff("attack", [{"zone": "stat_pct", "stat": "atk", "value": .25}])]
    result = calculate(template)
    event = _event(result)
    modifier = result["damage_explanations"]["modifiers"][event["modifier_refs"][0]]
    assert modifier["value"] == .25
    assert modifier["basis_value"] == 1000 and modifier["resolved_contribution"] == 250
    assert result["damage_explanations"]["panels"][event["panel_ref"]]["inputs"]["scaling"] == 2250


def _runtime_explained():
    template, rotation, model = fixture((.2, .3))
    template["characters"].append({"id": "b", "level": 90, "stats": {"atk": 0, "em": 500}})
    rotation["characters"].append({**rotation["characters"][0], "id": "b"})
    add(model, rule(owner="b", domain="provider", value={"kind": "stat", "subject": "owner",
        "stat": "em", "factor": .001, "sample": "trigger"}))
    execution = execute_mechanics(template, rotation, model)
    result = execution["result"]
    result["mechanics_trace"] = execution["mechanics_trace"]
    result["sampling_trace"] = execution["sampling_trace"]
    report = {"plan": {"resolved_template": execution["resolved_template"]}, "mechanics_model": model,
              "compiled_rotation_model": rotation, "input_fingerprint": "1" * 64,
              "output_fingerprint": "2" * 64, "source_fingerprint": "3" * 64}
    assemble_damage_explanations({}, result, report)
    receipt = {**{key: report[key] for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint")},
               "damage_explanations": make_explanation_receipt(result), "result_fingerprint": fingerprint(result)}
    return result, receipt, report


def test_actual_provider_trigger_sample_and_parent_action_survive_projection():
    result, receipt, report = _runtime_explained()
    event = _event(result)
    doc = result["damage_explanations"]
    source = doc["sources"][event["source_ref"]]
    modifier = doc["modifiers"][event["modifier_refs"][0]]
    assert source["action_id"] == "cast" and source["ability_id"] == "e"
    assert source["cycle"] == 0
    panel = doc["panels"][event["panel_ref"]]
    assert panel["sample_t"] is None and panel["evaluation_t"] == .2
    assert modifier["sampling"]["character"] == "b"
    assert modifier["sampling"]["source_value"] == 500
    assert modifier["sampling"]["sample_t"] == 0
    assert modifier["execution"]["provider"] == "b"
    assert modifier["execution"]["domain"] == "provider"
    assert validate_damage_explanations(result, receipt, report=report) == "complete"


def test_rehashed_sampling_tamper_still_cannot_claim_actual_runtime_value():
    result, receipt, report = _runtime_explained()
    event = _event(result)
    doc = result["damage_explanations"]
    old = event["modifier_refs"][0]
    modifier = copy.deepcopy(doc["modifiers"].pop(old))
    modifier["sampling"]["source_value"] += 1
    new = digest(modifier)
    doc["modifiers"][new] = modifier
    for row in doc["events"].values():
        row["modifier_refs"] = [new if ref == old else ref for ref in row["modifier_refs"]]
    result["damage_explanation_fingerprint"] = digest(doc)
    receipt["damage_explanations"] = make_explanation_receipt(result)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError, match="逐击解释"):
        validate_damage_explanations(result, receipt, report=report)


def test_preseal_capacity_partial_preserves_numbers_and_has_no_dangling_refs():
    template, _, _ = fixture(tuple(.2 + i * .1 for i in range(20)))
    result = calculate(template, explanation_max_bytes=3500)
    plain = calculate(template, include_damage_explanations=False)
    assert _without_explanations(result) == plain
    assert result["damage_explanations"]["status"] == "partial"
    assert result["damage_explanations"]["coverage"]["omitted"] > 0
    validate_damage_explanations(result)


def test_final_app_enrichment_is_bounded_before_sealing():
    template, rotation, model = fixture(tuple(.2 + i * .1 for i in range(20)))
    result = calculate(template, explanation_max_bytes=3500)
    plain = calculate(template, include_damage_explanations=False)
    report = {"plan": {"resolved_template": template}, "mechanics_model": model,
              "compiled_rotation_model": rotation, "input_fingerprint": "1" * 64,
              "output_fingerprint": "2" * 64, "source_fingerprint": "3" * 64}
    assemble_damage_explanations({}, result, report)
    receipt = {**{key: report[key] for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint")},
               "damage_explanations": make_explanation_receipt(result), "result_fingerprint": fingerprint(result)}
    doc = result["damage_explanations"]
    assert len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf8")) <= 3500
    assert doc["status"] == "partial"
    assert result["summary"] == plain["summary"]
    assert [row["expected"] for row in result["hits"]] == [row["expected"] for row in plain["hits"]]
    assert validate_damage_explanations(result, receipt, report=report) == "partial"


def test_special_lineage_preserves_actual_action_ability_component_not_reaction_label():
    template, rotation, model = fixture((.2,))
    selected = template["hits"][0]
    selected.update(skill="星超导", damage_system="stellar", reaction={"type": "stellar_superconduct", "layers": 3})
    selected["extensions"] = {
        "elemental": {"action_id": "cast", "special_reaction_request_id": "special.declared", "target_id": "enemy", "area_id": "area"},
        "special_reaction": {"request_id": "special.declared", "component": "periodic.part",
                             "ability_profile": {"ability_binding": {"ability_id": "e", "component_id": "periodic.part"}}}}
    result = calculate(template)
    report = {"plan": {"resolved_template": template}, "mechanics_model": model,
              "compiled_rotation_model": rotation, "input_fingerprint": "1" * 64,
              "output_fingerprint": "2" * 64, "source_fingerprint": "3" * 64}
    assemble_damage_explanations({}, result, report)
    source = result["damage_explanations"]["sources"][_event(result)["source_ref"]]
    assert source["action_id"] == "cast" and source["ability_id"] == "e"
    assert source["component_id"] == "periodic.part" and source["source_event_id"] == "special.declared"
    assert source["skill"] == "星超导"  # classification is preserved, not used to invent ability identity


def test_app_explanation_sections_are_lazy_paginated_and_immutable(tmp_path):
    calculated = run_plan_calc(delivery_plan(), tmp_path / "absent.db", capture_display=True)
    before = copy.deepcopy(calculated)
    request = {"view": "damage_explanations", "limit": 1,
               "sections": ["identity", "panel", "formula", "normalization", "outcome"]}
    page = review_calculation(calculated, request)
    assert page["explanation"]["status"] == "complete"
    detail = page["items"][0]["explanation"]
    assert detail["panel"]["inputs"]["scaling"] == 100
    assert detail["formula"]["checkpoints"]["expected_per_count"] == 45
    assert set(detail) == set(request["sections"])
    batch = review_calculation_batch(calculated, {"requests": [request, request]})
    assert [row["review"] for row in batch["pages"]] == [page, page]
    assert calculated == before
    by_type = review_calculation(calculated, {"view": "damage_explanations", "filters": {
        "damage_type_id": calculated["result"]["hits"][0]["damage_type_ids"][0]}})
    assert by_type["total_rows"] == 1
    assert "zones" not in by_type["items"][0]
    with pytest.raises(PlanError):
        review_calculation(calculated, {"view": "hits", "sections": ["panel"]})


def test_old_result_reports_missing_without_inventing_a_panel():
    page = review_calculation(_saved(), {"view": "damage_explanations", "sections": ["panel"]})
    assert page["explanation"]["status"] == "missing"
    assert page["items"] == []


def test_raw_core_orphan_fingerprint_cannot_be_treated_as_legacy_missing():
    template, _, _ = fixture((.2,))
    result = calculate(template)
    result.pop("damage_explanations")
    with pytest.raises(ValueError, match="逐击解释"):
        validate_damage_explanations(result)


@pytest.mark.parametrize("signal", ["fingerprint", "receipt", "marker", "null_document"])
def test_any_remaining_family_signal_rejects_a_missing_document(signal):
    result, receipt = {"hits": []}, {}
    if signal == "fingerprint":
        result["damage_explanation_fingerprint"] = "1" * 64
    elif signal == "receipt":
        receipt["damage_explanations"] = {"contract": "damage-explanation-receipt-v1"}
    elif signal == "marker":
        result["damage_explanation_contract"] = "damage-explanation-v1"
    else:
        result["damage_explanations"] = None
    with pytest.raises(ValueError, match="逐击解释"):
        validate_damage_explanations(result, receipt)
    assert validate_damage_explanations({"hits": []}, {}) == "missing"


def test_stripping_every_result_family_field_cannot_hide_the_final_receipt(tmp_path):
    calculated = run_plan_calc(delivery_plan(), tmp_path / "none.db")
    result = calculated["result"]
    for key in ("damage_explanations", "damage_explanation_fingerprint", "damage_explanation_contract"):
        result.pop(key)
    for row in result["hits"]:
        row.pop("explanation_ref", None)
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError, match="逐击解释"):
        validate_damage_explanations(result, calculated["result_receipt"])


def test_external_value_has_its_own_explanation_and_no_fake_formula(tmp_path):
    db = tmp_path / "none.db"
    calculated = run_plan_calc(confirm(external_plan(), db), db)
    doc = calculated["result"]["damage_explanations"]
    event = doc["events"]["external:extra.1"]
    assert event["outcome"]["origin"] == "external"
    assert event["outcome"]["expected"] == 12
    assert event["formula_ref"] is None and event["panel_ref"] is None
    assert event["normalization"]["status"] == "not_applicable"
    validate_damage_explanations(calculated["result"], calculated["result_receipt"], report=calculated["report"])


def test_real_fixed_value_takeover_is_not_explained_by_the_automatic_formula(tmp_path):
    from tests.test_calculation_overrides_execution import (
        accept_from_compose,
        declaration,
        native_plan,
        override_item,
        target,
    )
    db = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value", target("computed_event", "h0", "result.hits"),
        {"expected": 999, "precision": {"kind": "approximate", "method": "工程独立数值",
          "uncertainty": "仅验证接管", "state_effects": "none"}}))
    accept_from_compose(plan, db)
    calculated = run_plan_calc(plan, db)
    event = _event(calculated["result"])
    assert event["outcome"]["origin"] == "fixed_override"
    assert event["outcome"]["expected"] == 999 and event["outcome"]["crit"] is None
    assert event["outcome"]["automatic_expected"] == 45
    assert event["formula_scope"] == "automatic_baseline_only"
    assert event["normalization"]["status"] == "not_applicable"
    validate_damage_explanations(calculated["result"], calculated["result_receipt"], report=calculated["report"])


@pytest.mark.parametrize("field", ["damage_explanations", "damage_explanation_fingerprint"])
def test_current_saved_family_cannot_silently_downgrade_to_legacy(tmp_path, field):
    calculated = run_plan_calc(delivery_plan(), tmp_path / "none.db")
    calculated["result"].pop(field)
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises((ValueError, PlanError), match="逐击解释"):
        review_calculation({key: calculated[key] for key in ("result", "result_receipt")},
                           {"view": "damage_explanations"})
