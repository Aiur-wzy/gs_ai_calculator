"""Native action-dispatch declarations reach the real source/event compilers."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.action_dispatch_sources import action_dispatch_execution_plan
from genshin_app.equipment.composer import prepare_character_input
from genshin_app.equipment.periodic_receipt import validate_periodic_receipt
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_db.runtime_catalog import fingerprint
from genshin_db.runtime_queries import query_runtime
from tests.test_attack_metadata_app import metadata_native
from tests.test_elemental_swirl import swirl_model
from tests.test_periodic_application_v2 import dynamic_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_attack_metadata import candidate, metadata_db  # noqa: F401
from tests.test_runtime_periodic_attack import chosen, periodic_db  # noqa: F401

# ruff: noqa: F811

H2 = "2" * 64


def _ref(component):
    return {"kind": "calibration_record", "reference": "fixture://dispatch/" + component,
            "fingerprint": H2}


def _evidence(component, category="database_proven"):
    return {"status": "confirmed", "category": category, "source_refs": [_ref(component)]}


def _verification(component, *, sequence=True):
    return {"status": "confirmed", "locator": "fixture://dispatch/application/" + component,
        "version": "fixture-reviewed-v1", "reviewed_at": "2026-10-19", "component_scope": component,
        "confirmed_fields": (["application_mode", "counter_tag", "shared_scope"] if sequence else
                             ["application_mode", "shared_scope"])}


def _dataset(snapshot):
    return {"version_line": snapshot["version_line"], "upstream_commit": snapshot["upstream_commit"],
        "normalization_profile_id": snapshot["normalization"]["profile_id"], "snapshot_id": None}


def _bundle(*profiles):
    return {"schema_version": 1, "profile_version": 1, "contract_id": "action-dispatch-profile-v1",
            "profiles": list(profiles)}


def _declaration(profile, ability_id):
    selector = copy.deepcopy(profile["selector"])
    version_line = selector.pop("version_line")
    return {"ability_id": ability_id, "version_line": version_line, "selector": selector,
        "profile_id": profile["profile_id"], "expected_profile_fingerprint": fingerprint(profile),
        "scenario_calibrations": [], "status": "confirmed", "source": "已审动作派发档案夹具"}


def _single_profile(db):
    found = candidate(db)
    discovered = query_runtime(db, {"action": "attack_metadata_profile", "character_id": 90000001,
        "slot": "skill", "constellation": 0, "talent_level": 10,
        "candidate_id": found["candidate_id"]})
    exact = discovered["selection"]
    query = query_runtime(db, {"action": "attack_metadata_profile", "character_id": exact["character_id"],
        "skill_depot_id": exact["skill_depot_id"], "skill_id": exact["skill_id"],
        "constellation": 0, "talent_level": 10, "candidate_id": found["candidate_id"]})
    selection = query["selection"]
    component = "primary"
    profile = {"profile_id": "fixture.dispatch.single", "status": "confirmed",
        "selector": {"version_line": "release", "character_id": selection["character_id"],
            "skill_depot_id": selection["skill_depot_id"], "skill_id": selection["skill_id"],
            "slot": "skill", "effective_level": 10, "constellation": 0,
            "variant": "tap", "cast_stage": None},
        "ruleset": "gauge-swirl-dynamic-v3",
        "emissions": [{"component_id": component, "kind": "fixed",
            "attack_source": {"kind": "attack_metadata", "candidate_id": found["candidate_id"],
                "origin_reference_id": None, "source_fingerprint": query["source_fingerprint"]},
            "prototype": {"damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic",
                "scaling_stat": "atk", "count": 1, "skill_label": "工程技能",
                "hit_name": "首段命中", "description": "通用单次派发集成夹具"},
            "application": {"mode": "sequence", "counter_tag": "declared.metadata.shared",
                "verification": _verification(component)},
            "schedule": {"offset_seconds": .2, "precision_seconds": .01},
            "landed": {"policy": "default", "default": True}, "self_sampling": "current",
            "evidence": {name: _evidence(component, "calibration_required" if name == "timing" else
                                          "database_proven")
                         for name in ("dispatch", "timing", "damage", "application")}}],
        "lifecycle": {"establishment_offset_seconds": None, "instance_end_offset_seconds": None,
            "precision_seconds": None, "owner_and_target_survive": None, "no_interrupt": None,
            "no_refresh_replacement_or_early_despawn": None,
            "evidence": {"status": "pending", "category": "scenario_required", "source_refs": []}},
        "dataset": _dataset(query["snapshot"]),
        "review": {"status": "reviewed", "date": "2026-10-19", "reviewer": "integration-fixture",
            "notes": "Synthetic generic integration profile."}}
    return profile


def _single_plan(db, profile):
    plan = metadata_native(db)
    for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides",
                "attack_metadata_sources"):
        plan.pop(key, None)
    plan["manual_template"]["hits"] = []
    elemental = swirl_model("cryo")
    elemental["ruleset"] = "gauge-swirl-dynamic-v3"
    elemental["profiles"] = []
    plan["mechanics_model"]["elemental_model"] = elemental
    prepared = prepare_character_input(db, 90000001, 90)
    plan["equipment_inputs"]["a"] = {
        "mode": "panel", "panel_policy": "static_included", "panel_basis_mode": "database",
        "character_ref": prepared["character_ref"], "weapon": None, "artifact_sets": [],
        "source_refs": [{"kind": "manual", "note": "工程夹具沿用模板给定面板"}],
        "data_version": prepared["data_version"],
    }
    plan["action_dispatch_sources"] = [_declaration(profile, "e")]
    return plan


def _install(monkeypatch, *profiles):
    import genshin_db.runtime_action_dispatch as runtime
    bundle = _bundle(*profiles)
    monkeypatch.setattr(runtime, "load_action_dispatch_bundle", lambda: copy.deepcopy(bundle))


def test_schema_rejects_duplicate_abilities_and_empty_scenario_rows(metadata_db):
    profile = _single_profile(metadata_db)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"].append(copy.deepcopy(plan["action_dispatch_sources"][0]))
    with pytest.raises(PlanError, match="每个能力最多"):
        validate_plan(plan)
    plan["action_dispatch_sources"] = [plan["action_dispatch_sources"][0]]
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{"component_id": "primary"}]
    with pytest.raises(PlanError, match="至少声明"):
        validate_plan(plan)


def test_single_dispatch_source_automatically_materializes_and_runs(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    before = copy.deepcopy(plan)
    output = run_plan_calc(plan, metadata_db)
    assert plan == before
    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    assert output["report"]["action_dispatch_compilations"][0]["status"] == "ready"
    assert len(output["report"]["action_dispatch_automatic_confirmations"]) == 1
    action = output["report"]["action_event_compilations"][0]
    assert action["status"] == "ready" and action["stats"]["generated_hits"] == 3
    assert len(output["result"]["hits"]) == 3
    audit = output["report"]["action_automation_audit"]["summary"]
    assert audit["automatic_actions"] == 3
    assert audit["reviewed_dispatch_actions"] == 3
    assert audit["reusable_profile_actions"] == 0
    assert audit["unique_dispatch_profile_decisions"] == 1
    assert audit["unique_reusable_profile_decisions"] == 0
    assert audit["manual_hit_rows"] == 0
    assert audit["manual_per_instance_hit_rows"] == 0
    assert audit["reusable_prototype_hit_rows"] == 0
    assert output["report"]["action_automation_audit"]["dispatch_manifest"]["status"] == "complete"
    native = output["plan"]
    assert "action_dispatch_sources" in native
    assert not native.get("action_event_profiles") and not native["manual_template"]["hits"]
    manifest = output["report"]["action_dispatch_materialization"]
    assert manifest["contract_id"] == "action-dispatch-materialization-v1"
    assert manifest["prototype_hits"][0]["character"] == "a"
    assert output["result_receipt"]["attack_metadata"][
        "action_dispatch_materialization_fingerprint"] == manifest["content_fingerprint"]
    assert output["result_receipt"]["attack_metadata"]["input_sources_fingerprint"] == fingerprint(
        manifest["attack_metadata_sources"])
    resolved = native["resolved_template"]
    assert len(resolved["hits"]) == 3
    assert all(hit["character"] == "a" and hit["damage_type_ids"] == ["damage.elemental_skill"]
               for hit in resolved["hits"])


def test_zero_fixed_offset_runs_immediately_after_its_parent_action(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    profile["emissions"][0]["schedule"]["offset_seconds"] = 0
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)

    output = run_plan_calc(plan, metadata_db)

    actions = {row["id"]: row for row in plan["rotation_model"]["actions"]}
    generated = output["report"]["action_event_compilations"][0]["generated"]
    assert generated
    assert all(row["t"] == actions[row["action_id"]]["start"] for row in generated)
    assert all(row["order"] == actions[row["action_id"]]["order"] + 1 for row in generated)


def _multi_profile(db):
    origin, ids = chosen(db)
    discovered = query_runtime(db, {"action": "periodic_attack_profile", "character_id": 90000001,
        "slot": "burst", "constellation": 0, "talent_level": 10,
        "origin_reference_id": origin, "candidate_ids": ids})
    exact = discovered["selection"]
    query = query_runtime(db, {"action": "periodic_attack_profile", "character_id": exact["character_id"],
        "skill_depot_id": exact["skill_depot_id"], "skill_id": exact["skill_id"],
        "constellation": 0, "talent_level": 10,
        "origin_reference_id": origin, "candidate_ids": ids})
    selection = query["selection"]
    candidates = {row["channel_kind"]: row for row in query["selected_candidates"]}
    specs = [
        ("slice", "periodic_callback_attack", "periodic",
         {"first_offset_seconds": .2, "interval_seconds": .25, "count": 4,
          "precision_seconds": .01}),
        ("blast", "ability_start_attack", "fixed", {"offset_seconds": 1.1, "precision_seconds": .01}),
    ]
    emissions = []
    for component, channel_kind, kind, schedule in specs:
        row = candidates[channel_kind]
        emissions.append({"component_id": component, "kind": kind,
            "attack_source": {"kind": "periodic_attack", "candidate_id": row["candidate_id"],
                "origin_reference_id": origin, "source_fingerprint": query["source_fingerprint"]},
            "prototype": {"damage_type_ids": ["damage.elemental_burst"], "damage_system": "classic",
                "scaling_stat": "atk", "count": 1, "skill_label": "工程爆发",
                "hit_name": component, "description": "通用多通道派发集成夹具"},
            "application": {"mode": "sequence", "counter_tag": "Elemental_Burst",
                "verification": _verification(component)},
            "schedule": schedule, "landed": {"policy": "default", "default": True},
            "self_sampling": "current",
            "evidence": {name: _evidence(component, "calibration_required" if name == "timing" else
                                          "database_proven")
                         for name in ("dispatch", "timing", "damage", "application")}})
    return {"profile_id": "fixture.dispatch.multi", "status": "confirmed",
        "selector": {"version_line": "release", "character_id": selection["character_id"],
            "skill_depot_id": selection["skill_depot_id"], "skill_id": selection["skill_id"],
            "slot": "burst", "effective_level": 10, "constellation": 0,
            "variant": "default", "cast_stage": None},
        "ruleset": "gauge-swirl-dynamic-v3", "emissions": emissions,
        "lifecycle": {"establishment_offset_seconds": .1, "instance_end_offset_seconds": 1.2,
            "precision_seconds": .01, "owner_and_target_survive": True, "no_interrupt": True,
            "no_refresh_replacement_or_early_despawn": True,
            "evidence": _evidence("lifecycle", "calibration_required")},
        "dataset": _dataset(query["snapshot"]),
        "review": {"status": "reviewed", "date": "2026-10-19", "reviewer": "integration-fixture",
            "notes": "Synthetic generic multi-channel integration profile."}}


def _multi_plan(db, profile):
    plan = dynamic_plan(db)
    for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides",
                "periodic_attack_sources"):
        plan.pop(key, None)
    plan["manual_template"]["hits"] = []
    prepared = prepare_character_input(db, 90000001, 90)
    plan["equipment_inputs"]["a"] = {
        "mode": "panel", "panel_policy": "static_included", "panel_basis_mode": "database",
        "character_ref": prepared["character_ref"], "weapon": None, "artifact_sets": [],
        "source_refs": [{"kind": "manual", "note": "工程夹具沿用模板给定面板"}],
        "data_version": prepared["data_version"],
    }
    plan["action_dispatch_sources"] = [_declaration(profile, "e")]
    return plan


def test_multi_dispatch_source_materializes_all_channels_and_period(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)
    output = run_plan_calc(plan, periodic_db)
    record = output["report"]["action_event_compilations"][0]
    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    assert record["kind"] == "calibrated_multi_application_v2"
    assert record["stats"]["generated_hits"] == 15
    assert len(output["result"]["hits"]) == 15
    dispatch = output["report"]["action_dispatch_compilations"][0]
    receipts = dispatch["source_verification"]["receipts"]
    assert {row["component_id"] for row in receipts} == {"slice", "blast"}
    assert next(row for row in receipts if row["component_id"] == "slice")["interval_seconds"] == .25
    assert not output["plan"].get("periodic_attack_sources")
    assert len(output["report"]["action_dispatch_materialization"]["periodic_attack_sources"]) == 1
    assert output["result_receipt"]["periodic_attacks"][
        "action_dispatch_materialization_fingerprint"] == output["report"][
            "action_dispatch_materialization"]["content_fingerprint"]


def test_zero_periodic_first_offset_runs_with_zero_establishment(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    periodic = next(row for row in profile["emissions"] if row["kind"] == "periodic")
    periodic["schedule"]["first_offset_seconds"] = 0
    profile["lifecycle"]["establishment_offset_seconds"] = 0
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)

    output = run_plan_calc(plan, periodic_db)

    actions = {row["id"]: row for row in plan["rotation_model"]["actions"]}
    generated = output["report"]["action_event_compilations"][0]["generated"]
    first_contacts = [row for row in generated
                      if row["channel_id"] == periodic["component_id"] and row["ordinal"] == 1]
    assert len(first_contacts) == len(actions)
    assert all(row["t"] == actions[row["action_id"]]["start"] for row in first_contacts)
    spends = {row["action_id"]: row for row in plan["rotation_model"]["events"]
              if row["type"] == "energy_spend"}
    assert all(row["order"] == max(actions[row["action_id"]]["order"],
                                   spends[row["action_id"]]["order"]) + 1
               for row in first_contacts)


def test_one_stale_peer_withdraws_every_generated_fragment(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    alias = {**copy.deepcopy(plan["rotation_model"]["abilities"][0]), "id": "e.alias"}
    plan["rotation_model"]["abilities"].append(alias)
    action = {**copy.deepcopy(plan["rotation_model"]["actions"][0]), "id": "cast.alias",
              "ability_id": "e.alias", "start": 1.0, "occupation_end": 1.2, "order": 10}
    plan["rotation_model"]["actions"].append(action)
    stale = _declaration(profile, "e.alias")
    stale["expected_profile_fingerprint"] = "9" * 64
    plan["action_dispatch_sources"].append(stale)
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert {row["status"] for row in report["action_dispatch_compilations"]} == {"pending"}
    assert not report.get("action_event_compilations")
    assert report["plan"]["manual_template"]["hits"] == []
    assert not report["plan"].get("action_event_profiles")
    assert any(item["code"] == "action_dispatch_source_pending" for item in report["missing_inputs"])


def test_cast_stage_and_scenario_order_are_explicit_pending(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    profile["selector"]["cast_stage"] = 2
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0] = _declaration(profile, "e")
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert not report.get("action_event_compilations")
    assert "action_dispatch_cast_stage_unrepresentable" in report["action_dispatch_compilations"][0][
        "issues"][0]["message"]

    profile["selector"]["cast_stage"] = None
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": "primary", "order": 7}]
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert not report.get("action_event_compilations")
    assert "action_dispatch_order_binding_required" in report["action_dispatch_compilations"][0][
        "issues"][0]["message"]


def test_missing_independent_character_ref_is_pending(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["equipment_inputs"].pop("a")
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert not report.get("action_event_compilations")
    assert report["plan"]["manual_template"]["hits"] == []
    assert "character_ref" in report["action_dispatch_compilations"][0]["issues"][0]["message"]


def test_scenario_landed_requires_an_unambiguous_action_instance(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    profile["emissions"][0]["landed"] = {"policy": "scenario_required", "default": None}
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0] = _declaration(profile, "e")
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": "primary", "landed": True}]
    assert sum(action["ability_id"] == "e" for action in plan["rotation_model"]["actions"]) > 1
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert not report.get("action_event_compilations")
    assert report["plan"]["manual_template"]["hits"] == []
    assert "多实例需要逐action/ordinal绑定" in report[
        "action_dispatch_compilations"][0]["issues"][0]["message"]


def test_empty_catalog_is_explicit_pending(metadata_db, monkeypatch):
    _install(monkeypatch)
    profile = _single_profile(metadata_db)
    plan = _single_plan(metadata_db, profile)
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert report["action_dispatch_compilations"][0]["status"] == "pending"
    assert not report.get("action_dispatch_materialization")
    assert "action_dispatch_profile_missing" in report[
        "action_dispatch_compilations"][0]["issues"][0]["message"]


def test_materialization_manifest_rejects_partial_component_tamper(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    report = compose_equipment_plan(_multi_plan(periodic_db, profile), periodic_db)
    assert report["state"] == "ready"
    manifest = report["action_dispatch_materialization"]
    manifest["prototype_hits"] = manifest["prototype_hits"][:1]
    payload = {key: copy.deepcopy(value) for key, value in manifest.items()
               if key != "content_fingerprint"}
    manifest["content_fingerprint"] = fingerprint(payload)
    with pytest.raises(ValueError, match="缺少预期组件"):
        action_dispatch_execution_plan(report["plan"], report)


def test_materialization_manifest_self_hash_cannot_replace_compiler_semantics(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    output = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    report = copy.deepcopy(output["report"])
    receipt = copy.deepcopy(output["result_receipt"])
    manifest = report["action_dispatch_materialization"]
    manifest["prototype_hits"][0]["count"] = 2
    payload = {key: copy.deepcopy(value) for key, value in manifest.items()
               if key != "content_fingerprint"}
    manifest["content_fingerprint"] = fingerprint(payload)
    receipt["periodic_attacks"]["action_dispatch_materialization_fingerprint"] = manifest[
        "content_fingerprint"]
    with pytest.raises(ValueError, match="原编译语义"):
        validate_periodic_receipt(output["plan"], output["result"], report, receipt)


def test_downstream_confirmation_refusal_remains_pending_without_manifest(
        metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    import genshin_app.equipment.action_dispatch_sources as dispatch_sources
    monkeypatch.setattr(dispatch_sources, "authorize_materialized_profiles",
                        lambda plan, records, generated: [])
    report = compose_equipment_plan(_single_plan(metadata_db, profile), metadata_db)
    assert report["state"] == "pending"
    assert report["action_dispatch_compilations"][0]["status"] == "ready"
    assert report["action_event_compilations"][0]["status"] == "pending"
    assert "action_dispatch_materialization" not in report


def test_downstream_source_failure_remains_pending_without_manifest(metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    import genshin_db.runtime_attack_metadata as runtime_metadata
    original = runtime_metadata.get_attack_metadata_context
    calls = 0

    def fail_after_dispatch_resolution(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("fixture downstream source unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_metadata, "get_attack_metadata_context",
                        fail_after_dispatch_resolution)
    report = compose_equipment_plan(plan, metadata_db)
    assert calls > 1
    assert report["state"] == "pending"
    assert report["action_dispatch_compilations"][0]["status"] == "ready"
    assert report["attack_metadata_compilations"][0]["status"] == "pending"
    assert "action_dispatch_materialization" not in report


def test_ambiguous_zero_offset_channels_remain_pending_without_manifest(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    for emission in profile["emissions"]:
        field = "first_offset_seconds" if emission["kind"] == "periodic" else "offset_seconds"
        emission["schedule"][field] = 0
    profile["lifecycle"]["establishment_offset_seconds"] = 0
    _install(monkeypatch, profile)

    report = compose_equipment_plan(_multi_plan(periodic_db, profile), periodic_db)

    assert report["state"] == "pending"
    assert report["action_dispatch_compilations"][0]["status"] == "ready"
    assert report["action_event_compilations"][0]["status"] == "pending"
    assert any(issue["code"] == "periodic_action_group_pending"
               for issue in report["action_event_compilations"][0]["issues"])
    assert "action_dispatch_materialization" not in report
