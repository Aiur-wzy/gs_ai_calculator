"""Calibrated action dispatch uses abstract timing, never a team answer key."""
from __future__ import annotations

import copy
import json

import pytest
from test_freeze_runtime import model as freeze_model
from test_mechanics_execution import EVIDENCE, action, fixture
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder

# ruff: noqa: F811


@pytest.fixture
def action_events_db(tmp_path, fake_repo, stub_fetcher):
    path = fake_repo / "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[0]["skills"] = [0, 90011, 0, 0]
    path.write_text(json.dumps(rows), "utf8")
    path = fake_repo / "ExcelBinOutput/ProudSkillExcelConfigData.json"
    path.write_text(json.dumps([{"proudSkillId": 910020, "proudSkillGroupId": 91001, "level": 10,
        "paramList": [.5], "paramDescList": [201]}]), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def action_event_plan(db):
    template, rotation, mechanics = fixture((0,))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    prototype = plan["manual_template"]["hits"][0]
    prototype.pop("multiplier")
    prototype["scaling_stat"] = "atk"
    rotation["abilities"][0]["variant"] = "tap"
    rotation["actions"] = [action(f"cast{i}", start=i*2, end=i*2+1, repeat={"mode": "once"}) for i in range(3)]
    mechanics["hit_events"] = []
    mechanics["elemental_model"] = freeze_model()
    mechanics["elemental_model"]["contact_profiles"] = [{"id": "safe", "freeze_interaction": "none", **EVIDENCE}]
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    selection = {"character_id": 90000001, "slot": "skill", "constellation": 0}
    q = query_runtime(db, {"action": "action_parameter", "param_index": 0, **selection})
    plan["hit_parameter_sources"] = [{"hit_id": "h0", "version_line": "release", "selection": selection,
        "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", **EVIDENCE,
        "expected_snapshot_id": q["snapshot"]["snapshot_id"], "expected_node_sha256": q["source"]["node_sha256"]}]
    plan["action_event_profiles"] = [{"id": "contact", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "h0",
        "variant": "tap", "calibration": {"offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "application_profile_id": "cryo", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None}]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "contact"}]
    plan["action_event_overrides"] = []
    return plan


def confirm_action_events(plan, db):
    report = compose_equipment_plan(plan, db)
    for record in report["action_event_compilations"]:
        profile = next(p for p in plan["action_event_profiles"] if p["id"] == record["profile_id"])
        if record["required_confirmation"]:
            profile.update(record["required_confirmation"])
    return plan


def test_shared_prototype_generates_actual_once_actions(action_events_db):
    plan = action_event_plan(action_events_db)
    first = compose_equipment_plan(plan, action_events_db)
    assert first["state"] == "pending"
    assert first["action_event_compilations"][0]["generated"] == []
    confirm_action_events(plan, action_events_db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, action_events_db)
    assert plan == before
    assert [h["expected"] for h in out["result"]["hits"]] == [22.5] * 3
    assert [h["t"] for h in out["result"]["hits"]] == [.2, 2.2, 4.2]
    assert len(out["report"]["mechanics_model"]["hit_patterns"]) == 3
    assert out["plan"]["mechanics_model"]["hit_patterns"] == []
    assert "multiplier" not in out["plan"]["manual_template"]["hits"][0]
    record = out["report"]["action_event_compilations"][0]
    assert record["dispatch_mode"] == "calibrated" and record["skill_coverage"] == "partial"
    assert record["stats"] == {"matched_actions": 3, "planned_hits": 3, "generated_hits": 3, "landed_hits": 3, "missed_hits": 0}


@pytest.mark.parametrize("value", [True, .0000001, float("nan"), 61, -1, 10**400, 1e200])
def test_invalid_offset_is_rejected_before_query(action_events_db, value):
    plan = action_event_plan(action_events_db)
    plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_zero_offset_is_valid_before_query(action_events_db):
    plan = action_event_plan(action_events_db)
    plan["action_event_profiles"][0]["calibration"]["offset_seconds"] = 0
    validate_plan(plan)


def _compile_arguments(plan, db):
    report = compose_equipment_plan(plan, db)
    return report["plan"]["resolved_template"], {
        "parameter_compilations": report["hit_parameter_compilations"],
        "elemental_compilations": report.get("elemental_compilations", []),
        "reaction_compilations": report.get("reaction_damage_compilations", []), "missing": lambda *args: None}


@pytest.mark.parametrize("source_kind", ["parameter", "avatar", "depot", "skill"])
def test_raw_node_identity_not_overwritten_by_interpretation_text(action_events_db, source_kind):
    from genshin_app.equipment.action_events import compile_action_events
    plan = confirm_action_events(action_event_plan(action_events_db), action_events_db)
    template, kwargs = _compile_arguments(plan, action_events_db)
    param = kwargs["parameter_compilations"][0]
    source = param["source"] if source_kind == "parameter" else param["entry_sources"][source_kind]
    # Same scalar, same snapshot, different immutable raw-node identity must
    # change semantic acceptance; explanatory source text must not shadow it.
    source["node_sha256"] = "d" * 64
    source["definition"]["node_sha256"] = "d" * 64
    compiled, records, sources = compile_action_events(plan, template, **kwargs)
    assert compiled is None and sources == []
    assert records[0]["profile_fingerprint"] != plan["action_event_profiles"][0]["accepted_profile_fingerprint"]
    assert records[0]["issues"][0]["code"] == "action_event_confirmation"


def test_compiler_is_pure_and_receipt_budget_is_atomic(action_events_db, monkeypatch):
    import sqlite3

    from genshin_app.equipment import action_events
    plan = confirm_action_events(action_event_plan(action_events_db), action_events_db)
    template, kwargs = _compile_arguments(plan, action_events_db)
    before = copy.deepcopy((plan, template, kwargs["parameter_compilations"]))
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("pure compiler opened SQLite"))
    model, records, _ = action_events.compile_action_events(plan, template, **kwargs)
    assert len(model["hit_patterns"]) == 3
    assert records[0]["status"] == "ready"
    monkeypatch.setattr(action_events, "MAX_RECEIPT_BYTES", 1024)
    model, records, sources = action_events.compile_action_events(plan, template, **kwargs)
    assert model is None and sources == []
    assert records[0]["status"] == "pending" and records[0]["generated"] == []
    assert records[0]["stats"]["generated_hits"] == 0
    assert records[0]["issues"][0]["code"] == "action_event_receipt_limit"
    assert (plan, template, kwargs["parameter_compilations"]) == before


def test_nested_normalization_identity_is_semantic_dependency(action_events_db):
    from genshin_app.equipment.action_events import compile_action_events
    plan = confirm_action_events(action_event_plan(action_events_db), action_events_db)
    template, kwargs = _compile_arguments(plan, action_events_db)
    snapshot = kwargs["parameter_compilations"][0]["snapshot"]
    snapshot["normalization"] = {"mapping_fingerprint": "a" * 64, "normalization_version": 2,
        "profile_id": "precise-profile", "upstream_commit": "a" * 40}
    _, first, _ = compile_action_events(plan, template, **kwargs)
    plan["action_event_profiles"][0].update(first[0]["required_confirmation"])
    ready, _, _ = compile_action_events(plan, template, **kwargs)
    assert ready is not None
    # Deliberately preserve snapshot_id and all raw values: mapping semantics
    # still require a new acceptance independently of the input hash.
    snapshot["normalization"]["mapping_fingerprint"] = "b" * 64
    pending, final, _ = compile_action_events(plan, template, **kwargs)
    assert pending is None
    assert final[0]["profile_fingerprint"] != first[0]["profile_fingerprint"]


def test_pending_receipt_bounds_legacy_actor_identifiers(action_events_db):
    from genshin_app.equipment.action_events import (
        MAX_RECEIPT_BYTES,
        compile_action_events,
    )
    plan = confirm_action_events(action_event_plan(action_events_db), action_events_db)
    template, kwargs = _compile_arguments(plan, action_events_db)
    # Legacy IDs have no small length cap. A malformed actor must not leak a
    # multi-megabyte identity into a pending receipt before the ready-only cap.
    plan["manual_template"]["hits"][0]["character"] = "x" * (MAX_RECEIPT_BYTES + 1)
    model, records, sources = compile_action_events(plan, template, **kwargs)
    assert model is None and not sources
    assert records[0]["actor"] is None
    assert len(json.dumps(records, ensure_ascii=False).encode()) < MAX_RECEIPT_BYTES


def test_utf8_input_budget_counts_all_profile_bytes(action_events_db):
    plan = action_event_plan(action_events_db)
    p = plan["action_event_profiles"][0]
    p["calibration"]["source"] = "证" * 2048
    plan["action_event_profiles"] = [{**copy.deepcopy(p), "id": f"p{i}", "prototype_hit_id": f"h{i}"} for i in range(32)]
    with pytest.raises(PlanError, match="128KiB"):
        validate_plan(plan)


@pytest.mark.parametrize("draft", ["unbound", "no-actions", "no-rotation"])
def test_gui_two_step_creation_keeps_profile_pending_record(action_events_db, draft):
    plan = action_event_plan(action_events_db)
    if draft == "unbound":
        plan["action_event_bindings"] = []
    elif draft == "no-actions":
        plan["rotation_model"]["actions"] = []
    else:
        plan["rotation_model"] = None
    out = compose_equipment_plan(plan, action_events_db)
    assert out["state"] == "pending"
    assert out["action_event_compilations"][0]["profile_id"] == "contact"
    assert out["action_event_compilations"][0]["status"] == "pending"
    assert out["action_event_compilations"][0]["generated"] == []


def test_caps_and_empty_fields_preserve_old_plan(action_events_db):
    from genshin_app.equipment.action_events import action_event_capabilities
    plan = action_event_plan(action_events_db)
    for name in action_event_capabilities()["empty_fields"]:
        plan.pop(name)
    # Explicit old hit patterns remain an independent unchanged pathway.
    plan["mechanics_model"]["hit_patterns"] = [{"id": "manual", "prototype_hit_id": "h0", "action_id": "cast0",
        "offsets": [.2], "orders": [0], "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True},
        "contact": {"kind": "profile", "profile_id": "safe"}}]
    old = run_plan_calc(plan, action_events_db)
    assert "action_event_compilations" not in old["report"]
    plan.update(action_event_capabilities()["empty_fields"])
    new = run_plan_calc(plan, action_events_db)
    assert new["result"]["hits"] == old["result"]["hits"]
    assert "action_event_compilations" not in new["report"]
