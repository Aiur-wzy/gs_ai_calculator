"""Factual validation scope: neither a completeness score nor a new gate."""
from __future__ import annotations

from genshin_app.failure_analysis import _trace_state


def validation_scope(plan, result=None, report=None):
    result, report = result or {}, report or {}
    mechanics = report.get("mechanics_model") or plan.get("mechanics_model") or {}
    rotation = result.get("rotation_check") or {}
    template = plan.get("manual_template") or {}
    hits = template.get("hits") or []
    prefilled = [hit for hit in hits if isinstance(hit.get("reaction"), dict)
                 and hit["reaction"].get("type") not in (None, "none")]
    dynamic_declared = bool(mechanics.get("elemental_model") or mechanics.get("elemental_world"))
    dynamic_state, dynamic_count = _trace_state(result, "elemental", dynamic_declared)
    if dynamic_state not in ("trace_saved", "trace_empty"):
        world = result.get("elemental_world")
        if isinstance(world, dict) and isinstance(world.get("trace"), list):
            dynamic_state, dynamic_count = _trace_state({**result, "elemental": world}, "elemental", dynamic_declared)
    dynamic_observed = dynamic_state == "trace_saved"
    special_declared = bool(mechanics.get("special_reaction_model"))
    special_state, special_count = _trace_state(result, "special_reactions", special_declared)
    special_observed = special_state == "trace_saved"
    lines = []
    if not result:
        lines.append("尚未执行伤害计算；以下只描述当前输入。")
    else:
        lines.append("结果按本次声明的输入计算，不等于已穷尽游戏机制。")
    if dynamic_observed:
        lines.append("已有动态元素执行记录；只覆盖输入声明的目标、附着与规则。")
    elif dynamic_state == "trace_empty":
        lines.append("元素流水已保存但为空；尚未观察到实际附着或反应事件。")
    elif dynamic_declared:
        lines.append("已声明动态元素模型，尚无可核对的执行记录。")
    else:
        lines.append("未建立动态元素模型，未验证元素底、附着与反应顺序。")
    if prefilled:
        lines.append(f"{len(prefilled)}条输入命中预填了反应；预填值不是元素模拟证据。")
    if special_declared or special_observed:
        lines.append("特殊反应领域以实际执行记录为准，来源事实仍需核实。")
    if special_state == "trace_empty":
        lines.append("特殊反应流水已保存但为空，不能据此证明已执行特殊反应。")
    equipment = plan.get("equipment_inputs") or {}
    missing_gear = [a.get("id") for a in template.get("characters", []) if not equipment.get(a.get("id"))]
    if missing_gear:
        lines.append("部分角色未提供结构化配装，未验证其装备组成与词条预算。")
    action_check = rotation.get("action_check") or {}
    energy_check = rotation.get("energy_check") or {}
    interpretation = rotation.get("damage_interpretation") or {}
    complete_window = interpretation.get("complete_first_cycle") is True
    opening = energy_check.get("opening_status", "not_checked")
    finite_energy = ("passed" if opening == "passed" and complete_window else
                     "failed" if opening == "failed" else "not_verified")
    if finite_energy == "passed" and energy_check.get("status") != "passed":
        lines.append("已输入时段内的扣能与回能检查通过；这不代表时段之后能无限循环。")
    elif interpretation.get("kind") == "executed_prefix":
        lines.append("本次只执行到中途，后续动作和能量尚未验证；不能把已执行部分当成完整排轴。")
    return {"contract": "genshin-calculation-validation-scope-v1", "acceptance_effect": "none",
            "summary": " ".join(lines), "messages": lines,
            "execution_available": bool(result),
            "actions": action_check.get("status", "not_checked"),
            "energy": energy_check.get("status", "not_checked"),
            "energy_scope": {"supplied_window": finite_energy,
                             "opening_status": opening,
                             "complete_supplied_window": complete_window,
                             "sustainable_loop": energy_check.get("status", "not_checked"),
                             "interpretation": "已输入动作的能量合法性与无限循环证明是不同问题；未输入后续轮次不会自动计伤。"},
            "elemental": {"declared": dynamic_declared, "execution_observed": dynamic_observed,
                          "state": dynamic_state, "trace_event_count": dynamic_count},
            "special_reactions": {"declared": special_declared, "execution_observed": special_observed,
                                  "state": special_state, "trace_event_count": special_count},
            "prefilled_reaction_hit_count": len(prefilled),
            "unlinked_equipment_characters": missing_gear,
            "game_mechanism_completeness": "not_independently_certified"}
