"""Typed, reviewed source locators; no character dispatch or executable rules."""
from __future__ import annotations

import copy
import json
import re

LIMITS = {"max_bytes": 256 * 1024, "max_nodes": 32, "max_parameters": 32,
          "max_evidence": 64, "max_branches": 64, "max_decoded_source_bytes": 128 * 1024 * 1024}
_ID = r"[a-z0-9][a-z0-9_.-]{0,79}"
_HASH = r"[0-9a-f]{64}"
_TOP = {"schema_version", "upstream_commit", "normalization_fingerprint", "subject", "selection",
        "nodes", "parameters", "evidence", "coverage"}
_SELECT = {"character_id", "skill_depot_id", "skill_id", "proud_skill_id", "talent_level",
           "constellation", "character_level", "ascension", "slot", "variant", "refinement"}


def _require(value, message):
    if not value:
        raise ValueError(message)


def _object(value, keys, name):
    _require(type(value) is dict and set(value) == keys, name + "字段缺失或未知")


def _text(value, limit, name):
    _require(type(value) is str and 0 < len(value) <= limit and value.strip() == value,
             name + "需要非空有限字符串")


def _pattern(value, pattern, name):
    _require(type(value) is str and re.fullmatch(pattern, value) is not None, name + "格式无效")


def _array(value, low, high, name):
    _require(type(value) is list and low <= len(value) <= high, name + "数量超限或不是列表")


def _integer(value, low, high, name):
    _require(type(value) is int and low <= value <= high, name + "需要范围内非布尔整数")


def validate_pointer(value):
    """Strict RFC6901 syntax; array index validity is checked against actual raw."""
    _require(type(value) is str and len(value) <= 2048 and (not value or value.startswith("/"))
             and re.search(r"~(?![01])", value) is None, "来源JSON Pointer无效")
    return value


def resolve_pointer(value, pointer):
    validate_pointer(pointer)
    for raw in pointer[1:].split("/") if pointer else []:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            _require(re.fullmatch(r"0|[1-9][0-9]*", part) is not None, "数组来源指针不是规范非负索引")
            _require(len(part) <= 10 and int(part) < len(value), "数组来源指针越界")
            value = value[int(part)]
        else:
            _require(type(value) is dict and part in value, "来源指针没有对应字段")
            value = value[part]
    return value


def validate_source_binding(value):
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8"))
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ValueError("来源绑定必须为有限JSON") from exc
    _require(size <= LIMITS["max_bytes"], "来源绑定超过字节预算")
    _object(value, _TOP, "来源绑定")
    _integer(value["schema_version"], 1, 1, "来源绑定版本")
    _pattern(value["upstream_commit"], r"[0-9a-f]{40}", "上游提交")
    _pattern(value["normalization_fingerprint"], _HASH, "归一化指纹")
    subject = value["subject"]
    _object(subject, {"target_type", "target_id"}, "来源主体")
    _require(subject["target_type"] in ("character", "weapon", "artifact_set"), "来源主体类型未支持")
    _integer(subject["target_id"], 1, 2**53 - 1, "来源主体ID")
    selection = value["selection"]
    _require(type(selection) is dict and not selection.keys() - _SELECT, "来源资格字段未知")
    if "refinement" in selection:
        _require(subject["target_type"] == "weapon" and set(selection) == {"refinement"},
                 "精炼选择仅属于武器来源，不能与角色资格混合")
    for key, item in selection.items():
        if key == "slot":
            _require(item in ("normal", "skill", "burst"), "技能槽无效")
        elif key == "variant":
            _pattern(item, _ID, "来源变体")
        else:
            bounds = {"constellation": (0, 6), "ascension": (0, 6), "talent_level": (1, 15),
                      "character_level": (1, 100), "refinement": (1, 5)}.get(key, (1, 2**53 - 1))
            _integer(item, *bounds, "来源资格." + key)
    if subject["target_type"] == "character":
        _require(selection.get("character_id") == subject["target_id"], "来源资格不属于记录主体")
    _array(value["nodes"], 1, LIMITS["max_nodes"], "来源节点")
    nodes, locations = {}, set()
    for node in value["nodes"]:
        _object(node, {"id", "definition_kind", "source_path", "json_pointer", "node_sha256", "source_sha256"}, "来源节点")
        _pattern(node["id"], _ID, "来源节点ID")
        _require(node["id"] not in nodes, "来源节点ID重复")
        _require(node["definition_kind"] in ("excel_record", "talent_config", "ability", "modifier"), "来源节点类型未支持")
        path = node["source_path"]
        _text(path, 512, "来源路径")
        _require(not any(c in path for c in ("\\", ":", "\x00"))
                 and all(p not in ("", ".", "..") for p in path.split("/")), "来源路径必须是仓库内规范相对路径")
        validate_pointer(node["json_pointer"])
        _pattern(node["node_sha256"], _HASH, "来源节点指纹")
        _pattern(node["source_sha256"], _HASH, "完整源文件指纹")
        location = (path, node["json_pointer"], node["definition_kind"])
        _require(location not in locations, "同一来源节点重复绑定")
        nodes[node["id"]] = node
        locations.add(location)
    _array(value["parameters"], 0, LIMITS["max_parameters"], "来源参数")
    refs = set()
    for parameter in value["parameters"]:
        _object(parameter, {"ref", "value_type", "unit", "node_id", "pointer"}, "来源参数")
        _pattern(parameter["ref"], _ID, "参数引用")
        _require(parameter["ref"] not in refs, "来源参数引用重复")
        refs.add(parameter["ref"])
        _require(parameter["value_type"] in ("finite_number", "positive_integer", "seconds_exact_us"), "参数类型未支持")
        _require(parameter["unit"] in ("ratio", "seconds", "count", "flat"), "参数单位未支持")
        if parameter["value_type"] in ("positive_integer", "seconds_exact_us"):
            _require(parameter["unit"] == {"positive_integer": "count", "seconds_exact_us": "seconds"}[parameter["value_type"]], "参数类型与单位冲突")
        _require(type(parameter["node_id"]) is str and parameter["node_id"] in nodes, "参数缺完整来源节点")
        validate_pointer(parameter["pointer"])
    _array(value["evidence"], 1, LIMITS["max_evidence"], "来源证据")
    evidence_ids, runtime_evidence_ids = set(), set()
    for evidence in value["evidence"]:
        _object(evidence, {"id", "kind", "locator", "sha256"}, "来源证据")
        _pattern(evidence["id"], _ID, "来源证据ID")
        _require(evidence["id"] not in evidence_ids, "来源证据ID重复")
        evidence_ids.add(evidence["id"])
        _require(evidence["kind"] in ("runtime_node", "primary_test", "reference_implementation"), "来源证据类别未支持")
        _text(evidence["locator"], 4096, "来源证据定位")
        _pattern(evidence["sha256"], _HASH, "来源证据指纹")
        if evidence["kind"] == "runtime_node":
            node = nodes.get(evidence["id"])
            _require(node is not None and evidence["locator"] == node["source_path"] + "#" + node["json_pointer"]
                     and evidence["sha256"] == node["node_sha256"], "原始证据未指向受审完整节点")
            runtime_evidence_ids.add(evidence["id"])
    _require(set(nodes) <= runtime_evidence_ids, "完整来源节点没有对应原始证据记录")
    _object(value["coverage"], {"whole_source_status", "excluded_branches", "unknown_branches"}, "来源覆盖")
    _require(value["coverage"]["whole_source_status"] == "partial", "局部来源不能声明完整角色就绪")
    for key in ("excluded_branches", "unknown_branches"):
        values = value["coverage"][key]
        _array(values, 0, LIMITS["max_branches"], "来源覆盖." + key)
        for branch in values:
            _text(branch, 1024, "来源分支")
        _require(len(set(values)) == len(values), "来源覆盖分支重复")
    return copy.deepcopy(value)
