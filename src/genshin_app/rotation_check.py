"""Application boundary for explicit action/energy checks; no inferred game actions."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def resolve_rotation_model(template, model):
    """Native plans bind ER to the composed panel, including manually managed actors.

    Initial energy, costs, particle rules and their evidence remain explicit inputs.
    Missing panel ER stays unknown; it is never silently replaced by 100%.
    """
    if model is None:
        return None, []
    resolved = copy.deepcopy(model)
    chars = {c["id"]: c for c in template["characters"]}
    bindings = []
    for actor in resolved.get("characters", []):
        cid = actor.get("id")
        if cid not in chars:
            continue
        value = chars[cid]["stats"].get("energy_recharge")
        bindings.append({"character": cid, "field": "energy_recharge", "value": value,
                         "declared_value": actor.get("energy_recharge"),
                         "source": "resolved_template.characters.stats.energy_recharge"})
        actor["energy_recharge"] = value
    return resolved, bindings


def describe_rotation_check(check, template, model, bindings, *, mechanics_model=None, matches=None):
    """Give both executors the same app-facing scope and input identity."""
    inputs = {"template": template, "rotation_model": model}
    if mechanics_model is not None:
        inputs.update(mechanics_model=mechanics_model, matches=matches or [])
    check["input_fingerprint"] = fingerprint(inputs)
    check["panel_bindings"] = bindings
    check["resolved_rotation_model"] = model
    check["damage_interpretation"] = {
        "kind": "planned_damage", "executable_dps": None,
        "message": "伤害为计划输入的纸面结果；动作与能量检查单独列示，未模拟元素底、体力和实际命中。",
    }
    return check


def evaluate_rotation(template, model=None, *, native=False):
    from genshin_engine.rotation import check_rotation

    resolved, bindings = resolve_rotation_model(template, model) if native else (copy.deepcopy(model), [])
    check = check_rotation(template, resolved)
    # Check success covers only the implemented constraints, never game hit/aura accuracy.
    return describe_rotation_check(check, template, resolved, bindings)


def attach_rotation_check(result, template, model=None, *, native=False):
    result = copy.deepcopy(result)
    result["rotation_check"] = evaluate_rotation(template, model, native=native)
    return result


def _preflight_result(check):
    # The standalone checker sees declared resources, while the full
    # calculation can also generate or prevent those events during execution.
    check["preflight"] = {
        "scope": "declared_rotation_only",
        "declared_status": check["status"],
        "mechanics_executed": False,
        "closure_verified": False,
        "message": "只检查已声明的动作、冷却和能量。机制产生或取消的资源未核验，首个问题供定位；正式结果须完整计算。",
    }
    check["status"] = "unproven"
    return check


def check_document(document, db_path=None, *, preflight=False):
    """Read a versioned standalone check input or recompose a native plan."""
    from genshin_engine import validate_template

    if not isinstance(document, dict):
        from genshin_app.equipment.schema import PlanError
        raise PlanError("检查文件必须是 JSON 对象", "invalid_rotation_check", {"path": "/", "expected_type": "object"})
    if document.get("kind") == "genshin-plan":
        from genshin_app.equipment import compose_equipment_plan
        from genshin_app.equipment.service import require_ready
        if db_path is None:
            from genshin_app.equipment.schema import PlanError
            raise PlanError("检查原生装备方案需要指定公共数据库", "database_missing", {"path": "/data_dir"})
        if document.get("plan_version") == 3 and not preflight:
            from genshin_app.equipment.service import run_plan_calc
            return run_plan_calc(document, Path(db_path))["result"]["rotation_check"]
        report = compose_equipment_plan(document, Path(db_path))
        require_ready(report)
        plan = report["plan"]
        check = evaluate_rotation(
            plan["resolved_template"],
            report.get("compiled_rotation_model", plan.get("rotation_model")), native=True,
        )
        return _preflight_result(check) if preflight else check
    if document.get("kind") == "genshin-rotation-check":
        if (type(document.get("version")) is not int or document["version"] != 1
                or set(document) - {"kind", "version", "template", "rotation_model"}
                or not {"template", "rotation_model"} <= set(document)):
            from genshin_app.equipment.schema import PlanError
            raise PlanError("不支持的动作检查文件结构；需要 version=1、template 和 rotation_model", "invalid_rotation_check")
        validate_template(document["template"])
        check = evaluate_rotation(document["template"], document["rotation_model"])
        return _preflight_result(check) if preflight else check
    if document.get("form") == "resolved":
        validate_template(document)
        check = evaluate_rotation(document)
        return _preflight_result(check) if preflight else check
    from genshin_app.equipment.schema import PlanError
    raise PlanError("需要原生方案、独立动作检查文件或数值模板", "invalid_rotation_check")


def read_check_document(path):
    from genshin_app.equipment.files import read_input_bytes
    from genshin_app.equipment.schema import PlanError
    maximum = 8 * 1024 * 1024
    raw = read_input_bytes(path, maximum_bytes=maximum)
    if len(raw) > maximum:
        raise PlanError("检查文件超过 8 MiB", "input_file_too_large", {"path": "/input", "maximum_bytes": maximum})
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("动作检查需要有效的UTF-8 JSON", "input_json_invalid", {"path": "/input"}) from exc


def check_sheets(check, display=None):
    """Auditable workbook tables shared by CLI and native-plan exports."""
    from genshin_engine.rotation_schema import ACTION_TYPES

    names = (display or {}).get("characters", {})
    status_labels = {"passed": "通过", "pass": "通过", "failed": "失败", "fail": "失败",
                     "pending": "待补充", "invalid": "输入无效", "not_checked": "未检查",
                     "unproven": "尚未证明", "valid": "结构有效", "not_verified": "后续未验证",
                     "error": "错误", "warning": "提示"}
    event_names = {"action": "施放动作", "energy_spawn": "生成元素微粒／晶球",
                   "energy_credit": "元素能量到账", "energy_direct": "直接回能",
                   "energy_spend": "消耗元素能量", "cooldown_change": "改变技能冷却",
                   "mechanics_snapshot_capture": "面板取样", "mechanics_hit": "机制命中处理",
                   "mechanics_end": "动作结束处理"}
    status = lambda value: status_labels.get(value, value or "未检查")
    summary = [["检查项目", "结论"], ["总状态", status(check.get("status"))],
               ["输入指纹", check.get("input_fingerprint", "")],
               ["伤害解释", check.get("damage_interpretation", {}).get("message", "")]]
    for key, title in (("action_check", "动作与冷却"), ("energy_check", "能量循环")):
        value = check.get(key, {})
        summary.append([title, status(value.get("status") if isinstance(value, dict) else value)])
    particle_quantity = check.get("energy_check", {}).get("particle_quantity")
    if particle_quantity:
        summary.append(["产球口径", particle_quantity["message"]])
    summary.extend([["尚未检查", json.dumps(value, ensure_ascii=False)]
                    for value in check.get("unchecked_constraints", [])])
    issues = [["严重程度", "原因", "描述", "完整定位"]]
    for value in check.get("issues", []):
        issues.append([status(value.get("status", value.get("severity"))), value.get("code", ""),
                       value.get("message", ""), json.dumps(value, ensure_ascii=False)])
    actions = [["角色", "动作种类", "动作变体", "连携组", "连携段", "连携总段数", "续段窗口（秒）",
                "起点（秒）", "占用结束（秒）", "同刻顺序", "来源", "动作标识", "技能定义标识"]]
    model = check.get("resolved_rotation_model") or {}
    abilities = {a["id"]: a for a in model.get("abilities", [])}
    for action in model.get("actions", []):
        owner = abilities.get(action.get("ability_id"), {}).get("character", "")
        ability = abilities.get(action.get("ability_id"), {})
        sequence = ability.get("cast_sequence") or {}
        actions.append([names.get(owner, owner), ACTION_TYPES.get(ability.get("action_type_id"), "动作种类待补"),
                        ability.get("variant", ""), sequence.get("group", ""), sequence.get("stage", ""),
                        sequence.get("max_stages", ""), sequence.get("continuation_window", ""),
                        action.get("start"), action.get("occupation_end"), action.get("order"),
                        action.get("source", ""), action.get("id"), action.get("ability_id")])
    events = [["轮次", "时刻（秒）", "事件种类", "检查状态", "角色", "前后台", "入账前能量", "原始回能",
               "有效回能", "溢出", "要求能量", "扣除", "缺口", "剩余能量", "来源", "事件标识", "完整检查记录"]]
    if particle_quantity:
        events[0].extend(["粒球数量", "丢失粒球数量", "剩余在途数量", "产球口径"])
    for row in check.get("event_log", []):
        for credit in row.get("energy") or [row]:
            cid = credit.get("character", row.get("character", ""))
            events.append([row.get("cycle", 0) + 1, row.get("time", row.get("t")),
                           ("系统扣能（动作提交瞬间）" if row.get("delivery") == "automatic_action_start"
                            else event_names.get(row.get("type", row.get("kind")), "待补充")), status(row.get("status")),
                           names.get(cid, cid), ("前台" if credit["on_field"] else "后台") if "on_field" in credit else "",
                           credit.get("before"), credit.get("raw"), credit.get("gain"), credit.get("overflow"),
                           row.get("required"), row.get("amount"), row.get("deficit"), credit.get("after"), row.get("source"),
                           row.get("id", row.get("event_id", "")), json.dumps(row, ensure_ascii=False)])
            if particle_quantity:
                events[-1].extend([row.get("count"), row.get("lost_count"), row.get("remaining_count"),
                                  "平均产球" if row.get("count_mode") == "average" else ""])
    cycles = [["轮次", "检查状态", "角色", "轮初能量", "轮末能量", "边界状态与证明"]]
    for index, row in enumerate(check.get("cycles", [])):
        for cid, energy in row.get("end_energy", {}).items():
            cycles.append([row.get("cycle", index) + 1, status(row.get("status")), names.get(cid, cid),
                           row.get("start_energy", {}).get(cid), energy, json.dumps(row["boundary_state"], ensure_ascii=False)])
    return [("动作能量结论", summary), ("动作清单", actions), ("能量流水", events),
            ("循环边界", cycles), ("动作能量问题", issues)]
