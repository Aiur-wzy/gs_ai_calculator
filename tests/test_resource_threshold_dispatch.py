"""Constant-only candidate preparation; real queue/source permission is separate."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
from genshin_engine.shared_pools import SharedPoolError
from tests.test_mechanics_execution import add, fixture, rule
from tests.test_resource_thresholds import declaration
from tests.test_shared_pool_v2 import scaled


def model():
    _, _, config = fixture()
    pools = scaled(initial=49)
    pools["consumers"] = []
    pools["operations"][0].update(operation="grant", amount=1, trigger={"event": "hit", "phase": "pre"})
    pools["operations"][0].pop("spend_policy")
    config.update(shared_pools=pools, resource_threshold_observers=declaration(pools),
        resource_threshold_triggers={"schema_version": 1, "bindings": [{
            "id": "raise", "name_zh": "工程阈值收益", "owner": "a", "source_id": "source:effect",
            "observer_id": "cross", "order": 1, "payload": {"kind": "activate_rule", "ref_id": "boost"},
            "status": "assumed", "source": "工程假设"}]})
    add(config, rule("boost", domain="provider", source_id="source:effect", duration=.25,
        trigger={"kind": "resource_threshold", "binding_id": "raise"}))
    return config


def frame(time=100_000, uid="dispatch:1"):
    return {"time_us": time, "origin": 0, "dispatch_run_id": uid,
            "source_run_id": "0:hit", "source_event_id": "hit", "phase": "pre"}


def make(config=None):
    return ThresholdTriggerProgram(config or model(), character_ids=["a", "b"])


def empty():
    return {"cooldown": 0, "instances": [], "pending": []}


def prepare(prog, state=None, **kw):
    return prog.prepare_constant_refresh("raise", state if state is not None else empty(),
        beneficiaries=["a"], frame=kw.pop("frame", frame()), qualified=kw.pop("qualified", True), **kw)


def test_constant_candidate_has_no_panel_capture_and_does_not_mutate_input():
    prog, state = make(), empty()
    before = copy.deepcopy(state)
    out = prepare(prog, state, attribute_layer_serial=9)
    assert state == before
    assert out["attribute_layer_serial"] == 10
    inst = out["state"]["instances"][0]
    assert inst["trigger_snapshot"] is None and inst["layer_snapshots"] == [None]
    assert inst["attribute_layers"] == ["layer:9"]
    assert out["activation"]["sampling_contract"] == "constant_no_snapshot"
    assert out["trace"]["outcome"] == "activated"


def test_refresh_keeps_instance_layer_identity_and_has_new_occurrence_and_ttl():
    prog = make()
    first = prepare(prog, attribute_layer_serial=9)
    old = copy.deepcopy(first)
    second = prepare(prog, first["state"], frame=frame(200_000, "dispatch:2"), attribute_layer_serial=10)
    assert first == old
    assert second["attribute_layer_serial"] == 10
    assert first["activation"]["instance_id"] == second["activation"]["instance_id"]
    assert second["state"]["instances"][0]["expires"] == [450_000]
    assert second["activation"]["threshold_dispatch_run_id"] == "dispatch:2"


def test_false_and_cooldown_leave_complete_state_unchanged():
    config = model()
    config["rules"][0]["timing"]["cooldown"] = 1
    prog = make(config)
    false = prepare(prog, qualified=False, attribute_layer_serial=9)
    assert false["state"] == empty() and false["attribute_layer_serial"] == 9
    first = prepare(prog, attribute_layer_serial=9)
    second = prepare(prog, first["state"], frame=frame(200_000, "dispatch:2"), attribute_layer_serial=10)
    assert second["state"] == first["state"] and second["activation"] is None
    assert second["trace"]["reason"] == "internal_cooldown"


@pytest.mark.parametrize("kind", ["self", "stat", "quota", "delay", "stack", "foreign_owner", "extra_effect", "initial", "consumer"])
def test_full_graph_rejects_unsupported_target_or_source(kind):
    config = model()
    target = config["rules"][0]
    if kind == "self": target["effects"][0]["domain"] = "self"
    elif kind == "stat": target["effects"][0]["value"] = {"kind": "stat", "stat": "atk"}
    elif kind == "quota": target["quota"] = {"capacity": 1}
    elif kind == "delay": target["timing"]["delay"] = .1
    elif kind == "stack": target["stacking"]["max"] = 2
    elif kind == "foreign_owner": target["owner"] = "b"
    elif kind == "extra_effect":
        target["effects"].append({**copy.deepcopy(target["effects"][0]), "id": "bad", "domain": "self"})
    elif kind == "initial": config["initial_state"]["rules"][0]["instances"] = [{"remaining": [1], "beneficiaries": ["a"]}]
    else:
        config["resource_threshold_observers"]["observers"][0]["causes"]["consumer_ids"] = ["consumer"]
        config["shared_pools"]["consumers"] = scaled()["consumers"]
    with pytest.raises(SharedPoolError):
        make(config)


def test_failure_time_overflow_does_not_mutate_any_input():
    prog, state = make(), empty()
    before = copy.deepcopy(state)
    with pytest.raises(SharedPoolError):
        prepare(prog, state, frame=frame(10**15), attribute_layer_serial=12)
    assert state == before


def test_invalid_consumer_origin_diagnostic_does_not_deny_all_reaction_sources():
    config = model()
    config['resource_threshold_observers']['observers'][0]['causes']['consumer_ids'] = ['consumer']
    config['shared_pools']['consumers'] = scaled()['consumers']
    with pytest.raises(SharedPoolError) as error:
        make(config)
    assert error.value.issue['code'] == 'threshold_dispatch_origin'
    assert '消费者来源' in str(error.value)
    assert '不接消费者、反应' not in str(error.value)


def test_different_actual_dispatch_creates_distinct_new_instances_after_expiry():
    prog = make()
    first = prepare(prog)
    next_ = prepare(prog, frame=frame(500_000, "dispatch:2"))
    assert first["activation"]["instance_id"] != next_["activation"]["instance_id"]


def test_definition_is_deeply_isolated_from_later_agent_edits():
    config = model()
    prog = make(config)
    config["rules"][0]["timing"]["duration"] = 100
    assert prepare(prog)["activation"]["expires_at"] == [.35]
