"""Reaction damage/application projection over the single Mechanics execution port.

The self argument is the original runtime implementing the typed port.
Subruntimes and callbacks therefore receive the same object and shared state;
this module neither copies that runtime nor creates another scheduler.
"""

from __future__ import annotations

import copy
import math

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.constants import LUNAR_FACTOR
from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET
from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.mechanics_special_damage import (
    _direct_lunar_damage_hit,
    _direct_stellar_damage_hit,
    _special_damage_hit,
)
from genshin_engine.rotation_port import TICKS
from genshin_engine.runtime_reference_audit import (
    prepare_projected_damage,
    register_generated,
)
from genshin_engine.special_reaction_port import (
    LUNAR_STELLAR_WEIGHTS,
    validate_direct_lunar_damage_profile,
    validate_direct_stellar_damage_profile,
)
from genshin_engine.special_reaction_port import (
    validate_stellar_swirl_role_provenance as _validate_stellar_swirl_role_provenance,
)


def project_special_damage(self: MechanicsExecutionPort, request, cycle, parent, *, _callbacks=True):
    if request.get("time_tick") != self.time:
        raise ElementalPending(
            "special_reaction_deadline_skipped",
            "星月伤害请求没有在自身事件时刻进入统一队列，拒绝用较晚属性帧代算。",
            request.get("event_id", "special_reaction_damage_request"),
            {"request_time_tick": request.get("time_tick"), "runtime_time_tick": self.time},
        )
    register_generated(self, request, cycle)
    if request.get("reaction_base") is False:
        if request.get("damage_system") == "lunar":
            return self.project_direct_lunar_damage(request, cycle, parent)
        if (request.get("damage_system") == "stellar"
                and request.get("reaction_type") == "stellar_superconduct"):
            return self.project_direct_stellar_damage(request, cycle, parent)
        raise ElementalPending(
            "direct_special_damage_type_unsupported",
            "能力直伤请求没有接入受审的星月投影器。",
            request.get("event_id", "special_reaction_damage_request"),
            {"damage_system": request.get("damage_system"),
             "reaction_type": request.get("reaction_type")},
        )
    if request.get("reaction_base") is not True:
        raise ElementalPending(
            "special_reaction_damage_mode_missing",
            "星月伤害请求必须显式声明是反应基础伤害还是能力直伤。",
            request.get("event_id", "special_reaction_damage_request"),
        )
    contributors = request.get("contributor_ids") or (
        [request["owner"]] if isinstance(request.get("owner"), str) and request["owner"] else [])
    if not contributors:
        raise ElementalPending(
            "special_reaction_contributors_missing",
            "星月伤害请求没有可在事件时刻取样的贡献者。",
            request.get("event_id", "special_reaction_damage_request"),
        )
    if len(contributors) > len(LUNAR_STELLAR_WEIGHTS) or len(set(contributors)) != len(contributors):
        raise ValueError("星月伤害贡献者必须是1至4个唯一角色。")
    absent = sorted(set(contributors)-set(self.data["chars"]))
    if absent:
        raise ValueError("星月伤害贡献者不在队伍中: "+", ".join(absent))
    if str(request.get("reaction_type", "")).startswith("stellar_swirl_"):
        _validate_stellar_swirl_role_provenance(
            request["reaction_type"], contributors,
            request.get("role_provenance"),
            event_id=request.get("event_id", "special_reaction_damage_request"),
        )
    group_id = f"{cycle}:{request['event_id']}"
    projected_hit_ids = []
    for index, contributor in enumerate(contributors):
        hit = _special_damage_hit(request, contributor, index, cycle=cycle)
        event = {
            "id": hit["id"], "origin": cycle, "run_id": f"{cycle}:{hit['id']}",
            "event": "reaction_damage", "character": contributor,
            "reaction_type": request["reaction_type"], "element": hit["element"],
            "reaction_element": hit["element"], "damage_type_ids": ["damage.reaction"],
            "conditions": dict(self.conditions),
        }
        prepare_projected_damage(self, event, {"hit": hit, "descriptor": {"report": True}}, request['event_id'])
        if not _callbacks and self.attribute_runtime:
            # Suppressing gameplay callbacks must not suppress the ordinary
            # attribute stage. A preceding contributor may have left a
            # root-only damage_projection frame; capture this real event's
            # pre-state before constructing its reaction snapshot instead.
            self.attribute_runtime.stage(event, 'pre')
        event['reaction_snapshot'] = self.snapshot()
        generated_parent = {
            "id": request["event_id"], "run_id": f"{cycle}:{request['event_id']}",
            "time": self.time, "order": parent.get("order", 0), "origin": cycle,
            "causal": tuple(parent.get("causal", ())),
        }
        self.count_event("reaction_damage")
        if _callbacks:
            self.phase(event, "pre", generated_parent)
        before = len(self.hit_rows)
        self.hit(event, {"hit": hit, "descriptor": {"report": True}})
        if _callbacks:
            self.phase(event, "post", generated_parent)
        elif self.attribute_runtime:
            # The next contributor/caller must not inherit a damage-only
            # projection context. This stages facts; it dispatches no rules,
            # pool operations, lifecycle observers or resource callbacks.
            self.attribute_runtime.stage(event, 'post')
        if len(self.hit_rows) > before:
            projected_hit_ids.append(hit["id"])
    if projected_hit_ids:
        self.special_damage_groups[group_id] = {
            "request": copy.deepcopy(request), "cycle": cycle,
            "hit_ids": projected_hit_ids,
        }
        if _callbacks and self.special_damage_echoes is not None:
            self.special_damage_echoes.project(self, request, cycle, parent)


def project_direct_lunar_damage(self: MechanicsExecutionPort, request, cycle, parent):
    """Project one explicitly profiled ability hit with the owner's live stats."""
    event_id = request.get("event_id", "special_reaction_damage_request")
    profile_issues = validate_direct_lunar_damage_profile(request.get("ability_profile"))
    if profile_issues:
        raise ElementalPending(
            "direct_lunar_ability_profile_invalid",
            "直伤月曜请求缺少完整且内容哈希匹配的能力profile。",
            event_id,
            {"issues": copy.deepcopy(profile_issues)},
        )
    owner, action_id = request.get("owner"), request.get("action_id")
    action = self.data["actions"].get(action_id)
    ability = self.data["abilities"].get(action.get("ability_id")) if action else None
    binding = request["ability_profile"].get("ability_binding")
    binding_ok = (
        isinstance(binding, dict)
        and binding.get("character_id") == owner
        and isinstance(action, dict)
        and binding.get("ability_id") == action.get("ability_id")
        and binding.get("component_id") == request.get("component")
        and isinstance(ability, dict)
        and binding.get("variant_id") == ability.get("variant")
        and type(ability.get("effective_level")) is int
        and binding.get("effective_level")
        == ability.get("effective_level")
    )
    contract_ok = (
        request.get("type") == "special_reaction_damage_request"
        and request.get("status") == "ready_for_stat_sample"
        and request.get("reaction_type") in {
            "lunar_electro", "lunar_bloom", "lunar_crystallize"}
        and request.get("damage_system") == "lunar"
        and request.get("contributor_ids") == []
        and request.get("actor_sample_policy") == "event_time_owner_attribute"
        and request.get("factor_policy") == "engine_constant"
        and request.get("system_factor") == LUNAR_FACTOR.get(
            request.get("reaction_type"))
        and request.get("application") is None
        and isinstance(request.get("target_id"), str)
        and request.get("target_id") == self.elemental.target
        and isinstance(action, dict)
        and isinstance(ability, dict)
        and owner in self.data["chars"]
        and ability.get("character") == owner
        and binding_ok
    )
    if not contract_ok:
        raise ElementalPending(
            "direct_lunar_damage_contract_mismatch",
            "直伤月曜请求的角色、动作、目标或取样契约无法核对。",
            event_id,
            {"owner": owner, "action_id": action_id,
             "target_id": request.get("target_id"),
             "runtime_target_id": self.elemental.target},
        )
    hit = _direct_lunar_damage_hit(request, cycle=cycle)
    reaction_type = request["reaction_type"]
    event = {
        "id": hit["id"], "origin": cycle, "run_id": f"{cycle}:{hit['id']}",
        "event": "reaction_damage", "character": owner,
        "action_id": action_id,
        "reaction_type": reaction_type, "element": request["element"],
        "reaction_element": request["element"],
        "damage_type_ids": ["damage.reaction"],
        "conditions": dict(self.conditions), "reaction_snapshot": self.snapshot(),
    }
    generated_parent = {
        "id": event_id, "run_id": f"{cycle}:{event_id}",
        "time": self.time, "order": parent.get("order", 0), "origin": cycle,
        "causal": tuple(parent.get("causal", ())),
    }
    self.count_event("reaction_damage")
    prepare_projected_damage(self, event, {"hit": hit, "descriptor": {"report": True}}, event_id)
    self.phase(event, "pre", generated_parent)
    self.hit(event, {"hit": hit, "descriptor": {"report": True}})
    self.phase(event, "post", generated_parent)
    projection = {
        "type": "special_reaction_damage_projection",
        "event_id": event_id+":projection", "request_id": event_id,
        "t": request["t"], "time_tick": request["time_tick"], "cycle": cycle,
        "reaction_type": reaction_type, "reaction_base": False,
        "owner": owner, "action_id": action_id, "hit_id": hit["id"],
        "actor_sample_policy": request["actor_sample_policy"],
        "contributor_weighting": "not_applicable", "status": "executed",
    }
    if self.record_special_event(projection, cycle):
        self.count_event("special_reaction_event")


def project_direct_stellar_damage(self: MechanicsExecutionPort, request, cycle, parent):
    """Project one Star-Superconduct ability hit with live owner stats."""
    event_id = request.get("event_id", "special_reaction_damage_request")
    profile_issues = validate_direct_stellar_damage_profile(
        request.get("ability_profile"))
    if profile_issues:
        raise ElementalPending(
            "direct_stellar_ability_profile_invalid",
            "直伤星超导请求缺少完整且内容哈希匹配的能力profile。",
            event_id, {"issues": copy.deepcopy(profile_issues)},
        )
    owner, action_id = request.get("owner"), request.get("action_id")
    action = self.data["actions"].get(action_id)
    ability = self.data["abilities"].get(action.get("ability_id")) if action else None
    binding = request["ability_profile"]["ability_binding"]
    runtime = self.special_runtime()
    area_id = request.get("area_id")
    current_snapshot = (
        runtime.stellar_superconduct_snapshot(area_id)
        if runtime is not None and area_id in runtime.stellar_field_by_area else None)
    contract_ok = (
        request.get("type") == "special_reaction_damage_request"
        and request.get("status") == "ready_for_stat_sample"
        and request.get("reaction_type") == "stellar_superconduct"
        and request.get("damage_system") == "stellar"
        and request.get("contributor_ids") == []
        and request.get("actor_sample_policy") == "event_time_owner_attribute"
        and request.get("factor_policy")
        == "event_time_last_completed_four_second_window"
        and isinstance(request.get("target_id"), str)
        and request.get("target_id") == self.elemental.target
        and isinstance(action, dict) and isinstance(ability, dict)
        and owner in self.data["chars"] and ability.get("character") == owner
        and binding.get("character_id") == owner
        and binding.get("ability_id") == action.get("ability_id")
        and binding.get("variant_id") == ability.get("variant")
        and type(ability.get("effective_level")) is int
        and binding.get("effective_level") == ability.get("effective_level")
        and current_snapshot == request.get("settled_snapshot")
        and request.get("layers") == (current_snapshot or {}).get("layers")
        and request.get("system_factor") == (current_snapshot or {}).get("system_factor")
        and request.get("application") is None
    )
    if not contract_ok:
        raise ElementalPending(
            "direct_stellar_damage_contract_mismatch",
            "直伤星超导请求的能力、目标、区域或运行时层数无法核对。",
            event_id,
            {"owner": owner, "action_id": action_id,
             "target_id": request.get("target_id"), "area_id": area_id,
             "runtime_target_id": self.elemental.target,
             "runtime_snapshot": copy.deepcopy(current_snapshot)},
        )
    hit = _direct_stellar_damage_hit(request, cycle=cycle)
    event = {
        "id": hit["id"], "origin": cycle, "run_id": f"{cycle}:{hit['id']}",
        "event": "reaction_damage", "character": owner,
        "action_id": action_id, "reaction_type": "stellar_superconduct",
        "element": request["element"], "reaction_element": request["element"],
        "damage_type_ids": ["damage.reaction"],
        "conditions": dict(self.conditions), "reaction_snapshot": self.snapshot(),
    }
    generated_parent = {
        "id": event_id, "run_id": f"{cycle}:{event_id}",
        "time": self.time, "order": parent.get("order", 0), "origin": cycle,
        "causal": tuple(parent.get("causal", ())),
    }
    self.count_event("reaction_damage")
    prepare_projected_damage(self, event, {"hit": hit, "descriptor": {"report": True}}, event_id)
    self.phase(event, "pre", generated_parent)
    self.hit(event, {"hit": hit, "descriptor": {"report": True}})
    self.phase(event, "post", generated_parent)
    projection = {
        "type": "special_reaction_damage_projection",
        "event_id": event_id+":projection", "request_id": event_id,
        "t": request["t"], "time_tick": request["time_tick"], "cycle": cycle,
        "reaction_type": "stellar_superconduct", "reaction_base": False,
        "owner": owner, "action_id": action_id, "hit_id": hit["id"],
        "area_id": area_id, "settled_layers": request["layers"],
        "system_factor": request["system_factor"],
        "actor_sample_policy": request["actor_sample_policy"],
        "contributor_weighting": "not_applicable", "status": "executed",
    }
    if self.record_special_event(projection, cycle):
        self.count_event("special_reaction_event")


def enqueue_special_application(self: MechanicsExecutionPort, request, cycle, parent):
    """Return a reviewed special application to the ordinary event queue."""
    if request.get("time_tick") != self.time:
        raise ElementalPending(
            "special_reaction_deadline_skipped",
            "星月附着请求没有在自身事件时刻进入统一队列。",
            request.get("event_id", "special_reaction_application_request"),
            {"request_time_tick": request.get("time_tick"),
             "runtime_time_tick": self.time},
        )
    register = getattr(self.elemental, "register_special_application", None)
    if not callable(register):
        raise ElementalPending(
            "special_application_bridge_unavailable",
            "当前元素运行时尚未提供星月派生附着接收接口。",
            request.get("event_id", "special_reaction_application_request"),
        )
    action_id = request.get("action_id")
    owner = request.get("owner")
    action = self.data["actions"].get(action_id)
    ability = self.data["abilities"].get(action.get("ability_id")) if action else None
    if not action or not ability or ability.get("character") != owner:
        raise ElementalPending(
            "special_application_causality_missing",
            "星月派生附着的动作与角色因果无法在当前轴中核对。",
            request.get("event_id", "special_reaction_application_request"),
            {"action_id": action_id, "owner": owner},
        )
    descriptor = register(request, target_id=self.elemental.target)
    register_generated(self, request, cycle)
    payload = {
        "id": request["event_id"], "t": request["t"],
        "order": parent.get("order", 0), "action_id": action_id,
        "application": descriptor,
        "generated_by": "special_reaction_runtime",
        "source_request": copy.deepcopy(request),
    }
    child = self.emit_event(
        parent, "elemental_application", 0, payload,
        "special-application:" + request["event_id"],
    )
    # Keep the runtime request identity as the public event identity.  The
    # cycle remains isolated by run_id, exactly like authored repeated
    # events, and causal ordering keeps this after its sibling damage.
    child.update(
        id=request["event_id"], run_id=f"{cycle}:{request['event_id']}",
        time=request["time_tick"], origin=cycle,
    )
    return child


def project_ordinary_bloom_damage(self: MechanicsExecutionPort, request, cycle, parent):
    """Project one reviewed ordinary Dendro-core rupture at detonation time."""
    event_id = request.get("event_id", "ordinary_bloom_damage_request")
    expected_sequence = {
        "reset_seconds": .5,
        "max_hits": 2,
        "scope": ["owner", "target_id", "reaction_type"],
    }
    if (request.get("core_variant") != "ordinary"
            or request.get("core_lifetime_seconds") != 6
            or request.get("field_limit") != 5
            or request.get("damage_sequence") != expected_sequence):
        raise ElementalPending(
            "bloom_core_variant_unreviewed",
            "草原核缺少受审的普通核寿命、场上上限与伤害段限制；丰穰之核不得按普通核代算。",
            event_id,
            {"core_variant": request.get("core_variant"),
             "core_lifetime_seconds": request.get("core_lifetime_seconds"),
             "field_limit": request.get("field_limit"),
             "damage_sequence": copy.deepcopy(request.get("damage_sequence"))},
        )
    if (request.get("type") != "ordinary_bloom_damage_request"
            or request.get("reaction_type") != "bloom"
            or request.get("damage_system") != "transformative"
            or request.get("element") != "dendro"
            or request.get("status") != "ready_for_damage_projection"):
        raise ElementalPending(
            "ordinary_bloom_damage_contract_mismatch",
            "普通草原核爆炸请求与受审的绽放剧变伤害契约不一致。",
            event_id,
        )
    if request.get("time_tick") != self.time:
        raise ElementalPending(
            "ordinary_bloom_deadline_skipped",
            "草原核爆炸没有在自身事件时刻进入统一队列。",
            event_id,
            {"request_time_tick": request.get("time_tick"),
             "runtime_time_tick": self.time},
        )
    owner, target_id = request.get("owner"), request.get("target_id")
    if owner not in self.data["chars"] or target_id != self.elemental.target:
        raise ElementalPending(
            "ordinary_bloom_owner_or_target_missing",
            "草原核生成者或爆炸目标无法在当前队伍与目标模型中核对。",
            event_id, {"owner": owner, "target_id": target_id},
        )
    contributors = request.get("contributor_ids")
    if (not isinstance(contributors, list) or not contributors
            or any(value not in self.data["chars"] for value in contributors)):
        raise ElementalPending(
            "ordinary_bloom_contributors_missing",
            "草原核没有保留可核对的元素附着贡献者。",
            event_id, {"contributor_ids": copy.deepcopy(contributors)},
        )
    if request.get("reason") not in ("natural_expiry", "field_limit_oldest"):
        raise ElementalPending(
            "ordinary_bloom_detonation_reason_unreviewed",
            "草原核爆炸原因尚未接入普通绽放投影。",
            event_id, {"reason": request.get("reason")},
        )
    key = (owner, target_id, "bloom")
    state = self.ordinary_bloom_damage_windows.get(key)
    reset_ticks = TICKS // 2
    if state is None or self.time >= state["anchor"] + reset_ticks:
        first_attempt = state is None
        state = self.ordinary_bloom_damage_windows[key] = {
            "anchor": self.time, "attempts": 1,
        }
        reason = "first_damage_attempt" if first_attempt else "damage_sequence_reset"
    else:
        state["attempts"] += 1
        reason = "damage_sequence"
    sequence_index = state["attempts"] - 1
    allowed = sequence_index < 2
    admission = {
        "type": "ordinary_bloom_damage_admission", "event_id": event_id,
        "cycle": cycle, "t": self.time/TICKS, "time_tick": self.time,
        "reaction_type": "bloom", "owner": owner, "target_id": target_id,
        "area_id": request.get("area_id"), "reason": reason if allowed else "damage_sequence_blocked",
        "allowed": allowed, "sequence_index": sequence_index,
        "shared_key": list(key), "anchor_t": state["anchor"]/TICKS,
        "reset_at": (state["anchor"]+reset_ticks)/TICKS,
        "core_variant": "ordinary", "detonation_reason": request["reason"],
        "status": "executed",
    }
    self.trace.append(copy.deepcopy(admission))
    self.elemental.trace.append(copy.deepcopy(admission))
    if not allowed:
        return
    register_generated(self, request, cycle)
    hit = {
        "id": event_id, "t": request["t"], "character": owner,
        "skill": "绽放", "hit_name": ("草原核爆炸（自然到期）"
            if request["reason"] == "natural_expiry" else "草原核爆炸（场上上限）"),
        "description": "普通草原核爆炸；归属生成核心的触发角色，属性在爆炸时读取。",
        "damage_type_ids": ["damage.reaction"],
        "damage_system": "transformative", "element": "dendro",
        "reaction": {"type": "bloom"}, "count": 1,
        "extensions": {"elemental": {
            "mode": "ordinary_bloom_core_runtime",
            "target_id": target_id, "area_id": request.get("area_id"),
            "parent_reaction_id": request.get("parent_reaction_id"),
            "core_variant": "ordinary", "detonation_reason": request["reason"],
            "owner_sample_phase": "core_detonation",
            "target_sample_phase": "core_detonation",
            "damage_sequence": copy.deepcopy(admission),
            "contributor_ids": copy.deepcopy(contributors),
            "source": request.get("source"),
            "target_selection_policy": "declared_single_target",
            "area_multi_target_projection": "not_implemented",
            "owner_survival_assumption": "party_member_available_no_death_state_model",
        }},
    }
    event = {
        **hit, "event": "reaction_damage", "origin": cycle,
        "run_id": f"{cycle}:{event_id}", "conditions": dict(self.conditions),
        "action_id": None, "action_type_id": None,
        "reaction_type": "bloom", "reaction_element": "dendro",
        "reaction_snapshot": self.snapshot(),
    }
    generated_parent = {
        "id": request.get("parent_reaction_id") or event_id,
        "run_id": f"{cycle}:{request.get('parent_reaction_id') or event_id}",
        "time": self.time, "order": parent.get("order", 0), "origin": cycle,
        "causal": tuple(parent.get("causal", ())),
    }
    self.count_event("reaction_damage")
    prepare_projected_damage(self, event, {"hit": hit, "descriptor": {"report": True}}, event_id)
    self.phase(event, "pre", generated_parent)
    self.hit(event, {"hit": hit, "descriptor": {"report": True}})
    self.phase(event, "post", generated_parent)


def project_swirl_damage(self: MechanicsExecutionPort, ev, parent_event, reaction, reaction_snapshot, reported):
    self.verify_swirl_provider(reaction["owner"], reaction["id"])
    provider_sample = None
    if self.elemental.model["ruleset"] in (DYNAMIC_SWIRL_RULESET, MULTI_AURA_RULESET):
        actor = reaction["owner"]
        graph_receipt = self.attribute_runtime.read(actor, "total", reaction_snapshot) if self.attribute_runtime and self.attribute_runtime.graph else None
        stats, dependencies = self.ordinary_panel(actor, reaction_snapshot, defer_em=bool(graph_receipt), defer_atk=bool(self.attribute_runtime and self.attribute_runtime.multistat))
        em = graph_receipt["value"] if graph_receipt else stats["em"]
        em_dependencies = []
        for dependency in dependencies:
            if dependency["zone"] != "em":
                continue
            item = copy.deepcopy(dependency)
            if "rule_id" in item:
                item.update(path=f"rules.{item['rule_id']}.{item['effect_id']}",
                            source_id=self.rules[item["rule_id"]]["source_id"])
            else:
                item["path"] = f"buffs.{item['buff_id']}.{item['effect_id']}"
            em_dependencies.append(item)
        if not math.isfinite(em) or em < 0:
            raise ElementalPending("swirl_em_sample_invalid", "扩散生成时的有效总精通必须有限且非负。", reaction["id"],
                                   {"character": actor, "value": em if math.isfinite(em) else None,
                                    "dependencies": em_dependencies})
        provider_sample = {"contract": "ordinary-em-sample-v1", "character": actor,
                           "stat": "em", "basis": "total", "phase": "reaction_created",
                           "t": reaction_snapshot["time"] / TICKS,
                           "base_value": next(c for c in self.template["characters"] if c["id"] == actor)["stats"]["em"],
                           "value": em, "dependencies": em_dependencies}
        if graph_receipt:
            provider_sample.update(contract="attribute-em-sample-v2", dependencies=[
                {"attribute_node_id": row["id"], "value": row["evaluated_value"],
                 "component": copy.deepcopy(row["component"]), "instance_id": row["instance_id"], "layer_id": row["layer_id"]}
                for row in graph_receipt["contributions"] if row["decision"] == "included"])
        if self.attribute_runtime:
            attribute_receipt = graph_receipt or self.attribute_runtime.read(actor, "total", reaction_snapshot)
            if attribute_receipt["value"] != em:
                raise AttributeLedgerError("attribute_projection_mismatch", "属性账与实际精通投影不一致", actor)
            provider_sample["attribute_receipt_id"] = attribute_receipt["receipt_id"]
    try:
        receipt = self.elemental.attempt_reaction_damage(reaction)
    except ElementalPending:
        self.elemental.trace[-1]["cycle"] = ev["origin"]
        self.trace.append(copy.deepcopy(self.elemental.trace[-1]))
        raise
    receipt["cycle"] = ev["origin"]
    self.elemental.trace[-1]["cycle"] = ev["origin"]
    self.trace.append(copy.deepcopy(receipt))
    if not receipt["allowed"]:
        return
    self.count_event("reaction_damage")
    metadata = {"mode": "simulated", "ruleset": self.elemental.model["ruleset"], "damage_scope": "origin_target",
                "target_id": reaction["target_id"], "reaction_id": reaction["id"], "reaction_run_id": reaction["run_id"],
                "parent_event_id": reaction["parent_event_id"], "parent_run_id": reaction["parent_run_id"],
                "run_id": receipt["run_id"], "action_id": reaction["action_id"], "reaction_type": "swirl",
                "reaction_element": reaction["reaction_element"], "source": receipt["source"],
                "provider_sample_phase": "reaction_created", "enemy_sample_phase": "reaction_damage_pre",
                "provider_sample_t": reaction_snapshot["time"] / TICKS,
                "reaction": copy.deepcopy(reaction), "damage_icd": copy.deepcopy(receipt)}
    if provider_sample is not None:
        metadata["provider_stat_sample"] = provider_sample
    hit = {"id": receipt["event_id"], "t": self.time/TICKS, "character": reaction["owner"],
            "skill": "扩散", "hit_name": {"cryo": "冰扩散", "hydro": "水扩散", "pyro": "火扩散",
                                                "electro": "雷扩散"}[reaction["reaction_element"]],
           "damage_type_ids": ["damage.reaction"], "damage_system": "transformative", "element": reaction["reaction_element"],
           "reaction": {"type": "swirl"}, "count": 1, "extensions": {"elemental": metadata}}
    event = {**hit, "event": "reaction_damage", "origin": ev["origin"], "run_id": receipt["run_id"], "conditions": dict(self.conditions),
             "action_id": reaction["action_id"], "action_type_id": parent_event.get("action_type_id"),
             "reaction_type": "swirl", "reaction_element": reaction["reaction_element"], "reaction_snapshot": reaction_snapshot}
    prepare_projected_damage(self, event, {"hit": hit, "descriptor": {"report": reported}}, reaction['id'])
    if self.attribute_runtime:
        self.attribute_runtime.stage(event, "reaction_damage_pre")
    self.hit(event, {"hit": hit, "descriptor": {"report": reported}})
    self.phase(event, "post", {**ev, "id": receipt["event_id"], "run_id": receipt["run_id"]})


__all__ = [
    "enqueue_special_application",
    "project_direct_lunar_damage",
    "project_direct_stellar_damage",
    "project_ordinary_bloom_damage",
    "project_special_damage",
    "project_swirl_damage",
]

