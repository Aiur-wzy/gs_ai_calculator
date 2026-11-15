"""A bounded equipment projection into existing reaction rules, not a VM."""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources

from genshin_runtime.normalization import Profile, verify_receipt

from .elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT
from .runtime_compiler import _Compiler, _escape, _hash, _Stop

PROFILE_ID = "equipment-ordinary-swirl-benefits-v1"
PROFILE_VERSION = 2
COMPILER_VERSION = 1
MAX_BYTES = 2 * 1024 * 1024
_ELEMENTS = {"Fire": "pyro", "Water": "hydro", "Electric": "electro", "Ice": "cryo"}
_PROPERTIES = {"Fire": "Actor_FireSubHurtDelta", "Water": "Actor_WaterSubHurtDelta",
               "Electric": "Actor_ElecSubHurtDelta", "Ice": "Actor_IceSubHurtDelta"}
_BONUSES = {"Actor_ElemReactSwirl" + element + "Delta" for element in _ELEMENTS}
_STAR = {"Actor_ElemReactStarSwirlIceDelta", "Actor_ElemReactStarSwirlWindDelta"}


@lru_cache(maxsize=1)
def _profile():
    return json.loads(resources.files("genshin_runtime").joinpath("resources/reaction_benefit_profiles.json").read_text("utf8"))


def reaction_benefit_compiler_caps():
    defaults = {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION, "single_player_fixed": True,
        "reaction_scope": "ordinary_hydro_cryo_original_target", "target_state_profile": "freeze-reference-target-v1",
        "target_id": None, "origin_target_range": "unknown", "simulation_clock": "no_hitlag",
        "initial_source_effects": "unknown", "event_order_policy": "unknown", "static_policy": "included",
        "reaction_bonus_policy": "not_included", "status": "pending",
        "source": "待核对原目标范围、初态及已校准原子事件顺序；普通反应增益未重复计入",
        "accepted_assumptions_fingerprint": None}
    variable = {"target_id", "origin_target_range", "initial_source_effects", "event_order_policy",
                "status", "source", "accepted_assumptions_fingerprint"}
    properties = {k: {"const": v} for k, v in defaults.items() if k not in variable}
    properties.update(target_id={"type": ["string", "null"], "minLength": 1, "maxLength": 128},
        origin_target_range={"enum": ["unknown", "within_source_range"]}, initial_source_effects={"enum": ["unknown", "none"]},
        event_order_policy={"enum": ["unknown", "declared_atomic_hit_order"]}, status={"enum": ["pending", "assumed", "confirmed"]},
        source={"type": "string", "minLength": 1, "maxLength": 4096},
        accepted_assumptions_fingerprint={"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"})
    return {"compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
        "bundle_version": 1, "default_scenario": defaults,
        "scenario_schema": {"type": "object", "required": list(defaults), "additionalProperties": False, "properties": properties},
        "source_id_format": "{binding_id}:upstream", "part_ids": ["upstream.swirl_bonus", "upstream.shred.hydro", "upstream.shred.cryo"],
        "max_bytes": MAX_BYTES, "max_input_nodes": 16384, "max_input_depth": 64,
        "max_generated_parts": 3, "max_generated_rules": 2,
        "confirmable_issue_codes": ["semantic_profile_acceptance_required", "scenario_evidence_pending"],
        "scene_input_issue_codes": ["scene_input_required", "scenario_evidence_required"],
        "confirmation_policy": "未知范围、初态、顺序或空证据可以保存草稿，但不允许只确认来源将其提升为就绪。",
        "whole_source_status": "partial", "execution_binding": {"kind": "elemental_reaction"},
        "supported": ["普通冰水原目标反应触发减抗", "提供者前后台均保留常驻扩散加成", "同元素单层续期，水冰独立"],
        "unsupported": ["星烁/火雷/传播分支", "原生同时多直伤包", "未知距离或初态", "多提供者唯一效果争抢", "角色精通转增伤"],
        "evidence": copy.deepcopy(_profile()["semantics"]), "mapping_fingerprint": _hash(_profile())}


def _walk(value, path=""):
    yield path, value
    children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, child in children:
        yield from _walk(child, path + "/" + _escape(key))


class _ReactionCompiler(_Compiler):
    def __init__(self, bundle, owner, source_id, scenario, period):
        super().__init__(bundle, owner, source_id, scenario, period)
        self.profile, self.normalization = _profile(), None
        self.branches, self.raw_sources = [], []

    def admitted(self, entry, kind, payload_key):
        self.evidence(entry, "/" + kind, payload_key, definition=kind != "affix")
        expected = self.profile["admission"][kind]
        file = next(f for f in self.profile["files"] if f["path"] == expected["source_path"])
        if (entry["source_path"] != expected["source_path"] or entry["json_pointer"] != expected["json_pointer"]
                or entry["source_sha256"] not in (file["workspace_source_sha256"], file["git_blob_sha256"])
                or _hash(entry[payload_key]) != expected["node_sha256"]):
            self.fail("unsupported_source_node", "/" + kind, "完整原节点、原指针或源字节未被此局部映射准入。")
        if "node_sha256" in entry and entry["node_sha256"] != expected["node_sha256"]:
            self.fail("inconsistent_node_hash", "/" + kind, "原文与提供的节点SHA不一致。", severity="error")
        self.usage.append({"path": "/" + kind, "classification": "admitted_complete_node", **expected})

    def branch(self, entry, body, semantic_path, kind, **detail):
        pointer = self.normalization.raw_pointer(body, semantic_path, kind="ability")
        raw = dict(_walk(body))[pointer]
        self.branches.append({"kind": kind, "source_path": entry["source_path"], "json_pointer": entry["json_pointer"] + pointer,
            "source_sha256": entry["source_sha256"], "node_sha256": _hash(raw), "raw": copy.deepcopy(raw), **detail})

    def references(self, b, body, operations):
        """Reconstruct all explicit local edges; a forged closed flag is insufficient."""
        snapshot, line = b["source"]["snapshot_id"], b["source"]["version_line"]
        entry, talent, affix = b["abilities"][0], b["talent"], b["affix"]
        aid = _hash([snapshot, entry["source_path"], entry["json_pointer"], "ability"])
        tid = _hash([snapshot, talent["source_path"], talent["json_pointer"], "talent_config"])
        if entry["definition_id"] != aid or talent["definition_id"] != tid:
            self.fail("definition_identity_mismatch", "/", "节点身份与当前快照原指针不符。", severity="error")
        modifier_ids = {name: _hash([snapshot, entry["source_path"], entry["json_pointer"] + self.normalization.raw_pointer(
            entry["payload"], "/modifiers/" + _escape(name), kind="ability"), "modifier"]) for name in body["modifiers"]}
        expected = {}
        for origin, raw, normalized, kind, definition_id in (
            (entry, entry["payload"], body, "ability", aid), (talent, talent["operations"], operations, "talent_config", tid),
            (affix, affix["record"], affix["record"], "excel_record", _hash([snapshot, affix["source_path"], affix["json_pointer"], "excel_record"]))):
            for pointer, node in _walk(normalized):
                if not isinstance(node, dict):
                    continue
                for field in ("openConfig", "abilityName", "modifierName"):
                    if field not in node or (field == "abilityName" and kind == "ability" and not pointer):
                        continue
                    value = node[field]
                    target = tid if field == "openConfig" else aid if field == "abilityName" else modifier_ids.get(value)
                    if target is None:
                        self.fail("missing_modifier", pointer, "原节点指向未知modifier。")
                    relative = pointer + "/" + field
                    if kind != "excel_record":
                        relative = self.normalization.raw_pointer(raw, relative, kind=kind)
                    source_id = definition_id
                    if kind == "ability" and pointer.startswith("/modifiers/"):
                        name = pointer.split("/")[2].replace("~1", "/").replace("~0", "~")
                        source_id = modifier_ids[name]
                    expected[(origin["source_path"], origin["json_pointer"] + relative)] = (source_id, target, value, field)
        actual = {}
        for row in b["references"]:
            if (not isinstance(row, dict) or row.get("resolution_status") != "resolved"
                    or row.get("snapshot_id") != snapshot or row.get("version_line") != line):
                self.fail("source_not_closed", "/references", "来源引用未解析或混用快照。")
            key = (row.get("source_path"), row.get("json_pointer"))
            if key in actual:
                self.fail("duplicate_reference", "/references", "来源引用原指针重复。")
            actual[key] = (row.get("from_definition_id"), row.get("resolved_definition_id"), row.get("target_key"), row.get("field_name"))
        if actual != expected:
            self.fail("reference_graph_mismatch", "/references", "完整来源引用清单与原文控制图不一致。")

    def build(self):
        b = self.bundle
        self.fields(b, ("bundle_version", "status", "issues", "source", "snapshot", "param_list", "affix", "talent",
            "abilities", "references", "reference_closed", "content_fingerprint", "normalization"),
            ("talent_candidates", "execution_support", "closure_fingerprint"), "/")
        self.exact(b["bundle_version"], 1, "/bundle_version")
        if b["status"] != "ready" or b["issues"] or b["reference_closed"] is not True or b.get("talent_candidates"):
            self.fail("source_not_closed", "/", "需要完整闭合的装备来源包。")
        if b["content_fingerprint"] != _hash({k: v for k, v in b.items() if k != "content_fingerprint"}):
            self.fail("bundle_fingerprint_mismatch", "/", "来源包指纹不符。", severity="error")
        for value, path in ((self.owner, "/owner"), (self.source_id, "/source_id")):
            self.text(value, path)
            if len(value) > 512:
                self.fail("identity_limit", path, "身份超出长度上限。", severity="error")
        if not self.source_id.endswith(":upstream"):
            self.fail("invalid_source_id", "/source_id", "需要binding_id:upstream。", severity="error")
        self.time(self.period, "/period")
        source = b["source"]
        self.fields(source, ("kind", "affix_id", "affix_group_id", "source_level", "version_line", "snapshot_id", "upstream_commit"), (), "/source")
        self.exact(source["kind"], "equip_affix", "/source/kind")
        self.exact(source["upstream_commit"], self.profile["commit"], "/source/upstream_commit")
        if not isinstance(b["snapshot"], dict) or any(b["snapshot"].get(k) != source[k] for k in ("snapshot_id", "version_line", "upstream_commit")):
            self.fail("mixed_snapshot", "/snapshot", "来源快照不一致。", severity="error")
        if source["version_line"] not in ("release", "beta") or not isinstance(source["snapshot_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["snapshot_id"]):
            self.fail("invalid_snapshot", "/source", "需要明确快照与资料线。", severity="error")
        for k in ("affix_id", "affix_group_id", "source_level"):
            if type(source[k]) is not int or source[k] < (0 if k == "source_level" else 1):
                self.fail("invalid_identity", "/source/" + k, "来源ID/等级必须是有效整数。", severity="error")
        if not isinstance(b["abilities"], list) or len(b["abilities"]) != 1 or not isinstance(b["references"], list) or len(b["references"]) > 256:
            self.fail("unsupported_graph", "/", "仅接完整单能力有界来源。")
        base = verify_receipt(b["normalization"], commit=source["upstream_commit"])
        if b["snapshot"].get("normalization") != b["normalization"]:
            self.fail("mixed_normalization", "/snapshot", "来源归一化身份不一致。", severity="error")
        self.normalization = Profile(PROFILE_ID, (self.profile["commit"],),
            {**base.fields, **self.profile["fields"]}, {**base.types, **self.profile["types"]})
        entry, talent, affix = b["abilities"][0], b["talent"], b["affix"]
        for node, kind, payload in ((entry, "ability", "payload"), (talent, "talent", "operations"), (affix, "affix", "record")):
            self.raw_sources.append({"kind": kind, **copy.deepcopy(node)})
            self.admitted(node, kind, payload)
        self.exact(entry.get("variant_key"), "Default", "/ability/variant_key")
        record, params = affix["record"], b["param_list"]
        if params != record["paramList"] or record["affixId"] != source["affix_id"] or record["id"] != source["affix_group_id"] or record.get("level", 0) != source["source_level"]:
            self.fail("source_identity_mismatch", "/affix", "装备身份、等级或参数与原记录不符。", severity="error")
        body = self.normalization.normalize(entry["payload"], kind="ability")
        operations = self.normalization.normalize(talent["operations"], kind="talent_config")
        if record["openConfig"] != body["abilityName"] or talent["json_pointer"] != "/" + _escape(record["openConfig"]):
            self.fail("source_identity_mismatch", "/talent", "装备到能力的精确入口不一致。")
        self.references(b, body, operations)
        self.parameters = copy.deepcopy(body["abilitySpecials"])
        self.parameter_sources = {name: {"kind": "ability_default", "value": value,
            "source_path": entry["source_path"], "json_pointer": entry["json_pointer"] + self.normalization.raw_pointer(
                entry["payload"], "/abilitySpecials/" + _escape(name), kind="ability")}
            for name, value in self.parameters.items()}
        writers = set()
        for i, op in enumerate(operations):
            fields = ("$type", "CLGIKOPIKBC", "abilityName") + (() if i == 0 else ("paramDelta", "paramSpecial"))
            self.fields(op, fields, (), f"/talent/{i}")
            self.exact(op["$type"], "AddAbility" if i == 0 else "ModifyAbility", f"/talent/{i}/$type")
            self.exact(op["abilityName"], body["abilityName"], f"/talent/{i}/abilityName")
            self.exact(op["CLGIKOPIKBC"], True, f"/talent/{i}/CLGIKOPIKBC", "精确已审装配开关；不解释一般原生取值。")
            if not i:
                continue
            token, name = op["paramDelta"], op["paramSpecial"]
            if name not in self.parameters or name in writers or self.parameters[name] != 0 or not re.fullmatch(r"%[1-8]", token):
                self.fail("unsupported_parameter_assembly", f"/talent/{i}", "只接受零初值单写者的精确参数槽。")
            index = int(token[1:]) - 1
            self.parameters[name] = self.scalar(params[index], f"/param_list/{index}")
            self.parameter_sources[name] = {"param_index": index, "operation_pointer": talent["json_pointer"] + f"/{i}", "parameter_pointer": affix["json_pointer"] + f"/paramList/{index}"}
            writers.add(name)
        if any(value != -1 for key, value in self.parameters.items() if key not in writers):
            self.fail("unresolved_parameter", "/abilitySpecials", "只允许未写入的精确-1乘法常量。")
        self.defaults(body["LKLPDIMKLKI"], "/ability/LKLPDIMKLKI")
        self.exact(body["isDynamicAbility"], True, "/ability/isDynamicAbility", "固定装备、参数、存活和队伍，不执行动态恢复或换装。")
        attach = body["onAdded"][0]
        handler_name = attach["modifierName"]
        handler = body["modifiers"][handler_name]
        hp = "/modifiers/" + _escape(handler_name)
        self.branch(entry, entry["payload"], "/onAdded/0", "attachment", status="projected", execution_status="ready", reason="固定装备安装；不是后台反应增伤的在场开关")
        self.assumptions.extend(copy.deepcopy([{"status": "unverified_game_semantics", "assumption": value} for value in self.profile["semantics"].values() if isinstance(value, str)]))
        bonus_values = []
        for key, expression in handler["properties"].items():
            value = self.param(expression, hp + "/properties/" + key)
            supported = key in _BONUSES
            if not supported and key not in _STAR:
                self.fail("unknown_property", hp, "未知反应属性不做忽略。")
            if supported:
                bonus_values.append(value)
            self.branch(entry, entry["payload"], hp + "/properties/" + key, "static_property",
                status="projected" if supported else "excluded_by_scope", execution_status="ready" if supported else "unsupported",
                value=value, reason="持有者常驻普通扩散增伤" if supported else "星烁属性不属于普通冰水范围")
        if len(bonus_values) != 4 or len(set(bonus_values)) != 1:
            self.fail("unsupported_static_bonus", hp, "普通扩散各元素必须为同一明确加成。")
        self.bonus = bonus_values[0]
        self.shreds = {}
        for i, listener in enumerate(handler["modifierMixins"]):
            path = hp + f"/modifierMixins/{i}"
            self.exact(listener["$type"], "DoActionByElementReactionMixin", path + "/$type")
            self.exact(listener["entityTypes"], ["Monster"], path + "/entityTypes")
            self.exact(listener["range"], 20, path + "/range", "场景声明原目标满足来源范围；不推测单位、不模拟空间。")
            reactions = listener["reactionTypes"]
            ordinary = [x for x in reactions if x.startswith("Swirl")]
            if len(ordinary) != 1 or ordinary[0][5:] not in _ELEMENTS:
                self.fail("unsupported_reaction", path, "未知反应枚举不能投影。")
            source_element = ordinary[0][5:]
            expected_reactions = [ordinary[0]] + (["StarSwirlIce"] if source_element == "Ice" else [])
            self.exact(reactions, expected_reactions, path + "/reactionTypes")
            action = listener["actions"][0]
            self.exact(action["$type"], "ApplyModifier", path + "/actions/0/$type")
            self.exact(action["target"], "Other", path + "/actions/0/target", "受限原目标反应语义；非通用Other解释器。")
            self.exact(action["doOffStage"], False, path + "/actions/0/doOffStage", "动态减抗只由在场提供者触发，已生效实例不因退场清除。")
            modifier_name = action["modifierName"]
            modifier = body["modifiers"][modifier_name]
            property_key = _PROPERTIES[source_element]
            self.exact(list(modifier["properties"]), [property_key], path + "/target_modifier/properties")
            expression = modifier["properties"][property_key]
            if not isinstance(expression, list) or len(expression) != 3 or expression[2] != "MUL":
                self.fail("unsupported_expression", path, "只接参数乘以精确负一的抗性增量。")
            self.exact(self.param(expression[1], path), -1, path)
            reduction = self.param(expression[0], path)
            duration = self.time(self.param(modifier["duration"], path + "/duration"), path + "/duration")
            if not 0 <= reduction <= 1:
                self.fail("unsupported_reduction", path, "本片减抗需在0..1。")
            element = _ELEMENTS[source_element]
            supported = element in ("hydro", "cryo")
            if supported:
                self.shreds[element] = (reduction, duration, path)
                self.unique_keys.append(modifier_name)
            self.branch(entry, entry["payload"], path, "reaction_listener", element=element,
                status="projected" if supported else "excluded_by_scope", execution_status="ready" if supported else "unsupported",
                excluded_reactions=[r for r in reactions if r != ordinary[0]], reason="普通分支投影；其他原枚举完整保留" if supported else "当前普通冰水模型不执行此元素")
            self.branch(entry, entry["payload"], "/modifiers/" + _escape(modifier_name), "target_modifier", element=element,
                status="projected" if supported else "excluded_by_scope", execution_status="ready" if supported else "unsupported",
                value=reduction, duration=duration, reason="正削减量适配负抗性属性；modifier耐久不作为GU")
        if set(self.shreds) != {"hydro", "cryo"}:
            self.fail("incomplete_projection", hp, "必须同时完整投影冰水分支。")
        self.branch(entry, entry["payload"], hp + "/onAttackLanded/0", "landed_damage_path",
            status="collapsed_original_target", execution_status="partial", reason="原目标同刻同值Unique刷新与反应路径幂等合并；传播/AoE不执行，未声称此路径无效")

    def scenario_check(self):
        schema = reaction_benefit_compiler_caps()["scenario_schema"]
        self.fields(self.scenario, schema["required"], (), "/scenario")
        for key, rule in schema["properties"].items():
            value = self.scenario[key]
            if "const" in rule:
                self.exact(value, rule["const"], "/scenario/" + key)
            elif "enum" in rule and value not in rule["enum"]:
                self.fail("invalid_scenario", "/scenario/" + key, "非法场景枚举。", severity="error")
        if self.scenario["status"] == "pending":
            self.fail("scenario_evidence_pending", "/scenario/status", "来源指纹确认不会确认未知场景。")
        for key, expected in (("origin_target_range", "within_source_range"), ("initial_source_effects", "none"), ("event_order_policy", "declared_atomic_hit_order")):
            if self.scenario[key] != expected:
                self.fail("scene_input_required", "/scenario/" + key, "须明确范围、无初始源減抗及已校准原子顺序。")
        for key, limit in (("source", 4096), ("target_id", 128)):
            value = self.scenario[key]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                self.fail("scenario_evidence_required", "/scenario/" + key, "需要有限非空场景证据或目标ID。")
        accepted = self.scenario["accepted_assumptions_fingerprint"]
        if accepted is not None and (not isinstance(accepted, str) or not re.fullmatch(r"[0-9a-f]{64}", accepted)):
            self.fail("invalid_confirmation", "/scenario/accepted_assumptions_fingerprint", "确认须为64位SHA或null。", severity="error")

    def materialize(self):
        base = self.source_id[:-len(":upstream")]

        def part(pid, rule, delivery, label):
            item = self.part(rule, delivery, label)
            item["part_id"] = pid
            item["conditions"][0]["evidence"][0].update(profile_id=PROFILE_ID, profile_version=PROFILE_VERSION)
            return item

        static = part("upstream.swirl_bonus", {"kind": "constant", "value": self.bonus},
            {"kind": "buff", "start": 0, "end": self.period, "scope": "team", "target": self.owner,
             "source": "来源常驻普通扩散加成（前后台）", "effect": {"id": "value", "benefit_type_id": "benefit.reaction_bonus.swirl",
             "zone": "reaction_bonus", "stat": "swirl", "applies_to": {"mode": "all"}}}, "常驻普通扩散加成（仅提供者）")
        self.parts.append(static)
        self.static.append(copy.deepcopy(static))
        for element in ("hydro", "cryo"):
            value, duration, path = self.shreds[element]
            pid = "upstream.shred." + element
            rid = "upstream." + _hash({"source_id": self.source_id, "path": path})[:24]
            rule = {"id": rid, "name_zh": {"hydro": "水扩散减抗", "cryo": "冰扩散减抗"}[element], "owner": self.owner, "source_id": base + ":" + pid,
                "trigger": {"event": "elemental_reaction", "phase": "post", "characters": [self.owner], "reaction_types": ["swirl"],
                            "reaction_elements": [element], "owner_presence": "frontline"},
                "beneficiaries": {"mode": "team", "scope": "locked"},
                "timing": {"delay": 0, "duration": duration, "cooldown": 0, "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [{"id": "value", "benefit_type_id": "benefit.res_shred." + element, "value": value, "domain": "enemy"}],
                "status": self.scenario["status"], "source": self.scenario["source"]}
            self.rules.append(rule)
            self.initial.append({"rule_id": rid, "cooldown_remaining": 0, "instances": []})
            self.parts.append(part(pid, {"kind": "mechanics", "rule_id": rid}, {"kind": "mechanics"}, rule["name_zh"]))


def compile_reaction_benefit_source(bundle, *, owner, source_id, scenario, period):
    """Compile an atomic projection; no serialized result is accepted as input."""
    out = {"compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
        "status": "pending", "execution_status": "pending", "projection_status": "pending", "whole_source_status": "partial",
        "gameplay_semantics_status": "unverified", "parts": [], "static_parts": [], "rules": [], "initial_rules": [], "unique_keys": [],
        "issues": [], "branches": [], "raw_sources": [], "provenance": {}, "semantic_assumptions": [], "source_identity": {},
        "execution_binding": {"kind": "elemental_reaction", "ruleset": FREEZE_RULESET, "reaction_types": ["swirl"],
            "reaction_elements": ["hydro", "cryo"], "source_scope": "original_target", "provider_presence": "frontline",
            "provider_presence_scope": "dynamic_shred_only", "static_bonus_presence": "owner_on_and_off_field"},
        "actual_bundle_fingerprint": None, "assumptions_fingerprint": None,
        "required_confirmation": {"accepted_assumptions_fingerprint": None}, "execution_fingerprint": None}
    c = _ReactionCompiler(bundle, owner, source_id, scenario, period)
    materializable = False
    try:
        raw = json.dumps([bundle, owner, source_id, scenario, period], ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError("编译输入超过2MiB")
        stack, count = [([bundle, owner, source_id, scenario, period], 0)], 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if count > 16384 or depth > 64:
                raise ValueError("编译输入树超过16384节点或64层")
            if isinstance(value, dict) and any(not isinstance(key, str) for key in value):
                raise ValueError("JSON对象键必须是字符串")
            stack.extend((v, depth + 1) for v in (value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()))
        materializable = True
        out["actual_bundle_fingerprint"] = _hash(bundle)
        c.build()
    except _Stop:
        pass
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        c.issues.append({"code": "invalid_source", "path": "/", "severity": "error", "message": str(exc)[:1024]})
    if materializable:
        scope = {k: v for k, v in scenario.items() if k != "accepted_assumptions_fingerprint"} if isinstance(scenario, dict) else scenario
        fp = _hash({"compiler": COMPILER_VERSION, "profile": _hash(_profile()), "bundle": bundle, "owner": owner,
            "source_id": source_id, "scenario": scope, "period": period, "reference": REFERENCE_CONTRACT["contract_fingerprint"]})
        out.update(assumptions_fingerprint=fp, required_confirmation={"accepted_assumptions_fingerprint": fp})
        try:
            c.scenario_check()
            out["execution_binding"]["target_id"] = scenario["target_id"]
            if scenario["accepted_assumptions_fingerprint"] != fp:
                c.fail("semantic_profile_acceptance_required", "/scenario/accepted_assumptions_fingerprint", "请确认本次完整来源、场景与实验参考语义。", required_fingerprint=fp)
            if not c.issues:
                c.materialize()
        except _Stop:
            pass
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            c.issues.append({"code": "invalid_scenario", "path": "/scenario", "severity": "error", "message": str(exc)[:1024]})
        out["source_identity"] = copy.deepcopy(bundle.get("source", {})) if isinstance(bundle, dict) else {}
        out["provenance"] = {"compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION,
            "actual_bundle_fingerprint": out["actual_bundle_fingerprint"], "parameters": c.parameters, "parameter_bindings": c.parameter_sources,
            "field_usage": c.usage, "local_mapping_fingerprint": _hash(_profile()), "source_byte_evidence": copy.deepcopy(_profile()["files"]),
            "mapping_evidence": {"parent_checks": len(_profile()["mapping_evidence"]["parent_checks"]), "kind": "packaged_complete_old_new_nodes"},
            "semantics": copy.deepcopy(_profile()["semantics"]), "target_reference_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"]}
    status = "invalid" if any(i.get("severity") == "error" for i in c.issues) else "pending" if c.issues else "ready"
    out.update(status=status, execution_status=status, projection_status=status, issues=c.issues[:128],
        branches=c.branches, raw_sources=c.raw_sources, semantic_assumptions=c.assumptions,
        gameplay_semantics_status="assumed_in_scope" if status == "ready" else "unverified",
        coverage_certificate={"kind": "ordinary_swirl_benefit_projection_certificate", "source_complete": False,
            "projection_complete": status == "ready", "projection_status": status, "gameplay_verified": False,
            "reaction_scope": "ordinary_hydro_cryo_original_target"})
    if status == "ready":
        out.update(parts=c.parts, static_parts=c.static, rules=c.rules, initial_rules=c.initial, unique_keys=c.unique_keys,
                   execution_fingerprint=_hash({"assumptions": out["assumptions_fingerprint"], "parts": c.parts, "rules": c.rules, "initial_rules": c.initial}))
    if len(json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_BYTES:
        out.update(status="invalid", execution_status="invalid", projection_status="invalid", parts=[], static_parts=[], rules=[], initial_rules=[],
            unique_keys=[], branches=[], raw_sources=[], provenance={}, source_identity={}, semantic_assumptions=[], execution_fingerprint=None,
            issues=[{"code": "response_too_large", "severity": "error", "message": "回执超过2MiB；未返回部分投影或大原文。"}])
        out["coverage_certificate"].update(projection_complete=False, projection_status="invalid")
    return copy.deepcopy(out)
