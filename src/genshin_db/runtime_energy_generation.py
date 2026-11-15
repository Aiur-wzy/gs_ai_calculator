"""Exact material identities and a finite reference unit projection; no dispatch."""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from importlib import resources

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    fingerprint,
    resolve_pointer,
)

GRAPH_SCOPE = "energy_generation_identity_v1"
MATERIAL_PATH = "ExcelBinOutput/MaterialExcelConfigData.json"
ENERGY_LIMITS = {"max_bytes": 256 * 1024, "max_nodes": 16, "max_candidates": 8,
                 "max_edges": 64, "max_item_use": 8, "max_issues": 64}


@lru_cache(maxsize=1)
def _recipe():
    data = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/energy_generation_profiles.json").read_text("utf8"))
    data["recipe_fingerprint"] = fingerprint(data)
    return data


def _contract():
    recipe = _recipe()
    return {key: copy.deepcopy(recipe[key]) for key in
        ("profile_id", "profile_version", "evidence_tier", "reference_commit", "reference_repository",
         "recipe_fingerprint", "field_mapping", "elements", "standard_units", "domain")} | {
        "reference_sources": copy.deepcopy(recipe["reference_sources"]),
        "limits": ["局部参考实现，非官方客户端真值；不采用按角色的历史fallback",
                   "单位ready不证明父条件、实际生成、逐粒监听或拾取自动调用",
                   "useOnGain缺失不否决单位投影；实际拾取执行仍未评估"]}


def energy_generation_capabilities():
    return {"projection_version": 1, "graph_scope": GRAPH_SCOPE, "minimum_indexer_version": "6",
        "selection": "必填origin_reference_id为原legacy GenerateElemBall.configID精确引用；不另填Material ID",
        "limits": dict(ENERGY_LIMITS), "reference_contract": _contract(),
        "upgrade_reason": "material_index_upgrade_required", "whole_source_status": "partial",
        "execution_support": "not_evaluated",
         "quantity_scope": "局部单位投影仅覆盖受审固定整商。概率分支仍是来源资料，可由作者核实后显式声明平均数量，不由此查询自动派发。",
        "status": "ready仅证明所选来源关系和局部参考单位；父条件与触发均未执行"}


def _invalid(message):
    raise RuntimeConfigError(message, "dataset_invalid", 500)


def _limit(kind):
    raise RuntimeConfigError("产能完整来源超过查询限额；未截断原文", "response_too_large", 413,
                             {"limit_kind": kind, "limits": dict(ENERGY_LIMITS)})


def _field_state(mapping, key, expected):
    if key not in mapping:
        return "absent"
    value = mapping[key]
    if value is None:
        return "null"
    if not expected(value):
        return "invalid_type"
    return "empty" if value in ([], {}, "") else "present"


def _number(value):
    # Compare bounded magnitudes before int/float conversion (huge JSON ints are legal).
    return type(value) in (int, float) and 0 < value <= _recipe()["domain"]["max_integer"] and int(value) == value


def _reference(row):
    data = dict(row)
    for key in ("target_key_json", "scope_json", "candidate_ids_json"):
        data[key.removesuffix("_json")] = json.loads(data.pop(key))
    return data


class _Reader:
    def __init__(self, conn, state):
        from genshin_db.runtime_queries import _Nodes
        self.conn, self.state, self.line = conn, state, state["version_line"]
        self.nodes = _Nodes(conn, self.line)
        self.entries, self.issues = {}, []
        self.candidate_index = {}
        self.node_bytes = self.edge_count = 0

    def issue(self, code, path):
        if len(self.issues) >= ENERGY_LIMITS["max_issues"]:
            _limit("issues")
        self.issues.append({"code": code, "path": path})

    def identity(self, row, raw):
        if row["definition_kind"] != "material_record":
            # Reuse the already reviewed source/owner identity reconstruction,
            # never its Gadget graph traversal, budgets, or resolution rules.
            from genshin_db.runtime_gadget_source import _Reader as IdentityReader
            return IdentityReader.identity(self, row, raw)
        path, pointer = row["source_path"], row["json_pointer"]
        source, root = self.nodes.source(path)
        if (path != MATERIAL_PATH or source["source_family"] != "material_excel"
                or re.fullmatch(r"/(0|[1-9][0-9]*)", pointer) is None
                or not isinstance(root, list) or not isinstance(raw, dict)
                or type(raw.get("id")) is not int or not 1 <= raw["id"] <= 2**53 - 1):
            _invalid("Material身份不对应原Excel行")
        expected = {"definition_id": fingerprint([self.state["snapshot_id"], path, pointer, "material_record"]),
            "exact_key_json": dumps(raw["id"]), "owner_definition_id": None, "variant_key": None,
            "namespace_key": "material_excel", "summary_json": dumps({"name": raw["id"], "kind": "material_record"})}
        if any(row[k] != v for k, v in expected.items()):
            _invalid("Material索引身份与完整原文不一致")

    def entry(self, row):
        from genshin_db.runtime_queries import _definition_summary
        if row["version_line"] != self.line or row["snapshot_id"] != self.state["snapshot_id"]:
            raise RuntimeConfigError("产能来源节点跨快照", "mixed_snapshot", 500)
        ident = row["definition_id"]
        if ident not in self.entries:
            if len(self.entries) >= ENERGY_LIMITS["max_nodes"]:
                _limit("nodes")
            raw, source = self.nodes.node(row)
            self.identity(row, raw)
            value = {"definition": _definition_summary(row), **source,
                     "node_sha256": row["node_sha256"], "payload": raw}
            self.node_bytes += len(dumps(value).encode("utf8"))
            if self.node_bytes > ENERGY_LIMITS["max_bytes"]:
                _limit("node_bytes")
            self.entries[ident] = value
        return self.entries[ident]

    def edges(self, rows):
        self.edge_count += len(rows)
        if self.edge_count > ENERGY_LIMITS["max_edges"]:
            _limit("edges")
        return rows

    def precheck_edges(self, table, definition_id):
        # These fixed table names are internal; never interpolate caller input.
        if table not in ("runtime_reference", "runtime_material_reference"):
            raise AssertionError("unsupported relation table")
        remaining = ENERGY_LIMITS["max_edges"] - self.edge_count
        rows = self.conn.execute(
            f"SELECT reference_id,target_kind,target_key_json FROM {table} "
            "WHERE version_line=? AND from_definition_id=? LIMIT ?",
            (self.line, definition_id, remaining + 1)).fetchall()
        if len(rows) > remaining:
            _limit("edges")
        return rows

    def bounded_candidates(self, kind, key_json):
        key = (kind, key_json)
        if key not in self.candidate_index:
            limit = ENERGY_LIMITS["max_candidates"]
            rows = self.conn.execute(
                "SELECT definition_id FROM runtime_definition WHERE version_line=? "
                "AND definition_kind=? AND exact_key_json=? LIMIT ?",
                (self.line, kind, key_json, limit + 1)).fetchall()
            if len(rows) > limit:
                _limit("material_candidates")
            self.candidate_index[key] = [dict(row) for row in rows]
        return self.candidate_index[key]

    def origin(self, ident):
        from genshin_db.runtime_queries import _references, _row
        from genshin_db.runtime_store import validate_definition_material_references
        stored = self.conn.execute("SELECT * FROM runtime_reference WHERE version_line=? AND reference_id=?",
                                   (self.line, ident)).fetchone()
        if stored is None:
            raise RuntimeConfigError("原产能引用未收录", "not_found", 404, {"origin_reference_id": ident})
        row = _row(self.conn, self.line, stored["from_definition_id"])
        if row["definition_kind"] not in ("ability", "modifier"):
            raise RuntimeConfigError("来源不是受支持Ability/modifier产能引用", "unsupported_source", 409)
        parent = self.entry(row)
        self.precheck_edges("runtime_reference", row["definition_id"])
        legacy = next((r for r in self.edges(_references(self.conn, self.line, row["definition_id"], self.nodes))
                       if r["reference_id"] == ident), None)
        if legacy is None:
            _invalid("产能原引用未通过原文重建")
        base, pointer = row["json_pointer"], legacy["json_pointer"]
        if not pointer.startswith(base + "/"):
            _invalid("产能原引用不在所属完整定义中")
        relative = pointer[len(base):]
        operation_relative = relative.rsplit("/", 1)[0]
        raw = resolve_pointer(parent["payload"], operation_relative)
        canonical = self.nodes.normalization.normalize(raw, kind="operation") if self.nodes.normalization else raw
        if not isinstance(canonical, dict) or canonical.get("$type") != "GenerateElemBall":
            raise RuntimeConfigError("origin不是GenerateElemBall引用", "unsupported_source", 409)
        raw_id = resolve_pointer(parent["payload"], relative)
        expected_pointer = "/configID"
        if self.nodes.normalization:
            expected_pointer = self.nodes.normalization.raw_pointer(raw, expected_pointer, kind="operation")
        if (relative != operation_relative + expected_pointer or legacy["relation_kind"] != "external_asset"
                or dumps(canonical.get("configID")) != dumps(raw_id)
                or dumps(legacy["target_key"]) != dumps(raw_id)):
            raise RuntimeConfigError("origin不是GenerateElemBall.configID精确引用", "unsupported_source", 409)
        material_rows = self.precheck_edges("runtime_material_reference", row["definition_id"])
        # The shared validator checks every sibling Material edge. Supply a
        # bounded exact index so its internal candidate reads cannot expand an
        # unselected sibling target before the public lookup is reached.
        for ref in material_rows:
            self.bounded_candidates(ref["target_kind"], ref["target_key_json"])
        links = self.edges([_reference(r) for r in validate_definition_material_references(
            self.conn, self.line, row, parent["payload"], index=self.candidate_index, normalization=self.nodes.normalization,
            indexer_version=self.nodes.indexer_version)])
        matches = [r for r in links if r["legacy_reference_id"] == ident]
        if len(matches) != 1:
            _invalid("index6受支持产能引用缺少唯一Material边")
        link = matches[0]
        if (link["relation_kind"] != "element_ball_material" or link["target_kind"] != "material_record"
                or any(dumps(link[k]) != dumps(legacy[k]) for k in ("source_path", "json_pointer", "from_definition_id", "target_key"))
                or link["version_line"] != self.line or link["snapshot_id"] != self.state["snapshot_id"]):
            _invalid("新旧产能引用关系身份不一致")
        owner = None
        if row["definition_kind"] == "modifier":
            owner_row = _row(self.conn, self.line, row["owner_definition_id"])
            if (owner_row["definition_kind"] != "ability" or owner_row["source_path"] != row["source_path"]
                    or not base.startswith(owner_row["json_pointer"] + "/")):
                _invalid("产能modifier缺少精确owner Ability")
            owner = self.entry(owner_row)
            if resolve_pointer(owner["payload"], base[len(owner_row["json_pointer"]):]) != parent["payload"]:
                _invalid("产能modifier与完整owner原文不一致")
        fields, mapping = _fields(raw, self.state["upstream_commit"], self.issue)
        return {"reference": legacy, "material_reference": link, "parent": parent, "owner": owner,
            "operation": {"raw_pointer": base + operation_relative, "node_sha256": fingerprint(raw), "raw": raw,
                          "normalized_fields": fields, "mapping_status": mapping}}, link

    def material(self, link):
        from genshin_db.runtime_queries import _lookup
        if link["resolution_status"] in ("source_empty", "invalid_reference_form"):
            if link["candidate_ids"] or link["resolved_definition_id"] is not None:
                _invalid("未解析原始Material ID含有伪造候选")
            self.issue("energy_material_id_" + link["resolution_status"], "origin.operation.configID")
            return {"status": link["resolution_status"], "candidates": [], "selected_definition_id": None,
                "field_states": {"itemUse": "absent", "useTarget": "absent", "useOnGain": "absent"}, "item_use_entries": []}
        self.bounded_candidates("material_record", dumps(link["target_key"]))
        rows = _lookup(self.conn, self.line, "material_record", link["target_key"], MATERIAL_PATH)
        if len(rows) > ENERGY_LIMITS["max_candidates"]:
            _limit("material_candidates")
        candidates = [self.entry(r) for r in rows]
        if len(rows) > 1:
            _invalid("Material精确ID不唯一")
        ids = [r["definition_id"] for r in rows]
        if set(ids) != set(link["candidate_ids"]):
            _invalid("Material边候选与精确原行集合不一致")
        selected = ids[0] if ids else None
        expected = "resolved" if selected else "missing"
        if link["resolution_status"] != expected or link["resolved_definition_id"] != selected:
            _invalid("Material边解析状态与完整原行不一致")
        raw = candidates[0]["payload"] if candidates else {}
        if isinstance(raw.get("itemUse"), list) and len(raw["itemUse"]) > ENERGY_LIMITS["max_item_use"]:
            _limit("item_use")
        if not selected:
            self.issue("material_target_missing", "material")
        return {"status": expected, "candidates": candidates, "selected_definition_id": selected,
            "field_states": {"itemUse": _field_state(raw, "itemUse", lambda v: isinstance(v, list)),
                "useTarget": _field_state(raw, "useTarget", lambda v: isinstance(v, str)),
                "useOnGain": _field_state(raw, "useOnGain", lambda v: type(v) is bool)}, "item_use_entries": []}


def _fields(raw, commit, issue):
    recipe, keys = _recipe(), {k: k for k in ("configID", "baseEnergy", "ratio")}
    mapping = recipe["field_mapping"]
    if raw.get("$type") != "GenerateElemBall":
        if raw.get("$type") != mapping["operation_type"] or commit != mapping["upstream_commit"]:
            issue("energy_field_mapping_unverified", "origin.operation")
            return {}, "pending"
        keys = mapping["fields"]
    # Both spelling families in one operation are ambiguous, even equal-valued.
    other = mapping["fields"] if keys["ratio"] == "ratio" else {k: k for k in keys}
    if any(other[k] in raw for k in keys):
        issue("energy_field_alias_conflict", "origin.operation")
        return {}, "pending"
    return {k: {"state": _field_state(raw, v, lambda x: type(x) in (int, float)),
                "raw_field": v, "raw_value": copy.deepcopy(raw.get(v))} for k, v in keys.items()}, "ready"


def _projection(origin, material, reader):
    fields, issues_at = origin["operation"]["normalized_fields"], len(reader.issues)
    field = lambda k: fields.get(k, {"state": "unmapped", "raw_value": None})
    base, ratio = field("baseEnergy"), field("ratio")
    result = {"status": "pending", "raw_values": {"base_energy": base["raw_value"], "ratio": ratio["raw_value"],
        "config_id": origin["material_reference"]["target_key"]},
        "field_states": {"base_energy": base["state"], "ratio": ratio["state"]},
        "formula": "ceil(baseEnergy * ratio / material_same_element_energy)",
        "arithmetic": "positive_integer_exact_quotient_v1",
        "unit": {"element": None, "same_element_energy": None, "different_element_energy": None},
        "particle_type": None, "count": None, "mapping_status": "pending", "evidence_tier": "reference_implementation"}
    if origin["operation"]["mapping_status"] != "ready" or material["status"] != "resolved":
        return result
    for name, value in (("base_energy", base), ("ratio", ratio)):
        if value["state"] != "present" or not _number(value["raw_value"]):
            reader.issue("energy_number_outside_reference_domain", "reference_projection." + name)
    raw = material["candidates"][0]["payload"]
    for key, value in (("itemType", "ITEM_MATERIAL"), ("materialType", "MATERIAL_ADSORBATE"),
                       ("useTarget", "ITEM_USE_TARGET_CUR_TEAM")):
        if raw.get(key) != value:
            reader.issue("energy_material_scope_unsupported", "material." + key)
    uses = raw.get("itemUse")
    if not isinstance(uses, list) or not uses:
        reader.issue("energy_material_item_use_missing_or_invalid", "material.itemUse")
        return result
    if len(uses) > ENERGY_LIMITS["max_item_use"]:
        _limit("item_use")
    for i, use in enumerate(uses):
        state = "unsupported"
        if isinstance(use, dict):
            params = use.get("useParam")
            if i == 0 and set(use) == {"useOp", "useParam"} and use["useOp"] == "ITEM_USE_ADD_ELEM_ENERGY":
                state = "effective"
            elif (i > 0 and set(use) in ({"useParam"}, {"useOp", "useParam"})
                  and ("useOp" not in use or use["useOp"] == "ITEM_USE_NONE")
                  and isinstance(params, list) and len(params) in _recipe()["domain"]["empty_tail_lengths"]
                  and all(type(v) is str and v == "" for v in params)):
                state = "empty_placeholder"
        material["item_use_entries"].append({"index": i, "raw_pointer":
            material["candidates"][0]["json_pointer"] + "/itemUse/" + str(i), "state": state})
        if state == "unsupported":
            reader.issue("energy_material_item_use_unsupported", f"material.itemUse[{i}]")
    first = uses[0] if isinstance(uses[0], dict) else {}
    params = first.get("useParam")
    if (not isinstance(params, list) or not 3 <= len(params) <= 8
            or any(type(v) is not str or re.fullmatch(r"0|[1-9][0-9]{0,7}", v) is None for v in params[:3])
            or any(type(v) is not str or v != "" for v in params[3:])):
        reader.issue("energy_material_parameters_unsupported", "material.itemUse[0].useParam")
        return result
    element, same, different = params[0], int(params[1]), int(params[2])
    unit = next((u for u in _recipe()["standard_units"] if (u["same"], u["different"]) == (same, different)), None)
    if element not in _recipe()["elements"] or unit is None:
        reader.issue("energy_standard_unit_unsupported", "material.itemUse[0].useParam")
    result["unit"] = {"element": _recipe()["elements"].get(element), "same_element_energy": same,
                      "different_element_energy": different}
    if len(reader.issues) != issues_at:
        return result
    product = int(base["raw_value"]) * int(ratio["raw_value"])
    if product > _recipe()["domain"]["max_product"]:
        reader.issue("energy_product_outside_reference_domain", "reference_projection")
    elif product % same:
        reader.issue("non_integral_reference_domain", "reference_projection")
    elif not 1 <= product // same <= _recipe()["domain"]["max_count"]:
        reader.issue("energy_count_outside_reference_domain", "reference_projection")
    else:
        result.update(status="ready", count=product // same, particle_type=unit["particle_type"],
                      mapping_status="standard_energy_model_projection")
    return result


def energy_generation_source_payload(profile):
    """Pure content identity, not authorization to substitute for a fresh query."""
    origin, material = profile["origin"], profile["material"]
    def node(value):
        return None if value is None else {k: value[k] for k in
            ("definition", "source_path", "source_sha256", "json_pointer")} | {"raw_sha256": fingerprint(value["payload"])}
    return {"projection_version": profile["projection_version"], "graph_scope": profile["graph_scope"],
        "snapshot": {k: profile["snapshot"][k] for k in
            ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization") if k in profile["snapshot"]},
        "selection": profile["selection"], "reference_contract": profile["reference_contract"],
        "origin": {"reference": origin["reference"], "material_reference": origin["material_reference"],
            "parent": node(origin["parent"]), "owner": node(origin["owner"]),
            "operation_pointer": origin["operation"]["raw_pointer"], "operation_raw_sha256": fingerprint(origin["operation"]["raw"])},
        "material": {"status": material["status"], "candidates": [node(c) for c in material["candidates"]]},
        "reference_projection": profile["reference_projection"]}


def get_runtime_energy_generation_profile(db_path, *, origin_reference_id, version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _read
    if not isinstance(origin_reference_id, str) or re.fullmatch(r"[0-9a-f]{64}", origin_reference_id) is None:
        from genshin_db.validation import QueryValidationError
        error = QueryValidationError(
            "origin_reference_id 需要运行配置关系条目的64位小写十六进制引用ID，"
            "不是角色ID、技能ID、路径或技能资料指纹。先按角色查询运行配置关系，"
            "再复制产球 configID 关系返回的 reference_id 作为 origin_reference_id；原请求尚未读取数据库，"
            "无需更新数据库。")
        error.details = {"field": "origin_reference_id", "actual": origin_reference_id,
                         "expected": "64 lowercase hexadecimal characters from runtime material reference",
                         "execution_state": "not_started", "recovery": {
                             "action": "query_runtime_material_reference", "retry_same_request": False,
                             "navigation": "query_runtime(action=roots,characters=[{id:当前角色ID,constellation:当前命座}]) → action=closure,roots=返回的定义ID → references中GenerateElemBall.configID对应的reference_id；也可在definition的references节读取。",
                             "source_field": "references[].reference_id where field_name=configID and operation is GenerateElemBall",
                             "next_request": {"action": "energy_generation_profile", "origin_reference_id": "复制实际产球关系的reference_id"}}}
        raise error
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        if int(state["indexer_version"]) < 6:
            raise RuntimeConfigError("产能Material关系需要同快照更新运行索引", "material_index_upgrade_required", 409,
                {"stored_indexer_version": state["indexer_version"], "required_indexer_version": "6"})
        reader = _Reader(conn, state)
        origin, link = reader.origin(origin_reference_id)
        material = reader.material(link)
        projection = _projection(origin, material, reader)
        result = {"projection_version": 1, "graph_scope": GRAPH_SCOPE, "snapshot": copy.deepcopy(state),
            "selection": {"origin_reference_id": origin_reference_id},
            "status": "ready" if not reader.issues and projection["status"] == "ready" else "pending",
            "source_status": "ready" if material["status"] == "resolved" else "pending",
            "whole_source_status": "partial", "execution_support": "not_evaluated", "origin": origin,
            "material": material, "reference_contract": _contract(), "reference_projection": projection,
            "parent_semantics": {"status": "not_evaluated", "conditions_not_executed": True,
                "dispatch_not_evaluated": True, "notes": ["完整父条件、随机、前后台、掉落允许及实际拾取未执行"]},
            "issues": reader.issues, "limits": dict(ENERGY_LIMITS)}
        result["source_fingerprint"] = fingerprint(energy_generation_source_payload(result))
        result["content_fingerprint"] = fingerprint(result)
        if len(dumps(result).encode("utf8")) > ENERGY_LIMITS["max_bytes"]:
            _limit("response_bytes")
        return copy.deepcopy(result)
