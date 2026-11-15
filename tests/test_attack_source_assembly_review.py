"""Independent EC21 checks: raw fixture inputs, Decimal damage oracles, no fake DB."""
from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from test_action_events import action_event_plan, confirm_action_events
from test_mechanics_execution import EVIDENCE, action, add, hit, rule
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_attack_metadata import candidate, change, metadata_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attack_source_assembly import assemble_attack_source
from genshin_app.equipment.files import fingerprint, input_view, json_bytes, read_plan
from genshin_app.equipment.schema import PlanError, validate_plan

# ruff: noqa: F811


def empty_plan(db):
    plan = action_event_plan(db)
    plan.pop("hit_parameter_sources")
    plan["manual_template"]["hits"] = []
    for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides"):
        plan.pop(key)
    plan["mechanics_model"]["elemental_model"]["profiles"] = []
    plan["manual_template"]["meta"] = {"assumptions": ["Independent engineering timing; not a game rotation"]}
    validate_plan(plan)
    return plan


def request(db, ability="Avatar_Ayaka_Icespine_Strike_New"):
    return {"schema_version": 1, "profile_id": "review.contact", "ability_ids": ["e"],
        "version_line": "release", "selection": {"character_id": 90000001, "slot": "skill", "constellation": 0},
        "candidate_id": candidate(db, ability)["candidate_id"], "damage_type_ids": ["damage.elemental_skill"],
        "scaling_stat": "atk", "calibration": {"offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "contact_profile_id": "safe", "counter_tag": "review.shared", "default_landed": True,
        "self_sampling": "current", "application_mode": "sequence",
        "status": "confirmed", "source": "独立工程夹具逐攻击段用途说明",
        "verification": {"status": "confirmed", "locator": "tests/test_attack_source_assembly_review.py request",
            "version": "commit 342084292ba4c3c6ec2bc34c045de4de72ae8d53", "reviewed_at": "2026-10-18",
            "component_scope": "selected candidate exact attack component and declared shared counter",
            "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]}}


def accepted(plan, db):
    return confirm_action_events(copy.deepcopy(plan), db)


@pytest.mark.parametrize(("ability", "variant", "element", "multiplier"), [
    ("Avatar_Ayaka_Icespine_Strike_New", "tap", "cryo", "4.305600166320801"),
    ("Avatar_Kazuha_ElementalArt_FlyAway", "hold", "anemo", "4.694399833679199"),
])
def test_review_empty_native_to_three_real_contacts_without_auto_accept(metadata_db, tmp_path, ability, variant, element, multiplier):
    plan, req = empty_plan(metadata_db), request(metadata_db, ability)
    plan["rotation_model"]["abilities"][0]["variant"] = variant
    before = copy.deepcopy((plan, req))
    out = assemble_attack_source(plan, req, metadata_db)
    native = out["plan"]
    assert (plan, req) == before
    assert out["assembly"]["created"] == {"hits": 1, "profiles": 1, "bindings": 1, "sources": 1}
    assert out["report"]["state"] == "pending"
    assert native["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    assert out["report"]["action_event_compilations"][0]["generated"] == []
    assert out["report"]["attack_metadata_compilations"][0]["status"] == "ready"
    assert native["rotation_model"] == before[0]["rotation_model"]
    assert native["equipment_inputs"] == before[0]["equipment_inputs"]
    assert native["mechanics_model"] == before[0]["mechanics_model"]
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert "element" not in native["manual_template"]["hits"][0]
    assert "multiplier" not in native["manual_template"]["hits"][0]
    assert "hit_parameter_sources" not in native and "elemental_sources" not in native
    saved = tmp_path / "assembled-native.json"
    saved.write_bytes(json_bytes(native))
    assert read_plan(saved)["plan"] == native
    calculated = run_plan_calc(accepted(native, metadata_db), metadata_db)
    expected = Decimal(100) * Decimal(multiplier) * Decimal("0.5") * Decimal("0.9")
    assert [h["expected"] for h in calculated["result"]["hits"]] == pytest.approx([float(expected)] * 3)
    assert [h["t"] for h in calculated["result"]["hits"]] == [.2, 2.2, 4.2]
    assert [h["element"] for h in calculated["plan"]["resolved_template"]["hits"]] == [element] * 3
    assert len(calculated["report"]["mechanics_model"]["hit_patterns"]) == 3
    assert native["mechanics_model"]["hit_patterns"] == []


def test_review_idempotent_request_keeps_acceptance_but_rechecks_database(metadata_db, fake_repo):
    req = request(metadata_db)
    native = accepted(assemble_attack_source(empty_plan(metadata_db), req, metadata_db)["plan"], metadata_db)
    before = copy.deepcopy(native)
    repeated = assemble_attack_source(native, req, metadata_db)
    assert repeated["assembly"]["status"] == "existing"
    assert repeated["assembly"]["created"] == {"hits": 0, "profiles": 0, "bindings": 0, "sources": 0}
    assert repeated["plan"] == native == before
    assert repeated["report"]["action_event_compilations"][0]["status"] == "ready"
    change(metadata_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json",
           lambda rows: rows[0]["paramList"].__setitem__(0, 5.25))
    refreshed = assemble_attack_source(native, req, metadata_db)
    assert refreshed["plan"] == before
    assert refreshed["report"]["state"] == "pending"
    record = refreshed["report"]["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert record["required_confirmation"]["accepted_profile_fingerprint"] != native["action_event_profiles"][0]["accepted_profile_fingerprint"]


@pytest.mark.parametrize("edited", ["calibration", "prototype", "source", "bindings"])
def test_review_same_profile_different_owned_content_refuses_instead_of_overwriting(metadata_db, edited):
    req = request(metadata_db)
    native = assemble_attack_source(empty_plan(metadata_db), req, metadata_db)["plan"]
    if edited == "calibration":
        native["action_event_profiles"][0]["calibration"]["offset_seconds"] = .3
    elif edited == "prototype":
        native["manual_template"]["hits"][0]["hit_name"] = "User edited label"
    elif edited == "source":
        native["attack_metadata_sources"][0]["counter_tag"] = "different.shared"
    else:
        native["action_event_bindings"] = []
    before = copy.deepcopy(native)
    with pytest.raises(PlanError) as error:
        assemble_attack_source(native, req, metadata_db)
    assert error.value.reason == "attack_source_assembly_conflict"
    assert native == before


@pytest.mark.parametrize("kind", ["hit", "pattern", "application", "action_hit_link"])
def test_review_manual_delivery_is_not_deleted_or_adopted(metadata_db, kind):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    plan["manual_template"]["hits"] = [hit("manual", .2)]
    if kind == "hit":
        plan["mechanics_model"]["hit_events"] = [{"hit_id": "manual", "action_id": "cast0", "order": 0}]
    elif kind == "pattern":
        plan["mechanics_model"]["hit_patterns"] = [{"id": "manual.pattern", "prototype_hit_id": "manual", "action_id": "cast0", "offsets": [.2], "orders": [0]}]
    elif kind == "application":
        plan["mechanics_model"]["elemental_model"]["profiles"] = [{"id": "manual.app", "element": "cryo", "application": "none", **EVIDENCE}]
        plan["mechanics_model"]["application_events"] = [{"id": "manual.apply", "action_id": "cast0", "t": .2, "order": 0,
            "application": {"profile_id": "manual.app", "target_id": "enemy", "landed": True}, "contact": {"kind": "none", **EVIDENCE}}]
    else:
        plan["rotation_model"]["action_hit_links"] = [{"action_id": "cast0", "hit_id": "manual"}]
    validate_plan(plan)
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as error:
        assemble_attack_source(plan, req, metadata_db)
    assert error.value.reason == "attack_source_assembly_conflict"
    assert plan == before


@pytest.mark.parametrize("kind", ["actor", "variant", "non_skill"])
def test_review_multiple_abilities_cannot_sneak_mixed_scope(metadata_db, kind):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    other = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    other["id"] = "other"
    if kind == "actor":
        other["character"] = "somebody.else"
    elif kind == "variant":
        other["variant"] = "hold"
    else:
        other["action_type_id"] = "action.normal_attack"
    plan["rotation_model"]["abilities"].append(other)
    req["ability_ids"].append("other")
    with pytest.raises(PlanError) as error:
        assemble_attack_source(plan, req, metadata_db)
    assert error.value.reason == "attack_source_assembly_scope"


def test_review_unrelated_manual_delivery_and_user_metadata_survive(metadata_db):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    other = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    other["id"] = "unrelated"
    plan["rotation_model"]["abilities"].append(other)
    plan["rotation_model"]["actions"].append(action("old.cast", bid="unrelated", start=6, end=7, repeat={"mode": "once"}))
    plan["manual_template"]["hits"] = [hit("old.hit", 6.2)]
    plan["mechanics_model"]["hit_events"] = [{"hit_id": "old.hit", "action_id": "old.cast", "order": 0}]
    before = copy.deepcopy(plan)
    out = assemble_attack_source(plan, req, metadata_db)["plan"]
    assert out["manual_template"]["hits"][0] == before["manual_template"]["hits"][0]
    for key in ("rotation_model", "mechanics_model", "equipment_inputs", "effect_bindings"):
        assert out[key] == before[key]
    assert out["manual_template"]["meta"] == before["manual_template"]["meta"]
    assert plan == before


@pytest.mark.parametrize("field", ["default_landed", "candidate_id", "status"])
def test_review_unknown_request_never_becomes_confirmed_or_executable(metadata_db, field):
    req = request(metadata_db)
    req[field] = "pending" if field == "status" else None
    out = assemble_attack_source(empty_plan(metadata_db), req, metadata_db)
    assert out["report"]["state"] == "pending"
    assert out["plan"]["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    assert out["report"]["action_event_compilations"][0]["generated"] == []
    if field == "default_landed":
        assert out["plan"]["action_event_profiles"][0][field] is None
    else:
        assert out["plan"]["attack_metadata_sources"][0][field] == req[field]


def test_review_source_candidate_wrong_identity_remains_pending(metadata_db):
    req = request(metadata_db)
    req["candidate_id"] = "not.a.database.candidate"
    out = assemble_attack_source(empty_plan(metadata_db), req, metadata_db)
    assert out["report"]["state"] == "pending"
    assert out["report"]["attack_metadata_compilations"][0]["status"] == "pending"
    assert out["report"]["action_event_compilations"][0]["generated"] == []


def test_review_alias_ability_selection_reuses_exact_actual_actions(metadata_db):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    alias = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    alias["id"] = "local.alias"
    plan["rotation_model"]["abilities"].append(alias)
    plan["rotation_model"]["actions"][1]["ability_id"] = alias["id"]
    req["ability_ids"] = [alias["id"], "e"]
    out = assemble_attack_source(plan, req, metadata_db)
    assert len(out["plan"]["action_event_profiles"]) == 1
    assert len(out["plan"]["action_event_bindings"]) == 2
    req["ability_ids"].reverse()
    same = assemble_attack_source(out["plan"], req, metadata_db)
    assert same["assembly"]["status"] == "existing"
    calc = run_plan_calc(accepted(same["plan"], metadata_db), metadata_db)
    assert [h["t"] for h in calc["result"]["hits"]] == [.2, 2.2, 4.2]


def test_review_existing_selector_reference_cannot_be_silently_collected(metadata_db):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    hid = "assembly.hit." + fingerprint(req["profile_id"])
    # A pre-existing selector, even with no emitted hit, must not gain a new target.
    add(plan["mechanics_model"], rule(trigger={"event": "hit", "phase": "post", "event_ids": [hid]}))
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as error:
        assemble_attack_source(plan, req, metadata_db)
    assert error.value.reason == "attack_source_assembly_conflict"
    assert plan == before


def test_review_receipt_input_hashes_cover_native_not_preview_cache(metadata_db):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    out = assemble_attack_source(plan, req, metadata_db)
    assert out["assembly"]["base_input_fingerprint"] == fingerprint(input_view(plan))
    assert out["assembly"]["input_fingerprint"] == fingerprint(input_view(out["plan"]))
    assert out["assembly"]["input_fingerprint"] != fingerprint(out["report"])


@pytest.mark.parametrize(("field", "value", "code"), [
    ("occupation_end", .1, "action_event_time_scope"),
    ("repeat", {"mode": "each_cycle"}, "action_event_repeat"),
])
def test_review_accepting_source_cannot_bypass_existing_action_bounds(metadata_db, field, value, code):
    plan, req = empty_plan(metadata_db), request(metadata_db)
    plan["rotation_model"]["actions"][0][field] = value
    before = copy.deepcopy(plan["rotation_model"])
    native = accepted(assemble_attack_source(plan, req, metadata_db)["plan"], metadata_db)
    report = compose_equipment_plan(native, metadata_db)
    assert native["rotation_model"] == before
    record = report["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert code in {issue["code"] for issue in record["issues"]}


def test_review_explicit_misses_remain_zero_with_three_real_contact_records(metadata_db):
    req = request(metadata_db)
    req["default_landed"] = False
    native = assemble_attack_source(empty_plan(metadata_db), req, metadata_db)["plan"]
    out = run_plan_calc(accepted(native, metadata_db), metadata_db)
    assert out["result"]["summary"]["total_damage"] == 0
    assert out["result"]["hits"] == []
    record = out["report"]["action_event_compilations"][0]
    assert len(record["generated"]) == 3 and all(not item["landed"] for item in record["generated"])
    assert native["action_event_profiles"][0]["default_landed"] is False
