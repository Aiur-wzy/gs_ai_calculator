"""Exact level-table scalars; selecting their gameplay use remains explicit."""
from __future__ import annotations

import copy
import math

from genshin_db.parameter_references import parameter_indices
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint

TEXT_MAP_PATH = "TextMap/TextMap_MediumCHS.json"
PARAMETER_LIMITS = {"max_bytes": 65536, "max_param_index": 63, "max_descriptions": 64}


def parameter_capabilities():
    return {"projection_version": 1, **PARAMETER_LIMITS,
            "selection": "沿用action_facts角色/技能选择，param_index为零起始ProudSkill.paramList序号",
            "automatic_level_scope": "采用显式有效等级；无激活加级操作或已证明只影响其他技能时可用，命中所选技能或目标未决时保留数值并返回pending；完整自动加级使用action_parameter_at_level",
            "semantics": "原始参数标量，不执行ModifyAbility，不证明用途为伤害倍率",
            "descriptions": "同快照简体中文原文；按完整{paramN(:格式)?}引用关联，保留多参数整句与缺文状态，不解析为公式",
            "operation_execution": "not_evaluated", "action_dispatch": "pending"}


def _description_context(conn, state, facts):
    """Read original description strings once, without interpreting their use."""
    from genshin_db.runtime_queries import _Nodes

    record = facts["records"].get("proud_skill")
    result = {"records": [], "unresolved": [], "issues": []}
    if record is None:
        result["issues"].append({"code": "missing_parameter_record"})
        return result
    hashes = record["fields"].get("paramDescList", [])
    if not isinstance(hashes, list):
        result["issues"].append({"code": "invalid_parameter_description_list"})
        return result
    if len(hashes) > PARAMETER_LIMITS["max_descriptions"]:
        raise RuntimeConfigError("技能参数描述数量超限", "response_too_large", 413)
    if not hashes:
        return result
    nodes = _Nodes(conn, state["version_line"])
    source, text_map = nodes.source(TEXT_MAP_PATH)
    if not isinstance(text_map, dict):
        raise RuntimeConfigError("中文TextMap根形状错误", "dataset_invalid", 500)
    for offset, text_hash in enumerate(hashes):
        # Zero is an empty source slot, not a text reference.
        if type(text_hash) is int and text_hash == 0:
            continue
        origin = {key: record[key] for key in ("source_path", "source_sha256")}
        origin.update(json_pointer=record["json_pointer"] + f"/paramDescList/{offset}",
                      node_sha256=fingerprint(text_hash))
        item = {"text_map_hash": text_hash, "parameter_description_origin": origin}
        if type(text_hash) is not int or text_hash < 1:
            result["unresolved"].append({**item, "code": "invalid_description_hash"})
            continue
        key = str(text_hash)
        text = text_map.get(key)
        if not isinstance(text, str) or not text:
            result["unresolved"].append({**item, "code": "missing_description_text" if text is None or text == "" else "invalid_description_text"})
            continue
        if len(text.encode("utf8")) > PARAMETER_LIMITS["max_bytes"]:
            raise RuntimeConfigError("技能参数原文长度超限", "response_too_large", 413)
        indices = parameter_indices(text)
        result["records"].append({**item, "text": text, "param_indices": indices,
                                  "text_origin": {"source_path": TEXT_MAP_PATH, "json_pointer": "/" + key,
                                                  "source_sha256": source["source_sha256"], "node_sha256": fingerprint(text)}})
    if len(dumps(result).encode("utf8")) > PARAMETER_LIMITS["max_bytes"]:
        raise RuntimeConfigError("技能参数描述上下文超限", "response_too_large", 413)
    return result


def get_action_parameter_context(db_path, **selection):
    """Fresh internal context, reusable for several parameters of one selection.

    Both reads borrow the same locked transaction. Callers may cache this only
    within their current compose/read session, never accept client-supplied data.
    """
    from genshin_db.runtime_action_facts import get_runtime_action_facts
    from genshin_db.runtime_queries import _read
    from genshin_db.runtime_talent_level import get_runtime_talent_level

    with _read(db_path, selection.get("version_line", "release"), selection.get("snapshot_id")) as (conn, state):
        facts = get_runtime_action_facts(db_path, **selection)
        automatic_level_evidence = "not_present"
        level_operations = [
            operation
            for talent in facts.get("talents", [])
            if isinstance(talent, dict) and talent.get("role") == "constellation"
            for operation in talent.get("operations_normalized", [])
            if isinstance(operation, dict) and operation.get("$type") == "AddTalentExtraLevel"
        ]
        if level_operations:
            level_profile = get_runtime_talent_level(
                db_path,
                character_id=facts["selection"]["character_id"],
                skill_id=facts["selection"]["skill_id"],
                skill_depot_id=facts["selection"]["skill_depot_id"],
                constellation=facts["selection"]["constellation"],
                version_line=state["version_line"],
                snapshot_id=state["snapshot_id"],
            )
            contributions = level_profile.get("contributions", [])
            recipe_unverified = any(
                row.get("code") == "talent_level_recipe_unverified"
                for row in level_profile.get("issues", []) if isinstance(row, dict)
            )
            matches = [row.get("matches_selected_group") for row in contributions if isinstance(row, dict)]
            if recipe_unverified or len(contributions) != len(level_operations) or any(type(value) is not bool for value in matches):
                automatic_level_evidence = "unresolved"
            elif any(matches):
                automatic_level_evidence = "matches_selected_group"
            else:
                automatic_level_evidence = "verified_other_group"
        return {"facts": facts, "descriptions": _description_context(conn, state, facts),
                "automatic_level_evidence": automatic_level_evidence}


def _project_descriptions(context, param_index):
    if context is None:
        return {"status": "pending", "matched": [], "unresolved": [],
                "issues": [{"code": "description_context_unavailable"}]}
    matched = copy.deepcopy([row for row in context["records"] if param_index in row["param_indices"]])
    unresolved, issues = copy.deepcopy(context["unresolved"]), copy.deepcopy(context["issues"])
    if not matched:
        issues.append({"code": "no_matching_parameter_description", "parameter_token": f"param{param_index + 1}"})
    return {"status": "pending" if not matched else "partial" if unresolved or issues else "ready",
            "matched": matched, "unresolved": unresolved, "issues": issues}


def _raw_scalar(record, param_index):
    """One exact table scalar, without deciding level or gameplay eligibility."""
    issues, value, source = [], None, None
    if record is None:
        issues.append({"code": "missing_parameter_record", "message": "当前选择没有精确有效等级的技能参数记录"})
    else:
        values = record["fields"].get("paramList")
        source = {key: copy.deepcopy(record[key]) for key in ("definition", "source_path", "json_pointer", "source_sha256")}
        source["node_sha256"] = record["definition"]["node_sha256"]
        source["parameter_pointer"] = record["json_pointer"] + f"/paramList/{param_index}"
        if not isinstance(values, list) or param_index >= len(values):
            issues.append({"code": "parameter_index_missing", "message": "参数序号超出该等级原始参数表"})
        else:
            candidate = values[param_index]
            try:
                finite = type(candidate) in (int, float) and math.isfinite(candidate)
            except OverflowError:
                finite = False
            if finite:
                value = candidate
            else:
                issues.append({"code": "non_scalar_parameter", "message": "所选原文不是有限数值标量，不能解释为倍率"})
    return issues, value, source


def _selected_skill_has_automatic_level_operation(context, facts):
    """Whether one active constellation operation raises this selected skill.

    The legacy ``action_parameter`` entry point accepts an already-effective
    ``talent_level`` but cannot prove that it includes a constellation level
    increase.  Keep ordinary explicit levels usable and close only the case
    that the source data actually contains an active AddTalentExtraLevel for
    the selected skill.  The v2 level-bound entry point owns that proof.
    """
    if "facts" in context:
        return context.get("automatic_level_evidence") in ("matches_selected_group", "unresolved")

    # A bare facts projection lacks the canonical target-group lookup.  It can
    # prove absence from the active operation list, but cannot prove that a
    # present operation targets another ProudSkill group, so fail closed.
    for talent in facts.get("talents", []):
        if not isinstance(talent, dict) or talent.get("role") != "constellation":
            continue
        operations = talent.get("operations_normalized", [])
        if not isinstance(operations, list):
            continue
        for operation in operations:
            if isinstance(operation, dict) and operation.get("$type") == "AddTalentExtraLevel":
                return True
    return False


def project_action_parameter(context, param_index):
    """Project fresh internal context; legacy facts still support scalar-only use."""
    from genshin_db.runtime_queries import _integer

    _integer(param_index, "param_index", 0, 63)
    facts = context.get("facts", context)
    descriptions = context.get("descriptions") if "facts" in context else None
    record = facts["records"].get("proud_skill")
    issues, value, source = _raw_scalar(record, param_index)
    # The facts layer never infers constellation level bonuses.  Ordinary
    # explicitly supplied effective levels remain usable, but the legacy
    # entry point cannot verify an active automatic increase for this skill.
    # The v2 action_parameter_at_level entry point owns that proof.
    if facts["level"].get("effective_level") is None:
        issues.append({"code": "effective_talent_level_required",
                       "message": "非零命座必须明确提供包含命座加级后的有效talent_level"})
    elif _selected_skill_has_automatic_level_operation(context, facts):
        issues.append({"code": "automatic_level_unverified",
                       "message": "当前命座包含所选技能的自动等级提升；旧接口保留参数值但不验证有效等级，请使用action_parameter_at_level"})
    result = {"projection_version": 1, "status": "pending" if issues else "ready",
              "snapshot": copy.deepcopy(facts["snapshot"]), "selection": copy.deepcopy(facts["selection"]),
              "level": copy.deepcopy(facts["level"]), "param_index": param_index,
              "scalar_projection": {"status": "pending" if issues else "ready", "value": value,
                                    "semantics": "proud-skill-input-scalar-v1", "issues": issues},
              "source": source,
              "parameter_descriptions_raw": copy.deepcopy(record["fields"].get("paramDescList", [])) if record else [],
              "parameter_descriptions": _project_descriptions(descriptions, param_index),
              "entry_sources": {key: copy.deepcopy(facts["records"][key]) for key in ("avatar", "depot", "skill") if key in facts["records"]},
              "action_facts_status": facts["status"], "action_facts_issues": copy.deepcopy(facts["issues"]),
              "action_dispatch": copy.deepcopy(facts["action_dispatch"]), "operation_execution": "not_evaluated",
              "gameplay_interpretation": "not_evaluated", "limits": dict(PARAMETER_LIMITS)}
    result["content_fingerprint"] = fingerprint({key: result[key] for key in (
        "projection_version", "snapshot", "selection", "level", "param_index", "source", "scalar_projection", "parameter_descriptions")})
    if len(dumps(result).encode("utf8")) > PARAMETER_LIMITS["max_bytes"]:
        raise RuntimeConfigError("技能标量投影响应超限", "response_too_large", 413)
    return result


def get_runtime_action_parameter(db_path, *, param_index, **selection):
    from genshin_db.runtime_queries import _integer

    _integer(param_index, "param_index", 0, 63)
    return project_action_parameter(get_action_parameter_context(db_path, **selection), param_index)
