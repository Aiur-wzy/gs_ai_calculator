"""Source assembly uses actual metadata fixtures and the existing calculation path."""
from __future__ import annotations

import copy
import json

import pytest
from test_action_events import confirm_action_events
from test_attack_metadata_app import metadata_native
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_attack_metadata import change, metadata_db  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.application_evidence import research_report_template
from genshin_app.equipment.attack_source_assembly import (
    MAX_REQUEST_BYTES,
    assemble_attack_source,
    attack_source_assembly_capabilities,
)
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError, validate_plan

# ruff: noqa: F811


def empty_source_plan(db, ability="Avatar_Ayaka_Icespine_Strike_New"):
    plan = metadata_native(db, ability)
    plan["manual_template"]["hits"] = []
    for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides", "attack_metadata_sources"):
        plan.pop(key, None)
    if ability.endswith("FlyAway"):
        plan["rotation_model"]["abilities"][0]["variant"] = "hold"
    return plan


def request_for(db, ability="Avatar_Ayaka_Icespine_Strike_New"):
    manual = metadata_native(db, ability)
    profile, source = manual["action_event_profiles"][0], manual["attack_metadata_sources"][0]
    return {"schema_version": 1, "profile_id": "assembled.contact", "ability_ids": ["e"],
            **{k: copy.deepcopy(source[k]) for k in (
                "version_line", "selection", "candidate_id", "counter_tag", "verification", "status", "source")},
            "application_mode": source["application_mode"],
            "damage_type_ids": ["damage.elemental_skill"], "scaling_stat": "atk",
            **{k: copy.deepcopy(profile[k]) for k in (
                "calibration", "contact_profile_id", "default_landed", "self_sampling")}}


def audit_report():
    report = research_report_template()
    report["actual_queries"] = ["exact attack component attenuationTag"]
    report["sources_checked"] = [{"locator": "fixture://attack-component", "version": "fixture-v1",
        "accessed_at": "2026-10-18", "supported_fields": ["counter_tag"],
        "decision": "adopted", "reason": "受控夹具精确字段"}]
    report["adopted_findings"] = ["该组件使用夹具中的精确计数标签"]
    return report


@pytest.mark.parametrize("ability", ["Avatar_Ayaka_Icespine_Strike_New", "Avatar_Kazuha_ElementalArt_FlyAway",
                                    "Avatar_Kazuha_ElementalArt_EmptyJump"])
def test_real_sources_from_empty_hits_match_manual_calculation(metadata_db, ability):
    plan, request = empty_source_plan(metadata_db, ability), request_for(metadata_db, ability)
    before = copy.deepcopy((plan, request))
    validate_plan(plan)  # The starting point is an ordinary valid native draft.
    result = assemble_attack_source(plan, request, metadata_db)
    assert (plan, request) == before
    native, report, summary = result["plan"], result["report"], result["assembly"]
    assert report["state"] == "pending"
    assert summary["status"] == "created"
    assert summary["created"] == {"hits": 1, "profiles": 1, "bindings": 1, "sources": 1}
    assert summary["base_input_fingerprint"] == fingerprint(input_view(plan))
    assert summary["input_fingerprint"] == fingerprint(input_view(native))
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert native["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    assert len(native["manual_template"]["hits"]) == 1
    assert not ({"element", "multiplier"} & native["manual_template"]["hits"][0].keys())
    assert native["mechanics_model"]["elemental_model"]["profiles"] == []
    assert native["mechanics_model"]["hit_patterns"] == []
    validate_plan(json.loads(json.dumps(native)))

    manual = metadata_native(metadata_db, ability)
    manual["manual_template"]["hits"][0]["skill"] = "元素战技"
    if ability.endswith("FlyAway"):
        manual["action_event_profiles"][0]["variant"] = "hold"
        manual["rotation_model"]["abilities"][0]["variant"] = "hold"
    generated = run_plan_calc(confirm_action_events(native, metadata_db), metadata_db)
    baseline = run_plan_calc(confirm_action_events(manual, metadata_db), metadata_db)
    for key in ("expected", "t"):
        assert [h[key] for h in generated["result"]["hits"]] == [h[key] for h in baseline["result"]["hits"]]
    assert generated["result"]["summary"] == baseline["result"]["summary"]
    assert generated["report"]["attack_metadata_compilations"][0]["compiled_fields"] == baseline["report"]["attack_metadata_compilations"][0]["compiled_fields"]
    assert generated["report"]["action_event_compilations"][0]["profile_fingerprint"] == baseline["report"]["action_event_compilations"][0]["profile_fingerprint"]
    for calc in (generated, baseline):
        assert len(calc["result"]["hits"]) == 3
    def applications(calc):
        rows = []
        for row in calc["result"]["elemental"]["trace"]:
            if row["type"] != "elemental_application":
                continue
            states = [{k: {sk: sv for sk, sv in v.items() if sk != "source"} if isinstance(v, dict) else v
                       for k, v in row[state].items()} for state in ("before", "after")]
            rows.append((row["t"], row["allowed"], states))
        return rows
    # Only aura-source generated event identity differs; all state numbers and gates match.
    assert applications(generated) == applications(baseline)


def test_existing_preserves_acceptance_and_cached_plan_but_preview_is_fresh(metadata_db):
    request = request_for(metadata_db)
    first = assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)
    accepted = confirm_action_events(first["plan"], metadata_db)
    accepted["resolution"] = {"state": "ready", "untrusted_preview": 999}
    accepted["resolved_template"] = None
    before = copy.deepcopy(accepted)
    out = assemble_attack_source(accepted, request, metadata_db)
    assert out["plan"] == accepted == before
    assert out["assembly"]["status"] == "existing"
    assert not any(out["assembly"]["created"].values())
    assert out["report"]["state"] == "ready"
    assert "untrusted_preview" not in out["report"]
    assert out["report"]["action_event_compilations"][0]["status"] == "ready"
    # A source-independent contact change is still revalidated, never accepted from cache.
    accepted["mechanics_model"]["elemental_model"]["contact_profiles"][0]["source"] += " changed"
    stale = assemble_attack_source(accepted, request, metadata_db)
    assert stale["report"]["state"] == "pending"
    assert stale["plan"]["action_event_profiles"][0]["accepted_profile_fingerprint"] == before["action_event_profiles"][0]["accepted_profile_fingerprint"]


def test_research_report_round_trips_through_attack_assembly_without_granting_authority(metadata_db):
    request = request_for(metadata_db)
    request["research_report"] = audit_report()
    first = assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)
    source = first["plan"]["attack_metadata_sources"][0]
    assert source["research_report"] == request["research_report"]
    assert source["research_report"]["execution_authority"] == "none"
    assert assemble_attack_source(first["plan"], request, metadata_db)["assembly"]["status"] == "existing"
    without_report = copy.deepcopy(request)
    without_report.pop("research_report")
    with pytest.raises(PlanError):
        assemble_attack_source(first["plan"], without_report, metadata_db)


@pytest.mark.parametrize("change", ["hit_name", "calibration", "source", "bindings"])
def test_existing_edits_are_conflicts_without_mutation(metadata_db, change):
    request = request_for(metadata_db)
    plan = assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)["plan"]
    if change == "hit_name":
        plan["manual_template"]["hits"][0]["hit_name"] = "用户重新命名"
    elif change == "calibration":
        plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = .3
    elif change == "source":
        plan["attack_metadata_sources"][0]["source"] += " 更正"
    else:
        plan["action_event_bindings"] = []
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError, match="不同内容或归属"):
        assemble_attack_source(plan, request, metadata_db)
    assert plan == before


@pytest.mark.parametrize("candidate_id", [None, "unavailable-candidate"])
def test_candidate_draft_does_not_choose_first_or_materialize(metadata_db, candidate_id):
    request = request_for(metadata_db)
    request["candidate_id"] = candidate_id
    out = assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)
    assert out["report"]["state"] == "pending"
    assert out["plan"]["attack_metadata_sources"][0]["candidate_id"] == candidate_id
    assert out["report"]["action_event_compilations"][0]["generated"] == []


def test_multiple_same_variant_abilities_share_one_profile(metadata_db):
    request, plan = request_for(metadata_db), empty_source_plan(metadata_db)
    extra = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    extra["id"] = "e.second"
    plan["rotation_model"]["abilities"].append(extra)
    plan["rotation_model"]["actions"][1]["ability_id"] = extra["id"]
    request["ability_ids"] = [extra["id"], "e"]
    out = assemble_attack_source(plan, request, metadata_db)
    assert len(out["plan"]["action_event_profiles"]) == 1
    assert out["assembly"]["created"]["bindings"] == 2
    request["ability_ids"].reverse()
    assert assemble_attack_source(out["plan"], request, metadata_db)["assembly"]["status"] == "existing"
    calc = run_plan_calc(confirm_action_events(out["plan"], metadata_db), metadata_db)
    assert len(calc["result"]["hits"]) == 3


@pytest.mark.parametrize("case", ["unknown", "missing", "bool", "scaling", "selection", "duplicate",
                                  "huge", "nonfinite", "precision"])
def test_request_rejected_before_database(case, monkeypatch):
    from genshin_app.equipment import attack_source_assembly as module
    request = {"schema_version": 1, "profile_id": "p", "ability_ids": ["e"],
               "version_line": "release", "selection": {"character_id": 1, "slot": "skill", "constellation": 0},
               "candidate_id": None, "damage_type_ids": ["damage.elemental_skill"], "scaling_stat": "atk",
               "calibration": {"offset_seconds": .2, "precision_seconds": .01, "status": "assumed", "source": "工程"},
               "contact_profile_id": "safe", "counter_tag": "shared", "default_landed": True,
               "self_sampling": "current", "application_mode": "sequence",
               "status": "assumed", "source": "工程"}
    if case == "huge":
        request["source"] = "x" * (MAX_REQUEST_BYTES + 1)
    elif case == "nonfinite":
        request["schema_version"] = float("nan")
    elif case == "bool":
        request["schema_version"] = True
    elif case == "unknown":
        request["prepared"] = {}
    elif case == "missing":
        request.pop("candidate_id")
    elif case == "precision":
        request["calibration"] = {"offset_seconds": .0000001}
    elif case == "selection":
        request["selection"] = {"character_id": 1, "constellation": 0, "slot": "skill", "skill_id": 1}
    elif case == "duplicate":
        request["ability_ids"] = ["a", "a"]
    else:
        request["scaling_stat"] = "def"
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("invalid request reached DB"))
    with pytest.raises(PlanError) as caught:
        assemble_attack_source({}, request, "missing.db")
    assert caught.value.reason.startswith("attack_source_assembly_")


@pytest.mark.parametrize("case", ["manual", "contact", "id", "variant", "wrong_actor", "wrong_source"])
def test_native_conflicts_are_atomic(metadata_db, case):
    plan, request = empty_source_plan(metadata_db), request_for(metadata_db)
    if case == "manual":
        plan["rotation_model"]["action_hit_links"] = [{"action_id": "cast0", "hit_id": "old"}]
    elif case == "contact":
        request["contact_profile_id"] = "unknown"
    elif case == "id":
        plan["manual_template"].setdefault("meta", {})["owned"] = {"id": "assembly.hit." + fingerprint(request["profile_id"])}
    elif case == "variant":
        plan["rotation_model"]["abilities"][0].pop("variant")
    elif case == "wrong_actor":
        extra = copy.deepcopy(plan["rotation_model"]["abilities"][0])
        extra.update(id="other", character="unknown")
        plan["rotation_model"]["abilities"].append(extra)
        request["ability_ids"].append("other")
    else:
        plan["equipment_inputs"]["a"] = {"mode": "panel", "panel_policy": "static_included", "weapon": None,
            "artifact_sets": [], "source_refs": [], "data_version": None,
            "character_ref": {"db_character_id": 7, "version_line": "release"}}
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError):
        assemble_attack_source(plan, request, metadata_db)
    assert plan == before


def test_unrelated_native_fields_and_overrides_are_not_rewritten(metadata_db):
    plan, request = empty_source_plan(metadata_db), request_for(metadata_db)
    plan["manual_template"].setdefault("meta", {})["note"] = "既有显式动作，不自动补多轮"
    before = copy.deepcopy(plan)
    out = assemble_attack_source(plan, request, metadata_db)
    for key in ("rotation_model", "mechanics_model", "equipment_inputs", "effect_bindings", "rotation_tuning"):
        if key in plan:
            assert out["plan"][key] == before[key]
    assert out["plan"]["manual_template"]["meta"] == before["manual_template"]["meta"]


def test_capabilities_are_isolated_and_strict():
    caps = attack_source_assembly_capabilities()
    schema = caps["request_schema"]
    assert set(schema["required"]) == set(schema["properties"]) - {
        "counter_tag", "shared_group", "verification", "research_report"}
    assert "application_mode" in schema["required"]
    assert schema["properties"]["application_mode"]["enum"] == [
        "sequence", "no_icd", "no_application", "pending"]
    assert "attack_metadata_sources[].research_report" in caps["research_report_input"]
    assert caps["deprecated_application_mode_aliases"] == {"unknown": "pending"}
    assert schema["additionalProperties"] is False
    schema["properties"]["candidate_id"] = {}
    assert attack_source_assembly_capabilities()["request_schema"]["properties"]["candidate_id"]


def test_new_attack_assembly_request_rejects_legacy_unknown_before_database(metadata_db, monkeypatch):
    from genshin_app.equipment import attack_source_assembly as module

    request = request_for(metadata_db)
    request.update(application_mode="unknown", status="pending")
    request.pop("counter_tag")
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("legacy request reached DB"))
    with pytest.raises(PlanError) as caught:
        assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)
    assert caught.value.reason == "attack_source_assembly_schema"


def test_changed_fresh_database_source_invalidates_acceptance(metadata_db, fake_repo):
    request = request_for(metadata_db)
    native = assemble_attack_source(empty_source_plan(metadata_db), request, metadata_db)["plan"]
    confirm_action_events(native, metadata_db)
    old_fp = native["action_event_profiles"][0]["accepted_profile_fingerprint"]
    change(metadata_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json",
           lambda rows: rows[0]["paramList"].__setitem__(0, 5.0))
    out = assemble_attack_source(native, request, metadata_db)
    assert out["assembly"]["status"] == "existing"
    assert out["plan"]["action_event_profiles"][0]["accepted_profile_fingerprint"] == old_fp
    assert out["report"]["state"] == "pending"
    assert out["report"]["action_event_compilations"][0]["generated"] == []
    with pytest.raises(PlanError):
        run_plan_calc(out["plan"], metadata_db)


@pytest.mark.parametrize("case", ["landed_unknown", "calibration_pending", "outside_occupation", "cancel", "no_actions"])
def test_existing_compiler_guards_remain_pending(metadata_db, case):
    request, plan = request_for(metadata_db), empty_source_plan(metadata_db)
    if case == "landed_unknown":
        request["default_landed"] = None
    elif case == "calibration_pending":
        request["calibration"]["status"] = "pending"
    elif case == "outside_occupation":
        request["calibration"]["offset_seconds"] = 2
    elif case == "cancel":
        plan["rotation_model"]["actions"][0]["switch_allowed_at"] = .1
        character = copy.deepcopy(plan["manual_template"]["characters"][0])
        character["id"] = "b"
        plan["manual_template"]["characters"].append(character)
        state = copy.deepcopy(plan["rotation_model"]["characters"][0])
        state["id"] = "b"
        plan["rotation_model"]["characters"].append(state)
        plan["rotation_model"]["abilities"].append({"id": "swap", "character": "a", "action_type_id": "action.switch"})
        plan["rotation_model"]["actions"].append({"id": "early-switch", "ability_id": "swap", "start": .15,
            "occupation_end": .15, "order": 1, "switch_to": "b", "repeat": {"mode": "once"},
            "status": "assumed", "source": "明确的工程切人"})
    else:
        plan["rotation_model"]["actions"] = []
    out = assemble_attack_source(plan, request, metadata_db)
    native = confirm_action_events(out["plan"], metadata_db)
    repeated = assemble_attack_source(native, request, metadata_db)
    assert repeated["report"]["state"] == "pending"
    assert repeated["report"]["action_event_compilations"][0]["generated"] == []
    with pytest.raises(PlanError):
        run_plan_calc(native, metadata_db)


@pytest.mark.parametrize("collection", ["hit_events", "hit_patterns", "application_events", "action_hit_links"])
def test_handwritten_delivery_is_rejected_before_compose(metadata_db, collection, monkeypatch):
    from genshin_app.equipment import attack_source_assembly as module
    request, plan = request_for(metadata_db), empty_source_plan(metadata_db)
    prototype = {"id": "manual", "damage_type_ids": ["damage.elemental_skill"], "t": 0,
                 "character": "a", "skill": "手工保留", "damage_system": "classic", "element": "cryo", "multiplier": 1}
    plan["manual_template"]["hits"].append(prototype)
    if collection == "hit_events":
        plan["mechanics_model"][collection] = [{"hit_id": "manual", "action_id": "cast0", "order": 10}]
    elif collection == "hit_patterns":
        plan["mechanics_model"][collection] = [{"id": "manual.pattern", "prototype_hit_id": "manual", "action_id": "cast0",
                                                "offsets": [.2], "orders": [10]}]
    elif collection == "action_hit_links":
        plan["rotation_model"][collection] = [{"action_id": "cast0", "hit_id": "manual"}]
    else:
        # This valid non-damage application has no owned hit to detect indirectly.
        plan["mechanics_model"]["elemental_model"]["profiles"].append({"id": "manual.app", "element": "cryo",
            "application": "apply", "gauge_units": 1, "icd": {"kind": "none"}, "status": "assumed", "source": "工程"})
        plan["mechanics_model"][collection] = [{"id": "manual.application", "action_id": "cast0", "t": .2,
            "order": 10,
            "application": {"profile_id": "manual.app", "target_id": "enemy", "landed": True},
            "contact": {"kind": "profile", "profile_id": "safe"}}]
    validate_plan(plan)
    before = copy.deepcopy(plan)
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("conflict reached compose"))
    with pytest.raises(PlanError) as caught:
        assemble_attack_source(plan, request, metadata_db)
    assert caught.value.reason == "attack_source_assembly_conflict"
    assert plan == before
