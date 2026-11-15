"""Actual App-generated parents are checked without any mechanism-ID dispatch."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.mechanism_parent_graph import build_mechanism_parent_graph
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    mechanism_db,
    mechanism_repo,
)
from tests.test_limited_hit_benefit_sources import benefit_plan
from tests.test_passive_sources_app import passive_attack_db, passive_plan  # noqa: F401

# ruff: noqa: F811
ROOT = Path(__file__).resolve().parents[1]


def qualification(mechanism):
    data = json.loads((ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v2.json").read_text("utf8"))
    record = next(r for r in data["descriptors"] if r["id"] == mechanism)
    refs = record["payload"]["descriptor"]["evidence_fact_ids"]
    return next(f["payload"]["claim"] for f in data["facts"]
                if f["id"] + "@" + str(f["revision"]) in refs and f["payload"]["kind"] == "execution_qualification")


def graph_inputs(plan, db, mechanism, *, allow_pending_candidate=False):
    report = compose_equipment_plan(plan, db)
    profile = report["action_event_compilations"][0]
    plan["action_event_profiles"][0].update(profile["required_confirmation"] or {})
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "ready", report["missing_inputs"]
    source = query_runtime(db, {"action": "mechanism_profile", "mechanism_id": mechanism})
    if allow_pending_candidate:
        assert source["status"] == "pending" and source["source_binding"] and source["source_proof"]
        assert source["materialization_status"] == "pending"
    else:
        assert source["status"] == "ready", source["issues"]
    work = copy.deepcopy(plan)
    work["mechanics_model"] = copy.deepcopy(report["mechanics_model"])
    args = {"actor": "a", "source_binding": source["source_binding"], "source_proof": source["source_proof"],
            "qualification": qualification(mechanism), "action_records": report["action_event_compilations"],
            "parameter_records": report.get("hit_parameter_compilations", []),
            "metadata_records": report.get("attack_metadata_compilations", [])}
    return work, report["plan"]["resolved_template"], args


@pytest.fixture
def addition_graph(mechanism_db):
    plan = benefit_plan(mechanism_db)
    plan.pop("limited_hit_benefit_sources")
    return graph_inputs(plan, mechanism_db, "skill.current-atk-limited-addition")


def run(inputs):
    plan, template, args = inputs
    return build_mechanism_parent_graph(plan, template, **args)


def test_actual_accepted_action_parent_and_parameter_positions(addition_graph):
    before = copy.deepcopy(addition_graph)
    out = run(addition_graph)
    assert out["status"] == "ready", out["issues"]
    assert len(out["parents"]) == 1
    assert out["parents"][0]["action_tick"] == 0
    assert out["parents"][0]["absolute_tick"] == 200000
    assert out["execution_status"] == "not_evaluated"
    assert addition_graph == before
    out["parents"][0].clear()
    assert run(addition_graph)["status"] == "ready"


@pytest.mark.parametrize("change", ["parameter_pointer", "same_value_other_index", "other_variant", "node_hash",
                                  "stale_receipt", "count_two", "wrong_route", "duplicate_record", "missing_receipt"])
def test_parent_identity_and_actual_route_fail_atomically(addition_graph, change):
    plan, template, args = addition_graph
    if change == "parameter_pointer":
        args["parameter_records"][0]["source"]["parameter_pointer"] += "0"
    if change == "same_value_other_index":
        args["parameter_records"][0]["param_index"] = 0  # Existing scalar value deliberately unchanged.
    if change == "other_variant":
        plan["rotation_model"]["abilities"][0]["variant"] = "tap"
    if change == "node_hash":
        args["parameter_records"][0]["source"]["node_sha256"] = "a" * 64
    if change == "stale_receipt":
        args["parameter_records"][0]["snapshot"]["snapshot_id"] = "b" * 64
    if change == "count_two":
        template["hits"][0]["count"] = 2
    if change == "wrong_route":
        plan["mechanics_model"]["hit_patterns"][0]["orders"][0] += 1
    if change == "duplicate_record":
        args["action_records"].append(copy.deepcopy(args["action_records"][0]))
    if change == "missing_receipt":
        args["parameter_records"] = []
    out = run(addition_graph)
    assert out["status"] == "pending"
    assert out["parents"] == out["dependencies"] == []


def test_uncovered_related_cast_is_not_silently_ignored(addition_graph):
    plan, _, _ = addition_graph
    action = copy.deepcopy(plan["rotation_model"]["actions"][0])
    action.update(id="uncovered", start=2, occupation_end=3)
    plan["rotation_model"]["actions"].append(action)
    out = run(addition_graph)
    assert out["status"] == "pending"
    assert "未覆盖" in out["issues"][0]["message"]


def test_cast_miss_is_valid_parent_and_does_not_mean_no_action(addition_graph):
    plan, _, args = addition_graph
    args["action_records"][0]["generated"][0]["landed"] = False
    plan["mechanics_model"]["hit_patterns"][0]["application"]["landed"] = False
    out = run(addition_graph)
    assert out["status"] == "ready", out["issues"]
    assert out["parents"][0]["landed"] is False


def test_pending_reaction_knowledge_can_probe_parent_graph_without_execution_authority(passive_plan):
    plan, db = passive_plan
    plan.pop("passive_sources")
    # Isolated DB copied by the existing parent fixture; never the formal DB.
    seed = json.loads((ROOT / "src/genshin_db/resources/mechanisms/reviewed-mechanisms-v1.json").read_text("utf8"))
    seed["descriptors"] = [r for r in seed["descriptors"] if r["id"] == "passive.swirl-elemental-bonus"]
    subject = seed["descriptors"][0]["subject"]
    seed["facts"] = [r for r in seed["facts"] if r["subject"] == subject]
    kept = {r["id"] for r in [*seed["facts"], *seed["descriptors"]]}
    seed["heads"] = [r for r in seed["heads"] if r["record_id"] in kept]
    for record in [*seed["facts"], *seed["descriptors"]]:
        record["review"] = {"status": "approved", "reviewer": "isolated-parent-graph-test",
                            "reviewed_at": "2026-10-03"}
    assert import_bundle(db, seed, dry_run=False)["applied"]
    inputs = graph_inputs(
        plan, db, "passive.swirl-elemental-bonus", allow_pending_candidate=True)
    out = run(inputs)
    assert out["status"] == "ready", out["issues"]
    assert len(out["parents"]) == 1
    assert inputs[2]["source_binding"]["selection"]["proud_skill_id"] != inputs[2]["parameter_records"][0]["resolved_selection"].get("proud_skill_id")
    inputs[2]["metadata_records"] = []
    assert run(inputs)["status"] == "pending"
