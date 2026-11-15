"""Shared-pool integration, with invented characters and independently set sums."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import (
    ability,
    action,
    add,
    damages,
    fixture,
    rule,
    second_character,
)
from tests.test_shared_pools import config


def setup(times=(.2, .3, .4), initial=2):
    t, r, m = fixture(times)
    pools = config()
    pools["pools"][0]["initial"] = {"team": initial}
    pools["operations"][0]["trigger"] = {"event": "hit", "phase": "post", "event_ids": ["h0"]}
    pools["consumers"][0]["actor_relation"] = "party_member"
    add(m, rule("benefit", benefit="benefit.base_add.all", value=100, source_id="binding:benefit",
                resource_gate={"consumer_id": "consumer"}))
    m["shared_pools"] = pools
    return t, r, m


def execute(*values, matches=None):
    old = copy.deepcopy(values)
    out = execute_mechanics(*values, matches=matches)
    assert values == old
    return out


def consumers(out, cycle=0):
    return [x for x in out.get("shared_pool_trace", []) if x["type"] == "consume" and x["cycle"] == cycle]


def test_post_grant_shared_across_real_hits_and_retains_cross_cycle_identity():
    out = execute(*setup(initial=0))
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert damages(out) == pytest.approx([45, 90, 90])
    assert [x["accepted"] for x in consumers(out)] == [0, 1, 1]
    assert [x["run_id"] for x in consumers(out)] == ["0:h0", "0:h1", "0:h2"]
    assert consumers(out, 1)[0]["run_id"] == "1:h0"


def test_without_refill_quota_exhausts_but_same_named_later_hits_are_not_deduped():
    t, r, m = setup(initial=4)
    m["shared_pools"]["operations"] = []
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([90, 90, 90])
    assert [x["accepted"] for x in consumers(out, 1)] == [1, 0, 0]


def test_hidden_real_damage_still_consumes():
    t, r, m = setup(initial=1)
    m["shared_pools"]["operations"] = []
    m["hit_events"][0]["report"] = False
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([45, 45])
    assert [x["accepted"] for x in consumers(out)] == [1, 0, 0]


def test_background_does_not_take_frontline_pool():
    t, r, m = setup(initial=1)
    second_character(t, r, "b")
    m["shared_pools"]["operations"] = []
    m["shared_pools"]["consumers"][0]["actor_relation"] = "other_frontline"
    r["abilities"] += [ability("switch", "a", "action.switch"), ability("eb", "b")]
    r["actions"] += [action("switch_b", "switch", .1, .11, 1, switch_to="b"), action("cast_b", "eb", .25, .26, 2)]
    t["hits"][1]["character"] = "b"
    m["hit_events"][1]["action_id"] = "cast_b"
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([45, 135, 45])
    assert [x["accepted"] for x in consumers(out)] == [0, 1, 0]


def test_snapshot_and_match_cannot_reanimate_expired_qualification():
    t, r, m = setup(initial=2)
    m["shared_pools"]["operations"] = []
    m["rules"][0]["timing"]["duration"] = .25
    for desc in m["hit_events"]:
        desc["self_snapshot_event"] = "cast"
    out = execute(t, r, m, matches=[{"rule_id": "benefit", "effect_id": "value", "hit_id": "h1", "operation": "include"}])
    assert damages(out) == pytest.approx([90, 45, 45])
    assert [x["accepted"] for x in consumers(out)] == [1, 0, 0]


def test_exclude_effect_does_not_refund_consumption_and_include_cannot_ignore_balance():
    t, r, m = setup(initial=1)
    m["shared_pools"]["operations"] = []
    out = execute(t, r, m, matches=[{"rule_id": "benefit", "effect_id": "value", "hit_id": "h0", "operation": "exclude"},
                                  {"rule_id": "benefit", "effect_id": "value", "hit_id": "h1", "operation": "include"}])
    assert damages(out) == pytest.approx([45, 45, 45])
    assert [x["accepted"] for x in consumers(out)] == [1, 0, 0]


@pytest.mark.parametrize("mutation", [
    lambda m: m["rules"][0].update(quota={"capacity": 1, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}),
    lambda m: m["rules"][0]["effects"][0].update(benefit_type_id="benefit.em.all"),
    lambda m: m["shared_pools"]["consumers"][0].update(source_id="other"),
    lambda m: m["shared_pools"]["pools"][0].update(owner="b"),
    lambda m: m["rules"][0].pop("resource_gate"),
    lambda m: m["shared_pools"]["consumers"].append({**copy.deepcopy(m["shared_pools"]["consumers"][0]), "id": "other"}),
])
def test_gate_binding_or_unqualified_stat_writers_rejected(mutation):
    t, r, m = setup()
    mutation(m)
    out = execute(t, r, m)
    assert out["status"] == "invalid" and out["result"] is None
    assert any(i["code"].startswith("pool_") for i in out["issues"]), out["issues"]


@pytest.mark.parametrize("selector,key,value,status", [
    ("operation", "event_ids", ["missing"], "invalid"),
    ("operation", "action_ids", ["missing"], "invalid"),
    ("consumer", "condition", "unknown", "pending"),
])
def test_full_selector_reference_validation(selector, key, value, status):
    t, r, m = setup()
    row = m["shared_pools"]["operations"][0]["trigger"] if selector == "operation" else m["shared_pools"]["consumers"][0]["filter"]
    row[key] = value
    out = execute(t, r, m)
    assert out["status"] == status and out["result"] is None
    assert any(i["code"].startswith("pool_") for i in out["issues"])


def test_unknown_condition_during_execution_keeps_pool_trace_without_total():
    t, r, m = setup()
    m["shared_pools"]["operations"][0]["trigger"]["condition"] = "late"
    m["condition_events"] = [{"id": "late", "condition": "late", "value": True, "t": .5, "order": 20,
                              "repeat": {"mode": "each_cycle"}, "status": "assumed", "source": "工程条件"}]
    out = execute(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    assert consumers(out) and any(i["code"] == "unknown_condition" for i in out["issues"])


def test_shared_boundary_uses_remaining_cooldown_and_no_absolute_identity():
    t, r, m = setup(initial=0)
    out = execute(t, r, m)
    states = [x["boundary_state"]["mechanics"]["shared_pools"] for x in out["rotation_check"]["cycles"]]
    assert states
    assert all(set(x) == {"balances", "cooldown_remaining_us"} for x in states)
    assert states[-1]["cooldown_remaining_us"] == {"refill": 0}
    assert "capacity_modes" not in out["result"]["shared_pools"]["state"]


def test_unbounded_runtime_receipts_and_final_report_are_self_describing():
    t, r, m = setup(initial=19)
    m["shared_pools"]["pools"][0]["capacity"] = "unbounded"
    out = execute(t, r, m)
    assert out["result"] is not None, out["issues"]
    report = out["result"]["shared_pools"]
    assert report["state"]["capacity_modes"] == {"pool": "unbounded"}
    assert max(row["after"] for row in report["trace"]) > 20
    assert all(row["capacity_mode"] == "unbounded" for row in report["trace"])


def test_old_input_does_not_gain_pool_output_fields():
    out = execute(*fixture())
    assert "shared_pool_trace" not in out
    assert "shared_pools" not in out["result"]


def test_pool_attempts_share_total_event_budget_and_keep_completed_trace(monkeypatch):
    import genshin_engine.mechanics as engine
    t, r, m = setup(initial=2)
    monkeypatch.setattr(engine, "MAX_MECHANICS_EVENTS", 17)
    out = execute(t, r, m)
    assert out["status"] == "pending" and out["result"] is None
    assert out.get("shared_pool_trace")
    assert any(i["code"] == "pool_event_limit" for i in out["issues"])
    assert out["rotation_check"]["status"] == "pending"
    assert out["rotation_check"]["event_log"]
    assert out["rotation_check"]["cycles"][-1]["incomplete"] is True
    assert out["rotation_check"]["cycles"][-1]["executed_action_count"] >= 1


def attach_pool(t, r, m, initial=0):
    _, _, base = setup(initial=initial)
    m["shared_pools"] = base["shared_pools"]
    add(m, base["rules"][0])
    return t, r, m


def test_actual_reaction_post_refill_is_after_parent_and_has_distinct_occurrence():
    from tests.test_elemental_execution import setup as elemental_setup
    t, r, m = attach_pool(*elemental_setup(("cryo", "pyro", "pyro"), (.2, .3, .4)))
    m["shared_pools"]["operations"][0]["trigger"] = {"event": "elemental_reaction", "phase": "post", "reaction_types": ["melt"]}
    out = execute(t, r, m)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([45, 90, 90])
    assert [x["accepted"] for x in consumers(out)] == [0, 0, 1]
    ops = [x for x in out["shared_pool_trace"] if x["type"] == "operation" and x["cycle"] == 0]
    assert ops[0]["run_id"] == "0:h1:reaction"


def test_miss_and_transformative_child_do_not_consume_classic_pool():
    from tests.test_mechanics_swirl import setup as swirl_setup
    t, r, m = attach_pool(*swirl_setup((.2, .3)), initial=1)
    m["shared_pools"]["operations"] = []
    m["hit_events"][0]["application"]["landed"] = False
    out = execute(t, r, m)
    assert [x["event_id"] for x in consumers(out)] == ["h1"]
    assert damages(out) == pytest.approx([90, 781.299])


def test_expired_captured_instance_cannot_borrow_reactivated_instance_gate():
    t, r, m = setup(times=(.2, 1.2), initial=2)
    m["shared_pools"]["operations"] = []
    m["rules"][0]["timing"]["duration"] = .5
    r["actions"].append(action("cast2", "e", 1, 1.1, 2))
    m["rules"][0]["trigger"]["action_ids"] = ["cast", "cast2"]
    m["hit_events"][1]["action_id"] = "cast2"
    m["hit_events"][1]["self_snapshot_event"] = "cast"
    out = execute(t, r, m, matches=[{"rule_id": "benefit", "effect_id": "value", "hit_id": "h1", "operation": "include"}])
    assert damages(out) == pytest.approx([90, 45])
    assert [x["accepted"] for x in consumers(out)] == [1, 1]
    assert consumers(out)[0]["eligible_instance_ids"] != consumers(out)[1]["eligible_instance_ids"]


def test_nonrepeating_pool_grant_not_reset_on_next_check_cycle():
    t, r, m = setup(initial=0)
    m["shared_pools"]["operations"][0]["trigger"] = {"event": "hit", "phase": "post", "event_ids": ["h0"]}
    m["shared_pools"]["operations"][0]["cooldown"]["seconds"] = 25
    out = execute(t, r, m)
    assert [x["accepted"] for x in consumers(out, 1)] == [1, 0, 0]
    states = [x["boundary_state"]["mechanics"]["shared_pools"] for x in out["rotation_check"]["cycles"]]
    assert [x["cooldown_remaining_us"]["refill"] for x in states] == [15200000, 5200000, 0]


def test_pre_grant_after_pre_activation_is_available_to_current_hit():
    t, r, m = setup(initial=0)
    m["rules"][0]["trigger"] = {"event": "hit", "phase": "pre", "event_ids": ["h0"]}
    m["shared_pools"]["operations"][0]["trigger"]["phase"] = "pre"
    out = execute(t, r, m)
    assert damages(out) == pytest.approx([90, 90, 90])
    assert [x["type"] for x in out["shared_pool_trace"][:2]] == ["operation", "consume"]


def test_plain_input_dictionary_order_does_not_change_pool_receipts():
    values = setup(initial=0)
    def reverse_dicts(value):
        if isinstance(value, dict):
            return {k: reverse_dicts(v) for k, v in reversed(list(value.items()))}
        if isinstance(value, list):
            return [reverse_dicts(v) for v in value]
        return value
    left = execute(*values)
    changed = tuple(reverse_dicts(v) for v in values)
    right = execute(*changed)
    assert left["shared_pool_trace"] == right["shared_pool_trace"]
    assert left["result"] == right["result"]


def test_switch_listener_is_a_separate_logical_event_after_action_start():
    t, r, m = setup(initial=0)
    second_character(t, r, "b")
    r["abilities"].append(ability("switch", "a", "action.switch"))
    r["actions"].append(action("switch_b", "switch", .1, .11, 1, switch_to="b"))
    m["shared_pools"]["operations"][0]["trigger"] = {"event": "switch", "phase": "pre"}
    out = execute(t, r, m)
    assert out["result"] is not None, out["issues"]
    ops = [x for x in out["shared_pool_trace"] if x["type"] == "operation"]
    assert ops[0]["run_id"] == "0:switch_b:switch"
