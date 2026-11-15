"""Bounded, non-executing character action facts from one runtime snapshot.

Parameter-modification targets are deliberately not treated as cast entry points.
"""
from __future__ import annotations

import re

from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.validation import QueryValidationError

ACTION_FACT_LIMITS = {"max_bytes": 262144, "max_operations": 128, "max_abilities": 64, "max_issues": 256}


def get_runtime_action_facts(db_path, *, character_id, slot=None, skill_id=None,
                             skill_depot_id=None, talent_level=None, constellation=0,
                             version_line="release", snapshot_id=None):
    """Resolve explicit record relationships, never infer animation or dispatch."""
    # Late import keeps the shared query transaction/cache implementation canonical.
    from genshin_db.runtime_queries import (
        _definition_summary,
        _integer,
        _lookup,
        _Nodes,
        _read,
        _references,
    )

    _integer(character_id, "character_id")
    _integer(constellation, "constellation", 0, 6)
    if (slot is None) == (skill_id is None):
        raise QueryValidationError("slot与skill_id必须且只能提供一个")
    if slot is not None and (not isinstance(slot, str) or slot not in ("normal", "skill", "burst")):
        raise QueryValidationError("slot只允许normal/skill/burst")
    for value, name in ((skill_id, "skill_id"), (skill_depot_id, "skill_depot_id")):
        if value is not None:
            _integer(value, name)
    if talent_level is not None:
        _integer(talent_level, "talent_level", 1, 15)

    with _read(db_path, version_line, snapshot_id) as (conn, state):
        nodes = _Nodes(conn, version_line)
        issues, records, talents, bindings, abilities = [], {}, [], [], {}
        operation_count = 0

        def add_issue(value):
            if len(issues) >= ACTION_FACT_LIMITS["max_issues"]:
                raise RuntimeConfigError("动作资料未决项数量超限", "response_too_large", 413)
            issues.append(value)

        def exact(table, key):
            path = f"ExcelBinOutput/{table}ExcelConfigData.json"
            rows = _lookup(conn, version_line, "excel_record", key, path)
            if len(rows) != 1:
                raise RuntimeConfigError("资料入口不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                         404 if not rows else 409, {"table": table, "key": key})
            return rows[0]

        def record(name, row, fields):
            payload, origin = nodes.node(row)
            records[name] = {"definition": _definition_summary(row), **origin,
                             "fields": {k: payload[k] for k in fields if k in payload},
                             "field_pointers": {k: row["json_pointer"] + "/" + k for k in fields if k in payload}}
            return payload

        def add_ability(name, relation, origin):
            found = _lookup(conn, version_line, "ability", name) if isinstance(name, str) and name else []
            candidates = []
            for row in found:
                # Reading the actual node checks its stored fingerprint.
                nodes.node(row)
                abilities[row["definition_id"]] = _definition_summary(row)
                candidates.append(row["definition_id"])
            if len(abilities) > ACTION_FACT_LIMITS["max_abilities"]:
                raise RuntimeConfigError("动作资料Ability数量超限", "response_too_large", 413)
            if len(found) != 1:
                add_issue({"code": "missing_ability" if not found else "ambiguous_ability_variant",
                               "ability_name": name, "candidate_ids": candidates})
            return {"ability_name": name, "relation": relation, "candidate_ids": candidates,
                    "resolution_status": "resolved" if len(found) == 1 else "missing" if not found else "ambiguous",
                    "origin": origin}

        def add_talent(row, role):
            nonlocal operation_count
            payload, record_origin = nodes.node(row)
            config = payload.get("openConfig")
            if not config:
                return
            found = _lookup(conn, version_line, "talent_config", config)
            if len(found) != 1:
                add_issue({"code": "missing_talent" if not found else "ambiguous_talent", "open_config": config})
                return
            definition = found[0]
            raw, origin = nodes.node(definition)
            view = nodes.normalization.normalize(raw, kind="talent_config") if nodes.normalization else raw
            if not isinstance(view, list):
                raise RuntimeConfigError("Talent操作形状错误", "dataset_invalid", 500)
            operation_count += len(view)
            if operation_count > ACTION_FACT_LIMITS["max_operations"]:
                raise RuntimeConfigError("动作资料Talent操作数量超限", "response_too_large", 413)
            refs = _references(conn, version_line, definition["definition_id"], nodes)
            for ref in refs:
                if ref["resolution_status"] not in ("resolved", "source_empty"):
                    add_issue({"code": "unresolved_talent_reference", "reference_id": ref["reference_id"],
                               "resolution_status": ref["resolution_status"], "source_path": ref["source_path"],
                               "json_pointer": ref["json_pointer"]})
            talents.append({"role": role, "definition": _definition_summary(definition), **origin,
                            "parameter_record_origin": record_origin, "param_list": payload.get("paramList", []),
                            "operations_raw": raw, "operations_normalized": view, "references": refs,
                            "execution_support": "not_evaluated"})
            for index, op in enumerate(view):
                if not isinstance(op, dict) or op.get("$type") != "ModifyAbility":
                    continue
                expression = op.get("paramDelta")
                item = {"role": role, "operation_pointer": definition["json_pointer"] + f"/{index}",
                        "source_path": definition["source_path"], "parameter_name": op.get("paramSpecial"),
                        "raw_expression": expression, "operation_semantics": "not_evaluated",
                        "target": add_ability(op.get("abilityName"), "parameter_modification_target", origin)}
                match = re.fullmatch(r"%([1-9][0-9]*)", expression) if isinstance(expression, str) else None
                values = payload.get("paramList", [])
                if match and isinstance(values, list) and int(match[1]) <= len(values):
                    offset = int(match[1]) - 1
                    item.update(parameter_input_status="resolved", parameter_input=values[offset],
                                parameter_input_origin={**record_origin,
                                                        "json_pointer": row["json_pointer"] + f"/paramList/{offset}"})
                else:
                    item["parameter_input_status"] = "pending"
                    add_issue({"code": "unresolved_parameter_input", "source_path": item["source_path"],
                               "operation_pointer": item["operation_pointer"], "raw_expression": expression})
                bindings.append(item)

        avatar_row = exact("Avatar", {"id": character_id})
        avatar = record("avatar", avatar_row, ("id", "skillDepotId", "candSkillDepotIds", "combatConfigHash", "animatorConfigPathHash"))
        depot_ids = list(dict.fromkeys([avatar.get("skillDepotId"), *avatar.get("candSkillDepotIds", [])]))
        depot_ids = [v for v in depot_ids if type(v) is int and v > 0]
        if skill_depot_id is None:
            if len(depot_ids) != 1:
                raise RuntimeConfigError("角色技能组需明确选择", "skill_depot_selection_required", 409,
                                         {"candidate_skill_depot_ids": depot_ids})
            skill_depot_id = depot_ids[0]
        elif skill_depot_id not in depot_ids:
            raise QueryValidationError("skill_depot_id不属于角色明确列出的技能组")
        depot_row = exact("AvatarSkillDepot", {"id": skill_depot_id})
        depot = record("depot", depot_row, ("id", "skills", "subSkills", "energySkill", "talents", "inherentProudSkillOpens", "extraAbilities"))
        slots = depot.get("skills", [])
        slot_ids = {"normal": slots[0] if slots else 0, "skill": slots[1] if len(slots) > 1 else 0,
                    "burst": depot.get("energySkill", 0)}
        if slot is not None:
            skill_id = slot_ids[slot]
            if not skill_id:
                raise RuntimeConfigError("所选技能槽为空", "empty_skill_slot", 409, {"slot": slot})
        elif skill_id not in [*slots, *depot.get("subSkills", []), depot.get("energySkill")]:
            raise QueryValidationError("skill_id不属于所选技能组")
        matching_slots = [k for k, value in slot_ids.items() if value == skill_id]
        skill_row = exact("AvatarSkill", {"id": skill_id})
        skill = record("skill", skill_row, ("id", "proudSkillGroupId", "triggerID", "abilityName", "cdTime", "maxChargeNum",
                                            "costElemType", "costElemVal", "costStamina", "nameTextMapHash", "descTextMapHash"))

        # Level-changing constellation operations are not yet covered by the
        # reviewed schema profile. Never silently use level 10 for such a build.
        effective = talent_level if talent_level is not None else 10 if constellation == 0 else None
        level = {"base_level": 10 if talent_level is None else None, "effective_level": effective,
                 "source": "explicit_effective_level" if talent_level is not None else "default_base_level",
                 "constellation_applied": False, "constellation_level_support": "not_evaluated" if constellation else "not_applicable"}
        if effective is None:
            add_issue({"code": "effective_talent_level_required", "message": "命座加级解释尚未验证，请明确提供包含命座加级后的有效talent_level"})
        active = depot.get("talents", [])[:constellation]
        for tid in active:
            if tid:
                add_talent(exact("AvatarTalent", {"talentId": tid}), "constellation")
        proud_group = skill.get("proudSkillGroupId")
        if effective is not None and proud_group:
            path = "ExcelBinOutput/ProudSkillExcelConfigData.json"
            candidates = [r for r in nodes.source(path)[1] if r.get("proudSkillGroupId") == proud_group and r.get("level") == effective]
            if len(candidates) != 1:
                add_issue({"code": "missing_parameter_record" if not candidates else "ambiguous_parameter_record",
                               "group_id": proud_group, "level": effective})
            else:
                proud_row = exact("ProudSkill", {"proudSkillId": candidates[0]["proudSkillId"]})
                record("proud_skill", proud_row, ("proudSkillId", "proudSkillGroupId", "level", "paramList", "paramDescList", "openConfig"))
                add_talent(proud_row, "skill_level")
        elif not proud_group:
            add_issue({"code": "missing_proud_skill_group"})
        direct = None
        if skill.get("abilityName"):
            direct = add_ability(skill["abilityName"], "skill_explicit_ability_name", records["skill"])
        dispatch_reasons = ["avatar_resource_identity_unresolved", "action_trigger_semantics_not_compiled"]
        result = {"facts_version": 1, "status": "pending" if issues else "ready", "snapshot": state,
                  "selection": {"character_id": character_id, "skill_depot_id": skill_depot_id, "skill_id": skill_id,
                                "slot": slot, "matching_slots": matching_slots, "constellation": constellation,
                                "active_constellation_ids": active},
                  "level": level, "records": records, "talents": talents, "parameter_bindings": bindings,
                  "ability_candidates": list(abilities.values()), "direct_skill_ability": direct, "issues": issues,
                  "action_dispatch": {"status": "pending", "reasons": dispatch_reasons},
                  "execution_support": "not_evaluated", "limits": dict(ACTION_FACT_LIMITS),
                  "notes": ["ready仅表示所列资料关系已装配，不证明动作派发、时序、技能完整效果或可执行性",
                            "Talent原始操作可查询不代表支持执行，所有操作语义均未评估",
                            "ModifyAbility目标是参数修改对象，不能作为技能释放入口",
                            "parameter_input是参数表输入值，不是执行修改后最终ability参数",
                            "未解释的字段原样保留；数值缺失不默认为0；elementDurability不转换为GU"]}
        result["content_fingerprint"] = fingerprint(result)
        if len(dumps(result).encode("utf-8")) > ACTION_FACT_LIMITS["max_bytes"]:
            raise RuntimeConfigError("动作资料响应字节数超限", "response_too_large", 413)
        return result
