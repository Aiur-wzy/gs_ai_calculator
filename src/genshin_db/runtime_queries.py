"""Bounded public runtime queries and same-snapshot source assembly."""
from __future__ import annotations

import collections
import json
from contextlib import contextmanager
from pathlib import Path

from genshin_db.equipment_queries import (
    borrow_read_cache,
    borrow_read_connection,
    equipment_read_session,
)
from genshin_db.runtime_catalog import (
    INDEXER_VERSION,
    PROFILE_ID,
    PROFILE_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_INDEXER_VERSIONS,
    RuntimeConfigError,
    dumps,
    fingerprint,
    resolve_pointer,
)
from genshin_db.runtime_store import (
    COMPONENT,
    decode_source,
    runtime_datasets,
    runtime_normalization,
    validate_definition_references,
    validate_runtime,
)
from genshin_db.validation import QueryValidationError
from genshin_db.validation import version_line as validate_line

LIMITS = {"max_nodes": 2000, "max_edges": 10000, "max_depth": 64, "max_bytes": 2 * 1024 * 1024,
          "fragment_bytes": 65536, "characters": 4, "weapons": 4, "artifact_sets": 8, "affix_ids": 32}
from genshin_db.runtime_request_contracts import (
    ACTION_FIELDS,
    REQUIRED,
    validate_runtime_request,
)


def _integer(value, name, low=1, high=2**53-1):
    if type(value) is not int or not low <= value <= high:
        raise QueryValidationError(f"{name} 必须为{low}–{high}的整数")
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise QueryValidationError(f"{name} 必须为非空且不超过128字符的字符串")
    return value


def runtime_capabilities():
    from genshin_db.runtime_action_admission import (
        capabilities as action_admission_capabilities,
    )
    from genshin_db.runtime_action_capacity import (
        capabilities as action_capacity_capabilities,
    )
    from genshin_db.runtime_action_dispatch import action_dispatch_query_capabilities
    from genshin_db.runtime_action_level import action_level_context_capabilities
    from genshin_db.runtime_action_parameter import parameter_capabilities
    from genshin_db.runtime_action_resource import action_resource_capabilities
    from genshin_db.runtime_action_sources import action_source_capabilities
    from genshin_db.runtime_attack_metadata import attack_metadata_capabilities
    from genshin_db.runtime_attenuation import attenuation_capabilities
    from genshin_db.runtime_batch import batch_capabilities
    from genshin_db.runtime_energy_generation import energy_generation_capabilities
    from genshin_db.runtime_gadget_source import gadget_source_capabilities
    from genshin_db.runtime_limited_hit_benefit import limited_hit_benefit_capabilities
    from genshin_db.runtime_mechanism import mechanism_source_capabilities
    from genshin_db.runtime_passive_source import passive_source_capabilities
    from genshin_db.runtime_periodic_attack import periodic_attack_capabilities
    from genshin_db.runtime_public_root import public_root_capabilities
    from genshin_db.runtime_reaction_damage import reaction_damage_capabilities
    from genshin_db.runtime_talent_level import talent_level_capabilities

    return {"schema_versions": [SCHEMA_VERSION], "bundle_versions": [1], "profile_id": PROFILE_ID,
            "profile_version": PROFILE_VERSION, "indexer_version": INDEXER_VERSION,
            "supported_indexer_versions": list(SUPPORTED_INDEXER_VERSIONS),
            "path": "/v1/runtime/query", "method": "POST", "read_only": True,
            "actions": {k: sorted(v) for k, v in ACTION_FIELDS.items()},
            "common_fields": ["action", "version_line", "snapshot_id"], "limits": dict(LIMITS),
            "required": {key: list(value) for key, value in REQUIRED.items()},
            "action_admission_profile": action_admission_capabilities(),
            "action_admission_model_candidates": action_admission_capabilities()["model_catalog"],
            "action_capacity_profile": action_capacity_capabilities(),
            "action_capacity_source_profile": action_source_capabilities(),
            "action_activation_catalog": {"contract": "fresh-action-activation-catalog-v1", "scope": "资料目录与明确数值条件；完整生效/容量/派发未授权"},
            "action_parameter": parameter_capabilities(),
            "talent_level_profile": talent_level_capabilities(),
            "action_level_context": action_level_context_capabilities(),
            "action_parameter_at_level": {"contract":"level-bound-parameter-v1","projection_version":2,"max_bytes":524288,
                "required":["character_id","param_index"],"level_selection":"同talent_level_profile；base_level默认10",
                "scope":"有效等级下的原始ProudSkill标量；命座完整执行与动作派发仍独立核实"},
            "action_resource_profile": action_resource_capabilities(),
            "action_dispatch_profile": action_dispatch_query_capabilities(),
            "public_root_catalog": public_root_capabilities(),
            "attack_metadata_profile": attack_metadata_capabilities(),
            "passive_source_profile": passive_source_capabilities(),
            "gadget_source_profile": gadget_source_capabilities(),
            "energy_generation_profile": energy_generation_capabilities(),
            "limited_hit_benefit_profile": limited_hit_benefit_capabilities(),
            "mechanism_semantics": mechanism_source_capabilities(),
            "periodic_attack_profile": periodic_attack_capabilities(),
            "attenuation_profile": attenuation_capabilities(),
            "reaction_damage_profile": reaction_damage_capabilities(),
            "batch": batch_capabilities(),
            "action_facts": {"selection": "slot(normal/skill/burst)与skill_id二选一；多技能组角色必须明确skill_depot_id",
                             "level": "talent_level为显式有效等级1..15，不再叠加命座；C0缺省10，其他命座缺省返回等级待确认",
                             "limits": {"max_bytes": 262144, "max_operations": 128, "max_abilities": 64, "max_issues": 256},
                             "execution_support": "not_evaluated", "action_dispatch": "pending"},
            "selection": {"characters": "[{id,constellation?:0..6,talent_levels?:{normal:1..15,skill:1..15,burst:1..15}}]；按精确给定等级，命座升级操作供编译器另行处理", "weapons": "[{id,refinement?:1..5}]", "artifact_sets": "[{id,count:1..5}]", "affix_ids": "[正整数]"},
            "notes": ["ready仅表示结构装配；游戏语义由编译器单独判断", "闭包截断返回frontier；不把截断视为引用闭合", "source文件与原始未知字段完整保留；片段按pointer受限读取", "缺runtime旧库只读返回不可用，不迁移或联网", "人物到Avatar资源缺精确关系时明确unresolved_avatar_entry，不拼角色名路径"]}


def get_runtime_status(db_path, version_line="release"):
    validate_line(version_line)
    if not Path(db_path).is_file():
        return {"version_line": version_line, "state": "missing", "index_state": "missing", "execution_support": "not_evaluated"}
    with equipment_read_session(db_path):
        conn = borrow_read_connection(db_path)
        return {"version_line": version_line, **runtime_datasets(conn)[COMPONENT]["lines"][version_line]}


@contextmanager
def _read(db_path, line, expected=None):
    validate_line(line)
    if expected is not None:
        _identifier(expected, "snapshot_id")
    if not Path(db_path).is_file():
        raise RuntimeConfigError("运行配置数据库不存在", "dataset_unavailable", 409)
    with equipment_read_session(db_path):
        conn = borrow_read_connection(db_path)
        cache = borrow_read_cache(db_path, COMPONENT)
        state = cache.get(line)
        if state is None:
            state = runtime_datasets(conn)[COMPONENT]["lines"][line]
            if state["state"] != "ready":
                reason, code = {"missing": ("dataset_unavailable", 409), "invalid": ("dataset_invalid", 500), "incompatible": ("unsupported_schema", 409)}[state["state"]]
                raise RuntimeConfigError("运行配置状态: " + state["state"], reason, code, state)
            validate_runtime(conn, line)
            meta = dict(conn.execute("SELECT key,value FROM meta"))
            source_commit = meta.get("upstream_commit:" + line)
            if source_commit is not None and source_commit != state["upstream_commit"]:
                raise RuntimeConfigError("运行配置与角色/装备不属于同一commit", "mixed_snapshot", 500)
            cache[line] = state
        conn.runtime_source_cache = cache.setdefault((line, "sources"), collections.OrderedDict())
        if expected is not None and expected != state["snapshot_id"]:
            raise RuntimeConfigError("运行配置snapshot已变化", "snapshot_changed", 409, state)
        yield conn, {"version_line": line, **state}


def _row(conn, line, ident):
    _identifier(ident, "definition_id")
    row = conn.execute("SELECT * FROM runtime_definition WHERE version_line=? AND definition_id=?", (line, ident)).fetchone()
    if row is None:
        raise RuntimeConfigError("运行定义未收录", "not_found", 404, {"definition_id": ident})
    return dict(row)


def _lookup(conn, line, kind, key, source_path=None):
    sql = "SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?"
    args = [line, kind, dumps(key)]
    if source_path is not None:
        sql += " AND source_path=?"
        args.append(source_path)
    return [dict(r) for r in conn.execute(sql + " ORDER BY source_path,json_pointer", args)]


class _Nodes:
    def __init__(self, conn, line):
        self.conn, self.line = conn, line
        self.cache = getattr(conn, "runtime_source_cache", collections.OrderedDict())
        self.normalization, self.indexer_version = runtime_normalization(conn, line)

    def source(self, path):
        if path not in self.cache:
            row = self.conn.execute("SELECT * FROM runtime_source WHERE version_line=? AND source_path=?", (self.line, path)).fetchone()
            if row is None:
                raise RuntimeConfigError("运行定义来源不存在")
            self.cache[path] = (dict(row), decode_source(row))
            while len(self.cache) > 1 and (len(self.cache) > 8 or sum(v[0]["source_size_bytes"] for v in self.cache.values()) > 64 * 1024 * 1024):
                self.cache.popitem(last=False)
        self.cache.move_to_end(path)
        return self.cache[path]

    def node(self, definition):
        source, value = self.source(definition["source_path"])
        try:
            node = resolve_pointer(value, definition["json_pointer"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeConfigError("运行定义pointer损坏") from exc
        if fingerprint(node) != definition["node_sha256"]:
            raise RuntimeConfigError("运行定义内容指纹错误")
        return node, {"source_path": definition["source_path"], "json_pointer": definition["json_pointer"], "source_sha256": source["source_sha256"]}


def _definition_summary(row):
    result = {k: row[k] for k in ("definition_id", "definition_kind", "owner_definition_id", "source_path", "json_pointer", "variant_key", "namespace_key", "node_sha256")}
    result["exact_key"] = json.loads(row["exact_key_json"])
    return result


def _references(conn, line, ident, nodes=None):
    result = []
    definition = _row(conn, line, ident)
    nodes = nodes or _Nodes(conn, line)
    payload = nodes.node(definition)[0]
    for row in validate_definition_references(conn, line, definition, payload,
                                             normalization=nodes.normalization, indexer_version=nodes.indexer_version):
        value = dict(row)
        for key in ("target_key_json", "scope_json", "candidate_ids_json"):
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
        result.append(value)
    return result


def _closure(conn, state, roots, options):
    if not isinstance(roots, list) or not roots or len(roots) > 256 or any(not isinstance(v, str) for v in roots) or len(set(roots)) != len(roots):
        raise QueryValidationError("roots必须为1–256个不重复definition_id")
    for ident in roots:
        _identifier(ident, "root")
        _row(conn, state["version_line"], ident)
    limits = {name: _integer(options.get(name, LIMITS[name]), name, 1 if name != "max_bytes" else 4096, LIMITS[name]) for name in ("max_nodes", "max_edges", "max_depth", "max_bytes")}
    line = state["version_line"]
    nodes = _Nodes(conn, line)
    queue, visited, definitions, references, frontier = collections.deque((v, 0) for v in roots), set(), [], [], []
    size = 2048
    while queue:
        ident, depth = queue.popleft()
        if ident in visited:
            continue
        if depth > limits["max_depth"] or len(definitions) >= limits["max_nodes"]:
            frontier.append(ident)
            continue
        row = _row(conn, line, ident)
        refs = _references(conn, line, ident, nodes)
        summary = _definition_summary(row)
        extra = len(dumps([summary, refs]).encode("utf-8"))
        if len(references) + len(refs) > limits["max_edges"] or size + extra > limits["max_bytes"]:
            frontier.append(ident)
            continue
        visited.add(ident)
        size += extra
        definitions.append(summary)
        references.extend(refs)
        if row["owner_definition_id"]:
            queue.append((row["owner_definition_id"], depth + 1))
        if row["definition_kind"] == "ability":
            queue.extend((r[0], depth + 1) for r in conn.execute("SELECT definition_id FROM runtime_definition WHERE version_line=? AND owner_definition_id=? ORDER BY source_path,json_pointer", (line, ident)))
        queue.extend((r["resolved_definition_id"], depth + 1) for r in refs if r["resolution_status"] == "resolved")
    frontier = sorted(set(frontier) - visited)
    unresolved = [r for r in references if r["resolution_status"] not in ("resolved", "source_empty")]
    value = {"snapshot": state, "roots": roots, "definitions": definitions, "references": references,
             "unresolved": unresolved, "truncated": bool(frontier), "frontier": frontier, "limits": limits,
             "metrics": {"definitions": len(definitions), "references": len(references)},
             "reference_closed": not frontier and not unresolved, "execution_support": "not_evaluated"}
    value["closure_fingerprint"] = fingerprint(value)
    if len(dumps(value).encode("utf-8")) > limits["max_bytes"]:
        raise RuntimeConfigError("闭包响应包含未决引用/边界后超限，请减少roots或增加max_bytes", "response_too_large", 413)
    return value


def query_runtime_closure(db_path, request):
    allowed = {"version_line", "snapshot_id", "roots", "max_nodes", "max_edges", "max_depth", "max_bytes"}
    if not isinstance(request, dict) or request.keys() - allowed:
        raise QueryValidationError("非法runtime闭包字段")
    with _read(db_path, request.get("version_line", "release"), request.get("snapshot_id")) as (conn, state):
        return _closure(conn, state, request.get("roots"), request)


def get_runtime_definition(db_path, definition_id, *, version_line="release", snapshot_id=None, sections=("summary", "references")):
    if not isinstance(sections, (list, tuple)) or not sections or any(not isinstance(v, str) for v in sections) or set(sections) - {"summary", "references", "payload"} or len(set(sections)) != len(sections):
        raise QueryValidationError("非法definition sections")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        row = _row(conn, version_line, definition_id)
        result = {"snapshot": state, "definition": _definition_summary(row)}
        if "references" in sections:
            result["references"] = _references(conn, version_line, definition_id)
        if "payload" in sections:
            node, source = _Nodes(conn, version_line).node(row)
            payload = dumps(node)
            if len(payload.encode("utf-8")) > LIMITS["fragment_bytes"]:
                raise RuntimeConfigError("定义内容过大，请用fragment读取更小pointer", "fragment_too_large", 413)
            result.update(payload_json=payload, source=source)
        result["content_fingerprint"] = fingerprint(result)
        return result


def get_runtime_fragment(db_path, definition_id, *, version_line="release", snapshot_id=None, relative_pointer="", max_bytes=8192):
    _integer(max_bytes, "max_bytes", 1, LIMITS["fragment_bytes"])
    if not isinstance(relative_pointer, str) or len(relative_pointer) > 2048:
        raise QueryValidationError("relative_pointer过长或类型不正确")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        row = _row(conn, version_line, definition_id)
        node, source = _Nodes(conn, version_line).node(row)
        try:
            value = resolve_pointer(node, relative_pointer)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeConfigError("片段pointer不存在", "not_found", 404) from exc
        payload = dumps(value)
        size = len(payload.encode("utf-8"))
        if size > max_bytes:
            children = list(value)[:100] if isinstance(value, dict) else list(range(min(len(value), 100))) if isinstance(value, list) else []
            raise RuntimeConfigError("片段超过字节上限，请缩小pointer", "fragment_too_large", 413, {"bytes": size, "max_bytes": max_bytes, "child_keys": children})
        result = {"snapshot": state, "source": {**source, "json_pointer": source["json_pointer"] + relative_pointer},
                  "definition_id": definition_id, "payload_json": payload, "size_bytes": size}
        result["content_fingerprint"] = fingerprint(result)
        return result


def resolve_runtime_roots(db_path, request):
    allowed = {"version_line", "snapshot_id", "characters", "weapons", "artifact_sets", "affix_ids"}
    if not isinstance(request, dict) or request.keys() - allowed:
        raise QueryValidationError("非法runtime实体选择字段")
    for key in ("characters", "weapons", "artifact_sets", "affix_ids"):
        values = request.get(key, [])
        if not isinstance(values, list) or len(values) > LIMITS[key]:
            raise QueryValidationError(f"{key}选择数量超限")
    with _read(db_path, request.get("version_line", "release"), request.get("snapshot_id")) as (conn, state):
        line, nodes = state["version_line"], _Nodes(conn, state["version_line"])
        roots, issues, selections = [], [], []

        def source_rows(table):
            return nodes.source(f"ExcelBinOutput/{table}ExcelConfigData.json")[1]

        def exact(table, key):
            found = _lookup(conn, line, "excel_record", key, f"ExcelBinOutput/{table}ExcelConfigData.json")
            if len(found) != 1:
                issues.append({"code": "missing_definition" if not found else "ambiguous_reference", "table": table, "key": key})
                return None
            return found[0]

        def add_affix(ident):
            _integer(ident, "affix_id")
            row = exact("EquipAffix", {"affixId": ident})
            if row:
                roots.append(row["definition_id"])

        for ident in request.get("affix_ids", []):
            add_affix(ident)
        for spec in request.get("weapons", []):
            if not isinstance(spec, dict) or spec.keys() - {"id", "refinement"} or "id" not in spec:
                raise QueryValidationError("武器选择只允许id/refinement")
            ident, refinement = _integer(spec["id"], "weapon_id"), _integer(spec.get("refinement", 1), "refinement", 1, 5)
            row = exact("Weapon", {"id": ident})
            if row:
                weapon = nodes.node(row)[0]
                active = [r["affixId"] for r in source_rows("EquipAffix") if r.get("id") in weapon.get("skillAffix", []) and r.get("level", 0) == refinement - 1]
                for group in (v for v in weapon.get("skillAffix", []) if v):
                    matches = [r for r in source_rows("EquipAffix") if r.get("id") == group and r.get("level", 0) == refinement - 1]
                    if len(matches) != 1:
                        issues.append({"code": "missing_parameter_record" if not matches else "ambiguous_reference", "group_id": group, "level": refinement - 1})
                for aid in active:
                    add_affix(aid)
                selections.append({"kind": "weapon", "id": ident, "refinement": refinement, "affix_ids": active})
        for spec in request.get("artifact_sets", []):
            if not isinstance(spec, dict) or set(spec) != {"id", "count"}:
                raise QueryValidationError("套装选择需要id/count")
            ident, count = _integer(spec["id"], "set_id"), _integer(spec["count"], "count", 1, 5)
            row = exact("ReliquarySet", {"setId": ident})
            if row:
                artifact_set = nodes.node(row)[0]
                levels = [i for i, threshold in enumerate(artifact_set.get("setNeedNum", [])) if threshold <= count]
                active = [r["affixId"] for r in source_rows("EquipAffix") if r.get("id") == artifact_set.get("EquipAffixId") and r.get("level", 0) in levels]
                for level in levels:
                    matches = [r for r in source_rows("EquipAffix") if r.get("id") == artifact_set.get("EquipAffixId") and r.get("level", 0) == level]
                    if len(matches) != 1:
                        issues.append({"code": "missing_parameter_record" if not matches else "ambiguous_reference", "group_id": artifact_set.get("EquipAffixId"), "level": level})
                for aid in active:
                    add_affix(aid)
                selections.append({"kind": "artifact_set", "id": ident, "count": count, "affix_ids": active})
        for spec in request.get("characters", []):
            if not isinstance(spec, dict) or spec.keys() - {"id", "constellation", "talent_levels"} or "id" not in spec:
                raise QueryValidationError("角色选择只允许id/constellation/talent_levels")
            ident, constellation = _integer(spec["id"], "character_id"), _integer(spec.get("constellation", 0), "constellation", 0, 6)
            levels = spec.get("talent_levels", {})
            if not isinstance(levels, dict) or levels.keys() - {"normal", "skill", "burst"}:
                raise QueryValidationError("非法talent_levels")
            for slot, value in levels.items():
                _integer(value, slot, 1, 15)
            row = exact("Avatar", {"id": ident})
            if not row:
                continue
            avatar = nodes.node(row)[0]
            depots = [d for d in source_rows("AvatarSkillDepot") if d.get("id") == avatar.get("skillDepotId")]
            if len(depots) != 1:
                issues.append({"code": "unresolved_skill_depot", "character_id": ident})
                continue
            depot = depots[0]
            skills = {r.get("id"): r for r in source_rows("AvatarSkill")}
            ids = depot.get("skills", [])
            selected = [(ids[i] if len(ids) > i else 0, slot) for i, slot in enumerate(("normal", "skill"))]
            selected.append((depot.get("energySkill"), "burst"))
            group_levels = [(skills[sid]["proudSkillGroupId"], levels.get(slot, 10)) for sid, slot in selected if sid in skills and skills[sid].get("proudSkillGroupId")]
            group_levels += [(r["proudSkillGroupId"], 1) for r in depot.get("inherentProudSkillOpens", []) if isinstance(r, dict) and r.get("proudSkillGroupId")]
            for gid, level in group_levels:
                candidates = [r for r in source_rows("ProudSkill") if r.get("proudSkillGroupId") == gid and r.get("level") == level]
                if len(candidates) != 1 or "proudSkillId" not in candidates[0]:
                    issues.append({"code": "missing_parameter_record", "group_id": gid, "level": level})
                else:
                    entry = exact("ProudSkill", {"proudSkillId": candidates[0]["proudSkillId"]})
                    if entry:
                        roots.append(entry["definition_id"])
            for tid in depot.get("talents", [])[:constellation]:
                if tid:
                    entry = exact("AvatarTalent", {"talentId": tid})
                    if entry:
                        roots.append(entry["definition_id"])
            issues.append({"code": "unresolved_avatar_entry", "character_id": ident, "message": "只返回明确技能/天赋入口，尚未证明Avatar/动画资源映射；未自动执行命座等级变化"})
            selections.append({"kind": "character", "id": ident, "constellation": constellation, "talent_levels": levels})
        if not any(request.get(k) for k in ("characters", "weapons", "artifact_sets", "affix_ids")):
            raise QueryValidationError("至少选择一个runtime实体")
        result = {"snapshot": state, "roots": sorted(set(roots)), "selections": selections, "issues": issues,
                  "status": "pending" if issues else "ready", "execution_support": "not_evaluated"}
        result["content_fingerprint"] = fingerprint(result)
        return result


def assemble_runtime_source(db_path, affix_id, version_line="release", *, snapshot_id=None):
    _integer(affix_id, "affix_id")
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        found = _lookup(conn, version_line, "excel_record", {"affixId": affix_id}, "ExcelBinOutput/EquipAffixExcelConfigData.json")
        if len(found) != 1:
            raise RuntimeConfigError("装备效果来源不存在或不唯一", "not_found" if not found else "ambiguous_reference", 404 if not found else 409)
        nodes = _Nodes(conn, version_line)
        record, origin = nodes.node(found[0])
        closure = _closure(conn, state, [found[0]["definition_id"]], {})
        issues = [{"code": r["resolution_status"], "source_path": r["source_path"], "json_pointer": r["json_pointer"], "target_key": r["target_key"]} for r in closure["unresolved"]]
        if closure["truncated"]:
            issues.append({"code": "closure_truncated", "frontier": closure["frontier"]})
        talents, abilities = [], []
        for definition in closure["definitions"]:
            if definition["definition_kind"] not in ("talent_config", "ability"):
                continue
            row = _row(conn, version_line, definition["definition_id"])
            payload, source = nodes.node(row)
            value = {"definition_id": row["definition_id"], **source}
            if row["definition_kind"] == "talent_config":
                talents.append({**value, "operations": payload})
            else:
                abilities.append({**value, "payload": payload, "variant_key": row["variant_key"]})
        if record.get("openConfig") and len(talents) != 1:
            issues.append({"code": "talent_entry_not_unique", "count": len(talents)})
        result = {"bundle_version": 1, "status": "pending" if issues else "ready", "issues": issues,
                  "source": {"kind": "equip_affix", "affix_id": affix_id, "affix_group_id": record.get("id"),
                             "source_level": record.get("level", 0), "version_line": version_line,
                             "snapshot_id": state["snapshot_id"], "upstream_commit": state["upstream_commit"]},
                  "snapshot": state, "param_list": record.get("paramList", []), "affix": {"record": record, **origin},
                  "talent": talents[0] if len(talents) == 1 else None, "talent_candidates": talents if len(talents) != 1 else [],
                  "abilities": abilities, "references": closure["references"], "reference_closed": closure["reference_closed"],
                  "execution_support": "not_evaluated", "closure_fingerprint": closure["closure_fingerprint"]}
        if "normalization" in state:
            result["normalization"] = state["normalization"]
        if len(dumps(result).encode("utf-8")) > LIMITS["max_bytes"]:
            raise RuntimeConfigError("完整来源装配超过字节上限，不能以删去未知节点继续编译", "bundle_too_large", 413)
        result["content_fingerprint"] = fingerprint(result)
        return result


def query_runtime(db_path, request):
    """Read the canonical projection used by source compilers and verification."""
    validate_runtime_request(request)
    return _query_runtime(db_path, request)


def query_runtime_public(db_path, request):
    """Read an existing runtime projection with the explicitly selected entity scope."""
    validate_runtime_request(request)
    if request["action"] == "batch":
        from genshin_db.runtime_batch import query_runtime_batch
        return query_runtime_batch(db_path, request, public=True)
    if not Path(db_path).is_file():
        return _query_runtime(db_path, request)
    from genshin_db.appendix_navigation import annotation_owner_scope
    from genshin_db.publication_evidence import ENTITY_TYPES, owner_publication

    selection = request.get("selection")
    selection = selection if isinstance(selection, dict) else {}
    scope = {kind + "_ids": [value] for kind in ENTITY_TYPES
             if type(value := request.get(kind + "_id", selection.get(kind + "_id"))) is int}
    for kind in ENTITY_TYPES:
        plural = "artifact_sets" if kind == "artifact_set" else kind + "s"
        if isinstance(request.get(plural), list):
            scope.setdefault(kind + "_ids", []).extend(item["id"] for item in request[plural]
                if isinstance(item, dict) and type(item.get("id")) is int)
    target_type, target_id = request.get("target_type"), request.get("target_id")
    if target_type in ENTITY_TYPES and type(target_id) is int:
        scope.setdefault(target_type + "_ids", []).append(target_id)
    affix_ids = request.get("affix_ids", [])
    if type(request.get("affix_id")) is int:
        affix_ids = [request["affix_id"]]
    if not scope and not affix_ids:
        return _query_runtime(db_path, request)
    with equipment_read_session(db_path):
        result = _query_runtime(db_path, request)
        if not isinstance(result, dict):
            return result
        conn = borrow_read_connection(db_path)
        line = request.get("version_line", "release")
        for ident in affix_ids:
            owners = annotation_owner_scope(conn, "equip_affix", ident, line)
            for kind in ENTITY_TYPES:
                key = kind + "_ids"
                scope.setdefault(key, []).extend(owners[key])
        enriched = {**result, "owner_publication": owner_publication(conn, scope, line)}
        if request["action"] == "roots" and len(dumps(enriched).encode("utf-8")) > LIMITS["max_bytes"]:
            raise RuntimeConfigError("运行入口与实体上线证据超过响应预算", "response_too_large", 413)
        return enriched


def _query_runtime(db_path, request):
    action = request["action"]
    args = {k: v for k, v in request.items() if k != "action"}
    line, snapshot = args.get("version_line", "release"), args.get("snapshot_id")
    validate_line(line)
    if action == "batch":
        from genshin_db.runtime_batch import query_runtime_batch
        return query_runtime_batch(db_path, request)
    if action in ("mechanism_catalog", "mechanism_profile"):
        from genshin_db.runtime_mechanism import (
            get_mechanism_catalog,
            get_mechanism_profile,
        )
        if action == "mechanism_profile" and "mechanism_id" not in args:
            raise QueryValidationError("缺少mechanism_id")
        function = get_mechanism_catalog if action == "mechanism_catalog" else get_mechanism_profile
        return function(db_path, **args)
    if action == "action_admission_profile":
        from genshin_db.runtime_action_admission import get_action_admission_profile
        if not {"selection", "variant", "action_type_id", "model_ref"} <= args.keys():
            raise QueryValidationError("动作接纳模型需要selection/variant/action_type_id/model_ref")
        return get_action_admission_profile(db_path, **args)
    if action == "action_admission_model_candidates":
        from genshin_db.runtime_action_admission import (
            list_action_admission_model_candidates,
        )
        if "request" not in args:
            raise QueryValidationError("动作接纳模型目录缺少request包络")
        return list_action_admission_model_candidates(
            db_path,
            args["request"],
            version_line=args.get("version_line"),
            snapshot_id=args.get("snapshot_id"),
        )
    if action == "action_capacity_profile":
        from genshin_db.runtime_action_capacity import get_action_capacity_profile
        if not {"selection", "context", "model_ref"} <= args.keys():
            raise QueryValidationError("容量模型需要selection/context/model_ref")
        return get_action_capacity_profile(db_path, **args)
    if action == "action_capacity_model_candidates":
        from genshin_db.runtime_action_capacity import (
            list_action_capacity_model_candidates,
        )
        if "request" not in args:
            raise QueryValidationError("容量模型目录缺少request包络")
        return list_action_capacity_model_candidates(
            db_path,
            args["request"],
            version_line=args.get("version_line"),
            snapshot_id=args.get("snapshot_id"),
        )
    if action in ("action_activation_catalog", "action_capacity_source_profile"):
        from genshin_db.runtime_action_sources import (
            get_action_activation_catalog,
            get_action_source_profile,
        )
        if not {"character_id", "constellation"} <= args.keys():
            raise QueryValidationError("动作资料需要character_id与constellation")
        function = get_action_activation_catalog if action == "action_activation_catalog" else get_action_source_profile
        return function(db_path, **args)
    if action == "public_root_catalog":
        from genshin_db.runtime_public_root import get_public_root_catalog
        return get_public_root_catalog(db_path, **args)
    if action == "status":
        result = get_runtime_status(db_path, line)
        if snapshot is not None and result.get("snapshot_id") != snapshot:
            raise RuntimeConfigError("运行配置snapshot已变化", "snapshot_changed", 409, result)
        return result
    if action == "roots":
        return resolve_runtime_roots(db_path, args)
    if action == "closure":
        return query_runtime_closure(db_path, args)
    if action in ("definition", "fragment"):
        if "definition_id" not in args:
            raise QueryValidationError("缺少definition_id")
        function = get_runtime_definition if action == "definition" else get_runtime_fragment
        return function(db_path, **args)
    if action == "action_parameter":
        from genshin_db.runtime_action_parameter import get_runtime_action_parameter

        if "character_id" not in args or "param_index" not in args:
            raise QueryValidationError("缺少character_id或param_index")
        return get_runtime_action_parameter(db_path, **args)
    if action in ('talent_level_profile','action_parameter_at_level'):
        from genshin_db.runtime_talent_level import (
            get_runtime_action_parameter_at_level,
            get_runtime_talent_level,
        )
        if 'character_id' not in args or action=='action_parameter_at_level' and 'param_index' not in args:
            raise QueryValidationError('缺少character_id或param_index')
        function=get_runtime_talent_level if action=='talent_level_profile' else get_runtime_action_parameter_at_level
        return function(db_path,**args)
    if action == "limited_hit_benefit_profile":
        from genshin_db.runtime_limited_hit_benefit import (
            get_runtime_limited_hit_benefit_profile,
        )
        if "character_id" not in args or "param_index" not in args:
            raise QueryValidationError("缺少character_id或param_index")
        return get_runtime_limited_hit_benefit_profile(db_path, **args)
    if action == "action_resource_profile":
        from genshin_db.runtime_action_resource import (
            get_runtime_action_resource_profile,
        )
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_action_resource_profile(db_path, **args)
    if action == "action_dispatch_profile":
        from genshin_db.runtime_action_dispatch import (
            get_runtime_action_dispatch_profile,
        )
        required = {"character_id", "skill_depot_id", "skill_id", "slot", "effective_level",
                    "constellation", "variant"}
        if not required <= args.keys():
            raise QueryValidationError("动作发射档案缺少完整语义身份")
        return get_runtime_action_dispatch_profile(db_path, **args)
    if action == "attack_metadata_profile":
        from genshin_db.runtime_attack_metadata import (
            get_runtime_attack_metadata_profile,
        )
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_attack_metadata_profile(db_path, **args)
    if action == "passive_source_profile":
        from genshin_db.runtime_passive_source import get_runtime_passive_source_profile
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_passive_source_profile(db_path, **args)
    if action == "gadget_source_profile":
        from genshin_db.runtime_gadget_source import get_runtime_gadget_source_profile
        if "gadget_id" not in args:
            raise QueryValidationError("缺少gadget_id")
        return get_runtime_gadget_source_profile(db_path, **args)
    if action == "energy_generation_profile":
        from genshin_db.runtime_energy_generation import (
            get_runtime_energy_generation_profile,
        )
        if "origin_reference_id" not in args:
            raise QueryValidationError("缺少origin_reference_id")
        return get_runtime_energy_generation_profile(db_path, **args)
    if action == "periodic_attack_profile":
        from genshin_db.runtime_periodic_attack import (
            get_runtime_periodic_attack_profile,
        )
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_periodic_attack_profile(db_path, **args)
    if action == "action_level_context":
        from genshin_db.runtime_action_level import get_runtime_action_level_context
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_action_level_context(db_path, **args)
    if action == "action_facts":
        from genshin_db.runtime_action_facts import get_runtime_action_facts
        if "character_id" not in args:
            raise QueryValidationError("缺少character_id")
        return get_runtime_action_facts(db_path, **args)
    if action == "attenuation_profile":
        from genshin_db.runtime_attenuation import get_runtime_attenuation_profile
        if "group" not in args:
            raise QueryValidationError("缺少group")
        return get_runtime_attenuation_profile(db_path, **args)
    if action == "reaction_damage_profile":
        from genshin_db.runtime_reaction_damage import (
            get_runtime_reaction_damage_profile,
        )
        if "element" not in args:
            raise QueryValidationError("缺少element")
        return get_runtime_reaction_damage_profile(db_path, **args)
    if "affix_id" not in args:
        raise QueryValidationError("缺少affix_id")
    return assemble_runtime_source(db_path, **args)
