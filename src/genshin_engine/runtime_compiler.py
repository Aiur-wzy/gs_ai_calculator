"""D21: pure, bounded compilation of an explicitly adopted upstream subset.

The adapter executes no source code and does not discover or edit actions/hits.
Unknown behavior is rejected; exact legacy sentinels require a fingerprinted
assumption profile. Readiness is deliberately distinct from game verification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from decimal import Decimal

from genshin_runtime.normalization import for_commit, verify_receipt

PROFILE_ID = "equipment-effects-fixed-single-player"
PROFILE_VERSION = 1
COMPILER_VERSION = 2
_SCENARIO_FLAGS = ("single_player_fixed", "all_targets_monsters",
                   "single_unique_provider", "no_external_state_changes")
_TAGS = {"Elemental_Burst": "damage.elemental_burst",
         "Elemental_Art": "damage.elemental_skill",
         "Elemental_Art_Hold": "damage.elemental_skill",
         "Elemental_Art_Extra": "damage.elemental_skill",
         "FallingAttack": "damage.plunging_attack"}
_DEFAULT_CONTAINER = {"elementDurability": 100, "minus_one": -1,
                      "IAKPBCLBNKF": -1, "isLimitedProperties": True}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _escape(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def compiler_caps():
    """Return an independent public contract; no model or DB is loaded."""
    return {"compiler_version": COMPILER_VERSION, "bundle_version": 1,
            "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
            "part_id": "upstream", "source_id_format": "{binding_id}:upstream",
            "default_scenario": {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
                "accepted_assumptions_fingerprint": None,
                **{name: True for name in _SCENARIO_FLAGS}, "max_party_distance": 40,
                "static_policy": "included"},
            "scenario_schema": {"type": "object", "additionalProperties": False,
                "required": ["profile_id", "profile_version", "accepted_assumptions_fingerprint",
                             *_SCENARIO_FLAGS, "max_party_distance", "static_policy"],
                "properties": {"profile_id": {"const": PROFILE_ID},
                    "profile_version": {"const": PROFILE_VERSION},
                    "accepted_assumptions_fingerprint": {"type": ["string", "null"]},
                    **{name: {"const": True} for name in _SCENARIO_FLAGS},
                    "max_party_distance": {"type": "number", "minimum": 0, "maximum": 40},
                    "static_policy": {"enum": ["execute", "included"]}}},
            "supported": ["单一零初值参数装配", "分类静态增伤", "成功爆发触发全队基础攻击加成"],
            "unsupported": ["动作/命中自动生成", "多提供者唯一效果冲突", "一般全局变量/实体/反应",
                            "未知字段或不同哨兵值", "无分类承载的已含静态增伤"],
            "gameplay_semantics": "仅在明确采用的适配假设和场景范围内执行，非完整游戏验证"}


class _Stop(Exception):
    pass


class _Compiler:
    def __init__(self, bundle, owner, source_id, scenario, period):
        self.bundle, self.owner, self.source_id = bundle, owner, source_id
        self.scenario, self.period = scenario, period
        self.issues, self.assumptions, self.usage = [], [], []
        self.parameters, self.parameter_sources = {}, {}
        self.parts, self.rules, self.initial, self.static = [], [], [], []
        self.unique_keys, self.kind = [], None

    def fail(self, code, path, message, *, severity="pending", **details):
        self.issues.append({"code": code, "path": path, "message": message,
                            "severity": severity, **details})
        raise _Stop

    def fields(self, obj, required, optional, path):
        if not isinstance(obj, dict):
            self.fail("invalid_shape", path, "需要对象。", severity="error")
        extras, missing = set(obj) - set(required) - set(optional), set(required) - set(obj)
        for key in sorted(extras):
            self.issues.append({"code": "unsupported_field", "path": path + "/" + _escape(key),
                                "message": "此上下文中的字段未经适配，不会静默忽略。", "severity": "pending"})
        for key in sorted(missing):
            self.issues.append({"code": "unsupported_default", "path": path + "/" + _escape(key),
                                "message": "必要字段缺失；未猜测原生默认值。", "severity": "pending"})
        if extras or missing:
            raise _Stop

    def scalar(self, value, path, *, positive=False):
        if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
            self.fail("invalid_number", path, "需要有限数值。", severity="error")
        self.usage.append({"path": path, "classification": "consumed", "value": value})
        return value

    def time(self, value, path):
        self.scalar(value, path, positive=True)
        ticks = Decimal(str(value)) * 1_000_000
        if ticks != ticks.to_integral_value() or ticks > 10**15:
            self.fail("unsupported_time_precision", path, "时间须能按现有微秒队列准确表达。")
        return value

    def text(self, value, path):
        if not isinstance(value, str) or not value.strip():
            self.fail("invalid_identity", path, "需要非空标识。", severity="error")
        self.usage.append({"path": path, "classification": "consumed", "value": value})
        return value

    def exact(self, value, expected, path, assumption=None):
        if type(value) is not type(expected) and not (type(value) in (float, int) and type(expected) in (float, int)):
            self.fail("unsupported_value", path, "字段类型不符合已冻结适配。", expected=expected, actual=value)
        if value != expected:
            self.fail("unsupported_value", path, "字段值不符合已冻结适配。", expected=expected, actual=value)
        classification = "assumed_under_profile" if assumption else "consumed"
        self.usage.append({"path": path, "classification": classification, "value": copy.deepcopy(value)})
        if assumption:
            self.assumptions.append({"path": path, "raw_value": copy.deepcopy(value),
                                     "assumption": assumption, "status": "unverified_game_semantics"})

    def defaults(self, obj, path):
        self.fields(obj, _DEFAULT_CONTAINER, (), path)
        for key, value in _DEFAULT_CONTAINER.items():
            self.exact(obj[key], value, path + "/" + key,
                "固定原始默认容器在当前装备/参数/存活状态不变场景不额外产生数值或事件；未证明其一般游戏语义。")

    def evidence(self, obj, path, payload_key, *, definition=True):
        if not isinstance(obj, dict):
            self.fail("invalid_shape", path, "来源记录必须为对象。", severity="error")
        identity_fields = ("definition_id",) if definition else ()
        for key in (*identity_fields, "source_path", "json_pointer", "source_sha256", payload_key):
            if key not in obj:
                self.fail("missing_source_evidence", path + "/" + key, "装配包缺少可追溯来源。", severity="error")
        for key in (*identity_fields, "source_path", "source_sha256"):
            self.text(obj[key], path + "/" + key)
        if not isinstance(obj["json_pointer"], str) or not obj["json_pointer"].startswith("/"):
            self.fail("invalid_source_pointer", path + "/json_pointer", "需要源JSON Pointer。", severity="error")
        if not re.fullmatch(r"[0-9a-f]{64}", obj["source_sha256"]):
            self.fail("invalid_source_hash", path + "/source_sha256", "需要源SHA256。", severity="error")

    def param(self, value, path):
        if type(value) in (int, float):
            return self.scalar(value, path)
        if not isinstance(value, str) or value not in self.parameters:
            self.fail("unresolved_param", path, "仅支持有限常数或已唯一装配的能力变量。")
        self.usage.append({"path": path, "classification": "consumed", "parameter": value,
                           "value": self.parameters[value], "binding": self.parameter_sources[value]})
        return self.parameters[value]

    def predicate_monster(self, obj, path):
        self.fields(obj, ("$type", "target", "value", "entityTypes"), (), path)
        self.exact(obj["$type"], "ByEntityTypes", path + "/$type")
        self.exact(obj["target"], "Target", path + "/target")
        self.exact(obj["entityTypes"], ["Monster"], path + "/entityTypes")
        self.exact(obj["value"], -1, path + "/value",
                   "在明确单Monster目标场景中−1哨兵不改变entityTypes过滤；原生选择语义未完全验证。")

    def predicate_distance(self, obj, path):
        self.fields(obj, ("$type", "target", "value", "logic"), (), path)
        self.exact(obj["$type"], "ByTargetPositionToSelfPosition", path + "/$type")
        self.exact(obj["target"], "Target", path + "/target")
        self.exact(obj["logic"], "LesserOrEqual", path + "/logic")
        self.exact(obj["value"], 40, path + "/value")

    def action_modifier(self, obj, expected_type, path):
        self.fields(obj, ("$type", "target", "actorIceSubHurtDelta", "doOffStage", "modifierName",
                          "predicates", "canBeHandledOnRecover"), (), path)
        self.exact(obj["$type"], expected_type, path + "/$type")
        self.exact(obj["target"], "AllPlayerAvatars", path + "/target")
        self.exact(obj["actorIceSubHurtDelta"], -1, path + "/actorIceSubHurtDelta",
                   "−1为精确匹配哨兵；不参与本受限模型的冰伤运算，不宣称字段一般语义已知。")
        self.exact(obj["doOffStage"], False, path + "/doOffStage",
                   "提供者在前台成功Q时执行；此值不把AllPlayerAvatars缩为前台受益者，范围内后台队员也受益。")
        self.exact(obj["canBeHandledOnRecover"], True, path + "/canBeHandledOnRecover",
                   "本场景无恢复/死亡/离队，不模拟一般恢复处理。")
        predicates = obj["predicates"]
        if not isinstance(predicates, list) or len(predicates) != 1:
            self.fail("unsupported_predicate", path + "/predicates", "仅支持单一已验证形状的距离谓词。")
        self.predicate_distance(predicates[0], path + "/predicates/0")
        return self.text(obj["modifierName"], path + "/modifierName")

    def part(self, rule, delivery, label):
        return {"part_id": "upstream", "label": label, "state": "resolved", "rule": rule,
                "delivery": delivery, "conditions": [{"condition_id": "upstream-profile", "description": "已采用的受限结构适配场景",
                    "state": "confirmed", "scope": "always", "intervals": [],
                    "evidence": [{"kind": "compiler_profile", "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION}]}],
                "dependencies": {"mode": "source_only", "context_fingerprint": None,
                                 "note": "每次组合重新编译；机器适配证明不等于人工游戏审核。"},
                "reason": None, "extensions": {"upstream_compiled": True}}

    def static_mixin(self, mixin, path):
        self.fields(mixin, ("$type", "actorIceSubHurtDelta", "damagePercentageRatio", "attackTags"), ("predicates",), path)
        self.exact(mixin["$type"], "ModifyDamageMixin", path + "/$type")
        self.exact(mixin["actorIceSubHurtDelta"], -1, path + "/actorIceSubHurtDelta",
                   "−1为精确匹配哨兵；不参与本受限模型的冰伤运算，不宣称字段一般语义已知。")
        if "predicates" in mixin:
            if not isinstance(mixin["predicates"], list) or len(mixin["predicates"]) != 1:
                self.fail("unsupported_predicate", path + "/predicates", "只接受单Monster谓词。")
            self.predicate_monster(mixin["predicates"][0], path + "/predicates/0")
        tags = mixin["attackTags"]
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or tag not in _TAGS for tag in tags) or len(set(tags)) != len(tags):
            self.fail("unsupported_attack_tag", path + "/attackTags", "未知、重复或空攻击分类，不能猜映射。")
        # The skill tags must cover all variants of the supported D20 category.
        if any(t.startswith("Elemental_Art") for t in tags) and not {"Elemental_Art", "Elemental_Art_Hold", "Elemental_Art_Extra"}.issubset(tags):
            self.fail("unsupported_attack_tag_subset", path + "/attackTags", "部分战技变体不能无损映射成整个战技伤害类。")
        self.usage.append({"path": path + "/attackTags", "classification": "consumed", "value": tags})
        amount = self.param(mixin["damagePercentageRatio"], path + "/damagePercentageRatio")
        self.assumptions.append({"path": path + "/attackTags", "raw_value": tags,
            "assumption": "调用者已声明命中的damage_type_ids符合对应上游攻击标签分类；本期不从动画独立验证命中分类。",
            "status": "unverified_game_semantics"})
        self.kind = "static_damage"
        effect = {"id": "value", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus", "stat": "all",
                  "applies_to": {"mode": "types", "damage_type_ids": sorted({_TAGS[tag] for tag in tags})}}
        part = self.part({"kind": "constant", "value": amount},
            {"kind": "buff", "start": 0, "end": self.period, "scope": "team", "target": self.owner,
             "source": "上游结构编译·常驻分类增伤", "effect": effect}, "上游常驻分类增伤")
        self.parts.append(part)
        self.static.append(copy.deepcopy(part))

    def dynamic_mixin(self, body, mixin, path, body_path):
        self.fields(mixin, ("$type", "actorIceSubHurtDelta", "onTriggerUltimateSkill", "clearSkillIdDelay"), (), path)
        self.exact(mixin["$type"], "OnAvatarUseSkillMixin", path + "/$type")
        self.exact(mixin["actorIceSubHurtDelta"], -1, path + "/actorIceSubHurtDelta",
                   "−1为精确匹配哨兵；不参与本受限模型的冰伤运算，不宣称字段一般语义已知。")
        self.exact(mixin["clearSkillIdDelay"], 0.20000000298023224, path + "/clearSkillIdDelay",
                   "每个D20已准入爆发只桥接一次成功事件，不模拟客户端重复技能通知及原生清理时间。")
        actions = mixin["onTriggerUltimateSkill"]
        if not isinstance(actions, list) or len(actions) != 2:
            self.fail("unsupported_action_sequence", path + "/onTriggerUltimateSkill", "仅支持无副作用的先移除再施加。")
        names = [self.action_modifier(action, expected, path + f"/onTriggerUltimateSkill/{i}")
                 for i, (action, expected) in enumerate(zip(actions, ("RemoveUniqueModifier", "ApplyModifier")))]
        if names[0] != names[1]:
            self.fail("unsupported_modifier_sequence", path, "移除和施加必须引用同一modifier。")
        modifiers = body.get("modifiers")
        if not isinstance(modifiers, dict) or set(modifiers) != {names[0]}:
            self.fail("unsupported_modifier_graph", body_path + "/modifiers", "本图必须只有完整可消费的同一modifier。")
        mod, mp = modifiers[names[0]], body_path + "/modifiers/" + _escape(names[0])
        self.fields(mod, ("duration", "elementDurability", "minus_one", "IAKPBCLBNKF",
                          "reApplyModifierOnStateChange", "isUnique", "isLimitedProperties", "properties", "buffID", "state"), (), mp)
        for key, value in _DEFAULT_CONTAINER.items():
            self.exact(mod[key], value, mp + "/" + key,
                       "仅按精确默认值采用受限适配，不将modifier耐久作为攻击附着量。")
        for key in ("reApplyModifierOnStateChange", "isUnique"):
            self.exact(mod[key], True, mp + "/" + key,
                       "常量参数、单一提供者、无恢复/死亡/离队/形态或装备变化；不复现一般重应用与争抢语义。")
        self.exact(mod["buffID"], 10001, mp + "/buffID",
                   "固定状态标识不参与本模型伤害运算；没有监听该状态的其它未知来源。")
        self.exact(mod["state"], "AttackUp", mp + "/state",
                   "仅消费显式攻击属性，不模拟其它未知状态监听者。")
        self.fields(mod["properties"], ("Actor_AttackRatio",), (), mp + "/properties")
        amount = self.param(mod["properties"]["Actor_AttackRatio"], mp + "/properties/Actor_AttackRatio")
        duration = self.time(self.param(mod["duration"], mp + "/duration"), mp + "/duration")
        rid = "upstream." + _hash({"source_id": self.source_id, "pointer": path})[:24]
        rule = {"id": rid, "name_zh": "上游爆发触发·全队攻击加成", "owner": self.owner, "source_id": self.source_id,
                "trigger": {"event": "action_start", "phase": "post", "characters": [self.owner],
                            "action_type_ids": ["action.elemental_burst"]},
                "beneficiaries": {"mode": "team", "scope": "locked"},
                "timing": {"delay": 0, "duration": duration, "cooldown": 0, "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [{"id": "value", "benefit_type_id": "benefit.stat_pct.atk", "value": amount, "domain": "self"}],
                "status": "assumed", "source": "上游结构编译；明确适配profile下的成功爆发语义"}
        self.kind = "burst_team_attack"
        self.rules.append(rule)
        self.initial.append({"rule_id": rid, "cooldown_remaining": 0, "instances": []})
        self.parts.append(self.part({"kind": "mechanics", "rule_id": rid}, {"kind": "mechanics"}, "上游爆发触发全队攻击加成"))
        self.unique_keys.append(names[0])

    def build(self):
        b = self.bundle
        if not isinstance(b, dict) or b.get("bundle_version") != 1:
            self.fail("unsupported_bundle", "/bundle_version", "需要装配包版本1。", severity="error")
        if b.get("status") != "ready":
            self.fail("source_not_closed", "/status", "数据库装配未闭合，不能编译。", source_issues=b.get("issues", []))
        if b.get("issues") or b.get("reference_closed") is False:
            self.fail("inconsistent_source_closure", "/references", "来源仍有结构诊断或未闭合引用。", source_issues=b.get("issues", []))
        for key in ("source", "param_list", "talent", "abilities", "references", "content_fingerprint", "affix", "snapshot"):
            if key not in b:
                self.fail("missing_bundle_field", "/" + key, "装配包不完整。", severity="error")
        self.text(self.owner, "/owner")
        self.text(self.source_id, "/source_id")
        if not self.source_id.endswith(":upstream"):
            self.fail("invalid_source_id", "/source_id", "来源ID须为binding_id:upstream。", severity="error")
        self.time(self.period, "/period")
        source = b["source"]
        if not isinstance(source, dict) or source.get("kind") != "equip_affix":
            self.fail("unsupported_source", "/source", "首期仅编译精确装备affix来源。")
        for key in ("affix_id", "affix_group_id", "source_level", "version_line", "snapshot_id", "upstream_commit"):
            if key not in source:
                self.fail("missing_source_identity", "/source/" + key, "缺少同快照来源身份。", severity="error")
        for key in ("affix_id", "affix_group_id", "source_level"):
            if type(source[key]) is not int or source[key] < (0 if key == "source_level" else 1):
                self.fail("invalid_source_identity", "/source/" + key, "来源身份必须为有效整数。", severity="error")
        if source["version_line"] not in ("release", "beta"):
            self.fail("invalid_source_identity", "/source/version_line", "未知版本线。", severity="error")
        for key in ("snapshot_id", "upstream_commit"):
            self.text(source[key], "/source/" + key)
        if not isinstance(b["snapshot"], dict):
            self.fail("invalid_snapshot", "/snapshot", "需要快照元数据。", severity="error")
        for key in ("snapshot_id", "upstream_commit", "version_line"):
            if key in b["snapshot"] and b["snapshot"][key] != source[key]:
                self.fail("mixed_snapshot", "/snapshot/" + key, "装配快照身份不一致。", severity="error")
        if not isinstance(b["references"], list):
            self.fail("invalid_shape", "/references", "引用集合必须为列表。", severity="error")
        for i, reference in enumerate(b["references"]):
            if not isinstance(reference, dict) or reference.get("resolution_status") not in ("resolved", "source_empty"):
                self.fail("source_not_closed", f"/references/{i}", "引用仍未唯一解析。")
        params = b["param_list"]
        if not isinstance(params, list) or len(params) != 8:
            self.fail("unsupported_parameter_slots", "/param_list", "当前装备档案要求原始8槽参数。")
        for i, value in enumerate(params):
            self.scalar(value, f"/param_list/{i}")
        if not isinstance(b["affix"], dict):
            self.fail("invalid_shape", "/affix", "affix来源必须为对象。", severity="error")
        self.evidence(b["affix"], "/affix", "record", definition=False)
        record = b["affix"].get("record", {})
        self.fields(record, ("affixId", "id", "paramList", "addProps", "openConfig", "descTextMapHash", "nameTextMapHash"), ("level",), "/affix/record")
        self.exact(record["addProps"], [{}, {}, {}], "/affix/record/addProps")
        self.text(record["openConfig"], "/affix/record/openConfig")
        if record.get("affixId") != source["affix_id"] or record.get("id") != source["affix_group_id"] or record.get("level", 0) != source["source_level"] or record.get("paramList") != params:
            self.fail("source_identity_mismatch", "/affix/record", "affix原始记录与装配身份/参数不一致。", severity="error")
        if not isinstance(b["abilities"], list) or len(b["abilities"]) != 1:
            self.fail("unsupported_ability_graph", "/abilities", "首期要求一个完整能力，不能裁剪多能力源。")
        entry, talent = b["abilities"][0], b["talent"]
        self.evidence(entry, "/abilities/0", "payload")
        self.evidence(talent, "/talent", "operations")
        if entry.get("variant_key", "Default") != "Default":
            self.fail("unsupported_variant", "/abilities/0/variant_key", "首期未验证其它能力变体。")
        expected_pointer = "/" + record["openConfig"].replace("~", "~0").replace("/", "~1")
        self.exact(talent["json_pointer"], expected_pointer, "/talent/json_pointer")
        body, body_path = entry["payload"], "/abilities/0/payload"
        self.fields(body, ("abilityName", "abilitySpecials", "isDynamicAbility", "LKLPDIMKLKI", "abilityMixins"), ("modifiers",), body_path)
        self.exact(body["isDynamicAbility"], True, body_path + "/isDynamicAbility",
                   "固定装备与参数窗口中不执行额外动态装配；true标记的完整原生含义尚未验证。")
        self.defaults(body["LKLPDIMKLKI"], body_path + "/LKLPDIMKLKI")
        ability_name = self.text(body["abilityName"], body_path + "/abilityName")
        specials = body["abilitySpecials"]
        if not isinstance(specials, dict) or not specials:
            self.fail("unsupported_parameters", body_path + "/abilitySpecials", "缺少显式能力默认参数。")
        for key, value in specials.items():
            self.exact(value, 0, body_path + "/abilitySpecials/" + _escape(key))
        operations = talent["operations"]
        if not isinstance(operations, list) or len(operations) != len(specials) + 1:
            self.fail("unsupported_parameter_assembly", "/talent/operations", "仅支持单AddAbility及每变量恰好一个修改。")
        for i, operation in enumerate(operations):
            path = f"/talent/operations/{i}"
            fields = ("$type", "CLGIKOPIKBC", "abilityName") + (() if i == 0 else ("paramDelta", "paramSpecial"))
            self.fields(operation, fields, (), path)
            self.exact(operation["$type"], "AddAbility" if i == 0 else "ModifyAbility", path + "/$type")
            self.exact(operation["CLGIKOPIKBC"], True, path + "/CLGIKOPIKBC",
                       "true开关在固定单人普通装备装配场景不改变已声明参数；一般语义未验证。")
            self.exact(operation["abilityName"], ability_name, path + "/abilityName")
            if i:
                key = operation["paramSpecial"]
                if not isinstance(key, str) or key not in specials or key in self.parameters:
                    self.fail("multiple_or_unknown_parameter_writer", path + "/paramSpecial", "参数未知或存在多写者。")
                token = operation["paramDelta"]
                if not isinstance(token, str) or not re.fullmatch(r"%[1-8]", token):
                    self.fail("unsupported_parameter_expression", path + "/paramDelta", "只支持原始%1到%8槽绑定。")
                index = int(token[1:]) - 1
                self.parameters[key] = specials[key] + params[index]
                self.parameter_sources[key] = {"operation_pointer": talent["json_pointer"] + f"/{i}",
                                              "parameter_slot": index, "source": b["affix"].get("source_path")}
                self.usage.append({"path": path + "/paramDelta", "classification": "consumed", "value": token})
                self.usage.append({"path": path + "/paramSpecial", "classification": "consumed", "value": key})
        if set(self.parameters) != set(specials):
            self.fail("unresolved_param", body_path + "/abilitySpecials", "能力参数未全部装配。")
        mixins = body["abilityMixins"]
        if not isinstance(mixins, list) or len(mixins) != 1 or not isinstance(mixins[0], dict):
            self.fail("unsupported_mixin_graph", body_path + "/abilityMixins", "首期只接受完整单Mixin结构。")
        mp = body_path + "/abilityMixins/0"
        if mixins[0].get("$type") == "ModifyDamageMixin":
            if "modifiers" in body:
                self.fail("unsupported_modifier_graph", body_path + "/modifiers", "静态来源包含额外modifier，不能截断。")
            self.static_mixin(mixins[0], mp)
        elif mixins[0].get("$type") == "OnAvatarUseSkillMixin":
            self.dynamic_mixin(body, mixins[0], mp, body_path)
        else:
            self.fail("unsupported_operation", mp + "/$type", "能力Mixin尚无完整适配。", actual=mixins[0].get("$type"))
        used = {v["parameter"] for v in self.usage if "parameter" in v}
        if used != set(self.parameters):
            self.fail("unused_parameter", body_path + "/abilitySpecials", "已选来源有未消费参数，不按整source通过。")

    def scenario_check(self, assumption_fp):
        schema = compiler_caps()["scenario_schema"]
        self.fields(self.scenario, schema["required"], (), "/scenario")
        self.exact(self.scenario["profile_id"], PROFILE_ID, "/scenario/profile_id")
        self.exact(self.scenario["profile_version"], PROFILE_VERSION, "/scenario/profile_version")
        for key in _SCENARIO_FLAGS:
            self.exact(self.scenario[key], True, "/scenario/" + key)
        distance = self.scalar(self.scenario["max_party_distance"], "/scenario/max_party_distance")
        if not 0 <= distance <= 40:
            self.fail("needs_scene_input", "/scenario/max_party_distance", "首期要求确定全队距离不超过40单位。")
        if self.scenario["static_policy"] not in ("execute", "included"):
            self.fail("unsupported_static_policy", "/scenario/static_policy", "静态政策只能为execute或included。")
        if self.kind == "static_damage" and self.scenario["static_policy"] != "execute":
            self.fail("static_coverage_unrepresentable", "/scenario/static_policy", "现有面板无法结构化承载分类增伤；仅显式execute（此来源未计入）可计算。")
        if self.scenario["accepted_assumptions_fingerprint"] != assumption_fp:
            self.fail("semantic_profile_acceptance_required", "/scenario/accepted_assumptions_fingerprint",
                      "须明确采用当前profile及完整假设集合；旧确认不能沿用。", required_fingerprint=assumption_fp)


def compile_source(bundle, owner, source_id, scenario, period):
    """Compile one complete source; pending/invalid never exposes active parts."""
    c = _Compiler(bundle, owner, source_id, scenario, period)
    out = {"status": "pending", "execution_status": "pending", "gameplay_semantics_status": "unverified",
           "issues": c.issues, "rules": [], "initial_rules": [], "static_parts": [], "parts": [],
           "semantic_assumptions": c.assumptions, "provenance": {}, "coverage_certificate": {},
           "required_scenario": [], "unique_keys": [], "actual_bundle_fingerprint": None}
    normalization = None
    try:
        # Reject non-finite/foreign values before hashing, and cap payload size.
        encoded = json.dumps(bundle, ensure_ascii=False, allow_nan=False)
        out["actual_bundle_fingerprint"] = _hash(bundle)
        if len(encoded.encode()) > 2 * 1024 * 1024:
            c.fail("bundle_too_large", "/", "装配包超过2MiB编译上限。", severity="error")
        source = bundle.get("source", {}) if isinstance(bundle, dict) else {}
        commit = source.get("upstream_commit") if isinstance(source, dict) else None
        expected_profile = for_commit(commit) if isinstance(commit, str) else None
        if expected_profile is not None or isinstance(bundle, dict) and "normalization" in bundle:
            try:
                normalization = verify_receipt(bundle.get("normalization"), commit=commit)
                view = copy.deepcopy(bundle)
                talent = view.get("talent")
                if isinstance(talent, dict) and "operations" in talent:
                    talent["operations"] = normalization.normalize(talent["operations"], kind="talent_config")
                for entry in view.get("abilities", []):
                    if isinstance(entry, dict) and "payload" in entry:
                        entry["payload"] = normalization.normalize(entry["payload"], kind="ability")
                c.bundle = view
            except (ValueError, TypeError, KeyError) as exc:
                c.fail("invalid_normalization", "/normalization", "字段映射凭据或原文结构无效：" + str(exc), severity="error")
        c.build()
    except _Stop:
        pass
    except (TypeError, ValueError, KeyError, RecursionError) as exc:
        c.issues.append({"code": "invalid_bundle", "path": "/", "message": "装配包非法：" + str(exc), "severity": "error"})
    def locate(row):
        path = row.get("path", "")
        candidates = []
        if isinstance(bundle, dict):
            for key, prefix in (("talent", "/talent/operations"), ("affix", "/affix/record")):
                if isinstance(bundle.get(key), dict):
                    candidates.append((prefix, bundle[key]))
            if isinstance(bundle.get("abilities"), list):
                candidates.extend((f"/abilities/{i}/payload", entry) for i, entry in enumerate(bundle["abilities"])
                                  if isinstance(entry, dict))
        for prefix, entry in candidates:
            if path == prefix or path.startswith(prefix + "/"):
                row["source_path"] = entry.get("source_path")
                relative = path[len(prefix):]
                if normalization and prefix != "/affix/record":
                    kind = "talent_config" if prefix == "/talent/operations" else "ability"
                    key = "operations" if kind == "talent_config" else "payload"
                    try:
                        relative = normalization.raw_pointer(entry[key], relative, kind=kind)
                    except (ValueError, TypeError, KeyError, IndexError):
                        # Missing-field diagnostics can name a canonical field that has no
                        # raw node. Do not present that diagnostic path as source evidence.
                        row["source_pointer_status"] = "not_present"
                        break
                row["source_json_pointer"] = str(entry.get("json_pointer", "")) + relative
                break

    for row in [*c.assumptions, *c.issues, *c.usage]:
        locate(row)
    # Acceptance covers the adapter and source-specific assumed pointers, not
    # ordinary parameters. Source/parameter changes still change execution hash.
    assumption_fp = _hash({"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION, "assumptions": c.assumptions})
    out["assumptions_fingerprint"] = assumption_fp
    if not c.issues:
        try:
            c.scenario_check(assumption_fp)
        except _Stop:
            pass
        except (TypeError, ValueError, KeyError) as exc:
            c.issues.append({"code": "invalid_scenario", "path": "/scenario", "message": str(exc), "severity": "error"})
    invalid = any(i.get("severity") == "error" for i in c.issues)
    status = "invalid" if invalid else "pending" if c.issues else "ready"
    out.update(status=status, execution_status=status,
               gameplay_semantics_status="assumed_in_scope" if status == "ready" else "unverified")
    source = bundle.get("source", {}) if isinstance(bundle, dict) else {}
    out["provenance"] = {"compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION, "source": copy.deepcopy(source),
        "content_fingerprint": bundle.get("content_fingerprint") if isinstance(bundle, dict) else None,
        "actual_bundle_fingerprint": out["actual_bundle_fingerprint"],
        "parameter_bindings": c.parameter_sources, "parameters": c.parameters,
        "field_usage": c.usage, "assumptions_fingerprint": assumption_fp,
        "source_mapping": {key: {k: bundle[key].get(k) for k in ("source_path", "json_pointer", "source_sha256")}
                           for key in ("talent", "affix") if isinstance(bundle, dict) and isinstance(bundle.get(key), dict)}}
    if normalization:
        out["provenance"]["normalization"] = normalization.receipt(source.get("upstream_commit"))
    if isinstance(bundle, dict) and isinstance(bundle.get("abilities"), list):
        out["provenance"]["ability_sources"] = [{k: a.get(k) for k in ("definition_id", "source_path", "json_pointer", "source_sha256")}
                                                for a in bundle["abilities"] if isinstance(a, dict)]
    out["coverage_certificate"] = {"kind": "compiler_scope_certificate", "human_reviewed": False,
        "source_complete": status == "ready", "requested_source_status": status,
        "interpreted_fields": sum(u["classification"] == "consumed" for u in c.usage),
        "assumed_fields": len(c.assumptions), "gameplay_verified": False}
    # Source identity excludes profile and user-defined aliases: changing an
    # adapter must never permit duplicate delivery of the same real source.
    if isinstance(source, dict):
        out["source_identity"] = {"provider": owner, "version_line": source.get("version_line"),
                                  "kind": source.get("kind"), "affix_id": source.get("affix_id"), "part_id": "upstream"}
    if status == "ready":
        out.update(rules=c.rules, initial_rules=c.initial, static_parts=c.static, parts=c.parts, unique_keys=c.unique_keys)
        out["execution_fingerprint"] = _hash({"bundle": bundle, "owner": owner, "source_id": source_id,
                                             "scenario": scenario, "period": period,
                                             "compiler_version": COMPILER_VERSION, "assumptions": assumption_fp})
    return copy.deepcopy(out)
