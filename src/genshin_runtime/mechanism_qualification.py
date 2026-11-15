"""Bounded, data-only execution qualification; consistency is not DB authority."""
from __future__ import annotations

import copy

from genshin_runtime.mechanism_semantics import (
    LIMITS,
    _id,
    _list,
    _number,
    _object,
    _sha,
    _text,
    fingerprint,
    finite_json,
    require,
    validate_descriptor,
)

MAX_QUALIFICATION_BYTES = 32 * 1024
SCENARIO_REQUIREMENTS = {
    "target_kind": "ordinary_non_animal_monster", "party_mode": "single_player_fixed",
    "all_party_alive": True, "all_party_in_apply_predicate": True,
    "all_party_in_retention_predicate": True, "initial_effect": "none",
    "no_external_source_changes": True, "hitlag_policy": "zero_hitlag_reference",
    "origin_target_in_source_range": True, "recipient_bonus_policy": "not_included",
    "stat_basis": "fixed_panel_nonextra_equals_total",
    "event_order_policy": "declared_atomic_hit_order",
    "no_source_state_changes": True,
}
CONTACT_SCENARIO_REQUIREMENTS = {**SCENARIO_REQUIREMENTS, "no_provider_transformative_damage": True}


def _integer(value, low, high, name):
    require(type(value) is int and low <= value <= high, name + "须为范围内非布尔整数")


def _choices(value, allowed, name, minimum=1):
    _list(value, minimum, len(allowed))
    require(all(type(v) is str and v in allowed for v in value), name + "含未知类别")
    require(len(set(value)) == len(value), name + "含重复类别")


def validate_execution_qualification(claim):
    """Validate the single typed fact's claim, returning an isolated JSON value."""
    from genshin_engine.constants import ELEMENTS
    from genshin_engine.registry import DAMAGE_TYPES
    from genshin_engine.rotation_schema import ACTION_TYPES

    finite_json(claim, MAX_QUALIFICATION_BYTES)
    if type(claim) is dict and type(claim.get("schema_version")) is int and claim["schema_version"] == 3:
        return _validate_contact_qualification(claim)
    if type(claim) is dict and type(claim.get("schema_version")) is int and claim["schema_version"] == 2:
        return _validate_cast_qualification(claim)
    _object(claim, ("schema_version", "parent", "coverage", "consumer",
                    "scenario_requirements", "duplicate_identity"))
    _integer(claim["schema_version"], 1, 1, "资格版本")
    parent = claim["parent"]
    _object(parent, ("profile_kind", "selection", "required_receipts", "parameter_binding", "hit"))
    require(parent["profile_kind"] == "calibrated_single_contact_v1", "父派发形状未支持")
    selection = parent["selection"]
    _object(selection, ("slot", "constellation"), ("talent_level", "skill_id", "variant"))
    require(selection["slot"] in ("normal", "skill", "burst"), "父技能槽错误")
    _integer(selection["constellation"], 0, 6, "父命座")
    if "talent_level" in selection:
        _integer(selection["talent_level"], 1, 15, "父天赋等级")
    if "skill_id" in selection:
        _integer(selection["skill_id"], 1, 2**53 - 1, "父技能ID")
    if "variant" in selection:
        _id(selection["variant"])
    receipts = parent["required_receipts"]
    _choices(receipts, ("action_event", "hit_parameter", "attack_metadata"), "父来源回执")
    require("action_event" in receipts, "资格必须核对父动作派发回执")
    binding = parent["parameter_binding"]
    if binding is not None:
        _object(binding, ("node_id", "pointer", "param_index"))
        _id(binding["node_id"])
        _integer(binding["param_index"], 0, 63, "父参数序号")
        require(binding["pointer"] == "/paramList/" + str(binding["param_index"]), "父参数指针与序号不一致")
        require("hit_parameter" in receipts, "父参数绑定缺倍率来源回执")
    hit = parent["hit"]
    _object(hit, ("damage_systems", "count", "scaling_stats", "elements", "damage_type_ids"))
    _choices(hit["damage_systems"], ("classic", "transformative"), "父伤害系统")
    _integer(hit["count"], 1, 1, "父单次命中数")
    _choices(hit["scaling_stats"], ("atk", "def", "hp", "em", "none"), "父取样属性")
    _choices(hit["elements"], ELEMENTS, "父元素")
    _choices(hit["damage_type_ids"], DAMAGE_TYPES, "父伤害类型")
    coverage = claim["coverage"]
    _object(coverage, ("action_filter", "application_elements", "reject_unknown_application"))
    filters = coverage["action_filter"]
    if filters is not None:
        _object(filters, (), ("action_type_ids", "damage_type_ids", "elements"))
        for key, values in filters.items():
            _choices(values, {"action_type_ids": ACTION_TYPES, "damage_type_ids": DAMAGE_TYPES,
                              "elements": ELEMENTS}[key], "父覆盖." + key)
    _choices(coverage["application_elements"], ELEMENTS, "附着覆盖", minimum=0)
    require(filters is not None or bool(coverage["application_elements"]), "资格覆盖范围不能全部关闭")
    require(coverage["reject_unknown_application"] is True, "资格不能放行未知附着")
    _validate_shared_scope(claim)
    return copy.deepcopy(claim)


def _validate_shared_scope(claim):
    from genshin_engine.registry import DAMAGE_TYPES

    consumer = claim["consumer"]
    if consumer is not None:
        _object(consumer, ("damage_systems", "count", "damage_type_ids"))
        _choices(consumer["damage_systems"], ("classic", "transformative"), "消费者伤害系统")
        _integer(consumer["count"], 1, 1, "消费者单次命中数")
        _choices(consumer["damage_type_ids"], DAMAGE_TYPES, "消费者伤害类型")
    scene = claim["scenario_requirements"]
    scene_contract = CONTACT_SCENARIO_REQUIREMENTS if claim["schema_version"] == 3 else SCENARIO_REQUIREMENTS if claim["schema_version"] == 2 else {
        k: v for k, v in SCENARIO_REQUIREMENTS.items() if k != "no_source_state_changes"}
    _object(scene, (), scene_contract)
    require(bool(scene), "资格必须声明受审情景要求")
    for key, value in scene.items():
        require(type(value) is type(scene_contract[key]) and value == scene_contract[key], "未支持情景要求：" + key)
    duplicate = claim["duplicate_identity"]
    _object(duplicate, ("kind", "source_id", "parts"))
    require(duplicate["kind"] in ("skill", "proud", "weapon", "artifact_set"), "同源类别未支持")
    _integer(duplicate["source_id"], 1, 2**53 - 1, "同源ID")
    _list(duplicate["parts"], 1, 8)
    for part in duplicate["parts"]:
        _id(part)
    require(len(set(duplicate["parts"])) == len(duplicate["parts"]), "同源机制部件重复")


def _validate_cast_qualification(claim):
    from genshin_runtime.mechanism_source_binding import validate_pointer

    _object(claim, ("schema_version", "parent", "provider", "target_dependency", "coverage",
                    "consumer", "scenario_requirements", "duplicate_identity"))
    parent = claim["parent"]
    _object(parent, ("profile_kind", "selection", "required_receipts", "callback_bridge", "activation"))
    profile_kind = parent["profile_kind"]
    require(profile_kind in ("successful_action_v1", "successful_action_v2"), "施放资格父类别未支持")
    selection = parent["selection"]
    _object(selection, ("slot", "constellation"), ("talent_level", "skill_id", "variant"))
    require(selection["slot"] in ("skill", "burst"), "施放资格技能槽未支持")
    _integer(selection["constellation"], 0, 6, "父命座")
    if "talent_level" in selection:
        _integer(selection["talent_level"], 1, 15, "父天赋等级")
    if "skill_id" in selection:
        _integer(selection["skill_id"], 1, 2**53 - 1, "父技能ID")
    if "variant" in selection:
        _id(selection["variant"])
    if profile_kind == "successful_action_v1":
        require(parent["required_receipts"] == ["cast_identity"], "施放资格必须核对独立施放身份")
    else:
        require(selection["slot"] == "burst", "接纳施放资格首片仅支持元素爆发")
        require(parent["required_receipts"] == ["cast_identity", "action_admission"],
                "接纳施放资格必须恰好核对施放身份与动作接纳回执")
    bridge = parent["callback_bridge"]
    require(type(bridge) is dict, "缺施放回调证据")
    kind = bridge.get("kind")
    require(kind in ("skill_start_callback", "owner_burst_listener"), "施放回调类别未支持")
    keys = ("kind", "node_id", "callback_pointer", "apply_pointers")
    _object(bridge, (*keys, "skill_id_pointer") if kind == "skill_start_callback" else keys)
    _id(bridge["node_id"])
    validate_pointer(bridge["callback_pointer"])
    require(bool(bridge["callback_pointer"]), "回调必须定位完整父节点内子树")
    _list(bridge["apply_pointers"], 1, 8)
    pointers = bridge["apply_pointers"]
    if kind == "skill_start_callback":
        require("skill_id" in selection, "技能回调缺精确技能scope")
        pointers = [*pointers, bridge["skill_id_pointer"]]
    else:
        require(selection["slot"] == "burst", "拥有者爆发回调仅能绑定真实爆发槽")
    for pointer in pointers:
        validate_pointer(pointer)
        require(pointer.startswith(bridge["callback_pointer"] + "/"), "操作或技能指针不在回调子树内")
    require(len(set(pointers)) == len(pointers), "回调证据指针重复")
    activation = parent["activation"]
    _object(activation, ("phase", "offset_policy", "cancellation"))
    require(activation["phase"] in ("pre", "post")
            and activation["offset_policy"] in ("calibrated", "fixed_zero")
            and activation["cancellation"] == "reject_before_activation", "施放生效参考不受支持")
    provider = claim["provider"]
    require(type(provider) is dict, "缺提供者资格")
    if provider.get("relation") == "source_character":
        _object(provider, ("relation",))
    else:
        _object(provider, ("relation", "set_node_id", "affix_node_id", "required_count", "affix_id", "source_level", "requirement_coverage"))
        require(provider["relation"] == "equipped_artifact_set", "提供者关系未支持")
        for key in ("set_node_id", "affix_node_id"):
            _id(provider[key])
        require(provider["set_node_id"] != provider["affix_node_id"], "装备证明节点不能重用")
        _integer(provider["required_count"], 1, 5, "套装件数")
        _integer(provider["affix_id"], 1, 2**53 - 1, "装备效果ID")
        _integer(provider["source_level"], 0, 63, "装备效果级")
        require(provider["requirement_coverage"] in ("partial", "complete_selected_effect_reference"),
                "装备效果覆盖声明未支持")
    target = claim["target_dependency"]
    require(type(target) is dict, "缺目标依赖")
    if target.get("mode") == "none":
        _object(target, ("mode",))
        require(claim["consumer"] is None, "无目标来源不能有消费者资格")
        require(type(claim["scenario_requirements"]) is dict and not
                {"target_kind", "origin_target_in_source_range"} & claim["scenario_requirements"].keys(),
                "无目标来源含目标情景要求")
    else:
        _object(target, ("mode", "ruleset"))
        require(target["mode"] == "ordinary_single_target_reference"
                and target["ruleset"] == "gauge-freeze-reference-v1", "目标依赖未支持")
    coverage = claim["coverage"]
    _object(coverage, ("mode", "action_type_ids", "skill_scope"))
    expected = "action.elemental_skill" if selection["slot"] == "skill" else "action.elemental_burst"
    require(coverage["mode"] == "all_provider_actions" and coverage["skill_scope"] == "parent_selection"
            and coverage["action_type_ids"] == [expected], "施放覆盖必须完整匹配父技能槽")
    _validate_shared_scope(claim)
    return copy.deepcopy(claim)


def qualification_node_ids(claim):
    """Nodes which the qualification fact must cite; no runtime authority."""
    q = validate_execution_qualification(claim)
    if q["schema_version"] == 1:
        binding = q["parent"]["parameter_binding"]
        return [] if binding is None else [binding["node_id"]]
    ids = [q["parent"]["callback_bridge"]["node_id"]]
    if q["provider"]["relation"] == "equipped_artifact_set":
        ids.extend(q["provider"][key] for key in ("set_node_id", "affix_node_id"))
    elif q["provider"]["relation"] == "equipped_weapon":
        ids.extend(q["provider"][key] for key in ("weapon_node_id", "affix_node_id"))
    return list(dict.fromkeys(ids))


def _evidenced_node(source_proof, node_id, evidence_refs):
    require(node_id in evidence_refs, "施放或装备节点未获资格事实引用")
    require(type(source_proof) is dict, "缺完整来源证明")
    nodes, evidence = source_proof.get("nodes"), source_proof.get("evidence")
    _list(nodes, 1, LIMITS["nodes"])
    _list(evidence, 1, 64)
    matches = [n for n in nodes if type(n) is dict and n.get("id") == node_id]
    require(len(matches) == 1, "施放或装备节点不存在或不唯一")
    node = matches[0]
    _object(node, ("id", "source_path", "source_sha256", "json_pointer", "node_sha256", "payload"))
    from genshin_runtime.mechanism_source_binding import validate_pointer
    _text(node["source_path"], 512)
    _sha(node["source_sha256"])
    validate_pointer(node["json_pointer"])
    require(node["node_sha256"] == fingerprint(node["payload"]), "施放或装备完整原节点摘要失配")
    refs = [e for e in evidence if type(e) is dict and e.get("id") == node_id]
    require(len(refs) == 1 and refs[0].get("kind") == "runtime_node"
            and refs[0].get("sha256") == node["node_sha256"]
            and refs[0].get("locator") == node["source_path"] + "#" + node["json_pointer"], "缺完整原节点定位证据")
    return node["payload"]


def _verify_cast_qualification(q, descriptor, proof, evidence_refs):
    from genshin_engine.registry import BENEFIT_TYPES
    from genshin_runtime.mechanism_source_binding import resolve_pointer

    parent = q["parent"]
    bridge = parent["callback_bridge"]
    raw = _evidenced_node(proof, bridge["node_id"], evidence_refs)
    callback = resolve_pointer(raw, bridge["callback_pointer"])
    require(type(callback) is list and bool(callback), "回调证据必须为完整非空操作数组")
    for pointer in bridge["apply_pointers"]:
        operation = resolve_pointer(raw, pointer)
        require(type(operation) is dict and type(operation.get("$type")) is str and bool(operation["$type"]),
                "施加证据不是原始操作对象")
    if bridge["kind"] == "skill_start_callback":
        skill = resolve_pointer(raw, bridge["skill_id_pointer"])
        _integer(skill, 1, 2**53 - 1, "回调原始技能ID")
        require(skill == parent["selection"]["skill_id"], "回调原始技能与父scope不一致")
    subject = proof.get("subject")
    _object(subject, ("target_type", "target_id"))
    _integer(subject["target_id"], 1, 2**53 - 1, "来源主体ID")
    provider = q["provider"]
    if provider["relation"] == "source_character":
        require(subject["target_type"] == "character", "角色提供者与来源主体不符")
        require(q["duplicate_identity"]["kind"] in ("skill", "proud"), "角色来源不能借装备同源身份")
        if q["duplicate_identity"]["kind"] == "skill" and "skill_id" in parent["selection"]:
            require(q["duplicate_identity"]["source_id"] == parent["selection"]["skill_id"], "同源技能与父scope不一致")
    else:
        require(subject["target_type"] == "artifact_set", "装备提供者与来源主体不符")
        s = _evidenced_node(proof, provider["set_node_id"], evidence_refs)
        a = _evidenced_node(proof, provider["affix_node_id"], evidence_refs)
        require(type(s) is dict and type(a) is dict, "装备证明缺完整Excel记录")
        for obj, key in ((s, "setId"), (s, "EquipAffixId"), (a, "id"), (a, "affixId")):
            _integer(obj.get(key), 1, 2**53 - 1, "装备原始." + key)
        _integer(a.get("level"), 0, 63, "装备原始.level")
        counts = s.get("setNeedNum")
        _list(counts, 1, 64)
        for count in counts:
            _integer(count, 1, 5, "装备原始件数")
        require(s["setId"] == subject["target_id"] and s["EquipAffixId"] == a["id"]
                and a["affixId"] == provider["affix_id"] and a["level"] == provider["source_level"]
                and a["level"] < len(counts) and counts[a["level"]] == provider["required_count"],
                "套装效果/件数/等级原始关系不一致")
        require(q["duplicate_identity"]["kind"] == "artifact_set"
                and q["duplicate_identity"]["source_id"] == subject["target_id"], "装备同源身份不一致")
    for item in descriptor["rule_templates"]:
        rule = item["rule"]
        trigger = rule["trigger"]
        require(trigger["event"] == "action_start" and trigger["phase"] == parent["activation"]["phase"],
                "施放规则与受审触发相位不一致")
        require(not set(trigger) - {"event", "phase", "action_type_ids", "owner_presence"},
                "施放触发不得依赖伤害或目标筛选")
        if "action_type_ids" in trigger:
            require(trigger["action_type_ids"] == q["coverage"]["action_type_ids"], "规则动作覆盖与资格不符")
        delay_ports = [p for p in item["execution_ports"] if p["target"] == "timing.delay"]
        if parent["activation"]["offset_policy"] == "calibrated":
            require(len(delay_ports) == 1, "施放生效缺校准端口")
        else:
            require(not delay_ports and type(rule["timing"].get("delay")) in (int, float)
                    and rule["timing"]["delay"] == 0, "零延迟资格必须为固定零值")
        if "quota" in rule:
            require(q["consumer"] is not None, "次数规则缺消费者资格")
        if q["consumer"] is not None and any(e["benefit_type_id"].startswith("benefit.base_add.") for e in rule["effects"]):
            require(q["consumer"]["damage_systems"] == ["classic"], "基础加算消费者仅支持经典伤害")
        if q["target_dependency"]["mode"] == "none":
            require("quota" not in rule and "single_target" not in item["requirements"], "无目标资格含目标或次数要求")
            for effect in rule["effects"]:
                require(effect["domain"] == "self" and "filter" not in effect
                        and BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") in ("stat_add", "stat_pct", "em"),
                        "无目标资格仅支持无筛选自身/队伍属性贡献")
    return q


def verify_execution_qualification(claim, *, descriptor, source_proof, evidence_refs):
    """Check rule/source consistency only; approval and freshness stay external."""
    finite_json([claim, descriptor, source_proof, evidence_refs], LIMITS["input_bytes"])
    result = validate_execution_qualification(claim)
    d = validate_descriptor(descriptor)
    _list(evidence_refs, 1, 10)
    for ref in evidence_refs:
        _id(ref)
    require(len(set(evidence_refs)) == len(evidence_refs), "资格证据引用重复")
    if result["schema_version"] == 2:
        return _verify_cast_qualification(result, d, source_proof, evidence_refs)
    if result["schema_version"] == 3:
        return _verify_contact_qualification(result, d, source_proof, evidence_refs)
    parent, consumer = result["parent"], result["consumer"]
    receipts, binding = parent["required_receipts"], parent["parameter_binding"]
    for item in d["rule_templates"]:
        rule = item["rule"]
        if rule["trigger"]["event"] == "elemental_reaction":
            require("attack_metadata" in receipts, "反应触发缺父攻击元信息回执")
        if rule["trigger"]["event"] == "action_start":
            require(rule["trigger"]["phase"] == "pre", "旧资格不支持新施放相位")
            require("hit_parameter" in receipts and binding is not None, "动作触发缺精确父参数绑定")
        if "quota" in rule:
            require(consumer is not None, "次数规则缺消费者资格")
        if consumer is not None and any(e["benefit_type_id"].startswith("benefit.base_add.") for e in rule["effects"]):
            require(consumer["damage_systems"] == ["classic"], "基础加算消费者仅支持经典伤害")
    if binding is not None:
        require(binding["node_id"] in evidence_refs, "父参数节点未获资格事实证据引用")
        require(type(source_proof) is dict, "缺物化来源证明")
        nodes, evidence = source_proof.get("nodes"), source_proof.get("evidence")
        _list(nodes, 1, LIMITS["nodes"])
        _list(evidence, 1, 64)
        matches = [n for n in nodes if type(n) is dict and n.get("id") == binding["node_id"]]
        require(len(matches) == 1, "父参数节点不存在或不唯一")
        node = matches[0]
        _object(node, ("id", "source_path", "source_sha256", "json_pointer", "node_sha256", "payload"))
        _text(node["source_path"], 512)
        _sha(node["source_sha256"])
        require(type(node["json_pointer"]) is str and (not node["json_pointer"] or node["json_pointer"].startswith("/")), "父参数完整节点定位错误")
        require(node["node_sha256"] == fingerprint(node["payload"]), "父参数原节点摘要失配")
        matching_evidence = [e for e in evidence if type(e) is dict and e.get("id") == binding["node_id"]]
        require(len(matching_evidence) == 1 and matching_evidence[0].get("kind") == "runtime_node"
                and matching_evidence[0].get("sha256") == node["node_sha256"]
                and matching_evidence[0].get("locator") == node["source_path"] + "#" + node["json_pointer"], "父参数缺完整原节点证据")
        raw = node["payload"]
        require(type(raw) is dict and type(raw.get("paramList")) is list
                and binding["param_index"] < len(raw["paramList"]), "父参数原节点没有精确参数位置")
        _number(raw["paramList"][binding["param_index"]])
    return result


def _validate_contact_qualification(claim):
    from genshin_engine.registry import DAMAGE_TYPES
    from genshin_runtime.mechanism_source_binding import validate_pointer

    _object(claim, ("schema_version", "parent", "provider", "target_dependency", "coverage",
                    "consumer", "scenario_requirements", "duplicate_identity"))
    parent = claim["parent"]
    _object(parent, ("profile_kind", "allowed_profile_kinds", "selection", "required_receipts",
                     "hit", "callback_bridge", "activation"))
    require(parent["profile_kind"] == "actual_contact_v1", "接触父类别未支持")
    _choices(parent["allowed_profile_kinds"], ("calibrated_single_contact_v1", "calibrated_multi_contact_v1"), "实际派发类别")
    selection = parent["selection"]
    _object(selection, ("constellation", "talent_level"))
    _integer(selection["constellation"], 0, 0, "接触父命座")
    _integer(selection["talent_level"], 10, 10, "接触父等级")
    require(parent["required_receipts"] == ["contact_identity"], "接触资格缺新鲜父链")
    hit = parent["hit"]
    _object(hit, ("damage_systems", "count", "scaling_stats", "elements", "damage_type_ids"))
    require(hit["damage_systems"] == ["classic"] and hit["scaling_stats"] == ["atk"], "当前接触仅准经典攻击倍率")
    _integer(hit["count"], 1, 1, "接触逐事件count")
    _choices(hit["elements"], ("physical", "hydro", "cryo", "anemo"), "已核接触元素")
    _choices(hit["damage_type_ids"], ("damage.elemental_skill", "damage.elemental_burst"), "已核接触伤害类别")
    bridge = parent["callback_bridge"]
    _object(bridge, ("kind", "node_id", "mount_pointer", "handler_pointer", "callback_pointer",
                     "predicate_pointers", "apply_pointers", "hit_filter"))
    require(bridge["kind"] == "owner_attack_landed", "接触回调类别未支持")
    _id(bridge["node_id"])
    for key in ("mount_pointer", "handler_pointer", "callback_pointer"):
        validate_pointer(bridge[key])
        require(bool(bridge[key]), "接触桥必须指向完整父内部子树")
    require(bridge["callback_pointer"].rsplit("/", 1)[0] == bridge["handler_pointer"], "回调不直接属于完整handler")
    pointers = []
    for key in ("predicate_pointers", "apply_pointers"):
        _list(bridge[key], 1, 16)
        for pointer in bridge[key]:
            validate_pointer(pointer)
            require(pointer.startswith(bridge["callback_pointer"] + "/"), "回调操作不属于受审父节点")
            pointers.append(pointer)
    require(len(set(pointers)) == len(pointers), "接触证据指针重复")
    filters = bridge["hit_filter"]
    _object(filters, (), ("elements", "damage_type_ids"))
    require(bool(filters), "接触回调必须有完整来源筛选")
    for key, values in filters.items():
        _choices(values, ("pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro", "physical")
                 if key == "elements" else DAMAGE_TYPES, "完整回调筛选")
    activation = parent["activation"]
    _object(activation, ("phase", "offset_policy"))
    require(activation == {"phase": "post", "offset_policy": "fixed_zero"}, "接触收益仅支持该击之后零延迟参考")
    coverage = claim["coverage"]
    _object(coverage, ("mode", "scope", "reject_unknown_contact"))
    require(coverage["mode"] == "all_provider_contacts" and coverage["scope"] == "callback_hit_filter"
            and coverage["reject_unknown_contact"] is True, "不能忽略覆盖范围内未知接触")
    target = claim["target_dependency"]
    _object(target, ("mode", "ruleset"))
    require(target == {"mode": "ordinary_single_target_reference", "ruleset": "gauge-freeze-reference-v1"}, "接触目标范围未支持")
    require(claim["consumer"] is None, "接触首片无次数消费者")
    provider = claim["provider"]
    require(type(provider) is dict, "缺实际装备提供者关系")
    common = ("relation", "affix_node_id", "affix_id", "source_level", "requirement_coverage")
    if provider.get("relation") == "equipped_artifact_set":
        _object(provider, (*common, "set_node_id", "required_count"))
        _integer(provider["required_count"], 1, 5, "套装件数")
        _id(provider["set_node_id"])
        require(provider["set_node_id"] != provider["affix_node_id"], "装备来源节点重用")
    else:
        _object(provider, (*common, "weapon_node_id", "refinement"))
        require(provider["relation"] == "equipped_weapon", "接触提供者关系未支持")
        _integer(provider["refinement"], 1, 5, "武器精炼")
        _id(provider["weapon_node_id"])
        require(provider["weapon_node_id"] != provider["affix_node_id"], "装备来源节点重用")
        require(provider["source_level"] == provider["refinement"] - 1, "效果级与精炼不一致")
    _id(provider["affix_node_id"])
    _integer(provider["affix_id"], 1, 2**53 - 1, "装备效果ID")
    _integer(provider["source_level"], 0, 63, "装备效果级")
    require(provider["requirement_coverage"] in ("partial", "complete_selected_effect_reference"), "装备效果覆盖声明未支持")
    _validate_shared_scope(claim)
    return copy.deepcopy(claim)


def _verify_contact_qualification(q, descriptor, proof, evidence_refs):
    from genshin_runtime.mechanism_source_binding import resolve_pointer

    bridge, provider = q["parent"]["callback_bridge"], q["provider"]
    raw = _evidenced_node(proof, bridge["node_id"], evidence_refs)
    mount = resolve_pointer(raw, bridge["mount_pointer"])
    handler = resolve_pointer(raw, bridge["handler_pointer"])
    callback = resolve_pointer(raw, bridge["callback_pointer"])
    require(type(raw) is dict and type(mount) is dict and type(mount.get("$type")) is str,
            "接触挂载必须为完整Ability内部操作")
    handler_name = bridge["handler_pointer"].rsplit("/", 1)[1].replace("~1", "/").replace("~0", "~")
    require(handler_name in [v for v in mount.values() if type(v) is str], "挂载操作未引用该handler")
    require(type(handler) is dict and type(callback) is list and bool(callback), "缺完整handler或回调数组")
    groups = {}
    for key in ("predicate_pointers", "apply_pointers"):
        seen_groups = {}
        for pointer in bridge[key]:
            operation = resolve_pointer(raw, pointer)
            require(type(operation) is dict and type(operation.get("$type")) is str and bool(operation["$type"]),
                    "接触原文指针必须定位完整typed操作")
            container, index = pointer.rsplit("/", 1)
            sequence = resolve_pointer(raw, container)
            require(type(sequence) is list and index.isdigit(), "接触证据必须完整枚举原文操作数组")
            seen_groups.setdefault(container, set()).add(int(index))
        for container, indices in seen_groups.items():
            sequence = resolve_pointer(raw, container)
            require(indices == set(range(len(sequence))), "接触证据遗漏同组原文操作")
            branch = container.rsplit("/", 1)[0]
            require(branch.rsplit("/", 1)[0] == bridge["callback_pointer"], "接触操作必须属于完整回调分支")
        groups[key] = seen_groups
    require(not set(groups["predicate_pointers"]) & set(groups["apply_pointers"]), "谓词与施加操作不能重用同一原文数组")
    expected_branches = {bridge["callback_pointer"] + "/" + str(i) for i in range(len(callback))}
    for grouped in groups.values():
        require({p.rsplit("/", 1)[0] for p in grouped} == expected_branches, "完整接触回调存在未认证分支")
    # Typed, reviewed facts interpret these pointers. Slot labels cannot prove raw attack tags.
    subject = proof["subject"]
    affix = _evidenced_node(proof, provider["affix_node_id"], evidence_refs)
    require(type(affix) is dict, "缺完整效果原文")
    for key in ("id", "affixId"):
        _integer(affix.get(key), 1, 2**53 - 1, "效果原文身份")
    level = affix.get("level", 0)  # Excel level 0 is omitted in the exact original record.
    _integer(level, 0, 63, "效果原文级别")
    require(affix["affixId"] == provider["affix_id"] and level == provider["source_level"], "效果身份或等级不匹配")
    if provider["relation"] == "equipped_weapon":
        weapon = _evidenced_node(proof, provider["weapon_node_id"], evidence_refs)
        require(type(weapon) is dict and subject["target_type"] == "weapon", "武器主体不一致")
        _integer(weapon.get("id"), 1, 2**53 - 1, "原始武器ID")
        groups = weapon.get("skillAffix")
        _list(groups, 1, 16)
        for group in groups:
            _integer(group, 0, 2**53 - 1, "武器效果组")
        require(weapon["id"] == subject["target_id"] and groups.count(affix["id"]) == 1,
                "武器未精确绑定此效果组")
        duplicate_kind = "weapon"
    else:
        gear = _evidenced_node(proof, provider["set_node_id"], evidence_refs)
        require(type(gear) is dict and subject["target_type"] == "artifact_set", "套装主体不一致")
        for key in ("setId", "EquipAffixId"):
            _integer(gear.get(key), 1, 2**53 - 1, "原始套装ID")
        counts = gear.get("setNeedNum")
        _list(counts, 1, 8)
        for count in counts:
            _integer(count, 1, 5, "原始套装件数")
        require(gear["setId"] == subject["target_id"] and gear["EquipAffixId"] == affix["id"]
                and level < len(counts) and counts[level] == provider["required_count"], "套装效果/件数关系不一致")
        duplicate_kind = "artifact_set"
    require(q["duplicate_identity"]["kind"] == duplicate_kind and q["duplicate_identity"]["source_id"] == subject["target_id"],
            "接触来源竞争身份不一致")
    for item in descriptor["rule_templates"]:
        rule, trigger = item["rule"], item["rule"]["trigger"]
        require(trigger.get("event") == "hit" and trigger.get("phase") == "post", "接触规则触发相位不一致")
        require(not set(trigger) - {"event", "phase", "owner_presence", "damage_type_ids", "elements"},
                "接触模板不得隐含其它路由")
        require({k: v for k, v in trigger.items() if k in ("damage_type_ids", "elements")} == bridge["hit_filter"],
                "规则必须保留完整受审回调筛选，不能拿派发子集替换")
        require("quota" not in rule and "resources" not in rule and all(type(e.get("value", 0)) in (int, float) for e in rule["effects"]),
                "接触来源首片仅支持无次数数值收益")
        require(rule["timing"].get("delay") == 0 and type(rule["timing"].get("delay")) in (int, float), "接触收益不能暗加延迟")
        require({p["target"]: p["role"] for p in item["execution_ports"]} == {"owner": "provider", "trigger.event_ids": "qualifying_hits"},
                "接触缺唯一实际命中路由")
    return q
