"""Compile bounded reaction damage sequence sources, not arbitrary abilities."""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError
from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET, SWIRL_RULESETS

from .files import fingerprint
from .schema import PlanError

COMPILER = "database-swirl-damage-v1"
DYNAMIC_COMPILER = "database-swirl-damage-v2"


def compile_reaction_damage_sources(plan, db_path, missing):
    bindings = plan.get("reaction_damage_sources", [])
    if not bindings:
        return [], []
    from genshin_db.runtime_reaction_damage import get_runtime_reaction_damage_profile
    from genshin_engine.elemental import validate_elemental_model

    model = (plan.get("mechanics_model") or {}).get("elemental_model")
    path = "reaction_damage_sources.swirl"

    def task_missing(code, message, *, category="knowledge_missing",
                     scope="agent_materialization", retryable=True):
        emit_task_missing(
            missing, code, path, message, "review_reaction_damage_source",
            consumer="reaction_damage:swirl", category=category,
            blocking_scope=scope, retryable=retryable,
            repair_hint="核对普通扩散规则集、各元素来源、伤害序列和当前确认指纹",
        )

    if model is None or model.get("ruleset") not in SWIRL_RULESETS or "swirl_damage" not in model:
        task_missing(
            "missing_input", "数据库扩散伤害来源需要普通扩散规则集及明确的swirl_damage档案",
            category="core_data_missing", scope="task_capability",
        )
        return [], []
    profile = model["swirl_damage"]
    if profile.get("icd") != {"kind": "unknown"}:
        raise PlanError("数据库扩散伤害规则与手填ICD不能同时交付；原生icd须为unknown占位", "duplicate_delivery", {"path": path})
    binding = bindings[0]
    dynamic = model["ruleset"] == DYNAMIC_SWIRL_RULESET
    elements = ("hydro", "cryo", "pyro") if dynamic else ("hydro", "cryo")
    compiler = DYNAMIC_COMPILER if dynamic else COMPILER
    element_label = "冰水火" if dynamic else "冰水"
    record = {"reaction_type": "swirl", "status": "pending", "version_line": binding["version_line"],
              "binding_basis": binding["source"], "compiler": compiler,
              "gameplay_semantics": "ordinary_origin_target_ruleset; ability_execution_not_implemented",
              "scope": "普通单目标冰/水扩散的原目标伤害序列；一般攻击对象、Alter、Star和传播未执行"}
    if dynamic:
        record["scope"] = "普通单目标冰/水/火扩散的原目标伤害序列；一般攻击对象、Alter、Star和传播未执行"
    records, queries = [record], []
    try:
        for element in elements:
            queries.append(get_runtime_reaction_damage_profile(db_path, reaction_type="swirl", element=element,
                                                               version_line=binding["version_line"]))
    except (EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
        task_missing(
            "reaction_source_unavailable", str(exc),
            category="core_data_missing", scope="task_capability",
        )
        record["issue"] = {"code": getattr(exc, "reason", "unavailable"), "message": str(exc)}
        return records, []
    record["profiles"] = copy.deepcopy(queries)
    if dynamic and tuple(q.get("selection", {}).get("element") for q in queries) != elements:
        record["issue"] = {"code": "reaction_source_element_conflict", "message": "新规则必须分别取得冰、水、火的原目标资料"}
        task_missing("reaction_source_element_conflict", record["issue"]["message"])
        return records, []
    snapshots = {q["snapshot"]["snapshot_id"] for q in queries}
    if len(snapshots) != 1:
        record["issue"] = {"code": "reaction_source_snapshot_conflict", "message": f"{element_label}扩散来源不属于同一快照"}
        task_missing(
            "reaction_source_snapshot_conflict",
            f"{element_label}扩散来源不属于同一快照",
            category="core_data_missing", scope="task_capability",
        )
        return records, []
    record["snapshot"] = copy.deepcopy(queries[0]["snapshot"])
    profile_fingerprint = fingerprint({"compiler": compiler, "ruleset": model["ruleset"],
                                       "sources": [q["content_fingerprint"] for q in queries]})
    confirmation = {"expected_snapshot_id": next(iter(snapshots)), "expected_profile_fingerprint": profile_fingerprint}
    record.update(profile_fingerprint=profile_fingerprint, required_confirmation=confirmation)
    if any(q["execution"]["sequence_status"] != "ready" for q in queries):
        task_missing(
            "unsupported_reaction_source",
            "当前来源尚不能交付有限二元伤害冷却序列；一般能力执行状态与序列状态分开",
            category="engine_capability_missing", scope="engine_capability",
            retryable=False,
        )
        return records, []
    signatures = [(q["projection"]["reset_cycle_seconds_rounded"], q["projection"]["damage_sequence"]) for q in queries]
    if any(signature != signatures[0] for signature in signatures[1:]):
        record["issue"] = {"code": "reaction_damage_group_conflict", "message": f"{element_label}扩散伤害规则不同，当前共用档案不能任意选取一条"}
        task_missing(
            "reaction_damage_group_conflict",
            f"{element_label}扩散伤害规则不同，当前共用档案不能任意选取一条",
        )
        return records, []
    reset, sequence = signatures[0]
    compiled_icd = {"kind": "sequence", "reset_seconds": reset, "sequence": copy.deepcopy(sequence)}
    candidate = copy.deepcopy(model)
    candidate["swirl_damage"]["icd"] = compiled_icd
    issues = validate_elemental_model(candidate)
    if issues:
        record["assembly_issues"] = copy.deepcopy(issues)
        for issue in issues:
            emit_task_missing(
                missing, issue["code"], issue["path"], issue["message"],
                "review_reaction_damage_source", consumer="reaction_damage:swirl",
                category="engine_capability_missing", impact="high",
                blocking_scope="engine_capability",
                repair_hint="修复组合后的通用元素模型，再重新编译反应伤害来源",
            )
        return records, []
    if any(binding[key] != value for key, value in confirmation.items()):
        task_missing(
            "source_changed",
            f"请复核{element_label}原目标攻击关系、伤害序列和普通模式适用范围，再确认当前来源",
        )
        return records, []
    profile["icd"] = compiled_icd
    record.update(status="ready", compiled_icd=copy.deepcopy(profile["icd"]))
    record["compilation_fingerprint"] = fingerprint(record)
    return records, [{"kind": "reaction_damage", "reaction_type": "swirl", "snapshot_id": next(iter(snapshots)),
                      "profile_fingerprint": profile_fingerprint, "compilation_fingerprint": record["compilation_fingerprint"]}]
