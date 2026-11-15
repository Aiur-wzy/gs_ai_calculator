"""Source-backed finite dispatch; synthetic timings, not a claimed game rotation."""
import copy

import pytest
from test_freeze_runtime import model as freeze_model
from test_mechanics_execution import EVIDENCE, action, fixture
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import chosen, periodic_db, query  # noqa: F401

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.action_event_schema import _check
from genshin_app.equipment.application_evidence import (
    RESEARCH_REPORT,
    research_report_template,
)
from genshin_app.equipment.periodic_schema import periodic_attack_capabilities
from genshin_app.equipment.schema import PlanError

# ruff: noqa: F811


def test_research_report_template_is_directly_persistable_and_has_no_execution_authority():
    template = research_report_template()
    assert _check(template, RESEARCH_REPORT, "research_report") == []
    assert template["report_version"] == 1 and template["execution_authority"] == "none"


def test_periodic_capabilities_expose_saved_per_channel_research_report_without_execution_authority():
    capabilities = periodic_attack_capabilities()
    includes = capabilities["verification_task"]["includes"]
    channel_schema = capabilities["schema"]["items"]["properties"]["channels"]["items"]
    report_schema = channel_schema["properties"]["research_report"]
    assert "research_report" in includes and "research_report_template" in includes
    assert capabilities["research_report_contract"]["saved_field"] == (
        "periodic_attack_sources[].channels[].research_report"
    )
    assert capabilities["research_report_contract"]["execution_authority"] == "none"
    assert report_schema["properties"]["execution_authority"] == {"enum": ["none"]}


def periodic_plan(db):
    template, rotation, mechanics = fixture((0,))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    template["loop_duration"] = 22
    plan = enable_mechanics(new_plan(template))
    base = copy.deepcopy(plan["manual_template"]["hits"][0])
    base.pop("multiplier")
    base.pop("element")
    base.update(scaling_stat="atk", damage_type_ids=["damage.elemental_burst"])
    plan["manual_template"]["hits"] = [{**base, "id": name} for name in ("slice", "blast")]
    rotation.update(period=22, max_cycles=3)
    rotation["abilities"][0].update(action_type_id="action.elemental_burst", variant="default", energy={"required": 0, "amount": 0})
    rotation["actions"] = [action(f"q{i}", start=i*10, end=i*10+.15, repeat={"mode": "once"}) for i in range(3)]
    rotation["events"] = [{"id": f"spend{i}", "type": "energy_spend", "action_id": f"q{i}", "character": "a",
        "t": i*10, "order": 1, "required": 0, "amount": 0, "repeat": {"mode": "once"}, **EVIDENCE} for i in range(3)]
    mechanics["hit_events"] = []
    elemental = mechanics["elemental_model"] = freeze_model()
    elemental["profiles"] = []
    elemental["contact_profiles"] = [{"id": "safe", "freeze_interaction": "none", **EVIDENCE}]
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    origin, ids = chosen(db)
    selected = query(db, origin_reference_id=origin, candidate_ids=ids)["selected_candidates"]
    by_kind = {c["channel_kind"]: c["candidate_id"] for c in selected}
    channels = [{"channel_id": "slice", "prototype_hit_id": "slice", "application_profile_id": "slice.app", "contact_profile_id": "safe",
        "schedule": {"kind": "periodic", "first_offset_seconds": .2, "count": 4, "precision_seconds": .01, **EVIDENCE}, "default_landed": True, "self_sampling": "current"},
        {"channel_id": "blast", "prototype_hit_id": "blast", "application_profile_id": "blast.app", "contact_profile_id": "safe",
        "schedule": {"kind": "single", "offset_seconds": 1.1, "precision_seconds": .01, **EVIDENCE}, "default_landed": True, "self_sampling": "current"}]
    plan["action_event_profiles"] = [{"id": "q.profile", "kind": "calibrated_multi_contact_v1", "variant": "default",
        "lifecycle": {"establishment_offset_seconds": .1, "instance_end_offset_seconds": 1.2, "precision_seconds": .01,
            "owner_and_target_survive": True, "no_interrupt": True, "no_refresh_replacement_or_early_despawn": True, **EVIDENCE},
        "channels": channels, "accepted_profile_fingerprint": None}]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "q.profile"}]
    plan["action_event_overrides"] = []
    verification = {"status": "confirmed", "locator": "tests/test_periodic_actions.py exact channel fixture",
        "version": "fixture-v1", "reviewed_at": "2026-10-18", "component_scope": "one selected periodic attack channel",
        "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]}
    plan["periodic_attack_sources"] = [{"action_event_profile_id": "q.profile", "version_line": "release",
        "selection": {"character_id": 90000001, "slot": "burst", "constellation": 0}, "origin_reference_id": origin,
        "channels": [{"channel_id": "slice", "candidate_id": by_kind["periodic_callback_attack"],
                      "counter_tag": "Elemental_Burst", "application_mode": "sequence", "verification": copy.deepcopy(verification)},
                     {"channel_id": "blast", "candidate_id": by_kind["ability_start_attack"],
                      "counter_tag": "Elemental_Burst", "application_mode": "sequence", "verification": copy.deepcopy(verification)}],
        "purpose": "calibrated_periodic_attack", **EVIDENCE}]
    return plan


def confirm_periodic(plan, db):
    report = compose_equipment_plan(plan, db)
    records = report["action_event_compilations"]
    for record in records:
        assert record["required_confirmation"], report["missing_inputs"]
        next(p for p in plan["action_event_profiles"] if p["id"] == record["profile_id"]).update(record["required_confirmation"])
    return plan


def periodic_application_mode(plan, mode):
    for channel in plan["periodic_attack_sources"][0]["channels"]:
        channel["application_mode"] = mode
        if mode != "sequence":
            channel.pop("counter_tag", None)
            channel["verification"]["confirmed_fields"] = ["application_mode", "shared_scope"]
    return plan


def test_three_explicit_actions_generate_fifteen_hits_with_one_acceptance(periodic_db):
    plan = periodic_plan(periodic_db)
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert report["periodic_attack_compilations"][0]["status"] == "ready", report["missing_inputs"]
    for elemental_record in report["elemental_compilations"]:
        uses = elemental_record["verification_task"]["uses"]
        assert uses and all(use["actor"] == "a" and use["character_id"] == 90000001
                            and use["slot"] == "burst" and use["candidate_id"]
                            and use["version_line"] == "release" for use in uses)
    assert plan == before
    confirm_periodic(plan, periodic_db)
    output = run_plan_calc(plan, periodic_db)
    assert output["result"]["summary"]["total_damage"] == pytest.approx(1500.889481306076)
    assert [h["t"] for h in output["result"]["hits"]] == [i*10+x for i in range(3) for x in (.2,.45,.7,.95,1.1)]
    assert len(output["report"]["mechanics_model"]["hit_patterns"]) == 15
    assert output["plan"]["mechanics_model"]["hit_patterns"] == []
    assert output["plan"]["mechanics_model"]["elemental_model"]["profiles"] == []
    assert all(not ({"element", "multiplier"} & h.keys()) for h in output["plan"]["manual_template"]["hits"])


def test_one_miss_keeps_generated_identity_without_damage_or_application(periodic_db):
    plan = confirm_periodic(periodic_plan(periodic_db), periodic_db)
    plan["action_event_overrides"] = [{"kind": "calibrated_multi_contact_v1", "action_id": "q1",
        "contacts": [{"channel_id": "slice", "ordinal": 2, "landed": False, **EVIDENCE}]}]
    out = run_plan_calc(plan, periodic_db)
    assert len(out["result"]["hits"]) == 14
    record = out["result"]["action_event_compilations"][0]
    assert record["stats"] == {"matched_actions": 3, "planned_hits": 15, "generated_hits": 15, "landed_hits": 14, "missed_hits": 1}


@pytest.mark.parametrize("mode", ["no_icd", "no_application"])
def test_periodic_application_modes_keep_damage_and_do_not_create_sequence_counter(periodic_db, mode):
    plan = periodic_application_mode(periodic_plan(periodic_db), mode)
    first = compose_equipment_plan(plan, periodic_db)
    record = first["periodic_attack_compilations"][0]
    assert record["status"] == "ready", first["missing_inputs"]
    assert all(channel["compiled_fields"]["application_mode"] == mode for channel in record["channels"])
    assert all(channel["compiled_fields"]["counter_tag"] is None
               and channel["compiled_fields"]["rule_group"] is None for channel in record["channels"])
    out = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    assert len(out["result"]["hits"]) == 15
    applications = [row for row in out["result"]["elemental"]["trace"]
                    if row["type"] == "elemental_application"]
    if mode == "no_icd":
        assert len(applications) == 15 and all(row["allowed"] for row in applications)
    else:
        assert len(applications) == 15
        assert all(not row["allowed"] and row["reason"] == "no_application" for row in applications)


def test_periodic_nonsequence_mode_requires_structured_per_channel_evidence(periodic_db):
    plan = periodic_application_mode(periodic_plan(periodic_db), "no_application")
    plan["periodic_attack_sources"][0]["channels"][0].pop("verification")
    report = compose_equipment_plan(plan, periodic_db)
    record = report["periodic_attack_compilations"][0]
    assert report["state"] == "pending" and record["status"] == "pending"
    task = next(task for task in record["verification_tasks"]
                if task["attack_identity"]["channel_id"] == "slice")
    assert "structured_verification" in task["missing_fields"]
    assert "不尝试元素附着" in "".join(task["targeted_research_questions"])
    assert task["research_report_template"] == {
        "report_version": 1, "execution_authority": "none", "actual_queries": [],
        "sources_checked": [], "adopted_findings": [], "rejected_findings": [],
        "remaining_unknowns": [], "database_patch_plan": [],
    }


def test_verified_no_application_uses_damage_parameter_when_gauge_projection_is_unavailable(
        periodic_db, fake_repo):
    from tests.test_runtime_periodic_attack import SOURCE, change

    def invalidate_gauge(rows):
        def objects(value):
            if isinstance(value, dict):
                yield value
                for child in value.values():
                    yield from objects(child)
            elif isinstance(value, list):
                for child in value:
                    yield from objects(child)
        holders = [obj for obj in objects(rows[2]["Default"]) if "KOLOHIDMHHE" in obj]
        assert len(holders) == 1
        holders[0]["DBGOKAKAHFP"] = 24

    change(periodic_db, fake_repo, SOURCE, invalidate_gauge)
    plan = periodic_application_mode(periodic_plan(periodic_db), "no_application")
    report = compose_equipment_plan(plan, periodic_db)
    assert report["periodic_attack_compilations"][0]["status"] == "ready", report["missing_inputs"]
    bad = next(channel for channel in report["periodic_attack_compilations"][0]["channels"]
               if channel["projection"]["gauge"]["status"] != "ready")
    assert bad["compiled_fields"]["application_mode"] == "no_application"
    out = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    assert len(out["result"]["hits"]) == 15
    applications = [row for row in out["result"]["elemental"]["trace"]
                    if row["type"] == "elemental_application"]
    assert len(applications) == 15
    assert all(not row["allowed"] and row["reason"] == "no_application" for row in applications)


@pytest.mark.parametrize("raw_mode", ["pending", "unknown", None])
def test_periodic_pending_and_legacy_unknown_expose_concrete_work_orders(periodic_db, raw_mode):
    plan = periodic_plan(periodic_db)
    for channel in plan["periodic_attack_sources"][0]["channels"]:
        channel.pop("counter_tag")
        channel.pop("verification")
        if raw_mode is None:
            channel.pop("application_mode")
        else:
            channel["application_mode"] = raw_mode
    report = compose_equipment_plan(plan, periodic_db)
    record = report["periodic_attack_compilations"][0]
    assert report["state"] == "pending" and record["status"] == "pending"
    assert len(record["verification_tasks"]) == 2
    for task in record["verification_tasks"]:
        identity = task["attack_identity"]
        assert identity["actor"] == "a" and identity["character_id"] == 90000001
        assert identity["action_event_profile_id"] == "q.profile"
        assert identity["prototype_hit_id"] and identity["application_profile_id"]
        assert identity["constellation"] == 0
        assert "application_mode" in task["missing_fields"]
        assert "sequence、no_icd、no_application" in "".join(task["targeted_research_questions"])


@pytest.mark.parametrize("field,value", [("instance_end_offset_seconds", 2), ("establishment_offset_seconds", .16), ("owner_and_target_survive", None)])
def test_invalid_lifecycle_never_produces_partial_events(periodic_db, field, value):
    plan = periodic_plan(periodic_db)
    plan["action_event_profiles"][0]["lifecycle"][field] = value
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert report["action_event_compilations"][0]["generated"] == []
    with pytest.raises(PlanError):
        run_plan_calc(plan, periodic_db)


def test_discovery_draft_keeps_native_ownership_without_partial_materialization(periodic_db):
    plan = periodic_plan(periodic_db)
    plan["periodic_attack_sources"][0]["origin_reference_id"] = None
    plan["periodic_attack_sources"][0]["channels"][1]["candidate_id"] = None
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert report["periodic_attack_compilations"][0]["query"]["mode"] == "origin_discovery"
    assert report["action_event_compilations"][0]["generated"] == []
    assert report["plan"]["mechanics_model"]["elemental_model"]["profiles"] == []
    assert all("element" not in h for h in plan["manual_template"]["hits"])


def test_result_export_has_full_sources_and_never_opens_database(periodic_db, monkeypatch):
    import sqlite3

    from genshin_app.equipment.service import result_bytes
    from genshin_app.formula_workbook import build_formula_workbook

    out = run_plan_calc(confirm_periodic(periodic_plan(periodic_db), periodic_db), periodic_db)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("offline export opened DB"))
    for fmt in ("json", "csv", "xlsx"):
        assert result_bytes(out["result"], out["result_receipt"], fmt)
    assert build_formula_workbook(out)


@pytest.mark.parametrize("mutation", ["raw", "fields", "generated", "lifecycle", "pattern", "delete_source"])
def test_tampered_source_dispatch_or_native_cannot_export(periodic_db, mutation):
    from genshin_app.equipment.periodic_receipt import validate_periodic_receipt

    out = run_plan_calc(confirm_periodic(periodic_plan(periodic_db), periodic_db), periodic_db)
    if mutation == "raw":
        out["result"]["periodic_attack_compilations"][0]["query"]["source_graph"]["creation_bundle"]["origin"]["operation"]["raw"]["injected"] = True
    elif mutation == "fields":
        out["result"]["periodic_attack_compilations"][0]["channels"][0]["compiled_fields"]["multiplier"] *= 2
    elif mutation == "generated":
        out["result"]["action_event_compilations"][0]["generated"][0]["t"] += .1
    elif mutation == "lifecycle":
        out["plan"]["action_event_profiles"][0]["lifecycle"]["instance_end_offset_seconds"] += .1
    elif mutation == "pattern":
        out["report"]["mechanics_model"]["hit_patterns"][0]["offsets"][0] += .1
    else:
        del out["result"]["periodic_attack_compilations"]
    with pytest.raises(ValueError):
        validate_periodic_receipt(out["plan"], out["result"], out["report"], out["result_receipt"])


def test_dispatch_receipt_budget_withdraws_all_generated_hits(periodic_db, monkeypatch):
    import genshin_app.equipment.action_events as events

    plan = confirm_periodic(periodic_plan(periodic_db), periodic_db)
    monkeypatch.setattr(events, "MAX_RECEIPT_BYTES", 1024)
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert report["action_event_compilations"][0]["generated"] == []
    assert report["plan"]["mechanics_model"]["hit_patterns"] == []
