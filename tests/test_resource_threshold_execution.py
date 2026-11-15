"""R1 real-queue acceptance; starts red until typed threshold bridge is wired."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError
from tests.test_mechanics_execution import add, condition, fixture, rule
from tests.test_shared_pool_v2 import scaled


def case(phase="pre", *, initial=49, times=(.2, .3)):
    template, rotation, model = fixture(times)
    pools = scaled(initial=initial)
    pools["consumers"] = []
    operation = pools["operations"][0]
    operation.update(operation="grant", amount=1, trigger={"event": "hit", "phase": phase, "event_ids": ["h0"]})
    operation.pop("spend_policy")
    model["shared_pools"] = pools
    model["resource_threshold_observers"] = {"schema_version": 1, "observers": [{
        "id": "high", "name_zh": "工程资源过线", "owner": "a", "source_id": "source:threshold",
        "input": {"kind": "pool_balance_key", "pool_id": "pool", "key": "team"},
        "comparison": "enter_gte", "threshold_atoms": 50,
        "causes": {"operation_ids": ["refill"], "consumer_ids": []},
        "status": "assumed", "source": "工程假设，非游戏原生证据"}]}
    model["resource_threshold_triggers"] = {"schema_version": 1, "bindings": [{
        "id": "high-window", "name_zh": "阈值短时收益", "owner": "a", "source_id": "source:threshold-effect",
        "observer_id": "high", "order": 10, "payload": {"kind": "activate_rule", "ref_id": "boost"},
        "status": "assumed", "source": "工程假设，非游戏原生证据"}]}
    add(model, rule("boost", domain="provider", duration=1, source_id="source:threshold-effect",
        trigger={"kind": "resource_threshold", "binding_id": "high-window"}))
    return template, rotation, model


@pytest.mark.parametrize("phase,expected", [("pre", [90, 90]), ("post", [45, 90])])
def test_real_operation_threshold_affects_only_causally_later_damage(phase, expected):
    args = case(phase)
    original = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == expected
    assert not args[2]["condition_events"]
    assert args == original
    dispatch = [r for r in out["resource_threshold_triggers"]["trace"] if r["type"] == "threshold_dispatch_completed"]
    assert dispatch[0]["outcome"] == "activated"
    assert dispatch[0]["phase"] == phase and dispatch[0]["source_run_id"] == "0:h0"


def test_initial_high_is_not_a_crossing_and_does_not_activate():
    out = execute_mechanics(*case(initial=50))
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45]
    assert not out["resource_threshold_triggers"]["trace"]


def test_no_rule_trigger_from_an_agent_match_or_typed_id_name_alone():
    args = case(initial=50)
    matches = [{"rule_id": "boost", "effect_id": "value", "hit_id": "h0", "operation": "include"}]
    out = execute_mechanics(*args, matches)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45]


@pytest.mark.parametrize("invalid", ["self", "dynamic", "gate", "next_switch", "resource", "missing_reverse"])
def test_whole_target_rule_must_satisfy_new_explicit_contract(invalid):
    args = case()
    target = args[2]["rules"][0]
    if invalid == "self": target["effects"][0]["domain"] = "self"
    elif invalid == "dynamic":
        target["effects"][0]["value"] = {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .1, "sample": "current"}
    elif invalid == "gate": target["resource_gate"] = {"consumer_id": "consumer"}
    elif invalid == "next_switch": target["beneficiaries"]["mode"] = "next_switch"
    elif invalid == "resource":
        target["resources"] = [{"id": "energy", "kind": "energy_direct", "delay": 0, "source_part": "e", "amounts": {"a": 1}}]
    else: target["trigger"]["binding_id"] = "wrong"
    out = execute_mechanics(*args)
    assert out["status"] == "invalid" and out["result"] is None
    assert any(i["code"].startswith("threshold_") or (invalid == "gate" and i["code"] == "pool_gate_reference")
               for i in out["issues"]), out["issues"]


def test_unknown_qualification_preserves_actual_grant_without_total():
    args = case()
    args[2]["resource_threshold_triggers"]["bindings"][0]["qualification"] = {"condition": "inside"}
    condition(args[2], "inside", 0, 1)
    args[2]["condition_events"][0]["value"] = None
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert out["shared_pool_trace"][0]["after"] == 50
    assert out["resource_threshold_triggers"]["trace"][-1]["outcome"] == "pending"


def test_dispatch_reserve_failure_preserves_resource_prefix_not_buff(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_threshold_dispatch":
            raise SharedPoolError("threshold_dispatch_limit", "工程派发预算失败", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*case())
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert out["shared_pool_trace"][0]["after"] == 50
    assert not any(r["type"] == "activation" and r["rule_id"] == "boost" for r in out["mechanics_trace"])
    assert out["resource_threshold_triggers"]["trace"][-1]["type"] == "threshold_dispatch_failed"


def test_provider_window_is_half_open_and_ignores_old_self_capture():
    args = case(times=(.2, .3, .4))
    args[2]["rules"][0]["timing"]["duration"] = .2
    for descriptor in args[2]["hit_events"]:
        descriptor["self_snapshot_event"] = "cast"
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [90, 90, 45]


def test_swirl_stays_outside_constant_classic_threshold_scope():
    from tests.test_mechanics_swirl import setup
    args = case(times=(.2,))
    elemental = setup(times=(.2,))
    args[2]["elemental_model"] = elemental[2]["elemental_model"]
    args[2]["hit_events"][0]["application"] = elemental[2]["hit_events"][0]["application"]
    args[0]["hits"][0]["element"] = "anemo"
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([90, 781.299])
    assert any(r.get("reason") == "unsupported_threshold_damage_scope" for r in out["mechanics_trace"])
    assert len(out["resource_threshold_triggers"]["trace"]) == 1


def test_unknown_qualification_definition_is_pending_before_any_execution():
    args = case()
    args[2]["resource_threshold_triggers"]["bindings"][0]["qualification"] = {"condition": "missing"}
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert "resource_threshold_triggers" not in out


def test_false_qualification_does_not_activate_and_still_reports_actual_crossing():
    args = case()
    args[2]["resource_threshold_triggers"]["bindings"][0]["qualification"] = {"condition": "inside"}
    condition(args[2], "inside", 0, 1)
    args[2]["condition_events"][0]["value"] = False
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45]
    assert out["resource_threshold_observers"]["trace"][0]["crossed"] is True
    assert out["resource_threshold_triggers"]["trace"][0]["reason"] == "qualification"


def test_observer_only_keeps_audit_without_implicit_buff_or_dispatch_report():
    args = case()
    args[2].pop("resource_threshold_triggers")
    args[2]["rules"] = []
    args[2]["initial_state"]["rules"] = []
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45]
    assert out["resource_threshold_observers"]["trace"][0]["crossed"] is True
    assert "resource_threshold_triggers" not in out and "resource_threshold_triggers" not in out["result"]


def test_same_hit_multiple_operations_observe_each_committed_crossing_in_order():
    args = independent_crossings(case(times=(.2,)))
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    observations = [r for r in out["resource_threshold_observers"]["trace"] if r["run_id"] == "0:h0"]
    assert [(r["before"], r["after"], r["crossed"]) for r in observations] == [(49, 50, True), (50, 48, False), (48, 50, True)]
    dispatch = [r for r in out["resource_threshold_triggers"]["trace"] if r["origin"] == 0]
    assert len(dispatch) == 2 and dispatch[0]["dispatch_run_id"] != dispatch[1]["dispatch_run_id"]
    activations = [r for r in out["mechanics_trace"]
                   if r["type"] == "activation" and r["cycle"] == 0 and r["rule_id"] == "boost"]
    assert [r['rule_id'] for r in out['mechanics_trace'] if r['type'] == 'activation' and r['t'] == 0] == ['followup-grant-authority']
    assert len(activations) == 2 and activations[0]["instance_id"] == activations[1]["instance_id"]
    assert out["result"]["hits"][0]["expected"] == 90


def test_true_queue_second_target_failure_keeps_first_target_and_current_resource(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    args = case()
    target = copy.deepcopy(args[2]["rules"][0])
    target.update(id="second", source_id="source:second", trigger={"kind": "resource_threshold", "binding_id": "second-binding"})
    add(args[2], target)
    binding = copy.deepcopy(args[2]["resource_threshold_triggers"]["bindings"][0])
    binding.update(id="second-binding", source_id="source:second", order=11, payload={"kind": "activate_rule", "ref_id": "second"})
    args[2]["resource_threshold_triggers"]["bindings"].append(binding)
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_threshold_dispatch" and ctx.state["boost"]["instances"]:
            raise SharedPoolError("threshold_dispatch_limit", "工程第二目标限额", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None, out["issues"]
    assert out["shared_pool_trace"][0]["after"] == 50
    assert [r["rule_id"] for r in out["mechanics_trace"] if r["type"] == "activation"] == ["boost"]
    assert [r["type"] for r in out["resource_threshold_triggers"]["trace"]] == ["threshold_dispatch_completed", "threshold_dispatch_failed"]
    assert out["rotation_check"]["event_log"][-1]["id"] == "h0"


def test_typed_target_coexists_with_existing_current_attribute_conversion():
    from tests.test_attribute_graph_runtime import classify, conversion, enable_graph
    args = case()
    enable_graph(*args)
    add(args[2], conversion())
    classify(args[2], "share", "b")
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [90, 90]
    assert out["attribute_ledger"]["contract"] == "attribute-ledger-v2"


@pytest.mark.parametrize("triggered", [False, True])
def test_actual_c2_execution_does_not_implicitly_authorize_resource_thresholds(triggered):
    from tests.test_attribute_callback_runtime import callback_case
    args = callback_case()
    reference = case()[2]
    for field in ("shared_pools", "resource_threshold_observers"):
        args[2][field] = copy.deepcopy(reference[field])
    if triggered:
        args[2]["resource_threshold_triggers"] = copy.deepcopy(reference["resource_threshold_triggers"])
        add(args[2], copy.deepcopy(reference["rules"][0]))
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None, out["issues"]
    assert any(i["code"] == "causal_observer_scope_pending" for i in out["issues"])
    assert not out["mechanics_trace"]


def independent_crossings(args, *, second_id="up", independent_second=True):
    """One actual hit grants A, spends, then grants a distinct declared B.

    Resetting the balance or renaming an operation cannot replay A. The
    synthetic follow-up is independently owned; no game character is implied.
    """
    operations = args[2]["shared_pools"]["operations"]
    operations[0]["order"] = 1
    down = copy.deepcopy(operations[0])
    down.update(id="down", order=2, operation="spend", amount=2, spend_policy="up_to_available")
    up = copy.deepcopy(operations[0])
    up.update(id=second_id, order=3, amount=2)
    if independent_second:
        up.update(source_id="source:followup_grant",
                  source="Synthetic independent follow-up grant B; not another execution of grant A.")
        add(args[2], rule("followup-grant-authority", source_id="source:followup_grant", effects=[]))
    operations.extend([up, down])
    args[2]["resource_threshold_observers"]["observers"][0]["causes"]["operation_ids"].extend([second_id, "down"])
    return args


def test_grant_alias_after_spend_cannot_fabricate_a_second_threshold_crossing():
    out = execute_mechanics(*independent_crossings(case(times=(.2,)), independent_second=False))
    assert out['result'] is None and out['status'] == 'invalid'
    issue, = [r for r in out['issues'] if r['code'] == 'pool_duplicate_benefit']
    assert issue['details']['incoming_operation_id'] == 'up'
    assert issue['details']['existing_operation_ids'] == ['refill']
    assert [(r['operation_id'], r['after']) for r in out['shared_pool_trace']] == [('refill', 50), ('down', 48)]
