"""Read-only human views of capacity provenance and actual core admissions."""
from __future__ import annotations

import json

NOTICE = "次数及消费记录是本次 App 计算的只读历史。修改装备、命座、天赋、动作、冷却或初始队列后，须回 App 重新计算；Excel 不重演资源链。"
STATUS = {"ready": "已确认", "pending": "待补充", "committed": "已消费", "rejected": "次数不足，拒绝施放",
          "not_checked": "此前门槛未通过，未检查次数", "not_committed": "未提交", "not_verified": "后续未验证"}
REASONS = {"cooldown_unavailable": "技能冷却或可用次数尚未恢复", "not_frontline": "施放角色不在前台",
           "action_overlap": "动作占用冲突", "insufficient_energy": "动作开始时能量不足",
           "action_capacity_receipt_limit": "容量审计达到上限，本动作未提交",
           "automatic_energy_receipt_limit": "扣能审计达到上限，本动作未提交"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def capacity_sheets(result, display=None):
    records = result.get("action_capacity_compilations") or []
    check = result.get("rotation_check") or {}
    audit = check.get("action_capacity_consumption")
    if not records and audit is None:
        return []
    names = (display or {}).get("characters", {})
    source_rows = [["记录性质", NOTICE], ["绑定", "状态", "角色", "最大次数", "基础次数", "激活增量", "模型来源", "实际选择与适用条件", "开场恢复队列（秒）", "开场可用次数", "本方案冷却配置（非容量模型证明）", "待处理项", "来源指纹"]]
    for record in records:
        projection = record.get("projection") or {}
        profile = projection.get("profile") or {}
        model = profile.get("model_record") or {}
        claim = model.get("claim") or model.get("payload", {}).get("claim") or {}
        pool = projection.get("pool") or {}
        queue = pool.get("initial", {}).get("ready_in")
        capacity, base = projection.get("capacity"), claim.get("base", {}).get("value")
        cooldown = next((item.get("cooldown") for item in pool.get("members", []) if item.get("cooldown")), {})
        mode = {"sequential": "依次恢复", "parallel": "各次独立恢复"}.get(cooldown.get("recovery"), "恢复方式待补")
        start = {"action_start": "动作开始", "occupation_end": "占用结束"}.get(cooldown.get("start"), "起点待补")
        source_rows.append([record.get("id"), STATUS.get(record.get("status"), "待核实"), names.get(pool.get("actor"), pool.get("actor", "待核实")),
            capacity, base, capacity - base if type(capacity) is int and type(base) is int else "待核实",
            f"{model.get('id', '待核实')} / 修订 {model.get('revision', '待核实')}", encoded(projection.get("actual", {})),
            encoded(queue) if queue is not None else "未知，必须显式填写", capacity - len(queue) if type(capacity) is int and isinstance(queue, list) else "未知",
            f"{cooldown.get('duration', '待补')} 秒；{start}计时；{mode}；组 {pool.get('group', '待补')}",
            "；".join(issue.get("message", str(issue)) for issue in record.get("issues", [])), record.get("source_fingerprint")])
    consumption = [["记录性质", NOTICE], ["时刻（秒）", "角色", "动作标识", "能力标识", "执行状态", "最大次数", "消费前", "消费后", "本次恢复时刻（秒）", "下次可用时刻（秒）", "消费前队列", "消费后队列", "绑定来源", "拒绝或未检查原因", "执行标识"]]
    for row in (audit or {}).get("receipts", []):
        consumption.append([row.get("t"), names.get(row.get("character"), row.get("character")), row.get("action_id"), row.get("ability_id"),
            STATUS.get(row.get("status"), "待核实"), row.get("capacity"), row.get("before"), row.get("after"), row.get("ready_at"), row.get("next_ready_at"),
            encoded(row.get("queue_before")), encoded(row.get("queue_after")), row.get("binding_id"), REASONS.get(row.get("reason"), row.get("reason") or ""), row.get("run_id")])
    for row in check.get("event_log", []):
        admission = row.get("capacity_admission") or {}
        if not admission or admission.get("status") in ("committed", "rejected"):
            continue
        consumption.append([row.get("t", row.get("time")), names.get(row.get("character"), row.get("character")), row.get("action_id", row.get("id")), row.get("ability_id"),
            STATUS.get(admission.get("status"), "待核实"), None, None, None, None, None, "未检查", "未提交", admission.get("binding_id"),
            REASONS.get(admission.get("reason"), admission.get("reason") or "请查看动作检查原因"), row.get("run_id")])
    if audit:
        consumption.extend([["已报告动作", audit.get("planned_actions")], ["配置窗口计划动作", audit.get("max_cycles_planned_actions")],
                            ["未报告动作", audit.get("unreported_actions")], ["已消费动作", audit.get("committed_actions")],
                            ["被拒动作", audit.get("rejected_actions")], ["次数审计回执", audit.get("receipt_count")],
                            ["账本终止原因", encoded(audit.get("termination"))]])
    return [("技能次数来源", source_rows), ("技能次数消费", consumption)]
