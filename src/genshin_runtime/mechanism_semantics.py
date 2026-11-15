"""Typed mechanism evidence and unbound D20 templates, without DB authority."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from decimal import Decimal

LIMITS = {"descriptor_bytes": 128 * 1024, "input_bytes": 2 * 1024 * 1024,
          "output_bytes": 2 * 1024 * 1024, "rules": 8, "effects": 8,
          "facts": 64, "parameters": 32, "nodes": 32, "depth": 64, "json_nodes": 65536}
REQUIREMENTS = frozenset({"single_target", "ordinary_stat_read", "fresh_empty_instance", "fixed_party",
    "range_declared", "no_hitlag", "no_unmodelled_cancel", "activation_before_initial_hit",
    "qualified_action_origin", "qualified_reaction_origin", "fixed_nonextra_em_equality",
    "no_dynamic_em", "no_duplicate_source", "qualified_cast_origin", "activation_within_cast_window",
    "qualified_contact_origin", "complete_contact_coverage", "no_provider_transformative_damage"})
PORTS = {"owner": "provider", "trigger.action_ids": "qualifying_actions",
         "trigger.characters": "provider_characters", "timing.delay": "activation_delay",
         "trigger.event_ids": "qualifying_hits"}
TIERS = {"upstream_structural", "primary_test", "experimental_reference"}
STATUSES = {"approved", "pending", "revoked", "stale", "conflict"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite_json(value, limit=None):
    stack, count = [(value, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        require(count <= LIMITS["json_nodes"] and depth <= LIMITS["depth"], "JSON结构预算超限")
        require(type(node) in (dict, list, str, int, float, bool, type(None)), "只接受JSON类型")
        if type(node) is dict:
            require(all(type(k) is str for k in node), "JSON键必须为字符串")
            stack.extend((v, depth + 1) for v in node.values())
        elif type(node) is list:
            stack.extend((v, depth + 1) for v in node)
        elif type(node) is float:
            require(math.isfinite(node), "数值必须有限")
        elif type(node) is int:
            require(abs(node) <= 2**63 - 1, "整数超出受支持范围")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")
    require(limit is None or len(raw) <= limit, "JSON字节预算超限")
    return raw


def fingerprint(value):
    return hashlib.sha256(finite_json(value)).hexdigest()


def _object(value, keys, optional=()):
    require(type(value) is dict and set(keys) <= value.keys() and not value.keys() - set(keys) - set(optional),
            "对象字段缺失或未知：" + ",".join(keys))


def _text(value, maximum=128):
    require(type(value) is str and 0 < len(value.strip()) <= maximum, "字符串为空或超限")


def _id(value):
    require(type(value) is str and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", value) is not None, "非法资料ID")


def _revision(value):
    require(type(value) is int and 1 <= value <= 2**31 - 1, "修订号必须为正整数")


def _sha(value):
    require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None, "SHA256格式错误")


def _list(value, minimum, maximum):
    require(type(value) is list and minimum <= len(value) <= maximum, "数组长度错误")


def _number(value, seconds=False):
    require(type(value) in (int, float) and abs(value) <= 10**12 and math.isfinite(value), "数值类型/范围错误")
    if seconds:
        ticks = Decimal(str(value)) * 1000000
        require(0 <= value <= 10**9 and ticks == ticks.to_integral_value(), "时间必须是非负精确微秒")


def _schema(value, schema, path="rule"):
    """Validate the existing D20 schema strictly, including typed oneOf branches."""
    if "oneOf" in schema:
        matches = 0
        for choice in schema["oneOf"]:
            try:
                _schema(value, choice, path)
                matches += 1
            except ValueError:
                pass
        require(matches == 1, path + "不符合唯一类型")
        return
    if "const" in schema:
        require(fingerprint(value) == fingerprint(schema["const"]), path + "常量错误")
    if "enum" in schema:
        require(any(fingerprint(value) == fingerprint(v) for v in schema["enum"]), path + "枚举错误")
    kind = schema.get("type")
    if kind == "object":
        require(type(value) is dict, path + "必须为对象")
        props = schema.get("properties", {})
        require(set(schema.get("required", ())) <= value.keys(), path + "缺必填字段")
        for key, item in value.items():
            child = props.get(key, schema.get("additionalProperties", True))
            require(child is not False, path + "." + key + "未知")
            if isinstance(child, dict):
                _schema(item, child, path + "." + key)
    elif kind == "array":
        require(type(value) is list, path + "必须为数组")
        for item in value:
            _schema(item, schema["items"], path)
    elif kind == "string":
        _text(value, schema.get("maxLength", 4096))
    elif kind == "number":
        _number(value)
    elif kind == "integer":
        require(type(value) is int, path + "必须为整数")
    elif kind == "boolean":
        require(type(value) is bool, path + "必须为布尔")
    if kind in ("number", "integer"):
        require(value >= schema.get("minimum", -10**12) and value <= schema.get("maximum", 10**12), path + "数值越界")


def target_location(rule, target):
    """Closed semantic paths; callers may only fill an absent leaf."""
    _text(target)
    parts = target.split(".")
    if target in ("owner", "trigger.action_ids", "trigger.characters", "trigger.event_ids", "timing.delay",
                  "timing.duration", "timing.cooldown", "quota.capacity"):
        return (rule, target) if len(parts) == 1 else (rule[parts[0]], parts[1])
    require(len(parts) in (3, 4) and parts[0] == "effects" and parts[2] == "value"
            and (len(parts) == 3 or parts[3] in ("factor", "add", "min", "max")), "值槽路径不受支持")
    matches = [e for e in rule.get("effects", ()) if e.get("id") == parts[1]]
    require(len(matches) == 1, "值槽effect ID不存在或不唯一")
    if len(parts) == 3:
        return matches[0], "value"
    require(type(matches[0].get("value")) is dict, "表达式值槽父对象缺失")
    return matches[0]["value"], parts[3]


def target_unit(target, rule):
    from genshin_engine.registry import BENEFIT_TYPES
    if target.startswith("timing."):
        return "seconds"
    if target == "quota.capacity":
        return "count"
    if target.endswith(".factor"):
        return "ratio"
    effect = next(e for e in rule["effects"] if e["id"] == target.split(".")[1])
    return BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("unit", "ratio")


def _rule_template(item):
    from genshin_engine.constants import ENEMY_ZONES
    from genshin_engine.mechanics_schema import RULE
    from genshin_engine.registry import BENEFIT_TYPES
    _object(item, ("key", "rule", "value_bindings", "execution_ports", "requirements"))
    require(type(item["key"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item["key"]), "规则key错误")
    rule = item["rule"]
    require(type(rule) is dict and not set(rule) & {"id", "owner", "source_id", "status", "source"}, "模板不得含本地执行身份")
    _list(rule.get("effects"), 1, LIMITS["effects"])
    ids = [e.get("id") for e in rule["effects"] if type(e) is dict]
    require(len(ids) == len(rule["effects"]) and all(type(i) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", i) for i in ids)
            and len(set(ids)) == len(ids), "effect ID必须唯一")
    _list(item["value_bindings"], 0, LIMITS["parameters"])
    _list(item["execution_ports"], 1, 4)
    _list(item["requirements"], 0, len(REQUIREMENTS))
    require(all(r in REQUIREMENTS for r in item["requirements"]) and len(set(item["requirements"])) == len(item["requirements"]), "未知/重复执行要求")
    filled, targets = copy.deepcopy(rule), set()
    for binding in [*item["value_bindings"], *item["execution_ports"]]:
        port = "role" in binding
        _object(binding, ("target", "role" if port else "parameter_ref"))
        target = binding["target"]
        require(target not in targets and not any(target.startswith(t + ".") or t.startswith(target + ".") for t in targets), "值槽重复或祖孙覆盖")
        targets.add(target)
        if port:
            require(target in PORTS and binding["role"] == PORTS[target], "执行端口不受支持")
            dummy = "provider" if target == "owner" else 0 if target == "timing.delay" else ["provider"]
        else:
            _id(binding["parameter_ref"])
            require(target not in PORTS, "参数不能覆盖执行端口")
            dummy = 1
        try:
            container, key = target_location(filled, target)
        except (KeyError, TypeError) as exc:
            raise ValueError("值槽父对象缺失") from exc
        require(type(container) is dict and key not in container, "接管值槽在模板中必须缺失")
        container[key] = dummy
    require("owner" in targets, "必须声明提供者端口")
    filled.update(id="rule", source_id="source", status="assumed", source="typed mechanism")
    _schema(filled, RULE)
    trigger = filled["trigger"]
    require((trigger["event"], trigger["phase"]) in (("action_start", "pre"), ("action_start", "post"),
                                                  ("elemental_reaction", "post"), ("hit", "post")), "不支持触发原语")
    contact = trigger["event"] == "hit"
    route = "trigger.event_ids" if contact else "trigger.action_ids" if trigger["event"] == "action_start" else "trigger.characters"
    require(route in targets,
            "触发必须声明实际动作或提供者角色路由端口")
    if trigger["event"] == "elemental_reaction":
        require(trigger.get("reaction_types") == ["swirl"] and trigger.get("reaction_elements") in (["hydro"], ["cryo"]), "首片只支持分离的普通冰水扩散")
    require(filled["beneficiaries"] in ({"mode": "self", "scope": "locked"}, {"mode": "team", "scope": "locked"}), "受益范围未支持")
    stack = filled["stacking"]
    require((contact and stack["policy"] == "add_and_refresh" and 1 <= stack["add"] <= stack["max"] <= 1000)
            or (stack["max"] == stack["add"] == 1 and stack["policy"] in ("replace", "refresh")), "叠层原语未支持")
    if contact:
        require("quota" not in filled and targets & set(PORTS) == {"owner", "trigger.event_ids"},
                "接触模板仅支持实际命中端口且无次数池")
        require(all(type(e.get("value")) in (int, float) for e in filled["effects"]), "接触模板首片仅支持数值效果")
    require("resources" not in filled, "首片不编译资源事件")
    for key in ("delay", "duration", "cooldown"):
        _number(filled["timing"][key], seconds=True)
    filters = [filled["trigger"]]
    if "quota" in filled:
        q = filled["quota"]
        require(q["refresh"] == "reset" and (q["consume"]["event"], q["consume"]["phase"]) == ("hit", "pre"), "次数消费原语未支持")
        filters.append(q["consume"])
    for effect in filled["effects"]:
        zone = BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone")
        require((zone in ENEMY_ZONES) == (effect["domain"] == "enemy"), "收益域与registry不一致")
        value = effect["value"]
        if type(value) is dict:
            require(value["subject"] == "owner" and value["stat"] in ("atk", "hp", "def", "em")
                    and value["sample"] in ("current", "trigger") and "event_id" not in value, "属性取样原语未支持")
            owned_range = any(f"effects.{effect['id']}.value.{k}" in targets for k in ("min", "max"))
            require(owned_range or not ("min" in value and "max" in value) or value["min"] <= value["max"], "表达式范围倒置")
        if "filter" in effect:
            require(effect["filter"]["event"] in ("hit", "reaction_damage") and effect["filter"]["phase"] == "pre", "收益筛选阶段不支持")
            filters.append(effect["filter"])
    for f in filters:
        require("condition" not in f and ("event_ids" not in f or
                contact and f is trigger and "trigger.event_ids" in targets), "本地事件不能藏在模板")
        for k in ("action_ids", "characters"):
            require(k not in f or f is trigger and "trigger." + k in targets, "本地身份须由端口绑定")
    return item


def validate_descriptor(value):
    finite_json(value, LIMITS["descriptor_bytes"])
    _object(value, ("schema_version", "mechanism_id", "revision", "evidence_fact_ids", "rule_templates"))
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "机制schema不受支持")
    _id(value["mechanism_id"])
    _revision(value["revision"])
    _list(value["evidence_fact_ids"], 1, LIMITS["facts"])
    for ref in value["evidence_fact_ids"]:
        _fact_ref(ref)
    require(len(set(value["evidence_fact_ids"])) == len(value["evidence_fact_ids"]), "事实引用重复")
    _list(value["rule_templates"], 1, LIMITS["rules"])
    for item in value["rule_templates"]:
        _rule_template(item)
    require(len({r["key"] for r in value["rule_templates"]}) == len(value["rule_templates"]), "规则key重复")
    require(len({b["parameter_ref"] for r in value["rule_templates"] for b in r["value_bindings"]}) <= LIMITS["parameters"], "累计参数引用超限")
    return copy.deepcopy(value)


def _fact_ref(value):
    require(type(value) is str and value.count("@") == 1, "事实引用必须含修订")
    ident, revision = value.split("@")
    _id(ident)
    require(re.fullmatch(r"[1-9][0-9]{0,9}", revision) is not None, "事实引用修订错误")
    _revision(int(revision))


def _parameter_type(kind, value, unit):
    require(kind in ("finite_number", "positive_integer", "seconds_exact_us") and unit in ("ratio", "seconds", "count", "flat"), "参数类型/单位未知")
    _number(value, seconds=kind == "seconds_exact_us")
    if kind == "positive_integer":
        require(type(value) is int and 1 <= value <= 10000 and unit == "count", "次数须为正整数")
    elif kind == "seconds_exact_us":
        require(unit == "seconds", "时间单位错误")
    else:
        require(unit in ("ratio", "flat"), "普通数值单位错误")


def validate_fact(value):
    finite_json(value, LIMITS["descriptor_bytes"])
    _object(value, ("id", "revision", "kind", "claim", "evidence_tier", "evidence_refs"))
    _id(value["id"])
    _revision(value["revision"])
    require(value["evidence_tier"] in TIERS, "证据等级未知")
    from genshin_runtime.action_resource_semantics import ACTION_FACT_KINDS
    _list(value["evidence_refs"], 1, 32 if value["kind"] in ACTION_FACT_KINDS else 10)
    for ref in value["evidence_refs"]:
        _id(ref)
    require(len(set(value["evidence_refs"])) == len(value["evidence_refs"]), "证据引用重复")
    c = value["claim"]
    if value["kind"] in ACTION_FACT_KINDS:
        from genshin_runtime.action_resource_semantics import validate_action_claim
        validate_action_claim(value["kind"], c)
    elif value["kind"] == "rule_template":
        _rule_template(c)
    elif value["kind"] == "parameter_role":
        _object(c, ("ref", "value_type", "unit", "targets"))
        _id(c["ref"])
        _parameter_type(c["value_type"], 1, c["unit"])
        _list(c["targets"], 1, 64)
        for target in c["targets"]:
            _object(target, ("rule_key", "target"))
            _text(target["rule_key"], 80)
            _text(target["target"])
        require(len({fingerprint(t) for t in c["targets"]}) == len(c["targets"]), "参数用途重复")
    elif value["kind"] == "execution_requirement":
        _object(c, ("requirement",))
        require(c["requirement"] in REQUIREMENTS, "未知执行要求")
    elif value["kind"] == "execution_qualification":
        from genshin_runtime.mechanism_qualification import (
            qualification_node_ids,
        )
        require(set(qualification_node_ids(c)) <= set(value["evidence_refs"]), "父或装备节点未获资格事实证据引用")
    elif value['kind'] in ('attribute_read', 'attribute_output', 'attribute_sample'):
        from genshin_runtime.attribute_source_semantics import validate_attribute_claim
        validate_attribute_claim(value['kind'], c)
    else:
        raise ValueError("未知typed事实类型")
    return copy.deepcopy(value)


def validate_parameter(value):
    finite_json(value, LIMITS["descriptor_bytes"])
    _object(value, ("ref", "value_type", "value", "unit", "status", "origin_kind", "evidence_ref", "derivation"))
    _id(value["ref"])
    _parameter_type(value["value_type"], value["value"], value["unit"])
    require(value["status"] in ("ready", "pending") and value["derivation"] == "identity", "参数状态/推导不支持")
    e = value["evidence_ref"]
    if value["origin_kind"] == "upstream_field":
        _object(e, ("node_id", "pointer"))
        _text(e["node_id"])
    else:
        require(value["origin_kind"] == "reviewed_fact", "未知参数来源")
        _object(e, ("fact_ref", "pointer"))
        _fact_ref(e["fact_ref"])
    require(type(e["pointer"]) is str and (not e["pointer"] or e["pointer"].startswith("/")), "参数pointer无效")
    return copy.deepcopy(value)


def _rule_semantics(item):
    result = copy.deepcopy(item)
    result["rule"].pop("name_zh", None)
    return result


def descriptor_payload(value):
    result = validate_descriptor(value)
    result["rule_templates"] = [_rule_semantics(r) for r in result["rule_templates"]]
    return result


def descriptor_fingerprint(value):
    return fingerprint(descriptor_payload(value))


def fact_fingerprint(value):
    result = validate_fact(value)
    if result["kind"] == "rule_template":
        result["claim"] = _rule_semantics(result["claim"])
    return fingerprint(result)


def parameter_fingerprint(value):
    return fingerprint(validate_parameter(value))


def _pointer(value, pointer):
    for token in pointer[1:].split("/") if pointer else ():
        require(not re.search(r"~(?![01])", token), "无效pointer转义")
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is list:
            require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None, "无效数组pointer")
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _verify_mechanism_source(descriptor, *, source_proof, facts, parameters):
    finite_json([descriptor, source_proof, facts, parameters], LIMITS["input_bytes"])
    d = validate_descriptor(descriptor)
    _list(facts, 1, LIMITS["facts"])
    _list(parameters, 0, LIMITS["parameters"])
    fs = {f["id"] + "@" + str(f["revision"]): validate_fact(f) for f in facts}
    require(not any(f['kind'] in ('attribute_read', 'attribute_output', 'attribute_sample') for f in fs.values()),
            '当前descriptor资格尚未接入版本化属性事实；不得借旧compiler扩大准入')
    from genshin_runtime.action_resource_semantics import ACTION_FACT_KINDS
    require(not any(f["kind"] in ACTION_FACT_KINDS for f in fs.values()),
            "动作来源事实不得由D20机制descriptor消费")
    ps = {p["ref"]: validate_parameter(p) for p in parameters}
    require(len(fs) == len(facts) and set(fs) == set(d["evidence_fact_ids"]), "事实集合重复/遗漏/多余")
    require(len(ps) == len(parameters) and all(p["status"] == "ready" for p in ps.values()), "参数重复或未就绪")
    require({f["claim"]["key"] for f in fs.values() if f["kind"] == "rule_template"}
            == {r["key"] for r in d["rule_templates"]}, "存在未归属的规则事实")
    require({f["claim"]["ref"] for f in fs.values() if f["kind"] == "parameter_role"} == set(ps), "存在未归属的参数用途事实")
    proof = source_proof
    _object(proof, ("schema_version", "snapshot", "subject", "nodes", "evidence", "review", "coverage",
                    "descriptor_fingerprint", "fact_fingerprints", "parameter_fingerprints"))
    require(type(proof["schema_version"]) is int and proof["schema_version"] == 1, "来源schema错误")
    snap = proof["snapshot"]
    _object(snap, ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization"))
    require(snap["version_line"] in ("release", "beta"), "版本线错误")
    _sha(snap["snapshot_id"])
    _text(snap["upstream_commit"])
    _text(snap["indexer_version"], 16)
    require(type(snap["normalization"]) in (dict, type(None)), "规范映射身份错误")
    _object(proof["subject"], ("target_type", "target_id"))
    _text(proof["subject"]["target_type"], 80)
    require(type(proof["subject"]["target_id"]) is int and 0 < proof["subject"]["target_id"] <= 2**53-1, "来源主体ID错误")
    _object(proof["review"], ("descriptor_status", "fact_statuses"))
    require(proof["review"]["descriptor_status"] == "approved" and type(proof["review"]["fact_statuses"]) is dict
            and set(proof["review"]["fact_statuses"]) == set(fs)
            and all(v == "approved" for v in proof["review"]["fact_statuses"].values()), "材料含未批准/撤销/陈旧事实")
    cov = proof["coverage"]
    _object(cov, ("whole_source_status", "excluded_branches", "unknown_branches"))
    require(cov["whole_source_status"] == "partial", "不得声明全源可执行")
    for key in ("excluded_branches", "unknown_branches"):
        _list(cov[key], 0, 64)
        for item in cov[key]:
            _text(item, 4096)
    require(not cov["unknown_branches"], "存在未知控制分支")
    _list(proof["nodes"], 0, LIMITS["nodes"])
    nodes = {}
    for n in proof["nodes"]:
        _object(n, ("id", "source_path", "source_sha256", "json_pointer", "node_sha256", "payload"))
        for key in ("id", "source_path"):
            _text(n[key], 512)
        _sha(n["source_sha256"])
        require(type(n["json_pointer"]) is str and (not n["json_pointer"] or n["json_pointer"].startswith("/")), "原节点pointer错误")
        require(n["id"] not in nodes and n["node_sha256"] == fingerprint(n["payload"]), "节点身份重复/内容指纹错误")
        nodes[n["id"]] = n
    _list(proof["evidence"], 1, 64)
    evidence = set()
    for e in proof["evidence"]:
        _object(e, ("id", "kind", "locator", "sha256"))
        _id(e["id"])
        require(e["id"] not in evidence and e["kind"] in ("runtime_node", "primary_test", "reference_implementation"), "证据重复或类型错误")
        _text(e["locator"], 4096)
        _sha(e["sha256"])
        if e["kind"] == "runtime_node":
            require(e["id"] in nodes and e["locator"] == nodes[e["id"]]["source_path"] + "#" + nodes[e["id"]]["json_pointer"]
                    and e["sha256"] == nodes[e["id"]]["node_sha256"], "原节点证据定位/摘要不一致")
        evidence.add(e["id"])
    require(all(set(f["evidence_refs"]) <= evidence for f in fs.values()), "事实缺来源证据")
    require(proof["descriptor_fingerprint"] == descriptor_fingerprint(d)
            and proof["fact_fingerprints"] == {k: fact_fingerprint(v) for k, v in fs.items()}
            and proof["parameter_fingerprints"] == {k: parameter_fingerprint(v) for k, v in ps.items()}, "材料局部指纹不一致")
    used = {}
    for rule in d["rule_templates"]:
        matching = [f for f in fs.values() if f["kind"] == "rule_template" and f["claim"]["key"] == rule["key"]]
        require(len(matching) == 1 and fingerprint(_rule_semantics(matching[0]["claim"])) == fingerprint(_rule_semantics(rule)), "规则完整语义未获唯一typed事实覆盖")
        for binding in rule["value_bindings"]:
            ref, target = binding["parameter_ref"], binding["target"]
            require(ref in ps and ps[ref]["unit"] == target_unit(target, rule["rule"]), "参数缺失或用途单位不符")
            if target == "quota.capacity":
                require(ps[ref]["value_type"] == "positive_integer", "额度必须为整型参数")
            if target.startswith("timing."):
                require(ps[ref]["value_type"] == "seconds_exact_us", "时间必须为精确微秒参数")
            used.setdefault(ref, []).append({"rule_key": rule["key"], "target": target})
    require(set(used) == set(ps), "存在未使用参数")
    for ref, p in ps.items():
        roles = [f["claim"] for f in fs.values() if f["kind"] == "parameter_role" and f["claim"]["ref"] == ref]
        require(len(roles) == 1 and roles[0]["value_type"] == p["value_type"] and roles[0]["unit"] == p["unit"]
                and sorted(roles[0]["targets"], key=fingerprint) == sorted(used[ref], key=fingerprint), "参数类型或精确用途未认证")
        e = p["evidence_ref"]
        if p["origin_kind"] == "upstream_field":
            require(e["node_id"] in nodes, "参数原节点不存在")
            raw = _pointer(nodes[e["node_id"]]["payload"], e["pointer"])
        else:
            raise ValueError("首片尚未支持reviewed_fact标量")
        require(fingerprint(raw) == fingerprint(p["value"]), "参数值与原证据不符")
    qualifications = [f for f in fs.values() if f["kind"] == "execution_qualification"]
    require(len(qualifications) <= 1, "每份机制最多一条执行资格事实")
    qualification = None
    if qualifications:
        from genshin_runtime.mechanism_qualification import (
            verify_execution_qualification,
        )
        fact = qualifications[0]
        qualification = verify_execution_qualification(fact["claim"], descriptor=d,
            source_proof=proof, evidence_refs=fact["evidence_refs"])
    cast = qualification is not None and qualification["schema_version"] == 2
    contact = qualification is not None and qualification["schema_version"] == 3
    require(contact or not any(r["rule"]["trigger"]["event"] == "hit" for r in d["rule_templates"]),
            "实际接触触发必须有v3资格")
    require(cast or not any(r["rule"]["trigger"].get("event") == "action_start"
                           and r["rule"]["trigger"].get("phase") == "post" for r in d["rule_templates"]),
            "新施放相位必须有v2资格")
    requirements = {f["claim"]["requirement"] for f in fs.values() if f["kind"] == "execution_requirement"}
    requirements.update(req for r in d["rule_templates"] for req in r["requirements"])
    require(cast or not requirements & {"qualified_cast_origin", "activation_within_cast_window"},
            "新施放检查必须有v2资格")
    require(contact or not requirements & {"qualified_contact_origin", "complete_contact_coverage", "no_provider_transformative_damage"},
            "接触检查必须有v3资格")
    if contact:
        require(not requirements & {"qualified_action_origin", "qualified_reaction_origin", "activation_before_initial_hit"},
                "接触资格不得借旧动作或反应检查")
    if cast:
        require(not requirements & {"qualified_action_origin", "qualified_reaction_origin", "activation_before_initial_hit"},
                "施放资格不得保留旧接触父检查")
        if qualification["target_dependency"]["mode"] == "none":
            require("single_target" not in requirements, "无目标资格不能抹去已审单目标要求")
    return {"descriptor": d, "facts": copy.deepcopy(list(fs.values())), "parameters": copy.deepcopy(list(ps.values())),
            "execution_qualification": qualification,
            "source_fingerprint": fingerprint(proof), "evidence_tier": sorted({f["evidence_tier"] for f in fs.values()}),
            "coverage": copy.deepcopy(cov), "verification_scope": "materialized_consistency_only"}


def verify_mechanism_source(descriptor, *, source_proof, facts, parameters):
    try:
        return _verify_mechanism_source(descriptor, source_proof=source_proof, facts=facts, parameters=parameters)
    except (KeyError, TypeError, IndexError, StopIteration, OverflowError, RecursionError) as exc:
        raise ValueError("物化语义材料结构不完整或无效") from exc


def mechanism_semantics_capabilities():
    return {"schema_version": 1, "limits": dict(LIMITS), "execution_ports": dict(PORTS),
            "fact_kinds": ["rule_template", "parameter_role", "execution_requirement", "execution_qualification",
                           "attribute_read", "attribute_output", "attribute_sample",
                           "action_capacity_delta", "related_action_root", "action_capacity_model",
                           "action_admission_model"],
            "action_facts": {"schema_version": 1, "descriptor_execution": "rejected",
                             "source_query": "action_capacity_source_profile", "max_evidence_refs": 32},
            "attribute_facts": {"schema_version": 1, "descriptor_execution": "not_connected",
                                "source_compiler": "database-inherent-swirl-attribute-v2"},
            "execution_qualification": {"schema_version": 1, "max_per_descriptor": 1, "max_bytes": 32 * 1024,
                "missing_meaning": "unbound_templates_only", "parameter_index_max": 63,
                "supported_schema_versions": [1, 2, 3],
                "supported_parent_kinds": ["calibrated_single_contact_v1", "successful_action_v1",
                                           "successful_action_v2", "actual_contact_v1"]},
            "requirements": sorted(REQUIREMENTS), "verification_scope": "materialized_consistency_only",
            "fresh_authorization": "database_materializer_and_app_required"}


def validate_record(record, kind):
    """Strict catalog record wrapper; persistence/CAS remains the store's job."""
    finite_json(record, LIMITS["input_bytes"])
    _object(record, ("id", "revision", "subject", "payload", "evidence", "applicability", "review"))
    _id(record["id"])
    _revision(record["revision"])
    subject = record["subject"]
    _object(subject, ("target_type", "target_id"))
    require(subject["target_type"] in ("character", "weapon", "artifact_set")
            and type(subject["target_id"]) is int and 0 < subject["target_id"] <= 2**53-1, "资料主体无效")
    app = record["applicability"]
    _object(app, ("version_line", "upstream_commit", "normalization_fingerprint"))
    require(app["version_line"] in ("release", "beta") and type(app["upstream_commit"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", app["upstream_commit"]), "资料适用范围无效")
    _sha(app["normalization_fingerprint"])
    _list(record["evidence"], 1, 64)
    evidence = set()
    for e in record["evidence"]:
        _object(e, ("id", "kind", "locator", "sha256"))
        _id(e["id"])
        require(e["id"] not in evidence and e["kind"] in ("runtime_node", "primary_test", "reference_implementation"), "资料证据类型错误或重复")
        _text(e["locator"], 4096)
        _sha(e["sha256"])
        evidence.add(e["id"])
    review = record["review"]
    _object(review, ("status", "reviewer", "reviewed_at"))
    require(review["status"] in ("draft", "pending", "approved", "revoked"), "审核状态错误")
    if review["status"] in ("approved", "revoked"):
        _text(review["reviewer"], 256)
        _text(review["reviewed_at"], 80)
        try:
            datetime.fromisoformat(review["reviewed_at"])
        except ValueError as exc:
            raise ValueError("审核日期必须为ISO日期") from exc
    else:
        require(review["reviewer"] is None and review["reviewed_at"] is None, "未审核资料不能附审核元数据")
    if kind == "fact":
        value = validate_fact(record["payload"])
        from genshin_runtime.action_resource_semantics import (
            ACTION_FACT_KINDS,
            validate_action_record_scope,
        )
        if value["kind"] in ACTION_FACT_KINDS:
            validate_action_record_scope(record)
        require(value["id"] == record["id"] and value["revision"] == record["revision"]
                and set(value["evidence_refs"]) <= evidence, "事实身份或证据不归属")
    elif kind == "descriptor":
        from genshin_runtime.mechanism_source_binding import validate_source_binding
        _object(record["payload"], ("descriptor", "source_binding"))
        d = validate_descriptor(record["payload"]["descriptor"])
        b = validate_source_binding(record["payload"]["source_binding"])
        require(d["mechanism_id"] == record["id"] and d["revision"] == record["revision"], "描述身份不匹配")
        require(fingerprint(b["subject"]) == fingerprint(subject)
                and b["upstream_commit"] == app["upstream_commit"]
                and b["normalization_fingerprint"] == app["normalization_fingerprint"]
                and fingerprint(b["evidence"]) == fingerprint(record["evidence"]), "来源绑定与记录范围/证据不一致")
    else:
        raise ValueError("未知语义记录种类")
    return copy.deepcopy(record)


def record_payload_fingerprint(record, kind):
    value = validate_record(record, kind)
    value.pop("review")
    if kind == "fact":
        value["payload"] = {"fact_fingerprint": fact_fingerprint(record["payload"])}
    else:
        value["payload"]["descriptor"] = {"descriptor_fingerprint": descriptor_fingerprint(record["payload"]["descriptor"])}
    return fingerprint(value)


def record_review_fingerprint(record, kind):
    return fingerprint({"payload_fingerprint": record_payload_fingerprint(record, kind), "review": record["review"]})
