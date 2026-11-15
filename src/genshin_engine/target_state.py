"""Finite target predicates and pure, per-event observations for fixed effects.

No database lookup or general game-state interpreter belongs here.  A target's
elements and statuses are deliberately separate, even where the accepted
ordinary-freeze reference projects both from its positive frozen layer.
"""
from __future__ import annotations

import copy

from genshin_engine.elemental_schema import (
    FREEZE_RULESET,
    MULTI_AURA_RULESET,
    REFERENCE_CONTRACT,
    RULESET,
    SWIRL_RULESETS,
)
from genshin_engine.rotation_schema import arr, obj

TARGET_STATE_PROFILE = "freeze-reference-target-v1"
GENERAL_TARGET_PROFILE = "target-state-observation-v2"
MAX_PREDICATE_DEPTH = 4
MAX_PREDICATE_NODES = 32
MAX_PREDICATE_CHILDREN = 8
MAX_TARGET_CONDITION_CHECKS = 20000
MAX_TARGET_CONDITION_TRACE_BYTES = 8 * 1024 * 1024


def _predicate_schema(depth, *, general=False):
    elements = ["pyro", "hydro", "cryo", "electro", "dendro", "frozen"] if general else ["cryo", "hydro", "frozen"]
    choices = [
        obj({"kind": {"const": "target_aura_in"},
             "elements": {**arr({"enum": elements}),
                          "minItems": 1, "maxItems": len(elements), "uniqueItems": True}}, ["kind", "elements"]),
        obj({"kind": {"const": "target_status"}, "status_id": ({"type": "string", "minLength": 1}
             if general else {"const": "element_freeze"})},
            ["kind", "status_id"]),
    ]
    if depth < MAX_PREDICATE_DEPTH:
        choices.append(obj({"kind": {"enum": ["all", "any"]},
                            "children": {**arr(_predicate_schema(depth + 1, general=general)), "minItems": 1,
                                         "maxItems": MAX_PREDICATE_CHILDREN}}, ["kind", "children"]))
    return {"oneOf": choices}


TARGET_PREDICATE_SCHEMA = _predicate_schema(1)
GENERAL_TARGET_PREDICATE_SCHEMA = _predicate_schema(1, general=True)


def general_target_sample(runtime, *, event_id, run_id, cycle, time, order, status_runtime=None,
                          phase="before_application", target_id=None):
    """Observe the current committed elemental state, never infer game facts."""
    model = runtime.model if runtime else {}
    sample = {"profile_id": GENERAL_TARGET_PROFILE, "phase": phase,
              "event_id": event_id, "run_id": run_id, "cycle": cycle, "t": time / 1_000_000,
              "order": order, "target_id": target_id if target_id is not None else runtime.target if runtime else None,
              "ruleset": model.get("ruleset"), "status": "pending", "auras": None,
              "statuses": None, "state": None, "revision": None,
              "evidence_tier": "declared_elemental_execution"}
    if status_runtime is not None:
        sample['status_observations'] = status_runtime.observe(sample['target_id'], time)
        if runtime is None and sample['status_observations']:
            sample['evidence_tier'] = 'declared_status_execution'
    target = model.get("target", {})
    if (runtime is None or model.get("ruleset") not in {RULESET, FREEZE_RULESET, *SWIRL_RULESETS}
            or sample['target_id'] != runtime.target
            or target.get("kind") != "ordinary" or target.get("knowledge") != "known"
            or target.get("status") == "pending"):
        sample["reason"] = "unknown_target_state"
        return sample
    if model.get("ruleset") == FREEZE_RULESET:
        legacy = target_sample(runtime, event_id=event_id, run_id=run_id, cycle=cycle, time=time, order=order)
        if legacy["status"] != "known":
            sample["reason"] = legacy["reason"]
            return sample
        sample.update(auras=legacy["auras"], statuses=legacy["statuses"], state=legacy["state"],
                      revision=legacy["revision"])
    else:
        state = copy.deepcopy(runtime.state())
        if model.get("ruleset") == MULTI_AURA_RULESET:
            layers = state.get("auras", [])
        else:
            layers = [state["aura"]] if state.get("aura") else []
        auras = [a["element"] for a in layers if a.get("remaining_gu", 0) > 0]
        frozen = state.get("frozen")
        statuses = []
        if frozen and frozen.get("remaining_gu", 0) > 0:
            auras.append("frozen")
            statuses.append("element_freeze")
        sample.update(auras=auras, statuses=statuses, state=state)
    sample.update(status="known", reason="observed_committed_state")
    return sample


def observe_target_profiles(runtime, sources, event, *, time, order=0, hit=None, status_runtime=None):
    """Capture required profiles once before this event's own application."""
    kwargs = {"event_id": event["id"], "run_id": event.get("run_id", f"{event.get('origin', 0)}:{event['id']}"),
              "cycle": event.get("origin", 0), "time": time, "order": order}
    samples = dict(event.get("target_state_samples", {}))
    profiles = set()
    for source in sources:
        if hit is not None and source["target_state_profile"] == GENERAL_TARGET_PROFILE:
            from genshin_engine.mechanics_validation import _selector
            if (hit["character"] not in source.get("recipient_ids", [source["owner"]])
                    or hit.get("damage_system", "classic") not in source.get("damage_systems", ["classic"])
                    or not any(_selector(effect["filter"], event) for effect in source["effects"])):
                continue
        profiles.add(source["target_state_profile"])
    for profile in profiles:
        if profile not in samples:
            if profile == TARGET_STATE_PROFILE and "target_state_sample" in event:
                samples[profile] = event["target_state_sample"]
            else:
                samples[profile] = (general_target_sample(runtime, **kwargs, status_runtime=status_runtime,
                                                         target_id=event.get('target_id'))
                                    if profile == GENERAL_TARGET_PROFILE else target_sample(runtime, **kwargs))
    event["target_state_samples"] = samples


def predicate_bounds_valid(predicate):
    """Additional array/tree bounds for the engine's intentionally small validator."""
    stack, count = [(predicate, 1)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_PREDICATE_NODES or depth > MAX_PREDICATE_DEPTH:
            return False
        if node["kind"] in ("all", "any"):
            if not 1 <= len(node["children"]) <= MAX_PREDICATE_CHILDREN:
                return False
            stack.extend((child, depth + 1) for child in node["children"])
        elif node["kind"] == "target_aura_in":
            values = node["elements"]
            if not values or len(values) != len(set(values)):
                return False
    return True


def target_sample(runtime, *, event_id, run_id, cycle, time, order):
    """Observe after scheduled advance, without attempting an application or ICD.

    Returned data owns all its mutable children.  Callers keep this observation
    on the event instead of re-reading the live target after the application.
    """
    model = runtime.model if runtime else {}
    sample = {"profile_id": TARGET_STATE_PROFILE, "phase": "before_application",
              "event_id": event_id, "run_id": run_id, "cycle": cycle, "t": time / 1_000_000,
              "order": order, "target_id": runtime.target if runtime else None,
              "ruleset": model.get("ruleset"), "status": "pending", "auras": None,
              "statuses": None, "state": None, "revision": None,
              "reference_contract_fingerprint": None, "evidence_tier": "experimental_reference"}
    if not runtime or model.get("ruleset") != FREEZE_RULESET or runtime.freeze_state is None:
        sample["reason"] = "unsupported_target_state_profile"
        return sample
    target = model["target"]
    acceptance = model.get("reference_acceptance", {})
    if (target.get("kind") != "ordinary" or target.get("knowledge") != "known"
            or target.get("status") == "pending" or target.get("freeze", {}).get("freezable") is not True
            or target.get("freeze", {}).get("resistance") != 0
            or acceptance.get("acknowledged") is not True
            or acceptance.get("contract_fingerprint") != REFERENCE_CONTRACT["contract_fingerprint"]):
        sample["reason"] = "unknown_target_state"
        return sample
    state = copy.deepcopy(runtime.freeze_state.state())
    auras, statuses = [], []
    ordinary = state["ordinary_aura"]
    if ordinary and ordinary["remaining_gu"] > 0:
        auras.append(ordinary["element"])
    if state["frozen"] and state["frozen"]["remaining_gu"] > 0:
        auras.append("frozen")
        statuses.append("element_freeze")
    sample.update(status="known", auras=auras, statuses=statuses, state=state,
                  revision=runtime.freeze_state.revision,
                  reference_contract_fingerprint=acceptance["contract_fingerprint"], reason="observed_reference_state")
    return sample


def evaluate_target_predicate(predicate, sample):
    """Three-valued leaves; a known branch may determine a composite predicate."""
    if sample.get("profile_id") not in (TARGET_STATE_PROFILE, GENERAL_TARGET_PROFILE):
        return None
    kind = predicate["kind"]
    if kind == "target_status" and predicate['status_id'] != 'element_freeze':
        return sample.get('status_observations', {}).get(predicate['status_id'], {}).get('active')
    if kind in ("all", "any"):
        values = [evaluate_target_predicate(child, sample) for child in predicate["children"]]
        if kind == "all":
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    if sample.get("status") != "known":
        return None
    if kind == "target_aura_in":
        return bool(set(predicate["elements"]) & set(sample["auras"]))
    if kind == "target_status":
        return predicate["status_id"] in sample["statuses"]
    return None


class TargetStatePending(ValueError):
    def __init__(self, source_id):
        super().__init__("固定条件收益缺少受支持的命中前目标状态；未发布数值结果。")
        self.issue = {"code": "unknown_target_state", "message": str(self), "path": source_id,
                      "severity": "pending"}


class TargetConditionLimit(TargetStatePending):
    def __init__(self, limit, checks_completed):
        ValueError.__init__(self, "固定目标条件检查超过全程上限；仅保留有限流水前缀，未发布数值结果。")
        self.issue = {"code": "target_condition_limit", "message": str(self),
                      "path": "mechanics_model.fixed_effect_sources", "severity": "pending",
                      "limit": limit, "checks_completed": checks_completed}


class TargetConditionTraceLimit(TargetStatePending):
    def __init__(self, limit, bytes_used, attempted_bytes, checks_attempted):
        ValueError.__init__(self, "固定目标条件流水超过UTF-8字节上限；保留有限前缀，未截断原文或发布数值结果。")
        self.issue = {"code": "target_condition_trace_limit", "message": str(self),
                      "path": "mechanics_model.fixed_effect_sources", "severity": "pending",
                      "limit": limit, "bytes_used": bytes_used, "attempted_bytes": attempted_bytes,
                      "checks_attempted": checks_attempted}


def target_state_capabilities():
    return {"profile_id": TARGET_STATE_PROFILE, "sample_phase": "before_application",
            "general_profile": {"profile_id": GENERAL_TARGET_PROFILE,
                "predicate_schema": copy.deepcopy(GENERAL_TARGET_PREDICATE_SCHEMA),
                "damage_systems": ["classic", "transformative", "lunar", "stellar"],
                "supported_rulesets": [RULESET, *SWIRL_RULESETS, FREEZE_RULESET],
                "sampling": "每次真实伤害接触前；无附着伤害在自身执行前观察；不读取快照中的敌方状态。",
                "input": "fixed_effect_sources中显式general profile及declared_damage_systems范围，来源仍由Agent材料化。",
                "declared_status": "target_status_events显式施加/刷新/移除，或target_status_rules由实际反应触发。状态含义与时长来自Agent声明；事件按同一时间队列执行。",
                "status_without_elemental_model": "hit_events/hit_patterns可提供target_id观察显式状态，不必开启元素模型；元素底仍未知。有附着或元素模型时目标必须一致。"},
            "supported_rulesets": [FREEZE_RULESET], "truth_values": [True, False, None],
            "evidence_tier": "experimental_reference", "predicate_schema": copy.deepcopy(TARGET_PREDICATE_SCHEMA),
            "limits": {"max_sources": 64, "max_effects_per_source": 8, "max_depth": MAX_PREDICATE_DEPTH,
                       "max_nodes": MAX_PREDICATE_NODES, "max_children": MAX_PREDICATE_CHILDREN,
                       "max_target_condition_checks": MAX_TARGET_CONDITION_CHECKS,
                       "max_trace_bytes": MAX_TARGET_CONDITION_TRACE_BYTES},
            "check_budget": "所有检查窗口累计；结构拒绝、未命中和微调均计数，不按周期重置。超限pending，保留有限前缀，不发布伤害数字。",
            "trace_byte_budget": "target_condition_trace的UTF-8紧凑JSON，general v2另外按首次入库样本计费；共享sample引用不重复计费。旧freeze profile仍含完整行；非整个结果的内存上限。",
            "availability": "执行器初始化即存在；无到期、触发、次数或层数，不进入角色面板快照。",
            "sampling": "本事件自然推进和此前事件之后、本次接触/附着之前；每个事件实例独立纯读取。",
            "scope": "普通单目标、已接受冻结参考模型、本人经典命中；仅常数暴击率。",
            "matches": "fixed_source_id/effect_id/hit_id；include仅覆盖已知false，不绕过结构适用范围或未知目标状态。",
            "trace": "target_condition_trace；automatic_value与override、applied分别记录。general v2的完整状态在target_state_samples，由target_sample_ref定位；mechanics_trace只存条件账行号。"}
