"""Static mechanics event routes over one shared execution port.

The root observer selects time and opens the output batch before this dispatcher
runs. System operations, gameplay event preparation and the common contact
pipeline are distinct handlers; none owns another queue, clock or runtime state.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from genshin_engine.elemental import ElementalPending
from genshin_engine.health_mechanics import handle_health_event
from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.mechanics_validation import (
    _freeze_reference,
    _multi_aura_v4,
    _swirl_v2,
    _tick,
)
from genshin_engine.rotation_port import TICKS
from genshin_engine.rotation_port import rotation_issue as _issue
from genshin_engine.special_reaction_port import (
    SpecialReactionPending,
    capture_direct_damage_runtime,
    restore_direct_damage_runtime,
)
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import reserved_id
from genshin_engine.target_state import observe_target_profiles, target_sample


@dataclass(frozen=True, slots=True)
class PreparedRuntimeEvent:
    event: dict[str, Any]
    ability: Any = None
    terminal_events: list[dict] | None = None


def handle_state_runtime_event(self: MechanicsExecutionPort, ev, row, payload):
    if not self.state_runtime:
        raise StateRuntimeError("state_queue_authority", "内部事件没有生命周期桥")
    self.queue.extend(self.state_runtime.handle(ev))
    return self.queue


def handle_elemental_state_operation(self: MechanicsExecutionPort, ev, row, payload):
    if not self.elemental:
        raise ValueError("elemental_state_operation需要已启用的元素运行时。")
    if payload["kind"].startswith("bloom_core_"):
        cores = self.elemental.bloom_core_state
        if cores is None:
            raise ValueError("草原核操作需要元素运行时中的草原核账。")
        common = {"time": self.time, "target_id": payload["target_id"], "area_id": payload["area_id"],
                  "owner": payload["character"]}
        # Reserve the publication slot before mutating the core ledger.
        self.count_event()
        if payload["kind"] == "bloom_core_conversion_open":
            value = cores.open_conversion(**common, event_id=ev["run_id"],
                duration_ticks=_tick(payload["duration_seconds"]), lifetime_ticks=_tick(payload["replacement_lifetime_seconds"]),
                convert_existing=payload["convert_existing"])
        else:
            value = cores.absorb_replacements(**common, maximum=payload["maximum"])
        receipt = {"type": payload["kind"], "event_id": ev["run_id"], "t": self.time/TICKS, "cycle": ev["origin"],
                   "action_id": payload["action_id"], "source": payload["source"], **value}
        self.trace.append(copy.deepcopy(receipt))
        row.update(status="executed", bloom_core_operation=receipt)
        return self.queue
    if payload["kind"] != "crystallize_shard_pickup":
        raise ValueError("未知elemental_state_operation.kind。")
    receipt = self.elemental.pickup_crystallize_shard(
        event_id=ev["run_id"], shard_id=payload["shard_id"],
        character=payload["character"], target_id=payload["target_id"],
        time=self.time, source=payload["source"],
    )
    receipt["cycle"] = ev["origin"]
    # ElementalRuntime already owns the authoritative receipt; the
    # mechanics trace mirrors it so the effect axis can render the
    # same operation without inventing a second state transition.
    self.elemental.trace[-1]["cycle"] = ev["origin"]
    self.trace.append(copy.deepcopy(receipt))
    self.count_event()
    row["status"] = "executed"
    row["crystallize_shard_pickup"] = copy.deepcopy(receipt)
    return self.queue


def _dew_consumption(runtime, origin, event_id):
    run_id = f"{origin}:{event_id}"
    return next((entry for entry in reversed(runtime.trace)
                 if entry.get("event_id") == run_id and entry.get("operation") == "consume_dew"), None)


def handle_special_reaction_operation(self: MechanicsExecutionPort, ev, row, payload):
    runtime = self.special_runtime()
    if runtime is None:
        raise ValueError("special_reaction_operation需要已启用的星月反应运行时。")
    action = self.data["actions"][payload["action_id"]]
    ability_id = action["ability_id"]
    actor = self.data["abilities"][ability_id]["character"]
    transaction = capture_direct_damage_runtime(runtime)
    try:
        if payload["kind"] == "lunar_bloom_consume":
            receipt = runtime.consume_lunar_bloom(
                event_id=ev["run_id"], target_id=payload["target_id"],
                actor=actor, time=self.time, dew=payload["dew"],
                reserve_pool_ids=tuple(payload.get("reserve_pool_ids", ())),
                spend_policy=payload.get("spend_policy", "all_or_nothing"))
        elif payload["kind"] == "lunar_bloom_reserve_grant":
            receipt = runtime.grant_lunar_bloom_reserve(
                event_id=ev["run_id"], target_id=payload["target_id"], actor=actor, time=self.time,
                **{key: payload[key] for key in ("pool_id", "amount", "capacity", "duration_seconds", "source")})
        elif payload["kind"] == "direct_lunar_damage":
            dew_consumption = payload.get("dew_consumption")
            if dew_consumption is not None:
                debit = _dew_consumption(runtime, ev["origin"], dew_consumption["event_id"])
                if debit is None or debit.get("status") != "executed":
                    row.update(status="suppressed", reason="dew_consumption_unavailable")
                    return self.queue
                dew_consumption = {**dew_consumption, "run_id": debit["event_id"],
                                   "actual_dew": debit["amount"]}
            bloom_core_absorption = payload.get("bloom_core_absorption")
            if bloom_core_absorption is not None:
                absorption_run_id = f"{ev['origin']}:{bloom_core_absorption['event_id']}"
                absorption = next((entry for entry in reversed(self.trace)
                                   if entry.get("event_id") == absorption_run_id
                                   and entry.get("type") == "bloom_core_absorb"), None)
                if absorption is None:
                    row.update(status="suppressed", reason="bloom_core_absorption_unavailable")
                    return self.queue
                bloom_core_absorption = {**bloom_core_absorption, "run_id": absorption_run_id,
                                         "actual_cores": absorption["amount"], "source": absorption["source"]}
            receipt = runtime.request_direct_lunar_damage(
                event_id=ev["run_id"], target_id=payload["target_id"],
                area_id=payload["area_id"], actor=actor,
                action_id=payload["action_id"], time=self.time,
                ability_profile=payload["ability_profile"],
                ability_id=ability_id,
                ability_variant=self.data["abilities"][ability_id].get("variant"),
                ability_effective_level=self.data["abilities"][ability_id].get(
                    "effective_level"),
                dew_consumption=dew_consumption, bloom_core_absorption=bloom_core_absorption)
        elif payload["kind"] == "direct_stellar_damage":
            receipt = runtime.request_direct_stellar_damage(
                event_id=ev["run_id"], target_id=payload["target_id"],
                area_id=payload["area_id"], actor=actor,
                action_id=payload["action_id"], ability_id=ability_id,
                ability_variant=self.data["abilities"][ability_id].get("variant"),
                ability_effective_level=self.data["abilities"][ability_id].get(
                    "effective_level"),
                time=self.time, ability_profile=payload["ability_profile"])
        else:
            raise ValueError("未知special_reaction_operation.kind。")
    except SpecialReactionPending as exc:
        restore_direct_damage_runtime(runtime, transaction)
        raise ElementalPending(
            exc.issue["code"], str(exc), ev["id"],
            {"special_reaction_issue": copy.deepcopy(exc.issue)}) from exc
    except Exception:
        restore_direct_damage_runtime(runtime, transaction)
        raise
    try:
        self.drain_special_events(ev["origin"], ev, receipt["generated_events"])
    except Exception:
        restore_direct_damage_runtime(runtime, transaction)
        raise
    if payload["kind"] in {"lunar_bloom_consume", "lunar_bloom_reserve_grant"}:
        resource = receipt["resource_event"]
        row["status"] = ("executed" if resource["status"] == "executed"
                         else "rejected")
        if payload["kind"] == "lunar_bloom_reserve_grant" or payload.get("reserve_pool_ids") or payload.get("spend_policy"):
            row["resource_event"] = copy.deepcopy(resource)
    else:
        row["status"] = "executed"
        row["special_reaction_damage_request"] = copy.deepcopy(
            receipt["damage_event"])
    return self.queue


def handle_mechanics_snapshot_capture(self: MechanicsExecutionPort, ev, row, payload):
    self.capture_event(ev, row)
    return []


def handle_mechanics_activate(self: MechanicsExecutionPort, ev, row, payload):
    self.activate(self.rules[payload["rule_id"]], payload["event"], payload["beneficiaries"], payload["snapshot"], payload.get("selection"))
    return self.queue


def handle_target_status_operation(self: MechanicsExecutionPort, ev, row, payload):
    receipt = self.target_status_runtime.apply(payload, time=self.time, event_id=ev['run_id'], cycle=ev['origin'])
    row['target_status'] = copy.deepcopy(receipt)
    row['status'] = 'executed'
    self.trace.append(copy.deepcopy(receipt))
    return self.queue


def prepare_action(self: MechanicsExecutionPort, ev, row, payload, event):
    ability = self.data["abilities"][payload["ability_id"]]
    event.update(event="action_start", action_id=ev["id"], character=ability["character"], action_type_id=ability["action_type_id"])
    admission = row.get("action_admission")
    if admission and admission.get("status") == "committed" and admission.get("certificate"):
        # Pass an isolated reference to observers.  The queued action
        # remains unchanged and the receipt itself stays outside the
        # game event queue.
        event["action_admission_certificate"] = copy.deepcopy(admission["certificate"])
    if ability["action_type_id"] != "action.wait":
        for pending in self.pending_captures.values():
            if pending["actor"] == ability["character"]:
                pending.setdefault("interrupted_by", ev["run_id"])
    return PreparedRuntimeEvent(event=event, ability=ability)


def prepare_mechanics_end(self: MechanicsExecutionPort, ev, row, payload, event):
    ability = self.data["abilities"][payload["ability_id"]]
    event.update(event="action_end", action_id=payload["action_id"], character=ability["character"], action_type_id=ability["action_type_id"])
    return PreparedRuntimeEvent(event=event)


def prepare_mechanics_hit(self: MechanicsExecutionPort, ev, row, payload, event):
    event.update(payload["hit"])
    source_action = self.data["actions"][payload["action_id"]]
    event.update(event="hit", origin=ev["origin"], action_id=payload["action_id"],
                 action_type_id=self.data["abilities"][source_action["ability_id"]]["action_type_id"])
    target = payload['descriptor'].get('target_id') or payload['descriptor'].get('application', {}).get('target_id')
    if target is not None:
        event['target_id'] = target
    return PreparedRuntimeEvent(event=event)


def prepare_elemental_application(self: MechanicsExecutionPort, ev, row, payload, event):
    action = self.data["actions"][payload["action_id"]]
    ability = self.data["abilities"][action["ability_id"]]
    event.update(event="application", action_id=action["id"], character=ability["character"],
                 action_type_id=ability["action_type_id"])
    return PreparedRuntimeEvent(event=event)


def prepare_mechanics_condition(self: MechanicsExecutionPort, ev, row, payload, event):
    value = payload.get("value")
    if (selector := payload.get("dew_consumption")) is not None:
        debit = _dew_consumption(self.special_runtime(), ev["origin"], selector["event_id"])
        if debit is None:
            raise ElementalPending("lunar_bloom_condition_receipt_missing", "找不到扣露条件引用的实际消费记录。", ev["id"])
        amount = debit.get("amount", 0) if debit["status"] == "executed" else 0
        value = amount == selector["amount"]
        details = {"event_id": debit["event_id"], "actual_dew": amount, "required_dew": selector["amount"]}
        row["dew_consumption_condition"] = details
        self.count_event()
        self.trace.append({"type": "dew_consumption_condition", "event_id": ev["id"],
                           "t": self.time / TICKS, "cycle": ev["origin"], "value": value,
                           "consumption": copy.deepcopy(details)})
    self.conditions[payload["condition"]] = value
    event.update(event="condition", conditions=dict(self.conditions))
    if value is None:
        self.issues.append(_issue("unknown_condition", "外部条件未知，未按已触发处理。", ev["id"], severity="pending"))
    if value is not True:
        self.trace.append({"type": "condition", "event_id": event["id"], "t": self.time/TICKS,
                           "value": value, "triggered": False})
        if event["id"] in self.snapshot_ids:
            self.snapshots[(ev["origin"], event["id"])] = self.observed_snapshot(event)
        return PreparedRuntimeEvent(event=event, terminal_events=[])
    return PreparedRuntimeEvent(event=event)


def prepare_elemental_state_deadline(self: MechanicsExecutionPort, ev, row, payload, event):
    row["status"] = "executed"
    return PreparedRuntimeEvent(event=event, terminal_events=self.queue)


_SYSTEM_HANDLERS = MappingProxyType({
    "health_operation": handle_health_event,
    "target_status_operation": handle_target_status_operation,
    "state_runtime_event": handle_state_runtime_event,
    "elemental_state_operation": handle_elemental_state_operation,
    "special_reaction_operation": handle_special_reaction_operation,
    "mechanics_snapshot_capture": handle_mechanics_snapshot_capture,
    "mechanics_activate": handle_mechanics_activate,
})
_GAMEPLAY_PREPARERS = MappingProxyType({
    "action": prepare_action,
    "mechanics_end": prepare_mechanics_end,
    "mechanics_hit": prepare_mechanics_hit,
    "elemental_application": prepare_elemental_application,
    "mechanics_condition": prepare_mechanics_condition,
    "elemental_state_deadline": prepare_elemental_state_deadline,
})


def prepare_gameplay_event(self: MechanicsExecutionPort, ev, row, payload):
    event = {"id": ev["id"], "origin": ev["origin"], "conditions": dict(self.conditions)}
    if self.shared_pools or self.attribute_runtime or self.state_runtime:
        event["run_id"] = ev["run_id"]
    if self.counter_views and self.counter_views.frames:
        self.counter_views.frames.bind(event)
    prepare = _GAMEPLAY_PREPARERS.get(ev["kind"])
    if prepare is None:
        return PreparedRuntimeEvent(event=event, terminal_events=self.queue)
    return prepare(self, ev, row, payload, event)


def execute_reaction_post(self: MechanicsExecutionPort, ev, event, receipt, reaction,
                          reaction_event, reaction_parent, *, callback_token, commit_attribute):
    resources = self.resource_reactions
    scope = (resources.begin(ev, event, reaction_event, reaction_parent, receipt, reaction)
             if resources else None)
    try:
        self.phase(reaction_event, "post", reaction_parent, callback_authority=callback_token)
        if commit_attribute:
            self.attribute_callbacks.commit(callback_token)
        if scope is not None:
            resources.finish_callback(scope)
    finally:
        if scope is not None:
            resources.leave(scope)


def execute_gameplay_contact(self: MechanicsExecutionPort, ev, row, frontline, prepared):
    kind, payload = ev["kind"], ev["data"]
    event, ability = prepared.event, prepared.ability
    # A miss is neither a damage hit nor a successful hit trigger/ICD count.
    application = payload.get("descriptor", {}).get("application") if kind == "mechanics_hit" else payload.get("application")
    if kind == "mechanics_hit":
        from genshin_engine.runtime_event_match import prepare_execution_reference
        prepare_execution_reference(self, event, payload)
    if self.elemental and application and not application["landed"]:
        if kind == "mechanics_hit" and self.fixed_sources:
            self.fixed_effects(event, payload["hit"], payload["descriptor"].get("report", True), missed=True)
        self.apply_elemental(ev, event, application)
        self.drain_special_events(ev["origin"], ev)
        row["status"] = "missed"
        return self.queue
    if kind == "mechanics_hit" and self.fixed_sources:
        event["target_state_sample"] = target_sample(self.elemental, event_id=ev["id"], run_id=ev["run_id"],
                                                   cycle=ev["origin"], time=self.time, order=ev["order"])
        observe_target_profiles(self.elemental, self.fixed_sources.values(), event,
                                time=self.time, order=ev["order"], hit=payload["hit"],
                                status_runtime=self.target_status_runtime)
    if kind == "mechanics_hit" and self.special_runtime() is not None:
        area_id = (application or {}).get("area_id") or "battlefield.primary"
        event["stellar_field_before_contact"] = self.special_runtime().stellar_superconduct_snapshot(area_id)
    if _freeze_reference(self.model) and kind in ("mechanics_hit", "elemental_application"):
        contact = payload.get("descriptor", {}).get("contact") if kind == "mechanics_hit" else payload.get("contact")
        self.elemental.preview_copy().attempt(event_id=ev["run_id"], action_id=event["action_id"],
            actor=event["character"], time=self.time, application=application,
            damage_element=event.get("element") if kind == "mechanics_hit" else None,
            stable_event_id=ev["id"], contact=contact)
    self.phase(event, "pre", ev)
    reaction = None
    reactions = []
    reaction_snapshot = None
    if self.elemental and kind in ("mechanics_hit", "elemental_application"):
        receipt = self.apply_elemental(ev, event, application)
        if _freeze_reference(self.model) or _multi_aura_v4(self.model):
            reactions = receipt["reactions"]
            event.update(simulated_reactions=copy.deepcopy(reactions), reaction_package_id=receipt["package_id"],
                         target_state_before=receipt["before"], target_state_after=receipt["after"])
            if _multi_aura_v4(self.model) and receipt.get("reaction"):
                # A multi-route contact may emit more than one state
                # reaction, but only the first amplifying result belongs
                # to the parent direct-damage packet.
                event["simulated_reaction"] = copy.deepcopy(receipt["reaction"])
            self.check_reaction_package(reactions, event)
            for item in reactions:
                self.count_event("elemental_reaction")
            if any(item["type"] == "swirl" for item in reactions):
                if self.attribute_runtime:
                    self.attribute_runtime.stage(event, "reaction_created")
                reaction_snapshot = self.snapshot()
        else:
            reaction = receipt["reaction"]
        if reaction:
            event["simulated_reaction"] = reaction
            if _swirl_v2(self.model):
                self.count_event("elemental_reaction")
                if reaction["type"] == "swirl":
                    if self.attribute_runtime:
                        self.attribute_runtime.stage(event, "reaction_created")
                    reaction_snapshot = self.snapshot()
        self.target_status_runtime.reactions(reactions or ([reaction] if reaction else []),
            target_id=self.elemental.target, time=self.time, cycle=ev['origin'])
        from genshin_engine.runtime_reference_audit import audit_of
        if (reference_audit := audit_of(self)) is not None:
            reference_audit.reactions(ev, reactions or ([reaction] if reaction else []))
    if kind == "mechanics_hit":
        self.hit(event, payload)
    if event["id"] in self.snapshot_ids:
        self.snapshots[(ev["origin"], event["id"])] = self.observed_snapshot(event)
    callback_token = None
    packet_callback = False
    if self.attribute_callbacks and kind == "mechanics_hit":
        callback_source = reaction or (reactions[0] if reactions else None)
        callback_reaction = None
        if callback_source:
            # Freeze the same public reaction-event representation that the
            # post-phase bridge will later claim.  Keeping a second, partial
            # constructor here lets newly added reaction identity fields
            # (for example replacement-reaction trigger tags) invalidate an
            # otherwise legitimate callback token between prepare/claim.
            callback_reaction = self.reaction_event(event, callback_source)
            callback_reaction["run_id"] = callback_source["run_id"]
        if self.attribute_callbacks.multi_stat and reactions:
            packet_events = []
            for item in reactions:
                item_event = self.reaction_event(event, item)
                item_event["run_id"] = item["run_id"]
                packet_events.append(item_event)
            callback_token = self.attribute_callbacks.prepare_packet(ev, event, packet_events)
            packet_callback = True
        else:
            callback_token = self.attribute_callbacks.prepare(ev, event, callback_reaction)
    self.phase(event, "post", ev, callback_authority=callback_token)
    if callback_token is not None and not reaction and not reactions:
        self.attribute_callbacks.commit(callback_token)
    if reactions:
        for index, item in enumerate(reactions):
            reaction_event = self.reaction_event(event, item)
            self.trace.append({**copy.deepcopy(item), "type": "elemental_reaction", "reaction_type": item["type"],
                               "event_id": item["id"], "cycle": ev["origin"], "t": self.time/TICKS, "status": "executed"})
            execute_reaction_post(self, ev, event, receipt, item, reaction_event,
                                  {**ev, "id": item["id"], "run_id": item["run_id"]},
                                  callback_token=callback_token if packet_callback or index == 0 else None,
                                  commit_attribute=callback_token is not None and
                                  index == (len(reactions)-1 if packet_callback else 0))
        for item in reactions:
            if item["type"] == "swirl":
                self.project_swirl_damage(ev, event, item, reaction_snapshot, payload.get("descriptor", {}).get("report", True))
    if reaction:
        reaction_event = self.reaction_event(event, reaction)
        if not _swirl_v2(self.model):
            reaction_event["id"] = event["id"]+":reaction"
        reaction_parent = ev
        if _swirl_v2(self.model):
            self.trace.append({**copy.deepcopy(reaction), "type": "elemental_reaction", "reaction_type": reaction["type"],
                               "event_id": reaction["id"], "cycle": ev["origin"], "t": self.time/TICKS, "status": "executed"})
            reaction_parent = {**ev, "id": reaction["id"], "run_id": reaction["run_id"]}
        execute_reaction_post(self, ev, event, receipt, reaction, reaction_event, reaction_parent,
                              callback_token=callback_token, commit_attribute=callback_token is not None)
        if reaction["type"] == "swirl":
            self.project_swirl_damage(ev, event, reaction, reaction_snapshot, payload.get("descriptor", {}).get("report", True))
    if self.special_runtime() is not None and kind in ("mechanics_hit", "elemental_application"):
        self.drain_special_events(ev["origin"], ev)
    if kind == "action" and event["action_type_id"] == "action.switch":
        if row.get("cancelled_action"):
            cancelled = row["cancelled_action"]
            if self.state_runtime:
                self.queue.extend(self.state_runtime.cancel_source(f"{cancelled['origin']}:{cancelled['id']}", event, "post", ev))
            self.cancelled_actions.add((cancelled["origin"], cancelled["id"]))
            child = self.emit_event(ev, "mechanics_end", 0,
                                  {"action_id": cancelled["id"], "ability_id": cancelled["ability_id"], "cancelled_end": True}, "cancelled_end")
            child.update(id=cancelled["id"]+":end", origin=cancelled["origin"], run_id=f"{cancelled['origin']}:{cancelled['id']}:end")
        switch_event = {**event, "event": "switch", "character": row["character"]}
        for rid, state in self.state.items():
            rule = self.rules[rid]
            if state["pending"] and frontline != rule["owner"]:
                waiting = state["pending"].pop(0)
                if rule["timing"]["cooldown_anchor"] == "success":
                    state["cooldown"] = self.time+_tick(rule["timing"]["cooldown"])
                self.activate_or_delay(rule, switch_event, [frontline], waiting["snapshot"], ev)
        self.phase(switch_event, "pre", ev)
        self.phase(switch_event, "post", ev)
    if kind == "action" and ev["id"] in self.captures_by_action:
        capture = self.captures_by_action[ev["id"]]
        child = self.emit_event(ev, "mechanics_snapshot_capture", capture["offset"],
                              {"action_id": ev["id"], "source": capture["source"]}, "capture")
        child.update(id=capture["id"], run_id=f"{ev['origin']}:{capture['id']}")
        self.pending_captures[(ev["origin"], capture["id"])] = {
            "parent_run_id": ev["run_id"], "actor": ability["character"], "action_id": ev["id"]}
    return self.queue


def dispatch_mechanics_event(self: MechanicsExecutionPort, ev, row, frontline):
    kind, payload = ev["kind"], ev["data"]
    from genshin_engine.runtime_reference_audit import audit_of
    if (reference_audit := audit_of(self)) is not None:
        reference_audit.input_event(ev)
    # Internal state events are validated by their lifecycle bridge before the
    # ordinary reserved-ID guard, preserving the original authority ordering.
    if kind == "state_runtime_event":
        return _SYSTEM_HANDLERS[kind](self, ev, row, payload)
    if reserved_id(ev["id"]):
        raise StateRuntimeError("state_reserved_identity", "原生事件占用了生命周期内部身份")
    system = _SYSTEM_HANDLERS.get(kind)
    if system is not None:
        return system(self, ev, row, payload)
    if kind == "mechanics_end" and (ev["origin"], payload["action_id"]) in self.cancelled_actions and not payload.get("cancelled_end"):
        row["status"] = "cancelled"
        self.trace.append({"type": "action_end_superseded", "event_id": ev["id"], "cycle": ev["origin"], "t": self.time/TICKS})
        return []
    prepared = prepare_gameplay_event(self, ev, row, payload)
    if prepared.terminal_events is not None:
        return prepared.terminal_events
    return execute_gameplay_contact(self, ev, row, frontline, prepared)


__all__ = ["PreparedRuntimeEvent", "dispatch_mechanics_event"]

