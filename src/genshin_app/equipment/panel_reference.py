"""Optional aggregate-roll arithmetic; never edits or admits a candidate."""
from __future__ import annotations

import copy
import math

from genshin_db import equipment_queries, queries
from genshin_db.validation import version_line as validate_line

from .files import fingerprint
from .panel_calculation import PROP_MAP, build_character_basis, calculate_static_panel
from .roll_feasibility import aggregate_roll_feasibility
from .schema import ELEMENTS, STATS, PlanError, _check, _json_guard

CONTRACT = "genshin-aggregate-panel-reference-v1"
_NUMBER = {"type": "number", "minimum": 0}
_PROP = {"type": "string", "enum": sorted(k for k in PROP_MAP if k != "FIGHT_PROP_BASE_ATTACK")}
_STAT = {"type": "string", "enum": [*STATS, *("dmg_bonus:" + e for e in ELEMENTS)]}
_COMPARISON_VALUE = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


REQUEST_SCHEMA = _object({
    "db_character_id": {"type": "integer", "minimum": 1}, "level": {"enum": [90, 100]},
    "weapon": _object({"weapon_id": {"type": "integer", "minimum": 1},
                       "level": {"type": "integer", "minimum": 1, "maximum": 100},
                       "ascension": {"type": "integer", "minimum": 0, "maximum": 6},
                       "refinement": {"type": "integer", "minimum": 1, "maximum": 5}}, ("weapon_id", "level", "ascension")),
    "artifacts": _object({"rarity": {"type": "integer", "minimum": 1, "maximum": 5},
                          "level": {"type": "integer", "minimum": 0, "maximum": 20},
                          "sands": _PROP, "goblet": _PROP, "circlet": _PROP}, ("rarity", "level", "sands", "goblet", "circlet")),
    "artifact_sets": {"type": "array", "maxItems": 8, "items": _object({
        "set_id": {"type": "integer", "minimum": 1}, "count": {"type": "integer", "minimum": 1, "maximum": 5}}, ("set_id", "count"))},
    "substats": {"type": "array", "maxItems": 64, "items": _object({
        "prop_type": _PROP, "count": _NUMBER, "value_per_roll": _NUMBER,
        "source": {"type": "string", "minLength": 1, "maxLength": 4096}}, ("prop_type", "count", "value_per_roll", "source"))},
    "static_contributions": {"type": "array", "maxItems": 128, "items": _object({
        "contribution_id": {"type": "string", "minLength": 1, "maxLength": 128}, "stat": _STAT,
        "operation": {"enum": ["flat", "percent"]}, "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "source": {"type": "string", "minLength": 1, "maxLength": 4096}}, ("contribution_id", "stat", "operation", "value", "source"))},
    "current_panel": _object({
        **{stat: _COMPARISON_VALUE for stat in (*STATS, "base_atk", "base_hp", "base_def")},
        "dmg_bonus": _object({element: _COMPARISON_VALUE for element in ELEMENTS}),
    }),
    "budget": _object({"total": _NUMBER, "crit_total": _NUMBER}),
}, ("db_character_id", "level"))


def panel_reference_contract():
    return {"contract": CONTRACT, "request_schema": copy.deepcopy(REQUEST_SCHEMA),
            "authority": "advisory_only", "candidate_effect": "none", "acceptance_effect": "none",
            "semantics": "按明确主属性和聚合副词条复算参考；所选套装只采用无open_config的显式add_props固定属性，param_list不作为是否有条件的判断。其它套装/武器被动由有据static_contributions声明，条件效果不自动折入。成品面板任务不需逆推或调用。",
            "units": "百分比用ratio；count允许任务授权的连续配平；附必要聚合容量软提示，不分配逐件掉落或证明最优。"}


def reference_panel(db_path, request, version_line="release"):
    _json_guard(request)
    errors = []
    _check(request, REQUEST_SCHEMA, "request", errors)
    if errors:
        raise PlanError("参考面板参数不合法", "invalid_panel_reference", errors)
    validate_line(version_line)
    req = copy.deepcopy(request)
    if sum(item["count"] for item in req.get("artifact_sets", [])) > 5:
        raise PlanError("圣遗物套装件数合计不能超过五件", "invalid_panel_reference")
    contributions, missing, warnings = [], [], []
    weapon_basis = None
    def unknown_scope(source_id):
        for stat in (*STATS, *("dmg_bonus:" + e for e in ELEMENTS)):
            contributions.append({"contribution_id": source_id + "." + stat,
                                  "stat": stat, "operation": "flat", "value": None, "source_refs": []})
    with queries.equipment_read_session(db_path):
        meta = queries.get_meta(db_path)
        detail = queries.get_character_detail(db_path, req["db_character_id"], version_line, sections=["character"])
        basis = build_character_basis(meta, detail, req["db_character_id"], req["level"], version_line)
        base = {stat: basis.get(stat) for stat in STATS}
        base.update({stat: basis.get("character_base_" + stat) for stat in ("atk", "hp", "def")})
        base["dmg_bonus"] = {}
        contributions.extend(copy.deepcopy(basis["ascension_contributions"]))
        weapon = req.get("weapon")
        if weapon is None:
            base["atk"] = None
            missing.append({"code": "weapon_selection_missing", "path": "weapon", "message": "武器未知，攻击白值与武器副属性未假定为0。"})
            unknown_scope("weapon.unspecified")
        else:
            stats = queries.get_weapon_stats(db_path, weapon["weapon_id"], weapon["level"], weapon["ascension"], version_line)
            weapon_basis = copy.deepcopy(stats)
            white = [p for p in stats["properties"] if p.get("prop_type") == "FIGHT_PROP_BASE_ATTACK"]
            base["atk"] = (base["atk"] + white[0]["value"] if len(white) == 1 and base["atk"] is not None and white[0].get("value") is not None else None)
            if base["atk"] is None:
                missing.append({"code": "base_attack_unknown", "path": "weapon.base_atk"})
            for index, prop in enumerate(stats["properties"]):
                if prop.get("prop_type") == "FIGHT_PROP_BASE_ATTACK":
                    continue
                mapped = PROP_MAP.get(prop.get("prop_type"))
                if mapped:
                    contributions.append({"contribution_id": f"weapon.property.{index}", "stat": mapped[0], "operation": mapped[1],
                                          "value": prop.get("value"), "source_refs": prop.get("source_refs", [])})
                else:
                    warnings.append({"code": "weapon_property_unmapped", "prop_type": prop.get("prop_type")})
        artifact = req.get("artifacts")
        if artifact is not None:
            selections = [("flower", "EQUIP_BRACER", "FIGHT_PROP_HP"), ("feather", "EQUIP_NECKLACE", "FIGHT_PROP_ATTACK"),
                          ("sands", "EQUIP_SHOES", artifact["sands"]), ("goblet", "EQUIP_RING", artifact["goblet"]),
                          ("circlet", "EQUIP_DRESS", artifact["circlet"])]
            for slot, equip_type, prop_type in selections:
                source = equipment_queries.get_artifact_main_stat_for_slot(db_path, artifact["rarity"], equip_type, artifact["level"], prop_type, version_line)
                stat, operation = PROP_MAP[prop_type]
                contributions.append({"contribution_id": "artifact.main." + slot, "stat": stat, "operation": operation,
                                      "value": source["value"], "source_refs": source["source_refs"], "selector": source["selector"]})
                if source["state"] != "ready":
                    missing.append({"code": "artifact_main_" + source["state"], "path": "artifacts." + slot, "candidates": source["candidate_values"]})
        else:
            missing.append({"code": "artifact_construction_unspecified", "path": "artifacts", "message": "未声明主属性模板；此参考不代表完整配装。"})
            unknown_scope("artifact.unspecified")
        set_ids = set()
        for selection in req.get("artifact_sets", []):
            if selection["set_id"] in set_ids:
                raise PlanError("套装选择重复，请合并同套装件数", "invalid_panel_reference")
            set_ids.add(selection["set_id"])
            detail_set = queries.get_artifact_set_detail(db_path, selection["set_id"], version_line)
            for row in detail_set["effects"]:
                if row["required_count"] > selection["count"]:
                    continue
                effect = row.get("effect")
                if effect is None:
                    missing.append({"code": "artifact_effect_source_missing", "set_id": selection["set_id"], "count": row["required_count"]})
                    continue
                if effect.get("open_config"):
                    warnings.append({"code": "non_static_set_effect_excluded", "set_id": selection["set_id"], "count": row["required_count"]})
                    continue
                for index, prop in enumerate(effect.get("add_props", [])):
                    code = prop.get("propType")
                    if not code or prop.get("value") == 0:
                        continue
                    mapped = PROP_MAP.get(code)
                    if not mapped or mapped[0].startswith("base_"):
                        warnings.append({"code": "set_property_unmapped", "prop_type": code})
                        continue
                    contributions.append({"contribution_id": f"artifact.set.{selection['set_id']}.{row['required_count']}.{index}",
                                          "stat": mapped[0], "operation": mapped[1], "value": prop.get("value"),
                                          "source_refs": effect.get("source_refs", [])})
        roll_count = crit_count = 0
        for index, roll in enumerate(req.get("substats", [])):
            stat, operation = PROP_MAP[roll["prop_type"]]
            value = roll["count"] * roll["value_per_roll"]
            if not math.isfinite(value):
                raise PlanError("词条数值溢出", "invalid_panel_reference")
            contributions.append({"contribution_id": f"substat.{index}", "stat": stat, "operation": operation,
                                  "value": value, "source_refs": [{"kind": "explicit_input", "source": roll["source"],
                                  "count": roll["count"], "value_per_roll": roll["value_per_roll"]}]})
            roll_count += roll["count"]
            if stat in {"crit_rate", "crit_dmg"}:
                crit_count += roll["count"]
            if not math.isfinite(roll_count) or not math.isfinite(crit_count):
                raise PlanError("有效词条数量合计溢出", "invalid_panel_reference")
        ids = {c["contribution_id"] for c in contributions}
        for item in req.get("static_contributions", []):
            if item["contribution_id"] in ids or item["operation"] == "percent" and item["stat"] not in {"atk", "hp", "def"}:
                raise PlanError("常驻贡献ID重复或作用区不合法", "invalid_panel_reference")
            ids.add(item["contribution_id"])
            contributions.append({"contribution_id": item["contribution_id"], "stat": item["stat"], "operation": item["operation"],
                                  "value": item["value"], "source_refs": [{"kind": "explicit_input", "source": item["source"]}]})
        try:
            panel = calculate_static_panel(base, contributions, allow_unknown=True)
        except PlanError as error:
            # This helper's arithmetic inputs are the declared construction;
            # overflow is a request error, not a missing engine mechanism.
            if error.reason == "unsupported_effect":
                raise PlanError("参考面板算术结果不是有限数值", "invalid_panel_reference") from error
            raise
        differences = []
        flat_current = {key: value for key, value in req.get("current_panel", {}).items() if key != "dmg_bonus"}
        flat_reference = {**panel, **{"dmg_bonus:" + key: value for key, value in panel["dmg_bonus"].items()}}
        bonuses = req.get("current_panel", {}).get("dmg_bonus", {})
        if isinstance(bonuses, dict):
            flat_current.update({"dmg_bonus:" + key: value for key, value in bonuses.items()})
        for stat, value in flat_current.items():
            reference = flat_reference.get(stat)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(reference, (int, float)):
                if not math.isfinite(value):
                    raise PlanError("比较面板不是有限数值", "invalid_panel_reference")
                if not math.isclose(value, reference, abs_tol=1e-9, rel_tol=1e-9):
                    difference = value - reference
                    if not math.isfinite(difference):
                        raise PlanError("比较面板差值溢出", "invalid_panel_reference")
                    differences.append({"stat": stat, "input": value, "reference": reference, "difference": difference})
        budget = req.get("budget", {})
        usage = {"total": roll_count, "crit_total": crit_count}
        comparisons = {key: {"used": used, "limit": budget.get(key),
                             "status": ("not_compared" if key not in budget
                                        else "exceeded" if used > budget[key] else "within_limit")}
                       for key, used in usage.items()}
        budget_report = {"used": roll_count, "crit_used": crit_count, "limits": budget,
                         "exceeded": any(row["status"] == "exceeded" for row in comparisons.values()),
                         "comparisons": comparisons}
        if len(budget) < len(usage):
            warnings.append({"code": "roll_budget_not_fully_compared",
                             "message": "未声明上限的项目只统计用量。词条预算比较只核对传入上限，等式与题面其他条件需另核。"})
        if budget_report["exceeded"]:
            warnings.append({"code": "roll_budget_exceeded", "message": "超出已声明词条预算；仅提醒，不修改输入，也不豁免任务要求。"})
        feasibility = aggregate_roll_feasibility(req.get("artifacts"), req.get("substats", []))
        if feasibility["status"] == "infeasible":
            warnings.append({"code": "aggregate_roll_infeasible", "message": feasibility["reason"],
                             "issues": copy.deepcopy(feasibility["issues"])})
        warnings.append({"code": "passives_explicit_only", "message": "只计声明的主副属性、所选套装可证固定属性和显式常驻贡献；条件Buff及武器被动不自动计入。"})
        return {"contract": CONTRACT, "authority": "advisory_only", "candidate_effect": "none", "acceptance_effect": "none",
                "roll_feasibility": feasibility,
                "reference_panel": panel, "basis": basis, "weapon_basis": weapon_basis,
                "weapon_growth_interpretation": "仅使用所选突破阶段的累计加值一次，不把历史突破阶段相加。",
                "contributions": contributions,
                "missing_inputs": missing, "warnings": warnings, "differences": differences, "budget": budget_report,
                "request_fingerprint": fingerprint(req), "database_source": {"upstream_commit": meta.get("upstream_commit:" + version_line, meta.get("upstream_commit")), "version_line": version_line},
                "completeness": "partial" if missing or any(panel[s] is None for s in STATS) else "declared_scope_only"}
