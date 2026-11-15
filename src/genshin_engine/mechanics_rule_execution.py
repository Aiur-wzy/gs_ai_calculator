"""Rule lifecycle and trigger phase execution over one Mechanics root."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.rotation_port import TICKS
from genshin_engine.state_runtime import StateRuntimeError


def expire_rule_instances(runtime: MechanicsExecutionPort, time):
    for rid, state in runtime.state.items():
        for inst in state["instances"]:
            old = len(inst["expires"])
            expired = sorted(end for end in inst["expires"] if end <= time)
            if runtime.attribute_runtime:
                inst["attribute_layers"] = [layer for end, layer in zip(inst["expires"], inst["attribute_layers"], strict=True) if end > time]
            kept = [(end, sample) for end, sample in zip(inst["expires"], inst["layer_snapshots"], strict=True) if end > time]
            inst["expires"] = [pair[0] for pair in kept]
            inst["layer_snapshots"] = [pair[1] for pair in kept]
            before = old
            for end in sorted(set(expired)):
                after = before-expired.count(end)
                runtime.trace.append({"t": end/TICKS, "checked_at": time/TICKS, "rule_id": rid, "type": "expiry",
                                   "instance_id": inst["instance_id"], "layers_before": before,
                                   "layers_after": after, "beneficiaries": inst["beneficiaries"]})
                before = after
        state["instances"] = [inst for inst in state["instances"] if inst["expires"]]


def activate_rule_instance(runtime: MechanicsExecutionPort, rule, event, beneficiaries, trigger_snapshot, selection=None, *, tick):
    state, timing = runtime.state[rule["id"]], rule["timing"]
    old = next((i for i in state["instances"] if i["beneficiaries"] == beneficiaries), None)
    policy = rule["stacking"]["policy"]
    end = runtime.time + tick(timing["duration"])
    addition = rule["stacking"]["add"]
    frozen_panel = runtime.freeze_panel(trigger_snapshot)
    if old and policy == "keep":
        return
    if old:
        if runtime.attribute_runtime:
            if policy == "independent":
                layer_pairs = [*zip(old["expires"], old["attribute_layers"], strict=True),
                               *[(end, layer) for layer in runtime.new_attribute_layers(addition)]]
                old["attribute_layers"] = [pair[1] for pair in sorted(layer_pairs, key=lambda pair: pair[0])[-rule["stacking"]["max"]:]]
            elif policy == "add_and_refresh":
                old["attribute_layers"] += runtime.new_attribute_layers(min(addition, rule["stacking"]["max"]-len(old["expires"])))
            elif policy not in ("refresh", "extend"):
                old["attribute_layers"] = runtime.new_attribute_layers(addition)
        if policy == "refresh":
            old["expires"] = [end] * len(old["expires"])
            old["layer_snapshots"] = [frozen_panel]*len(old["expires"])
        elif policy == "add_and_refresh":
            layers = min(rule["stacking"]["max"], len(old["expires"]) + addition)
            old["expires"] = [end] * layers
            old["layer_snapshots"] = [frozen_panel] * layers
        elif policy == "extend":
            old["expires"] = [max(runtime.time, e) + tick(timing["duration"]) for e in old["expires"]]
        elif policy == "independent":
            # At cap, replace the oldest layer; expiry remains per layer.
            pairs = [*zip(old["expires"], old["layer_snapshots"], strict=True), *[(end, frozen_panel)]*addition]
            pairs = sorted(pairs, key=lambda pair: pair[0])[-rule["stacking"]["max"]:]
            old["expires"] = [pair[0] for pair in pairs]
            old["layer_snapshots"] = [pair[1] for pair in pairs]
        else:
            old["expires"] = [end]*addition
            old["layer_snapshots"] = [frozen_panel]*addition
    else:
        old = {"beneficiaries": list(beneficiaries), "expires": [end]*addition, "quota": {},
               "instance_id": f"{rule['id']}:{event['origin']}:{event['run_id'] if event.get('state_event') else event['id']}",
               "trigger_time": runtime.time, "trigger_snapshot": frozen_panel, "layer_snapshots": [frozen_panel]*addition,
               "origin": event["origin"], "event_id": event["id"]}
        state["instances"].append(old)
        if runtime.attribute_runtime:
            old["attribute_layers"] = runtime.new_attribute_layers(addition)
    old.update(trigger_snapshot=frozen_panel, trigger_time=trigger_snapshot["time"], origin=event["origin"], event_id=event["id"])
    quota = rule.get("quota")
    if quota:
        keys = beneficiaries if quota["scope"] == "per_beneficiary" else ["team"]
        for key in keys:
            before = old["quota"].get(key, 0)
            old["quota"][key] = (quota["capacity"] if quota["refresh"] == "reset" or key not in old["quota"] else
                                  min(quota["capacity"], before+quota["capacity"]) if quota["refresh"] == "add" else before)
    runtime.trace.append({"type": "activation", "t": runtime.time/TICKS, "rule_id": rule["id"], "name_zh": rule["name_zh"],
                       "instance_id": old["instance_id"],
                       "event_id": event["id"], "cycle": event["origin"], "beneficiaries": list(beneficiaries),
                       "expires_at": [e/TICKS for e in old["expires"]], "layers": len(old["expires"]),
                       "quota": dict(old["quota"]), "cooldown_until": state["cooldown"]/TICKS})
    if rule["trigger"].get("action_admission") is not None:
        runtime.trace[-1]["action_admission_certificate"] = copy.deepcopy(
            event["action_admission_certificate"])
    if selection is not None:
        runtime.trace[-1]["beneficiary_selection"] = selection
    if event.get("state_event"):
        runtime.trace[-1].update({key: copy.deepcopy(event.get(key)) for key in
            ("run_id", "state_event", "lifecycle_id", "timer_id", "ordinal", "generation_uid", "state_frame", "source_event_id", "source_run_id")})


def execute_rule_phase(runtime: MechanicsExecutionPort, event, phase, parent, *, allowed_rule_ids=None, allowed_operation_ids=None,
          observe_state=True, state_authority=None, callback_authority=None, commit_callback, is_reserved):
    scoped = (allowed_rule_ids is not None or allowed_operation_ids is not None or not observe_state
              or state_authority is not None or is_reserved(event["id"]))
    if scoped:
        if not runtime.state_runtime:
            raise StateRuntimeError("state_dispatch_authority", "内部生命周期路由没有执行桥授权")
        runtime.state_runtime.authorize_dispatch(event, phase, parent, allowed_rule_ids=allowed_rule_ids,
            allowed_operation_ids=allowed_operation_ids, authority=state_authority)
    deferred = set()
    if callback_authority is not None:
        if not runtime.attribute_callbacks:
            raise AttributeLedgerError("causal_authority", "没有因果执行桥签发该phase授权。")
        deferred = runtime.attribute_callbacks.claim(callback_authority, event, phase, parent)
    if runtime.attribute_runtime:
        runtime.attribute_runtime.stage(event, phase)
    # Collect candidates from one pre-state; each rule owns disjoint state.
    candidates = sorted((r for r in runtime.rules.values() if (allowed_rule_ids is None or r["id"] in allowed_rule_ids)
                         and r["id"] not in deferred
                         and r["trigger"].get("phase") == phase and runtime.filter(r["trigger"], event, r["id"])), key=lambda r: r["id"])
    if not candidates:
        if runtime.shared_pools:
            runtime.shared_pools.phase(runtime, event, phase, parent, allowed_operation_ids=allowed_operation_ids)
        if runtime.state_runtime and observe_state:
            runtime.queue.extend(runtime.state_runtime.observe(event, phase, parent, successful=True))
        return
    snapshot = runtime.snapshot()
    for rule in candidates:
        state = runtime.state[rule["id"]]
        if runtime.time < state["cooldown"]:
            runtime.trace.append({"type": "trigger_rejected", "reason": "internal_cooldown", "rule_id": rule["id"],
                               "event_id": event["id"], "t": runtime.time/TICKS, "cooldown_until": state["cooldown"]/TICKS})
            continue
        beneficiaries = runtime.beneficiaries(rule, event)
        commit_callback(runtime, rule, event, beneficiaries, snapshot, parent)
    if runtime.shared_pools:
        runtime.shared_pools.phase(runtime, event, phase, parent, allowed_operation_ids=allowed_operation_ids)
    if runtime.attribute_runtime:
        runtime.attribute_runtime.stage(event, phase)
    if runtime.state_runtime and observe_state:
        runtime.queue.extend(runtime.state_runtime.observe(event, phase, parent, successful=True))


def commit_rule_callback(runtime: MechanicsExecutionPort, rule, event, beneficiaries, snapshot, parent,
                         *, tick, selection_factory, activate_or_delay: Callable[..., Any]):
    state, timing = runtime.state[rule["id"]], rule["timing"]
    if timing["cooldown_anchor"] == "attempt":
        state["cooldown"] = runtime.time + tick(timing["cooldown"])
    selection = None
    if "eligibility" in rule["beneficiaries"]:
        # A selection is bounded by the roster, not a new full graph copy.
        runtime.reserve_events(1, "beneficiary_selection")
        selection = selection_factory(rule, runtime.data["chars"], runtime.frontline, event["id"], runtime.time/TICKS)
        runtime.reserve_capture(selection)
        if runtime.resource_reactions:
            from genshin_engine.resource_reaction_program import _bounded
            _bounded(selection, charge_work=runtime.resource_reactions.audit._charge)
        if selection["recipients"] != beneficiaries:
            raise ValueError("受益者名单与实际触发资格不一致。")
        if not beneficiaries:
            runtime.trace.append({"type": "trigger_rejected", "reason": "no_eligible_beneficiary",
                "rule_id": rule["id"], "event_id": event["id"], "t": runtime.time/TICKS,
                "cycle": event["origin"], "beneficiary_selection": selection})
            return
    if rule["beneficiaries"]["mode"] == "next_switch" and event["event"] != "switch":
        state["pending"] = [{"event": copy.deepcopy(event), "snapshot": snapshot}]
        return
    if timing["cooldown_anchor"] == "success":
        state["cooldown"] = runtime.time + tick(timing["cooldown"])
    # The phase facade owns the activation port. Adapters implementing that
    # existing phase contract must not acquire another whole-runtime method.
    activate_or_delay(rule, event, beneficiaries, snapshot, parent, selection)


def rule_filter_decision(runtime: MechanicsExecutionPort, selector, event, rule_id, *, owner=None, selector_matches):
    if selector.get("owner_presence") == "frontline" and (runtime.rules[rule_id]["owner"] if owner is None else owner) != runtime.frontline:
        return "no_match"
    condition = selector.get("condition")
    if condition and event.get("conditions", {}).get(condition) is None:
        unconditional = {k: v for k, v in selector.items() if k != "condition"}
        if selector_matches(unconditional, event):
            return "unknown_condition"
        return "no_match"
    return "match" if selector_matches(selector, event) else "no_match"


def filter_rule(runtime: MechanicsExecutionPort, selector, event, rule_id, *, owner=None, decision_sink=None, filter_decision, issue_factory):
    decision = filter_decision(runtime, selector, event, rule_id, owner=owner)
    if decision_sink is not None:
        decision_sink(decision)
    if decision == "unknown_condition":
        condition = selector["condition"]
        issue = issue_factory("unknown_condition", "本次事件需要的条件尚未知，不能认证为触发或不触发。", rule_id,
                       severity="pending", event_id=event["id"], cycle=event["origin"], condition=condition)
        if issue not in runtime.issues:
            runtime.issues.append(issue)
        runtime.trace.append({"type": "trigger_rejected", "reason": "unknown_condition", "rule_id": rule_id,
                           "event_id": event["id"], "t": runtime.time/TICKS, "condition": condition})
    return decision == "match"

