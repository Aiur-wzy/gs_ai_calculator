"""Actual App manual captures and offline same-engine consistency proofs."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_plan_mechanics import native, review

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment import snapshot_capture_receipt as proof
from genshin_app.equipment.files import fingerprint
from genshin_engine import mechanics


@pytest.fixture
def calculated(tmp_path):
    plan = native()
    plan["manual_template"]["characters"][0]["stats"]["base_atk"] = 100
    model = plan["mechanics_model"]
    rule = model["rules"][0]
    rule["trigger"]["phase"] = "post"
    rule["effects"][0].update(benefit_type_id="benefit.stat_pct.atk", value=.2)
    model["snapshot_captures"] = [{"id": "sample", "action_id": "cast", "offset": .05,
        "precision": .000001, "status": "assumed", "source": "工程0.05秒；不是当前游戏实测"}]
    for hit in model["hit_events"]:
        hit["self_snapshot_event"] = "sample"
    db = tmp_path / "absent.db"
    review(plan, db)
    value = run_plan_calc(plan, db)
    value["result_receipt"]["snapshot_captures"] = proof.make_snapshot_capture_receipt(
        value["plan"], value["report"], value["result"])
    value["result_receipt"]["result_fingerprint"] = fingerprint(value["result"])
    assert not db.exists()
    return value


def validate(value, *, full=False):
    if full:
        proof.validate_snapshot_capture_receipt(value["plan"], value["result"], value["report"], value["result_receipt"])
    else:
        proof.validate_snapshot_capture_result_receipt(value["result"], value["result_receipt"])


def resign(value):
    result, receipt = value["result"], value["result_receipt"]
    p = receipt["snapshot_captures"]
    p["captures_fingerprint"] = fingerprint(result["snapshot_captures"])
    p["execution_fingerprint"] = fingerprint(result["snapshot_capture_execution"])
    p["ledger_fingerprint"] = fingerprint(proof._ledger(result))
    receipt["result_fingerprint"] = fingerprint(result)


def test_actual_manual_capture_full_and_result_only_are_offline(calculated, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline capture proof read DB"))
    assert [h["expected"] for h in calculated["result"]["hits"]] == pytest.approx([54, 54, 54])
    before = copy.deepcopy(calculated)
    validate(calculated, full=True)
    validate(calculated)
    assert calculated == before


def test_one_engine_replay_per_validator_and_no_reimplemented_queue(calculated, monkeypatch):
    calls = []
    original = mechanics.execute_mechanics

    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(mechanics, "execute_mechanics", observed)
    validate(calculated)
    assert calls == [True]


@pytest.mark.parametrize("field", ["actor", "origin", "parent_run_id", "time_ticks", "snapshot_fingerprint"])
def test_rehashed_capture_forgery_is_not_accepted(calculated, field):
    row = calculated["result"]["snapshot_captures"]["trace"][0]
    row[field] = 1 if field in ("origin", "time_ticks") else "wrong"
    resign(calculated)
    with pytest.raises(ValueError):
        validate(calculated)


def test_rehashing_all_copies_of_snapshot_hash_still_requires_engine_state(calculated):
    result = calculated["result"]
    for row in result["snapshot_captures"]["trace"] + result["mechanics_trace"]:
        if row.get("type") in proof.TRACE_TYPES:
            row["snapshot_fingerprint"] = "0" * 64
    for row in result["rotation_check"]["event_log"]:
        if "snapshot_capture" in row:
            row["snapshot_capture"]["snapshot_fingerprint"] = "0" * 64
    resign(calculated)
    with pytest.raises(ValueError):
        validate(calculated)


@pytest.mark.parametrize("mutation", ["remove_capture", "remove_reference", "wrong_reference", "duplicate_capture"])
def test_missing_or_repointed_consumer_rejected_after_all_proof_hashes_recomputed(calculated, mutation):
    trace = calculated["result"]["snapshot_captures"]["trace"]
    if mutation == "remove_capture":
        trace.pop(0)
    elif mutation == "remove_reference":
        trace.pop(1)
    elif mutation == "wrong_reference":
        trace[1]["event_id"] = "h1"
    else:
        trace.append(copy.deepcopy(trace[0]))
    resign(calculated)
    with pytest.raises(ValueError):
        validate(calculated)


@pytest.mark.parametrize("residue", ["dedicated", "context", "event", "mechanics"])
def test_deleting_proof_and_other_top_fields_cannot_hide_each_residue(calculated, residue):
    result = calculated["result"]
    calculated["result_receipt"].pop("snapshot_captures")
    if residue != "dedicated":
        result.pop("snapshot_captures")
    if residue != "context":
        result.pop("snapshot_capture_execution")
    if residue != "event":
        result["rotation_check"]["event_log"] = [r for r in result["rotation_check"]["event_log"]
                                                  if r["type"] != "mechanics_snapshot_capture"]
    if residue != "mechanics":
        result["mechanics_trace"] = [r for r in result["mechanics_trace"] if r.get("type") not in proof.TRACE_TYPES]
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate(calculated)


def test_changed_hit_damage_cannot_pass_by_resigning_ledger_and_result(calculated):
    calculated["result"]["hits"][0]["expected"] = 123
    calculated["result"]["summary"]["total_damage"] += 69
    resign(calculated)
    with pytest.raises(ValueError):
        validate(calculated)


def test_changed_native_capture_is_bound_in_full_package(calculated):
    calculated["plan"]["mechanics_model"]["snapshot_captures"][0]["offset"] = .06
    with pytest.raises(ValueError):
        validate(calculated, full=True)


def test_context_and_actual_rotation_cannot_diverge(calculated):
    calculated["result"]["snapshot_capture_execution"]["rotation_model"]["actions"][0]["order"] = 3
    resign(calculated)
    with pytest.raises(ValueError):
        validate(calculated)


def test_budget_before_copy_or_replay(calculated, monkeypatch):
    result = calculated["result"]
    result.pop("snapshot_capture_execution")
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", 20)
    monkeypatch.setattr(mechanics, "execute_mechanics", lambda *a, **k: pytest.fail("budget must precede replay"))
    with pytest.raises(ValueError, match="字节预算 20"):
        proof.make_snapshot_capture_receipt(calculated["plan"], calculated["report"], result)
    assert "snapshot_capture_execution" not in result


def test_old_without_capture_stays_noop(monkeypatch):
    monkeypatch.setattr(mechanics, "execute_mechanics", lambda *a, **k: pytest.fail("old result replayed"))
    assert proof.make_snapshot_capture_receipt({}, {}, {}) is None
    proof.validate_snapshot_capture_result_receipt({}, {})
    proof.validate_snapshot_capture_receipt({}, {}, {}, {})


def repack(plan, db):
    review(plan, db)
    value = run_plan_calc(plan, db)
    value["result_receipt"]["snapshot_captures"] = proof.make_snapshot_capture_receipt(
        value["plan"], value["report"], value["result"])
    value["result_receipt"]["result_fingerprint"] = fingerprint(value["result"])
    return value


def test_complete_all_miss_keeps_capture_and_zero_damage_proof(calculated, tmp_path):
    from test_verified_empty_damage_engine import attach_misses
    plan = copy.deepcopy(calculated["plan"])
    plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "once"}
    attach_misses(plan["mechanics_model"])
    value = repack(plan, tmp_path / "no-miss-db.db")
    assert value["result"]["hits"] == []
    assert value["result"]["summary"]["total_damage"] == 0
    assert value["result"]["damage_reporting"]["status"] == "verified_zero"
    validate(value, full=True)


def test_failed_later_action_keeps_valid_captured_prefix(calculated, tmp_path):
    from test_mechanics_execution import action
    plan = copy.deepcopy(calculated["plan"])
    rotation = plan["rotation_model"]
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["abilities"][0]["cooldown"] = {"duration": 10, "charges": 1, "group": "cd",
                                           "recovery": "parallel", "start": "action_start"}
    rotation["initial_cooldowns"] = [{"character": "a", "group": "cd", "ready_in": []}]
    rotation["actions"].append(action(aid="late", start=.5, end=.6, repeat={"mode": "once"}))
    value = repack(plan, tmp_path / "no-prefix-db.db")
    assert value["result"]["mechanics_status"] == "failed"
    assert value["result"]["summary"]["dps"] is None
    validate(value, full=True)


def test_exact_total_evidence_budget_and_one_byte_below(calculated, monkeypatch):
    from genshin_app.equipment.replay_numeric_projection import numeric_budget_result
    result = calculated["result"]
    values = [result["snapshot_capture_execution"], result["snapshot_captures"], numeric_budget_result(proof._ledger(result)),
              calculated["result_receipt"]["snapshot_captures"]]
    size = len(json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", size)
    validate(calculated)
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", size - 1)
    monkeypatch.setattr(mechanics, "execute_mechanics", lambda *a, **k: pytest.fail("over budget executed"))
    with pytest.raises(ValueError, match=f"字节预算 {size - 1}"):
        validate(calculated)


def test_long_rotation_evidence_above_old_limit_is_not_truncated():
    # Exercise actual UTF-8 accounting above the previous 8 MiB ceiling.
    value = {"ledger": "层" * (3 * 1024 * 1024)}
    original_length = len(value["ledger"])
    proof._bounded(value)
    assert len(value["ledger"]) == original_length


def test_evidence_budget_counts_utf8_bytes(monkeypatch):
    value = ["层"]
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", size)
    proof._bounded(value)
    monkeypatch.setattr(proof, "MAX_EVIDENCE_BYTES", size - 1)
    with pytest.raises(ValueError, match=f"字节预算 {size - 1}") as caught:
        proof._bounded(value)
    assert caught.value.reason == "response_budget_exceeded"
    assert caught.value.details["phase"] == "snapshot_capture_evidence"
    assert caught.value.details["truncated"] is False
