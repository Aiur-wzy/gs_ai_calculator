"""Recalculate build-only edits from a disk-verified whiteboard return package.

The original package remains the immutable baseline for every subsequent edit.
Neither a browser result cache nor an old dependency acknowledgement is promoted
to authority over the new build.
"""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.edit_review import prepare_equipment_edit
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import run_plan_calc
from genshin_app.whiteboard import (
    ELEMENTS,
    PANEL_KEYS,
    board_fingerprint,
    read_whiteboard_result,
    validate_result_intent,
    validate_whiteboard,
    validate_whiteboard_capacity_result,
)
from genshin_db.queries import NotFoundError


def _issue(code, path, message, action="ask_agent"):
    return {"code": code, "path": path, "message": message, "action": action}


def _locked_intent(board):
    locked = copy.deepcopy(board)
    for key in ("revision", "title"):
        locked.pop(key, None)
    for actor in locked["team"]:
        for key in ("panel", "weapon", "artifact_sets", "artifact_presence", "panel_basis_mode"):
            actor.pop(key, None)
    return locked


def _diagnostic_blocking(item):
    """Preserve real engine errors; only explicit warnings are advisory."""
    from genshin_app.calculation_completion import execution_issue_is_blocking
    return execution_issue_is_blocking(item)


def _calculation_issue(code, path, message, *, blocking=True, severity=None):
    row = _issue(code, path, message, "ask_agent" if blocking else "view_details")
    row.update(consumer="calculation_diagnostics." + code, required=blocking, consumed=blocking,
               impact="high" if blocking else "informational", blocking=blocking,
               severity=severity or ("error" if blocking else "info"))
    return row


def _diagnostics(calculated, *, energy_intent=None):
    """Project actual checks in the requested scope; never grant acceptance.

    A fully executed explicit window need not prove an infinite loop. The
    caller supplies the saved workflow intent; unknown/legacy scope stays
    conservative, and an executed prefix or genuine engine issue is retained.
    """
    result = calculated["result"]
    from genshin_app.calculation_completion import (
        calculation_completion,
        execution_issue_is_required,
        has_valid_reporting_scope,
    )
    completion = calculation_completion(calculated)
    valid_scope = has_valid_reporting_scope(result)
    scoped_complete = (valid_scope and energy_intent in {'single_window_legal', 'multi_round_legal'}
                       and completion['task_admission'] == 'accepted')
    issues = []
    check = result.get("rotation_check") or {}
    action, energy = check.get("action_check") or {}, check.get("energy_check") or {}
    mechanics = result.get("mechanics") or {}
    raw_interpretation = check.get("damage_interpretation")
    interpretation = (raw_interpretation if isinstance(raw_interpretation, dict) else
                      {"kind": raw_interpretation} if isinstance(raw_interpretation, str) else {})
    hard_states = {"failed", "pending", "invalid", "rejected", "incomplete"}
    raw_issues = [item for item in [*check.get("issues", []), *result.get("mechanics_issues", [])]
                  if isinstance(item, dict)]
    complete_flags = (interpretation.get("complete_first_cycle"), mechanics.get("complete_first_cycle"))
    complete = any(flag is True for flag in complete_flags) and not any(flag is False for flag in complete_flags)
    explicit_complete = scoped_complete or (
        energy_intent in {"single_window_legal", "multi_round_legal"}
        and action.get("status") == "passed" and energy.get("opening_status") == "passed"
        and complete and interpretation.get("kind") != "executed_prefix"
        and check.get("status") not in hard_states and energy.get("status") not in hard_states
        and result.get("mechanics_status") not in hard_states and mechanics.get("execution_status") not in hard_states
        and not any(_diagnostic_blocking(item) for item in raw_issues)
    )
    if action.get("status") != "passed":
        issues.append(_calculation_issue("calculation_action_check", "result.rotation_check.action_check",
            ("当前必需动作窗口已通过；后继审计的动作问题另列，不影响本窗口。" if scoped_complete else
             "动作与冷却尚未全部通过，请让 Agent 检查诊断并补全；已生成的数值不代表该检查通过。"),
            blocking=not scoped_complete))
    if energy.get("status") != "passed":
        scoped = scoped_complete or explicit_complete and energy.get("status") == "unproven"
        message = ("显式输入窗口的能量检查已通过；本次未声明无限循环，未证明循环收敛不构成当前结果的阻断。"
                   if scoped else "当前能量证明范围尚未通过，请让 Agent 核对所有声明动作的实际收支和执行；已生成的数值不代表能量检查通过。")
        issues.append(_calculation_issue("calculation_energy_check", "result.rotation_check.energy_check", message, blocking=not scoped))
    if result.get("mechanics_status") not in (None, "passed", "ready"):
        scoped = scoped_complete or explicit_complete and result.get("mechanics_status") == "unproven"
        message = ("当前显式输入窗口已完整执行；循环与未声明机制不在本次证明范围，覆盖率、假设和其余局限仍需查看。"
                   if scoped else "机制执行仍有待核实项，请让 Agent 检查机制诊断及是否只执行了轴的前半段。")
        issues.append(_calculation_issue("calculation_mechanics", "result.mechanics_status", message, blocking=not scoped))
    for item in raw_issues:
        optional = valid_scope and not execution_issue_is_required(item, valid_reporting_scope=True)
        message = str(item.get("message") or item.get("code") or "引擎报告了一项未说明详情的诊断，请查看运行记录。")
        row = _calculation_issue(item.get("code") or "calculation_diagnostic", item.get("path", "result"),
            "后继审计：" + message if optional else message,
            blocking=not optional and _diagnostic_blocking(item),
            severity='warning' if optional and _diagnostic_blocking(item) else item.get("severity"))
        if optional:
            row.update(audit_scope='optional', original_severity=item.get('severity'))
        issues.append(row)
    if completion['task_admission'] != 'accepted' and not any(row['blocking'] for row in issues):
        issues.append(_calculation_issue('calculation_incomplete', 'calculation_completion',
            '当前数值或必需执行验证尚未完成，请查看计算诊断。'))
    return issues


def _patch_panel(stats, previous, current, actor_id, issues):
    for key in PANEL_KEYS:
        if previous.get(key) == current.get(key):
            continue
        value = current.get(key)
        if value is None:
            stats.pop(key, None)
            issues.append(_issue("unknown_panel", f"team.{actor_id}.panel.{key}",
                "已清空的面板数值视为未知；请填写数值或让 Agent 补齐，不沿用旧值。", "edit_panel"))
        else:
            stats[key] = value
    bonuses = stats.setdefault("dmg_bonus", {})
    for key in ELEMENTS:
        if previous["dmg_bonus"].get(key) == current["dmg_bonus"].get(key):
            continue
        value = current["dmg_bonus"].get(key)
        if value is None:
            bonuses.pop(key, None)
            issues.append(_issue("unknown_panel", f"team.{actor_id}.panel.dmg_bonus.{key}",
                "已清空的伤害加成视为未知；明确没有加成时请填 0。", "edit_panel"))
        else:
            bonuses[key] = value


def apply_whiteboard_equipment(board, previous_board=None, previous_result_path=None, *, db_path):
    """Apply current build values, preserving only verified original action rules.

    No output is written. A caller must retain the original previous_board/path
    pair, even after successful recalculation, for subsequent cumulative edits.
    """
    validate_whiteboard(board)
    response = {"state": "pending", "board_fingerprint": board_fingerprint(board),
                "readonly": True, "missing_inputs": [], "agent_followup": []}
    if previous_board is None or not previous_result_path:
        response["missing_inputs"] = [_issue("agent_model_required", "actions",
            "当前白板尚无已验收的 Agent 计算回传。装备和面板已可保存，请先交给 Agent 补齐动作与效果规则。")]
        return response
    validate_whiteboard(previous_board)
    if _locked_intent(board) != _locked_intent(previous_board):
        response["missing_inputs"] = [_issue("whiteboard_model_changed", "whiteboard",
            "角色身份、等级、命座、动作、轴长或说明已变化，原计算模型不能直接复用；请将当前白板交给 Agent 重新核实。")]
        return response
    # Validation reads the complete package and verifies its actual native input,
    # not just the declared board digest or a result supplied by the browser.
    try:
        previous = read_whiteboard_result(previous_board, previous_result_path)
    except (PlanError, OSError, ValueError) as exc:
        response["missing_inputs"] = [_issue("whiteboard_baseline_unavailable", "previous_result_path",
            "原计算回传无法重新验证，请重新载入有效回传或交给 Agent 处理：" + str(exc))]
        return response
    response["source_result"] = {"path": previous["path"], "file_sha": previous["file_sha"],
                                 "board_fingerprint": board_fingerprint(previous_board)}
    plan = copy.deepcopy(previous["calculated"]["plan"])
    characters = {row["id"]: row for row in plan["manual_template"]["characters"]}
    original_actors = {row["id"]: row for row in previous_board["team"]}
    issues = []
    for actor in board["team"]:
        cid, old = actor["id"], original_actors[actor["id"]]
        stats, equipment = characters[cid]["stats"], plan["equipment_inputs"][cid]
        _patch_panel(stats, old["panel"], actor["panel"], cid, issues)
        if "panel_basis_mode" in actor:
            equipment["panel_basis_mode"] = actor["panel_basis_mode"]
        else:
            equipment.pop("panel_basis_mode", None)
        if old["weapon"] != actor["weapon"]:
            selected = actor["weapon"]
            if selected is None:
                equipment["weapon"] = None
            elif (selected.get("entity_ref") or {}).get("kind") == "provisional":
                equipment["weapon"] = {
                    "weapon_id": None,
                    "entity_ref": copy.deepcopy(selected["entity_ref"]),
                    "level": selected["level"],
                    "ascension": selected["ascension"],
                    "selected_by": "user", "refinements": [],
                    **({"refinement": selected["refinement"]}
                       if selected["refinement"] is not None else {}),
                }
                issues.append(_issue(
                    "provisional_entity_agent_required", f"team.{cid}.weapon",
                    "临时武器已保留，但只能在当前原生Agent方案重新完成数据库核对、来源授权和计算。",
                ))
            else:
                from genshin_db.queries import get_weapon_detail
                try:
                    detail = get_weapon_detail(db_path, selected["weapon_id"], version_line=board["version_line"])
                except (OSError, ValueError, sqlite3.Error, NotFoundError) as exc:
                    response["missing_inputs"] = [_issue("equipment_unavailable", f"team.{cid}.weapon",
                        "所选武器资料不可用，请检查公共数据库后重新应用：" + str(exc), "choose_equipment")]
                    return response
                equipment["weapon"] = {"weapon_id": selected["weapon_id"],
                    **({"entity_ref": copy.deepcopy(selected["entity_ref"])}
                       if selected.get("entity_ref") is not None else {}),
                    "level": selected["level"],
                    "ascension": selected["ascension"], "selected_by": "user",
                    **({"refinement": selected["refinement"]} if selected["refinement"] is not None else {}),
                    "refinements": [{"source_index": item["source_index"], "affix_group_id": item["affix_group_id"],
                                     "refinement": selected["refinement"]} for item in detail["effects"]]}
        if old["artifact_sets"] != actor["artifact_sets"]:
            equipment["artifact_sets"] = [{
                "set_id": row["set_id"], "count": row["count"],
                **({"entity_ref": copy.deepcopy(row["entity_ref"])}
                   if row.get("entity_ref") is not None else {}),
            } for row in actor["artifact_sets"]]
            if any((row.get("entity_ref") or {}).get("kind") == "provisional"
                   for row in actor["artifact_sets"]):
                issues.append(_issue(
                    "provisional_entity_agent_required", f"team.{cid}.artifact_sets",
                    "临时套装已保留，但只能在当前原生Agent方案重新完成数据库核对、来源授权和效果材料化。",
                ))
        if actor.get("artifact_presence") != old.get("artifact_presence"):
            if "artifact_presence" in actor:
                equipment["artifact_presence"] = copy.deepcopy(actor["artifact_presence"])
            else:
                equipment.pop("artifact_presence", None)
        if any(actor.get(key) != old.get(key) for key in ("panel", "weapon", "artifact_sets", "artifact_presence")):
            equipment["source_refs"].append({"kind": "user_whiteboard_equipment", "board_id": board["board_id"],
                "board_fingerprint": response["board_fingerprint"],
                "note": "用户重新应用固定初始面板，已包含当前武器与套装常驻属性；未自动加算成长或词条。"})
        if actor["panel"].get("energy_recharge") != old["panel"].get("energy_recharge"):
            for character in plan["rotation_model"]["characters"]:
                if character["id"] == cid:
                    character["energy_recharge"] = actor["panel"].get("energy_recharge")
    plan["manual_template"]["meta"]["name"] = board["title"]
    handoff = plan["extensions"]["whiteboard_handoff"]
    handoff.update(board=copy.deepcopy(board), board_fingerprint=response["board_fingerprint"])
    plan["revision"] += 1
    plan.update(resolution={"state": "pending"}, resolved_template=None)
    validate_result_intent(board, plan)
    if issues:
        # A cleared value is unknown, not the engine's optional-stat default.
        # Keep the whiteboard as the editable handoff rather than offering an
        # executable native candidate that could silently treat it as zero.
        response["missing_inputs"] = issues
        return response
    plan = prepare_equipment_edit(plan, previous["calculated"]["plan"])
    report = compose_equipment_plan(plan, db_path)
    response.update(plan=report["plan"], report=report)
    response["missing_inputs"] = [*issues, *report["missing_inputs"]]
    if response["missing_inputs"]:
        return response
    try:
        calculated = run_plan_calc(plan, db_path, capture_display=True)
        validate_whiteboard_capacity_result(board, calculated)
    except (PlanError, ValueError) as exc:
        details = exc.details if isinstance(getattr(exc, "details", None), dict) else {}
        reason = getattr(exc, "reason", "whiteboard_capacity_result_mismatch")
        response["missing_inputs"] = details.get("missing_inputs") or [_issue(reason, "calculation", str(exc))]
        return response
    response.update(state="ready", plan=calculated["plan"], report=calculated["report"], calculated=calculated,
                    agent_followup=_diagnostics(calculated))
    return response
