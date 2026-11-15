"""Finite same-commit capacity provenance rebinding; never grants authority.

The original archive stays byte-exact and pending. Current qualification belongs
to a separately reviewed current record, not to a rewritten historical fact.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from genshin_runtime.action_resource_semantics import (
    _source_claim,
    digest,
    integer,
    obj,
    path,
    require,
    text,
)
from genshin_runtime.action_root_semantics import bounded
from genshin_runtime.mechanism_semantics import fingerprint
from genshin_runtime.mechanism_source_binding import validate_pointer

CONTRACT = "action-capacity-delta-v2"
PROVENANCE = "same-commit-identical-source-capacity-rebinding-v1"
MAX_ARCHIVE_BYTES = 32768
MAX_CLAIM_BYTES = 128 * 1024
RECORD_ROLES = ("avatar", "depot", "talent", "description", "target")
DEFINITION_KEYS = {"definition_id", "definition_kind", "owner_definition_id", "source_path",
                   "json_pointer", "variant_key", "namespace_key", "node_sha256", "exact_key"}
DECISION_KEYS = {"approved_amount", "character", "character_id", "claim_sha256", "config_pointer",
                 "constellation_rank", "decision", "depot_id", "direct_skill", "evidence_class",
                 "fact_id", "fact_status_mutated", "notes", "operation_pointer", "original_description",
                 "source_path", "source_sha256", "sub_skill", "talent_id", "talent_name", "target_name",
                 "target_skill_id"}


def _same(a, b):
    return fingerprint(a) == fingerprint(b)


def _at(value, pointer):
    validate_pointer(pointer)
    for token in pointer.split("/")[1:] if pointer else ():
        token = token.replace("~1", "/").replace("~0", "~")
        if type(value) is list:
            require(token.isascii() and token.isdigit() and str(int(token)) == token,
                    "容量迁移数组指针无效")
            index = int(token)
            require(index < len(value), "容量迁移数组指针越界")
            value = value[index]
        else:
            require(type(value) is dict and token in value, "容量迁移原字段指针不存在")
            value = value[token]
    return value


def _archive(value, expected):
    require(type(value) is str and 0 < len(value) <= MAX_ARCHIVE_BYTES, "原审核归档文本超限")
    # One bounded string is encoded only after its character ceiling is known.
    try:
        raw = value.encode("utf8")
    except UnicodeError as exc:
        raise ValueError("原审核归档不是有效UTF-8") from exc
    require(len(raw) <= MAX_ARCHIVE_BYTES, "原审核归档UTF-8字节超限")
    require(hashlib.sha256(raw).hexdigest() == expected, "原审核归档字节摘要不符")
    # Bound JSON structural expansion before the parser allocates containers.
    quoted, escape, depth, delimiters = False, False, 0, 0
    for char in value:
        if quoted:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            delimiters += 1
            require(depth <= 48 and delimiters <= 8192, "原审核归档结构预算超限")
        elif char in "]}":
            depth -= 1
            require(depth >= 0, "原审核归档括号无效")
        elif char in ",:":
            delimiters += 1
            require(delimiters <= 8192, "原审核归档节点预算超限")

    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, "原审核归档包含重复键")
            result[key] = item
        return result

    def constant(_value):
        raise ValueError("原审核归档包含非有限数值")

    try:
        parsed = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("原审核归档JSON无效") from exc
    bounded(parsed)
    obj(parsed, {"fact", "decision", "prior_report_sha256", "source_paths"})
    require(type(parsed["source_paths"]) is list and 1 <= len(parsed["source_paths"]) <= 8,
            "原审核归档来源清单无效")
    for name in parsed["source_paths"]:
        text(name, 1024)  # Historical Windows paths are documentary, never opened.
    return parsed


def _snapshot_transition(old, current):
    require(old["indexer_version"] == "6" and current["indexer_version"] == "7",
            "仅支持索引6到7的同源容量身份迁移")
    changed = {"snapshot_id", "manifest_fingerprint", "built_at", "indexer_version", "counts"}
    require(_same({k: v for k, v in old.items() if k not in changed},
                  {k: v for k, v in current.items() if k not in changed}),
            "容量迁移不可改变提交、归一化或快照语义")
    for key in ("snapshot_id", "manifest_fingerprint", "built_at"):
        require(old[key] != current[key], "容量迁移应绑定实际新索引身份")
    try:
        before, after = (datetime.fromisoformat(v["built_at"]) for v in (old, current))
        require(before.tzinfo is not None and after.tzinfo is not None and after > before,
                "容量迁移构建时间无效")
    except (ValueError, TypeError) as exc:
        raise ValueError("容量迁移构建时间无效") from exc
    counts_changed = {"runtime_definition", "runtime_source"}
    require(counts_changed <= old["counts"].keys() and counts_changed <= current["counts"].keys(),
            "容量迁移计数缺失")
    require(_same({k: v for k, v in old["counts"].items() if k not in counts_changed},
                  {k: v for k, v in current["counts"].items() if k not in counts_changed}),
            "容量迁移改变了其他索引计数")
    require(all(current["counts"][k] == old["counts"][k] + 1 for k in counts_changed),
            "容量迁移不是单个公共源及定义增量")


def _record(value, claim, role):
    if role == "description":
        obj(value, {"text", "source_path", "source_sha256", "json_pointer", "node_sha256", "hash_field"})
        text(value["text"], 16384)
        require(value["source_path"] == "TextMap/TextMap_MediumCHS.json"
                and value["hash_field"] == "descTextMapHash", "容量原文来源错误")
        digest(value["source_sha256"])
        digest(value["node_sha256"])
        validate_pointer(value["json_pointer"])
        require(fingerprint(value["text"]) == value["node_sha256"], "容量原文节点摘要错误")
        return
    obj(value, {"definition", "source_path", "source_sha256", "json_pointer", "node_sha256", "record"})
    definition = value["definition"]
    obj(definition, DEFINITION_KEYS)
    table = {"avatar": "Avatar", "depot": "AvatarSkillDepot", "talent": "AvatarTalent", "target": "AvatarSkill"}[role]
    require(value["source_path"] == "ExcelBinOutput/" + table + "ExcelConfigData.json", "容量原行来源错误")
    validate_pointer(value["json_pointer"])
    digest(value["source_sha256"])
    digest(value["node_sha256"])
    require(type(value["record"]) is dict and fingerprint(value["record"]) == value["node_sha256"],
            "容量完整原行与节点摘要不符")
    require(all(definition[k] == value[k] for k in ("source_path", "json_pointer", "node_sha256")),
            "容量完整原行定义与来源不符")
    require(definition["definition_kind"] == "excel_record" and definition["namespace_key"] == "excel"
            and definition["owner_definition_id"] is None and definition["variant_key"] is None,
            "容量迁移原行定义类型不符")
    key = "talentId" if role == "talent" else "id"
    integer(value["record"].get(key))
    require(_same(definition["exact_key"], {key: value["record"][key]}), "容量原行exact key不符")
    require(definition["definition_id"] == fingerprint([claim["snapshot"]["snapshot_id"], value["source_path"],
            value["json_pointer"], "excel_record"]), "容量新旧定义ID不符")


def _relations(wrappers, c, activation, decision):
    avatar, depot, talent, target = (wrappers[k]["record"] for k in ("avatar", "depot", "talent", "target"))
    require(avatar["id"] == c["character_id"] and depot["id"] == c["skill_depot_id"]
            and target["id"] == c["target_skill_id"], "容量迁移选择与原行不符")
    candidate_depots = avatar.get("candSkillDepotIds", [])
    require(type(candidate_depots) is list and len(candidate_depots) <= 32, "角色技能组目录无效")
    for ident in [avatar.get("skillDepotId"), *candidate_depots]:
        integer(ident, 0)
    require(c["skill_depot_id"] in [avatar.get("skillDepotId"), *candidate_depots], "容量迁移技能组不属于角色")
    ranks = depot.get("talents")
    require(type(ranks) is list and len(ranks) == 6, "容量迁移命座槽不完整")
    for ident in ranks:
        integer(ident)
    rank = c["constellation_rank"]
    require(talent["talentId"] == ranks[rank - 1] == activation["talent_id"], "容量迁移命座激活槽错误")
    require(activation["depot_pointer"] == wrappers["depot"]["json_pointer"] + "/talents/" + str(rank - 1),
            "容量迁移必须绑定精确原命座字段")
    require(talent.get("openConfig") == c["config_pointer"][1:], "容量迁移原命座配置错误")
    integer(talent.get("descTextMapHash"))
    require(wrappers["description"]["json_pointer"] == "/" + str(talent["descTextMapHash"]),
            "容量原文不属于原命座字段")
    for key in ("skills", "subSkills"):
        require(type(depot.get(key)) is list and len(depot[key]) <= 64, "容量迁移技能目录无效")
        for ident in depot[key]:
            integer(ident, 0)
    integer(depot.get("energySkill"), 0)
    direct = c["target_skill_id"] in [*depot["skills"], depot["energySkill"]]
    sub = c["target_skill_id"] in depot["subSkills"]
    require(decision["direct_skill"] is direct and decision["sub_skill"] is sub and (direct or sub),
            "容量迁移主技能或子技能归属改变")


def validate_capacity_rebinding_claim(c):
    """Validate material-only provenance; callers still need a current review/head."""
    usage = bounded(c)  # Reserves child work before expanding any container.
    require(usage["serialized_bytes"] <= MAX_CLAIM_BYTES, "容量迁移事实超过128KiB")
    obj(c, {"contract", "source_claim", "activation", "application", "evidence_route", "provenance"})
    require(c["contract"] == CONTRACT and c["application"] == "static_capacity_once_per_selected_constellation_slot",
            "容量迁移合同不符")
    current = c["source_claim"]
    _source_claim(current)
    a = c["activation"]
    obj(a, {"kind", "rank", "talent_id", "depot_pointer"})
    integer(a["rank"], 1, 6)
    integer(a["talent_id"])
    validate_pointer(a["depot_pointer"])
    require(a["kind"] == "constellation_slot" and a["rank"] == current["constellation_rank"],
            "容量迁移激活合同不符")
    p = c["provenance"]
    obj(p, {"contract", "original_archive_utf8", "original_archive_bytes_sha256", "original_fact_fingerprint",
            "original_source_claim_sha256", "original_decision_fingerprint", "original_report_sha256",
            "current_source_claim_sha256", "record_materials", "source_material"})
    require(p["contract"] == PROVENANCE, "容量迁移来源合同不符")
    for key in ("original_archive_bytes_sha256", "original_fact_fingerprint", "original_source_claim_sha256",
                "original_decision_fingerprint", "original_report_sha256", "current_source_claim_sha256"):
        digest(p[key])
    archive = _archive(p["original_archive_utf8"], p["original_archive_bytes_sha256"])
    fact, decision = archive["fact"], archive["decision"]
    obj(fact, {"id", "revision", "kind", "status", "claim", "review_scope", "assumptions", "evidence_tier"})
    text(fact["id"], 80)
    integer(fact["revision"], 1, 2**31 - 1)
    require(fact["status"] == "pending" and fact["kind"] == "charge_capacity_bonus"
            and fact["review_scope"] == "exact_source_capacity_delta_only"
            and fact["evidence_tier"] == "same_snapshot_raw_and_explicit_original_description",
            "容量旧事实原状态或范围改变")
    require(type(fact["assumptions"]) is list and 1 <= len(fact["assumptions"]) <= 16, "容量旧假设缺失")
    for note in fact["assumptions"]:
        text(note, 4096)
    original = fact["claim"]
    _source_claim(original)
    obj(decision, DECISION_KEYS)
    require(fingerprint(fact) == p["original_fact_fingerprint"]
            and fingerprint(original) == p["original_source_claim_sha256"] == decision["claim_sha256"]
            and fingerprint(decision) == p["original_decision_fingerprint"]
            and archive["prior_report_sha256"] == p["original_report_sha256"]
            and fingerprint(current) == p["current_source_claim_sha256"], "容量旧新命题或审核摘要混淆")
    require(decision["fact_id"] == fact["id"] and decision["fact_status_mutated"] is False
            and decision["decision"] == "source_scoped_capacity_delta_plus_one_approved"
            and type(decision["approved_amount"]) is int and decision["approved_amount"] == original["amount"] == 1,
            "容量原审核决定与原事实不符")
    for dk, ck in (("character_id", "character_id"), ("depot_id", "skill_depot_id"), ("target_skill_id", "target_skill_id"),
                   ("constellation_rank", "constellation_rank"), ("source_path", "source_path"),
                   ("source_sha256", "source_sha256"), ("config_pointer", "config_pointer"), ("operation_pointer", "operation_pointer")):
        require(_same(decision[dk], original[ck]), "容量原审核决定的目标或来源改变")
    require(type(decision["talent_id"]) is int and decision["talent_id"] == a["talent_id"], "容量原审核命座不符")
    require(type(decision["notes"]) is list and 1 <= len(decision["notes"]) <= 16, "容量原审核说明缺失")
    for note in decision["notes"]:
        text(note, 4096)
    for key in ("character", "talent_name", "target_name", "original_description"):
        text(decision[key], 16384)
    routes = {"direct_skill_description": (True, False, "direct_named_skill_original_text_and_exact_source_operation"),
              "same_parent_explicit_subskill_operation": (False, True, "same_constellation_parent_explicit_second_target_plus_named_primary_skill_context")}
    text(c["evidence_route"], 128)
    require(c["evidence_route"] in routes, "容量迁移证据路径不支持")
    direct, sub, evidence = routes[c["evidence_route"]]
    require(decision["direct_skill"] is direct and decision["sub_skill"] is sub and decision["evidence_class"] == evidence,
            "容量迁移证据路径与旧决定不符")
    _snapshot_transition(original["snapshot"], current["snapshot"])
    changed = {"snapshot", "record_fingerprints", "target_skill_fingerprint"}
    require(_same({k: v for k, v in original.items() if k not in changed},
                  {k: v for k, v in current.items() if k not in changed}), "容量迁移不可改变游戏命题或原源字节")
    materials = p["record_materials"]
    obj(materials, set(RECORD_ROLES))
    for role in RECORD_ROLES:
        pair = materials[role]
        obj(pair, {"original", "current"})
        for label, claim in (("original", original), ("current", current)):
            value = pair[label]
            _record(value, claim, role)
            expected = claim["target_skill_fingerprint"] if role == "target" else claim["record_fingerprints"][role]
            require(fingerprint(value) == expected, "容量新旧完整原行指纹不符")
        before, after = pair["original"], pair["current"]
        if role == "description":
            require(_same(before, after), "容量迁移不能改变原文内容或来源")
        else:
            require(_same({k: v for k, v in before.items() if k != "definition"},
                          {k: v for k, v in after.items() if k != "definition"})
                    and _same({k: v for k, v in before["definition"].items() if k != "definition_id"},
                              {k: v for k, v in after["definition"].items() if k != "definition_id"}),
                    "容量完整原行只能迁移定义ID")
    require(decision["original_description"] == materials["description"]["original"]["text"], "容量原审描述错误")
    for label, claim in (("original", original), ("current", current)):
        _relations({k: materials[k][label] for k in RECORD_ROLES}, claim, a, decision)
    source = p["source_material"]
    obj(source, {"source_path", "source_sha256", "canonical_sha256", "raw"})
    path(source["source_path"])
    digest(source["source_sha256"])
    digest(source["canonical_sha256"])
    require(source["source_path"] == current["source_path"] and source["source_sha256"] == current["source_sha256"]
            and fingerprint(source["raw"]) == source["canonical_sha256"] == current["parent_canonical_sha256"],
            "容量迁移完整原父身份不符")
    require(type(source["raw"]) is dict and 1 <= len(source["raw"]) <= 64, "容量迁移完整原父形状无效")
    config = _at(source["raw"], current["config_pointer"])
    operation = _at(source["raw"], current["operation_pointer"])
    require(type(config) is list and fingerprint(config) == current["config_node_sha256"]
            and type(operation) is dict and fingerprint(operation) == current["operation_node_sha256"],
            "容量迁移完整配置或操作身份不符")
    return {"original_fact_id": fact["id"], "original_revision": fact["revision"],
            "original_source_claim_sha256": p["original_source_claim_sha256"],
            "current_source_claim_sha256": p["current_source_claim_sha256"],
            "original_archive_bytes_sha256": p["original_archive_bytes_sha256"],
            "provenance_fingerprint": fingerprint(p), "execution_authority": False}
