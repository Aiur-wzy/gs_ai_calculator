"""Finite reference trigger certificates; this module never executes a Modifier VM."""
from __future__ import annotations

import copy
import json
import re
from decimal import ROUND_CEILING, Decimal
from importlib import resources

from genshin_runtime.energy_trigger_identity import (
    LIMITS,
    all_entries,
    build_bridge,
    energy_trigger_source_payload,
    family_for,
    fingerprint,
    generation_source_payload,
    pointer,
    recipe,
    reference_contract,
)
from genshin_runtime.normalization import verify_receipt

COMPILER = "reference-energy-trigger-v1"
PROFILE_ID = "single-contact-energy-trigger-reference-v1"


def energy_trigger_compiler_caps():
    default = {"target_kind": "ordinary_non_animal_monster", "drop_allowed": True,
        "initial_controller": "inactive", "pickup": "all_at_once", "hitlag_policy": "unknown",
        "no_external_controller_changes": True, "all_party_alive": True, "status": "pending", "source": ""}
    properties = {k: {"const": v} for k, v in default.items() if k not in ("hitlag_policy", "status", "source")}
    properties.update(hitlag_policy={"enum": ["unknown", "zero_hitlag_reference"]},
        status={"enum": ["pending", "assumed", "confirmed"]}, source={"type": "string", "maxLength": 4096})
    return {"compiler": COMPILER, "compiler_version": 1, "profile_id": PROFILE_ID, "profile_version": 1,
        "default_scenario": default, "scenario_schema": {"type": "object", "required": list(default),
            "additionalProperties": False, "properties": properties}, "limits": dict(LIMITS),
        "reference_contract": reference_contract(), "whole_source_status": "partial",
        "supported_families": ["mark_selected_initial_attack_v1", "ability_local_initial_attack_v1"],
        "confirmable_issue_codes": ["energy_trigger_scenario_pending"],
        "unsupported": ["random_generation", "general_unique_modifier_vm", "nonzero_hitlag", "multiple_contacts",
            "offstage_owner", "unreviewed_parent_structure", "damage_ratio_interpretation", "particle_travel_inference"]}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _entry(entry, snapshot):
    d = entry["definition"]
    _require(all(entry[k] == d[k] for k in ("source_path", "json_pointer", "node_sha256")), "来源原指针身份不一致")
    _require(fingerprint(entry["payload"]) == entry["node_sha256"], "来源完整原文SHA不一致")
    _require(fingerprint([snapshot["snapshot_id"], entry["source_path"], entry["json_pointer"], d["definition_kind"]]) == d["definition_id"], "定义身份不符")
    _require(re.fullmatch("[0-9a-f]{64}", entry["source_sha256"]) is not None, "源文件SHA格式错误")
    if d["definition_kind"] == "ability":
        _require(entry["payload"].get("IIBMNPFLNMJ") == d["exact_key"], "Ability精确名称与原文不符")
    elif d["definition_kind"] == "modifier":
        _require(entry["json_pointer"].rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~") == d["exact_key"], "modifier精确名称与原文不符")


def _units(query):
    """Recheck exactly the admitted standard units, not arbitrary item execution."""
    _require(query["status"] == "ready", "单位来源尚未就绪")
    _require(fingerprint(generation_source_payload(query)) == query["source_fingerprint"], "单位来源指纹不一致")
    _require(fingerprint({k: v for k, v in query.items() if k != "content_fingerprint"}) == query["content_fingerprint"], "单位完整回执指纹不一致")
    contract = json.loads(resources.files("genshin_runtime").joinpath("resources/energy_generation_profiles.json").read_text("utf8"))
    _require(query["reference_contract"]["recipe_fingerprint"] == fingerprint(contract), "单位参考版本变化")
    material = query["material"]
    _require(len(material["candidates"]) == 1 and material["status"] == "resolved", "Material并非唯一解析")
    raw = material["candidates"][0]["payload"]
    origin = query["origin"]
    parent, owner = origin["parent"], origin["owner"] or origin["parent"]
    operation = origin["operation"]
    _require(operation["raw_pointer"].startswith(parent["json_pointer"] + "/")
        and pointer(parent["payload"], operation["raw_pointer"][len(parent["json_pointer"]):]) == operation["raw"]
        and operation["node_sha256"] == fingerprint(operation["raw"]), "生成叶原文与所属定义不符")
    if origin["owner"] is not None:
        _require(parent["definition"]["owner_definition_id"] == owner["definition"]["definition_id"]
            and parent["source_path"] == owner["source_path"]
            and pointer(owner["payload"], parent["json_pointer"][len(owner["json_pointer"]):]) == parent["payload"], "生成modifier与owner不一致")
    ref, material_ref = origin["reference"], origin["material_reference"]
    leaf = operation["raw_pointer"] + "/KKMLMJNCLLG"
    _require(ref["from_definition_id"] == parent["definition"]["definition_id"]
        and ref["source_path"] == parent["source_path"] and ref["json_pointer"] == leaf
        and ref["target_key"] == raw["id"] and ref["relation_kind"] == "external_asset"
        and ref["reference_id"] == fingerprint([ref["from_definition_id"], leaf])
        and query["selection"]["origin_reference_id"] == ref["reference_id"], "原Generate→Material引用身份不符")
    mid = material["candidates"][0]["definition"]["definition_id"]
    _require(all(material_ref[k] == ref[k] for k in ("from_definition_id", "source_path", "json_pointer", "target_key"))
        and material_ref["legacy_reference_id"] == ref["reference_id"]
        and material_ref["reference_id"] == fingerprint(["material-reference-v1", ref["from_definition_id"], leaf, "element_ball_material"])
        and material_ref["relation_kind"] == "element_ball_material" and material_ref["target_kind"] == "material_record"
        and material_ref["candidate_ids"] == [mid] and material_ref["resolved_definition_id"] == mid
        and material_ref["resolution_status"] == "resolved" and material["selected_definition_id"] == mid, "Material精确关系不闭合")
    _require(raw.get("useTarget") == "ITEM_USE_TARGET_CUR_TEAM", "非全队粒子材料")
    uses = raw.get("itemUse")
    _require(isinstance(uses, list) and 1 <= len(uses) <= 8, "材料用途数量错误")
    _require(set(uses[0]) == {"useOp", "useParam"} and uses[0]["useOp"] == "ITEM_USE_ADD_ELEM_ENERGY", "材料首用途未知")
    for use in uses[1:]:
        _require(isinstance(use, dict) and set(use) in ({"useParam"}, {"useOp", "useParam"})
            and use.get("useOp", "ITEM_USE_NONE") == "ITEM_USE_NONE" and isinstance(use["useParam"], list)
            and len(use["useParam"]) in (2, 3) and all(v == "" for v in use["useParam"]), "材料额外用途未知")
    params = uses[0]["useParam"]
    _require(isinstance(params, list) and len(params) >= 3 and all(v == "" for v in params[3:]), "材料单位形状未知")
    element = contract["elements"].get(params[0])
    unit = next((u for u in contract["standard_units"] if [str(u["same"]), str(u["different"])] == params[1:3]), None)
    _require(element is not None and unit is not None, "材料不是受审标准能量单位")
    op = query["origin"]["operation"]["raw"]
    base, ratio = op["AOCCEKAGBFB"], op["HHENHNHHHDA"]
    _require(type(base) in (int, float) and type(ratio) in (int, float) and 0 < base <= 10**6 and 0 < ratio <= 10**6
        and int(base) == base and int(ratio) == ratio, "自动来源只覆盖已受审固定整数输入；平均数量使用显式粒球资源")
    product = int(base) * int(ratio)
    _require(product % unit["same"] == 0 and 1 <= product // unit["same"] <= 20, "自动来源仅支持已受审固定整商；平均数量使用显式粒球资源")
    result = {"status": "ready", "count": product // unit["same"], "element": element,
        "particle_type": unit["particle_type"], "unit_reference_fingerprint": query["source_fingerprint"]}
    p = query["reference_projection"]
    _require(p["count"] == result["count"] and p["particle_type"] == result["particle_type"] and p["unit"]["element"] == element,
             "单位投影与原文不一致")
    _require(op["KKMLMJNCLLG"] == raw["id"], "生成材料身份不一致")
    return result


def _source(bundle):
    _require(bundle["graph_scope"] == "energy_trigger_source_v1" and bundle["projection_version"] == 1, "触发来源版本未知")
    _require(bundle["status"] == bundle["source_status"] == "ready", "触发来源资料未就绪")
    _require(bundle["reference_contract"] == reference_contract(), "触发参考版本变化")
    _require(bundle["snapshot"]["upstream_commit"] == recipe()["commit"], "源提交未准入")
    verify_receipt(bundle["snapshot"]["normalization"], commit=bundle["snapshot"]["upstream_commit"])
    _require(fingerprint({k: v for k, v in bundle.items() if k != "content_fingerprint"}) == bundle["content_fingerprint"], "触发完整回执指纹不一致")
    payload = energy_trigger_source_payload(bundle)
    _require(payload == bundle["semantic_dependency"] and fingerprint(payload) == bundle["source_fingerprint"], "触发来源语义指纹不一致")
    query, bridge = bundle["source_query"], bundle["source_bundle"]["parent_bridge"]
    _require(query["snapshot"] == bundle["snapshot"], "触发与单位混合快照")
    entries = all_entries(bundle)
    _require(len(entries) <= LIMITS["max_nodes"], "完整节点预算超限")
    for entry in entries.values():
        _entry(entry, bundle["snapshot"])
    owner = query["origin"]["owner"] or query["origin"]["parent"]
    attack = entries[bridge["attack"]["entry_id"]]
    relative = query["origin"]["operation"]["raw_pointer"][len(owner["json_pointer"]):]
    family = family_for(owner["payload"], attack["payload"], relative)
    _require(family is not None, "完整触发父结构未受审")
    if family["metadata_required"]:
        expected_metadata = "attack." + fingerprint({"source_path": attack["source_path"],
            "ability_name": attack["definition"]["exact_key"], "variant": attack["definition"]["variant_key"],
            **family["metadata_identity"]})
        _require(bundle["parent_identity"]["metadata_candidate_id"] == expected_metadata, "元信息候选不属于受审实际攻击父树")
    else:
        _require(bundle["parent_identity"]["metadata_candidate_id"] is None, "独立标量父桥不接受元信息替身")
    _require(pointer(owner["payload"], relative) == query["origin"]["operation"]["raw"], "生成叶不属于完整owner")
    parameter = bridge["parameter"]
    talent, proud = entries[parameter["writer_entry_id"]], entries[parameter["parameter_entry_id"]]
    _require(any(talent["payload"] == t["raw"] for t in recipe()["fixture_evidence"]["talents"]), "完整Talent包含未受审写者或操作")
    expected_writer = {"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True,
        "IIBMNPFLNMJ": attack["definition"]["exact_key"], "PEOJKHKHAKL": family["parameter_special"],
        "PCABAADALND": f'%{parameter["param_index"] + 1}'}
    writers = [(i, op) for i, op in enumerate(talent["payload"]) if isinstance(op, dict)
        and op.get("IIBMNPFLNMJ") == attack["definition"]["exact_key"] and op.get("PEOJKHKHAKL") == family["parameter_special"]]
    _require(len(writers) == 1 and writers[0][1] == expected_writer, "参数写者未知或不唯一")
    _require(proud["payload"]["openConfig"] == talent["definition"]["exact_key"] and proud["payload"]["level"] == 10
        and 0 <= parameter["param_index"] < len(proud["payload"]["paramList"]), "等级参数与Talent未精确相连")
    identity, selection = bundle["parent_identity"], bundle["selection"]
    _require(identity["version_line"] == bundle["snapshot"]["version_line"] and identity["snapshot_id"] in (None, bundle["snapshot"]["snapshot_id"]), "父来源快照不一致")
    _require(identity["selection"]["constellation"] == 0 and identity["selection"]["talent_level"] == 10
        and "skill" in selection["matching_slots"] and identity["param_index"] == parameter["param_index"], "父动作范围未受审")
    for key in ("character_id", "skill_depot_id", "skill_id", "constellation"):
        _require(identity["selection"][key] == selection[key], "父选择不一致")
    excel = [e for e in entries.values() if e["definition"]["definition_kind"] == "excel_record"]
    def record(table, ident):
        rows = [e["payload"] for e in excel if e["source_path"] == "ExcelBinOutput/" + table + "ExcelConfigData.json" and e["payload"].get("id") == ident]
        _require(len(rows) == 1, "角色技能资料入口不唯一")
        return rows[0]
    avatar = record("Avatar", selection["character_id"])
    depot = record("AvatarSkillDepot", selection["skill_depot_id"])
    skill = record("AvatarSkill", selection["skill_id"])
    _require(selection["skill_depot_id"] in [avatar.get("skillDepotId"), *avatar.get("candSkillDepotIds", [])]
        and depot["skills"][1] == selection["skill_id"] and skill["proudSkillGroupId"] == proud["payload"]["proudSkillGroupId"], "Avatar到参数入口断链")
    refs = bundle["source_bundle"]["references"]
    _require(len(refs) <= LIMITS["max_edges"] and len({r["reference_id"] for r in refs}) == len(refs), "引用预算或唯一性错误")
    for ref in refs:
        origin = entries[ref["from_definition_id"]]
        _require(ref["json_pointer"].startswith(origin["json_pointer"] + "/"), "引用原指针越界")
        _require(pointer(origin["payload"], ref["json_pointer"][len(origin["json_pointer"]):]) == ref["target_key"]
            and fingerprint([ref["from_definition_id"], ref["json_pointer"]]) == ref["reference_id"], "引用原文不一致")
    mount = bridge["mounting"]
    if family["mount_required"]:
        _require(isinstance(mount, dict) and mount["status"] == "ready", "共同挂载未证明")
        entry = entries[mount["entry_id"]]
        _require(pointer(entry["payload"], recipe()["mount"]["new_pointer"]) == recipe()["mount"]["new_raw"], "挂载完整列表变化")
        ids = {owner["definition"]["definition_id"], attack["definition"]["definition_id"]}
        selected = [r for r in refs if r["reference_id"] in mount["exact_reference_ids"]]
        _require({r["resolved_definition_id"] for r in selected} == ids and all(r["from_definition_id"] == mount["entry_id"]
            and r["resolution_status"] == "resolved" and len(r["candidate_ids"]) == 1
            and entries[r["resolved_definition_id"]]["definition"]["exact_key"] == r["target_key"] for r in selected), "挂载引用不闭合")
        _require(pointer(owner["payload"], family["skill_id_path"]) == selection["skill_id"], "挂载控制技能不匹配")
    rebuilt = build_bridge(family, owner=owner, attack=attack, talent=talent, proud=proud,
        param_index=parameter["param_index"], writer_index=writers[0][0],
        metadata_candidate_id=identity["metadata_candidate_id"], mounting=mount)
    _require(rebuilt == bridge, "触发证书与完整原文重建不同")
    return family, bridge, _units(query)


def compile_energy_trigger_source(bundle, *, parent_semantics, scenario):
    out = {"compiler": COMPILER, "compiler_version": 1, "profile_id": PROFILE_ID, "profile_version": 1,
        "status": "pending", "source_status": "pending", "trigger_status": "pending", "whole_source_status": "partial",
        "dispatch_mode": "calibrated_reference", "gameplay_interpretation": "bounded_reference_not_full_ability",
        "source_fingerprint": None, "semantic_dependency": None, "trigger_fingerprint": None,
        "trigger_certificate": None, "resource_projection": {"status": "pending"}, "issues": [], "limits": dict(LIMITS)}
    try:
        _require(len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8")) <= LIMITS["max_bytes"], "触发输入字节预算超限")
        family, bridge, resource = _source(bundle)
        out.update(source_status="ready", source_fingerprint=bundle["source_fingerprint"], resource_projection=resource)
        required = {"kind", "variant", "selection", "param_index", "profile_fingerprint", "source_dependency_fingerprint",
            "metadata_source_fingerprint", "metadata_dependency_fingerprint", "hit_semantics", "application_semantics", "contact_semantics"}
        _require(isinstance(parent_semantics, dict) and set(parent_semantics) == required, "父语义字段未知或缺失")
        _require(parent_semantics["kind"] == "calibrated_single_contact_v1" and parent_semantics["variant"] == family["variant"]
            and parent_semantics["param_index"] == bundle["parent_identity"]["param_index"], "父命中语义与源不一致")
        supplied = parent_semantics["selection"]
        _require(all(supplied.get(k) == v for k, v in bundle["parent_identity"]["selection"].items()), "父命中角色选择不一致")
        for key in ("profile_fingerprint", "source_dependency_fingerprint"):
            _require(isinstance(parent_semantics[key], str) and re.fullmatch("[0-9a-f]{64}", parent_semantics[key]) is not None, "父来源确认身份缺失")
        for key in ("metadata_source_fingerprint", "metadata_dependency_fingerprint"):
            value = parent_semantics[key]
            _require((isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None) if family["metadata_required"] else value is None,
                     "元信息父来源身份不一致")
        hit = parent_semantics["hit_semantics"]
        attack_entry = all_entries(bundle)[bridge["attack"]["entry_id"]]
        attack_property = pointer(attack_entry["payload"],
            bridge["attack"]["attack_property_pointer"][len(attack_entry["json_pointer"]):])
        source_element = {"Wind": "anemo", "Ice": "cryo"}.get(attack_property.get("EPPIDAILPBN"))
        _require(set(hit) == {"damage_system", "scaling_stat", "count", "element", "damage_type_ids"}
            and hit["damage_system"] == "classic" and hit["scaling_stat"] == "atk" and type(hit["count"]) is int and hit["count"] == 1
            and source_element is not None and hit["element"] == source_element == resource["element"]
            and hit["damage_type_ids"] == ["damage.elemental_skill"], "仅支持同元素单次普通E命中")
        defaults = energy_trigger_compiler_caps()["default_scenario"]
        _require(isinstance(scenario, dict) and set(scenario) == set(defaults), "场景声明字段未知或缺失")
        known = all(type(scenario[k]) is type(v) and scenario[k] == v for k, v in defaults.items() if k not in ("hitlag_policy", "status", "source"))
        known = known and scenario["hitlag_policy"] == "zero_hitlag_reference" and scenario["status"] in ("assumed", "confirmed")
        known = known and isinstance(scenario["source"], str) and 0 < len(scenario["source"].strip()) <= 4096
        ticks = int((max(Decimal(str(family["duration"])), Decimal(family["reference_frames"]) / 60) * 1000000).to_integral_value(rounding=ROUND_CEILING))
        certificate = {"family": family["id"], "variant": family["variant"], "controller_key": bridge["controller"]["controller_key"],
            "owner_presence": "frontline", "callback_phase": "hit_post", "predicate_semantics": family["predicate_semantics"],
            "hitlag_policy": "zero_hitlag_reference", "isolation": {"raw_duration_seconds": family["duration"],
                "reference_frames": family["reference_frames"], "reference_fps": 60, "ticks_per_second": 1000000,
                "isolation_ticks": ticks, "comparison": "strict_greater", "policy": "continuous_reference_scope"},
            "whole_source_status": "partial", "unsupported": energy_trigger_compiler_caps()["unsupported"]}
        dependency = {"compiler": COMPILER, "profile_version": 1, "reference_contract": reference_contract(),
            "source_fingerprint": bundle["source_fingerprint"], "parent_semantics": copy.deepcopy(parent_semantics), "scenario": copy.deepcopy(scenario)}
        out.update(trigger_certificate=certificate, semantic_dependency=dependency, trigger_fingerprint=fingerprint(dependency))
        if known:
            out.update(status="ready", trigger_status="ready")
        else:
            out["issues"].append({"code": "energy_trigger_scenario_pending", "path": "scenario", "message": "需明确零hitlag参考与目标、控制器初态及拾取情景"})
    except (KeyError, IndexError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        out["issues"].append({"code": "energy_trigger_source_or_parent_unsupported", "path": "source", "message": str(exc)[:1024]})
    return out
