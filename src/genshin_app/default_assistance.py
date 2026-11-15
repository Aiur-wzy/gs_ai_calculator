"""Small shared advisory surface for CLI, native tools and GUI."""
from __future__ import annotations

import copy

from genshin_app.calculation_scope import validation_scope


def safe_default_assistance(plan, db_path=None, *, result=None, report=None):
    from genshin_app.advisory_observation import observe_advisory

    return observe_advisory("default_assistance", lambda: read_default_assistance(
        plan, db_path, result=result, report=report), fallback={
            "contract": "genshin-default-calculation-assistance-v1", "panels": [],
            "energy": {"state": "observation_unavailable"},
            "damage_counts": {"state": "observation_unavailable"},
            "validation_scope": {"contract": "genshin-calculation-validation-scope-v1",
                "state": "observation_unavailable", "acceptance_effect": "none",
                "summary": "验证范围的辅助说明暂不可用；不得据此声称游戏机制已完整核验。"}})


def read_default_assistance(plan, db_path=None, *, result=None, report=None):
    from genshin_app.calculation_assistance import energy_summary
    from genshin_app.damage_count_observation import observe_damage_counts
    from genshin_app.panel_assistance import compare_plan_panels

    report = report or {}
    panels = report.get("panel_assistance")
    if panels is None:
        panels = compare_plan_panels(plan, db_path) if db_path is not None else []
    energy = energy_summary(result or {}, plan)
    return {"contract": "genshin-default-calculation-assistance-v1", "authority": "advisory_only",
            "acceptance_effect": "none", "candidate_effect": "none",
            "validation_scope": validation_scope(plan, result, report),
            "panels": panels, "energy": energy,
            "damage_counts": observe_damage_counts(plan, result or {}, report),
            "details": {"tool": "gs_diagnose_result", "view": "assistance",
                        "cli": "equipment diagnose --request"}}


def compact_default_assistance(value, *, limit=8):
    """A bounded reading surface; the complete evidence stays in the report.

    Limits affect presentation only, never generation, execution or admission.
    Nested lists are bounded too (a single character may have many differences).
    """
    omitted = []

    def project(item, pointer):
        if isinstance(item, dict):
            return {key: project(child, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"))
                    for key, child in item.items()}
        if isinstance(item, list):
            if len(item) > limit:
                omitted.append({"path": pointer, "total": len(item), "shown": limit,
                                "omitted": len(item) - limit})
            return [project(child, f"{pointer}/{i}") for i, child in enumerate(item[:limit])]
        if isinstance(item, str) and len(item) > 2048:
            omitted.append({"path": pointer, "total_characters": len(item), "shown_characters": 2048})
            return item[:2048]
        return copy.deepcopy(item)

    result = project(value or {}, "")
    result["presentation"] = {"truncated": bool(omitted), "omissions": omitted,
                              "details_ref": {"resource": "default_assistance"},
                              "interpretation": "仅精简本次显示；完整数据保存在计算报告中，可按路径续读。"}
    return result
