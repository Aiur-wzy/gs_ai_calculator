"""Read-only human views for reviewed action gates and runtime admissions."""
from __future__ import annotations

import json

NOTICE = (
    "动作来源与运行时接纳是本次 App 计算的只读历史。修改角色、技能、动作、场景、"
    "初始冷却或数据库模型后，须回 App 重新预览、确认并计算；Excel 不授予执行权。"
)
STATUS = {
    "ready": "已确认",
    "pending": "待补充",
    "committed": "已接纳并提交",
    "rejected": "游戏门禁拒绝",
    "not_committed": "门禁已过但未提交",
    "not_verified": "后续未验证",
}
REASONS = {
    "not_frontline": "施放角色不在前台",
    "action_overlap": "动作占用冲突或尚不能切出",
    "insufficient_energy": "动作开始时能量不足",
    "cooldown_unavailable": "技能冷却或次数尚未恢复",
    "action_admission_receipt_limit": "动作接纳审计达到上限，本动作未提交",
    "automatic_energy_receipt_limit": "自动扣能审计达到上限，本动作未提交",
}


def encoded(value):
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_text(record):
    model = record.get("model_ref") or {}
    return f"{model.get('id', '待核实')} / 修订 {model.get('revision', '待核实')}"


def _issues(record):
    return "；".join(item.get("message", str(item)) for item in record.get("issues", []))


def action_admission_sheets(result, display=None):
    """Return source and execution sheets without reconstructing game state."""
    records = result.get("action_admission_compilations") or []
    check = result.get("rotation_check") or {}
    audit = check.get("action_admission_execution")
    if not records and audit is None:
        return []
    names = (display or {}).get("characters", {})
    sources = [
        ["记录性质", NOTICE],
        [
            "绑定", "状态", "角色", "能力", "数据库技能", "动作类型/变体", "模型来源",
            "场景", "开场恢复队列（秒）", "基础冷却/次数", "固定耗能", "字段权威",
            "尚未验证", "待处理项", "来源指纹", "静态证书指纹",
        ],
    ]
    for record in records:
        selection = record.get("selection") or {}
        certificate = record.get("static_certificate") or {}
        values = record.get("compiled_values") or certificate.get("compiled_values") or {}
        cooldown, energy = values.get("cooldown") or {}, values.get("energy") or {}
        initial = certificate.get("initial_cooldown")
        queue = initial.get("ready_in") if isinstance(initial, dict) else None
        actor = record.get("actor")
        sources.append([
            record.get("id"), STATUS.get(record.get("status"), "待核实"),
            names.get(actor, actor or "待核实"), record.get("ability_id"),
            "/".join(str(selection.get(key, "?")) for key in ("character_id", "skill_depot_id", "skill_id")),
            f"{record.get('action_type_id', '待核实')} / {record.get('variant', '待核实')}",
            _model_text(record), encoded(record.get("context")),
            encoded(queue) if queue is not None else "未设置",
            (f"{cooldown.get('duration')} 秒 / {cooldown.get('charges')} 次 / {cooldown.get('group')}"
             if cooldown else "待补充"),
            (f"门槛 {energy.get('required')} / 扣除 {energy.get('amount')} / {energy.get('delivery')}"
             if energy else "待补充"),
            encoded(certificate.get("field_authority") or (record.get("profile") or {}).get("field_authority")),
            encoded(certificate.get("declared_not_verified")
                    or (record.get("profile") or {}).get("declared_not_verified")),
            _issues(record), record.get("source_fingerprint"),
            certificate.get("static_certificate_fingerprint"),
        ])

    execution = [
        ["记录性质", NOTICE],
        [
            "时刻（秒）", "角色", "动作", "能力", "状态", "原因", "能量（前→后）",
            "次数（前→后）", "下次可用（秒）", "门禁", "绑定", "模型", "来源指纹",
            "静态证书指纹", "接纳证书", "执行标识",
        ],
    ]
    committed_runs = set()
    for row in (audit or {}).get("receipts", []):
        committed_runs.add(row.get("parent_run_id"))
        model = row.get("model_ref") or {}
        execution.append([
            row.get("t"), names.get(row.get("actor"), row.get("actor")), row.get("action_id"),
            row.get("ability_id"), STATUS.get(row.get("status"), "待核实"), "",
            f"{row.get('energy_before')} → {row.get('energy_after')}",
            f"{row.get('charges_before')} → {row.get('charges_after')}", row.get("next_ready_at"),
            encoded(row.get("gates")), row.get("binding_id"),
            f"{model.get('id', '待核实')} / 修订 {model.get('revision', '待核实')}",
            row.get("source_fingerprint"), row.get("static_certificate_fingerprint"),
            row.get("id"), row.get("run_id"),
        ])
    for row in check.get("event_log", []):
        admission = row.get("action_admission") or {}
        if not admission or admission.get("status") == "committed" or row.get("run_id") in committed_runs:
            continue
        execution.append([
            row.get("t"), names.get(row.get("character"), row.get("character")),
            row.get("action_id", row.get("id")), row.get("ability_id"),
            STATUS.get(admission.get("status"), "待核实"),
            REASONS.get(admission.get("reason"), admission.get("reason") or "请查看动作检查原因"),
            "", "", row.get("next_ready_at"), "未全部通过", admission.get("binding_id"), "",
            admission.get("source_fingerprint"), admission.get("static_certificate_fingerprint"), "", row.get("run_id"),
        ])
    if audit:
        execution.extend([
            ["配置窗口计划动作", audit.get("planned_actions")],
            ["已报告动作", audit.get("reported_actions")],
            ["未报告动作", audit.get("unreported_actions")],
            ["已接纳动作", audit.get("committed_actions")],
            ["游戏门禁拒绝", audit.get("rejected_actions")],
            ["门禁已过但未提交", audit.get("not_committed_actions")],
            ["后续未验证", audit.get("not_verified_actions")],
            ["接纳回执", audit.get("receipt_count")],
            ["账本终止原因", encoded(audit.get("termination"))],
        ])
    return [("静态动作来源", sources), ("运行时动作接纳", execution)]


__all__ = ["NOTICE", "action_admission_sheets"]
