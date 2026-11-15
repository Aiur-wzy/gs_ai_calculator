"""Bridge protocol tests. The real rotation/schema hooks are not installed yet."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import _Mechanics
from genshin_engine.shared_pool_runtime import SharedPoolRuntime
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics, route_event_id
from tests.test_mechanics_execution import rule
from tests.test_state_runtime import config


def model():
    cfg = config()
    target = rule("buff", source_id="source:field",
                  trigger={"event": "condition", "phase": "post", "event_ids": [route_event_id("dispatch_rule", "buff")]})
    return {"state_runtime": cfg, "rules": [target], "condition_events": []}


class Context:
    def __init__(self, data):
        self.model = copy.deepcopy(data)
        self.rules = {r["id"]: r for r in self.model["rules"]}
        self.time, self.frontline, self.conditions = 0, "a", {}
        self.issues, self.trace, self.calls = [], [], []
        self.budget = 0
        self.shared_pools = None
        self.attribute_runtime = None
        self.state = {rid: {"instances": [], "cooldown": 0, "pending": []} for rid in self.rules}
        self.data = {"chars": {"a": {}, "b": {}}}
        self.use_real_phase = False
        if "shared_pools" in self.model:
            self.shared_pools = SharedPoolRuntime(self.model, ["a", "b"])

    def snapshot(self): return {"time": self.time, "states": copy.deepcopy(self.state)}
    def freeze_panel(self, snapshot): return copy.deepcopy(snapshot)
    beneficiaries = _Mechanics.beneficiaries
    activate = _Mechanics.activate
    _activate_or_delay = _Mechanics._activate_or_delay
    expire = _Mechanics.expire

    def _count_event(self, kind=None): self._reserve_events(1, kind)

    def _reserve_events(self, count, kind=None):
        if self.budget + count > 40_000:
            raise StateRuntimeError("state_event_limit", "test budget", severity="pending")
        self.budget += count

    def filter(self, selector, event, path, *, owner=None):
        from genshin_engine.mechanics import _Mechanics
        return _Mechanics.filter(self, selector, event, path, owner=owner)

    def phase(self, event, phase, parent, *, allowed_rule_ids=None, allowed_operation_ids=None,
              observe_state=True, state_authority=None):
        assert observe_state is False
        self.calls.append({"event": copy.deepcopy(event), "phase": phase, "parent": copy.deepcopy(parent),
                           "rules": allowed_rule_ids, "operations": allowed_operation_ids})
        if self.use_real_phase:
            _Mechanics.phase(self, event, phase, parent, allowed_rule_ids=allowed_rule_ids,
                             allowed_operation_ids=allowed_operation_ids, observe_state=observe_state,
                             state_authority=state_authority)
        else:
            self.bridge.authorize_dispatch(event, phase, parent, allowed_rule_ids=allowed_rule_ids,
                                           allowed_operation_ids=allowed_operation_ids, authority=state_authority)


def start_event(t=0, run="0:cast", order=0):
    return ({"event": "action_start", "id": "cast", "run_id": run, "origin": 0,
             "character": "a", "action_id": "cast", "conditions": {}},
            {"id": "cast", "run_id": run, "origin": 0, "time": t, "order": order, "causal": ()})


def setup(data=None, *, conditions=(), external_events=()):
    ctx = Context(data or model())
    ctx.bridge = StateRuntimeMechanics(ctx.model, ctx, character_ids=["a", "b"],
        event_ids={"cast"}, action_ids={"cast"}, conditions=set(conditions), external_events=external_events)
    ctx.state_runtime = ctx.bridge
    return ctx


def test_start_dispatch_calls_existing_phase_protocol_and_has_true_parent():
    ctx = setup()
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    assert len(ctx.calls) == 2 and ctx.calls[-1]["rules"] == frozenset({"buff"})
    assert ctx.calls[-1]["operations"] == frozenset()
    assert ctx.calls[-1]["event"]["state_event"] == "timer_tick"
    assert ctx.calls[-1]["event"]["source_event_id"] == "cast"
    assert ctx.calls[-1]["parent"]["run_id"] != "0:cast"
    assert queued and ctx.bridge.report()["engine_queue_integration"] == "adapter_protocol_only"


def test_native_cannot_copy_reserved_route_or_internal_authority():
    ctx = setup()
    ctx.bridge.observe(*[start_event()[0], "post", start_event()[1]], successful=True)
    call = ctx.calls[-1]
    with pytest.raises(StateRuntimeError):
        ctx.bridge.authorize_dispatch(call["event"], "post", call["parent"],
            allowed_rule_ids=call["rules"], allowed_operation_ids=call["operations"], authority=object())
    bad = model()
    bad["condition_events"] = [{"id": route_event_id("dispatch_rule", "buff")}]
    with pytest.raises(StateRuntimeError): setup(bad)


def test_failed_action_does_not_start_clock():
    ctx = setup()
    event, parent = start_event()
    assert ctx.bridge.observe(event, "post", parent, successful=False) == []
    assert ctx.calls == [] and ctx.bridge.clock.pending() == []


def test_real_phase_refreshes_one_instance_and_changes_frontline_recipient():
    data = model()
    data["rules"][0]["beneficiaries"] = {"mode": "frontline", "scope": "locked"}
    ctx = setup(data)
    ctx.use_real_phase = True
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    assert ctx.state["buff"]["instances"][0]["beneficiaries"] == ["a"]
    ctx.expire(1_000_000)
    ctx.frontline = "b"
    ctx.bridge.handle(next(q for q in queued if q["data"]["kind"] == "state_tick"))
    assert [r["beneficiaries"] for r in ctx.state["buff"]["instances"]] == [["a"], ["b"]]
    assert all(len(r["expires"]) == 1 for r in ctx.state["buff"]["instances"])
    assert ctx.bridge.report()["execution_trace"][-1]["outcome"] == "activated"


def pool_model(operation="grant"):
    from tests.test_shared_pools import config as pool_config
    data = model()
    data["rules"] = []
    pools = pool_config()
    pools["consumers"] = []
    op = pools["operations"][0]
    op.update(source_id="source:field", operation=operation,
              trigger={"event": "condition", "phase": "post", "event_ids": [route_event_id("resource_operation", "refill")]})
    data["shared_pools"] = pools
    data["state_runtime"]["timers"][0]["payload"] = {"kind": "resource_operation", "ref_id": "refill"}
    return data


@pytest.mark.parametrize(("operation", "first", "second"), [("grant", 13, 16), ("set", 3, 3)])
def test_real_shared_pool_phase_receives_typed_timer_dispatch(operation, first, second):
    ctx = setup(pool_model(operation))
    ctx.use_real_phase = True
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    assert ctx.shared_pools.ledger.snapshot()["balances"]["pool"]["team"] == first
    ctx.expire(1_000_000)
    ctx.bridge.handle(next(q for q in queued if q["data"]["kind"] == "state_tick"))
    assert ctx.shared_pools.ledger.snapshot()["balances"]["pool"]["team"] == second
    assert ctx.bridge.report()["execution_trace"][-1]["outcome"] == "resource_operation"
    assert len({r["run_id"] for r in ctx.shared_pools.trace}) == 2


def test_queue_outer_metadata_cannot_change_after_clock_signing():
    ctx = setup()
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    tick = next(q for q in queued if q["data"]["kind"] == "state_tick")
    tick["order"] = 999
    ctx.expire(tick["time"])
    with pytest.raises(StateRuntimeError): ctx.bridge.handle(tick)


def test_target_change_after_bridge_validation_cannot_borrow_old_source():
    ctx = setup()
    ctx.rules["buff"]["source_id"] = "unapproved"
    event, parent = start_event()
    with pytest.raises(StateRuntimeError): ctx.bridge.observe(event, "post", parent, successful=True)


@pytest.mark.parametrize("mutation", ["source", "owner", "wide_trigger", "two_targets", "unowned_route",
                                     "gate", "quota", "delay", "stacking", "hit_parent", "unknown_parent"])
def test_ambiguous_or_unsupported_graph_is_rejected(mutation):
    data = model()
    target = data["rules"][0]
    if mutation == "source": target["source_id"] = "other"
    elif mutation == "owner": target["owner"] = "b"
    elif mutation == "wide_trigger": target["trigger"].pop("event_ids")
    elif mutation == "two_targets":
        data["state_runtime"]["lifecycles"][0]["hooks"]["create"] = [{"kind": "dispatch_rule", "ref_id": "buff", "order": 1}]
    elif mutation == "unowned_route": data["state_runtime"]["timers"] = []
    elif mutation == "gate": target["resource_gate"] = {"consumer_id": "unknown"}
    elif mutation == "quota": target["quota"] = {}
    elif mutation == "delay": target["timing"]["delay"] = 1
    elif mutation == "stacking": target["stacking"]["max"] = 2
    elif mutation == "hit_parent": data["state_runtime"]["lifecycles"][0]["start"]["event"] = "hit"
    else: data["state_runtime"]["lifecycles"][0]["start"]["event_ids"] = ["unknown"]
    with pytest.raises(StateRuntimeError): setup(data)


@pytest.mark.parametrize("location", ["condition_events", "application_events", "hit_patterns", "hit_events", "expanded"])
def test_native_reserved_identity_is_rejected_at_every_bridge_input(location):
    data = model()
    internal = route_event_id("dispatch_rule", "buff")
    if location == "expanded":
        ctx = Context(data)
        with pytest.raises(StateRuntimeError):
            StateRuntimeMechanics(data, ctx, character_ids=["a"], event_ids={internal}, action_ids={"cast"}, conditions=set(), external_events=[])
    else:
        data[location] = [{"hit_id" if location == "hit_events" else "id": internal}]
        with pytest.raises(StateRuntimeError): setup(data)


@pytest.mark.parametrize(("condition", "outcome"), [(None, "pending"), (False, "not_activated"), (True, "activated")])
def test_real_filter_keeps_unknown_false_true_distinct(condition, outcome):
    data = model()
    data["rules"][0]["trigger"]["condition"] = "inside"
    ctx = setup(data, conditions={"inside"})
    ctx.conditions["inside"] = condition
    ctx.use_real_phase = True
    event, parent = start_event()
    ctx.bridge.observe(event, "post", parent, successful=True)
    assert ctx.bridge.report()["execution_trace"][-1]["outcome"] == outcome
    assert bool(ctx.state["buff"]["instances"]) == (condition is True)


def test_internal_cooldown_and_same_source_refresh_remain_existing_phase_behavior():
    data = model()
    data["rules"][0]["timing"]["cooldown"] = 1.5
    ctx = setup(data)
    ctx.use_real_phase = True
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    ctx.expire(1_000_000)
    ctx.bridge.handle(next(q for q in queued if q["data"]["kind"] == "state_tick"))
    assert len(ctx.state["buff"]["instances"]) == 1
    assert ctx.state["buff"]["instances"][0]["expires"] == [2_000_000]
    assert ctx.bridge.report()["execution_trace"][-1]["outcome"] == "not_activated"


def test_terminal_does_not_bypass_frontline_filter_or_reopen_lifecycle():
    data = model()
    data["state_runtime"] = config(end_tick="before_expiry")
    data["rules"][0]["trigger"]["owner_presence"] = "frontline"
    ctx = setup(data)
    ctx.use_real_phase = True
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    ctx.expire(1_000_000)
    ctx.bridge.handle(next(q for q in queued if q["data"]["kind"] == "state_tick"))
    ctx.expire(2_000_000)
    ctx.frontline = "b"
    ctx.bridge.handle(next(q for q in queued if q["data"]["kind"] == "state_expire"))
    final = ctx.bridge.report()["execution_trace"][-1]
    assert final["frame"] == "terminal_preclose" and final["outcome"] == "not_activated"
    assert not ctx.bridge.clock.active("field", 2_000_000)


def test_completed_resource_prefix_survives_second_dispatch_failure():
    data = pool_model()
    data["rules"] = model()["rules"]
    data["state_runtime"]["timers"] = []
    data["state_runtime"]["lifecycles"][0]["hooks"]["create"] = [
        {"kind": "resource_operation", "ref_id": "refill", "order": 1},
        {"kind": "dispatch_rule", "ref_id": "buff", "order": 2}]
    ctx = setup(data)
    ctx.use_real_phase = True
    ctx.rules["buff"]["source_id"] = "changed_after_validation"
    event, parent = start_event()
    with pytest.raises(StateRuntimeError): ctx.bridge.observe(event, "post", parent, successful=True)
    assert ctx.shared_pools.ledger.snapshot()["balances"]["pool"]["team"] == 13
    report = ctx.bridge.report()
    assert report["status"] == "failed"
    assert [r["type"] for r in report["execution_trace"]] == ["state_dispatch_completed", "state_dispatch_failed"]
    assert report["execution_trace"][0]["outcome"] == "resource_operation"
    with pytest.raises(StateRuntimeError): ctx.bridge.observe(event, "post", parent, successful=True)


def test_cancel_before_establishment_keeps_create_grant_and_discards_later_buff():
    data = pool_model()
    data["rules"] = model()["rules"]
    data["state_runtime"]["timers"] = []
    life = data["state_runtime"]["lifecycles"][0]
    life["establishment_delay_us"] = 500_000
    life["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    life["hooks"]["established"] = [{"kind": "dispatch_rule", "ref_id": "buff", "order": 1}]
    ctx = setup(data)
    ctx.use_real_phase = True
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    ctx.expire(100_000)
    event, parent = start_event(100_000, "0:cancel")
    ctx.bridge.cancel_source("0:cast", event, "post", parent)
    assert ctx.shared_pools.ledger.snapshot()["balances"]["pool"]["team"] == 13
    assert not any(ctx.bridge.keep_pending(q) for q in queued)
    assert ctx.state["buff"]["instances"] == []


def test_queue_payload_clone_is_not_an_actual_issued_object():
    ctx = setup()
    event, parent = start_event()
    queued = ctx.bridge.observe(event, "post", parent, successful=True)
    tick = next(q for q in queued if q["data"]["kind"] == "state_tick")
    ctx.expire(tick["time"])
    with pytest.raises(StateRuntimeError): ctx.bridge.handle(copy.deepcopy(tick))


def test_same_time_reverse_native_event_and_post_to_pre_are_refused_by_bridge():
    data = model()
    data["state_runtime"]["lifecycles"][0]["end_trigger"] = {"event": "action_start", "phase": "post"}
    ctx = setup(data)
    event, parent = start_event(order=30)
    ctx.bridge.observe(event, "post", parent, successful=True)
    event, parent = start_event(run="0:earlier", order=10)
    with pytest.raises(StateRuntimeError): ctx.bridge.observe(event, "post", parent, successful=True)
    event, parent = start_event(order=30)
    with pytest.raises(StateRuntimeError): ctx.bridge.cancel_source("0:cast", event, "pre", parent)
