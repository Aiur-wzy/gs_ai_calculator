"""Finite reviewed resource models; validation alone never authorizes execution."""
from __future__ import annotations

import copy

from genshin_runtime.action_resource_semantics import (
    digest,
    integer,
    obj,
    path,
    require,
    snapshot,
    text,
)
from genshin_runtime.action_root_semantics import bounded
from genshin_runtime.mechanism_source_binding import validate_pointer

CONTRACT = "reviewed-static-action-capacity-v1"
PROFILE_CONTRACT = "fresh-action-capacity-profile-v1"
SCOPE = "static_maximum_only_not_initial_cooldown_alias_or_native_dispatch"
CONTEXT_CONTRACT = "static-action-capacity-context-v1"
MAX_FACTS = 32


def fp(value):
    from genshin_runtime.mechanism_semantics import fingerprint
    return fingerprint(value)


def same(left, right):
    return fp(left) == fp(right)


def sequence(value, low, high):
    require(type(value) is list and low <= len(value) <= high, "容量资料数组超限或缺失")


def ref(value):
    obj(value, {"id", "revision"})
    text(value["id"], 80)
    integer(value["revision"], 1, 2**31 - 1)


def _selection_fields(value):
    obj(value, {"character_id", "skill_depot_id", "skill_id", "constellation", "character_level", "ascension",
                "base_level", "weapon", "artifacts", "team", "variant"})
    for key in ("character_id", "skill_depot_id", "skill_id"):
        integer(value[key])
    integer(value["constellation"], 0, 6)
    integer(value["character_level"], 1, 100)
    integer(value["ascension"], 0, 6)
    integer(value["base_level"], 1, 10)
    obj(value["weapon"], {"id", "level", "refinement"})
    integer(value["weapon"]["id"])
    integer(value["weapon"]["level"], 1, 100)
    integer(value["weapon"]["refinement"], 1, 5)
    require(value["artifacts"] == "all_empty" and value["team"] == "self_only", "容量模型要求明确实际无圣遗物及单人")
    require(value["variant"] in ("default", "tap", "hold"), "容量动作变体须使用规范枚举")


def validate_context(value):
    obj(value, {"contract", "mode", "external_capacity_effects", "window_changes"})
    require(value["contract"] == CONTEXT_CONTRACT and value["mode"] == "ordinary_open_world"
            and value["external_capacity_effects"] == value["window_changes"] == "none", "容量场景未支持或存在变化")


def validate_selection(value, context):
    bounded([value, context])
    _selection_fields(value)
    validate_context(context)


def validate_model(c):
    bounded(c)
    obj(c, {"contract", "qualification", "snapshot", "target", "domain", "materials", "text_inventory",
            "base", "delta_refs", "reviewed_cases", "prior_decision", "scope"})
    require(c["contract"] == CONTRACT and c["qualification"] == "reviewed_game_resource_model"
            and c["scope"] == SCOPE, "容量模型合同或权限范围错误")
    snapshot(c["snapshot"])
    target = c["target"]
    obj(target, {"character_id", "skill_depot_id", "skill_id", "avatar", "depot", "skill", "depot_pointer"})
    for key in ("character_id", "skill_depot_id", "skill_id"):
        integer(target[key])
    for key in ("avatar", "depot", "skill"):
        text(target[key], 80)
    validate_pointer(target["depot_pointer"])
    domain = c["domain"]
    obj(domain, {"constellations", "character_level", "ascension", "base_level", "weapon", "artifacts", "team", "variant", "context"})
    sequence(domain["constellations"], 1, 7)
    for value in domain["constellations"]:
        integer(value, 0, 6)
    require(domain["constellations"] == sorted(set(domain["constellations"])), "命座域重复或无序")
    validate_selection({**{k: target[k] for k in ("character_id", "skill_depot_id", "skill_id")},
        **{k: domain[k] for k in ("character_level", "ascension", "base_level", "weapon", "artifacts", "team", "variant")},
        "constellation": domain["constellations"][0]}, domain["context"])
    materials = c["materials"]
    obj(materials, {"sources", "nodes", "talent_parent", "weapon"})
    sequence(materials["sources"], 1, 16)
    sources = {}
    for source in materials["sources"]:
        obj(source, {"id", "source_path", "source_sha256", "canonical_sha256"})
        text(source["id"], 80)
        path(source["source_path"])
        digest(source["source_sha256"])
        digest(source["canonical_sha256"])
        require(source["source_path"] not in sources, "容量原源重复")
        sources[source["source_path"]] = source
    require(len({s["id"] for s in sources.values()}) == len(sources), "容量证据ID重复")
    sequence(materials["nodes"], 3, 32)
    nodes, locations = {}, set()
    for node in materials["nodes"]:
        obj(node, {"id", "definition_id", "definition_kind", "source_path", "source_sha256", "json_pointer", "node_sha256"})
        text(node["id"], 80)
        path(node["source_path"])
        require(node["source_path"] in sources and node["source_sha256"] == sources[node["source_path"]]["source_sha256"], "节点原源身份错误")
        validate_pointer(node["json_pointer"])
        digest(node["node_sha256"])
        require(node["definition_kind"] in ("excel_record", "talent_config"), "容量原节点类别未支持")
        require(node["definition_id"] == fp([c["snapshot"]["snapshot_id"], node["source_path"], node["json_pointer"], node["definition_kind"]]), "容量节点索引身份错误")
        location = (node["source_path"], node["json_pointer"], node["definition_kind"])
        require(node["id"] not in nodes and location not in locations, "容量原节点重复")
        nodes[node["id"]] = node
        locations.add(location)
    require(all(target[k] in nodes for k in ("avatar", "depot", "skill")), "容量目标完整原行缺失")
    require(materials["weapon"] in nodes, "实际武器原行缺失")
    require(materials["talent_parent"] in sources, "容量完整Talent原父未绑定")
    inventory = c["text_inventory"]
    sequence(inventory, 1, 256)
    seen = set()
    for item in inventory:
        obj(item, {"node_id", "field_pointer", "text_id", "status", "text_sha256", "source_path"})
        require(item["node_id"] in nodes and item["source_path"] in sources, "原文引用源缺失")
        validate_pointer(item["field_pointer"])
        integer(item["text_id"], 0)
        require(item["status"] in ("present", "missing"), "原文presence未知")
        if item["status"] == "present":
            digest(item["text_sha256"])
        else:
            require(item["text_sha256"] is None, "缺失原文不能有内容摘要")
        key = (item["node_id"], item["field_pointer"])
        require(key not in seen, "原文引用重复")
        seen.add(key)
    obj(c["base"], {"node_id", "pointer", "value"})
    require(c["base"]["node_id"] == target["skill"] and c["base"]["pointer"] == "/maxChargeNum", "基础次数字段不正确")
    integer(c["base"]["value"], 1, 100)
    sequence(c["delta_refs"], 0, 16)
    for item in c["delta_refs"]:
        ref(item)
    require(len({r["id"] for r in c["delta_refs"]}) == len(c["delta_refs"]), "增量引用重复")
    sequence(c["reviewed_cases"], 1, 7)
    for case in c["reviewed_cases"]:
        obj(case, {"constellation", "capacity"})
        integer(case["constellation"], 0, 6)
        integer(case["capacity"], 1, 100)
    require([x["constellation"] for x in c["reviewed_cases"]] == domain["constellations"], "受审容量分区不完整或重复")
    prior = c["prior_decision"]
    obj(prior, {"proposal_sha256", "selection_sha256", "decision_sha256", "report_sha256", "freeze_sha256", "snapshot_id", "variant_mapping"})
    for key in set(prior) - {"variant_mapping"}:
        digest(prior[key])
    mapping = prior["variant_mapping"]
    require(same(mapping, {"prior_contract": "finite-game-capacity-review-proposal-v1", "prior_value": "press",
                          "canonical_value": "tap", "meaning": "点按"}) and domain["variant"] == "tap", "首片只支持已审点按材料的明确格式映射")


def evidence_for(c):
    return [{"id": source["id"], "kind": "runtime_node", "locator": source["source_path"] + "#",
             "sha256": source["source_sha256"]} for source in c["materials"]["sources"]] + [
        {"id": "prior-decision", "kind": "reference_implementation", "locator": "review:finite-game-capacity-model",
         "sha256": c["prior_decision"]["decision_sha256"]}]


def selection_matches(c, selection, context):
    validate_selection(selection, context)
    d = c["domain"]
    return (all(same(selection[k], c["target"][k]) for k in ("character_id", "skill_depot_id", "skill_id"))
            and selection["constellation"] in d["constellations"]
            and all(same(selection[k], d[k]) for k in ("character_level", "ascension", "base_level", "weapon", "artifacts", "team", "variant"))
            and same(context, d["context"]))


def text_references(nodes):
    """Enumerate every original text field, including zero and missing references."""
    bounded(nodes)
    result = []
    def walk(node_id, value, pointer=""):
        if type(value) is dict:
            for key, child in value.items():
                location = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if key.lower().endswith("textmaphash"):
                    integer(child, 0)
                    result.append((node_id, location, child))
                elif key == "paramDescList":
                    require(type(child) is list, "参数描述原字段必须是列表")
                    for index, text_id in enumerate(child):
                        integer(text_id, 0)
                        result.append((node_id, location + "/" + str(index), text_id))
                elif type(child) in (dict, list):
                    walk(node_id, child, location)
                require(len(result) <= 256, "完整原文引用超过256项")
        elif type(value) is list:
            for index, child in enumerate(value):
                walk(node_id, child, pointer + "/" + str(index))
    for node_id, raw in nodes.items():
        walk(node_id, raw)
    return sorted(result)


def verify_material(c, material):
    """Recheck bounded saved full nodes; original-byte freshness is a DB-reader duty."""
    from genshin_db.runtime_catalog import resolve_pointer
    validate_model(c)
    bounded(material)
    obj(material, {"snapshot", "sources", "nodes", "talent_parent", "texts"})
    require(same(material["snapshot"], c["snapshot"]) and same(material["sources"], c["materials"]["sources"]), "容量材料快照/原源不符")
    require(len(material["nodes"]) == len(c["materials"]["nodes"]), "完整节点数量不符")
    nodes = {}
    for expected, node in zip(c["materials"]["nodes"], material["nodes"], strict=True):
        obj(node, {"binding", "raw"})
        require(same(node["binding"], expected) and fp(node["raw"]) == expected["node_sha256"], "容量完整原节点不符")
        nodes[expected["id"]] = node["raw"]
    parent_source = next(x for x in material["sources"] if x["source_path"] == c["materials"]["talent_parent"])
    require(fp(material["talent_parent"]) == parent_source["canonical_sha256"], "容量完整Talent原父不符")
    t = c["target"]
    avatar, depot, skill = (nodes[t[k]] for k in ("avatar", "depot", "skill"))
    require(type(avatar) is dict and type(depot) is dict and type(skill) is dict, "容量目标原行不是对象")
    require(same(avatar.get("id"), t["character_id"]) and same(depot.get("id"), t["skill_depot_id"])
            and same(skill.get("id"), t["skill_id"]), "容量目标ID与完整原行不符")
    depots = [avatar.get("skillDepotId"), *avatar.get("candSkillDepotIds", [])]
    require(all(type(x) is int for x in depots) and t["skill_depot_id"] in depots, "容量角色与技能组关系不符")
    require(same(resolve_pointer(depot, t["depot_pointer"]), t["skill_id"]), "容量目标技能槽不符")
    require(same(resolve_pointer(skill, c["base"]["pointer"]), c["base"]["value"]), "容量基础最大次数不符")
    require(type(nodes[c["materials"]["weapon"]]) is dict
            and same(nodes[c["materials"]["weapon"]].get("id"), c["domain"]["weapon"]["id"]), "容量武器原行与范围不符")
    require(text_references(nodes) == sorted((x["node_id"], x["field_pointer"], x["text_id"]) for x in c["text_inventory"]),
            "完整原节点中的原文引用清单不完整")
    require(len(material["texts"]) == len(c["text_inventory"]), "完整原文presence清单缺失")
    for expected, row in zip(c["text_inventory"], material["texts"], strict=True):
        obj(row, {"binding", "text"})
        require(same(row["binding"], expected)
                and same(resolve_pointer(nodes[expected["node_id"]], expected["field_pointer"]), expected["text_id"]), "原文引用与原字段不符")
        require((type(row["text"]) is str and fp(row["text"]) == expected["text_sha256"])
                if expected["status"] == "present" else row["text"] is None, "原文内容/presence不符")
    return copy.deepcopy(material)
