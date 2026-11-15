"""Full runtime targets remain equal to the graph originally checked by core.

This does not certify external App source approval; no source certificate is
created here and intentionally malicious Python can still replace private code.
"""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize("change", ["value", "owner"])
def test_changed_final_periodic_rule_cannot_use_original_target_permission(monkeypatch, change):
    original = StateRuntimeMechanics._apply_lifecycle_task
    mutated = []
    def apply(bridge, task, **kwargs):
        if change == "value":
            bridge.ctx.rules["boost"]["effects"][0]["value"] = 200
        else:
            bridge.ctx.rules["boost"]["owner"] = "unreviewed-owner"
        mutated.append(True)
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    out = execute_mechanics(*life_case())
    assert mutated and out["result"] is None
    assert any(i["code"] == "state_dispatch_target_changed" for i in out["issues"]), out["issues"]
    assert not any(r.get("type") == "activation" and r.get("rule_id") == "boost" for r in out["mechanics_trace"])


def test_changed_final_lifecycle_cannot_start_from_old_program_permission(monkeypatch):
    original = StateRuntimeMechanics._apply_lifecycle_task
    def apply(bridge, task, **kwargs):
        bridge.clock.lives["field_b"]["duration_us"] += 1
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    out = execute_mechanics(*life_case())
    assert out["result"] is None
    assert any(i["code"] == "state_threshold_authority" for i in out["issues"]), out["issues"]
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])


@pytest.mark.parametrize("change", ["copied_index", "missing_index", "new_timer"])
def test_changed_timer_lookup_cannot_hide_behind_unchanged_declaration(monkeypatch, change):
    original = StateRuntimeMechanics._apply_lifecycle_task
    def apply(bridge, task, **kwargs):
        if change == "copied_index":
            bridge.clock.by_life["field_b"] = copy.deepcopy(bridge.clock.by_life["field_b"])
            bridge.clock.by_life["field_b"][0]["first_offset_us"] = 10_000
        elif change == "missing_index":
            bridge.clock.by_life["field_b"] = []
        else:
            timer = copy.deepcopy(bridge.clock.timers["field_b_tick"])
            timer["id"] = "unapproved_tick"
            bridge.clock.timers[timer["id"]] = timer
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    out = execute_mechanics(*life_case())
    assert out["result"] is None
    assert any(i["code"] == "state_threshold_authority" for i in out["issues"]), out["issues"]
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])


@pytest.mark.parametrize("at", ["establish", "first_tick", "later_tick"])
def test_timer_mutation_after_start_is_rejected_before_future_clock_work(monkeypatch, at):
    original = StateRuntimeMechanics.handle
    seen = []
    def handle(bridge, queued):
        target = queued["data"]
        if target["lifecycle_id"] == "field_b" and (
            at == "establish" and target["kind"] == "state_establish"
            or at == "first_tick" and target["kind"] == "state_tick" and target["ordinal"] == 1
            or at == "later_tick" and target["kind"] == "state_tick" and target["ordinal"] == 2
        ):
            bridge.clock.timers["field_b_tick"]["interval_us"] = 20_000
            seen.append(True)
        return original(bridge, queued)
    monkeypatch.setattr(StateRuntimeMechanics, "handle", handle)
    args = life_case()
    if at == "establish":
        args[2]["state_runtime"]["lifecycles"][1]["establishment_delay_us"] = 10_000
    out = execute_mechanics(*args)
    assert seen and out["result"] is None
    assert any(i["code"] == "state_threshold_authority" for i in out["issues"]), out["issues"]
    ticks = [r for r in out["state_runtime_trace"] if r.get("timer_id") == "field_b_tick"]
    assert len(ticks) == (1 if at == "later_tick" else 0)
