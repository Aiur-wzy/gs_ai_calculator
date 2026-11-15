"""Pure, bounded identities for a reviewed limited-hit benefit source.

The recipe authenticates a complete local raw shape, not a global decoder or a
claim that the whole Ability can execute. No database or App imports belong here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from decimal import Decimal
from functools import lru_cache
from importlib import resources

LIMITS = {"max_bytes": 524288, "max_nodes": 32, "max_edges": 128,
          "max_candidates": 8, "max_issues": 64}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def pointer(value, path):
    for part in path[1:].split("/") if path else []:
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


@lru_cache(maxsize=1)
def recipe():
    data = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/limited_hit_benefit_profiles.json").read_text("utf8"))
    data["recipe_fingerprint"] = fingerprint(data)
    return data


def reference_contract():
    data = recipe()
    return copy.deepcopy({key: data[key] for key in ("profile_id", "profile_version", "commit", "old_commit",
        "evidence_tier", "recipe_fingerprint", "primary_sources", "reference_policy")})


def _same(a, b):
    return fingerprint(a) == fingerprint(b)


def limited_hit_benefit_source_payload(profile):
    bundle = profile["source_bundle"]
    return copy.deepcopy({"projection_version": profile["projection_version"], "graph_scope": profile["graph_scope"],
        "snapshot": {**{key: profile["snapshot"][key] for key in
            ("version_line", "snapshot_id", "upstream_commit", "indexer_version")},
            "normalization": profile["snapshot"].get("normalization")},
        "selection": profile["selection"], "level": profile["level"], "param_index": profile["param_index"],
        "parent_bridge": profile["parent_bridge"], "benefit_candidates": profile["benefit_candidates"],
        "projection": profile["projection"], "reference_contract": profile["reference_contract"],
        "source_bundle": {"entries": [{key: entry[key] for key in
            ("definition", "source_path", "source_sha256", "json_pointer", "node_sha256")}
            for entry in bundle["entries"]], "entry_ids": bundle["entry_ids"],
            "references": bundle["references"], "boundaries": bundle["boundaries"]}})


def limited_hit_benefit_source_fingerprint(profile):
    return fingerprint(limited_hit_benefit_source_payload(profile))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _positive_id(value):
    return type(value) is int and 0 < value <= 2**53-1


def rebuild_projection(profile):
    """Reconstruct trusted values from complete source; does not trust projections."""
    r, family = recipe(), recipe()["family"]
    state, bundle = profile["snapshot"], profile["source_bundle"]
    _require(profile["projection_version"] == 1 and type(profile["projection_version"]) is int
             and profile["graph_scope"] == "limited_hit_benefit_source_v1", "来源投影版本未准入")
    _require(state["upstream_commit"] == r["commit"] and _same(state["normalization"], r["normalization"]),
             "来源提交或规范映射未准入")
    _require(state["version_line"] in ("release", "beta") and type(state["indexer_version"]) is str
             and state["indexer_version"] in ("2", "3", "4", "5", "6", "7"), "来源版本线或索引未准入")
    _require(isinstance(state["snapshot_id"], str) and re.fullmatch("[0-9a-f]{64}", state["snapshot_id"]), "来源快照无效")
    _require(_same(profile["reference_contract"], reference_contract()), "参考契约改变")
    values = bundle["entries"]
    _require(isinstance(values, list) and len(values) <= LIMITS["max_nodes"], "完整节点预算超限")
    entries = {}
    for entry in values:
        d, raw = entry["definition"], entry["payload"]
        ident, path, ptr, kind = (d[key] for key in ("definition_id", "source_path", "json_pointer", "definition_kind"))
        _require(ident not in entries and isinstance(path, str) and isinstance(ptr, str), "重复或无效定义身份")
        _require(ident == fingerprint([state["snapshot_id"], path, ptr, kind]), "定义身份与快照不一致")
        _require(entry["source_path"] == path and entry["json_pointer"] == ptr
                 and entry["node_sha256"] == d["node_sha256"] == fingerprint(raw), "原文节点指纹不一致")
        _require(isinstance(entry["source_sha256"], str) and re.fullmatch("[0-9a-f]{64}", entry["source_sha256"]), "整源SHA无效")
        entries[ident] = entry
    ids = bundle["entry_ids"]
    _require(set(ids) == {"avatar", "depot", "skill", "proud_skill", "talent", "ability", "modifiers"}, "入口身份字段不完整")
    _require(isinstance(ids["modifiers"], list) and len(set(ids["modifiers"])) == len(ids["modifiers"]), "重复modifier")
    expected_ids = {ids[k] for k in ids if k != "modifiers"} | set(ids["modifiers"])
    _require(expected_ids == set(entries), "完整来源存在遗漏或未归属节点")
    selected = {key: entries[ids[key]] for key in ids if key != "modifiers"}
    raw = {key: entry["payload"] for key, entry in selected.items()}
    selection = profile["selection"]
    for key in ("character_id", "skill_depot_id", "skill_id"):
        _require(_positive_id(selection[key]), "角色/技能身份必须为正整数")
    _require(type(selection["constellation"]) is int and selection["constellation"] == 0
             and type(profile["level"]["effective_level"]) is int and profile["level"]["effective_level"] == 10,
             "只支持C0有效10级")
    _require(type(profile["param_index"]) is int and profile["param_index"] == family["parent_param_index"], "父参数用途未准入")
    _require(raw["avatar"].get("id") == selection["character_id"] and type(raw["avatar"].get("id")) is int
             and raw["depot"].get("id") == selection["skill_depot_id"] and type(raw["depot"].get("id")) is int
             and raw["skill"].get("id") == selection["skill_id"] and type(raw["skill"].get("id")) is int, "入口行身份不匹配")
    depot_ids = [raw["avatar"].get("skillDepotId"), *raw["avatar"].get("candSkillDepotIds", [])]
    _require(selection["skill_depot_id"] in depot_ids and all(type(v) is int for v in depot_ids), "角色技能组不归属")
    skills = raw["depot"].get("skills", [])
    _require(isinstance(skills, list) and len(skills) > 1 and type(skills[1]) is int
             and skills[1] == selection["skill_id"] and "skill" in selection["matching_slots"], "父来源不是精确E槽")
    proud = raw["proud_skill"]
    _require(type(proud.get("level")) is int and proud["level"] == 10
             and type(proud.get("proudSkillGroupId")) is int
             and proud["proudSkillGroupId"] == raw["skill"].get("proudSkillGroupId")
             and type(raw["skill"].get("proudSkillGroupId")) is int, "技能等级来源不归属")
    for name, table, field in (("avatar", "Avatar", "id"), ("depot", "AvatarSkillDepot", "id"),
                              ("skill", "AvatarSkill", "id"), ("proud_skill", "ProudSkill", "proudSkillId")):
        d = selected[name]["definition"]
        _require(d["definition_kind"] == "excel_record" and d["source_path"] == f"ExcelBinOutput/{table}ExcelConfigData.json"
                 and re.fullmatch(r"/(0|[1-9][0-9]*)", d["json_pointer"])
                 and _positive_id(raw[name].get(field)) and _same(d["exact_key"], {field: raw[name][field]})
                 and d["owner_definition_id"] is None and d["variant_key"] is None and d["namespace_key"] == "excel", "Excel原行身份不符")
    ability, talent = selected["ability"], selected["talent"]
    _require(_same(raw["ability"], r["ability"]["new_raw"]), "完整Ability原文形状未准入")
    _require(type(raw["ability"]["ECFFDLHMCLK"][0]["ACFKMLJKONM"]) is int
             and raw["ability"]["ECFFDLHMCLK"][0]["ACFKMLJKONM"] == selection["skill_id"], "Ability显式技能ID不属于父选择")
    _require(_same(raw["talent"], r["talent"]["raw"]), "完整Talent原文形状未准入")
    ad, td = ability["definition"], talent["definition"]
    _require(ad["definition_kind"] == "ability" and ad["source_path"] == r["ability"]["source_path"]
             and re.fullmatch(r"/(0|[1-9][0-9]*)/Default", ad["json_pointer"])
             and ad["exact_key"] == raw["ability"]["IIBMNPFLNMJ"] and ad["variant_key"] == "Default"
             and ad["owner_definition_id"] is None and ad["namespace_key"] == "Temp", "Ability完整父身份不符")
    _require(td["definition_kind"] == "talent_config" and td["source_path"] == r["talent"]["source_path"]
             and td["json_pointer"] == "/" + r["talent"]["name"] and td["exact_key"] == proud.get("openConfig") == r["talent"]["name"]
             and td["owner_definition_id"] is None and td["variant_key"] is None and td["namespace_key"] == "talent", "Talent与Proud关系不符")
    modifiers = raw["ability"]["GPPBBDEICKP"]
    _require(len(ids["modifiers"]) == len(modifiers), "modifier父树不完整")
    for ident in ids["modifiers"]:
        e, d = entries[ident], entries[ident]["definition"]
        key = d["exact_key"]
        _require(key in modifiers and d["definition_kind"] == "modifier" and d["owner_definition_id"] == ids["ability"]
                 and d["source_path"] == ad["source_path"] and d["variant_key"] == ad["variant_key"] and d["namespace_key"] == ad["namespace_key"]
                 and d["json_pointer"] == ad["json_pointer"] + "/GPPBBDEICKP/" + key
                 and e["source_sha256"] == ability["source_sha256"] and _same(e["payload"], modifiers[key]), "modifier与完整父节点不一致")
    refs = bundle["references"]
    _require(isinstance(refs, list) and len(refs) <= LIMITS["max_edges"], "关系预算超限")
    templates = r["reference_templates"]
    _require(len(refs) == len(templates), "完整父关系数量改变")
    by_leaf = {(e["from_definition_id"], e["json_pointer"]): e for e in refs}
    _require(len(by_leaf) == len(refs), "重复关系叶")
    for template in templates:
        origin = next(e for e in entries.values() if e["definition"]["definition_kind"] == template["entry_kind"]
                      and e["definition"]["exact_key"] == template["entry_key"])
        did = origin["definition"]["definition_id"]
        ptr = origin["json_pointer"] + template["relative_pointer"]
        ref = by_leaf[(did, ptr)]
        _require(ref["reference_id"] == fingerprint([did, ptr]) and ref["source_path"] == origin["source_path"]
                 and ref["version_line"] == state["version_line"] and ref["snapshot_id"] == state["snapshot_id"]
                 and ref["extraction_rule_id"] == state["indexer_version"], "关系来源身份不符")
        for key in ("field_name", "relation_kind", "target_kind", "target_key", "source_order", "presence_status"):
            _require(_same(ref[key], template[key]), "关系原文身份改变")
        scope = origin["definition"]["owner_definition_id"] or (did if origin["definition"]["definition_kind"] == "ability" else None)
        _require(_same(ref["scope"], {"owner_definition_id": scope}), "关系父作用域改变")
        targets = [e["definition"]["definition_id"] for e in entries.values()
                   if e["definition"]["definition_kind"] == template["target_kind"] and _same(e["definition"]["exact_key"], template["target_key"])]
        if len(targets) == 1:
            _require(ref["resolution_status"] == "resolved" and ref["resolved_definition_id"] == targets[0]
                     and ref["candidate_ids"] == targets, "已覆盖关系未精确解析")
    _require(_same(bundle["boundaries"], r["boundaries"]), "未覆盖边界改变")
    def origin(index, writer, special):
        op = raw["talent"][writer]
        expected = {"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True, "IIBMNPFLNMJ": ad["exact_key"],
                    "PEOJKHKHAKL": special, "PCABAADALND": f"%{index + 1}"}
        _require(_same(op, expected), "参数唯一写者形状不符")
        related = [v for v in raw["talent"] if v.get("IIBMNPFLNMJ") == ad["exact_key"] and v.get("PEOJKHKHAKL") == special]
        _require(len(related) == 1, "参数写者不唯一")
        return {"param_index": index, "special": special, "writer_entry_id": ids["talent"],
            "writer_pointer": talent["json_pointer"] + f"/{writer}", "parameter_entry_id": ids["proud_skill"],
            "parameter_pointer": selected["proud_skill"]["json_pointer"] + f"/paramList/{index}", "target_ability_entry_id": ids["ability"]}
    info = pointer(raw["ability"], family["attack_path"] + family["attack_info_suffix"])
    prop = info["EHKJIDFINEK"]
    bridge = {"status": "ready", "kind": "scalar_target_unique_attack", "variant": family["variant"],
        "parameter": origin(family["parent_param_index"], family["writer_index"], prop["KOLOHIDMHHE"]),
        "attack": {"entry_id": ids["ability"], "operation_pointer": ability["json_pointer"] + family["attack_path"],
            "attack_info_pointer": ability["json_pointer"] + family["attack_path"] + family["attack_info_suffix"],
            "attack_property_pointer": ability["json_pointer"] + family["attack_path"] + family["attack_info_suffix"] + family["property_suffix"],
            "attack_tag": info["IAFHAPFBBOB"], "attack_type": prop["PEPDKCDHNIM"], "element": "cryo"},
        "identity_scope": "source_identity_only_not_full_damage_semantics"}
    projection = {}
    for key, field in family["fields"].items():
        value = proud["paramList"][field["param_index"]]
        _require(type(value) in (int, float) and abs(value) <= 1_000_000_000 and math.isfinite(value), "收益参数不是有限有界数")
        _require(value >= 0 if key == "coefficient" else value > 0, "收益参数范围不支持")
        if key == "capacity":
            _require(value == int(value) and value <= 40000, "次数不是受支持的整数")
        if key == "duration_seconds":
            ticks = Decimal(str(value)) * 1000000
            _require(ticks == ticks.to_integral_value(), "持续时间不是精确微秒")
        projection[key] = {"status": "ready", "value": value,
                           "origin": origin(field["param_index"], field["writer_index"], field["special"])}
    candidate = {"candidate_id": "benefit." + fingerprint({"family": family["id"], "entry_id": ids["ability"],
        "param_index": profile["param_index"]}), "family_id": family["id"], "variant": family["variant"], "status": "ready"}
    return {"parent_bridge": bridge, "projection": projection, "candidate": candidate}


def verify_limited_hit_benefit_projection(profile):
    try:
        _require(len(json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")) <= LIMITS["max_bytes"], "完整响应预算超限")
        _require(profile["status"] == profile["source_status"] == profile["projection_status"] == "ready"
                 and profile["whole_source_status"] == "partial" and profile["execution_support"] == "not_evaluated", "来源仍未就绪")
        _require(profile["content_fingerprint"] == fingerprint({k: v for k, v in profile.items() if k != "content_fingerprint"}), "完整响应指纹失效")
        payload = limited_hit_benefit_source_payload(profile)
        _require(_same(payload, profile["semantic_dependency"]) and fingerprint(payload) == profile["source_fingerprint"], "来源身份失效")
        rebuilt = rebuild_projection(profile)
        _require(_same(rebuilt["parent_bridge"], profile["parent_bridge"])
                 and _same(rebuilt["projection"], profile["projection"])
                 and _same([rebuilt["candidate"]], profile["benefit_candidates"]), "投影与原始来源重建不一致")
        return copy.deepcopy(rebuilt)
    except (KeyError, TypeError, IndexError, StopIteration, OverflowError, RecursionError) as exc:
        raise ValueError("有限次数收益来源结构无效") from exc
