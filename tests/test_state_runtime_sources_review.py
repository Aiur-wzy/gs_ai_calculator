"""Independent complete-graph source and monotone resource closure checks."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import rule
from tests.test_shared_pools import config as pool_config
from tests.test_state_runtime_sources import graph, invoke


def chain():
    data, rules, active, linked = graph()
    gate = rule("gate", source_id="source:gate", resource_gate={"consumer_id": "consumer"})
    authority = rule("authority2", source_id="source:op")
    data["rules"] += [gate, authority]
    rules.update(gate=gate, authority2=authority)
    active.update(rules)
    linked.update({rid: ("source",rid) for rid in rules})
    shared = pool_config()
    shared["pools"][0]["source_id"] = "source:field"
    second = copy.deepcopy(shared["pools"][0])
    second.update(id="pool2",source_id="source:gate")
    shared["pools"].append(second)
    shared["consumers"][0].update(source_id="source:gate",eligibility_rule_id="gate")
    shared["operations"][0].update(id="refill2",pool_id="pool2",source_id="source:op")
    from genshin_engine.state_runtime_mechanics import route_event_id
    shared["operations"][0]["trigger"] = {"event":"condition","phase":"post",
        "event_ids":[route_event_id("resource_operation","refill2")]}
    data["shared_pools"] = shared
    life = copy.deepcopy(data["state_runtime"]["lifecycles"][0])
    life.update(id="second",source_id="source:op",start_order=2,end_order=2,establishment_order=11)
    timer = copy.deepcopy(data["state_runtime"]["timers"][0])
    timer.update(id="second-timer",lifecycle_id="second",source_id="source:op",order=21,
        payload={"kind":"resource_operation","ref_id":"refill2"})
    data["state_runtime"]["lifecycles"].append(life)
    data["state_runtime"]["timers"].append(timer)
    records = [{"kind":kind,"id":row["id"],"status":"ready"}
        for kind in ("pools","operations","consumers") for row in shared[kind]]
    return data,rules,active,linked,shared,records


def test_state_pool_gate_state_closure_is_order_independent_and_preserves_inputs():
    args = chain()
    original = copy.deepcopy(args)
    data,rules,active,linked,pools,records = args
    result = invoke(data,rules,active,linked,excluded={"buff"},pools=pools,pool_adoptions=records)
    state, kept_pools, disabled, adopted, adopted_pools, issues = result
    assert state is None and kept_pools is None and disabled == {"buff","gate"} and not issues
    assert all(row["effective_status"] == "dependency_excluded" for row in adopted if row["lifecycle_id"] == "second")
    assert all(row["status"] == "dependency_excluded" for row in adopted_pools)
    assert args == original
    permuted = copy.deepcopy(args)
    data,rules,active,linked,pools,records = permuted
    for rows in [data["rules"], *data["state_runtime"].values(), *pools.values(), records]:
        if isinstance(rows,list): rows.reverse()
    assert invoke(data,dict(reversed(list(rules.items()))),active,linked,excluded={"buff"},
        pools=pools,pool_adoptions=records) == result


def test_state_does_not_restore_none_pool_or_change_pending_to_excluded():
    data,rules,active,linked,_,records = chain()
    for row in records: row["status"] = "pending"
    state,pools,_,adopted,adopted_pools,issues = invoke(data,rules,active,linked,
        pools=None,pool_adoptions=records,pool_disabled_rules={"gate"})
    assert state is None and pools is None and issues
    assert all("pending" in row["effective_status"] for row in adopted)
    assert all(row["status"] == "pending" for row in adopted_pools)


def test_pending_excluded_group_cannot_hide_invalid_timer_structure():
    args = graph()
    args[0]["state_runtime"]["timers"][0].update(status="pending",interval_us=0)
    with pytest.raises(PlanError): invoke(*args,excluded={"buff"})


def test_excluded_group_still_exposes_unregistered_matching_source():
    args = graph()
    args[0]["state_runtime"]["timers"][0]["source_id"] = "missing-source"
    args[0]["rules"][0]["source_id"] = "missing-source"
    # Lifecycle separately still refers to source:field, also now unavailable.
    args[3].clear()
    state,_,_,records,_,issues = invoke(*args,excluded={"buff"})
    assert state is None and issues
    assert all("pending" in row["effective_status"] for row in records)


def test_ready_authority_cannot_make_unavailable_dispatch_target_ready():
    data,rules,active,linked = graph()
    other = rule("authority",source_id="source:field")
    data["rules"].append(other)
    rules["authority"] = other
    active.clear()
    active.add("authority")
    linked.clear()
    linked["authority"] = ("source","authority")
    state,_,disabled,records,_,issues = invoke(data,rules,active,linked)
    assert state is None
    assert "buff" in disabled and issues
    assert all("pending" in row["effective_status"] for row in records)
