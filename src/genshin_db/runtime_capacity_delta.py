"""Pure source-bound capacity-delta consistency; no DB/game admission.

The reviewed material class is generic. Character/skill identifiers bind facts
to their original records; they never select an algorithm. Complete capacity,
cooldown groups and action dispatch require independent root/engine contracts.
"""
from __future__ import annotations

import copy
import math

from genshin_db.runtime_catalog import dumps, fingerprint, resolve_pointer
from genshin_db.runtime_talent_level import _recipe
from genshin_runtime.normalization import for_commit

LIMITS = {"work": 200000, "depth": 64, "bytes": 8 * 1024 * 1024, "capacity": 100}
CONTRACT = "source-bound-charge-capacity-delta-v1"


class CapacityError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.reason = code
        self.status = 413 if code == "capacity_budget" else 409


def fail(code, message):
    raise CapacityError(code, message)


class Budget:
    """Finite traversal before retained copies, including repeated references."""
    def __init__(self, *, limits=None):
        self.work = self.bytes = 0
        self.limits = {**LIMITS, **(limits or {})}

    def charge(self, amount):
        if type(amount) is not int or amount < 0 or self.work + amount > self.limits["work"]:
            fail("capacity_budget", "来源材料累计访问预算超限")
        self.work += amount

    def scan(self, value):
        stack, active, estimate = [(value, 0, False)], set(), 0
        while stack:
            item, depth, exiting = stack.pop()
            if exiting:
                active.remove(id(item))
                continue
            self.charge(1)
            if depth > self.limits["depth"]:
                fail("capacity_budget", "来源材料深度超限")
            kind = type(item)
            if kind in (dict, list):
                if id(item) in active:
                    fail("capacity_shape", "来源材料包含循环")
                self.charge(len(item))
                active.add(id(item))
                stack.append((item, depth, True))
                estimate += 2 + 2 * len(item)
                if kind is dict:
                    for key, child in item.items():
                        if type(key) is not str:
                            fail("capacity_shape", "来源材料键必须是字符串")
                        stack.extend(((key, depth + 1, False), (child, depth + 1, False)))
                else:
                    stack.extend((child, depth + 1, False) for child in item)
            elif kind is str:
                if self.bytes + estimate + len(item) + 2 > self.limits["bytes"]:
                    fail("capacity_budget", "来源材料UTF-8编码容量超限")
                try:
                    # Match the public compact UTF-8 encoding. Six bytes per
                    # character rejected ordinary Chinese source catalogs far
                    # below their actual output limit.
                    estimate += len(dumps(item).encode("utf-8"))
                except UnicodeError as exc:
                    raise CapacityError("capacity_shape", "来源材料Unicode无效") from exc
            elif kind is bool or item is None:
                estimate += 5
            elif kind is int:
                if item.bit_length() > 256:
                    fail("capacity_shape", "来源材料整数过大")
                estimate += len(str(item))
            elif kind is float:
                if not math.isfinite(item):
                    fail("capacity_shape", "来源材料包含非有限数值")
                estimate += len(dumps(item))
            else:
                fail("capacity_shape", "来源材料不是严格JSON值")
            if self.bytes + estimate > self.limits["bytes"]:
                fail("capacity_budget", "来源材料安全编码上界超限")
        self.bytes += estimate


def _integer(value, *, minimum=0, maximum=2**53 - 1):
    return type(value) is int and minimum <= value <= maximum


def _same(left, right):
    """Compare already budget-scanned JSON without bool/int/float coercion."""
    return dumps(left) == dumps(right)


def _ids(values, *, allow_zero=True):
    if type(values) is not list or any(not _integer(v, minimum=0 if allow_zero else 1) for v in values):
        fail("capacity_relation", "关系ID列表必须为严格整数")
    return values


def _record(value, required):
    if type(value) is not dict or not required <= value.keys():
        fail("capacity_material", "来源记录字段缺失")
    raw = value["record"]
    if type(raw) is not dict or fingerprint(raw) != value.get("node_sha256"):
        fail("capacity_material", "来源记录与完整节点指纹不一致")
    definition = value.get("definition")
    if type(definition) is not dict or definition.get("node_sha256") != value["node_sha256"]:
        fail("capacity_material", "来源记录与索引节点不一致")
    for field in ("source_path", "json_pointer"):
        if definition.get(field) != value.get(field):
            fail("capacity_material", "来源记录与索引路径不一致")
    return raw


def _material(inventory, row, declaration, budget):
    if any(type(value) is not dict for value in (inventory, row, declaration)):
        fail("capacity_shape", "来源材料必须为对象")
    try:
        source = inventory["sources"][row["source_path"]]
        budget.scan({"snapshot": inventory["snapshot"], "normalization": inventory["normalization"],
                     "source": source, "row": row, "declaration": declaration})
        recipe = _recipe()
        normalization = for_commit(inventory["snapshot"]["upstream_commit"])
        if (inventory["snapshot"]["upstream_commit"] != recipe["commit"]
                or inventory["normalization"]["additional_recipe_fingerprint"] != recipe["recipe_fingerprint"]
                or normalization.mapping_fingerprint != inventory["normalization"]["mapping_fingerprint"]):
            fail("capacity_profile", "次数材料映射版本不匹配")
        if (source["source_sha256"] != row["source_sha256"]
                or fingerprint(source["raw"]) != source["canonical_sha256"]):
            fail("capacity_material", "完整原始父图不匹配")
        try:
            full = resolve_pointer(source["raw"], row["json_pointer"])
            operation = resolve_pointer(source["raw"], row["operation_pointer"])
        except ValueError as exc:
            raise CapacityError("capacity_material", "来源材料原始指针无效") from exc
        if (type(full) is not list or not _same(full, row["raw_operations"]) or not _same(operation, row["operation"])
                or fingerprint(operation) != row["operation_node_sha256"]
                or fingerprint(full) != row["definition"]["node_sha256"]):
            fail("capacity_material", "原始操作、完整配置及节点指纹不一致")
        if not row["operation_pointer"].startswith(row["json_pointer"] + "/"):
            fail("capacity_material", "操作未隶属于指定原配置")
        first_view = normalization.normalize([operation], kind="talent_config")[0]
        normalized = {recipe["fields"].get(key, key): recipe["types"].get(value, value)
                      if key == "$type" and type(value) is str else value for key, value in first_view.items()}
        if len(normalized) != len(first_view):
            fail("capacity_operation", "规范化字段发生冲突")
        if (set(normalized) != {"$type", "skillID", "pointDelta", "CLGIKOPIKBC"}
                or not _same(normalized, row["normalized_operation"]) or normalized["$type"] != "ModifySkillPoint"
                or normalized["CLGIKOPIKBC"] is not True
                or not _integer(normalized["skillID"], minimum=1)
                or not _integer(normalized["pointDelta"], maximum=99)):
            fail("capacity_operation", "操作超出来源次数增量的封闭形状")
        fields = {"record", "definition", "source_path", "json_pointer", "source_sha256", "node_sha256"}
        avatar = _record(declaration["avatar"], fields)
        depot = _record(declaration["depot"], fields)
        talent = _record(declaration["talent"], fields)
        skill = _record(row["target_skill"], fields)
        cid, did, rank = declaration["character_id"], declaration["skill_depot_id"], declaration["constellation_rank"]
        if (not _integer(cid, minimum=1) or not _integer(did, minimum=1)
                or not _integer(rank, minimum=1, maximum=6) or not _same(avatar.get("id"), cid)
                or not _same(depot.get("id"), did)):
            fail("capacity_relation", "角色或命座选择无效")
        allowed_depots = _ids([avatar.get("skillDepotId"), *_ids(avatar.get("candSkillDepotIds", []))])
        talents = _ids(depot["talents"])
        if (did not in allowed_depots or not _integer(talent.get("talentId"), minimum=1)
                or not _same(talents[rank - 1], talent["talentId"])):
            fail("capacity_relation", "技能组或命座未隶属于该角色")
        skill_ids = _ids([*_ids(depot.get("skills", [])), *_ids(depot.get("subSkills", [])), depot.get("energySkill")])
        target = normalized["skillID"]
        if not _same(skill.get("id"), target) or target not in skill_ids or talent.get("openConfig") != row["definition"]["exact_key"]:
            fail("capacity_relation", "精确命座、技能或配置引用不匹配")
        description = declaration["description"]
        expected_pointer = "/" + str(talent.get("descTextMapHash"))
        if (description["json_pointer"] != expected_pointer or description["hash_field"] != "descTextMapHash"
                or type(description["text"]) is not str or not description["text"]
                or fingerprint(description["text"]) != description["node_sha256"]):
            fail("capacity_material", "原文描述身份不一致")
        return normalized, skill
    except (KeyError, IndexError, TypeError, UnicodeError, OverflowError) as exc:
        raise CapacityError("capacity_material", "来源材料结构不完整") from exc


def _claim(inventory, row, declaration, operation):
    return {"schema_version": 1, "operation": "add", "amount": operation["pointDelta"],
            "character_id": declaration["character_id"], "skill_depot_id": declaration["skill_depot_id"],
            "target_skill_id": operation["skillID"], "constellation_rank": declaration["constellation_rank"],
            "snapshot": copy.deepcopy(inventory["snapshot"]), "normalization": copy.deepcopy(inventory["normalization"]),
            "source_path": row["source_path"], "source_sha256": row["source_sha256"],
            "parent_canonical_sha256": inventory["sources"][row["source_path"]]["canonical_sha256"],
            "config_pointer": row["json_pointer"], "config_node_sha256": row["definition"]["node_sha256"],
            "operation_pointer": row["operation_pointer"], "operation_node_sha256": row["operation_node_sha256"],
            "record_fingerprints": {key: fingerprint(declaration[key]) for key in ("avatar", "depot", "talent", "description")},
            "target_skill_fingerprint": fingerprint(row["target_skill"]),
            "scope": "capacity_delta_only_not_initial_uses_or_cooldown_or_dispatch",
            "raw_flag_interpretation": "source_scoped_evidence_does_not_decode_global_flag"}


def source_fact_claim(inventory, row, declaration):
    """Construct a proposal identity, never an approval."""
    operation, _ = _material(inventory, row, declaration, Budget())
    return _claim(inventory, row, declaration, operation)


def project_source_delta(inventory, row, declaration, fact, *, constellation):
    budget = Budget()
    if not _integer(constellation, maximum=6):
        fail("capacity_selection", "命座必须为0至6的严格整数")
    operation, skill = _material(inventory, row, declaration, budget)
    budget.scan(fact)
    if type(fact) is not dict or set(fact) != {"id", "revision", "kind", "status", "claim", "review_scope", "assumptions", "evidence_tier"}:
        fail("capacity_fact", "次数来源事实字段不完整")
    if (type(fact["id"]) is not str or not fact["id"] or not _integer(fact["revision"], minimum=1)
            or fact["kind"] != "charge_capacity_bonus" or fact["review_scope"] != "exact_source_capacity_delta_only"
            or fact["status"] not in ("reviewed", "pending", "revoked", "stale")
            or type(fact["assumptions"]) is not list or not fact["assumptions"]
            or any(type(value) is not str or not value for value in fact["assumptions"])
            or fact["evidence_tier"] != "same_snapshot_raw_and_explicit_original_description"):
        fail("capacity_fact", "次数来源事实不在受支持合同内")
    expected = _claim(inventory, row, declaration, operation)
    matched = dumps(expected) == dumps(fact["claim"])
    return _project_delta_result(operation, skill, expected, fact, constellation,
                                 matched and fact["status"] == "reviewed", budget)


def _project_delta_result(operation, skill, expected, source, constellation, known, budget,
                          *, contract=CONTRACT, source_key="source_fact"):
    """Shared arithmetic; callers supply separately validated source authority."""
    active = constellation >= expected["constellation_rank"]
    base = skill.get("maxChargeNum")
    base = base if _integer(base, minimum=1, maximum=LIMITS["capacity"]) else None
    amount = 0 if not active else operation["pointDelta"] if known else None
    projected = base + amount if base is not None and amount is not None else None
    if projected is not None and not 1 <= projected <= LIMITS["capacity"]:
        fail("capacity_overflow", "候选次数上限超出受支持范围")
    result = {"contract": contract, "status": "inactive" if not active else "known_delta" if known else "pending",
              "verification_scope": "materialized_consistency_no_source_authority", "requires_fresh_db_admission": True,
              "target_skill_id": operation["skillID"], "base_capacity": base, "amount": amount,
              "candidate_amount": operation["pointDelta"], "candidate_projected_capacity": projected,
              "capacity": None, "capacity_closure_status": "not_evaluated", "shared_cooldown_group": None,
              "action_dispatch": "pending", "whole_skill_coverage": "partial",
              "operation_identity": fingerprint({key: expected[key] for key in ("snapshot", "source_path", "source_sha256", "operation_pointer", "operation_node_sha256", "target_skill_id")}),
              source_key: copy.deepcopy(source), "selection": {"constellation": constellation},
              "issues": ([] if known or not active else [{"code": "source_fact_unreviewed_or_stale"}])
                        + ([] if base is not None else [{"code": "base_capacity_unresolved"}]),
              "usage": {"work": budget.work, "safe_encoding_bytes": budget.bytes}, "limits": dict(LIMITS)}
    budget.scan(result)
    result["usage"] = {"work": budget.work, "safe_encoding_bytes": budget.bytes}
    result["content_fingerprint"] = fingerprint(result)
    return result


def project_current_source_delta(inventory, row, declaration, record, *, constellation):
    """Project a v2 reviewed record without rewriting its historical provenance.

    This pure function verifies retained material. A live caller must separately
    establish that this exact record is still the current approved DB head.
    Inactive constellations do not bypass either record or material validation.
    """
    from genshin_runtime.mechanism_semantics import validate_record

    budget = Budget()
    if not _integer(constellation, maximum=6):
        fail("capacity_selection", "命座必须为0至6的严格整数")
    budget.scan(record)
    try:
        validate_record(record, "fact")
    except ValueError as exc:
        raise CapacityError("capacity_fact", "当前次数来源记录合同无效") from exc
    if (record["payload"]["kind"] != "action_capacity_delta"
            or record["payload"]["claim"].get("contract") != "action-capacity-delta-v2"):
        fail("capacity_fact", "当前记录投影仅支持v2次数增量合同")
    if record["review"]["status"] != "approved":
        fail("capacity_fact_unapproved", "当前次数来源记录尚未批准或已撤销")
    operation, skill = _material(inventory, row, declaration, budget)
    expected = _claim(inventory, row, declaration, operation)
    if not _same(expected, record["payload"]["claim"]["source_claim"]):
        fail("capacity_stale", "当前完整来源与当前审核命题不一致")
    claim = record["payload"]["claim"]
    wrappers = {**{key: declaration[key] for key in ("avatar", "depot", "talent", "description")},
                "target": row["target_skill"]}
    for role, value in wrappers.items():
        if not _same(value, claim["provenance"]["record_materials"][role]["current"]):
            fail("capacity_material", "当前来源完整记录与迁移材料不一致")
    if not _same(claim["provenance"]["source_material"],
                 {"source_path": expected["source_path"], **inventory["sources"][expected["source_path"]]}):
        fail("capacity_material", "当前完整原父与迁移材料不一致")
    activation = {"kind": "constellation_slot", "rank": declaration["constellation_rank"],
                  "talent_id": declaration["talent"]["record"]["talentId"],
                  "depot_pointer": declaration["depot"]["json_pointer"] + "/talents/" + str(declaration["constellation_rank"] - 1)}
    if not _same(claim["activation"], activation):
        fail("capacity_relation", "当前激活封套与原命座槽不匹配")
    evidence = [{"id": name, "kind": "runtime_node", "locator": node["source_path"] + "#" + node["json_pointer"],
                 "sha256": node["node_sha256"]} for name, node in wrappers.items()]
    evidence.append({"id": "operation", "kind": "runtime_node", "locator": row["source_path"] + "#" + row["operation_pointer"],
                     "sha256": row["operation_node_sha256"]})
    if not _same(record["evidence"], evidence) or record["payload"]["evidence_refs"] != [e["id"] for e in evidence]:
        fail("capacity_material", "当前完整原节点证据不匹配")
    result = _project_delta_result(
        operation, skill, expected, record, constellation, True, budget,
        contract="source-bound-charge-capacity-delta-v2", source_key="source_record",
    )
    result.pop("content_fingerprint")
    result.update(verification_scope="materialized_source_and_reviewed_record_no_live_head",
                  requires_fresh_db_admission=True, execution_authority=False)
    result["content_fingerprint"] = fingerprint(result)
    return result
