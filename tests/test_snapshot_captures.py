"""Explicit action-bound observations, independently of damage contact."""
from __future__ import annotations

import copy

import pytest

from genshin_engine import mechanics, snapshot_capture
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from tests.test_mechanics_execution import EVIDENCE, action, add, damages, fixture, rule


def capture(mid="sample", aid="cast", offset=.1, **kw):
    return {"id": mid, "action_id": aid, "offset": offset, "precision": .000001, **EVIDENCE, **kw}


def setup(times=(.2, 2.1)):
    t, r, m = fixture(times)
    t["characters"][0]["stats"]["base_atk"] = 100
    r["max_cycles"] = 3
    r["actions"][0]["repeat"] = {"mode": "once"}
    r["actions"][0]["occupation_end"] = 1
    m["snapshot_captures"] = [capture()]
    return t, r, m


def post_atk(m, delay=0):
    x = rule(benefit="benefit.stat_pct.atk", value=.2,
             trigger={"event": "action_start", "phase": "post", "action_ids": ["cast"]}, duration=1)
    x["timing"]["delay"] = delay
    add(m, x)


def records(out, kind="snapshot_capture"):
    return [x for x in out["snapshot_capture_trace"] if x["type"] == kind]


def test_post_old_action_and_new_capture_have_distinct_sampling_then_freeze():
    t, r, m = setup((.2, .2, .2, 2.1))
    post_atk(m)
    m["hit_events"][1]["self_snapshot_event"] = "cast"
    m["hit_events"][2]["self_snapshot_event"] = "sample"
    m["hit_events"][3]["self_snapshot_event"] = "sample"
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "unproven", out["issues"]
    assert damages(out) == pytest.approx([54, 45, 54, 54])
    assert (t, r, m) == before
    row = records(out)[0]
    assert (row["actor"], row["parent_run_id"], row["time_ticks"], row["status"]) == ("a", "0:cast", 100000, "captured")
    assert len(row["snapshot_fingerprint"]) == 64
    assert len(records(out, "snapshot_capture_reference")) == 2


def test_zero_offset_is_after_both_parent_phases_without_extra_rule_trigger():
    t, r, m = setup((0,))
    m["snapshot_captures"][0]["offset"] = 0
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    post_atk(m)
    out = execute_mechanics(t, r, m)
    assert damages(out) == pytest.approx([54])
    assert [e["type"] for e in out["rotation_check"]["event_log"][:3]] == ["action", "mechanics_snapshot_capture", "mechanics_hit"]
    assert len([x for x in out["mechanics_trace"] if x["type"] == "activation"]) == 1


@pytest.mark.parametrize(("delay", "expected"), [(.1, 54), (.100001, 45)])
def test_same_tick_activation_parent_causality_precedes_capture(delay, expected):
    t, r, m = setup((.2,))
    post_atk(m, delay)
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    out = execute_mechanics(t, r, m)
    assert damages(out) == pytest.approx([expected])


def test_same_tick_lower_order_consumer_cannot_read_future_capture():
    t, r, m = setup((.1,))
    r["actions"][0]["order"] = 20
    m["hit_events"][0]["order"] = 10
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert out["issues"][-1]["code"] == "snapshot_event_unexecuted"
    assert out["rotation_check"]["event_log"][0]["type"] == "action"


@pytest.mark.parametrize("successor_order", [-2, 2])
def test_successful_successor_at_occupation_end_respects_actual_queue_order(successor_order):
    t, r, m = setup((.3,))
    r["actions"][0]["occupation_end"] = .1
    r["actions"].append(action("second", start=.1, end=.2, order=successor_order))
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    out = execute_mechanics(t, r, m)
    if successor_order < 0:
        assert out["status"] == "pending" and out["result"] is None
        assert records(out)[0]["status"] == "cancelled"
        assert out["issues"][-1]["code"] == "snapshot_cancelled"
    else:
        assert damages(out) == pytest.approx([45])
        assert records(out)[0]["status"] == "captured"


def test_existing_prior_buff_is_in_action_snapshot_before_post_refresh():
    t, r, m = setup((.2,))
    post_atk(m)
    m["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": [1]}]
    m["hit_events"][0]["self_snapshot_event"] = "cast"
    assert damages(execute_mechanics(t, r, m)) == pytest.approx([54])


def test_provider_and_enemy_remain_current_while_self_is_frozen():
    t, r, m = setup((.3,))
    post_atk(m)
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    m["condition_events"] = [{"id": "late", "t": .2, "order": 1, "condition": "late", "value": True,
                              "repeat": {"mode": "once"}, **EVIDENCE}]
    add(m, rule("late_atk", benefit="benefit.stat_add.atk", value=100,
                trigger={"event": "condition", "phase": "pre", "event_ids": ["late"]}))
    add(m, rule("current_provider", benefit="benefit.base_add.all", domain="provider",
                value={"kind": "stat", "subject": "owner", "stat": "atk", "factor": 1, "sample": "current"}))
    add(m, rule("late_res", benefit="benefit.res_shred.all", domain="enemy", value=.2,
                trigger={"event": "condition", "phase": "pre", "event_ids": ["late"]}))
    out = execute_mechanics(t, r, m)
    # Self ATK120 at .1, provider ATK220 at .3, enemy RES -.1 -> 1.05.
    assert damages(out) == pytest.approx([(120+220)*.5*1.05])


def test_failed_parent_never_schedules_capture():
    t, r, m = setup((.2,))
    r["abilities"][0]["cooldown"] = {"duration": 10, "charges": 1, "group": "skill", "recovery": "parallel", "start": "action_start"}
    r["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": [1]}]
    out = execute_mechanics(t, r, m)
    assert out["status"] == "failed", out["issues"]
    assert records(out) == []
    assert not any(e["type"] == "mechanics_snapshot_capture" for e in out["rotation_check"]["event_log"])


def test_repeated_actions_have_origin_local_capture_and_proof_can_finish():
    t, r, m = setup((.2,))
    r["max_cycles"] = 20
    r["actions"][0]["repeat"] = {"mode": "each_cycle"}
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    out = execute_mechanics(t, r, m)
    assert out["status"] == "passed", out["issues"]
    assert [x["run_id"] for x in records(out)] == ["0:sample", "1:sample", "2:sample"]


@pytest.mark.parametrize(("key", "value"), [("offset", True), ("precision", True), ("offset", -.1),
    ("offset", .0000001), ("precision", 0), ("precision", .0000001)])
def test_capture_calibration_rejects_bad_time_types(key, value):
    _, _, m = setup()
    m["snapshot_captures"][0][key] = value
    assert validate_mechanics_model(m)["status"] == "invalid"


@pytest.mark.parametrize(("key", "value"), [("status", "pending"), ("source", "  "), ("offset", 1.000001)])
def test_unknown_or_outside_occupation_is_pending(key, value):
    t, r, m = setup()
    m["snapshot_captures"][0][key] = value
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None


def test_window_right_edge_rejected_even_if_occupation_extends():
    t, r, m = setup()
    r["actions"][0]["occupation_end"] = 10
    m["snapshot_captures"][0]["offset"] = 10
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["issues"][0]["code"] == "snapshot_scope_unsupported"


@pytest.mark.parametrize("ident", ["cast", "cast:end", "h0", "cast~generated", "@elemental_state:1"])
def test_capture_cannot_alias_existing_or_reserved_event(ident):
    t, r, m = setup()
    m["snapshot_captures"][0]["id"] = ident
    assert execute_mechanics(t, r, m)["status"] == "invalid"


def test_capture_byte_budget_exact_and_one_less(monkeypatch):
    t, r, m = setup((.2,))
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    base = execute_mechanics(t, r, m)
    size = base["result"]["snapshot_captures"]["storage_bytes"]
    monkeypatch.setattr(snapshot_capture, "MAX_SNAPSHOT_CAPTURE_BYTES", size)
    assert execute_mechanics(t, r, m)["status"] == "unproven"
    monkeypatch.setattr(snapshot_capture, "MAX_SNAPSHOT_CAPTURE_BYTES", size-1)
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert len(records(out)) == 1
    assert out["issues"][-1]["code"] == "snapshot_capture_limit"


def test_snapshot_event_budget_preserves_admitted_prefix(monkeypatch):
    t, r, m = setup((.2,))
    monkeypatch.setattr(mechanics, "MAX_MECHANICS_EVENTS", 3)
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    assert out["issues"][-1]["code"] == "snapshot_capture_event_limit"
    assert out["rotation_check"]["cycles"][0]["executed_action_count"] == 1
    assert records(out) == []


def test_capture_declaration_storage_is_bounded(monkeypatch):
    _, _, m = setup()
    monkeypatch.setattr(snapshot_capture, "MAX_SNAPSHOT_CAPTURE_DECLARATION_BYTES", 10)
    assert validate_mechanics_model(m)["issues"][0]["code"] == "snapshot_capture_limit"


def test_missed_contact_cannot_remove_capture_or_consume_quota():
    from tests.test_freeze_review import SAFE_CONTACT, fresh_model

    t, r, m = setup((.05, .2))
    m["elemental_model"] = fresh_model()
    for i, d in enumerate(m["hit_events"]):
        d["application"] = {"profile_id": "cryo1", "target_id": "enemy", "landed": i != 0}
        d["contact"] = copy.deepcopy(SAFE_CONTACT)
    m["hit_events"][1]["self_snapshot_event"] = "sample"
    add(m, rule(quota={"capacity": 1, "scope": "per_beneficiary", "refresh": "reset",
                       "consume": {"event": "hit", "phase": "pre"}}))
    out = execute_mechanics(t, r, m)
    assert damages(out) == pytest.approx([90])
    assert len(records(out)) == 1
    assert len([x for x in out["mechanics_trace"] if x["type"] == "quota"]) == 1


@pytest.mark.parametrize("switch_time", [.05, .15])
def test_actual_switch_before_capture_pending_after_capture_keeps_snapshot(switch_time):
    from tests.test_mechanics_execution import ability, second_character

    t, r, m = setup((.2,))
    second_character(t, r)
    r["abilities"].append(ability("switch", kind="action.switch"))
    r["actions"][0]["switch_allowed_at"] = .05
    r["actions"].append(action("swap", "switch", start=switch_time, end=switch_time,
                                 order=2, switch_to="b", repeat={"mode": "once"}))
    m["hit_events"][0]["self_snapshot_event"] = "sample"
    out = execute_mechanics(t, r, m)
    if switch_time < .1:
        assert out["status"] == "pending" and out["result"] is None
        assert out["issues"][-1]["code"] == "snapshot_cancelled"
    else:
        assert damages(out) == pytest.approx([45])
        assert records(out)[0]["status"] == "captured"


def test_counted_storage_does_not_reset_at_window_boundary(monkeypatch):
    t, r, m = setup((.2,))
    first = execute_mechanics(t, r, m)["result"]["snapshot_captures"]["storage_bytes"]
    r["actions"][0]["repeat"] = {"mode": "each_cycle"}
    monkeypatch.setattr(snapshot_capture, "MAX_SNAPSHOT_CAPTURE_BYTES", first)
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    assert [x["origin"] for x in records(out)] == [0]
    assert out["rotation_check"]["energy_check"]["completed_cycles"] == 1


def test_failed_energy_admission_does_not_capture():
    t, r, m = setup((.2,))
    r["characters"][0]["initial_energy"] = 0
    r["abilities"][0]["action_type_id"] = "action.elemental_burst"
    r["abilities"][0]["cooldown"] = {"duration": 10, "charges": 1, "group": "burst", "recovery": "parallel", "start": "action_start"}
    r["initial_cooldowns"] = [{"character": "a", "group": "burst", "ready_in": []}]
    r["abilities"][0]["energy"] = {"required": 10, "amount": 10, "delivery": "action_start"}
    out = execute_mechanics(t, r, m)
    assert out["status"] == "failed", out["issues"]
    assert records(out) == []


def test_captured_snapshot_is_reusable_by_event_expression():
    t, r, m = setup((.3,))
    add(m, rule(benefit="benefit.base_add.all", domain="provider",
                value={"kind": "stat", "subject": "owner", "stat": "atk", "factor": 1,
                       "sample": "event", "event_id": "sample"}))
    out = execute_mechanics(t, r, m)
    assert damages(out) == pytest.approx([90])
    assert records(out, "snapshot_capture_reference")[0]["usage"] == "expression"


def test_without_capture_preserves_old_result_shape():
    t, r, m = fixture()
    base = execute_mechanics(t, r, m)
    m["snapshot_captures"] = []
    assert execute_mechanics(t, r, m) == base
    assert "snapshot_captures" not in base["result"]


def er_fixture(credit_at):
    t, r, m = setup((.3,))
    r["characters"][0]["initial_energy"] = 0
    r["abilities"][0]["rules"] = [{"id": "particles", "kind": "energy_spawn"}]
    r["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "triggered", "event_ids": ["spawn"]}]
    r["events"] = [
        {"id": "spawn", "type": "energy_spawn", "t": 0, "order": 1, "action_id": "cast", "count": 1,
         "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "pickup", "type": "energy_credit", "credit_at": credit_at, "order": 1,
         "spawn_id": "spawn", "count": 1, "repeat": {"mode": "once"}, **EVIDENCE}]
    add(m, rule(benefit="benefit.energy_recharge.all", duration=30,
                value={"kind": "stat", "subject": "owner", "stat": "atk", "factor": .001,
                       "sample": "event", "event_id": "sample"}))
    return t, r, m


def test_er_reference_before_capture_retains_prefix_without_credit_commit():
    out = execute_mechanics(*er_fixture(.05))
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None and out["resolved_template"] is None
    assert out["issues"][-1]["code"] == "snapshot_event_unexecuted"
    log = out["rotation_check"]["event_log"]
    assert log[-1]["id"] == "pickup" and log[-1]["status"] == "pending"
    assert "energy" not in log[-1]
    assert out["rotation_check"]["characters"][0]["final_energy"] == 0


def test_er_delayed_credit_retains_capture_origin_and_real_consumer_id():
    out = execute_mechanics(*er_fixture(10.2))
    assert out["status"] == "unproven", out["issues"]
    credit = next(r for r in out["rotation_check"]["event_log"] if r["id"] == "pickup")
    assert credit["energy"][0]["gain"] == pytest.approx(3.3)
    reference = records(out, "snapshot_capture_reference")[0]
    assert (reference["event_id"], reference["origin"], reference["capture_run_id"]) == ("pickup", 0, "0:sample")


def test_er_capture_reference_budget_fails_before_credit_commit(monkeypatch):
    args = er_fixture(.2)
    baseline = execute_mechanics(*args)
    used = baseline["result"]["snapshot_captures"]["storage_bytes"]
    monkeypatch.setattr(snapshot_capture, "MAX_SNAPSHOT_CAPTURE_BYTES", used - 1)
    out = execute_mechanics(*args)
    assert out["status"] == "pending", out["issues"]
    assert out["issues"][-1]["code"] == "snapshot_capture_limit"
    assert out["rotation_check"]["event_log"][-1]["id"] == "pickup"
    assert out["rotation_check"]["characters"][0]["final_energy"] == 0
    assert len(records(out)) == 1


def test_later_auto_energy_budget_keeps_existing_capture_prefix(monkeypatch):
    from genshin_engine import automatic_energy
    t, r, m = setup((.2,))
    r["abilities"][0]["action_type_id"] = "action.elemental_burst"
    r["abilities"][0]["cooldown"] = {"duration": 1, "charges": 1, "group": "burst", "recovery": "parallel", "start": "action_start"}
    r["initial_cooldowns"] = [{"character": "a", "group": "burst", "ready_in": []}]
    r["abilities"][0]["energy"] = {"required": 1, "amount": 1, "delivery": "action_start"}
    r["actions"].append(action(aid="second", start=2, end=2.1, repeat={"mode": "once"}))
    monkeypatch.setattr(automatic_energy, "MAX_AUTOMATIC_ENERGY_RECEIPTS", 1)
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None and out["resolved_template"] is None
    assert len(records(out)) == 1
    assert records(out)[0]["parent_run_id"] == "0:cast"
