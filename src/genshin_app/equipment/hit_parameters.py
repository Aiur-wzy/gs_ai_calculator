"""Compile selected table scalars into existing hits, without action inference."""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError

from .files import fingerprint
from .schema import PlanError


def compile_hit_parameters(plan, template, db_path, missing, *, prepared_contexts=None):
    bindings = plan.get("hit_parameter_sources", [])
    if not bindings:
        return [], []
    from genshin_db.runtime_action_parameter import (
        get_action_parameter_context,
        project_action_parameter,
    )
    from genshin_db.runtime_talent_level import (
        get_level_parameter_context,
        project_action_parameter_at_level,
    )

    hits = {h["id"]: h for h in template["hits"]}
    # Prepared contexts are server-local objects from the same compose read
    # session, never native/cache receipts supplied by a caller over the API.
    actors, skills, cache, records, sources = {}, {}, dict(prepared_contexts or {}), [], []
    for binding in bindings:
        path = "hit_parameter_sources." + binding["hit_id"]
        consumer = "hit_parameter:" + binding["hit_id"]

        def task_missing(code, message, *, category="knowledge_missing",
                         scope="agent_materialization", retryable=True,
                         _path=path, _consumer=consumer):
            emit_task_missing(
                missing, code, _path, message, "review_hit_parameter",
                consumer=_consumer, category=category,
                blocking_scope=scope, retryable=retryable,
                repair_hint="核对该命中的角色、等级、参数原文、倍率用途和当前来源",
            )

        hit = hits.get(binding["hit_id"])
        if hit is None or hit["damage_system"] != "classic":
            raise PlanError("倍率来源只能关联原生输入中已有的classic命中或原型", "reference", {"path": path})
        if "multiplier" in hit:
            raise PlanError("数据库倍率与手填multiplier不能同时交付；原生命中须省略该字段", "duplicate_delivery", {"path": path})
        if hit.get("scaling_stat") != binding["scaling_stat"]:
            raise PlanError("命中须显式声明与参数用途一致的scaling_stat", "parameter_interpretation", {"path": path})
        selection, line, cid = binding["selection"], binding["version_line"], hit["character"]
        level_bound = binding.get('parameter_contract') == 'level-bound-parameter-v1'
        identity = (selection["character_id"], line, selection["constellation"])
        if actors.setdefault(cid, identity) != identity:
            raise PlanError("同一方案角色的技能参数不能来自不同角色、资料线或命座", "character_identity", {"path": path})
        char_ref = (plan["equipment_inputs"].get(cid, {}).get("character_ref") or {})
        if char_ref and (char_ref["db_character_id"], char_ref["version_line"]) != identity[:2]:
            raise PlanError("技能参数角色身份与装备方案角色不一致", "character_identity", {"path": path})
        if level_bound:
            from .mechanism_execution_scope import identities
            declared = identities({**plan,'rotation_model':plan.get('rotation_model') or {'abilities':[]}},cid)
            expected = {'character_id':selection['character_id'],'version_line':line,'constellation':selection['constellation']}
            for row in declared:
                if any(row[k] is not None and (type(row[k]) is not type(v) or row[k]!=v) for k,v in expected.items() if k in row):
                    raise PlanError('等级来源与角色的其它显式资料身份矛盾','character_identity',{'path':path,'origin':row.get('origin')})
        record = {"hit_id": hit["id"], "actor": cid, "status": "pending", "selection": copy.deepcopy(selection),
                  "version_line": line, "param_index": binding["param_index"], "scaling_stat": binding["scaling_stat"],
                  "interpretation_basis": binding["source"], "gameplay_interpretation": "declared_not_verified",
                  "declared_purpose": binding["purpose"], "interpretation_status": binding["status"],
                  "actor_identity": "matches_equipment_reference" if char_ref else "explicitly_declared"}
        records.append(record)
        key = fingerprint({"selection": selection, "version_line": line,
                           **({'parameter_contract':binding['parameter_contract']} if level_bound else {})})
        try:
            if key not in cache:
                cache[key] = (get_level_parameter_context if level_bound else get_action_parameter_context)(db_path, **selection, version_line=line)
            projected = (project_action_parameter_at_level if level_bound else project_action_parameter)(cache[key], binding["param_index"])
        except (EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
            task_missing(
                "parameter_source_unavailable", str(exc),
                category="core_data_missing", scope="task_capability",
            )
            record["issue"] = {"code": getattr(exc, "reason", "unavailable"), "message": str(exc)}
            continue
        for name in ("snapshot", "source", "scalar_projection", "level", "entry_sources", "action_dispatch", "operation_execution", "parameter_descriptions", "parameter_descriptions_raw"):
            record[name] = copy.deepcopy(projected[name])
        record["resolved_selection"] = copy.deepcopy(projected["selection"])
        if level_bound:
            record['parameter_contract']=binding['parameter_contract']
            record['level_profile']=copy.deepcopy(projected['level_profile'])
            for row in declared:
                if row.get('skill_depot_id') is not None and row['skill_depot_id']!=projected['selection']['skill_depot_id']:
                    raise PlanError('等级来源与同一角色的技能组声明矛盾','character_identity',{'path':path})
        entry = projected['entry_sources']['skill']
        group = entry.get('fields',entry.get('record',{})).get('proudSkillGroupId')
        skill_key = (cid, projected["selection"]["skill_depot_id"], group or projected["selection"]["skill_id"])
        level = projected["level"]["effective_level"]
        if level is not None and skills.setdefault(skill_key, level) != level:
            raise PlanError("同一角色同一技能在轴内不能采用不同有效等级", "parameter_level", {"path": path})
        origin = projected["source"]
        if origin is not None:
            confirmation = {"expected_snapshot_id": projected["snapshot"]["snapshot_id"],
                            "expected_node_sha256": origin["node_sha256"]}
            if level_bound:
                confirmation['expected_level_fingerprint']=projected['level_profile']['content_fingerprint']
            record["required_confirmation"] = confirmation
            if any(binding[key] != value for key, value in confirmation.items()):
                task_missing(
                    "source_changed",
                    "请核对当前角色、等级、参数原文及倍率用途，再确认本次来源",
                )
                continue
        value = projected["scalar_projection"]["value"]
        if binding["status"] == "pending":
            task_missing(
                "parameter_interpretation_pending",
                "参数用途尚未核实；来源确认不能替代倍率与属性的解释",
            )
            continue
        if projected["scalar_projection"]["status"] != "ready" or value is None or value < 0:
            task_missing(
                "unsupported_parameter_source",
                "该参数或等级尚不能自动交付非负倍率，请查看原文及投影原因",
                category="engine_capability_missing", scope="engine_capability",
                retryable=False,
            )
            continue
        hit["multiplier"] = value
        record.update(status="ready", multiplier=value, compiler="database-hit-parameter-v2" if level_bound else "database-hit-parameter-v1")
        record["compilation_fingerprint"] = fingerprint(record)
        sources.append({"kind": "hit_parameter", "hit_id": hit["id"], "snapshot_id": projected["snapshot"]["snapshot_id"],
                        "node_sha256": origin["node_sha256"], "compilation_fingerprint": record["compilation_fingerprint"]})
    return records, sources
