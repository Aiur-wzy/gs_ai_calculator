"""Off-tree actual mechanics closure; engineering facts, not game clock proof."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics, route_event_id
from tests.test_resource_threshold_counter_execution import counter_threshold_case


def life_case(*, counter=True, end=False, native=None):
    args = counter_threshold_case(times=(.24, .27, .34, .37, .45))
    model = args[2]
    state = model["state_runtime"]
    state["schema_version"] = 2
    model["resource_threshold_triggers"]["schema_version"] = 3
    binding = model["resource_threshold_triggers"]["bindings"][0]
    binding["payload"] = {"kind": "start_lifecycle", "ref_id": "field_b"}
    field = copy.deepcopy(state["lifecycles"][0])
    field.update(id="field_b", source_id="source:effect", start_order=1, end_order=2,
        establishment_order=11, duration_us=200_000, pending_cancel="never_after_success",
        start={"kind": "resource_threshold", "binding_id": binding["id"]})
    state["lifecycles"].append(field)
    timer = copy.deepcopy(state["timers"][0])
    timer.update(id="field_b_tick", lifecycle_id="field_b", source_id="source:effect",
        first_offset_us=50_000, max_ticks=2, order=30,
        payload={"kind": "dispatch_rule", "ref_id": "boost"})
    state["timers"].append(timer)
    target = model["rules"][-1]
    target["timing"]["duration"] = .08
    target["trigger"] = {"event": "condition", "phase": "pre",
        "event_ids": [route_event_id("dispatch_rule", "boost")]}
    target["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    if not counter:
        model.pop("resource_counters")
        observer = model["resource_threshold_observers"]["observers"][0]
        observer.update(input={"kind": "pool_balance_key", "pool_id": "pool", "key": "team"},
            comparison="enter_lte", threshold_atoms=3)
    if end:
        observer = copy.deepcopy(model["resource_threshold_observers"]["observers"][0])
        observer.update(id="empty", input={"kind": "pool_balance_key", "pool_id": "pool", "key": "team"},
            comparison="enter_lte", threshold_atoms=0)
        model["resource_threshold_observers"]["observers"].append(observer)
        ending = copy.deepcopy(binding)
        ending.update(id="empty-end", observer_id="empty", order=2,
            payload={"kind": "end_lifecycle", "ref_id": "field_b"})
        model["resource_threshold_triggers"]["bindings"].append(ending)
        field["end_trigger"] = {"kind": "resource_threshold", "binding_id": "empty-end"}
    if native:
        state["timers"] = [timer]
        model["shared_pools"]["operations"][0]["trigger"] = {"event": "hit", "phase": native}
    return args


@pytest.mark.parametrize("counter", [True, False])
def test_actual_timer_threshold_starts_periodic_life_without_manual_action(counter):
    args = life_case(counter=counter)
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["expected"] for r in out["result"]["hits"]] == [45, 90, 45, 90, 45]
    assert args == before and not args[2]["condition_events"]
    assert len(args[1]["actions"]) == 1
    starts = [r for r in out["state_runtime"]["issued_trace"]
        if r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b"]
    assert len(starts) == 1 and starts[0]["time_us"] == 200_000
    completed, = out["resource_threshold_triggers"]["trace"]
    assert completed["phase"] == "post" and completed["owner"] == "a"
    assert completed["source_id"] == "source:effect"
    assert completed["source_event_id"] == route_event_id("resource_operation", "refill")
    assert completed["source_run_id"] == out["shared_pool_trace"][1]["run_id"]
    assert completed["receipt_span"] == [2, 2]
    assert completed["source_origin"]["timer_id"] == "refresh"
    assert completed["before"] == (8 if counter else 11) and completed["after"] == (16 if counter else 3)
    assert completed["target_generation_uid"] == starts[0]["generation_uid"]
    if counter:
        assert completed["counter_observation"]["counter_id"] == "count"


def test_threshold_end_cancels_future_ticks_but_retains_buff_residual():
    out = execute_mechanics(*life_case(end=True))
    assert out["result"] is not None, out["issues"]
    assert [r["expected"] for r in out["result"]["hits"]] == [45, 90, 45, 45, 45]
    ticks = [r for r in out["state_runtime_trace"] if r.get("timer_id") == "field_b_tick"]
    assert len(ticks) == 1 and ticks[0]["time_us"] == 250_000


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_native_resource_start_returns_to_original_parent_observe(phase):
    out = execute_mechanics(*life_case(native=phase))
    assert out["result"] is not None, out["issues"]
    assert [r["expected"] for r in out["result"]["hits"]] == [45, 45, 90, 90, 90]


@pytest.mark.parametrize("counter", [True, False])
def test_actual_delivery_pending_return_cannot_open_life_even_after_resource_commit(monkeypatch, counter):
    original = StateRuntimeMechanics._dispatch
    def dispatch(bridge, command):
        result = original(bridge, command)
        return "pending" if command["time_us"] == 200_000 and command["payload"]["kind"] == "resource_operation" else result
    monkeypatch.setattr(StateRuntimeMechanics, "_dispatch", dispatch)
    args = life_case(counter=counter)
    args[0]["hits"][0]["t"] = .15
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert out["shared_pool_trace"][-1]["after"] == 3
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])
    assert out["resource_threshold_triggers"]["status"] == "failed"
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])


def test_target_clock_budget_rejection_keeps_true_resource_prefix_and_no_total(monkeypatch):
    original = StateRuntime._finish
    def finish(clock, draft, time):
        if any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in draft["trace"]):
            previous = clock._reserve_external
            def reject(count):
                raise StateRuntimeError("state_event_limit", "injected target joint reserve", severity="pending")
            clock._reserve_external = reject
            try: return original(clock, draft, time)
            finally: clock._reserve_external = previous
        return original(clock, draft, time)
    monkeypatch.setattr(StateRuntime, "_finish", finish)
    out = execute_mechanics(*life_case())
    assert out["status"] == "pending" and out["result"] is None
    assert out["resource_counters"]["applied_state"]["values"]["count"]["total"] == 16
    assert out["shared_pool_trace"][-1]["after"] == 3
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])


def test_mixed_targets_reject_whole_committed_batch_before_any_target():
    from tests.test_mechanics_execution import add, rule
    args = life_case()
    model = args[2]
    extra = copy.deepcopy(model["resource_threshold_triggers"]["bindings"][0])
    extra.update(id="constant-mixed", order=2, source_id="source:other", payload={"kind": "activate_rule", "ref_id": "other"})
    model["resource_threshold_triggers"]["bindings"].append(extra)
    add(model, rule("other", domain="provider", source_id="source:other", trigger={"kind": "resource_threshold", "binding_id": "constant-mixed"}))
    out = execute_mechanics(*args)
    assert out["status"] == "invalid" and out["result"] is None
    assert out["shared_pool_trace"][-1]["after"] == 3
    assert not any(r["type"] == "activation" for r in out["mechanics_trace"])
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])


def test_real_task_and_batch_cannot_be_copied_modified_or_only_claimed(monkeypatch):
    original = StateRuntimeMechanics._apply_lifecycle_task
    seen = []
    def apply(bridge, task, *, source_record=None):
        before = copy.deepcopy((bridge.clock._states, bridge._applied_control_sequence, bridge.clock._position_index.__dict__))
        with pytest.raises(StateRuntimeError): original(bridge, dict(task), source_record=source_record)
        old = task["qualified"]
        task["qualified"] = not old
        with pytest.raises(StateRuntimeError): original(bridge, task, source_record=source_record)
        task["qualified"] = old
        source_record["applied"] -= 1
        with pytest.raises(StateRuntimeError): original(bridge, task, source_record=source_record)
        source_record["applied"] += 1
        old_sequence = source_record["batch"]["sequence"]
        source_record["batch"]["sequence"] += 1
        with pytest.raises(StateRuntimeError): original(bridge, task, source_record=source_record)
        source_record["batch"]["sequence"] = old_sequence
        assert (bridge.clock._states, bridge._applied_control_sequence, bridge.clock._position_index.__dict__) == before
        seen.append(task["command"]["dispatch_run_id"])
        return original(bridge, task, source_record=source_record)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    out = execute_mechanics(*life_case())
    assert out["result"] is not None, out["issues"]
    assert len(seen) == 1


def test_spawned_periodic_buff_remains_classic_only_even_when_swirl_is_generated():
    from tests.test_mechanics_swirl import setup
    args = life_case()
    elemental = setup(times=(.27,))
    args[2]["elemental_model"] = elemental[2]["elemental_model"]
    for hit, descriptor in zip(args[0]["hits"], args[2]["hit_events"], strict=True):
        hit["element"] = "anemo"
        descriptor["application"] = copy.deepcopy(elemental[2]["hit_events"][0]["application"])
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert any(r.get("reason") == "unsupported_threshold_damage_scope" and r.get("rule_id") == "boost" for r in out["mechanics_trace"])
    swirls = [r["expected"] for r in out["result"]["hits"] if r["damage_system"] == "transformative"]
    assert swirls[:2] == pytest.approx([781.299, 781.299])


def add_second_target(args):
    model = args[2]
    binding = copy.deepcopy(model["resource_threshold_triggers"]["bindings"][0])
    binding.update(id="start-c", source_id="source:c", order=2, payload={"kind": "start_lifecycle", "ref_id": "field_c"})
    model["resource_threshold_triggers"]["bindings"].append(binding)
    life = copy.deepcopy(model["state_runtime"]["lifecycles"][1])
    life.update(id="field_c", source_id="source:c", start_order=2, establishment_order=12, end_order=3,
        start={"kind": "resource_threshold", "binding_id": "start-c"})
    model["state_runtime"]["lifecycles"].append(life)


@pytest.mark.parametrize("counter", [True, False])
def test_each_target_new_controls_really_complete_before_next_target(monkeypatch, counter):
    args = life_case(counter=counter)
    add_second_target(args)
    original = StateRuntimeMechanics._apply_lifecycle_task
    seen = []
    def apply(bridge, task, **kwargs):
        if task["command"]["payload"]["ref_id"] == "field_c":
            assert bridge._applied_lifecycles["field_b"]["phase"] == "established"
            assert bridge._applied_control_sequence == bridge.clock._control_sequence
            if counter:
                assert bridge.ctx.shared_pools.ledger._counter_state["lifecycles"]["field_b"]["phase"] == "established"
            seen.append(True)
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, "_apply_lifecycle_task", apply)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert seen == [True]


@pytest.mark.parametrize("counter", [True, False])
def test_second_target_failure_retains_first_applied_target_for_counter_and_non_counter(monkeypatch, counter):
    args = life_case(counter=counter)
    add_second_target(args)
    original = StateRuntimeMechanics._v2_trace_candidate
    def prepare(bridge, row):
        if row.get("lifecycle_id") == "field_c" and row.get("kind") == "lifecycle_start":
            raise StateRuntimeError("state_trace_limit", "injected second target failure", severity="pending")
        return original(bridge, row)
    monkeypatch.setattr(StateRuntimeMechanics, "_v2_trace_candidate", prepare)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert out["shared_pool_trace"][-1]["after"] == 3
    applied = out["state_runtime"]["applied_lifecycles"]
    assert applied["field_b"]["phase"] == "established" and "field_c" not in applied
    target_trace = out["resource_threshold_triggers"]["trace"]
    assert any(r.get("lifecycle_id") == "field_b" and r.get("outcome") == "lifecycle_started" for r in target_trace)
    assert any(r.get("lifecycle_id") == "field_c" and r["type"] == "threshold_dispatch_failed" for r in target_trace)


def test_old_end_applied_then_new_create_failure_keeps_actual_old_end(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
    from tests.test_mechanics_execution import action
    args = life_case()
    args[2]["state_runtime"]["lifecycles"][1]["duration_us"] = 1_000_000
    args[2]["shared_pools"]["pools"][0]["initial"]["team"] = 100
    args[1]["actions"].append(action("again", start=.3, end=.31, order=5, repeat={"mode": "once"}))
    args[2]["state_runtime"]["lifecycles"][0]["start"]["action_ids"].append("again")
    original = SharedPoolLedger.apply_counter_control
    starts = []
    def apply(ledger, step):
        if step["lifecycle_id"] == "field_b" and step["kind"] == "lifecycle_start":
            starts.append(step["generation_uid"])
            if len(starts) == 2:
                raise SharedPoolError("counter_limit", "injected new create rejection", severity="pending")
        return original(ledger, step)
    monkeypatch.setattr(SharedPoolLedger, "apply_counter_control", apply)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert len(starts) == 2 and starts[0] != starts[1]
    assert out["resource_counters"]["applied_state"]["lifecycles"]["field_b"]["phase"] == "ended"
    assert out["state_runtime"]["applied_lifecycles"]["field_b"]["phase"] == "ended"
    assert out["state_runtime"]["applied_lifecycles"]["field_b"]["generation_uid"] == starts[0]


@pytest.mark.parametrize("rejection", ["false", "cooldown"])
def test_known_false_or_cooldown_source_is_completed_without_starting_target(rejection):
    from tests.test_mechanics_execution import condition
    args = life_case()
    operation = args[2]["shared_pools"]["operations"][0]
    if rejection == "false":
        operation["trigger"]["condition"] = "inside"
        condition(args[2], "inside", 0, 2)
        args[2]["condition_events"][-1]["value"] = False
    else:
        operation["cooldown"] = {"seconds": 1, "anchor": "success"}
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45]*5
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])
    completed = [r for r in out["state_runtime_trace"] if r["type"] == "state_dispatch_completed"]
    assert len(completed) == 3
    assert all(r["outcome"] == ("not_activated" if rejection == "false" else "resource_operation") for r in completed)
    assert not any(r["type"] == "state_delivery_failed" for r in out["state_runtime_trace"])


def test_unknown_occurrence_blocks_actual_batch_even_if_issue_already_deduplicated(monkeypatch):
    from tests.test_mechanics_execution import condition
    args = life_case()
    operation = args[2]["shared_pools"]["operations"][0]
    operation["trigger"]["condition"] = "inside"
    condition(args[2], "inside", 0, 2)
    args[2]["condition_events"][-1]["value"] = None
    original = StateRuntimeMechanics._dispatch
    def dispatch(bridge, command):
        event = {"event": "condition", "id": route_event_id("resource_operation", "refill"),
            "phase": "post", "origin": 0, "conditions": {"inside": None}}
        # Seed the exact old diagnostic, as a prior rejected occurrence would.
        bridge.ctx.filter(operation["trigger"], event, "refill", owner="a")
        return original(bridge, command)
    monkeypatch.setattr(StateRuntimeMechanics, "_dispatch", dispatch)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    completed = [r for r in out["state_runtime_trace"] if r["type"] == "state_dispatch_completed"]
    assert [r["outcome"] for r in completed] == ["pending"]
    assert any(i["code"] == "state_dispatch_pending" for i in out["issues"])
    assert not any(r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b" for r in out["state_runtime"]["issued_trace"])


def test_false_binding_does_not_start_life_or_invent_position_consumption():
    from tests.test_mechanics_execution import condition
    args = life_case()
    args[2]["resource_threshold_triggers"]["bindings"][0]["qualification"] = {"condition": "allow"}
    condition(args[2], "allow", 0, 2)
    args[2]["condition_events"][-1]["value"] = False
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45]*5
    assert out["resource_counters"]["applied_state"]["values"]["count"]["total"] == 19
    assert [r["outcome"] for r in out["resource_threshold_triggers"]["trace"]] == ["not_activated"]


def two_native_costs_case(*, independent_second=True):
    """One hit incurs cost A, resets the meter, then incurs independent cost B.

    The old fixture copied A's declaration under a new operation id. That
    supplies neither a second source part nor a second actual occurrence.
    This synthetic mechanism explicitly declares B as a separate cost source;
    the App tests additionally require its own current source approval.
    """
    args = life_case(counter=False, native="pre")
    operations = args[2]["shared_pools"]["operations"]
    operations[0]["amount"] = 16
    operations[0]["trigger"]["event_ids"] = ["h0"]
    operations[0]['source'] = 'Synthetic primary cost A: h0 consumes 16 atoms from the shared meter.'
    refill = copy.deepcopy(operations[0])
    refill.update(id="restore", operation="set", amount=19, order=2)
    refill['source'] = 'Synthetic reset: after cost A and before independent cost B, reset the meter to 19.'
    refill.pop("spend_policy")
    second = copy.deepcopy(operations[0])
    second.update(id="spend-again", order=3)
    if independent_second:
        from tests.test_mechanics_execution import add, rule
        second.update(source_id='source:followup_cost',
            source='Synthetic independent cost B: a separate declared effect of the same hit consumes 16 atoms; not a replay of cost A.')
        add(args[2], rule('followup-cost-authority', source_id='source:followup_cost', effects=[]))
    operations.extend([refill, second])
    args[2]["resource_threshold_observers"]["observers"][0]["causes"]["operation_ids"] = ["refill", "spend-again"]
    return args


def test_same_native_parent_two_crossings_have_two_actual_generations():
    args = two_native_costs_case()
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    starts = [r for r in out["state_runtime"]["issued_trace"] if r["type"] == "lifecycle_start" and r["lifecycle_id"] == "field_b"]
    assert len(starts) == 2 and {r["time_us"] for r in starts} == {240_000}
    assert len({r["generation_uid"] for r in starts}) == 2
    ticks = [r for r in out["state_runtime_trace"] if r["type"] == "state_dispatch_completed" and r.get("timer_id") == "field_b_tick"]
    assert [r["time_us"] for r in ticks] == [290_000, 390_000]
    costs = [r for r in out['shared_pool_trace'] if r.get('operation') == 'spend']
    assert [r['accepted'] for r in costs] == [16, 16]
    assert len({r['source_id'] for r in costs}) == 2
    assert {r['run_id'] for r in costs} == {'0:h0'}


def test_reset_and_new_operation_alias_do_not_authorize_same_source_cost_twice():
    out = execute_mechanics(*two_native_costs_case(independent_second=False))
    assert out['result'] is None and out['status'] == 'invalid'
    issue, = [r for r in out['issues'] if r['code'] == 'pool_duplicate_benefit']
    assert issue['details']['incoming_operation_id'] == 'spend-again'
    assert issue['details']['existing_operation_ids'] == ['refill']
    assert [(r['operation'], r['after']) for r in out['shared_pool_trace']] == [('spend', 3), ('set', 19)]


def test_new_field_half_open_expiry_prevents_last_tick_without_erasing_residual():
    args = life_case()
    args[2]["state_runtime"]["lifecycles"][1]["duration_us"] = 150_000
    for hit, t in zip(args[0]["hits"], [.25, .329999, .33, .35, .37], strict=True):
        hit["t"] = t
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    # The first hit order=10 precedes timer order=30 at the same .25 s.
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 45, 45, 45]
    ticks = [r for r in out["state_runtime_trace"] if r["type"] == "state_dispatch_completed" and r.get("timer_id") == "field_b_tick"]
    assert [r["time_us"] for r in ticks] == [250_000]
