"""Independent real-queue checks; invented fields do not certify game facts."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import condition
from tests.test_state_runtime_execution import field_case


def test_real_queue_each_unknown_tick_is_pending_without_damage_promotion():
    template, rotation, model = field_case()
    model["rules"][0]["trigger"]["condition"] = "inside"
    condition(model, "inside", .5, 3)
    model["condition_events"][0]["value"] = None
    original = copy.deepcopy((template, rotation, model))
    result = execute_mechanics(template, rotation, model)
    assert result["status"] == "pending"
    assert result["result"] is None and result["resolved_template"] is None
    assert [row["outcome"] for row in result["state_runtime_trace"]] == ["pending", "pending"]
    assert result["state_runtime"]["engine_queue_integration"] == "mechanics_event_queue"
    assert (template, rotation, model) == original


@pytest.mark.parametrize("terminal", [False, True])
def test_exact_expiry_hit_observes_terminal_transaction_then_half_open_ttl(terminal):
    template, rotation, model = field_case(terminal=terminal)
    template["hits"][0]["t"] = .05
    template["hits"][2]["t"] = 2
    model["rules"][0]["timing"]["duration"] = 1
    result = execute_mechanics(template, rotation, model)
    assert result["result"] is not None, result["issues"]
    assert [row["expected"] for row in result["result"]["hits"]] == [
        90, 90, 90 if terminal else 45, 90 if terminal else 45, 45]
    terminal_rows = [row for row in result["state_runtime_trace"] if row["frame"] == "terminal_preclose"]
    assert len(terminal_rows) == int(terminal)
    if terminal:
        assert terminal_rows[0]["time_us"] == 2_000_000
        assert terminal_rows[0]["outcome"] == "activated"


def test_user_effect_filter_cannot_borrow_internal_route_authorization():
    from genshin_engine.state_runtime_mechanics import route_event_id
    template, rotation, model = field_case()
    model["rules"][0]["effects"][0]["filter"] = {
        "event": "hit", "phase": "pre", "event_ids": [route_event_id("dispatch_rule", "field-buff")]}
    result = execute_mechanics(template, rotation, model)
    assert result["result"] is None
    assert result["status"] in ("invalid", "pending")
    assert any(row["code"] == "reference" for row in result["issues"]), result["issues"]
