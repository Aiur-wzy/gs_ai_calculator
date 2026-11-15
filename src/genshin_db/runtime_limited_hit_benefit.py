"""Bounded, read-only full-parent source for a limited-hit reference benefit."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass

from genshin_db.runtime_catalog import RuntimeConfigError, dumps, excel_key, fingerprint
from genshin_db.validation import QueryValidationError
from genshin_runtime.limited_hit_benefit_identity import (
    LIMITS,
    limited_hit_benefit_source_payload,
    rebuild_projection,
    recipe,
    reference_contract,
)


@dataclass(frozen=True)
class LimitedHitBenefitContext:
    public_projection: dict
    parameter_context: dict


def limited_hit_benefit_capabilities():
    return {"projection_version": 1, "graph_scope": "limited_hit_benefit_source_v1", "limits": dict(LIMITS),
        "selection": "character_id与param_index；slot或skill_id二选一；param_index为父伤害参数",
        "scope": "C0/有效10级/完整精确父形状；数值来自Proud并重建唯一Talent写者",
        "whole_source_status": "partial", "execution_support": "not_evaluated",
        "reference_contract": reference_contract(), "source_ready_is_execution_ready": False}


def _limit(kind):
    raise RuntimeConfigError("有限次数收益完整来源超限；未截断", "response_too_large", 413,
                             {"limit_kind": kind, "limits": dict(LIMITS)})


class _Reader:
    def __init__(self, conn, state):
        from genshin_db.runtime_queries import _Nodes
        self.conn, self.state, self.line = conn, state, state["version_line"]
        self.nodes = _Nodes(conn, self.line)
        self.entries, self.refs, self.candidates = {}, {}, set()

    def lookup(self, kind, key, path=None, *, candidate=False):
        sql = "SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?"
        args = [self.line, kind, dumps(key)]
        if path is not None:
            sql += " AND source_path=?"
            args.append(path)
        rows = self.conn.execute(sql + " ORDER BY source_path,json_pointer LIMIT ?", [*args, LIMITS["max_candidates"] + 1]).fetchall()
        if len(rows) > LIMITS["max_candidates"]:
            _limit("candidates")
        if candidate:
            self.candidates.update(row["definition_id"] for row in rows)
            if len(self.candidates) > LIMITS["max_candidates"]:
                _limit("cumulative_candidates")
        return rows

    def exact(self, table, key):
        rows = self.lookup("excel_record", key, f"ExcelBinOutput/{table}ExcelConfigData.json")
        if len(rows) != 1:
            raise RuntimeConfigError("技能入口不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                     404 if not rows else 409, {"table": table, "key": key})
        return self.entry(rows[0])

    def entry(self, row):
        from genshin_db.runtime_gadget_source import _Reader as IdentityReader
        from genshin_db.runtime_queries import _definition_summary
        ident = row["definition_id"]
        if ident in self.entries:
            return self.entries[ident]
        if len(self.entries) >= LIMITS["max_nodes"]:
            _limit("nodes")
        if row["snapshot_id"] != self.state["snapshot_id"]:
            raise RuntimeConfigError("收益父链跨快照", "mixed_snapshot", 500)
        raw, source = self.nodes.node(row)
        kind, path, ptr = row["definition_kind"], row["source_path"], row["json_pointer"]
        if kind in ("ability", "modifier"):
            IdentityReader.identity(self, row, raw)
        else:
            src, root = self.nodes.source(path)
            family = src["source_family"]
            if kind == "excel_record" and family == "excel" and isinstance(root, list) and re.fullmatch(r"/(0|[1-9][0-9]*)", ptr):
                key = excel_key(path, raw, int(ptr[1:]), indexer_version=self.nodes.indexer_version)
            elif kind == "talent_config" and family == "talent" and isinstance(raw, list) and ptr.count("/") == 1:
                key = ptr[1:].replace("~1", "/").replace("~0", "~")
            else:
                raise RuntimeConfigError("收益来源定义不是完整原行/原Talent", "dataset_invalid", 500)
            expected = {"definition_id": fingerprint([self.state["snapshot_id"], path, ptr, kind]),
                "exact_key_json": dumps(key), "owner_definition_id": None, "variant_key": None,
                "namespace_key": family, "summary_json": dumps({"name": key, "kind": kind})}
            if any(row[k] != v for k, v in expected.items()):
                raise RuntimeConfigError("收益来源索引身份与原文不符", "dataset_invalid", 500)
        entry = {"definition": _definition_summary(row), **source, "node_sha256": row["node_sha256"], "payload": raw}
        self.entries[ident] = entry
        if len(dumps(list(self.entries.values())).encode("utf8")) > LIMITS["max_bytes"]:
            _limit("node_bytes")
        return entry

    def children(self, parent):
        rows = self.conn.execute("SELECT * FROM runtime_definition WHERE version_line=? AND owner_definition_id=? "
            "ORDER BY json_pointer,definition_id LIMIT ?", (self.line, parent["definition"]["definition_id"], LIMITS["max_nodes"] + 1)).fetchall()
        if len({row["definition_id"] for row in rows} | self.entries.keys()) > LIMITS["max_nodes"]:
            _limit("nodes")
        return [self.entry(row) for row in rows]

    def references(self, entry):
        from genshin_db.runtime_queries import _references
        ident = entry["definition"]["definition_id"]
        rows = self.conn.execute("SELECT reference_id,target_kind,target_key_json FROM runtime_reference "
            "WHERE version_line=? AND from_definition_id=? ORDER BY source_order,reference_id LIMIT ?",
            (self.line, ident, LIMITS["max_edges"] + 1)).fetchall()
        if len(rows) > LIMITS["max_edges"] or len({row["reference_id"] for row in rows} | self.refs.keys()) > LIMITS["max_edges"]:
            _limit("edges")
        for row in rows:
            self.lookup(row["target_kind"], json.loads(row["target_key_json"]), candidate=row["target_kind"] == "ability")
        for ref in _references(self.conn, self.line, ident, self.nodes):
            self.refs[ref["reference_id"]] = ref


def _ids(raw, field):
    result = raw.get(field, [])
    if not isinstance(result, list) or any(type(v) is not int or not 0 <= v <= 2**53-1 for v in result):
        raise RuntimeConfigError("技能入口ID列表无效", "dataset_invalid", 500)
    return result


def get_limited_hit_benefit_context(db_path, *, character_id, param_index, slot=None, skill_id=None,
                                   skill_depot_id=None, talent_level=None, constellation=0,
                                   version_line="release", snapshot_id=None):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_action_parameter import _description_context
    from genshin_db.runtime_action_resource import validate_selection
    from genshin_db.runtime_queries import _integer, _read
    validate_selection(character_id=character_id, slot=slot, skill_id=skill_id, skill_depot_id=skill_depot_id)
    _integer(param_index, "param_index", 0, 63)
    _integer(constellation, "constellation", 0, 6)
    if talent_level is not None:
        _integer(talent_level, "talent_level", 1, 15)
    requested = {"character_id": character_id, "param_index": param_index, "slot": slot, "skill_id": skill_id,
                 "skill_depot_id": skill_depot_id, "talent_level": talent_level, "constellation": constellation}
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        cache = borrow_read_cache(db_path, "limited-hit-benefit-fresh-v1")
        key = (state["snapshot_id"], fingerprint(requested))
        if key in cache:
            return copy.deepcopy(cache[key])
        if len(cache) >= 32:
            _limit("cached_selections")
        reader = _Reader(conn, state)
        issues, ids = [], {"modifiers": []}
        def issue(code, message=None):
            if len(issues) >= LIMITS["max_issues"]:
                _limit("issues")
            issues.append({"code": code, "message": message or code})
        def record(name, entry):
            ids[name] = entry["definition"]["definition_id"]
            return entry["payload"]
        avatar = record("avatar", reader.exact("Avatar", {"id": character_id}))
        base = avatar.get("skillDepotId", 0)
        if type(base) is not int or not 0 <= base <= 2**53-1:
            raise RuntimeConfigError("技能组身份错误", "dataset_invalid", 500)
        depots = list(dict.fromkeys(v for v in [base, *_ids(avatar, "candSkillDepotIds")] if v))
        if skill_depot_id is None:
            if len(depots) != 1:
                raise RuntimeConfigError("角色技能组需明确选择", "skill_depot_selection_required", 409, {"candidate_skill_depot_ids": depots})
            skill_depot_id = depots[0]
        elif skill_depot_id not in depots:
            raise QueryValidationError("skill_depot_id不属于角色")
        depot = record("depot", reader.exact("AvatarSkillDepot", {"id": skill_depot_id}))
        skills, subskills = _ids(depot, "skills"), _ids(depot, "subSkills")
        burst = depot.get("energySkill", 0)
        if type(burst) is not int or not 0 <= burst <= 2**53-1:
            raise RuntimeConfigError("爆发技能身份错误", "dataset_invalid", 500)
        slots = {"normal": skills[0] if skills else 0, "skill": skills[1] if len(skills) > 1 else 0, "burst": burst}
        if slot is not None:
            skill_id = slots[slot]
            if not skill_id:
                raise RuntimeConfigError("所选技能槽为空", "empty_skill_slot", 409)
        elif skill_id not in [*skills, *subskills, burst]:
            raise QueryValidationError("skill_id不属于技能组")
        skill = record("skill", reader.exact("AvatarSkill", {"id": skill_id}))
        effective = talent_level if talent_level is not None else 10 if constellation == 0 else None
        level = {"base_level": 10 if talent_level is None else None, "effective_level": effective,
            "source": "explicit_effective_level" if talent_level is not None else "default_base_level",
            "constellation_applied": False, "constellation_level_support": "not_evaluated" if constellation else "not_applicable"}
        selection = {"character_id": character_id, "skill_depot_id": skill_depot_id, "skill_id": skill_id,
            "slot": slot, "matching_slots": [k for k, v in slots.items() if v == skill_id], "constellation": constellation,
            "active_constellation_ids": _ids(depot, "talents")[:constellation]}
        if constellation != 0 or effective != 10 or "skill" not in selection["matching_slots"]:
            issue("limited_hit_benefit_parent_scope_unsupported")
        group = skill.get("proudSkillGroupId")
        if effective is not None and type(group) is int and group > 0:
            root = reader.nodes.source("ExcelBinOutput/ProudSkillExcelConfigData.json")[1]
            if not isinstance(root, list):
                raise RuntimeConfigError("ProudSkill来源根不是数组", "dataset_invalid", 500)
            found = []
            for row in root:
                if isinstance(row, dict) and type(row.get("proudSkillGroupId")) is int and row["proudSkillGroupId"] == group and type(row.get("level")) is int and row["level"] == effective:
                    found.append(row)
                    if len(found) > LIMITS["max_candidates"]:
                        _limit("proud_candidates")
            if len(found) == 1:
                proud = record("proud_skill", reader.exact("ProudSkill", {"proudSkillId": found[0]["proudSkillId"]}))
                config = proud.get("openConfig")
                talents = reader.lookup("talent_config", config, candidate=True) if isinstance(config, str) and config else []
                if len(talents) == 1:
                    talent = reader.entry(talents[0])
                    record("talent", talent)
                    reader.references(talent)
                    view = reader.nodes.normalization.normalize(talent["payload"], kind="talent_config") if reader.nodes.normalization else talent["payload"]
                    if not isinstance(view, list):
                        raise RuntimeConfigError("Talent完整操作列表无效", "dataset_invalid", 500)
                    if len(view) > LIMITS["max_edges"]:
                        _limit("talent_operations")
                    writers = [op for op in view if isinstance(op, dict) and op.get("$type") == "ModifyAbility" and op.get("paramDelta") == f"%{param_index+1}"]
                    targets = []
                    for op in writers:
                        name = op.get("abilityName")
                        if isinstance(name, str) and name:
                            targets.extend(reader.lookup("ability", name, candidate=True))
                    if len(writers) == len(targets) == 1:
                        target = reader.entry(targets[0])
                        record("ability", target)
                        children = reader.children(target)
                        ids["modifiers"] = [e["definition"]["definition_id"] for e in children]
                        for entry in [target, *children]:
                            reader.references(entry)
                    else:
                        issue("limited_hit_benefit_unique_parent_writer_required")
                else:
                    issue("limited_hit_benefit_exact_talent_required")
            else:
                issue("limited_hit_benefit_exact_proud_required")
        else:
            issue("limited_hit_benefit_exact_proud_required")
        records = {name: {**{k: copy.deepcopy(reader.entries[ident][k]) for k in
            ("definition", "source_path", "source_sha256", "json_pointer")},
            "fields": copy.deepcopy(reader.entries[ident]["payload"])}
            for name, ident in ids.items() if name in ("avatar", "depot", "skill", "proud_skill")}
        facts = {"snapshot": copy.deepcopy(state), "selection": selection, "level": level, "records": records,
                 "status": "pending" if issues else "ready", "issues": copy.deepcopy(issues),
                 "action_dispatch": {"status": "pending", "reasons": ["action_trigger_semantics_not_compiled"]}}
        descriptions = _description_context(conn, state, facts)
        result = {"projection_version": 1, "graph_scope": "limited_hit_benefit_source_v1", "snapshot": copy.deepcopy(state),
            "selection": selection, "level": level, "param_index": param_index,
            "status": "pending", "source_status": "pending", "projection_status": "pending",
            "whole_source_status": "partial", "execution_support": "not_evaluated",
            "parent_bridge": {"status": "pending", "variant": None}, "benefit_candidates": [], "projection": {},
            "source_bundle": {"entries": list(reader.entries.values()), "entry_ids": ids,
                "references": list(reader.refs.values()), "boundaries": copy.deepcopy(recipe()["boundaries"])},
            "descriptions": descriptions, "reference_contract": reference_contract(), "issues": issues, "limits": dict(LIMITS)}
        if not issues:
            try:
                rebuilt = rebuild_projection(result)
                result.update(parent_bridge=rebuilt["parent_bridge"], projection=rebuilt["projection"],
                    benefit_candidates=[rebuilt["candidate"]], status="ready", source_status="ready", projection_status="ready")
            except (ValueError, KeyError, TypeError, IndexError, StopIteration, OverflowError) as exc:
                issue("limited_hit_benefit_source_unreviewed", str(exc))
        result["semantic_dependency"] = limited_hit_benefit_source_payload(result)
        result["source_fingerprint"] = fingerprint(result["semantic_dependency"])
        result["content_fingerprint"] = fingerprint(result)
        if len(dumps(result).encode("utf8")) > LIMITS["max_bytes"]:
            _limit("response_bytes")
        context = LimitedHitBenefitContext(copy.deepcopy(result), {"facts": facts, "descriptions": copy.deepcopy(descriptions)})
        cache[key] = copy.deepcopy(context)
        return context


def get_runtime_limited_hit_benefit_profile(db_path, **selection):
    return copy.deepcopy(get_limited_hit_benefit_context(db_path, **selection).public_projection)
