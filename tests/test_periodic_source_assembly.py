"""Atomic multi-channel input assembly through actual small-DB composition."""
import copy
import json

import pytest
from periodic_assembly_fixtures import assembly_inputs
from test_periodic_actions import confirm_periodic
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import change, periodic_db  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.application_evidence import research_report_template
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.periodic_source_assembly import (
    MAX_REQUEST_BYTES,
    assemble_periodic_source,
    periodic_source_assembly_capabilities,
)
from genshin_app.equipment.schema import PlanError, validate_plan

# ruff: noqa: F811


def test_empty_native_real_three_actions_match_manual_calculation(periodic_db, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request, manual = assembly_inputs(periodic_db)
    for channel in request["channels"]:
        channel["self_sampling"] = "action_start"
    for channel in manual["action_event_profiles"][0]["channels"]:
        channel["self_sampling"] = "action_start"
    for hit in manual["manual_template"]["hits"]:
        hit["skill"] = "元素爆发"  # Compare equal display grouping as well as damage.
    before = copy.deepcopy((plan, request))
    validate_plan(plan)
    calls = []
    original = module.compose_equipment_plan

    def compose(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(module, "compose_equipment_plan", compose)
    out = assemble_periodic_source(plan, request, periodic_db)
    assert len(calls) == 1
    assert (plan, request) == before
    native, report, assembly = out["plan"], out["report"], out["assembly"]
    assert assembly["created"] == {"hits": 2, "profiles": 1, "bindings": 1, "sources": 1, "channels": 2}
    assert assembly["source_summary"]["status"] == "ready"
    assert assembly["source_summary"]["omitted_candidate_ids"] == []
    assert assembly["source_summary"]["whole_source_status"] == "partial"
    assert report["state"] == "pending"  # No acceptance is written by the helper.
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert native["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    assert native["mechanics_model"]["hit_patterns"] == []
    assert native["mechanics_model"]["elemental_model"]["profiles"] == []
    assert all(not ({"element", "multiplier"} & h.keys()) for h in native["manual_template"]["hits"])
    validate_plan(json.loads(json.dumps(native)))
    for key in ("rotation_model", "mechanics_model", "effect_bindings", "equipment_inputs", "rotation_tuning"):
        if key in plan:
            assert native[key] == plan[key]
    assert assembly["base_input_fingerprint"] == fingerprint(input_view(plan))
    assert assembly["input_fingerprint"] == fingerprint(input_view(native))

    generated = run_plan_calc(confirm_periodic(native, periodic_db), periodic_db)
    baseline = run_plan_calc(confirm_periodic(manual, periodic_db), periodic_db)
    assert len(generated["result"]["hits"]) == 15
    assert generated["result"]["summary"] == baseline["result"]["summary"]
    for field in ("t", "expected"):
        assert [h[field] for h in generated["result"]["hits"]] == [h[field] for h in baseline["result"]["hits"]]
    assert generated["report"]["action_event_compilations"][0]["profile_fingerprint"] == baseline["report"]["action_event_compilations"][0]["profile_fingerprint"]
    for pattern in generated["report"]["mechanics_model"]["hit_patterns"]:
        assert pattern["self_snapshot_event"] == pattern["action_id"]
    assert [(r["t"], r["allowed"]) for r in generated["result"]["elemental"]["trace"] if r["type"] == "elemental_application"] == [
        (r["t"], r["allowed"]) for r in baseline["result"]["elemental"]["trace"] if r["type"] == "elemental_application"]
    import sqlite3

    from genshin_app.equipment.service import result_bytes
    from genshin_app.formula_workbook import build_formula_workbook

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("offline delivery opened DB"))
    for fmt in ("json", "xlsx"):
        assert result_bytes(generated["result"], generated["result_receipt"], fmt)
    assert build_formula_workbook(generated)


def test_database_confirmed_sequence_identity_does_not_require_duplicate_plan_evidence(periodic_db):
    plan, request, _ = assembly_inputs(periodic_db)
    for channel in request["channels"]:
        channel.pop("verification")

    out = assemble_periodic_source(plan, request, periodic_db)

    assert out["assembly"]["source_summary"]["status"] == "ready"
    records = out["report"]["periodic_attack_compilations"][0]
    assert records["status"] == "ready"
    assert all("structured_verification" not in task["missing_fields"]
               for task in records["verification_tasks"])
    assert all(task["known_fields"]["counter_tag"]["status"] == "database_confirmed"
               for task in records["verification_tasks"])
    assert all(channel["application_identity"]["status"] == "database_confirmed"
               for channel in records["channels"])


def test_research_reports_round_trip_per_periodic_channel(periodic_db):
    plan, request, _ = assembly_inputs(periodic_db)
    for index, channel in enumerate(request["channels"], 1):
        report = research_report_template()
        report["actual_queries"] = [f"exact periodic component {index} attenuationTag"]
        report["remaining_unknowns"] = ["真实命中时点仍由校准合同负责"]
        channel["research_report"] = report

    first = assemble_periodic_source(plan, request, periodic_db)
    saved = first["plan"]["periodic_attack_sources"][0]["channels"]
    assert [row["research_report"] for row in saved] == [
        row["research_report"] for row in sorted(request["channels"], key=lambda row: row["candidate_id"])]
    assert all(row["research_report"]["execution_authority"] == "none" for row in saved)
    assert assemble_periodic_source(first["plan"], request, periodic_db)["assembly"]["status"] == "existing"
    without_reports = copy.deepcopy(request)
    for channel in without_reports["channels"]:
        channel.pop("research_report")
    with pytest.raises(PlanError):
        assemble_periodic_source(first["plan"], without_reports, periodic_db)


def test_request_reordering_and_existing_order_preserve_acceptance(periodic_db):
    plan, request, _ = assembly_inputs(periodic_db)
    second = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    second["id"] = "q.second"
    plan["rotation_model"]["abilities"].append(second)
    plan["rotation_model"]["actions"][1]["ability_id"] = second["id"]
    request["ability_ids"].append(second["id"])
    first = assemble_periodic_source(plan, request, periodic_db)
    reordered = copy.deepcopy(request)
    reordered["channels"].reverse()
    reordered["ability_ids"].reverse()
    other = assemble_periodic_source(plan, reordered, periodic_db)
    assert first["plan"] == other["plan"]
    assert first["assembly"] == other["assembly"]
    accepted = confirm_periodic(first["plan"], periodic_db)
    accepted["manual_template"]["hits"].reverse()
    accepted["action_event_profiles"][0]["channels"].reverse()
    accepted["periodic_attack_sources"][0]["channels"].reverse()
    accepted["action_event_bindings"].reverse()
    accepted["resolution"] = {"state": "ready", "untrusted_cached_note": "not a fresh report"}
    before = copy.deepcopy(accepted)
    out = assemble_periodic_source(accepted, reordered, periodic_db)
    assert out["plan"] == accepted == before
    assert out["assembly"]["status"] == "existing"
    assert not any(out["assembly"]["created"].values())
    assert out["report"]["state"] == "ready"
    assert "untrusted_cached_note" not in out["report"]


@pytest.mark.parametrize("case", ["origin_null", "unknown_origin", "unknown_candidate", "schedule_pending", "landed_unknown", "wrong_kind"])
def test_incomplete_source_keeps_whole_native_draft_without_events(periodic_db, case):
    plan, request, _ = assembly_inputs(periodic_db)
    if case == "origin_null":
        request["origin_reference_id"] = None
    elif case == "unknown_origin":
        request["origin_reference_id"] = "0" * 64
    elif case == "unknown_candidate":
        request["channels"][0]["candidate_id"] = "missing.candidate"
    elif case == "schedule_pending":
        request["channels"][0]["schedule"].update(first_offset_seconds=None, count=None, status="pending")
    elif case == "landed_unknown":
        request["channels"][0]["default_landed"] = None
    else:
        request["channels"][0]["schedule"] = copy.deepcopy(request["channels"][1]["schedule"])
    out = assemble_periodic_source(plan, request, periodic_db)
    assert out["report"]["state"] == "pending"
    assert len(out["plan"]["manual_template"]["hits"]) == len(request["channels"]) == 2
    assert out["report"]["action_event_compilations"][0]["generated"] == []
    assert out["plan"]["action_event_profiles"][0]["accepted_profile_fingerprint"] is None
    if case in ("origin_null", "unknown_origin"):
        assert out["assembly"]["source_summary"]["omitted_candidate_ids"] is None
        assert out["assembly"]["source_summary"]["omission_status"] == "not_evaluated"
    if case == "origin_null":
        assert out["assembly"]["source_summary"]["query_mode"] == "origin_discovery"
        assert out["plan"]["periodic_attack_sources"][0]["origin_reference_id"] is None


def test_selected_subset_discloses_omitted_candidate(periodic_db):
    plan, request, _ = assembly_inputs(periodic_db)
    omitted = request["channels"].pop()["candidate_id"]
    out = assemble_periodic_source(plan, request, periodic_db)
    assert len(out["plan"]["manual_template"]["hits"]) == 1
    assert out["assembly"]["source_summary"]["omitted_candidate_ids"] == [omitted]
    assert out["assembly"]["source_summary"]["omission_status"] == "known"
    assert out["assembly"]["source_summary"]["whole_source_status"] == "partial"


@pytest.mark.parametrize("change", ["hit", "calibration", "lifecycle", "source", "group", "less_channels", "less_bindings"])
def test_existing_edits_rejected_without_mutation(periodic_db, change):
    plan, request, _ = assembly_inputs(periodic_db)
    native = assemble_periodic_source(plan, request, periodic_db)["plan"]
    if change == "hit":
        native["manual_template"]["hits"][0]["hit_name"] = "用户改名"
    elif change == "calibration":
        request["channels"][0]["schedule"]["first_offset_seconds"] = .3
    elif change == "lifecycle":
        request["lifecycle"]["instance_end_offset_seconds"] = 1.3
    elif change == "source":
        request["source"] += " 修订"
    elif change == "group":
        request["channels"][0]["counter_tag"] = "other"
    elif change == "less_channels":
        request["channels"].pop()
    else:
        native["action_event_bindings"] = []
    before = copy.deepcopy(native)
    with pytest.raises(PlanError) as caught:
        assemble_periodic_source(native, request, periodic_db)
    assert caught.value.reason == "periodic_source_assembly_conflict"
    assert native == before


@pytest.mark.parametrize("collection", ["hit_events", "hit_patterns", "application_events", "action_hit_links"])
def test_manual_deliveries_rejected_before_compose(periodic_db, collection, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request, _ = assembly_inputs(periodic_db)
    plan["manual_template"]["hits"].append({"id": "manual", "damage_type_ids": ["damage.elemental_burst"],
        "t": .2, "character": "a", "skill": "既有", "damage_system": "classic", "element": "cryo", "multiplier": 1})
    if collection == "hit_events":
        plan["mechanics_model"][collection] = [{"hit_id": "manual", "action_id": "q0", "order": 10}]
    elif collection == "hit_patterns":
        plan["mechanics_model"][collection] = [{"id": "manual.pattern", "prototype_hit_id": "manual", "action_id": "q0", "offsets": [.2], "orders": [10]}]
    elif collection == "action_hit_links":
        plan["rotation_model"][collection] = [{"action_id": "q0", "hit_id": "manual"}]
    else:
        plan["mechanics_model"]["elemental_model"]["profiles"].append({"id": "manual.app", "element": "cryo", "application": "apply",
            "gauge_units": 1, "icd": {"kind": "none"}, "status": "assumed", "source": "工程"})
        plan["mechanics_model"][collection] = [{"id": "manual.application", "action_id": "q0", "t": .2, "order": 10,
            "application": {"profile_id": "manual.app", "target_id": "enemy", "landed": True}, "contact": {"kind": "profile", "profile_id": "safe"}}]
    validate_plan(plan)
    before = copy.deepcopy(plan)
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("conflict reached DB"))
    with pytest.raises(PlanError) as caught:
        assemble_periodic_source(plan, request, periodic_db)
    assert caught.value.reason == "periodic_source_assembly_conflict"
    assert plan == before


@pytest.mark.parametrize("field", ["id", "channel_id", "prototype_hit_id", "application_profile_id", "event_ids", "hit_ids"])
def test_generated_definitions_or_selector_occupancy_is_not_adopted(periodic_db, field, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request, _ = assembly_inputs(periodic_db)
    suffix = fingerprint(["source-periodic-assembly-v1", request["profile_id"], request["channels"][0]["candidate_id"]])
    value = "assembly.channel." + suffix if field == "channel_id" else "assembly.application." + suffix if field == "application_profile_id" else "assembly.hit." + suffix
    plan["manual_template"].setdefault("meta", {})["existing_reference"] = {field: [value] if field.endswith("ids") else value}
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("occupied ID reached DB"))
    with pytest.raises(PlanError) as caught:
        assemble_periodic_source(plan, request, periodic_db)
    assert caught.value.reason == "periodic_source_assembly_conflict"


def test_caps_are_isolated_and_schema_is_closed():
    caps = periodic_source_assembly_capabilities()
    schema = caps["request_schema"]
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert caps["auto_accept"] is False
    assert schema["properties"]["channels"]["items"]["properties"]["application_mode"]["enum"] == [
        "sequence", "no_icd", "no_application", "pending"]
    assert "periodic_attack_sources[].channels[].research_report" in caps["research_report_input"]
    schema["properties"]["channels"]["items"]["properties"]["candidate_id"].clear()
    assert periodic_source_assembly_capabilities()["request_schema"]["properties"]["channels"]["items"]["properties"]["candidate_id"]


@pytest.mark.parametrize("case", ["unknown", "missing", "bool", "scaling", "selection", "duplicate_ability",
                                  "duplicate_candidate", "null_candidate", "empty_candidate", "nine_channels",
                                  "huge", "nonfinite", "precision", "timer", "classification"])
def test_request_rejected_before_plan_or_database(case, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    evidence = {"status": "assumed", "source": "工程校准"}
    verification = {"status": "confirmed", "locator": "tests/test_periodic_source_assembly.py exact candidate",
        "version": "fixture-v1", "reviewed_at": "2026-10-18", "component_scope": "one selected periodic attack channel",
        "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]}
    request = {"schema_version": 1, "profile_id": "p", "ability_ids": ["q"], "version_line": "release",
        "selection": {"character_id": 1, "slot": "burst", "constellation": 0}, "origin_reference_id": None,
        "damage_type_ids": ["damage.elemental_burst"], "scaling_stat": "atk", **evidence,
        "lifecycle": {"establishment_offset_seconds": .1, "instance_end_offset_seconds": 2, "precision_seconds": .01,
            "owner_and_target_survive": True, "no_interrupt": True, "no_refresh_replacement_or_early_despawn": True, **evidence},
        "channels": [{"candidate_id": "periodic.known", "counter_tag": "shared", "application_mode": "sequence",
            "verification": verification, "contact_profile_id": "safe",
            "default_landed": True, "self_sampling": "action_start",
            "schedule": {"kind": "periodic", "first_offset_seconds": .2, "count": 4, "precision_seconds": .01, **evidence}}]}
    if case == "unknown":
        request["prepared_context"] = {}
    elif case == "missing":
        request.pop("lifecycle")
    elif case == "bool":
        request["schema_version"] = True
    elif case == "scaling":
        request["scaling_stat"] = "def"
    elif case == "selection":
        request["selection"]["skill_id"] = 1
    elif case == "duplicate_ability":
        request["ability_ids"] = ["q", "q"]
    elif case == "duplicate_candidate":
        request["channels"] *= 2
    elif case == "null_candidate":
        request["channels"][0]["candidate_id"] = None
    elif case == "empty_candidate":
        request["channels"][0]["candidate_id"] = ""
    elif case == "nine_channels":
        request["channels"] = [{**request["channels"][0], "candidate_id": f"c{i}"} for i in range(9)]
    elif case == "huge":
        request["source"] = "中" * MAX_REQUEST_BYTES
    elif case == "nonfinite":
        request["lifecycle"]["instance_end_offset_seconds"] = float("nan")
    elif case == "precision":
        request["channels"][0]["schedule"]["first_offset_seconds"] = .0000001
    elif case == "timer":
        request["channels"][0]["schedule"]["interval"] = .25
    else:
        request["damage_type_ids"] = ["damage.elemental_skill"]
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("invalid request reached DB"))
    with pytest.raises(PlanError) as caught:
        assemble_periodic_source({}, request, "does-not-exist.db")
    assert caught.value.reason.startswith("periodic_source_assembly_")


@pytest.mark.parametrize("case", ["contact", "missing_ability", "variant", "actor", "type", "source_identity"])
def test_invalid_parent_scope_rejected_without_mutation(periodic_db, case, monkeypatch):
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request, _ = assembly_inputs(periodic_db)
    if case == "contact":
        request["channels"][1]["contact_profile_id"] = "missing.contact"
    elif case == "missing_ability":
        request["ability_ids"] = ["missing.ability"]
    elif case == "variant":
        plan["rotation_model"]["abilities"][0].pop("variant")
    elif case == "actor":
        extra = copy.deepcopy(plan["rotation_model"]["abilities"][0])
        extra.update(id="other", character="different")
        plan["rotation_model"]["abilities"].append(extra)
        request["ability_ids"].append("other")
    elif case == "type":
        plan["rotation_model"]["abilities"][0]["action_type_id"] = "action.elemental_skill"
    else:
        plan["equipment_inputs"]["a"] = {"mode": "panel", "panel_policy": "static_included", "weapon": None,
            "artifact_sets": [], "source_refs": [], "data_version": None,
            "character_ref": {"db_character_id": 7, "version_line": "release"}}
    before = copy.deepcopy(plan)
    monkeypatch.setattr(module, "compose_equipment_plan", lambda *a: pytest.fail("scope conflict reached DB"))
    with pytest.raises(PlanError):
        assemble_periodic_source(plan, request, periodic_db)
    assert plan == before


def test_database_source_change_keeps_old_acceptance_but_cannot_execute(periodic_db, fake_repo):
    plan, request, _ = assembly_inputs(periodic_db)
    accepted = confirm_periodic(assemble_periodic_source(plan, request, periodic_db)["plan"], periodic_db)
    old_fp = accepted["action_event_profiles"][0]["accepted_profile_fingerprint"]
    change(periodic_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json",
           lambda rows: rows[0]["paramList"].__setitem__(0, 5.0))
    out = assemble_periodic_source(accepted, request, periodic_db)
    assert out["assembly"]["status"] == "existing"
    assert out["plan"]["action_event_profiles"][0]["accepted_profile_fingerprint"] == old_fp
    assert out["report"]["state"] == "pending"
    assert out["report"]["action_event_compilations"][0]["generated"] == []
    with pytest.raises(PlanError):
        run_plan_calc(out["plan"], periodic_db)


def test_group_budget_reports_unknown_omission_and_no_execution(periodic_db, monkeypatch):
    from genshin_app.equipment import periodic_sources

    plan, request, _ = assembly_inputs(periodic_db)
    monkeypatch.setattr(periodic_sources, "MAX_RECEIPT_BYTES", 1024)
    out = assemble_periodic_source(plan, request, periodic_db)
    assert out["report"]["state"] == "pending"
    assert out["assembly"]["source_summary"]["omitted_candidate_ids"] is None
    assert out["report"]["action_event_compilations"][0]["generated"] == []
