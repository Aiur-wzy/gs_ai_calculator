"""Compile a reviewed finite-hit benefit into an unbound D20 rule template.

This module consumes materialized evidence only. It never opens a database,
routes actions, or interprets an arbitrary modifier program.
"""
from __future__ import annotations

import copy
import json
import math
import re
from decimal import Decimal

from genshin_runtime.limited_hit_benefit_identity import (
    fingerprint,
    reference_contract,
    verify_limited_hit_benefit_projection,
)

COMPILER = "reference-limited-hit-benefit-v1"
PROFILE_ID = "single-cast-limited-hit-benefit-reference-v1"
LIMITS = {"max_source_bytes": 512 * 1024, "max_parent_bytes": 64 * 1024,
          "max_declaration_bytes": 32 * 1024, "max_output_bytes": 256 * 1024,
          "max_source_text": 4096}
_TYPES = ["damage.normal_attack", "damage.charged_attack", "damage.plunging_attack",
          "damage.elemental_skill", "damage.elemental_burst"]
_PARENT_KEYS = {"kind", "variant", "selection", "param_index", "profile_fingerprint",
                "source_dependency_fingerprint", "metadata_source_fingerprint",
                "metadata_dependency_fingerprint", "hit_semantics", "application_semantics", "contact_semantics"}
_SELECTION_KEYS = {"character_id", "skill_depot_id", "skill_id", "constellation", "talent_level"}
_SCENE = {"target_kind": "ordinary_non_animal_monster", "party_mode": "single_player_fixed",
          "all_party_alive": True, "all_party_in_apply_predicate": True,
          "all_party_in_retention_predicate": True, "initial_effect": "none",
          "no_external_quill_changes": True, "hitlag_policy": "zero_hitlag_reference"}


class _Unsupported(ValueError):
    def __init__(self, code, path, message):
        super().__init__(message)
        self.code, self.path = code, path


def _require(condition, code, path, message):
    if not condition:
        raise _Unsupported(code, path, message)


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf8"))


def _same_json(left, right):
    """JSON identity comparisons must not equate bool with an integer."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _ticks(value, path):
    _require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**9,
             "limited_hit_benefit_calibration_pending", path, "时间须明确为有限、非负、非布尔数值")
    ticks = Decimal(str(value)) * 1000000
    _require(ticks == ticks.to_integral_value(), "limited_hit_benefit_calibration_pending", path,
             "时间必须精确为整数微秒；未自动量化")
    return int(ticks)


def _evidence(value, code, path):
    _require(value.get("status") in ("assumed", "confirmed") and isinstance(value.get("source"), str)
             and 0 < len(value["source"].strip()) <= LIMITS["max_source_text"], code, path,
             "需要明确接受依据；未知或空白依据不能执行")


def limited_hit_benefit_compiler_caps():
    scene = {key: None if isinstance(value, bool) else "unknown" for key, value in _SCENE.items()}
    scene.update(status="pending", source="")
    calibration = {"activation_offset_seconds": None, "precision_seconds": None,
                   "status": "pending", "source": ""}
    return {"compiler": COMPILER, "compiler_version": 1, "profile_id": PROFILE_ID, "profile_version": 1,
            "limits": dict(LIMITS), "default_scenario": scene, "default_calibration": calibration,
            "accepted_scenario_values": copy.deepcopy(_SCENE), "reference_contract": copy.deepcopy(reference_contract()),
            "whole_source_status": "partial", "gameplay_interpretation": "reference_adapted_not_decoded",
            "template_requires": ["id", "owner", "source_id", "trigger.action_ids", "initial_rule.rule_id"],
            "supported": ["C0有效10级固定长按E", "独立每角色额度重置", "普通单目标逐命中提供者当前攻击力平值"],
            "unsupported": ["混合点长按", "命座分支", "聚合多命中", "多目标", "未建模取消和空间移除",
                            "复杂提供者攻击力依赖", "原文任意程序执行"]}


def _parent(parent, bundle, rebuilt):
    code = "limited_hit_benefit_parent_unsupported"
    _require(isinstance(parent, dict) and set(parent) == _PARENT_KEYS, code, "parent_semantics", "父语义字段缺失或未知")
    candidate, bridge = rebuilt["candidate"], rebuilt["parent_bridge"]
    _require(candidate["status"] == "ready" and candidate["variant"] == bridge["variant"] == "hold"
             and parent["kind"] == "calibrated_single_contact_v1" and parent["variant"] == "hold", code,
             "parent_semantics.variant", "只接受来源桥认证的单次长按E父档案")
    selection = parent["selection"]
    _require(isinstance(selection, dict) and set(selection) == _SELECTION_KEYS
             and all(type(selection[k]) is int for k in _SELECTION_KEYS), code, "parent_semantics.selection", "父选择必须是明确整数身份")
    _require(all(selection[k] > 0 for k in ("character_id", "skill_depot_id", "skill_id"))
             and selection["constellation"] == 0 and selection["talent_level"] == 10, code,
             "parent_semantics.selection", "首片仅C0、有效10级技能来源")
    source_selection = bundle["selection"]
    _require(all(_same_json(selection[k], source_selection[k]) for k in
                 ("character_id", "skill_depot_id", "skill_id", "constellation"))
             and type(bundle["level"]["effective_level"]) is int and bundle["level"]["effective_level"] == 10,
             code, "parent_semantics.selection", "父选择与新鲜源身份不一致")
    _require(type(parent["param_index"]) is int and _same_json(parent["param_index"], bundle["param_index"]),
             code, "parent_semantics.param_index", "直接攻击参数与认证父桥不一致")
    for key in ("profile_fingerprint", "source_dependency_fingerprint"):
        _require(isinstance(parent[key], str) and re.fullmatch("[0-9a-f]{64}", parent[key]) is not None,
                 code, "parent_semantics." + key, "父档案接受及来源身份缺失")
    _require(parent["metadata_source_fingerprint"] is None and parent["metadata_dependency_fingerprint"] is None,
             code, "parent_semantics.metadata_source_fingerprint", "本片不以冰翎来源解除攻击元信息的未支持分支")
    _require(_same_json(parent["hit_semantics"], {"damage_system": "classic", "scaling_stat": "atk", "count": 1,
              "element": "cryo", "damage_type_ids": ["damage.elemental_skill"]}), code,
             "parent_semantics.hit_semantics", "父命中须为明确冰元素、单次普通E伤害")
    _require(all(isinstance(parent[k], dict) and parent[k] for k in ("application_semantics", "contact_semantics")),
             code, "parent_semantics", "父附着与接触语义必须完整保留")


def _declarations(scenario, calibration):
    code = "limited_hit_benefit_scenario_pending"
    _require(isinstance(scenario, dict) and set(scenario) == {*_SCENE, "status", "source"},
             code, "scenario", "场景字段缺失或未知")
    _require(all(_same_json(scenario[k], v) for k, v in _SCENE.items()), code, "scenario",
             "首片需要普通单目标、固定存活队伍、范围谓词、无初态或外来冰翎与零卡肉参考")
    _evidence(scenario, code, "scenario")
    code = "limited_hit_benefit_calibration_pending"
    _require(isinstance(calibration, dict) and set(calibration) ==
             {"activation_offset_seconds", "precision_seconds", "status", "source"},
             code, "calibration", "施加时点校准字段缺失或未知")
    _evidence(calibration, code, "calibration")
    return {key: _ticks(calibration[key], "calibration." + key) for key in
            ("activation_offset_seconds", "precision_seconds")}


def _templates(rebuilt, scenario, calibration, ticks):
    values = {key: rebuilt["projection"][key]["value"] for key in ("coefficient", "duration_seconds", "capacity")}
    _require(all(rebuilt["projection"][key]["status"] == "ready" for key in values),
             "limited_hit_benefit_source_unsupported", "projection", "收益参数投影未就绪")
    factor, duration, capacity = (values[k] for k in ("coefficient", "duration_seconds", "capacity"))
    _require(type(factor) in (int, float) and math.isfinite(factor) and 0 <= factor <= 10**9,
             "limited_hit_benefit_source_unsupported", "projection.coefficient", "系数不在有限来源数值域")
    _require(type(capacity) in (int, float) and math.isfinite(capacity) and 1 <= capacity <= 10000 and int(capacity) == capacity,
             "limited_hit_benefit_source_unsupported", "projection.capacity", "次数须为有限正整数")
    duration_ticks = _ticks(duration, "projection.duration_seconds")
    _require(duration_ticks > 0, "limited_hit_benefit_source_unsupported", "projection.duration_seconds", "持续时间必须为正")
    consume = {"event": "hit", "phase": "pre", "elements": ["cryo"], "damage_type_ids": list(_TYPES)}
    rule = {"name_zh": "冰翎", "trigger": {"event": "action_start", "phase": "pre"},
            "beneficiaries": {"mode": "team", "scope": "locked"},
            "timing": {"delay": calibration["activation_offset_seconds"], "duration": duration,
                       "cooldown": 0, "cooldown_anchor": "success"},
            "stacking": {"max": 1, "add": 1, "policy": "replace"},
            "quota": {"capacity": int(capacity), "scope": "per_beneficiary", "refresh": "reset", "consume": consume},
            "effects": [{"id": "current_attack_flat", "benefit_type_id": "benefit.base_add.all", "domain": "provider",
                         "value": {"kind": "stat", "subject": "owner", "stat": "atk", "factor": factor, "sample": "current"},
                         "filter": copy.deepcopy(consume)}],
            "status": "assumed", "source": "受限冰翎参考；" + scenario["source"] + "；" + calibration["source"]}
    certificate = {"candidate_id": rebuilt["candidate"]["candidate_id"], "family_id": rebuilt["candidate"]["family_id"],
        "variant": "hold", "coefficient": factor, "duration_seconds": duration, "capacity": int(capacity),
        "activation": {"event": "action_start", "phase": "pre", "offset_ticks": ticks["activation_offset_seconds"],
                       "precision_ticks": ticks["precision_seconds"], "ordering": "actual_queue_key_before_initial_hit",
                       "admission": "successful_cast", "initial_hit_landed_required": False},
        "consumption": {"scope": "per_beneficiary", "refresh": "reset", "pool": "one_rule_for_all_casts",
                        "damage_system": "classic", "count": 1, "target_count": 1, "filter": copy.deepcopy(consume),
                        "report_false_consumes": True, "excluded_benefit_consumes": True},
        "sampling": {"domain": "provider", "subject": "owner", "stat": "atk", "sample": "current",
                     "contribution_scope": "ordinary_constant_panel_only"},
        "lifetime": {"duration_ticks": duration_ticks, "interval": "left_closed_right_open", "hitlag_policy": "zero_hitlag_reference"},
        "routing_required": ["id", "owner", "source_id", "trigger.action_ids", "initial_rule.rule_id"],
        "app_guards_required": ["actual_parent_identity_and_routes", "activation_before_initial_hit",
                                "no_unmodelled_cancel", "supported_provider_atk_contributions", "single_count_classic_consumers",
                                "single_provider_and_variant", "no_duplicate_benefit_source"]}
    return rule, {"cooldown_remaining": 0, "instances": []}, certificate


def compile_limited_hit_benefit_source(bundle, *, parent_semantics, scenario, calibration):
    out = {"compiler": COMPILER, "compiler_version": 1, "profile_id": PROFILE_ID, "profile_version": 1,
           "status": "pending", "source_status": "pending", "benefit_status": "pending", "whole_source_status": "partial",
           "gameplay_interpretation": "reference_adapted_not_decoded", "source_fingerprint": None,
           "semantic_dependency": None, "benefit_fingerprint": None, "benefit_certificate": None,
           "rule_template": None, "initial_rule_template": None, "issues": [], "limits": dict(LIMITS)}
    try:
        for value, limit, path in ((bundle, "max_source_bytes", "source"),
                                   (parent_semantics, "max_parent_bytes", "parent_semantics"),
                                   ([scenario, calibration], "max_declaration_bytes", "declarations")):
            _require(_size(value) <= LIMITS[limit], "limited_hit_benefit_limit", path, "输入超过有限字节预算")
        rebuilt = verify_limited_hit_benefit_projection(bundle)
        out.update(source_status="ready", source_fingerprint=bundle["source_fingerprint"])
        _parent(parent_semantics, bundle, rebuilt)
        ticks = _declarations(scenario, calibration)
        rule, initial, certificate = _templates(rebuilt, scenario, calibration, ticks)
        dependency = {"compiler": COMPILER, "compiler_version": 1, "profile_id": PROFILE_ID, "profile_version": 1,
                      "reference_contract": copy.deepcopy(reference_contract()), "source_fingerprint": bundle["source_fingerprint"],
                      "parent_semantics": copy.deepcopy(parent_semantics), "scenario": copy.deepcopy(scenario),
                      "calibration": copy.deepcopy(calibration), "benefit_certificate": certificate}
        out.update(status="ready", benefit_status="ready", semantic_dependency=dependency,
                   benefit_fingerprint=fingerprint(dependency), benefit_certificate=copy.deepcopy(certificate),
                   rule_template=rule, initial_rule_template=initial)
        _require(_size(out) <= LIMITS["max_output_bytes"], "limited_hit_benefit_limit", "result", "输出超过有限字节预算")
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        out.update(status="pending", benefit_status="pending", semantic_dependency=None,
                   benefit_fingerprint=None, benefit_certificate=None, rule_template=None, initial_rule_template=None)
        out["issues"].append({"code": getattr(exc, "code", "limited_hit_benefit_source_or_parent_unsupported"),
                              "path": getattr(exc, "path", "source"), "message": str(exc)[:1024]})
    return out
