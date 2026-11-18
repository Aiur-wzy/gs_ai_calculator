"""Finite declarative mechanics on the D16 action/energy queue.

No game database, probability, text interpreter, or rotation optimizer lives here.
The resolved projection contains numerical per-hit effects; trace retains the
expressions and sampled dependencies which produced those numbers.
"""
from __future__ import annotations

import copy
import math
from types import MappingProxyType

from genshin_engine import attribute_callback_runtime as callback_runtime
from genshin_engine import mechanics_resource_sampling as resource_sampling
from genshin_engine import resource_counter_view_runtime as counter_view_runtime
from genshin_engine import resource_reaction_runtime as reaction_resource_runtime
from genshin_engine import shared_pool_runtime as pool_runtime
from genshin_engine import snapshot_capture as capture_contract
from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_runtime import AttributeRuntime
from genshin_engine.beneficiary_selection import (
    possible_recipients,
    qualification_errors,
    select_recipients,
    selection_receipt,
)
from genshin_engine.constants import (
    REACTION_BASE_VALUE,
)
from genshin_engine.core import _verified_empty_result, calculate
from genshin_engine.damage_reporting import (
    DamageReportingLimit,
    candidate_sources_known,
    classify_verified_empty_damage,
    collect_damage_candidates,
)
from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
)
from genshin_engine.elemental_schema import (
    DYNAMIC_SWIRL_RULESET,
    MULTI_AURA_RULESET,
    REFERENCE_CONTRACT,
)
from genshin_engine.mechanics_elemental_bridge import (
    MechanicsElementalBridge,
    MechanicsElementalPorts,
)
from genshin_engine.mechanics_entry_reference_v1 import (
    build_reference_mechanics_entry_v1,
)
from genshin_engine.mechanics_event_execution import dispatch_mechanics_event
from genshin_engine.mechanics_execution_context import MechanicsExecutionRequest
from genshin_engine.mechanics_execution_pipeline import execute_mechanics_pipeline
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices
from genshin_engine.mechanics_hit_execution import (
    execute_hit,
    freeze_runtime_panel,
    ordinary_runtime_panel,
    project_fixed_effects,
    sample_effect_value,
)
from genshin_engine.mechanics_projection import (
    project_mechanics_inputs as _project,
)
from genshin_engine.mechanics_reaction_execution import (
    enqueue_special_application,
    project_direct_lunar_damage,
    project_direct_stellar_damage,
    project_ordinary_bloom_damage,
    project_special_damage,
    project_swirl_damage,
)
from genshin_engine.mechanics_reporting import (
    attach_attribute_report as _attach_attribute_report,
)
from genshin_engine.mechanics_reporting import (
    attach_buff_state_timeline as _attach_buff_state_timeline,
)
from genshin_engine.mechanics_reporting import (
    attach_shared_pool_report as _attach_shared_pool_report,
)
from genshin_engine.mechanics_reporting import (
    attach_special_reaction_report as _attach_special_reaction_report,
)
from genshin_engine.mechanics_reporting import (
    attach_state_runtime_report as _attach_state_runtime_report,
)
from genshin_engine.mechanics_resource_execution import activate_rule_and_resources
from genshin_engine.mechanics_rule_execution import (
    activate_rule_instance,
    commit_rule_callback,
    execute_rule_phase,
    expire_rule_instances,
    filter_rule,
    rule_filter_decision,
)
from genshin_engine.mechanics_scheduling import (
    ObserverEventEmitter,
    deadline_is_current,
    select_elemental_deadline,
)
from genshin_engine.mechanics_schema import MECHANICS_SCHEMA, mechanics_capabilities
from genshin_engine.mechanics_snapshot_execution import (
    capture_runtime_snapshot,
    observe_runtime_snapshot,
    reference_runtime_capture,
    reserve_snapshot_capacity,
    snapshot_runtime,
)
from genshin_engine.mechanics_special_damage import (
    _apply_special_damage_weights,
)
from genshin_engine.mechanics_validation import (
    _freeze_reference,
    _selector,
    _swirl_v2,
    _threshold_classic_rule_ids,
    _tick,
    validate_mechanics_model,
)
from genshin_engine.registry import BENEFIT_TYPES, DAMAGE_TYPES
from genshin_engine.rotation_port import (
    TICKS,
    validate_rotation_model,
)
from genshin_engine.rotation_port import (
    analyze_rotation as _semantic,
)
from genshin_engine.rotation_port import (
    event_sort_key as _event_sort_key,
)
from genshin_engine.rotation_port import (
    expand_rotation as _expand,
)
from genshin_engine.rotation_port import (
    rotation_issue as _issue,
)
from genshin_engine.rotation_port import (
    rotation_result as _base_result,
)
from genshin_engine.rotation_port import (
    run_rotation as _simulate,
)
from genshin_engine.rotation_schema import ACTION_TYPES
from genshin_engine.runtime_event_router import RuntimeEventRouter
from genshin_engine.shared_pools import SharedPoolError
from genshin_engine.snapshot_capture import SnapshotCapturePending
from genshin_engine.special_reaction_integration import (
    build_special_reaction_runtime,
    validate_special_reaction_bindings,
)
from genshin_engine.special_reaction_port import (
    SpecialReactionPending,
)
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import (
    StateRuntimeMechanics,
    reserved_id,
    route_event_id,
    validate_state_runtime_bindings,
)
from genshin_engine.target_state import (
    MAX_TARGET_CONDITION_CHECKS,
    MAX_TARGET_CONDITION_TRACE_BYTES,
    TargetStatePending,
    observe_target_profiles,
)
from genshin_engine.template import TemplateError, validate_template

MAX_MECHANICS_EVENTS = 40000




class _Mechanics:
    def __init__(self, template, rotation, model, data, matches):
        self.template, self.rotation, self.model, self.data = template, rotation, model, data
        self.rules = {r["id"]: r for r in model["rules"]}
        self.has_beneficiary_eligibility = any("eligibility" in r["beneficiaries"] for r in self.rules.values())
        self.threshold_classic_rule_ids = _threshold_classic_rule_ids(model)
        self.fixed_sources = {s["id"]: s for s in model.get("fixed_effect_sources", [])}
        self.target_condition_trace = []
        self.target_state_samples = {}
        from genshin_engine.target_status import TargetStatusRuntime
        self.target_status_runtime = TargetStatusRuntime(model)
        self.target_condition_checks = 0
        self.target_condition_trace_bytes = 2  # Compact JSON's enclosing [].
        self.state = {r["id"]: {"cooldown": 0, "instances": [], "pending": []} for r in model["rules"]}
        self.trace, self.samples, self.hit_rows, self.projected_buffs = [], [], [], []
        self.snapshots, self.conditions = {}, {}
        self.snapshot_ids = {d["self_snapshot_event"] for d in [*model["hit_events"], *model["hit_patterns"]] if "self_snapshot_event" in d}
        self.snapshot_ids.update(e["value"]["event_id"] for r in model["rules"] for e in r["effects"]
                                 if isinstance(e["value"], dict) and e["value"].get("sample") == "event")
        self.captures = {c["id"]: c for c in model.get("snapshot_captures", [])}
        self.captures_by_action = {c["action_id"]: c for c in self.captures.values()}
        self.pending_captures, self.capture_receipts = {}, {}
        self.capture_trace = []
        self.capture_bytes = 2
        self.capture_entries = 0
        self.frontline = rotation["initial_frontline"]
        self.time = 0
        self.matches = matches or []
        self.event_emitter = ObserverEventEmitter(
            reserve_event=self.count_event, to_ticks=_tick)
        self.event_budget_count = 0
        self.swirl_providers_verified = set()
        self.synchronous_counts = {"elemental_reaction": 0, "reaction_damage": 0,
                                   **({"special_reaction_event": 0}
                                      if model.get("special_reaction_model") else {}),
                                   **({"state_transition": 0} if _freeze_reference(model) else {})}
        self.issues = []
        self.windows = []
        self.cancelled_actions = set()
        special_runtime = build_special_reaction_runtime(model, list(data["chars"]))
        self.elemental = (ElementalRuntime(model["elemental_model"], special_runtime=special_runtime)
                          if "elemental_model" in model else None)
        self.special_damage_groups = {}
        from genshin_engine.special_damage_echo import SpecialDamageEchoRuntime
        self.special_damage_echoes = (SpecialDamageEchoRuntime(model['special_damage_echoes'])
                                     if model.get('special_damage_echoes') else None)
        self.special_damage_weighting = []
        self.ordinary_bloom_damage_windows = {}
        self.generated_event_router = RuntimeEventRouter({
            "special_reaction_damage_request": self._project_special_damage,
            "special_reaction_application_request": self._enqueue_special_application,
            "ordinary_bloom_damage_request": self._project_ordinary_bloom_damage,
            "ordinary_bloom_core_created": self._record_ordinary_bloom_core_created,
            "bloom_replacement_core_created": self._record_ordinary_bloom_core_created,
            "bloom_replacement_core_expired": self._record_ordinary_bloom_core_created,
        })
        self.elemental_bridge = MechanicsElementalBridge(MechanicsElementalPorts(
            get_elemental=self._current_elemental,
            get_special_runtime=self._special_runtime,
            get_time=self._current_time,
            append_trace=self._append_runtime_trace,
            count_event=self.count_event,
            dispatch_generated=self.generated_event_router.dispatch,
        ))
        self.shared_pools = (pool_runtime.SharedPoolRuntime(model, list(data["chars"]),
            reserve_events=lambda count: self._reserve_events(count, "resource_counters")) if "shared_pools" in model else None)
        self.resource_reactions = (reaction_resource_runtime.Runtime(self) if "resource_reaction_contract" in model else None)
        self.counter_views = (counter_view_runtime.CounterViewRuntime(model["resource_counter_views"], self.shared_pools, context=self)
                              if "resource_counter_views" in model else None)
        self.state_runtime = None
        self.attribute_runtime = None
        from genshin_engine.health_runtime import HealthRuntime
        self.health_runtime = HealthRuntime(model['health_model'], template) if 'health_model' in model else None
        self.attribute_scaling_receipts = {}
        self.attribute_layer_serial = 0
        for initial in model["initial_state"]["rules"]:
            state = self.state[initial["rule_id"]]
            state["cooldown"] = _tick(initial["cooldown_remaining"])
            for index, inst in enumerate(initial["instances"]):
                state["instances"].append({"beneficiaries": inst["beneficiaries"], "expires": [_tick(x) for x in inst["remaining"]],
                                           "instance_id": f"{initial['rule_id']}:initial:{index}",
                                           "quota": copy.deepcopy(inst.get("quota", {})), "trigger_time": 0,
                                           "trigger_snapshot": None, "layer_snapshots": [None]*len(inst["remaining"]),
                                           "origin": -1, "event_id": f"initial:{index}"})
                if "attribute_model" in model:
                    state["instances"][-1]["attribute_layers"] = self._new_attribute_layers(len(inst["remaining"]))
        if "attribute_model" in model:
            self.attribute_runtime = AttributeRuntime(self)
        self.attribute_callbacks = callback_runtime.AttributeCallbackRuntime(self) if "attribute_callbacks" in model else None

    @property
    def queue(self):
        return self.event_emitter.batch

    @property
    def serial(self):
        return self.event_emitter.serial

    @property
    def deadline_queued(self):
        return self.event_emitter.deadline_queued

    @property
    def special_trace(self):
        return self.elemental_bridge.trace

    @property
    def special_trace_seen(self):
        return self.elemental_bridge.seen

    @property
    def special_trace_cursor(self):
        return self.elemental_bridge.cursor

    def _current_elemental(self):
        return self.elemental

    def _current_time(self):
        return self.time

    def _append_runtime_trace(self, row):
        self.trace.append(row)

    @property
    def target_condition_check_limit(self):
        return MAX_TARGET_CONDITION_CHECKS

    @property
    def target_condition_trace_byte_limit(self):
        return MAX_TARGET_CONDITION_TRACE_BYTES

    def _new_attribute_layers(self, count):
        start = self.attribute_layer_serial
        self.attribute_layer_serial += count
        return [f"layer:{n}" for n in range(start, start+count)]

    def new_attribute_layers(self, count):
        return self._new_attribute_layers(count)

    def reserve_capture(self, value):
        return self._reserve_capture(value)

    def reserve_events(self, count, kind=None):
        return self._reserve_events(count, kind)

    def expire(self, time):
        self.time = time
        return expire_rule_instances(self, time)

    def snapshot(self):
        return snapshot_runtime(self)

    def _reserve_capture(self, value):
        return reserve_snapshot_capacity(self, value)

    def _capture(self, ev, row):
        return capture_runtime_snapshot(self, ev, row)

    def _observed_snapshot(self, event):
        return observe_runtime_snapshot(self, event)

    def _capture_reference(self, event, ref, usage):
        return reference_runtime_capture(self, event, ref, usage)

    def freeze_panel(self, snapshot):
        return freeze_runtime_panel(self, snapshot)

    def _ordinary(self, cid, snapshot, *, defer_em=False, defer_atk=False, defer_stats=()):
        return ordinary_runtime_panel(self, cid, snapshot, defer_em=defer_em, defer_atk=defer_atk, defer_stats=defer_stats)

    def manual_buffs(self, time):
        repeat = self.model.get("manual_buff_repeat", "each_cycle")
        for buff in self.template.get("buffs", []):
            # Authored endpoints retain the strict microsecond contract. Only
            # translate their integer ticks; adding floating-point seconds can
            # turn valid 0.2 + 0.1 into 0.30000000000000004 and break a later
            # boundary signature. Overlapping copies and half-open edges keep
            # exactly the same origin order and ownership.
            start, end = _tick(buff["start"]), _tick(buff["end"])
            for origin in range(time//self.data["period"]+1) if repeat == "each_cycle" else [0]:
                offset = origin*self.data["period"]
                if start+offset <= time < end+offset:
                    yield {**buff, "start": (start+offset)/TICKS, "end": (end+offset)/TICKS}

    def value(self, effect, rule, inst, cid, event, snapshot=None):
        return sample_effect_value(self, effect, rule, inst, cid, event, snapshot)

    def _enqueue(self, parent, kind, delay, payload, suffix):
        return self.event_emitter.emit(parent, kind, delay, payload, suffix)

    def _count_event(self, kind=None):
        self._reserve_events(1, kind)

    def _reserve_events(self, count, kind=None):
        if type(count) is not int or count < 0:
            raise ValueError("事件预算预留必须为非负整数。")
        if (not self.has_beneficiary_eligibility and not _swirl_v2(self.model)
                and not self.captures and not self.shared_pools and not self.attribute_runtime
                and not self.state_runtime and not self.health_runtime and self._special_runtime() is None):
            return
        if self.event_budget_count + count > MAX_MECHANICS_EVENTS:
            if self.health_runtime:
                raise AttributeLedgerError('health_event_limit', '生命与动作、规则共用事件预算超限', status='pending')
            if self.state_runtime:
                raise StateRuntimeError("state_event_limit", "生命周期与动作、规则及资源共用事件预算超限", severity="pending")
            if self.attribute_runtime:
                raise AttributeLedgerError("attribute_event_limit", "含属性账的动作与派生事件总量超过限制", status="pending")
            if self.shared_pools:
                raise SharedPoolError("pool_event_limit", "共享池与动作、捕获和反应共用的总事件数超过限制", severity="pending", limit=MAX_MECHANICS_EVENTS)
            if self.captures:
                raise SnapshotCapturePending("snapshot_capture_event_limit", "含捕获的机制总事件超过限制。",
                                             "mechanics_model.snapshot_captures", limit=MAX_MECHANICS_EVENTS)
            if self.has_beneficiary_eligibility:
                raise SnapshotCapturePending("beneficiary_selection_event_limit", "受益者选择与动作派生事件共用的预算超过限制。",
                                             "mechanics_model.rules.beneficiaries", limit=MAX_MECHANICS_EVENTS)
            raise ValueError("含同步反应与伤害的总事件数超过40000。")
        self.event_budget_count += count
        if kind in self.synchronous_counts:
            self.synchronous_counts[kind] += count

    def beneficiaries(self, rule, event):
        return select_recipients(rule, self.data["chars"], self.frontline)

    def activate(self, rule, event, beneficiaries, trigger_snapshot, selection=None):
        return activate_rule_instance(self, rule, event, beneficiaries, trigger_snapshot, selection, tick=_tick)

    def phase(self, event, phase, parent, *, allowed_rule_ids=None, allowed_operation_ids=None,
              observe_state=True, state_authority=None, callback_authority=None):
        return execute_rule_phase(self, event, phase, parent,
            allowed_rule_ids=allowed_rule_ids, allowed_operation_ids=allowed_operation_ids,
            observe_state=observe_state, state_authority=state_authority, callback_authority=callback_authority,
            commit_callback=_Mechanics._commit_callback, is_reserved=reserved_id)

    def _commit_callback(self, rule, event, beneficiaries, snapshot, parent):
        return commit_rule_callback(self, rule, event, beneficiaries, snapshot, parent,
                                    tick=_tick, selection_factory=selection_receipt,
                                    activate_or_delay=self._activate_or_delay)

    def _filter_decision(self, selector, event, rule_id, *, owner=None):
        return rule_filter_decision(self, selector, event, rule_id, owner=owner, selector_matches=_selector)

    def filter(self, selector, event, rule_id, *, owner=None, decision_sink=None):
        return filter_rule(self, selector, event, rule_id, owner=owner, decision_sink=decision_sink,
                           filter_decision=_Mechanics._filter_decision, issue_factory=_issue)

    def _activate_or_delay(self, rule, event, beneficiaries, snapshot, parent, selection=None):
        return activate_rule_and_resources(self, rule, event, beneficiaries, snapshot, parent, selection)

    def _match(self, rid, eid, hid):
        for match in self.matches:
            ref = match.get("effect_ref", {})
            if match.get("rule_id", ref.get("buff_id")) == rid and match.get("effect_id", ref.get("effect_id")) == eid and match.get("hit_id") == hid:
                return match.get("operation", match.get("mode"))
        return None

    def _fixed_effects(self, event, hit, reported, *, missed=False):
        return project_fixed_effects(self, event, hit, reported, missed=missed)

    def _special_runtime(self):
        return self.elemental.special_runtime if self.elemental else None

    @staticmethod
    def _special_area_id(hit):
        reaction = hit.get("reaction") or {}
        extensions = hit.get('extensions', {})
        extension = extensions.get("special_reaction", {})
        return (extension.get("area_id") or reaction.get("area_id")
                or extensions.get('elemental', {}).get('area_id') or "battlefield.primary")

    def _sample_stellar_superconduct(self, hit, event):
        runtime = self._special_runtime()
        reaction = hit.get("reaction") or {}
        if (runtime is None or hit.get("damage_system") != "stellar"
                or reaction.get("type") != "stellar_superconduct"):
            return
        area_id = self._special_area_id(hit)
        snapshot = runtime.stellar_superconduct_snapshot(area_id)
        declared = reaction.get("layers")
        reaction["layers"] = snapshot["layers"]
        hit["reaction"] = reaction
        hit.setdefault("extensions", {}).setdefault("special_reaction", {}).update(
            area_id=area_id, settled_snapshot=copy.deepcopy(snapshot),
            layer_source="runtime_area_snapshot")
        self.trace.append({
            "type": "special_reaction_stat_sample", "event_id": hit["id"],
            "cycle": event["origin"], "t": self.time/TICKS,
            "reaction_type": "stellar_superconduct", "area_id": area_id,
            "declared_layers": declared, "settled_layers": snapshot["layers"],
            "settled_buff_active": snapshot["settled_buff_active"],
            "status": "executed",
        })

    def _stellar_superconduct_effects(self, hit, event):
        runtime = self._special_runtime()
        if runtime is None or hit.get("damage_system") != "classic":
            return []
        element = hit.get("element")
        if element not in ("cryo", "electro", "physical"):
            return []
        area_id = self._special_area_id(hit)
        snapshot = event.get("stellar_field_before_contact") or runtime.stellar_superconduct_snapshot(area_id)
        if element in ("cryo", "electro"):
            benefit_type_id = "benefit.dmg_bonus."+element
            value = snapshot["ordinary_cryo_electro_bonus"]
            lifetime = "settlement" if snapshot["settled_buff_active"] else "field"
        else:
            benefit_type_id = "benefit.res_shred.physical"
            value = -snapshot["physical_res_delta"]
            lifetime = "field"
        if not value:
            return []
        info = BENEFIT_TYPES[benefit_type_id]
        effect_id = f"special:stellar-superconduct:{area_id}:{element}:{hit['id']}"
        effect = {"id": effect_id, "benefit_type_id": benefit_type_id,
                  "zone": info["zone"], "stat": info["stat"], "value": value,
                  "applies_to": {"mode": "hits", "hit_ids": [hit["id"]]}}
        self.trace.append({
            "type": "special_reaction_effect_applied", "event_id": hit["id"],
            "cycle": event["origin"], "t": self.time/TICKS,
            "reaction_type": "stellar_superconduct", "area_id": area_id,
            "effect_id": effect_id, "benefit_type_id": benefit_type_id,
            "value": value, "settled_layers": snapshot["layers"],
            "buff_expires_at": snapshot["buff_expires_at"] if lifetime == "settlement" else snapshot.get("field_expires_at"),
            "effect_lifetime": lifetime, "status": "executed",
        })
        return [effect]

    def hit(self, event, payload):
        from genshin_engine.runtime_event_match import register_runtime_matches
        register_runtime_matches(self, event, payload)
        if self.fixed_sources:
            observe_target_profiles(self.elemental, self.fixed_sources.values(), event,
                                    time=self.time, order=event.get("order", 0), hit=payload["hit"],
                                    status_runtime=self.target_status_runtime)
        return execute_hit(self, event, payload)

    def _record_special_event(self, row, cycle):
        return self.elemental_bridge.record_special_event(row, cycle)

    def _project_special_damage(self, request, cycle, parent):
        return project_special_damage(self, request, cycle, parent)

    def _project_direct_lunar_damage(self, request, cycle, parent):
        return project_direct_lunar_damage(self, request, cycle, parent)

    def _project_direct_stellar_damage(self, request, cycle, parent):
        return project_direct_stellar_damage(self, request, cycle, parent)

    def _enqueue_special_application(self, request, cycle, parent):
        return enqueue_special_application(self, request, cycle, parent)

    def _project_ordinary_bloom_damage(self, request, cycle, parent):
        return project_ordinary_bloom_damage(self, request, cycle, parent)

    def _sync_special_runtime_trace(self, cycle):
        return self.elemental_bridge.sync_trace(cycle)

    def _record_ordinary_bloom_core_created(self, row, cycle, _parent):
        clone = copy.deepcopy(row)
        clone["cycle"] = cycle
        self.trace.append(clone)
        self._count_event()

    def _drain_special_events(self, cycle=0, parent=None, events=None):
        return self.elemental_bridge.drain_generated(cycle, parent, events)

    def _advance_elemental(self, time, cycle=0, parent=None):
        return self.elemental_bridge.advance_to(time, cycle, parent)

    def keep_pending(self, ev):
        if self.state_runtime and not self.state_runtime.keep_pending(ev):
            return False
        return self._keep_pending_elemental(ev)

    def filter_pending(self, expanded, cursor):
        if self.state_runtime and getattr(self.state_runtime, '_resource_qualification', None) is not None:
            return self.state_runtime._filter_pending(expanded, cursor)
        return [item for item in expanded[cursor:] if self.keep_pending(item)]

    def _next_elemental_deadline(self):
        horizon = self.data["period"]*self.rotation.get("max_cycles", 3)
        return select_elemental_deadline(self.elemental, self._special_runtime, horizon)

    def _keep_pending_elemental(self, ev):
        if ev["kind"] != "elemental_state_deadline":
            return True
        deadline = self._next_elemental_deadline()
        return deadline_is_current(ev, deadline)

    def schedule_deadline(self, origin=0):
        if not self.elemental:
            return None
        deadline = self._next_elemental_deadline()
        return self.event_emitter.offer_deadline(deadline, target=self.elemental.target, origin=origin)

    def on_event(self, ev, row, frontline, energy):
        frames = self.counter_views.frames if self.counter_views else None
        if frames:
            frames.begin(ev)
        try:
            derived = self._on_event(ev, row, frontline, energy)
            deadline = self.schedule_deadline(ev["origin"])
            return [*derived, *([deadline] if deadline else [])]
        finally:
            if frames:
                frames.end(ev)

    def _on_event(self, ev, row, frontline, energy):
        self.health_event_context = ev
        self.event_emitter.begin_batch()
        if self.attribute_runtime:
            self.attribute_runtime.stage(ev, "capture" if ev["kind"] == "mechanics_snapshot_capture" else "pre")
        self.expire(ev["time"])
        if self.elemental:
            self._advance_elemental(ev["time"], ev["origin"], ev)
        self.frontline = frontline
        if self.health_runtime:
            from genshin_engine.health_mechanics import sync_health
            sync_health(self, ev)
        return dispatch_mechanics_event(self, ev, row, frontline)

    def _crystallize_trigger_sample(self, ev, event, application):
        if not self.elemental or not self.elemental.multi_state:
            return None
        profile = self.elemental.profiles.get(application.get("profile_id"))
        if not profile or profile.get("element") != "geo" or profile.get("application") != "apply":
            return None
        actor = event["character"]
        character = next((row for row in self.template["characters"]
                          if row["id"] == actor), None)
        if character is None:
            raise ElementalPending(
                "crystallize_trigger_character_missing",
                "普通结晶触发者不在角色面板中，无法取样护盾等级与精通。",
                ev["id"], {"character": actor})
        snapshot = self.snapshot()
        graph_receipt = (self.attribute_runtime.read(actor, "total", snapshot)
                         if self.attribute_runtime and self.attribute_runtime.graph
                         else None)
        stats, dependencies = self._ordinary(
            actor, snapshot, defer_em=bool(graph_receipt),
            defer_atk=bool(self.attribute_runtime and self.attribute_runtime.multistat))
        em = graph_receipt["value"] if graph_receipt else stats.get("em")
        level = character.get("level")
        if (type(level) is not int or not 1 <= level <= 100
                or isinstance(em, bool) or not isinstance(em, (int, float))
                or not math.isfinite(em) or em < 0):
            raise ElementalPending(
                "crystallize_shield_sample_invalid",
                "普通结晶护盾要求触发时角色等级为1至100且当前元素精通有限非负。",
                ev["id"], {"character": actor, "level": level,
                           "em": em if isinstance(em, (int, float))
                           and not isinstance(em, bool) and math.isfinite(em) else None})
        sample = {
            "contract": "ordinary-crystallize-trigger-stat-v1",
            "character": actor,
            "phase": "reaction_created",
            "time_tick": self.time,
            "t": self.time/TICKS,
            "level": level,
            "em": float(em),
            "source": "mechanics event-time character panel and attribute ledger",
            "dependencies": copy.deepcopy(dependencies),
        }
        if graph_receipt:
            sample["attribute_receipt_id"] = graph_receipt["receipt_id"]
            sample["dependencies"] = [
                {"attribute_node_id": row["id"],
                 "value": row["evaluated_value"],
                 "component": copy.deepcopy(row["component"]),
                 "instance_id": row["instance_id"],
                 "layer_id": row["layer_id"]}
                for row in graph_receipt["contributions"]
                if row["decision"] == "included"
            ]
        return sample

    def _application(self, ev, event, application):
        trigger_stat_sample = self._crystallize_trigger_sample(
            ev, event, application)
        receipt = self.elemental.attempt(event_id=ev["run_id"], action_id=event["action_id"],
            actor=event["character"], time=self.time, application=application,
            damage_element=event.get("element") if ev["kind"] == "mechanics_hit" else None,
            stable_event_id=ev["id"], contact=(ev["data"].get("descriptor", {}).get("contact")
                if ev["kind"] == "mechanics_hit" else ev["data"].get("contact")),
            trigger_stat_sample=trigger_stat_sample)
        receipt["cycle"] = ev["origin"]
        self.elemental.trace[-1]["cycle"] = ev["origin"]
        self.trace.append(copy.deepcopy(receipt))
        if self.resource_reactions:
            self.resource_reactions.observe_application(ev, event, receipt)
        return receipt

    @staticmethod
    def _reaction_event(event, reaction):
        tags = reaction.get("reaction_trigger_tags")
        if not isinstance(tags, list) or not tags:
            tags = [reaction["type"]]
        return {**event, "id": reaction["id"], "event": "elemental_reaction",
                "reaction_type": reaction["type"],
                "reaction_trigger_tags": copy.deepcopy(tags),
                "element": reaction["trigger_element"],
                "reaction_element": reaction.get(
                    "reaction_element", reaction.get("aura_element"))}

    def _check_reaction_package(self, reactions, parent):
        from genshin_engine.reaction_package_dependencies import (
            independent_attribute_write,
            package_attribute_reads,
        )
        swirls = [r for r in reactions if r["type"] == "swirl"]
        if len(swirls) < 2 and not any(len(r.get("layers", [])) > 1 for r in swirls):
            return
        paths = []
        candidates = set()
        candidate_recipients = {}
        for reaction in swirls:
            for candidate_event in (self._reaction_event(parent, reaction),
                    {**parent, "event": "reaction_damage", "reaction_type": "swirl",
                     "element": reaction["reaction_element"], "reaction_element": reaction["reaction_element"]}):
                for rule in self.rules.values():
                    selector = {k: v for k, v in rule["trigger"].items() if k != "condition"}
                    condition = rule["trigger"].get("condition")
                    if (condition and candidate_event.get("conditions", {}).get(condition) is False
                            or selector.get("owner_presence") == "frontline" and rule["owner"] != self.frontline):
                        continue
                    if _selector(selector, candidate_event):
                        candidates.add(rule["id"])
                        selected = self.beneficiaries(rule, candidate_event)
                        if rule['beneficiaries']['scope'] == 'current_frontline':
                            selected = [actor for actor in selected if actor == self.frontline]
                        candidate_recipients.setdefault(rule['id'], set()).update(selected)
        reads = package_attribute_reads(self, parent["character"], candidate_rule_ids=candidates,
                                        candidate_recipients=candidate_recipients)
        for reaction in swirls:
            events = [self._reaction_event(parent, reaction),
                      {**parent, "id": reaction["parent_event_id"]+":reaction_damage:swirl:"+reaction["reaction_element"],
                       "event": "reaction_damage", "reaction_type": "swirl", "element": reaction["reaction_element"],
                       "reaction_element": reaction["reaction_element"], "damage_type_ids": ["damage.reaction"]}]
            for event in events:
                for rule in self.rules.values():
                    condition = rule["trigger"].get("condition")
                    if condition and event.get("conditions", {}).get(condition) is False:
                        continue
                    selector = {k: v for k, v in rule["trigger"].items() if k != "condition"}
                    if not _selector(selector, event) or (selector.get("owner_presence") == "frontline" and rule["owner"] != self.frontline):
                        continue
                    timing, stack = rule["timing"], rule["stacking"]
                    safe = (stack["max"] == stack["add"] == 1 and stack["policy"] in ("refresh", "replace", "keep")
                            and timing["delay"] == timing["cooldown"] == 0 and timing["duration"] > 0
                            and not rule.get("quota") and not rule.get("resources")
                            and rule["beneficiaries"]["mode"] != "next_switch")
                    for effect in rule["effects"]:
                        info, value = BENEFIT_TYPES.get(effect["benefit_type_id"], {}), effect["value"]
                        zone, stat = info.get("zone"), info.get("stat")
                        # A damage-post may only change ordinary bonus, which no
                        # transformative member of this package reads.
                        allowed = zone == "dmg_bonus" or (event["event"] == "elemental_reaction"
                            and zone == "res_shred" and stat == reaction["reaction_element"])
                        selected = self.beneficiaries(rule, event)
                        if rule['beneficiaries']['scope'] == 'current_frontline':
                            selected = [actor for actor in selected if actor == self.frontline]
                        independent = independent_attribute_write(self, rule, effect, reads, recipients=selected)
                        allowed = allowed or independent
                        if isinstance(value, dict):
                            readable = (self.attribute_runtime is not None and self.attribute_runtime.supports_read(value.get("stat")))
                            allowed = allowed and value.get("kind") == "stat" and (readable or value.get("stat") == "em") and value["sample"] != "event"
                            if allowed and not readable:
                                owners = [rule["owner"]] if value["subject"] == "owner" else selected
                                for owner in owners:
                                    self._verify_swirl_provider(owner, event["id"])
                        safe = safe and allowed
                    if not safe:
                        paths.append("rules."+rule["id"])
        if paths:
            raise ElementalPending("freeze_package_effect_ambiguous", "多层/多元素反应包的监听不是已支持的幂等独立写入。", parent["id"],
                                   {"paths": sorted(set(paths)), "reaction_ids": [r["id"] for r in swirls],
                                    "attribute_reads": [dict(zip(("actor", "stat", "basis"), row, strict=True)) for row in sorted(reads)]})

    def _verify_swirl_provider(self, actor, event_id):
        if actor in self.swirl_providers_verified:
            return
        character = next(c for c in self.template["characters"] if c["id"] == actor)
        em = character["stats"].get("em")
        if isinstance(em, bool) or not isinstance(em, (int, float)) or not math.isfinite(em) or em < 0:
            raise ElementalPending("swirl_em_missing", "普通扩散v2需要提供者明确有限且非负的固定EM。", event_id, {"character": actor})
        if character["level"] not in REACTION_BASE_VALUE:
            raise ElementalPending("swirl_level_unsupported", "普通扩散v2缺少该等级的反应基数，不能插值或猜测。", event_id, {"character": actor, "level": character["level"]})
        dynamic = self.elemental.model["ruleset"] in (DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET)
        paths, unsupported = [], []
        for buff in self.template.get("buffs", []):
            if buff.get("target") in (None, actor):
                for effect in buff["effects"]:
                    if effect["zone"] == "em" or (effect["zone"] in ("stat_add", "stat_pct") and effect["stat"] == "em"):
                        path = f"buffs.{buff['id']}.{effect['id']}"
                        if not dynamic:
                            paths.append(path)
                        elif effect["zone"] != "em" or effect.get("applies_to", {}).get("mode", "all") != "all":
                            unsupported.append({"path": path, "reason": "nonordinary_or_filtered_manual_em"})
        for rule in self.rules.values():
            possible = possible_recipients(rule, self.data["chars"])
            if actor not in possible:
                continue
            for effect in rule["effects"]:
                info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
                if info.get("zone") == "em" or (info.get("zone") in ("stat_add", "stat_pct") and info.get("stat") == "em"):
                    path = f"rules.{rule['id']}.{effect['id']}"
                    if not dynamic:
                        paths.append(path)
                        continue
                    reasons = []
                    if isinstance(effect["value"], dict) and not (self.attribute_runtime and self.attribute_runtime.graph and effect["value"].get("kind") == "stat" and self.attribute_runtime.supports_read(effect["value"]["stat"])):
                        reasons.append("expression_write")
                    if "filter" in effect:
                        reasons.append("effect_filter")
                    if rule.get("quota"):
                        reasons.append("quota")
                    if rule.get("resource_gate"):
                        reasons.append("resource_gate")
                    if info.get("zone") != "em":
                        reasons.append("nonordinary_em")
                    for match in self.matches:
                        ref = match.get("effect_ref", {})
                        if (match.get("rule_id", ref.get("buff_id")) == rule["id"]
                                and match.get("effect_id", ref.get("effect_id")) == effect["id"]):
                            reasons.append("hit_match")
                            break
                    if reasons:
                        unsupported.append({"path": path, "reason": reasons})
        if unsupported:
            raise ElementalPending("swirl_em_sampling_unsupported", "动态扩散只支持普通精通；无筛选限次的EM转换还须显式启用属性图v2。", event_id,
                                   {"character": actor, "paths": sorted({p["path"] for p in unsupported}),
                                    "unsupported_writes": unsupported})
        if paths:
            raise ElementalPending("swirl_dynamic_em", "普通扩散v2首片仅接受固定EM，潜在EM写入路径需后续明确采样语义。", event_id,
                                   {"character": actor, "paths": paths})
        self.swirl_providers_verified.add(actor)

    def _swirl_damage(self, ev, parent_event, reaction, reaction_snapshot, reported):
        return project_swirl_damage(self, ev, parent_event, reaction, reaction_snapshot, reported)

    def begin_energy_credit(self, event):
        return resource_sampling.begin_energy_credit(self, event)

    def finish_energy_credit(self, event, *, committed):
        return resource_sampling.finish_energy_credit(self, event, committed=committed)

    def energy_recharge(self, cid, time, frontline, event=None):
        return resource_sampling.energy_recharge(self, cid, time, frontline, event)

    def boundary(self, time, next_cycle, pending=()):
        if self.special_damage_echoes is not None:
            self.special_damage_echoes.boundary(self, next_cycle)
        self.expire(time)
        if self.health_runtime:
            from genshin_engine.health_mechanics import sync_health
            boundary_event = {'id':'health:boundary', 'run_id':f'health:boundary:{next_cycle}', 'origin':next_cycle-1}
            self.health_event_context = boundary_event
            if self.attribute_runtime:
                self.attribute_runtime.stage(boundary_event, 'pre')
            sync_health(self, boundary_event)
        if self.elemental and self.elemental.freeze_state:
            self._advance_elemental(time, next_cycle-1)
        states = {}
        for rid, state in self.state.items():
            instances = []
            for inst in state["instances"]:
                # Snapshot values, not transient cast IDs, determine equivalence.
                frozen = None
                if any(isinstance(e["value"], dict) and e["value"]["sample"] == "trigger" for e in self.rules[rid]["effects"]):
                    frozen = [sample.get("stats") if sample else None for sample in inst["layer_snapshots"]]
                instances.append({"beneficiaries": inst["beneficiaries"], "expires_in": [e-time for e in inst["expires"]],
                                  "quota": dict(inst["quota"]), "frozen_stats": frozen})
                if self.health_runtime:
                    from genshin_engine.health_runtime import health_signature
                    instances[-1]['frozen_health'] = [health_signature(sample.get('health', {})) if sample else None
                                                      for sample in inst['layer_snapshots']]
            states[rid] = {"cooldown_in": max(0, state["cooldown"]-time), "instances": instances,
                           "awaiting_switch": [self.freeze_panel(p["snapshot"])["stats"] for p in state["pending"]]}
        # Retain only snapshots which can be needed by delayed hits from prior casts;
        # the queue itself is part of D16's boundary state.
        pending_samples = []
        for ev in pending:
            if ev["origin"] >= next_cycle:
                continue
            snapshot = None
            references = []
            if ev["kind"] == "mechanics_activate":
                snapshot = ev["data"]["snapshot"]
            elif ev["kind"] == "mechanics_hit":
                ref = ev["data"]["descriptor"].get("self_snapshot_event")
                if ref:
                    references.append(ref)
                references.extend(e["value"]["event_id"] for r in self.rules.values() for e in r["effects"]
                                  if isinstance(e["value"], dict) and e["value"]["sample"] == "event")
            elif ev["kind"] == "energy_credit":
                # A delayed particle can cross a cycle boundary while still
                # reading an ER source event from its true original cast.
                references.extend(resource_sampling.pending_energy_sample_refs(self))
            if snapshot:
                pending_samples.append({"event_id": ev["id"], "relative_cycle": ev["origin"]-next_cycle,
                                        "sampled_stats": self.freeze_panel(snapshot)["stats"],
                                        "sampled_effects": self._snapshot_signature(snapshot)})
            for ref in sorted(set(references)):
                snapshot = self.snapshots.get((ev["origin"], ref))
                if snapshot:
                    pending_samples.append({"event_id": ev["id"], "reference_id": ref, "relative_cycle": ev["origin"]-next_cycle,
                                            "sampled_stats": self.freeze_panel(snapshot)["stats"],
                                            "sampled_effects": self._snapshot_signature(snapshot)})
                    if (self.counter_views and self.counter_views.frames and ev["kind"] == "mechanics_hit"
                            and ev["data"]["descriptor"].get("self_snapshot_event") == ref):
                        frame_signature = self.counter_views.frames.boundary(snapshot, ev)
                        if frame_signature is not None:
                            pending_samples[-1]["counter_self_frame"] = frame_signature
        manual = [{"id": buff["id"], "remaining": _tick(buff["end"])-time}
                  for buff in self.manual_buffs(time)]
        bloom_windows = [
            {
                "owner": owner,
                "target_id": target_id,
                "reaction_type": reaction_type,
                "reset_in": max(0, state["anchor"] + TICKS//2 - time),
                "attempts": state["attempts"],
            }
            for (owner, target_id, reaction_type), state
            in sorted(self.ordinary_bloom_damage_windows.items())
            if time < state["anchor"] + TICKS//2
        ]
        return {**({"elemental": self.elemental.boundary(time, next_cycle)} if self.elemental else {}),
                **({'health': self.health_runtime.boundary()} if self.health_runtime else {}),
                **({"shared_pools": self.shared_pools.boundary(time)} if self.shared_pools else {}),
                **({"state_runtime": self.state_runtime.boundary(time)} if self.state_runtime else {}),
                **({"ordinary_bloom_damage_windows": bloom_windows} if bloom_windows else {}),
                "rules": states, "conditions": dict(self.conditions), "pending_samples": pending_samples,
                "manual_buffs": manual, "cancelled_ends": [(ev["id"], ev["origin"]-next_cycle) for ev in pending
                    if ev["kind"] == "mechanics_end" and (ev["origin"], ev["data"]["action_id"]) in self.cancelled_actions]}

    def _snapshot_signature(self, snapshot):
        result = {rid: [{"beneficiaries": inst["beneficiaries"], "layers": len(inst["expires"]),
                       "quota": inst["quota"], "trigger_stats": [sample.get("stats") if sample else None for sample in inst["layer_snapshots"]]}
                      for inst in state["instances"]] for rid, state in snapshot["state"].items()}
        if 'health' in snapshot:
            from genshin_engine.health_runtime import health_signature
            result['@health'] = health_signature(snapshot['health'])
        return result


    # Public execution services address this exact shared runtime. Dynamic
    # delegation also preserves scoped fault injection on the established
    # helper seam without a proxy object, copied store or second clock.
    def capture_reference(self, event, ref, usage):
        return self._capture_reference(event, ref, usage)

    def ordinary_panel(self, cid, snapshot, *, defer_em=False, defer_atk=False, defer_stats=()):
        return self._ordinary(cid, snapshot, defer_em=defer_em, defer_atk=defer_atk, defer_stats=defer_stats)

    def match_effect(self, rule_id, effect_id, hit_id):
        return self._match(rule_id, effect_id, hit_id)

    def fixed_effects(self, event, hit, reported, *, missed=False):
        return self._fixed_effects(event, hit, reported, missed=missed)

    def sample_stellar_superconduct(self, hit, event):
        return self._sample_stellar_superconduct(hit, event)

    def stellar_superconduct_effects(self, hit, event):
        return self._stellar_superconduct_effects(hit, event)

    def project_direct_lunar_damage(self, request, cycle, parent):
        return self._project_direct_lunar_damage(request, cycle, parent)

    def project_direct_stellar_damage(self, request, cycle, parent):
        return self._project_direct_stellar_damage(request, cycle, parent)

    def count_event(self, kind=None):
        return self._count_event(kind)

    def record_special_event(self, row, cycle):
        return self._record_special_event(row, cycle)

    def special_runtime(self):
        return self._special_runtime()

    def emit_event(self, parent, kind, delay, payload, suffix):
        return self._enqueue(parent, kind, delay, payload, suffix)

    def verify_swirl_provider(self, actor, event_id):
        return self._verify_swirl_provider(actor, event_id)

    def capture_event(self, event, row):
        return self._capture(event, row)

    def observed_snapshot(self, event):
        return self._observed_snapshot(event)

    def apply_elemental(self, event, projected_event, application):
        return self._application(event, projected_event, application)

    def check_reaction_package(self, reactions, event):
        return self._check_reaction_package(reactions, event)

    def reaction_event(self, event, reaction):
        return self._reaction_event(event, reaction)

    def project_swirl_damage(self, event, parent_event, reaction, reaction_snapshot, reported):
        return self._swirl_damage(event, parent_event, reaction, reaction_snapshot, reported)

    def drain_special_events(self, cycle=0, parent=None, events=None):
        return self._drain_special_events(cycle, parent, events)

    def activate_or_delay(self, rule, event, beneficiaries, snapshot, parent, selection=None):
        return self._activate_or_delay(rule, event, beneficiaries, snapshot, parent, selection)


_EXECUTION_SERVICES = MechanicsExecutionServices(MappingProxyType({
    "_base_result": lambda: _base_result,
    "validate_mechanics_model": lambda: validate_mechanics_model,
    "validate_rotation_model": lambda: validate_rotation_model,
    "_issue": lambda: _issue,
    "validate_template": lambda: validate_template,
    "_swirl_v2": lambda: _swirl_v2,
    "_tick": lambda: _tick,
    "_project": lambda: _project,
    "validate_special_reaction_bindings": lambda: validate_special_reaction_bindings,
    "pool_runtime": lambda: pool_runtime,
    "counter_view_runtime": lambda: counter_view_runtime,
    "qualification_errors": lambda: qualification_errors,
    "possible_recipients": lambda: possible_recipients,
    "_semantic": lambda: _semantic,
    "_expand": lambda: _expand,
    "_event_sort_key": lambda: _event_sort_key,
    "_freeze_reference": lambda: _freeze_reference,
    "DAMAGE_TYPES": lambda: DAMAGE_TYPES,
    "validate_state_runtime_bindings": lambda: validate_state_runtime_bindings,
    "ACTION_TYPES": lambda: ACTION_TYPES,
    "reserved_id": lambda: reserved_id,
    "route_event_id": lambda: route_event_id,
    "_threshold_classic_rule_ids": lambda: _threshold_classic_rule_ids,
    "_Mechanics": lambda: _Mechanics,
    "StateRuntimeMechanics": lambda: StateRuntimeMechanics,
    "_simulate": lambda: _simulate,
    "_attach_shared_pool_report": lambda: _attach_shared_pool_report,
    "MAX_MECHANICS_EVENTS": lambda: MAX_MECHANICS_EVENTS,
    "_attach_attribute_report": lambda: _attach_attribute_report,
    "_attach_state_runtime_report": lambda: _attach_state_runtime_report,
    "_attach_special_reaction_report": lambda: _attach_special_reaction_report,
    "_attach_buff_state_timeline": lambda: _attach_buff_state_timeline,
    "calculate": lambda: calculate,
    "_apply_special_damage_weights": lambda: _apply_special_damage_weights,
    "AttributeLedgerError": lambda: AttributeLedgerError,
    "candidate_sources_known": lambda: candidate_sources_known,
    "collect_damage_candidates": lambda: collect_damage_candidates,
    "classify_verified_empty_damage": lambda: classify_verified_empty_damage,
    "_verified_empty_result": lambda: _verified_empty_result,
    "REFERENCE_CONTRACT": lambda: REFERENCE_CONTRACT,
    "DamageReportingLimit": lambda: DamageReportingLimit,
    "SharedPoolError": lambda: SharedPoolError,
    "StateRuntimeError": lambda: StateRuntimeError,
    "ElementalPending": lambda: ElementalPending,
    "SpecialReactionPending": lambda: SpecialReactionPending,
    "TargetStatePending": lambda: TargetStatePending,
    "SnapshotCapturePending": lambda: SnapshotCapturePending,
    "TemplateError": lambda: TemplateError,
    "capture_contract": lambda: capture_contract,
}))


def execute_mechanics(template, rotation_model, mechanics_model, matches=None):
    """Run the single admission/execution pass chain and return its public receipt."""
    return execute_mechanics_pipeline(
        MechanicsExecutionRequest(template, rotation_model, mechanics_model, matches), _EXECUTION_SERVICES)


_REFERENCE_EXECUTE_MECHANICS_ENTRY_V1 = build_reference_mechanics_entry_v1(globals())


__all__ = ["MECHANICS_SCHEMA", "execute_mechanics", "mechanics_capabilities", "validate_mechanics_model"]
