"""Strict constant refresh candidates, not permission to dispatch game events.

The actual ledger must sign a crossing and the runtime must authenticate it.
Preparing a constant provider effect does not capture or read any panel/frame.
"""
from __future__ import annotations

import copy
import hashlib
import math
from decimal import Decimal

from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.resource_threshold_trigger_schema import (
    RESOURCE_THRESHOLD_TRIGGER_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA,
)
from genshin_engine.resource_thresholds import (
    ResourceThresholdError,
    ThresholdObserverProgram,
)
from genshin_engine.shared_pools import SharedPoolError, _canonical, _schema

DISPATCH_LIMITS = {"definition_bytes": 256*1024, "trace_bytes": 8*1024*1024, "state_bytes": 4*1024*1024,
                   "time_us": 10**15, "events": 40000}
CLASSIC_ZONES = {"base_add", "dmg_bonus", "crit_rate", "crit_dmg", "base_mult"}
NATIVE_CAUSES = {"action_start", "action_end", "hit", "switch", "condition"}


def _fail(code, message, **details):
    raise ResourceThresholdError(code, message, "resource_threshold_triggers", **details)


def _tick(value):
    if type(value) not in (int, float):
        _fail("threshold_dispatch_time", "收益时间必须是数值而非布尔")
    ticks = Decimal(str(value))*1000000
    if not ticks.is_finite() or ticks != ticks.to_integral_value() or not 0 <= ticks <= DISPATCH_LIMITS["time_us"]:
        _fail("threshold_dispatch_time", "阈值收益时间须为有界整数微秒")
    return int(ticks)


class ThresholdTriggerProgram:
    """Checked whole-target graph and immutable constant-only declarations."""

    def __init__(self, model, *, character_ids):
        try:
            if len(_canonical(model)) > DISPATCH_LIMITS["state_bytes"]:
                _fail("threshold_dispatch_limit", "阈值依赖图字节超限")
            config = model["resource_threshold_triggers"]
            if len(_canonical(config)) > DISPATCH_LIMITS["definition_bytes"]:
                _fail("threshold_dispatch_limit", "阈值联动定义字节超限")
            issues = []
            version = config.get("schema_version")
            _schema(config, {1: RESOURCE_THRESHOLD_TRIGGER_SCHEMA, 2: RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA,
                3: RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA, 4: RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA}.get(version, RESOURCE_THRESHOLD_TRIGGER_SCHEMA), "resource_threshold_triggers", issues)
            if issues:
                _fail("threshold_dispatch_schema", "阈值联动定义结构错误", issues=issues)
            counter = None
            if any(row.get("input", {}).get("kind") == "counter_value_key" for row in model["resource_threshold_observers"]["observers"]):
                if version not in (2, 3, 4):
                    _fail("threshold_dispatch_origin", "累计阈值触发需要显式v2合同")
                from genshin_engine.resource_counters import CounterProgram
                counter = CounterProgram(model["resource_counters"], model["shared_pools"], model["state_runtime"], character_ids=character_ids)
            observer = ThresholdObserverProgram(model["resource_threshold_observers"], model["shared_pools"], character_ids=character_ids, counter_program=counter)
            self._observer_config = copy.deepcopy(observer._config)
            self._pool_config = copy.deepcopy(observer._pool_config)
            self._characters = set(character_ids)
            self._config = copy.deepcopy(config)
            self._observers = {o["id"]: o for o in self._observer_config["observers"]}
            self._rules = {r["id"]: copy.deepcopy(r) for r in model["rules"]}
            operations = {o["id"]: o for o in model["shared_pools"]["operations"]}
            state = model.get("state_runtime", {})
            self._state_config = copy.deepcopy(state)
            self._counter_fingerprint = counter._fingerprint if counter is not None else None
            self._operation_origins = {}
            self._lifecycle_targets = {}
            hooks = {(p["kind"], p["ref_id"]) for life in state.get("lifecycles", [])
                     for group in life["hooks"].values() for p in group}
            state_owned = {(p["kind"], p["ref_id"]) for life in state.get("lifecycles", [])
                           for hooks in life["hooks"].values() for p in hooks}
            state_owned.update((t["payload"]["kind"], t["payload"]["ref_id"]) for t in state.get("timers", []))
            ids, orders, targets = set(), set(), set()
            self._bindings = sorted(copy.deepcopy(config["bindings"]), key=lambda b: b["order"])
            for binding in self._bindings:
                target_id = binding["payload"]["ref_id"]
                target_kind = binding["payload"]["kind"]
                target_identity = target_kind, target_id
                if binding["id"] in ids or binding["order"] in orders or target_identity in targets:
                    _fail("threshold_dispatch_duplicate", "联动ID、全局order或目标所有权重复")
                ids.add(binding["id"])
                orders.add(binding["order"])
                targets.add(target_identity)
                observation = self._observers.get(binding["observer_id"])
                life_target = target_kind in ("start_lifecycle", "end_lifecycle")
                target = (next((r for r in state.get("lifecycles", []) if r["id"] == target_id), None)
                          if life_target else self._rules.get(target_id))
                endpoint = "start" if target_kind == "start_lifecycle" else "end_trigger"
                if (observation is None or target is None or binding["owner"] != observation["owner"]
                        or target["owner"] != binding["owner"] or target["source_id"] != binding["source_id"]
                        or target.get(endpoint if life_target else "trigger") != {"kind": "resource_threshold", "binding_id": binding["id"]}):
                    _fail("threshold_dispatch_binding", "观察、来源、提供者和目标须双向一致")
                if binding["status"] == "pending" or target["status"] == "pending":
                    _fail("threshold_dispatch_pending", "阈值或目标来源仍待确认", severity="pending")
                if (observation["causes"]["consumer_ids"] or not observation["causes"]["operation_ids"]
                        or not life_target and ("dispatch_rule", target_id) in state_owned):
                    _fail("threshold_dispatch_origin", "阈值须由明确资源操作导致；不接受消费者来源或被生命周期重复拥有的派发目标")
                for oid in observation["causes"]["operation_ids"]:
                    routed = [t for t in state.get("timers", []) if t["payload"] == {"kind": "resource_operation", "ref_id": oid}]
                    if ("resource_operation", oid) in hooks:
                        _fail("threshold_dispatch_origin", "生命周期钩子不属于已支持的普通timer来源")
                    if routed:
                        if version not in (2, 3, 4) or len(routed) != 1 or routed[0]["end_tick"] != "exclude":
                            _fail("threshold_dispatch_origin", "timer触发须显式v2/v3/v4、唯一普通timer且无到期末跳许可")
                        timer = routed[0]
                        self._operation_origins[oid] = {"kind": "ordinary_timer", "timer_id": timer["id"],
                            "lifecycle_id": timer["lifecycle_id"]}
                    elif operations[oid]["trigger"]["event"] in NATIVE_CAUSES:
                        self._operation_origins[oid] = {"kind": "native"}
                    elif (version == 4 and operations[oid]['trigger']['event'] == 'elemental_reaction'
                          and oid in model.get('resource_reaction_contract', {}).get('operation_ids', [])):
                        self._operation_origins[oid] = {'kind': 'actual_reaction'}
                    else:
                        _fail("threshold_dispatch_origin", "资源操作须来自明确原生事件、唯一普通timer，或v4已登记的实际反应")
                if life_target:
                    if (version not in (3, 4) or state.get("schema_version") != 2
                            or any(target["hooks"].values()) or target["pending_cancel"] != "never_after_success"
                            or target["start_order" if target_kind == "start_lifecycle" else "end_order"] != binding["order"]):
                        _fail("threshold_lifecycle_scope", "生命周期目标需要v3/v4阈值合同及clock2、无hook且显式顺序对齐")
                    timers = [t for t in state["timers"] if t["lifecycle_id"] == target_id]
                    for timer in timers:
                        rule = self._rules.get(timer["payload"]["ref_id"])
                        if (timer["first_offset_us"] <= 0 or timer["end_tick"] != "exclude"
                                or timer["payload"]["kind"] != "dispatch_rule" or rule is None
                                or timer["status"] == "pending" or rule["status"] == "pending"):
                            _fail("threshold_lifecycle_scope", "目标timer须为正偏移非terminal且仅派发已确认收益")
                        if (rule.get("quota") or rule.get("resource_gate") or rule.get("resources")
                                or rule["timing"]["delay"] != 0 or rule["timing"]["duration"] <= 0
                                or rule["beneficiaries"]["mode"] == "next_switch"
                                or type(rule["stacking"]["max"]) is not int or type(rule["stacking"]["add"]) is not int
                                or rule["stacking"] != {"max": 1, "add": 1, "policy": "refresh"} or not rule["effects"]):
                            _fail("threshold_lifecycle_scope", "周期收益只能零延迟单层常量refresh，不写资源")
                        for effect in rule["effects"]:
                            if (effect["domain"] != "provider" or type(effect["value"]) not in (int, float)
                                    or not math.isfinite(effect["value"])
                                    or BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") not in CLASSIC_ZONES
                                    or effect.get("filter", {}).get("event") != "hit"
                                    or effect.get("filter", {}).get("phase") != "pre"):
                                _fail("threshold_lifecycle_scope", "周期目标仅限显式hit/pre经典直伤常量provider")
                        for field in ("delay", "duration", "cooldown"): _tick(rule["timing"][field])
                        if any(s["instances"] for s in model["initial_state"]["rules"] if s["rule_id"] == rule["id"]):
                            _fail("threshold_dispatch_initial", "周期阈值收益不能预放实例")
                    self._lifecycle_targets[binding["id"]] = copy.deepcopy(target)
                    continue
                if (target.get("quota") is not None or target.get("resource_gate") is not None or target.get("resources")
                        or target["timing"]["delay"] != 0 or target["timing"]["duration"] <= 0
                        or target["beneficiaries"]["mode"] == "next_switch"
                        or type(target["stacking"]["max"]) is not int or type(target["stacking"]["add"]) is not int
                        or target["stacking"] != {"policy": "refresh", "max": 1, "add": 1} or not target["effects"]):
                    _fail("threshold_dispatch_scope", "目标须为零延迟单层refresh，不附带消费/资源/延迟切人")
                for effect in target["effects"]:
                    value = effect["value"]
                    if (effect["domain"] != "provider" or type(value) not in (int, float) or not math.isfinite(value)
                            or BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") not in CLASSIC_ZONES
                            or effect.get("filter", {}).get("event", "hit") != "hit"
                            or effect.get("filter", {}).get("phase", "pre") != "pre"):
                        _fail("threshold_dispatch_scope", "整条目标仅接受经典直伤常量provider收益，不读取任何快照/属性表达式")
                for field in ("delay", "duration", "cooldown"):
                    _tick(target["timing"][field])
                if any(s["instances"] for s in model["initial_state"]["rules"] if s["rule_id"] == target_id):
                    _fail("threshold_dispatch_initial", "首片阈值目标不能预放活跃实例")
            for target in self._rules.values():
                if target["trigger"].get("kind") == "resource_threshold" and ("activate_rule", target["id"]) not in targets:
                    _fail("threshold_dispatch_binding", "typed规则未被唯一阈值联动拥有")
            for life in state.get("lifecycles", []):
                for field, kind in (("start", "start_lifecycle"), ("end_trigger", "end_lifecycle")):
                    if life.get(field, {}).get("kind") == "resource_threshold" and (kind, life["id"]) not in targets:
                        _fail("threshold_dispatch_binding", "typed生命周期端点未被唯一联动拥有")
            self._by_id = {b["id"]: b for b in self._bindings}
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, ArithmeticError) as exc:
            if isinstance(exc, SharedPoolError):
                raise
            _fail("threshold_dispatch_schema", "阈值联动图不是完整有界结构")

    @property
    def bindings(self):
        """Detached declaration display; changing it cannot retarget preparation."""
        return copy.deepcopy(self._bindings)

    @property
    def operation_origins(self):
        """Checked source graph for App dependency closure, never runtime authority.

        Maps selected operation IDs to ``{kind:native}`` or the explicit
        ordinary_timer kind with timer_id and lifecycle_id. Returned data is
        detached; only the actual state bridge authorizes a timer occurrence.
        """
        return copy.deepcopy(self._operation_origins)

    def authority_fingerprint(self):
        """Bind equivalent checked programs, never just their public IDs.

        This covers the complete effective target/qualification and resource
        graph. Rechecking before claim also detects mutation of an internal
        program after its runtime was constructed.
        """
        return hashlib.sha256(_canonical({"config": self._config, "bindings": self._bindings,
            "by_id": self._by_id, "observers": self._observer_config, "pools": self._pool_config,
            "rules": self._rules, "characters": sorted(self._characters), "state_runtime": self._state_config,
            "counter_fingerprint": self._counter_fingerprint, "operation_origins": self._operation_origins})).hexdigest()

    def _finish_candidate(self, result):
        if len(_canonical(result)) > DISPATCH_LIMITS["state_bytes"]:
            _fail("threshold_dispatch_limit", "目标候选字节超限", severity="pending")
        return result

    def prepare_constant_refresh(self, binding_id, state, *, beneficiaries, frame, qualified,
                                 attribute_layer_serial=None):
        """Detached state/CD/TTL/trace; qualified is not a source authorization.

        The runtime authenticates a signed crossing before calling this method,
        reserves the complete result, then publishes without other fallible work.
        No panel or attribute capture is created, even with an attribute ledger.
        """
        try:
            binding = self._by_id[binding_id]
            target = self._rules[binding["payload"]["ref_id"]]
            if type(qualified) is not bool or not isinstance(beneficiaries, list) or not beneficiaries:
                _fail("threshold_dispatch_context", "资格与受益者类型错误")
            if (len(beneficiaries) > 16 or len(set(beneficiaries)) != len(beneficiaries)
                    or not set(beneficiaries) <= self._characters):
                _fail("threshold_dispatch_context", "受益者重复或超限")
            if set(frame) != {"time_us", "origin", "dispatch_run_id", "source_run_id", "source_event_id", "phase"}:
                _fail("threshold_dispatch_context", "阈值当前执行帧结构错误")
            time = frame["time_us"]
            if (type(time) is not int or not 0 <= time <= DISPATCH_LIMITS["time_us"] or type(frame["origin"]) is not int
                    or frame["origin"] < 0 or frame["phase"] not in ("pre", "post")
                    or any(type(frame[k]) is not str or not frame[k] or len(frame[k]) > 512
                           for k in ("dispatch_run_id", "source_run_id", "source_event_id"))):
                _fail("threshold_dispatch_context", "阈值执行身份、时间或阶段无效")
            current = copy.deepcopy(state)
            if len(_canonical(current)) > DISPATCH_LIMITS["state_bytes"]:
                _fail("threshold_dispatch_limit", "目标状态字节超限", severity="pending")
            if type(current.get("cooldown")) is not int or not isinstance(current.get("instances"), list):
                _fail("threshold_dispatch_context", "目标状态不是有效冷却/实例结构")
            trace = {"type": "threshold_dispatch_completed", "binding_id": binding_id, "rule_id": target["id"],
                **copy.deepcopy(frame), "sampling_contract": "constant_no_snapshot"}
            if not qualified or time < current["cooldown"]:
                return self._finish_candidate({"state": current, "activation": None, "trace": {**trace, "outcome": "not_activated",
                    "reason": "qualification" if not qualified else "internal_cooldown"}, "attribute_layer_serial": attribute_layer_serial})
            end = time+_tick(target["timing"]["duration"])
            cooldown = time+_tick(target["timing"]["cooldown"])
            if max(end, cooldown) > DISPATCH_LIMITS["time_us"]:
                _fail("threshold_dispatch_limit", "目标期限超出微秒上限", severity="pending")
            old = next((s for s in current["instances"] if s["beneficiaries"] == beneficiaries), None)
            if old is None:
                ident = hashlib.sha256(_canonical([target["id"], frame["dispatch_run_id"]])).hexdigest()
                old = {"beneficiaries": list(beneficiaries), "expires": [end], "quota": {},
                       "instance_id": "threshold-instance:"+ident, "trigger_time": time,
                       "trigger_snapshot": None, "layer_snapshots": [None], "origin": frame["origin"],
                       "event_id": frame["source_event_id"]}
                if attribute_layer_serial is not None:
                    if type(attribute_layer_serial) is not int or not 0 <= attribute_layer_serial < DISPATCH_LIMITS["events"]:
                        _fail("threshold_dispatch_limit", "收益层身份预算超限", severity="pending")
                    old["attribute_layers"] = [f"layer:{attribute_layer_serial}"]
                    attribute_layer_serial += 1
                current["instances"].append(old)
            else:
                old.update(expires=[end], trigger_snapshot=None, layer_snapshots=[None], trigger_time=time,
                           origin=frame["origin"], event_id=frame["source_event_id"])
            current["cooldown"] = cooldown
            activation = {"type": "activation", "t": time/1000000, "rule_id": target["id"], "name_zh": target["name_zh"],
                "instance_id": old["instance_id"], "event_id": frame["source_event_id"], "cycle": frame["origin"],
                "beneficiaries": list(beneficiaries), "expires_at": [end/1000000], "layers": 1, "quota": {},
                "cooldown_until": cooldown/1000000, "sampling_contract": "constant_no_snapshot",
                "threshold_dispatch_run_id": frame["dispatch_run_id"], "source_run_id": frame["source_run_id"]}
            result = {"state": current, "activation": activation, "trace": {**trace, "outcome": "activated"},
                      "attribute_layer_serial": attribute_layer_serial}
            return self._finish_candidate(result)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError, ArithmeticError) as exc:
            if isinstance(exc, ResourceThresholdError):
                raise
            _fail("threshold_dispatch_context", "目标候选上下文不完整或无界")
