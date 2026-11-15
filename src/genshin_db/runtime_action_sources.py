"""Fresh source-delta and related-root admission, inside one shared read transaction."""
from __future__ import annotations

import copy
import re

from genshin_db.runtime_capacity_delta import (
    Budget,
    CapacityError,
    project_current_source_delta,
    project_source_delta,
    source_fact_claim,
)
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint, resolve_pointer
from genshin_db.runtime_queries import (
    _definition_summary,
    _lookup,
    _Nodes,
    _read,
    _references,
    _row,
)
from genshin_db.runtime_talent_level import _recipe
from genshin_db.semantic_store import (
    HEAD_TABLE,
    TABLES,
    read_current_reviewed_record,
    validate_component,
)
from genshin_runtime.action_resource_semantics import ACTION_FACT_KINDS
from genshin_runtime.normalization import for_commit

MAX_FACTS = 32


def action_source_capabilities():
    return {"contract": "fresh-action-source-profile-v1", "fact_kinds": sorted(ACTION_FACT_KINDS),
            "required": ["character_id", "constellation"], "max_facts": MAX_FACTS,
            "scope": "精确来源增量及相关候选根；不计算总容量或授予默认挂载",
            "fact_refs": "可省略，枚举该角色当前head；显式引用用{id,revision}锁定当前修订",
            "execution_authority": False, "capacity": None, "read_only": True}


def _selection(character_id, skill_depot_id, constellation, fact_refs):
    from genshin_db.runtime_queries import _integer
    _integer(character_id, "character_id")
    _integer(constellation, "constellation", 0, 6)
    if skill_depot_id is not None:
        _integer(skill_depot_id, "skill_depot_id")
    if fact_refs is not None:
        if type(fact_refs) is not list or len(fact_refs) > MAX_FACTS:
            raise ValueError("动作来源引用超限")
        seen = set()
        for ref in fact_refs:
            if (type(ref) is not dict or set(ref) != {"id", "revision"} or type(ref["id"]) is not str
                    or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", ref["id"])):
                raise ValueError("动作来源引用须为{id,revision}")
            _integer(ref["revision"], "revision", 1, 2**31 - 1)
            if ref["id"] in seen:
                raise ValueError("动作来源引用重复")
            seen.add(ref["id"])


def _refs(conn, line, character_id, requested):
    if requested is not None:
        return requested
    if validate_component(conn, line)["status"] != "ready":
        return []
    rows = conn.execute(
        f"SELECT h.record_id,h.revision FROM {HEAD_TABLE} h JOIN {TABLES['fact']} f "
        "ON f.version_line=h.version_line AND f.record_id=h.record_id AND f.revision=h.revision "
        "WHERE h.version_line=? AND h.record_kind='fact' "
        "AND json_extract(f.subject_json,'$.target_type')='character' "
        "AND json_extract(f.subject_json,'$.target_id')=? "
        "AND json_extract(f.payload_json,'$.kind') IN ('action_capacity_delta','related_action_root') "
        "ORDER BY h.record_id LIMIT ?", (line, character_id, MAX_FACTS + 1)).fetchall()
    if len(rows) > MAX_FACTS:
        raise CapacityError("capacity_budget", "当前角色动作事实超过数量预算")
    return [{"id": row[0], "revision": row[1]} for row in rows]


def _exact(conn, nodes, line, table, key):
    rows = _lookup(conn, line, "excel_record", key, f"ExcelBinOutput/{table}ExcelConfigData.json")
    if len(rows) != 1:
        raise RuntimeConfigError("动作来源记录缺失或不唯一", "source_identity_unresolved", 409)
    row = rows[0]
    raw, origin = nodes.node(row)
    return {"definition": _definition_summary(row), **origin, "node_sha256": row["node_sha256"], "record": raw}


def _text(nodes, record, field):
    source, raw = nodes.source("TextMap/TextMap_MediumCHS.json")
    key = record["record"].get(field)
    if type(key) is not int or type(raw) is not dict or type(raw.get(str(key))) is not str:
        raise RuntimeConfigError("原文节点缺失或格式错误", "source_text_unresolved", 409)
    value = raw[str(key)]
    return {"text": value, "source_path": "TextMap/TextMap_MediumCHS.json",
            "source_sha256": source["source_sha256"], "json_pointer": "/" + str(key),
            "node_sha256": fingerprint(value), "hash_field": field}


def fresh_delta_material(conn, state, record):
    """Reconstruct every old source-claim input from fresh, exact original nodes."""
    claim = record["payload"]["claim"]["source_claim"]
    line = state["version_line"]
    nodes = _Nodes(conn, line)
    avatar = _exact(conn, nodes, line, "Avatar", {"id": claim["character_id"]})
    depot = _exact(conn, nodes, line, "AvatarSkillDepot", {"id": claim["skill_depot_id"]})
    rank = claim["constellation_rank"]
    talents = depot["record"].get("talents")
    if type(talents) is not list or len(talents) != 6 or any(type(v) is not int or v <= 0 for v in talents):
        raise RuntimeConfigError("命座槽目录无效", "source_identity_unresolved", 409)
    talent = _exact(conn, nodes, line, "AvatarTalent", {"talentId": talents[rank - 1]})
    skill = _exact(conn, nodes, line, "AvatarSkill", {"id": claim["target_skill_id"]})
    rows = _lookup(conn, line, "talent_config", talent["record"].get("openConfig"), claim["source_path"])
    if len(rows) != 1:
        raise RuntimeConfigError("命座配置缺失或不唯一", "source_identity_unresolved", 409)
    definition = rows[0]
    full, origin = nodes.node(definition)
    source, parent = nodes.source(claim["source_path"])
    try:
        operation = resolve_pointer(parent, claim["operation_pointer"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CapacityError("capacity_material", "原始次数操作指针无效") from exc
    recipe = _recipe()
    norm = for_commit(state["upstream_commit"])
    if norm is None or state["upstream_commit"] != recipe["commit"]:
        raise CapacityError("capacity_profile", "次数来源归一化版本未支持")
    first = norm.normalize([operation], kind="talent_config")[0]
    normalized = {recipe["fields"].get(k, k): recipe["types"].get(v, v)
                  if k == "$type" and type(v) is str else v for k, v in first.items()}
    inventory = {"snapshot": state, "normalization": {"mapping_fingerprint": norm.mapping_fingerprint,
                  "additional_recipe_fingerprint": recipe["recipe_fingerprint"]},
                 "sources": {claim["source_path"]: {"source_sha256": source["source_sha256"],
                             "canonical_sha256": fingerprint(parent), "raw": parent}}}
    row = {"definition": _definition_summary(definition), **origin,
           "operation_pointer": claim["operation_pointer"], "operation_node_sha256": fingerprint(operation),
           "raw_operations": full, "operation": operation, "normalized_operation": normalized, "target_skill": skill}
    declaration = {"character_id": claim["character_id"], "skill_depot_id": claim["skill_depot_id"],
                   "constellation_rank": rank, "avatar": avatar, "depot": depot, "talent": talent,
                   "description": _text(nodes, talent, "descTextMapHash")}
    return inventory, row, declaration


def _delta(conn, state, record, constellation):
    inventory, row, declaration = fresh_delta_material(conn, state, record)
    return project_delta_material(inventory, row, declaration, record, constellation)


def project_delta_material(inventory, row, declaration, record, constellation):
    """Pure historical-material validation; this grants no current DB authority."""
    payload, claim = record["payload"], record["payload"]["claim"]
    if claim.get("contract") == "action-capacity-delta-v2":
        result = project_current_source_delta(inventory, row, declaration, record, constellation=constellation)
        result.pop("source_record")
        result.pop("content_fingerprint")
        from genshin_runtime.mechanism_semantics import (
            record_payload_fingerprint,
            record_review_fingerprint,
        )
        result.update(source_record_identity={"id": record["id"], "revision": record["revision"],
                      "payload_fingerprint": record_payload_fingerprint(record, "fact"),
                      "review_fingerprint": record_review_fingerprint(record, "fact")},
                      evidence_route=claim["evidence_route"], description=declaration["description"])
        return result
    if fingerprint(source_fact_claim(inventory, row, declaration)) != fingerprint(claim["source_claim"]):
        raise CapacityError("capacity_stale", "当前完整来源与原审核命题不一致")
    expected_activation = {"kind": "constellation_slot", "rank": declaration["constellation_rank"],
                           "talent_id": declaration["talent"]["record"]["talentId"],
                           "depot_pointer": declaration["depot"]["json_pointer"] + "/talents/" + str(declaration["constellation_rank"] - 1)}
    if fingerprint(claim["activation"]) != fingerprint(expected_activation):
        raise CapacityError("capacity_relation", "新激活封套与原命座槽不匹配")
    expected_evidence = []
    for name, node in [(k, declaration[k]) for k in ("avatar", "depot", "talent", "description")] + [("target", row["target_skill"])]:
        expected_evidence.append({"id": name, "kind": "runtime_node", "locator": node["source_path"] + "#" + node["json_pointer"],
                                  "sha256": node["node_sha256"]})
    expected_evidence.append({"id": "operation", "kind": "runtime_node", "locator": row["source_path"] + "#" + row["operation_pointer"],
                              "sha256": row["operation_node_sha256"]})
    if (fingerprint(record["evidence"]) != fingerprint(expected_evidence)
            or payload["evidence_refs"] != [e["id"] for e in expected_evidence]):
        raise CapacityError("capacity_material", "完整原节点证据不匹配")
    prior = claim["prior_review"]
    legacy = {"id": prior["original_fact_id"], "revision": prior["original_revision"], "kind": "charge_capacity_bonus",
              "status": "reviewed", "claim": claim["source_claim"], "review_scope": prior["scope"],
              "assumptions": prior["assumptions"], "evidence_tier": prior["original_evidence_tier"]}
    result = project_source_delta(inventory, row, declaration, legacy, constellation=constellation)
    if result["status"] == "pending":
        raise CapacityError("capacity_stale", "当前来源与审核命题不一致")
    result.pop("source_fact")
    result.update(verification_scope="fresh_source_and_current_review_head",
                  requires_fresh_db_admission=False, execution_authority=False,
                  evidence_route=claim["evidence_route"], description=declaration["description"])
    result.pop("content_fingerprint", None)
    return result


def _related(conn, state, record):
    from genshin_runtime.action_root_semantics import match_related_root_material
    claim, nodes = record["payload"]["claim"], _Nodes(conn, state["version_line"])
    values = []
    for bound in claim["nodes"]:
        row = _row(conn, state["version_line"], bound["definition"]["definition_id"])
        raw, origin = nodes.node(row)
        values.append({"id": bound["id"], "definition": _definition_summary(row), **origin, "raw": raw})
    config = next(n for n in values if n["id"] == claim["roles"]["config"])
    material = {"snapshot": state, "nodes": values,
                "config_references": _references(conn, state["version_line"], config["definition"]["definition_id"], nodes)}
    head_material = {"record_id": record["id"], "revision": record["revision"],
                     "record_fingerprint": fingerprint({"record": record})}
    result = match_related_root_material(material, record, head_material)
    result.update(verification_scope="fresh_source_and_current_review_head",
                  requires_fresh_registry_admission=False, material=material)
    return result


def read_action_sources(conn, state, *, character_id, constellation, skill_depot_id=None, fact_refs=None):
    _selection(character_id, skill_depot_id, constellation, fact_refs)
    if not conn.in_transaction:
        raise CapacityError("capacity_transaction", "动作事实必须在调用者事务内鲜读")
    # Resolve the actual selected depot before consulting its facts, even if the
    # catalog is empty. A omitted depot is not a wildcard across transformations.
    nodes = _Nodes(conn, state["version_line"])
    avatar = _exact(conn, nodes, state["version_line"], "Avatar", {"id": character_id})["record"]
    others = avatar.get("candSkillDepotIds", [])
    if type(others) is not list:
        raise CapacityError("capacity_selection", "角色技能组关系格式错误")
    ids = [avatar.get("skillDepotId"), *others]
    if any(type(x) is not int or x < 0 for x in ids):
        raise CapacityError("capacity_selection", "角色技能组关系要求严格整数")
    ids = list(dict.fromkeys(x for x in ids if x))
    if skill_depot_id is None:
        if len(ids) != 1:
            raise RuntimeConfigError("须明确角色技能组", "skill_depot_selection_required", 409)
        skill_depot_id = ids[0]
    if skill_depot_id not in ids:
        raise CapacityError("capacity_selection", "技能组不属于所选角色")
    budget, results = Budget(), []
    refs = _refs(conn, state["version_line"], character_id, fact_refs)
    _selection(character_id, skill_depot_id, constellation, refs)
    for ref in refs:
        current = read_current_reviewed_record(conn, state["version_line"], "fact", ref["id"], ref["revision"])
        item = {"id": ref["id"], "revision": ref["revision"], "review": current["receipt"],
                "status": "pending", "reason": current["reason"], "projection": None}
        if current["status"] == "ready":
            record = {k: current["record"][k] for k in ("id", "revision", "subject", "payload", "evidence", "applicability", "review")}
            budget.scan(record)
            kind, claim = record["payload"]["kind"], record["payload"]["claim"]
            if kind not in ACTION_FACT_KINDS or record["subject"] != {"target_type": "character", "target_id": character_id}:
                raise CapacityError("capacity_selection", "动作事实不属于所选角色或合同")
            if kind == "action_capacity_model":
                raise CapacityError("capacity_profile_required", "完整容量模型请使用action_capacity_profile")
            if kind == "action_admission_model":
                raise CapacityError("action_admission_profile_required", "动作接纳模型请使用action_admission_profile")
            identity = claim["source_claim"] if kind == "action_capacity_delta" else claim["selection"]
            if skill_depot_id is not None and identity["skill_depot_id"] != skill_depot_id:
                raise CapacityError("capacity_selection", "动作事实不属于所选技能组")
            snap = claim["source_claim"]["snapshot"] if kind == "action_capacity_delta" else claim["snapshot"]
            if fingerprint(snap) != fingerprint(state):
                item["reason"] = "source_snapshot_changed"
            else:
                item.update(status="known_source_delta" if kind == "action_capacity_delta" else "corroborated_root",
                            reason=None, fact=record)
                item["projection"] = _delta(conn, state, record, constellation) if kind == "action_capacity_delta" else _related(conn, state, record)
        budget.scan(item)
        results.append(item)
    result = {"contract": "fresh-action-source-profile-v1", "snapshot": copy.deepcopy(state),
              "selection": {"character_id": character_id, "skill_depot_id": skill_depot_id, "constellation": constellation},
              "sources": results, "source_count": len(results), "capacity": None,
              "capacity_closure_status": "not_evaluated", "native_ability_dispatch": "pending",
              "execution_authority": False, "semantic_catalog": validate_component(conn, state["version_line"])["status"]}
    budget.scan(result)
    result["content_fingerprint"] = fingerprint(result)
    return copy.deepcopy(result)


def get_action_source_profile(db_path, *, character_id, constellation, skill_depot_id=None,
                              fact_refs=None, version_line="release", snapshot_id=None):
    _selection(character_id, skill_depot_id, constellation, fact_refs)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return read_action_sources(conn, state, character_id=character_id, constellation=constellation,
                                   skill_depot_id=skill_depot_id, fact_refs=fact_refs)


def get_action_activation_catalog(db_path, *, character_id, constellation, skill_depot_id=None,
                                  base_level=10, character_level=None, ascension=None, fact_refs=None,
                                  version_line="release", snapshot_id=None, catalog_view="all", page=1, page_size=20):
    from genshin_db.runtime_action_activation import (
        CATALOG_BUDGET,
        collect_activation_catalog,
        validate_catalog_selection,
    )
    from genshin_db.runtime_public_root import get_public_root_catalog
    _selection(character_id, skill_depot_id, constellation, fact_refs)
    validate_catalog_selection(character_id, skill_depot_id, constellation, base_level, character_level, ascension)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        result = collect_activation_catalog(conn, state, db_path=db_path, character_id=character_id,
            skill_depot_id=skill_depot_id, constellation=constellation, base_level=base_level,
            character_level=character_level, ascension=ascension)
        result.pop("content_fingerprint", None)
        result["contract"] = "fresh-action-activation-catalog-v1"
        result["action_sources"] = read_action_sources(conn, state, character_id=character_id,
            skill_depot_id=result["selection"]["skill_depot_id"], constellation=constellation, fact_refs=fact_refs)
        result["corroborated_roots"] = [item["id"] for item in result["action_sources"]["sources"]
                                       if item["status"] == "corroborated_root"]
        result["public_root_material"] = get_public_root_catalog(db_path, version_line=version_line,
                                                               snapshot_id=state["snapshot_id"])
        result["root_directory_scope"] = "listed_depot_roots_plus_explicit_unproven_base_related_and_public_material"
        budget = Budget(limits=CATALOG_BUDGET)
        budget.scan(result)
        result["combined_usage"] = {"work": budget.work, "safe_encoding_bytes": budget.bytes}
        result["content_fingerprint"] = fingerprint(result)
        from genshin_db.runtime_catalog_projection import project_activation_catalog
        return project_activation_catalog(result, view=catalog_view, page=page, page_size=page_size)
