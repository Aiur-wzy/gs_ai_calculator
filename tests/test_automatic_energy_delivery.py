"""Opt-in admission audits share arithmetic, but never occupy the game queue."""
import copy

import pytest

from genshin_engine import automatic_energy as ae
from genshin_engine.elemental import ElementalPending
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.rotation import check_rotation
from tests.test_mechanics_execution import EVIDENCE, fixture


def automatic_fixture(initial=10):
    template, rotation, mechanics = fixture()
    rotation["characters"][0]["initial_energy"] = initial
    rotation["abilities"][0].update(action_type_id="action.elemental_burst",
        cooldown={"group": "q", "duration": 1, "charges": 1, "start": "action_start", "recovery": "sequential"},
        energy={"required": 10, "amount": 10, "delivery": "action_start"})
    rotation["initial_cooldowns"] = [{"character": "a", "group": "q", "ready_in": []}]
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": h["id"]} for h in template["hits"]]
    return template, rotation, mechanics


def refund(rotation, order=1):
    rotation["events"].append({"id": "refund", "type": "energy_direct", "t": 0, "order": order,
                              "repeat": {"mode": "once"}, "amounts": {"a": 10}, **EVIDENCE})


def audit(check):
    return [r for r in check["event_log"] if r.get("delivery") == "automatic_action_start"]


@pytest.mark.parametrize("initial", [0, 10])
def test_atomic_modes_agree_and_preserve_input(initial):
    t, r, m = automatic_fixture(initial)
    refund(r)
    original = copy.deepcopy((t, r, m))
    plain = check_rotation(t, r)
    actual = execute_mechanics(t, r, m)["rotation_check"]
    assert (t, r, m) == original
    assert plain["characters"][0]["final_energy"] == actual["characters"][0]["final_energy"] == initial
    assert len(audit(plain)) == len(audit(actual)) == int(initial == 10)
    if initial:
        item = audit(actual)[0]
        assert (item["before"], item["after"]) == (10, 0)
        assert "order" not in item
        assert actual["event_log"][0]["energy_delivery"]["status"] == "committed"
        assert actual["event_log"][1] == item
        assert actual["cycles"][0]["energy_spend_count"] == 1
    else:
        assert actual["event_log"][0]["energy_delivery"]["status"] == "not_committed"


def test_legacy_paths_keep_their_distinct_refund_phase():
    t, r, m = automatic_fixture()
    r["abilities"][0]["energy"].pop("delivery")
    refund(r)
    r["events"].append({"id": "spend", "type": "energy_spend", "action_id": "cast", "character": "a",
        "required": 10, "amount": 10, "t": 0, "order": 2, "repeat": {"mode": "once"}, **EVIDENCE})
    plain, mech = check_rotation(t, r), execute_mechanics(t, r, m)["rotation_check"]
    assert plain["characters"][0]["final_energy"] == 0
    assert mech["characters"][0]["final_energy"] == 10
    assert "automatic_energy_delivery" not in plain and "automatic_energy_delivery" not in mech


@pytest.mark.parametrize("constant,value", [("MAX_AUTOMATIC_ENERGY_RECEIPTS", 0), ("MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES", 2)])
def test_budget_pending_happens_before_cd_and_energy(monkeypatch, constant, value):
    t, r, m = automatic_fixture()
    monkeypatch.setattr(ae, constant, value)
    plain = check_rotation(t, r)
    out = execute_mechanics(t, r, m)
    assert plain["status"] == out["status"] == "pending"
    assert out["result"] is out["resolved_template"] is None
    for checked in (plain, out["rotation_check"]):
        row = checked["event_log"][0]
        assert row["energy_delivery"]["status"] == "not_committed"
        assert "next_ready_at" not in row
        assert checked["characters"][0]["final_energy"] == 10
        assert checked["automatic_energy_delivery"]["receipt_count"] == 0
        assert checked["energy_check"]["proven_cycle"] is None


def test_receipt_byte_budget_exact_boundary(monkeypatch):
    t, r, _ = automatic_fixture()
    size = check_rotation(t, r)["automatic_energy_delivery"]["receipt_bytes"]
    monkeypatch.setattr(ae, "MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES", size)
    assert check_rotation(t, r)["status"] == "unproven"
    monkeypatch.setattr(ae, "MAX_AUTOMATIC_ENERGY_RECEIPT_BYTES", size - 1)
    assert check_rotation(t, r)["status"] == "pending"


def test_controlled_observer_pending_preserves_committed_prefix(monkeypatch):
    t, r, m = automatic_fixture()

    def stop(*args):
        raise ElementalPending("test_pending", "工程中止", "cast")

    monkeypatch.setattr(_Mechanics, "_on_event", stop)
    result = execute_mechanics(t, r, m)
    assert result["status"] == "pending" and result["result"] is None
    checked = result["rotation_check"]
    assert len(audit(checked)) == 1
    assert checked["event_log"][0]["status"] == "pending"
    assert checked["event_log"][0]["energy_delivery"]["status"] == "committed"
    assert checked["automatic_energy_delivery"]["committed_actions"] == 1


def test_early_proof_does_not_fabricate_remaining_parents():
    t, r, _ = automatic_fixture()
    r["max_cycles"] = 20
    r["actions"][0]["repeat"] = {"mode": "each_cycle"}
    refund(r)
    r["events"][0]["repeat"] = {"mode": "each_cycle"}
    checked = check_rotation(t, r)
    assert checked["status"] == "passed"
    stats = checked["automatic_energy_delivery"]
    assert stats["max_cycles_planned_actions"] == 20
    assert stats["committed_actions"] == stats["planned_actions"] == 3
    assert stats["unreported_actions"] == 17


def test_duplicate_and_typed_namespace_rejected_before_ownership():
    t, r, m = automatic_fixture()
    r["events"].append({"id": "spend", "type": "energy_spend", "action_id": "cast", "character": "a",
        "required": 10, "amount": 10, "t": 0, "order": 1, "repeat": {"mode": "once"}, **EVIDENCE})
    assert any(i["code"] == "duplicate_delivery" for i in execute_mechanics(t, r, m)["issues"])
    r["events"] = []
    m["hit_events"][0]["self_snapshot_event"] = ae.receipt_id("cast")
    assert any(i["code"] == "automatic_energy_namespace" for i in execute_mechanics(t, r, m)["issues"])
