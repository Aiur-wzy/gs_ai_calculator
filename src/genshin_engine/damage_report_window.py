"""Versioned damage reporting boundaries, independent of execution periods."""
from __future__ import annotations

import copy
from decimal import Decimal

CONTRACT = "damage-report-window-v1"
_IDS = {"type": "array", "maxItems": 40000, "uniqueItems": True,
        "items": {"type": "string", "minLength": 1}}
REPORT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["contract", "mode"], "properties": {
        "contract": {"const": CONTRACT}, "mode": {"enum": ["explicit", "last_active_damage"]},
        "end": {"type": "number", "exclusiveMinimum": 0},
        "active_hit_ids": copy.deepcopy(_IDS), "active_operation_ids": copy.deepcopy(_IDS),
        "non_damage_action_ids": copy.deepcopy(_IDS), "audit_action_ids": copy.deepcopy(_IDS)}}


def tick(value):
    n = Decimal(str(value)) * 1000000
    if not n.is_finite() or n != n.to_integral_value():
        raise ValueError("统计截止须为有限微秒时间。")
    return int(n)


def report_contract():
    return {"contract": CONTRACT, "schema": copy.deepcopy(REPORT_SCHEMA),
        "field": "rotation_model.damage_report", "default_new_whiteboard": "last_active_damage",
        "explicit": "用户给定截止优先；统计0≤t≤T，分母T，不改变动作period或来源origin。",
        "automatic": "Agent将施放阶段直接命中标为active_hit_ids／active_operation_ids；最后非审计、非等待／切人动作的末次直接结算截止。后台不能标作主动命中。",
        "missing_anchor": "保留草稿及候选；请求计算时报告缺少当前动作的直接结算绑定，不能等待全部Buff。",
        "audit": "完整origin0动作／能量验证仍是必需；后续内部检查不重复计伤。旧无此子合同输入保留显式period口径。"}


def validate_report(config, *, period=None):
    issues = []
    def error(code, message, **details):
        issues.append({"code": code, "message": message, "path": "rotation_model.damage_report", "severity": "error", **details})
    if config["mode"] == "explicit":
        if "end" not in config:
            error("damage_report_end_missing", "用户指定截止模式需要end。")
        else:
            try:
                end = tick(config["end"])
                if end <= 0 or period is not None and end > tick(period):
                    error("damage_report_bounds", "截止须大于0且位于已声明执行窗口内；延长执行窗口不能改变来源轮次。")
            except (ValueError, ArithmeticError):
                error("damage_report_time", "截止时间最多保留六位小数。")
    elif "end" in config:
        error("damage_report_unused_end", "自动截止不能同时填入固定end。")
    return issues


def _operation_id(hit):
    request = hit.get("extensions", {}).get("special_reaction", {}).get("request_id", "")
    return request.removeprefix("0:")


def resolve_report(runtime, rotation, simulation):
    config = rotation.get("damage_report")
    if config is None:
        return None
    actions = {a["id"]: a for a in rotation["actions"]}
    abilities = {a["id"]: a for a in rotation["abilities"]}
    audit = set(config.get("audit_action_ids", []))
    non_damage = set(config.get("non_damage_action_ids", []))
    errors = []
    def pending(code, message, **details):
        issue = {"code": code, "message": message, "path": "rotation_model.damage_report", "severity": "pending", **details}
        runtime.issues.append(issue)
        errors.append(issue)
    if (audit | non_damage) - actions.keys():
        pending("damage_report_action_reference", "统计或审计引用了不存在的动作。")
    log = simulation.get("event_log", [])
    first = [row for row in log if row.get("cycle") == 0]
    passed = {row.get("id") for row in first if row.get("type") == "action" and row.get("status") == "passed"}
    required = set(actions) - audit
    complete_input = simulation.get("energy_check", {}).get("completed_cycles", 0) >= 1
    required_issues = [row for row in runtime.issues
        if row.get("severity", "error") not in ("warning", "info")
        and row.get("cycle", row.get("origin", 0)) == 0]
    required_complete = complete_input and required <= passed and not required_issues
    required_status = "complete" if required_complete else "failed" if simulation.get("status") in ("failed", "invalid") else "pending"
    anchor, end = None, config.get("end") if config["mode"] == "explicit" else None
    if config["mode"] == "last_active_damage":
        candidates = [a for a in actions.values() if a["id"] not in audit | non_damage
                      and abilities[a["ability_id"]]["action_type_id"] not in ("action.wait", "action.switch")]
        last = max(candidates, key=lambda a: (tick(a["start"]), a["order"]), default=None)
        if last is None:
            pending("damage_report_anchor_missing", "没有可确定截止的主动输出动作；请提供用户截止或有来源的主动结算绑定。")
        elif last["id"] not in passed:
            pending("damage_report_last_action_failed", "最后一个用户动作尚未合法执行，不能退回上一成功动作缩短窗口。", action_id=last["id"])
        else:
            declared = {h["hit_id"]: h["action_id"] for h in runtime.model.get("hit_events", [])}
            for pattern in runtime.model.get("hit_patterns", []):
                declared.update({f"{pattern['id']}:{index + 1}": pattern["action_id"]
                                 for index in range(len(pattern["offsets"]))})
            selected_hits = set(config.get("active_hit_ids", []))
            operations = {o["id"]: o["action_id"] for o in runtime.model.get("special_reaction_events", [])}
            selected_ops = set(config.get("active_operation_ids", []))
            if selected_hits - declared.keys() or selected_ops - operations.keys():
                pending("damage_report_component_reference", "主动伤害绑定需要真实hit或特殊操作身份。")
            hits = [h for h in runtime.hit_rows if
                    h["id"] in selected_hits and declared.get(h["id"]) == last["id"]
                    or _operation_id(h) in selected_ops and operations.get(_operation_id(h)) == last["id"]]
            if not hits:
                pending("damage_report_anchor_missing", "最后主动动作缺少实际直接结算；请绑定施放阶段命中，不要用后台尾伤。",
                        action_id=last["id"], available_hit_ids=[key for key, value in declared.items() if value == last["id"]],
                        available_operation_ids=[key for key, value in operations.items() if value == last["id"]])
            else:
                last_hit = max(hits, key=lambda h: tick(h["t"]))
                end, anchor = last_hit["t"], {"action_id": last["id"], "hit_id": last_hit["id"], "t": last_hit["t"]}
    if end is not None and tick(end) <= 0:
        pending("damage_report_zero_duration", "已找到结算但截止时长非正，无法定义DPS。")
    if not required_complete:
        # Required task validity is independent of finding a numerical cutoff.
        pending("damage_report_required_validation", "用户要求的完整动作／能量窗口尚未验证；短统计窗不能代替任务完成。")
    audit_status = ("not_requested" if simulation.get("energy_check", {}).get("checked_cycles", 0) <= 1
                    else "failed" if simulation.get("status") in ("failed", "invalid", "pending") else "complete")
    return {"contract": CONTRACT, "mode": config["mode"], "start": 0, "end": end,
        "boundary": "inclusive_time", "status": "pending" if errors else "complete", "anchor": anchor,
        "execution_period": rotation["period"], "required_validation": {"status": required_status,
            "action_ids": sorted(required), "passed_action_ids": sorted(required & passed),
            "issue_codes": [row.get("code") for row in required_issues]},
        "optional_audit": {"status": audit_status}, "source": "user_explicit" if config["mode"] == "explicit" else "last_active_direct_damage"}


def filter_report_template(template, report):
    if report is None or report["end"] is None or tick(report["end"]) <= 0:
        return template
    end = tick(report["end"])
    filtered = copy.deepcopy(template)
    filtered["hits"] = [h for h in filtered["hits"] if 0 <= tick(h["t"]) <= end]
    ids = {h["id"] for h in filtered["hits"]}
    buffs = []
    for buff in filtered.get("buffs", []):
        effects = []
        for effect in buff["effects"]:
            applies = effect.get("applies_to", {})
            if applies.get("mode") == "hits":
                applies["hit_ids"] = [hid for hid in applies["hit_ids"] if hid in ids]
                if not applies["hit_ids"]:
                    continue
            effects.append(effect)
        if effects:
            buff["effects"] = effects
            buffs.append(buff)
    filtered["buffs"], filtered["loop_duration"] = buffs, report["end"]
    return filtered
