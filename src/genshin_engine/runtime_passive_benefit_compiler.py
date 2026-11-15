"""Finite, evidence-backed inherent reaction benefits; no general ability VM."""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources

from genshin_runtime.normalization import Profile, verify_receipt

from .elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT
from .runtime_compiler import _Compiler, _escape, _hash, _Stop

PROFILE_ID = "inherent-ordinary-swirl-bonus-v1"
PROFILE_VERSION = 2
COMPILER_VERSION = 1
COMPILER_ID = "database-inherent-swirl-bonus-v1"
MAX_BYTES = 2 * 1024 * 1024
_ELEMENTS = {"Fire": "pyro", "Water": "hydro", "Electric": "electro", "Ice": "cryo"}
_PROPERTIES = {"Fire": "Actor_FireAddHurtExtraDelta", "Water": "Actor_WaterAddHurtExtraDelta",
               "Electric": "Actor_ElecAddHurtExtraDelta", "Ice": "Actor_IceAddHurtExtraDelta"}
_KNOWN = {"origin_target_range": "within_source_range", "recipient_range": "all_party_within_source_range",
          "initial_source_effects": "none", "recipient_bonus_policy": "not_included",
          "stat_basis": "fixed_panel_nonextra_em_equals_total_em", "event_order_policy": "declared_atomic_hit_order"}


@lru_cache(maxsize=1)
def _profile():
    return json.loads(resources.files("genshin_runtime").joinpath("resources/passive_benefit_profiles.json").read_text("utf8"))


def passive_benefit_compiler_caps():
    defaults = {"profile_id": PROFILE_ID, "profile_version": PROFILE_VERSION, "single_player_fixed": True,
        "reaction_scope": "ordinary_hydro_cryo_original_target", "target_state_profile": "freeze-reference-target-v1",
        "target_id": None, "simulation_clock": "no_hitlag", **{k: "unknown" for k in _KNOWN},
        "status": "pending", "source": ""}
    fields = {k: {"const": v} for k, v in defaults.items() if k not in {*_KNOWN, "target_id", "status", "source"}}
    fields.update({k: {"enum": ["unknown", v]} for k, v in _KNOWN.items()})
    fields.update(target_id={"type": ["string", "null"], "minLength": 1, "maxLength": 128},
        status={"enum": ["pending", "assumed", "confirmed"]}, source={"type": "string", "maxLength": 4096})
    return {"compiler": COMPILER_ID, "compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION, "default_scenario": defaults,
        "scenario_schema": {"type": "object", "required": list(defaults), "additionalProperties": False, "properties": fields},
        "source_kind": "inherent_proud_skill", "source_id_format": "{native_id}:{part_id}",
        "part_ids": ["passive.dmg_bonus.hydro", "passive.dmg_bonus.cryo"],
        "max_bytes": MAX_BYTES, "max_input_nodes": 32768, "max_input_depth": 64,
        "max_generated_parts": 2, "max_generated_rules": 2,
        "whole_source_status": "partial", "acceptance_owner": "app_actual_parent_scope",
        "confirmable_issue_codes": ["semantic_profile_acceptance_required"],
        "scene_input_issue_codes": ["scenario_evidence_pending", "scene_input_required", "scenario_evidence_required"],
        "confirmation_policy": "先明确突破、面板口径及场景，再由App核验真实事件父链并一次接受；未知不能靠确认升级。",
        "supported": ["固有来源参数到普通冰水反应增伤", "提供者触发时取样与受益者自身快照", "冰水独立刷新与到期"],
        "unsupported": ["动态精通与通用NONEXTRA账本", "来源不明的风附着", "武器扩散", "火雷/星扩散或传播", "多提供者覆盖"],
        "evidence": copy.deepcopy(_profile()["semantics"]), "mapping_fingerprint": _hash(_profile())}


def _walk(value, path=""):
    yield path, value
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, child in items:
        yield from _walk(child, path + "/" + _escape(key))


def _record_origin(record, field):
    """Rebuild field provenance from admitted raw parents, never from a claim."""
    origin = {key: copy.deepcopy(record[key]) for key in ("definition", "source_path", "source_sha256", "json_pointer", "node_sha256") if key in record}
    present = field in record["record"]
    return {**origin, "present": present, "raw_pointer": record["json_pointer"] + "/" + field if present else None,
        "requested_pointer": record["json_pointer"] + "/" + field,
        "field_node_sha256": _hash(record["record"][field]) if present else None}


class _PassiveCompiler(_Compiler):
    def __init__(self, profile, owner, source_id, scenario):
        super().__init__(profile, owner, source_id, scenario, None)
        self.profile = _profile()
        self.branches, self.raw_sources, self.bonuses = [], [], {}
        self.normalization = None

    def admitted(self, entry, kind, payload_key):
        self.evidence(entry, "/" + kind, payload_key, definition=kind in ("ability", "talent"))
        expected = self.profile["admission"][kind]
        file = next(f for f in self.profile["files"] if f["source_path"] == expected["source_path"])
        if (entry["source_path"] != expected["source_path"] or entry["json_pointer"] != expected["json_pointer"]
                or entry["source_sha256"] not in (file["workspace_source_sha256"], file["git_blob_sha256"])
                or _hash(entry[payload_key]) != expected["node_sha256"]):
            self.fail("unsupported_source_node", "/" + kind, "完整原节点、原指针或源字节未被此局部映射准入。")
        if "node_sha256" in entry and entry["node_sha256"] != expected["node_sha256"]:
            self.fail("inconsistent_node_hash", "/" + kind, "原文与节点SHA不一致。", severity="error")
        self.raw_sources.append({"kind": kind, **copy.deepcopy(entry)})
        self.usage.append({"path": "/" + kind, "classification": "admitted_complete_node", **expected})

    def branch(self, entry, path, kind, **detail):
        pointer = self.normalization.raw_pointer(entry["payload"], path, kind="ability")
        raw = dict(_walk(entry["payload"]))[pointer]
        self.branches.append({"kind": kind, "source_path": entry["source_path"],
            "json_pointer": entry["json_pointer"] + pointer, "source_sha256": entry["source_sha256"],
            "node_sha256": _hash(raw), "raw": copy.deepcopy(raw), **detail})

    def references(self, b, body, operations):
        """Reconstruct Proud/Talent/Ability references from complete raw parents."""
        source = b["source_identity"]
        snapshot, line = source["snapshot_id"], source["version_line"]
        entry, talent, proud = b["abilities"][0], b["talent"], b["parameter_record"]
        aid = _hash([snapshot, entry["source_path"], entry["json_pointer"], "ability"])
        tid = _hash([snapshot, talent["source_path"], talent["json_pointer"], "talent_config"])
        if entry["definition_id"] != aid or talent["definition_id"] != tid:
            self.fail("definition_identity_mismatch", "/", "节点身份与当前快照原指针不符。", severity="error")
        modifier_ids = {name: _hash([snapshot, entry["source_path"], entry["json_pointer"]
            + self.normalization.raw_pointer(entry["payload"], "/modifiers/" + _escape(name), kind="ability"), "modifier"])
            for name in body["modifiers"]}
        expected_nodes = {}
        for origin, kind, raw in ((entry, "ability", entry["payload"]), (talent, "talent_config", talent["operations"]),
                                  (proud, "excel_record", proud["record"])):
            ident = _hash([snapshot, origin["source_path"], origin["json_pointer"], kind])
            expected_nodes[ident] = (kind, origin["source_path"], origin["json_pointer"], _hash(raw))
        for name, ident in modifier_ids.items():
            relative = self.normalization.raw_pointer(entry["payload"], "/modifiers/" + _escape(name), kind="ability")
            raw = dict(_walk(entry["payload"]))[relative]
            expected_nodes[ident] = ("modifier", entry["source_path"], entry["json_pointer"] + relative, _hash(raw))
        definitions = b.get("definitions")
        if (not isinstance(definitions, list) or len(definitions) != len(expected_nodes)
                or {row.get("definition_id"): tuple(row.get(k) for k in ("definition_kind", "source_path", "json_pointer", "node_sha256"))
                    for row in definitions} != expected_nodes):
            self.fail("definition_graph_mismatch", "/definitions", "完整闭包节点身份与原始父结构不一致。")
        expected = {}
        for origin, raw, normalized, kind, definition_id in (
            (entry, entry["payload"], body, "ability", aid), (talent, talent["operations"], operations, "talent_config", tid),
            (proud, proud["record"], proud["record"], "excel_record", _hash([snapshot, proud["source_path"], proud["json_pointer"], "excel_record"]))):
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
                    from_id = definition_id
                    if kind == "ability" and pointer.startswith("/modifiers/"):
                        from_id = modifier_ids[pointer.split("/")[2].replace("~1", "/").replace("~0", "~")]
                    expected[(origin["source_path"], origin["json_pointer"] + relative)] = (from_id, target, value, field)
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
            self.fail("reference_graph_mismatch", "/references", "完整来源引用与原文控制图不一致。")

    def scenario_check(self):
        schema = passive_benefit_compiler_caps()["scenario_schema"]
        self.fields(self.scenario, schema["required"], (), "/scenario")
        for key, rule in schema["properties"].items():
            value = self.scenario[key]
            if "const" in rule:
                self.exact(value, rule["const"], "/scenario/" + key)
            elif "enum" in rule and value not in rule["enum"]:
                self.fail("invalid_scenario", "/scenario/" + key, "非法场景枚举。", severity="error")
        if self.scenario["status"] == "pending":
            self.fail("scenario_evidence_pending", "/scenario/status", "需要明确场景；来源确认不替代未知事实。")
        for key, expected in _KNOWN.items():
            if self.scenario[key] != expected:
                self.fail("scene_input_required", "/scenario/" + key, "须明确范围、初态、未计入增伤、固定精通口径及原子顺序。")
        for key, limit in (("source", 4096), ("target_id", 128)):
            if not isinstance(self.scenario[key], str) or not self.scenario[key].strip() or len(self.scenario[key]) > limit:
                self.fail("scenario_evidence_required", "/scenario/" + key, "需要非空有限的场景依据或目标ID。")

    def build(self):
        from genshin_runtime.passive_source_identity import (
            passive_source_fingerprint,
            passive_unlock_fingerprint,
        )

        p = self.bundle
        if not isinstance(p, dict) or p.get("mode") != "selected" or p.get("source_status") != "ready":
            self.fail("source_not_ready", "/", "需要明确选中且资料关系闭合的固有天赋来源。")
        if type(p.get("projection_version")) is not int or type(p.get("bundle_version")) is not int or p.get("projection_version") != 1 or p.get("bundle_version") != 1:
            self.fail("unsupported_source_version", "/", "尚未适配此固有来源版本。")
        for name, expected in (("content_fingerprint", _hash({k: v for k, v in p.items() if k != "content_fingerprint"})),
                               ("source_fingerprint", passive_source_fingerprint(p)), ("unlock_fingerprint", passive_unlock_fingerprint(p))):
            if p.get(name) != expected:
                self.fail("source_fingerprint_mismatch", "/" + name, "来源或解锁回执指纹不符。", severity="error")
        for value, path in ((self.owner, "/owner"), (self.source_id, "/source_id")):
            self.text(value, path)
            if len(value) > 128:
                self.fail("identity_limit", path, "来源或角色标识超出128字符。", severity="error")
        if any(i.get("blocks_source") for i in p["issues"]):
            self.fail("source_not_closed", "/issues", "来源仍有阻断项。")
        b = p["source_bundle"]
        if (not isinstance(b, dict) or b.get("source_kind") != "inherent_proud_skill"
                or b.get("reference_closed") is not True or b.get("truncated") or b.get("frontier") or b.get("talent_candidates")):
            self.fail("source_not_closed", "/source_bundle", "需要完整固有来源闭包，不能丢弃未决引用。")
        source = b["source_identity"]
        self.exact(source["kind"], "inherent_proud_skill", "/source_identity/kind")
        self.exact(source["upstream_commit"], self.profile["commit"], "/source_identity/upstream_commit")
        if source["version_line"] not in ("release", "beta") or not isinstance(source["snapshot_id"], str) or not re.fullmatch(r"[0-9a-f]{64}", source["snapshot_id"]):
            self.fail("invalid_snapshot", "/source_identity", "需要明确资料线和快照。", severity="error")
        for snapshot in (p["snapshot"], b["snapshot"]):
            if any(snapshot.get(k) != source[k] for k in ("version_line", "snapshot_id", "upstream_commit")):
                self.fail("mixed_snapshot", "/snapshot", "来源快照不一致。", severity="error")
        if any(source.get(k) != v for k, v in p["selection"].items()):
            self.fail("source_identity_mismatch", "/selection", "选择与来源身份不一致。", severity="error")
        if not isinstance(b["abilities"], list) or len(b["abilities"]) != 1 or not isinstance(b["references"], list) or len(b["references"]) > 256:
            self.fail("unsupported_graph", "/source_bundle", "此投影仅接完整单能力有界来源。")
        base = verify_receipt(b["normalization"], commit=source["upstream_commit"])
        if any(snapshot.get("normalization") != b["normalization"] for snapshot in (p["snapshot"], b["snapshot"])):
            self.fail("mixed_normalization", "/normalization", "来源归一化身份不一致。", severity="error")
        self.normalization = Profile(PROFILE_ID, (self.profile["commit"],),
            {**base.fields, **self.profile["fields"]}, {**base.types, **self.profile["types"]})
        records, unlock = p["entry_records"], p["unlock"]
        entry, talent, proud = b["abilities"][0], b["talent"], b["parameter_record"]
        for node, kind, payload in ((entry, "ability", "payload"), (talent, "talent", "operations"), (proud, "proud", "record"),
                                    (records["avatar"], "avatar", "record"), (records["depot"], "depot", "record")):
            self.admitted(node, kind, payload)
        if unlock.get("status") != "eligible_under_declared_progression" or unlock.get("supplied") != {"character_level": 90, "ascension": 6}:
            self.fail("progression_outside_scope", "/unlock", "当前执行片需要明确90级/突破6且达到来源门槛。")
        promote = unlock["selected_promote_record"]
        self.admitted(promote, "promote", "record")
        avatar, depot, pr = records["avatar"]["record"], records["depot"]["record"], proud["record"]
        opening, selection = records["selected_opening"], p["selection"]
        index = selection["opening_index"]
        if type(index) is not int or not 0 <= index < len(depot["inherentProudSkillOpens"]):
            self.fail("source_identity_mismatch", "/selection/opening_index", "固有入口序号无效。", severity="error")
        expected_selection = {"character_id": avatar["id"], "skill_depot_id": depot["id"], "proud_skill_id": pr["proudSkillId"],
            "proud_skill_group_id": pr["proudSkillGroupId"], "proud_level": pr["level"], "opening_index": index}
        if (selection != expected_selection or records["proud_skill"] != proud
                or depot["id"] not in [avatar.get("skillDepotId"), *avatar.get("candSkillDepotIds", [])]
                or opening["record"] != depot["inherentProudSkillOpens"][index]
                or opening["record"].get("proudSkillGroupId") != pr["proudSkillGroupId"]
                or opening["record"].get("needAvatarPromoteLevel") != pr.get("breakLevel")
                or type(pr.get("breakLevel")) is not int or not 0 <= pr["breakLevel"] <= 6
                or opening["source_path"] != records["depot"]["source_path"]
                or opening["source_sha256"] != records["depot"]["source_sha256"]
                or opening["json_pointer"] != records["depot"]["json_pointer"] + f"/inherentProudSkillOpens/{index}"
                or opening["node_sha256"] != _hash(opening["record"])
                or promote["record"].get("avatarPromoteId") != avatar.get("avatarPromoteId")
                or promote["record"].get("promoteLevel") != 6 or promote["record"].get("unlockMaxLevel") != 90
                or promote not in records["promote_records"]):
            self.fail("source_identity_mismatch", "/entry_records", "角色、固有入口、参数及突破证据不一致。", severity="error")
        threshold = pr["breakLevel"]
        facts = {key: {"present": True, "raw_value": record["record"][field], "origin": _record_origin(record, field)}
                 for key, record, field in (("need_avatar_promote_level", opening, "needAvatarPromoteLevel"),
                                             ("proud_break_level", proud, "breakLevel"))}
        required = {"status": "ready", "required_ascension": threshold, **facts}
        checks = [{"id": "current_stage_max_level", "status": "pass", "actual": 90, "expected": {"maximum": 90},
                   "origin": _record_origin(promote, "unlockMaxLevel")},
                  {"id": "explicit_ascension_threshold", "status": "pass", "actual": 6, "expected": {"minimum": threshold},
                   "origin": {key: facts[key]["origin"] for key in facts}}]
        if (unlock.get("requirement") != required or unlock.get("checks") != checks or unlock.get("issues")
                or unlock.get("eligibility_scope") != "explicit_ascension_threshold_and_current_stage_max_level"
                or unlock.get("complete_character_validity") != "not_evaluated"
                or unlock.get("unchecked") != ["minimum_character_level", "promotion_flow", "account_player_level", "current_client_unlock_state"]):
            self.fail("unlock_evidence_mismatch", "/unlock", "门槛、数值比较或字段原证据与实际记录不一致。", severity="error")
        self.exact(entry.get("variant_key"), "Default", "/ability/variant_key")
        params = b["param_list"]
        if not isinstance(params, list) or params != pr["paramList"]:
            self.fail("parameter_record_mismatch", "/param_list", "参数列表与实际天赋记录不一致。", severity="error")
        body = self.normalization.normalize(entry["payload"], kind="ability")
        operations = self.normalization.normalize(talent["operations"], kind="talent_config")
        if talent["json_pointer"] != "/" + _escape(pr["openConfig"]):
            self.fail("source_identity_mismatch", "/talent", "固有参数的openConfig未指向选中Talent。")
        self.references(b, body, operations)
        self.parameters = copy.deepcopy(body["abilitySpecials"])
        writers = set()
        for i, op in enumerate(operations):
            fields = ("$type", "CLGIKOPIKBC", "abilityName") + (() if i == 0 else ("paramDelta", "paramSpecial"))
            self.fields(op, fields, (), f"/talent/{i}")
            self.exact(op["$type"], "AddAbility" if i == 0 else "ModifyAbility", f"/talent/{i}/$type")
            self.exact(op["abilityName"], body["abilityName"], f"/talent/{i}/abilityName")
            self.exact(op["CLGIKOPIKBC"], True, f"/talent/{i}/CLGIKOPIKBC", "已审固定装配开关；不解释一般恢复行为。")
            if not i:
                continue
            token, name = op["paramDelta"], op["paramSpecial"]
            if name not in self.parameters or name in writers or self.parameters[name] != 0 or not isinstance(token, str) or not re.fullmatch(r"%[1-8]", token):
                self.fail("unsupported_parameter_assembly", f"/talent/{i}", "只接受零初值单写者精确参数槽。")
            param_index = int(token[1:]) - 1
            self.parameters[name] = self.scalar(params[param_index], f"/param_list/{param_index}")
            self.parameter_sources[name] = {"param_index": param_index, "operation_pointer": talent["json_pointer"] + f"/{i}",
                "parameter_pointer": proud["json_pointer"] + f"/paramList/{param_index}"}
            writers.add(name)
        if set(self.parameters) != writers:
            self.fail("unresolved_parameter", "/abilitySpecials", "所有用途参数均须被唯一装配。")
        self.interpret(entry, body)

    def interpret(self, entry, body):
        self.defaults(body["LKLPDIMKLKI"], "/ability/LKLPDIMKLKI")
        self.exact(body["isDynamicAbility"], True, "/ability/isDynamicAbility", "固定队伍/培养/装备，无外部移除、恢复或命中停滞。")
        attach = body["onAdded"][0]
        self.exact(len(body["onAdded"]), 1, "/ability/onAdded/count")
        self.exact(attach["$type"], "AttachModifier", "/ability/onAdded/0/$type")
        handler_name = attach["modifierName"]
        handler = body["modifiers"][handler_name]
        hp = "/modifiers/" + _escape(handler_name)
        self.branch(entry, "/onAdded/0", "attachment", status="projected", execution_status="ready",
            reason="固定来源挂载；onAdded.doOffStage不等同于后续反应前台限制")
        self.assumptions.extend({"status": "reference_or_declared_scope", "assumption": value} for value in self.profile["semantics"].values())
        seen = set()
        for i, listener in enumerate(handler["modifierMixins"]):
            path = hp + f"/modifierMixins/{i}"
            self.exact(listener["$type"], "DoActionByElementReactionMixin", path + "/$type")
            self.exact(listener["entityTypes"], ["Monster"], path + "/entityTypes")
            self.exact(listener["range"], 20, path + "/range", "原目标处于来源范围；不推断原字段单位。")
            reactions = listener["reactionTypes"]
            ordinary = [r for r in reactions if r.startswith("Swirl")]
            if len(ordinary) != 1 or ordinary[0][5:] not in _ELEMENTS:
                self.fail("unsupported_reaction", path, "未知反应枚举不能投影。")
            raw_element = ordinary[0][5:]
            self.exact(reactions, [ordinary[0]] + (["StarSwirlIce"] if raw_element == "Ice" else []), path + "/reactionTypes")
            actions = listener["actions"]
            self.exact(len(actions), 2, path + "/actions/count")
            for j, action in enumerate(actions):
                ap = path + f"/actions/{j}"
                self.exact(action["$type"], "RemoveUniqueModifier" if j == 0 else "ApplyModifier", ap + "/$type")
                self.exact(action["target"], "AllPlayerAvatars", ap + "/target", "单人固定全队且满足受益范围。")
                self.exact(action["doOffStage"], True, ap + "/doOffStage")
                self.exact(action["predicates"], [{"$type": "ByTargetPositionToSelfPosition", "target": "Target",
                    "value": 40, "logic": "LesserOrEqual"}], ap + "/predicates", "全部实际受益者处于原始距离谓词范围。")
            name = actions[1]["modifierName"]
            self.exact(actions[0]["modifierName"], name, path + "/actions/0/modifierName")
            modifier = body["modifiers"][name]
            self.exact(modifier["stacking"], "Unique", path + "/modifier/stacking")
            self.exact(modifier["isUnique"], True, path + "/modifier/isUnique")
            self.exact(list(modifier["properties"]), [_PROPERTIES[raw_element]], path + "/modifier/properties")
            expression = modifier["properties"][_PROPERTIES[raw_element]]
            if not isinstance(expression, list) or len(expression) != 3 or expression[1:] != ["FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY", "MUL"]:
                self.fail("unsupported_expression", path, "只支持有明确固定口径的NONEXTRA精通单层乘法。")
            factor = self.param(expression[0], path + "/factor")
            duration = self.time(self.param(modifier["duration"], path + "/duration"), path + "/duration")
            element = _ELEMENTS[raw_element]
            if element in seen:
                self.fail("duplicate_element", path, "同一元素多个监听不能静默合并。")
            seen.add(element)
            supported = element in ("hydro", "cryo")
            if supported:
                self.bonuses[element] = (factor, duration, path)
                self.unique_keys.append(name)
            self.branch(entry, path, "reaction_listener", element=element,
                status="projected" if supported else "excluded_by_scope", execution_status="ready" if supported else "unsupported",
                excluded_reactions=[r for r in reactions if r != ordinary[0]], reason="普通分支投影，保留原始特殊反应及范围")
            self.branch(entry, "/modifiers/" + _escape(name), "recipient_modifier", element=element, value_factor=factor, duration=duration,
                status="projected" if supported else "excluded_by_scope", execution_status="ready" if supported else "unsupported",
                raw_stat="FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY", execution_stat="em", reason="显式固定精通相等假设；受益者自身伤害面板，非扩散伤害加成")
        if set(self.bonuses) != {"hydro", "cryo"}:
            self.fail("incomplete_projection", hp, "必须原子取得冰水两个完整分支。")

    def materialize(self):
        for element in ("hydro", "cryo"):
            factor, duration, path = self.bonuses[element]
            pid = "passive.dmg_bonus." + element
            rid = "passive." + _hash({"source_id": self.source_id, "path": path})[:24]
            rule = {"id": rid, "name_zh": {"hydro": "扩散触发水元素增伤", "cryo": "扩散触发冰元素增伤"}[element],
                "owner": self.owner, "source_id": self.source_id + ":" + pid,
                "trigger": {"event": "elemental_reaction", "phase": "post", "characters": [self.owner], "reaction_types": ["swirl"],
                            "reaction_elements": [element], "owner_presence": "any"},
                "beneficiaries": {"mode": "team", "scope": "locked"},
                "timing": {"delay": 0, "duration": duration, "cooldown": 0, "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [{"id": "value", "benefit_type_id": "benefit.dmg_bonus." + element, "domain": "self",
                    "value": {"kind": "stat", "subject": "owner", "stat": "em", "factor": factor, "sample": "trigger"}}],
                "status": self.scenario["status"], "source": self.scenario["source"]}
            self.rules.append(rule)
            self.initial.append({"rule_id": rid, "cooldown_remaining": 0, "instances": []})
            part = self.part({"kind": "mechanics", "rule_id": rid}, {"kind": "mechanics"}, rule["name_zh"])
            part["part_id"] = pid
            part["conditions"][0]["evidence"][0].update(profile_id=PROFILE_ID, profile_version=PROFILE_VERSION)
            self.parts.append(part)


def compile_passive_benefit_source(profile, *, owner, source_id, scenario):
    """Pure source projection; App separately gates actual context and acceptance.

    A ready projection never authorizes an arbitrary reaction source or claims
    that an anonymous panel has been decomposed into game attribute channels.
    """
    out = {"compiler": COMPILER_ID, "compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION, "status": "pending", "execution_status": "pending", "projection_status": "pending",
        "whole_source_status": "partial", "gameplay_semantics_status": "unverified", "parts": [], "rules": [],
        "initial_rules": [], "unique_keys": [], "branches": [], "raw_sources": [], "provenance": {}, "issues": [],
        "semantic_assumptions": [], "source_identity": {}, "source_fingerprint": None, "unlock_fingerprint": None,
        "assumptions_fingerprint": None, "execution_fingerprint": None,
        "execution_binding": {"kind": "elemental_reaction", "ruleset": FREEZE_RULESET, "reaction_types": ["swirl"],
            "reaction_elements": ["hydro", "cryo"], "source_scope": "original_target", "provider_presence": "any",
            "eligibility": "fresh_calibrated_c0_skill_parent_required_by_app", "acceptance": "app_actual_parent_scope",
            "raw_stat": "FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY", "execution_stat": "em",
            "stat_basis": "declared_fixed_equality", "sample": "trigger", "beneficiary_domain": "self"}}
    c = _PassiveCompiler(profile, owner, source_id, scenario)
    materializable = False
    try:
        raw = json.dumps([profile, owner, source_id, scenario], ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError("编译输入超过2MiB")
        stack, count = [([profile, owner, source_id, scenario], 0)], 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if count > 32768 or depth > 64:
                raise ValueError("编译输入树超过32768节点或64层")
            if isinstance(value, dict) and any(not isinstance(key, str) for key in value):
                raise ValueError("JSON对象键必须是字符串")
            stack.extend((v, depth + 1) for v in (value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()))
        materializable = True
        c.build()
        c.scenario_check()
        c.materialize()
    except _Stop:
        pass
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        c.issues.append({"code": "invalid_source_or_scenario", "path": "/", "severity": "error", "message": str(exc)[:1024]})
    if materializable:
        out["source_fingerprint"] = profile.get("source_fingerprint") if isinstance(profile, dict) else None
        out["unlock_fingerprint"] = profile.get("unlock_fingerprint") if isinstance(profile, dict) else None
        source_bundle = profile.get("source_bundle") if isinstance(profile, dict) else None
        out["source_identity"] = copy.deepcopy(source_bundle.get("source_identity", {})) if isinstance(source_bundle, dict) else {}
        out["assumptions_fingerprint"] = _hash({"compiler": COMPILER_VERSION, "profile": _hash(_profile()),
            "source": out["source_fingerprint"], "unlock": out["unlock_fingerprint"], "owner": owner,
            "source_id": source_id, "scenario": scenario, "reference": REFERENCE_CONTRACT["contract_fingerprint"]})
        out["provenance"] = {"compiler": COMPILER_ID, "compiler_version": COMPILER_VERSION, "profile_id": PROFILE_ID,
            "profile_version": PROFILE_VERSION, "source_fingerprint": out["source_fingerprint"], "unlock_fingerprint": out["unlock_fingerprint"],
            "parameters": c.parameters, "parameter_bindings": c.parameter_sources, "field_usage": c.usage,
            "local_mapping_fingerprint": _hash(_profile()), "source_byte_evidence": copy.deepcopy(_profile()["files"]),
            "mapping_evidence": {"kind": "packaged_complete_old_new_nodes", "parent_checks": len(_profile()["mapping_evidence"]["parent_checks"])},
            "semantics": copy.deepcopy(_profile()["semantics"]), "reference_sources": copy.deepcopy(_profile()["reference_sources"]),
            "target_reference_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"]}
    status = "invalid" if any(i.get("severity") == "error" for i in c.issues) else "pending" if c.issues else "ready"
    out.update(status=status, execution_status=status, projection_status=status, issues=c.issues[:128],
        branches=c.branches, raw_sources=c.raw_sources, semantic_assumptions=c.assumptions,
        gameplay_semantics_status="declared_not_verified" if status == "ready" else "unverified",
        coverage_certificate={"kind": "ordinary_swirl_passive_projection_certificate", "source_complete": False,
            "projection_complete": status == "ready", "projection_status": status, "gameplay_verified": False,
            "reaction_scope": "ordinary_hydro_cryo_original_target", "actual_parent_eligibility": "checked_by_app"})
    if status == "ready":
        out["execution_binding"]["target_id"] = scenario["target_id"]
        out.update(parts=c.parts, rules=c.rules, initial_rules=c.initial, unique_keys=c.unique_keys,
            execution_fingerprint=_hash({"assumptions": out["assumptions_fingerprint"], "parts": c.parts,
                                        "rules": c.rules, "initial_rules": c.initial}))
    if len(json.dumps(out, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_BYTES:
        out.update(status="invalid", execution_status="invalid", projection_status="invalid", parts=[], rules=[], initial_rules=[],
            unique_keys=[], branches=[], raw_sources=[], provenance={}, source_identity={}, semantic_assumptions=[], execution_fingerprint=None,
            issues=[{"code": "response_too_large", "severity": "error", "message": "回执超过2MiB；未交付部分执行结果。"}])
        out["coverage_certificate"].update(projection_complete=False, projection_status="invalid")
    return copy.deepcopy(out)
