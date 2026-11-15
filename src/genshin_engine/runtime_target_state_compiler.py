"""Pure, commit-pinned classic target-state projection of a complete source.

The whole ability remains partial. Uninterpreted branches are retained; this
module is not a general game-script interpreter or an equipment-ID switch.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources

from genshin_engine.elemental_schema import REFERENCE_CONTRACT
from genshin_engine.runtime_compiler import _Compiler, _hash, _Stop
from genshin_runtime.normalization import Profile, verify_receipt

PROFILE_ID = "equipment-target-state-classic-v1"
PROFILE_VERSION = 2
COMPILER_VERSION = 1
TARGET_PROFILE = "freeze-reference-target-v1"
MAX_BYTES = 2 * 1024 * 1024


@lru_cache(maxsize=1)
def _profile():
    return json.loads(resources.files("genshin_runtime").joinpath("resources/target_state_profiles.json").read_text("utf8"))


def target_state_compiler_caps():
    defaults = {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION, "single_player_fixed": True,
        "single_target_monster": True, "target_state_profile": TARGET_PROFILE, "damage_scope": "classic_hits_only",
        "static_policy": "included", "conditional_crit_policy": "not_included", "status": "pending",
        "source": "待核对：固定装备及目标范围、常驻已含而条件暴率未含", "accepted_assumptions_fingerprint": None}
    properties = {key: {"const": value} for key, value in defaults.items() if key not in (
        "status", "source", "accepted_assumptions_fingerprint")}
    properties.update(status={"enum": ["assumed", "confirmed", "pending"]},
        source={"type": "string", "minLength": 1, "maxLength": 4096},
        accepted_assumptions_fingerprint={"type": ["string", "null"]})
    return {"compiler_version": COMPILER_VERSION, "bundle_version": 1, "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION, "part_id": "upstream", "source_id_format": "{binding_id}:upstream",
        "default_scenario": defaults, "scenario_schema": {"type": "object", "additionalProperties": False,
            "required": list(defaults), "properties": properties}, "max_bytes": MAX_BYTES,
        "supported": ["完整精确来源中的普通命中目标条件暴击率", "已有默认参数与单写者装配"],
        "unsupported": ["整能力执行", "Moon/Star分支", "冰共鸣", "先天Frozen或盾/免疫目标", "一般脚本与动态装备"],
        "whole_source_status": "partial", "execution_binding": {"kind": "persistent_equipment"},
        "evidence": copy.deepcopy(_profile()["semantics"]), "mapping_fingerprint": _hash(_profile())}


class _TargetCompiler(_Compiler):
    def __init__(self, bundle, owner, source_id, scenario):
        super().__init__(bundle, owner, source_id, scenario, None)
        self.branches, self.fixed, self.profile = [], [], _profile()
        self.normalization = None

    def inventory(self):
        """Retain bounded raw branches even when an admission guard rejects."""
        abilities = self.bundle.get("abilities", []) if isinstance(self.bundle, dict) else []
        if not isinstance(abilities, list) or len(abilities) > 16:
            return
        for ai, entry in enumerate(abilities):
            body = entry.get("payload") if isinstance(entry, dict) else None
            modifiers = body.get("GPPBBDEICKP") if isinstance(body, dict) else None
            if not isinstance(modifiers, dict) or len(modifiers) > 16:
                continue
            for name, modifier in modifiers.items():
                mixins = modifier.get("LFIJIMIMBIH") if isinstance(modifier, dict) else None
                if not isinstance(mixins, list) or len(mixins) > 64:
                    continue
                for index, raw in enumerate(mixins):
                    pointer = str(entry.get("json_pointer", "")) + "/GPPBBDEICKP/" + str(name).replace("~", "~0").replace("/", "~1") + f"/LFIJIMIMBIH/{index}"
                    self.branches.append({"ability_index": ai, "modifier": name, "index": index,
                        "source_path": entry.get("source_path"), "source_sha256": entry.get("source_sha256"),
                        "json_pointer": pointer, "node_sha256": _hash(raw), "raw": copy.deepcopy(raw),
                        "raw_type": raw.get("$type") if isinstance(raw, dict) else None,
                        "status": "unsupported", "execution_status": "pending"})

    def admitted(self, entry, prefix, payload_key, path):
        self.evidence(entry, path, payload_key, definition=prefix != "affix")
        expected = self.profile["target"]
        source_path = expected[prefix + "_source_path"]
        self.exact(entry["source_path"], source_path, path + "/source_path")
        source = next(row for row in self.profile["files"] if row["source_path"] == source_path)
        # Both raw byte versions have independently been verified; retain which
        # one the assembled DB actually carries, never conflate their hashes.
        if entry["source_sha256"] not in (source["workspace_source_sha256"], source["git_blob_sha256"]):
            self.fail("source_changed", path, "原始文件SHA不在本片准入证据中，需重新审核。")
        actual = _hash(entry[payload_key])
        choices = expected["affixes"] if prefix == "affix" else [{"pointer": expected[prefix + "_pointer"],
                                                                   "node_sha256": expected[prefix + "_node_sha256"]}]
        if not any(actual == row["node_sha256"] and entry["json_pointer"] == row["pointer"] for row in choices):
            self.fail("unsupported_source_node", path, "完整来源节点已变化或未经本片准入，不能裁剪后继续。")
        self.usage.append({"path": path, "classification": "admitted_complete_node", "node_sha256": actual,
            "source_path": entry["source_path"], "source_sha256": entry["source_sha256"], "json_pointer": entry["json_pointer"]})

    def predicate(self, obj, path):
        kind = obj.get("$type") if isinstance(obj, dict) else None
        if kind == "ByHasElement":
            self.fields(obj, ("$type", "target", "element"), (), path)
            self.exact(obj["target"], "Target", path + "/target")
            if obj["element"] not in ("Ice", "Frozen"):
                self.fail("unsupported_target_element", path, "本片只证明Ice/Frozen元素谓词。")
            return {"kind": "target_aura_in", "elements": [{"Ice": "cryo", "Frozen": "frozen"}[obj["element"]]]}
        if kind == "ByHasAbilityState":
            self.fields(obj, ("$type", "target", "abilityState"), (), path)
            self.exact(obj["target"], "Target", path + "/target")
            self.exact(obj["abilityState"], "ElementFreeze", path + "/abilityState")
            return {"kind": "target_status", "status_id": "element_freeze"}
        if kind == "ByAny":
            self.fields(obj, ("$type", "target", "predicates"), (), path)
            self.exact(obj["target"], "Target", path + "/target")
            if not isinstance(obj["predicates"], list) or len(obj["predicates"]) != 2:
                self.fail("unsupported_predicate", path, "只接已准入的两叶any条件。")
            return {"kind": "any", "children": [self.predicate(child, f"{path}/predicates/{i}") for i, child in enumerate(obj["predicates"])]}
        self.fail("unsupported_predicate", path, "目标谓词不在已审定子集中。")

    def build(self):
        b = self.bundle
        required = ("bundle_version", "status", "issues", "source", "snapshot", "param_list", "affix", "talent",
                    "abilities", "references", "reference_closed", "content_fingerprint", "normalization")
        self.fields(b, required, ("talent_candidates", "execution_support", "closure_fingerprint"), "/")
        if b["bundle_version"] != 1 or type(b["bundle_version"]) is not int:
            self.fail("unsupported_bundle", "/bundle_version", "需要完整装配版本1。", severity="error")
        if b["status"] != "ready" or b["issues"] or b["reference_closed"] is not True or b.get("talent_candidates"):
            self.fail("source_not_closed", "/", "数据库完整装配尚未闭合。")
        if b["content_fingerprint"] != _hash({k: v for k, v in b.items() if k != "content_fingerprint"}):
            self.fail("bundle_fingerprint_mismatch", "/content_fingerprint", "装配包指纹不符。", severity="error")
        self.text(self.owner, "/owner")
        self.text(self.source_id, "/source_id")
        if not self.source_id.endswith(":upstream") or len(self.source_id) > 512 or len(self.owner) > 512:
            self.fail("invalid_source_id", "/source_id", "需要有限长度binding_id:upstream及owner。", severity="error")
        source = b["source"]
        self.fields(source, ("kind", "affix_id", "affix_group_id", "source_level", "version_line", "snapshot_id", "upstream_commit"), (), "/source")
        self.exact(source["kind"], "equip_affix", "/source/kind")
        if source["upstream_commit"] != self.profile["commit"]:
            self.fail("unsupported_commit", "/source/upstream_commit", "当前提交没有本片的独立映射证据。")
        for key in ("snapshot_id", "upstream_commit", "version_line"):
            self.text(source[key], "/source/" + key)
            if b["snapshot"].get(key) != source[key]:
                self.fail("mixed_snapshot", "/snapshot/" + key, "来源快照不一致。", severity="error")
        if source["version_line"] not in ("release", "beta"):
            self.fail("invalid_version_line", "/source/version_line", "未知资料线。", severity="error")
        for key in ("affix_id", "affix_group_id", "source_level"):
            if type(source[key]) is not int or source[key] < (0 if key == "source_level" else 1):
                self.fail("invalid_identity", "/source/" + key, "来源身份非法。", severity="error")
        if not isinstance(b["references"], list) or len(b["references"]) > 256 or any(
                not isinstance(r, dict) or r.get("resolution_status") not in ("resolved", "source_empty") for r in b["references"]):
            self.fail("source_not_closed", "/references", "引用未闭合或超过预算。")
        base = verify_receipt(b["normalization"], commit=source["upstream_commit"])
        self.normalization = Profile(PROFILE_ID, (self.profile["commit"],),
            {**base.fields, **self.profile["fields"]}, {**base.types, **self.profile["types"]})
        if not isinstance(b["abilities"], list) or len(b["abilities"]) != 1:
            self.fail("unsupported_ability_graph", "/abilities", "要求一个完整来源能力，不能截去额外节点。")
        entry, talent, affix = b["abilities"][0], b["talent"], b["affix"]
        self.admitted(entry, "ability", "payload", "/abilities/0")
        self.admitted(talent, "talent", "operations", "/talent")
        self.admitted(affix, "affix", "record", "/affix")
        if entry.get("variant_key") != "Default":
            self.fail("unsupported_variant", "/abilities/0/variant_key", "只接已审定Default节点。")
        record, params = affix["record"], b["param_list"]
        if (not isinstance(params, list) or len(params) != 8 or params != record["paramList"]
                or record["affixId"] != source["affix_id"] or record["id"] != source["affix_group_id"]
                or record.get("level", 0) != source["source_level"]):
            self.fail("source_identity_mismatch", "/affix", "参数、等级或身份与原始记录不符。", severity="error")
        for i, value in enumerate(params):
            self.scalar(value, f"/param_list/{i}")
        body = self.normalization.normalize(entry["payload"], kind="ability")
        operations = self.normalization.normalize(talent["operations"], kind="talent_config")
        self.fields(body, ("abilityName", "abilitySpecials", "onAdded", "modifiers", "LKLPDIMKLKI"), (), "/ability")
        if record["openConfig"] != body["abilityName"] or talent["json_pointer"] != "/" + record["openConfig"]:
            self.fail("source_identity_mismatch", "/talent", "完整来源身份引用不一致。", severity="error")
        self.defaults(body["LKLPDIMKLKI"], "/ability/LKLPDIMKLKI")
        specials = body["abilitySpecials"]
        if not isinstance(specials, dict) or len(specials) != 2 or not isinstance(operations, list) or len(operations) != 3:
            self.fail("unsupported_parameter_assembly", "/talent", "只接完整两参数单写者装配。")
        for key, value in specials.items():
            self.exact(value, 0, "/ability/abilitySpecials/" + key)
        for i, operation in enumerate(operations):
            path = f"/talent/operations/{i}"
            self.fields(operation, ("$type", "CLGIKOPIKBC", "abilityName") + (() if i == 0 else ("paramDelta", "paramSpecial")), (), path)
            self.exact(operation["$type"], "AddAbility" if i == 0 else "ModifyAbility", path + "/$type")
            self.exact(operation["abilityName"], body["abilityName"], path + "/abilityName")
            self.exact(operation["CLGIKOPIKBC"], True, path + "/CLGIKOPIKBC", "固定场景参数装配开关隔离假设，不解释一般游戏默认。")
            if i:
                key, token = operation["paramSpecial"], operation["paramDelta"]
                if key not in specials or key in self.parameters or not isinstance(token, str) or not re.fullmatch(r"%[1-8]", token):
                    self.fail("unresolved_parameter", path, "参数槽位/写者不唯一或不支持表达式。")
                index = int(token[1:]) - 1
                self.parameters[key] = params[index]
                self.parameter_sources[key] = {"param_index": index, "operation_pointer": talent["json_pointer"] + f"/{i}",
                    "parameter_pointer": affix["json_pointer"] + f"/paramList/{index}"}
        if set(specials) != set(self.parameters):
            self.fail("unused_parameter", "/talent", "参数未完整装配。")
        if not isinstance(body["onAdded"], list) or len(body["onAdded"]) != 1:
            self.fail("unsupported_attach", "/ability/onAdded", "只接单一装备持久来源附加。")
        attach = body["onAdded"][0]
        self.fields(attach, ("$type", "actorIceSubHurtDelta", "doOffStage", "modifierName", "canBeHandledOnRecover"), (), "/ability/onAdded/0")
        for key, value in (("$type", "AttachModifier"), ("actorIceSubHurtDelta", -1), ("doOffStage", False), ("canBeHandledOnRecover", True)):
            self.exact(attach[key], value, "/ability/onAdded/0/" + key,
                       None if key == "$type" else "固定装备场景的精确哨兵/状态隔离，不推断一般AttachModifier默认。")
        name = attach["modifierName"]
        if not isinstance(body["modifiers"], dict) or set(body["modifiers"]) != {name}:
            self.fail("unsupported_modifier_graph", "/ability/modifiers", "必须保留唯一完整modifier。")
        modifier = body["modifiers"][name]
        defaults = {k: v for k, v in modifier.items() if k not in ("modifierMixins", "isUnique")}
        self.defaults(defaults, "/ability/modifiers/" + name)
        self.exact(modifier.get("isUnique"), True, "/ability/modifiers/isUnique")
        mixins = modifier.get("modifierMixins")
        if not isinstance(mixins, list) or len(mixins) != 4:
            self.fail("unsupported_branch_graph", "/ability/modifiers", "必须保留四条来源分支。")
        effects = []
        for i, mixin in enumerate(mixins):
            pointer = "/modifiers/" + name + f"/modifierMixins/{i}"
            raw_pointer = self.normalization.raw_pointer(entry["payload"], pointer, kind="ability")
            raw = entry["payload"]["GPPBBDEICKP"][name]["LFIJIMIMBIH"][i]
            branch = next(row for row in self.branches if row["ability_index"] == 0 and row["modifier"] == name and row["index"] == i)
            self.exact(branch["json_pointer"], entry["json_pointer"] + raw_pointer, pointer)
            self.exact(branch["node_sha256"], _hash(raw), pointer)
            if i >= 2:
                if mixin.get("$type") not in self.profile["unknown_branch_types"]:
                    self.fail("unknown_branch", pointer, "未知新增分支不能沿用排除证书。")
                branch.update(status="excluded_by_scope", execution_status="unsupported", damage_scope="Moon/Star",
                    reason="类型语义未恢复；本片仅classic命中，保留完整分支但不执行。", attack_tags=copy.deepcopy(mixin.get("attackTags", [])))
                continue
            self.fields(mixin, ("$type", "actorIceSubHurtDelta", "bonusCritical", "predicates", "ignoreEventInfo"), (), pointer)
            self.exact(mixin["$type"], "ModifyDamageMixin", pointer + "/$type")
            self.exact(mixin["actorIceSubHurtDelta"], -1, pointer + "/actorIceSubHurtDelta", "哨兵不在本片参与数值运算。")
            self.exact(mixin["ignoreEventInfo"], True, pointer + "/ignoreEventInfo", "持久来源按历史证据逐击检查目标状态，非完整原生字段语义。")
            predicates = mixin["predicates"]
            if not isinstance(predicates, list) or len(predicates) != 2:
                self.fail("unsupported_predicate", pointer, "只接Monster与一项状态树的完整组合。")
            self.predicate_monster(predicates[0], pointer + "/predicates/0")
            value = self.param(mixin["bonusCritical"], pointer + "/bonusCritical")
            if not 0 <= value <= 1:
                self.fail("unsupported_crit_value", pointer, "条件暴率必须在0..1内。")
            effect_id = f"conditional-crit-{i+1}"
            effects.append({"id": effect_id, "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": value,
                "filter": {"event": "hit", "phase": "pre"}, "target_predicate": self.predicate(predicates[1], pointer + "/predicates/1")})
            branch.update(status="projected", execution_status="ready", effect_id=effect_id, value=value)
        fixed_id = "upstream." + _hash(self.source_id)[:20] + ".target-state"
        self.fixed.append({"id": fixed_id, "name_zh": "装备目标状态条件暴击率（经典命中）", "owner": self.owner,
            "source_id": self.source_id, "availability": "fixed", "damage_scope": "classic_hits_only",
            "target_state_profile": TARGET_PROFILE, "effects": effects, "status": "assumed",
            "source": "完整数据库来源的局部映射与已接受历史证据；整能力partial，目标状态仍为实验参考。"})
        self.unique_keys.append(name)


def compile_target_state_source(bundle, *, owner, source_id, scenario):
    """Return a bounded materialized projection, with no DB access or mutation."""
    c = _TargetCompiler(bundle, owner, source_id, scenario)
    input_materializable = False
    out = {"status": "pending", "execution_status": "pending", "projection_status": "pending", "whole_source_status": "partial",
        "rules": [], "initial_rules": [], "static_parts": [], "fixed_effect_sources": [], "parts": [], "unique_keys": [],
        "branches": c.branches, "issues": c.issues, "semantic_assumptions": c.assumptions, "actual_bundle_fingerprint": None,
        "execution_binding": {"kind": "persistent_equipment", "target_state_profile": TARGET_PROFILE}}
    try:
        if len(json.dumps([bundle, owner, source_id, scenario], ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
            c.fail("bundle_too_large", "/", "装配包及场景参数超过2MiB上限。", severity="error")
        input_materializable = True
        out["actual_bundle_fingerprint"] = _hash(bundle)
        c.inventory()
        c.build()
    except _Stop:
        pass
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        c.issues.append({"code": "invalid_bundle", "path": "/", "severity": "error", "message": str(exc)[:1024]})
    scope = {k: v for k, v in scenario.items() if k != "accepted_assumptions_fingerprint"} if isinstance(scenario, dict) else None
    reference_fp = REFERENCE_CONTRACT["contract_fingerprint"]
    try:
        assumption_fp = _hash({"compiler": COMPILER_VERSION, "profile": _hash(_profile()), "bundle": out["actual_bundle_fingerprint"],
            "owner": owner, "source_id": source_id, "scenario": scope, "assumptions": c.assumptions,
            "target_reference_fingerprint": reference_fp})
    except (ValueError, TypeError, RecursionError):
        c.issues.append({"code": "invalid_context", "path": "/scenario", "severity": "error", "message": "场景/身份必须为有限JSON。"})
        assumption_fp = _hash({"invalid_context": True, "compiler": COMPILER_VERSION, "target_reference_fingerprint": reference_fp})
    out.update(assumptions_fingerprint=assumption_fp, required_confirmation={"accepted_assumptions_fingerprint": assumption_fp})
    if not c.issues:
        try:
            caps = target_state_compiler_caps()
            c.fields(scenario, caps["scenario_schema"]["required"], (), "/scenario")
            for key, rule in caps["scenario_schema"]["properties"].items():
                if "const" in rule:
                    c.exact(scenario[key], rule["const"], "/scenario/" + key)
            if scenario["status"] not in ("assumed", "confirmed", "pending"):
                c.fail("invalid_scenario", "/scenario/status", "用途依据状态非法。", severity="error")
            if not isinstance(scenario["source"], str) or not scenario["source"].strip() or len(scenario["source"]) > 4096:
                c.fail("scenario_evidence_required", "/scenario/source", "需要有限非空用途依据。")
            if scenario["status"] == "pending":
                c.fail("scenario_evidence_pending", "/scenario/status", "采用来源指纹不自动确认场景用途。")
            if scenario["accepted_assumptions_fingerprint"] != assumption_fp:
                c.fail("semantic_profile_acceptance_required", "/scenario/accepted_assumptions_fingerprint", "请确认当前来源、映射、未知分支隔离与历史参考假设。", required_fingerprint=assumption_fp)
        except _Stop:
            pass
        except (ValueError, TypeError, KeyError) as exc:
            c.issues.append({"code": "invalid_scenario", "path": "/scenario", "severity": "error", "message": str(exc)[:1024]})
    status = "invalid" if any(i.get("severity") == "error" for i in c.issues) else "pending" if c.issues else "ready"
    out.update(status=status, execution_status=status, projection_status=status,
        gameplay_semantics_status="assumed_in_scope" if status == "ready" else "unverified",
        coverage_certificate={"kind": "target_state_projection_certificate", "source_complete": False, "human_reviewed": False,
            "projection_complete": status == "ready", "projection_status": status, "damage_scope": "classic_hits_only", "gameplay_verified": False})
    source = bundle.get("source", {}) if input_materializable and isinstance(bundle, dict) else {}
    if not isinstance(source, dict):
        source = {}
    out["source_identity"] = {"provider": owner if isinstance(owner, str) and len(owner) <= 512 else None, "version_line": source.get("version_line"), "kind": source.get("kind"),
                              "affix_id": source.get("affix_id"), "part_id": "upstream"}
    out["provenance"] = {"compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
        "source": copy.deepcopy(source), "actual_bundle_fingerprint": out["actual_bundle_fingerprint"], "parameters": c.parameters,
        "parameter_bindings": c.parameter_sources, "field_usage": c.usage, "assumptions_fingerprint": assumption_fp,
        "local_mapping_fingerprint": _hash(_profile()), "mapping_evidence": copy.deepcopy(_profile()["admission"]),
        "source_byte_evidence": copy.deepcopy(_profile()["files"]), "semantics": copy.deepcopy(_profile()["semantics"]),
        "target_reference_fingerprint": reference_fp}
    if status == "ready":
        c.fixed[0]["status"] = scenario["status"]
        c.fixed[0]["source"] = scenario["source"]
        part = c.part({"kind": "fixed_effect_source", "fixed_source_id": c.fixed[0]["id"]},
                      {"kind": "fixed_effect_source"}, "上游目标状态条件暴击率（经典命中投影）")
        part["conditions"][0]["evidence"][0].update(profile_id=PROFILE_ID, profile_version=PROFILE_VERSION)
        out.update(parts=[part], fixed_effect_sources=c.fixed, unique_keys=c.unique_keys,
                   execution_fingerprint=_hash({"bundle": bundle, "owner": owner, "source_id": source_id,
                       "scenario": scenario, "compiler_version": COMPILER_VERSION, "mapping": _hash(_profile())}))
    return copy.deepcopy(out)
