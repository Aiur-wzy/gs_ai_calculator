"""Real mechanics queue acceptance tests, initially red until hooks are installed."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_mechanics_execution import (
    ability,
    action,
    add,
    fixture,
    rule,
    second_character,
)
from tests.test_shared_pool_execution import setup as pool_setup
from tests.test_state_runtime import config


def field_case(timer_order=20, terminal=False):
    template, rotation, mechanics = fixture((.75, 1.2, 2.05, 2.5, 3.2))
    second_character(template, rotation)
    template["characters"][1]["stats"] = copy.deepcopy(template["characters"][0]["stats"])
    template["loop_duration"] = rotation["period"] = 4
    template["switches"] = [{"t": 1, "character": "b"}]
    rotation["abilities"] += [ability("switch", "a", "action.switch"), ability("b-skill", "b")]
    rotation["actions"] += [action("switch-b", "switch", 1, 1.01, 10, switch_to="b"),
                            action("cast-b", "b-skill", 1.05, 1.1, 11)]
    for row in rotation["actions"]: row["repeat"] = {"mode": "once"}
    for row in template["hits"][1:]: row["character"] = "b"
    for row in mechanics["hit_events"][1:]: row["action_id"] = "cast-b"
    add(mechanics, rule("field-buff", duration=1.1, source_id="source:field",
        trigger={"event": "condition", "phase": "pre", "event_ids": [route_event_id("dispatch_rule", "field-buff")]},
        beneficiaries={"mode": "frontline", "scope": "locked"}))
    state = config(end_tick="before_expiry" if terminal else "exclude")
    state["lifecycles"][0]["start"]["action_ids"] = ["cast"]
    state["timers"][0]["order"] = timer_order
    state["timers"][0]["payload"]["ref_id"] = "field-buff"
    mechanics["state_runtime"] = state
    return template, rotation, mechanics


@pytest.mark.parametrize("timer_order", [5, 20])
@pytest.mark.parametrize("terminal", [False, True])
def test_real_field_timer_matches_independent_manual_oracle(timer_order, terminal):
    values = field_case(timer_order, terminal)
    before = copy.deepcopy(values)
    result = execute_mechanics(*values)
    assert result["result"] is not None, result["issues"]
    expected = [90, 90 if timer_order > 10 else 45,
                90 if terminal or timer_order > 10 else 45, 90 if terminal else 45, 45]
    assert [h["expected"] for h in result["result"]["hits"]] == expected
    assert values == before


def test_real_timer_grants_shared_pool_then_hits_consume():
    template, rotation, mechanics = pool_setup(times=(.75, 1.2, 2.05), initial=0)
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["rules"][0]["timing"]["duration"] = 5
    mechanics["shared_pools"]["operations"][0]["trigger"] = {
        "event": "condition", "phase": "post", "event_ids": [route_event_id("resource_operation", "refill")]}
    state = config(first=1_000_000)
    state["lifecycles"][0]["start"]["action_ids"] = ["cast"]
    state["timers"][0].update(max_ticks=1, source_id="binding:refill", payload={"kind": "resource_operation", "ref_id": "refill"})
    mechanics["state_runtime"] = state
    result = execute_mechanics(template, rotation, mechanics)
    assert result["result"] is not None, result["issues"]
    assert [h["expected"] for h in result["result"]["hits"]] == [45, 90, 90]
    assert result["shared_pool_trace"][-1]["after"] == 1


def test_timer_instances_after_expiry_have_distinct_actual_identity():
    template, rotation, mechanics = field_case()
    mechanics["rules"][0]["timing"]["duration"] = .1
    result = execute_mechanics(template, rotation, mechanics)
    rows = [r for r in result["mechanics_trace"] if r["type"] == "activation"]
    assert len(rows) == 2
    assert len({r["instance_id"] for r in rows}) == 2
    assert len({r["run_id"] for r in rows}) == 2
    assert [r["ordinal"] for r in rows] == [1, 2]


@pytest.mark.parametrize("location", ["condition", "action", "hit"])
def test_user_cannot_define_internal_identity_even_without_state_model(location):
    from tests.test_mechanics_execution import condition
    template, rotation, mechanics = fixture((.2,))
    internal = route_event_id("dispatch_rule", "unused")
    if location == "condition": condition(mechanics, internal, .1)
    elif location == "action":
        rotation["actions"][0]["id"] = internal
        mechanics["hit_events"][0]["action_id"] = internal
    else:
        template["hits"][0]["id"] = internal
        mechanics["hit_events"][0]["hit_id"] = internal
    result = execute_mechanics(template, rotation, mechanics)
    assert result["status"] == "invalid" and result["result"] is None
    assert any(r["code"] == "state_reserved_identity" for r in result["issues"])


def test_unknown_dispatch_condition_preserves_pending_and_no_total():
    from tests.test_mechanics_execution import condition
    template, rotation, mechanics = field_case()
    mechanics["rules"][0]["trigger"]["condition"] = "inside"
    condition(mechanics, "inside", .5, 3)
    result = execute_mechanics(template, rotation, mechanics)
    assert result["status"] == "pending" and result["result"] is None and result["resolved_template"] is None
    assert result["state_runtime_trace"][0]["outcome"] == "pending"


def test_controlled_state_budget_keeps_real_completed_event_prefix(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "state_runtime" and ctx.time >= 1_000_000:
            raise StateRuntimeError("state_event_limit", "工程预算限制", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    result = execute_mechanics(*field_case())
    assert result["status"] == "pending" and result["result"] is None and result["resolved_template"] is None
    assert result["state_runtime_trace"][0]["outcome"] == "activated"
    assert any(r["id"] == "h0" and r["t"] == .75 for r in result["rotation_check"]["event_log"])


def test_old_input_does_not_acquire_empty_state_output():
    result = execute_mechanics(*fixture())
    assert "state_runtime" not in result and "state_runtime_trace" not in result
    assert "state_runtime" not in result["result"]


def test_terminal_preclose_reads_frontline_before_same_time_switch():
    template, rotation, mechanics = field_case(terminal=True)
    rotation["abilities"].append(ability("switch-back", "b", "action.switch"))
    rotation["actions"].append(action("back", "switch-back", 2, 2.01, 30, switch_to="a", repeat={"mode": "once"}))
    template["switches"].append({"t": 2, "character": "a"})
    result = execute_mechanics(template, rotation, mechanics)
    assert result["result"] is not None, result["issues"]
    assert result["result"]["hits"][3]["expected"] == 90
    final = [r for r in result["mechanics_trace"] if r["type"] == "activation"][-1]
    assert final["beneficiaries"] == ["b"] and final["state_frame"] == "terminal_preclose"


def test_switch_cancel_discards_pending_establishment_without_reversing_create_grant():
    from tests.test_shared_pools import config as shared_config
    template, rotation, mechanics = field_case()
    rotation["actions"][0].update(occupation_end=1.5, switch_allowed_at=.5)
    life = mechanics["state_runtime"]["lifecycles"][0]
    life["establishment_delay_us"] = 1_200_000
    shared = shared_config()
    shared["consumers"] = []
    shared["pools"][0]["initial"] = {"team": 0}
    shared["operations"][0].update(source_id="source:field", trigger={"event": "condition", "phase": "post",
        "event_ids": [route_event_id("resource_operation", "refill")]})
    life["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    mechanics["shared_pools"] = shared
    result = execute_mechanics(template, rotation, mechanics)
    assert result["result"] is not None, result["issues"]
    assert [h["expected"] for h in result["result"]["hits"]] == [45]*5
    assert len(result["state_runtime_trace"]) == 1
    assert result["result"]["shared_pools"]["state"]["balances"]["pool"]["team"] == 3
    assert any(r.get("reason") == "cancelled_before_establishment" for r in result["state_runtime"]["issued_trace"])


def test_boundary_retains_terminal_at_next_window_zero_without_absolute_uid():
    template, rotation, mechanics = fixture((.75, 1.2))
    template["loop_duration"] = rotation["period"] = 2
    add(mechanics, rule("buff", source_id="source:field", trigger={"event": "condition", "phase": "post",
        "event_ids": [route_event_id("dispatch_rule", "buff")]}))
    mechanics["state_runtime"] = config(end_tick="before_expiry")
    mechanics["state_runtime"]["lifecycles"][0]["start"]["action_ids"] = ["cast"]
    result = execute_mechanics(template, rotation, mechanics)
    assert result["result"] is not None, result["issues"]
    boundary = result["rotation_check"]["cycles"][0]["boundary_state"]
    state = boundary["mechanics"]["state_runtime"]["lifecycles"]["field"]
    assert state["end_in_us"] == 0 and state["timers"]["refresh"]["next_in_us"] == 0
    assert state["timers"]["refresh"]["remaining"] == 1
    assert "@generation:" not in str(boundary) and "@state:" not in str(boundary)
    end_tick = [r for r in result["state_runtime_trace"] if r["time_us"] == 2_000_000 and r["frame"] == "terminal_preclose"]
    assert len(end_tick) == 1
