"""计算结果导出（app.md R2 / G3）：JSON 原样、CSV 逐 hit 明细、xlsx 三 sheet。

xlsx 用 stdlib zipfile 手写最小 OOXML（inlineStr 免 sharedStrings），保持零运行时依赖。
行结构由 *_rows 辅助函数统一产出，三格式共享同一明细口径。
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
import unicodedata
import zipfile
from pathlib import Path
from typing import BinaryIO
from xml.sax.saxutils import escape

from genshin_app.calculation_integrity import validate_result_binding
from genshin_app.openxml_text import spreadsheet_text
from genshin_app.presentation import label
from genshin_app.verification_scope import verification_scope

EXPORT_FORMATS = ("json", "xlsx", "csv")
_FREEZE_RULESET = "gauge-freeze-reference-v1"
PROVISIONAL_RESULT_FIDELITY = "approximate_with_provisional_entities"
PROVISIONAL_RESULT_LABEL = "临时公共资料·近似"
FORMAL_AUDIT_CONTRACT = "formal-calculation-export-audit-v1"


@verification_scope
def formal_calculation_audit_payload(
    result: dict, receipt: dict | None = None, *, _session=None,
) -> dict | None:
    """Validate and freeze the formal calculation proof chain for export.

    Legacy ``calculate()`` snapshots do not carry either proof family and stay
    exportable as before. Once a formal proof signal is present, the matching
    result receipt is part of the result and must not be silently dropped.
    """

    validate_result_binding(result, receipt, _session=_session)
    from genshin_app.equipment.attribute_receipt import (
        validate_attribute_result_receipt,
    )
    validate_attribute_result_receipt(result, receipt or {})
    return _formal_audit_from_verified_result(result, receipt)


def _formal_audit_from_verified_result(result, receipt):
    """Project an audit from a result already validated by the owning Host."""
    from genshin_app.equipment.agent_execution_receipt import (
        DOCUMENT_FIELD,
        EFFECTIVE_INPUT_FIELD,
        MATERIALIZATION_FIELD,
        PROOF_FIELD,
        RUNTIME_RECEIPT_FIELD,
        validate_agent_execution_result_receipt,
    )
    from genshin_app.equipment.calculation_overrides.ownership_receipt import (
        FIELD as OWNERSHIP_FIELD,
    )
    from genshin_app.equipment.calculation_overrides.ownership_receipt import (
        validate_ownership_result_proof,
    )

    value = receipt if isinstance(receipt, dict) else {}
    ownership_signal = result.get(OWNERSHIP_FIELD) is not None or value.get(
        OWNERSHIP_FIELD) is not None
    agent_fields = (
        DOCUMENT_FIELD,
        MATERIALIZATION_FIELD,
        EFFECTIVE_INPUT_FIELD,
        RUNTIME_RECEIPT_FIELD,
    )
    agent_signal = any(result.get(field) is not None for field in agent_fields) or (
        value.get(PROOF_FIELD) is not None
    )
    if not ownership_signal and not agent_signal:
        return None
    if not isinstance(receipt, dict):
        # ``receipt`` remains optional for legacy results, so reaching this
        # branch is not a Python call-shape/type error.  It means the supplied
        # result advertises a formal proof family but omits the matching proof
        # value, which is an invalid export value and must fail before writing.
        raise ValueError(  # noqa: TRY004 - public export value contract
            "正式计算结果必须连同结果回执导出"
        )
    validate_ownership_result_proof(result, receipt)
    validate_agent_execution_result_receipt(result, receipt)
    return {
        "contract": FORMAL_AUDIT_CONTRACT,
        "calculation_ownership_proof": copy.deepcopy(
            result.get(OWNERSHIP_FIELD)
        ),
        "agent_execution": {
            "materialization_document": copy.deepcopy(
                result.get(DOCUMENT_FIELD)
            ),
            "materialization_receipt": copy.deepcopy(
                result.get(MATERIALIZATION_FIELD)
            ),
            "effective_input": copy.deepcopy(result.get(EFFECTIVE_INPUT_FIELD)),
            "runtime_receipt": copy.deepcopy(result.get(RUNTIME_RECEIPT_FIELD)),
            "proof": copy.deepcopy(receipt.get(PROOF_FIELD)),
        },
        "result_receipt": copy.deepcopy(receipt),
    }


def saved_result_receipt_rows(receipt):
    """Preserve a verified result-only binding without inventing full authority."""
    if not isinstance(receipt, dict) or "result_fingerprint" not in receipt:
        return []
    rows = [["字段", "内容"], ["核验范围", "保存的结果指纹与专项证明；未核验缺席的原生输入或当前数据库。"],
            ["完整结果回执 JSON"]]
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    for offset in range(0, max(1, len(encoded)), 30000):
        rows.append([f"原文块 {offset // 30000 + 1}", encoded[offset:offset + 30000]])
    return rows


def formal_calculation_audit_rows(
    result: dict, receipt: dict | None = None,
) -> list[list]:
    """Return a compact header and lossless chunked formal audit JSON."""

    return formal_audit_payload_rows(formal_calculation_audit_payload(result, receipt))


def formal_audit_payload_rows(payload: dict | None) -> list[list]:
    """Render a previously verified audit without replaying the calculation."""
    if payload is None:
        return []
    ownership = payload.get("calculation_ownership_proof")
    agent = payload.get("agent_execution") or {}
    rows: list[list] = [
        ["审计类型", "内容"],
        ["审计合同", FORMAL_AUDIT_CONTRACT],
        ["计算范围所有权", "已验证" if ownership is not None else "未使用"],
        ["Agent强类型执行", "已验证" if agent.get("proof") is not None else "未使用"],
        [],
        ["完整正式审计 JSON"],
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    for offset in range(0, max(1, len(encoded)), 30000):
        rows.append([encoded[offset:offset + 30000]])
    return rows


def provisional_usage_receipt(
    result: dict, receipt: dict | None = None,
) -> dict | None:
    """Return the already-validated D2 usage proof without inferring authority."""

    value = result.get("provisional_entity_usage_receipt")
    if value is None and isinstance(receipt, dict):
        value = receipt.get("provisional_entity_usage_receipt")
    return value if isinstance(value, dict) else None


def provisional_entity_rows(
    result: dict, receipt: dict | None = None,
) -> list[list]:
    """Human-readable result label plus the complete machine-auditable receipt."""

    usage = provisional_usage_receipt(result, receipt)
    if usage is None:
        return []
    rows: list[list] = [
        ["结果性质", PROVISIONAL_RESULT_LABEL],
        ["说明", "本结果使用仅限当前方案的临时公共资料；不是数据库正式事实。"],
        ["结果精度标识", PROVISIONAL_RESULT_FIDELITY],
        ["数据库权威", "否"],
        ["作用范围", "仅当前方案"],
        ["使用项数", len(usage.get("entities") or [])],
        [],
        ["临时资料类型", "临时资料 ID", "计算消费者", "授权依据", "数据库核对回执", "Agent证明", "用户批准"],
    ]
    for entity in usage.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        rows.append([
            entity.get("entity_type", ""), entity.get("provisional_id", ""),
            entity.get("consumer", ""), entity.get("authority", ""),
            entity.get("lookup_receipt_id", ""), entity.get("attestation_id") or "",
            entity.get("approval_id") or "",
        ])
    rows += [[], ["完整使用回执 JSON"]]
    encoded = json.dumps(usage, ensure_ascii=False, sort_keys=True)
    for offset in range(0, max(1, len(encoded)), 30000):
        rows.append([encoded[offset:offset + 30000]])
    return rows


# ---------- 行结构（三格式共用口径） ----------

def summary_rows(result: dict, display: dict | None = None) -> list[list]:
    s = result.get("calculation_totals") or result["summary"]
    rows: list[list] = [
        ["指标", "数值"],
        ["总伤害", s["total_damage"]],
        ["每秒伤害", s["dps"] if s["dps"] is not None else ""],
        [],
        ["按角色", "期望伤害"],
    ]
    if provisional_usage_receipt(result) is not None:
        rows[3:3] = [
            ["结果性质", PROVISIONAL_RESULT_LABEL],
            ["精度标识", PROVISIONAL_RESULT_FIDELITY],
            ["范围说明", "使用临时公共资料，仅限当前方案；完整核对与授权见“临时公共资料”页。"],
            [],
        ]
    rows += [[display.get("characters", {}).get(k, k) if display else k, v]
             for k, v in s["by_character"].items()]
    rows += [[], ["按技能", "期望伤害"]]
    rows += [[label("skills", k) if display else k, v] for k, v in s["by_skill"].items()]
    if result.get("external_damage"):
        rows += [[], ["计算组成", "期望伤害"],
                 ["引擎计算", s["engine_total_damage"]],
                 ["外部补充（固定数值）", s["external_total_damage"]],
                 ["外部条目数", s["external_count"]],
                 ["证据层级", s["evidence_tier"]],
                 ["修改后处理", "外部值不随面板自动重算。修改输入后须回 App / Agent 重新复核并生成。"]]
    if s.get("precision") == "approximate":
        rows += [["结果精度", "近似"],
                 ["近似事件数", s.get("approximate_event_count", 0)],
                 ["近似角色", "、".join(s.get("approximate_by_character", []))],
                 ["近似技能", "、".join(s.get("approximate_by_skill", []))],
                 ["精度说明", "近似标记同时适用于相关事件、小计、总伤害与每秒伤害。"]]
    verification = ((result.get("rotation_check") or {}).get("damage_interpretation") or {}).get(
        "reaction_verification")
    if isinstance(verification, dict) and verification.get("mode") == "declared_not_simulated":
        rows += [[], ["反应验证", "预填声明，未由元素引擎复核"],
                 ["声明反应命中数", verification.get("declared_reaction_hit_count", 0)],
                 ["尚未验证", "命中前元素底、具体攻击附着资格、ICD计数身份与反应顺序"],
                 ["后续处理", verification.get("required_follow_up", "补齐具体攻击组件附着资料后重新计算")]]
    rows += [[], ["口径假设"]]
    rows += [[a] for a in result.get("assumptions", [])]
    return rows


def external_damage_rows(result: dict, display: dict | None = None, *, included_only=False) -> list[list]:
    """Keep externally calculated values separate from engine hit factors."""
    external = result.get("external_damage")
    if not external:
        return []
    from genshin_app.equipment.damage_report_scope import (
        report_includes,
        reporting_window,
    )
    window = reporting_window(result)
    items = [item for item in external["items"] if not included_only or window is None or report_includes(window, item["t"])]
    names = (display or {}).get("characters", {})
    rows = [[], ["外部补充伤害（固定数值）"],
            ["不执行增益、附着、能量或触发。修改输入后须重新复核；本页没有伤害乘区公式。"],
            ["补充 ID", "名称", "角色", "技能", "时刻（秒）", "期望伤害", "父动作", "覆盖身份", "外部事件 ID"]]
    for item in items:
        label = item["label"] + ("（窗口外审计，不计总量）" if window is not None and not report_includes(window, item["t"]) else "")
        rows.append([item["id"], label, names.get(item["character"], item["character"]),
                     item["skill"], item["t"], item["expected"], item["action_id"],
                     item["coverage_key"], item["event_id"]])
    rows += [[], ["补充 ID", "依据字段", "内容"]]
    for item in items:
        for key, label_zh in (("reason", "补充原因"), ("calculation_note", "外部计算说明"),
                              ("non_overlap_note", "无重复说明"), ("source_refs", "来源引用"),
                              ("precision", "近似方法与不确定性"),
                              ("accepted_fingerprint", "已复核指纹")):
            value = item.get(key)
            if value is None:
                continue
            encoded = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            for offset in range(0, max(1, len(encoded)), 15000):
                rows.append([item["id"], label_zh, encoded[offset:offset + 15000]])
    from genshin_app.equipment.files import fingerprint
    rows += [[], ["外部补充契约", external["contract"]],
             ["依据上下文指纹", fingerprint(external["basis"]["context"])],
             ["物化来源指纹", external["basis"]["source_fingerprint"]],
             ["证据层级", result["calculation_totals"]["evidence_tier"]]]
    return rows


def hit_rows(result: dict, display: dict | None = None) -> list[list]:
    hits = result["hits"]
    zone_keys: list[str] = []
    for h in hits:
        for k in h["zones"]:
            if k not in zone_keys:
                zone_keys.append(k)
    header = ["时间（秒）", "角色", "技能", "段名", "伤害体系", "段数", "期望伤害", "暴击伤害",
              "快照时刻", "生效增益", *[label("result_zones", k) if display else f"zone.{k}"
                                      for k in zone_keys]]
    rows: list[list] = [header]
    for h in hits:
        rows.append([
            h["t"], display.get("characters", {}).get(h["character"], h["character"])
            if display else h["character"],
            label("skills", h["skill"]) if display else h["skill"],
            label("", h["hit_name"], "伤害事件（中文名待补）") if display else h["hit_name"],
            label("damage_systems", h["damage_system"]) if display else h["damage_system"],
            h["count"], h["expected"], h["crit"] if h["crit"] is not None else "",
            h["snapshot_at"] if h["snapshot_at"] is not None else "",
            "、".join(display.get("buffs", {}).get(b, "增益（中文名待补）") for b in h["buffs"])
            if display else ",".join(h["buffs"]),
            *[h["zones"].get(k, "") for k in zone_keys],
        ])
    overridden = [h for h in hits if isinstance(h.get("calculation_override"), dict)]
    if overridden:
        rows += [[], ["固定值事件", "覆盖身份", "自动期望值", "生效期望值", "精度", "方法", "不确定性"]]
        for h in overridden:
            override = h["calculation_override"]
            precision = override.get("precision") or {}
            rows.append([
                h.get("id", ""), override.get("coverage_key", ""),
                override.get("automatic_expected", ""),
                override.get("effective_expected", ""),
                precision.get("kind", ""), precision.get("method", ""),
                precision.get("uncertainty", ""),
            ])
    return rows


def damage_reporting_rows(result: dict) -> list[list]:
    """Display an already validated execution proof, never infer an empty result."""
    proof = result.get("damage_reporting")
    if not proof:
        return []
    scope = proof["scope"]
    rows = [
        ["伤害报告证明", "只读执行记录；修改动作、接触或命中声明后须回 App 重新计算。"],
        ["契约", proof["contract"]],
        ["结果", "完整输入窗口内的已声明接触全部未命中；引擎总伤害与每秒伤害为 0，外部补充另列。" if result.get("external_damage") else "完整输入窗口内的已声明接触全部未命中；总伤害与每秒伤害为 0。"],
        ["证明范围", "本次完整显式输入窗口；不代表后续循环已成立。"],
        ["窗口开始（微秒）", scope["start_tick"]],
        ["窗口结束（微秒，右端不含）", scope["end_tick"]],
        ["计划接触数", proof["planned_contacts"]],
        ["实际未命中数", proof["missed_contacts"]],
        ["实际伤害事件数", proof["reported_damage_events"]],
        [],
        ["事件", "执行实例", "父动作", "时刻（微秒）", "同刻顺序", "计入报告", "命中", "执行结果"],
    ]
    rows.extend([[c["event_id"], c["run_id"], c["action_id"], c["tick"], c["order"],
                  "是" if c["report"] else "否", "是" if c["landed"] else "否",
                  "未命中" if c["outcome"] == "missed" else c["outcome"]]
                 for c in proof["contacts"]])
    return rows


def _beneficiary_time(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _beneficiary_window(result):
    """Read a declared/receipted window; do not derive one from trace or damage."""
    if "buff_state_timeline" in result:
        timeline = result["buff_state_timeline"]
        if (isinstance(timeline, dict)
                and timeline.get("contract") == "buff-state-timeline-v1"
                and type(timeline.get("version")) is int and timeline["version"] == 1
                and _beneficiary_time(timeline.get("duration"))
                and _beneficiary_time(timeline.get("execution_limit"))
                and 0 <= timeline["execution_limit"] <= timeline["duration"]):
            return ("receipt", timeline["duration"], timeline["execution_limit"],
                    "仅显示输入轴内已处理的选人记录；持续状态依据本次计算回执。")
        return ("unknown", None, None,
                "持续状态回执的契约或窗口无效；不能回退为已确认的输入轴范围。")
    rotation_check = result.get("rotation_check")
    rotation = rotation_check.get("resolved_rotation_model") if isinstance(rotation_check, dict) else None
    period = rotation.get("period") if isinstance(rotation, dict) else None
    if _beneficiary_time(period) and period > 0:
        return ("declared", period, period,
                "仅按明确声明的输入轴长筛选；未提供持续状态证明，不据此认定完整覆盖。")
    return ("unknown", None, None,
            "没有可信的持续状态窗口或明确声明的轴长；以下仅为原始选人诊断，不代表当前输入轴收益。")


def beneficiary_eligibility_rows(result: dict, display: dict | None = None) -> list[list]:
    """Explain recorded qualification decisions, never reconstruct recipients."""
    from genshin_engine.registry import BENEFIT_TYPES

    timeline = result.get("buff_state_timeline")
    raw_states = timeline.get("states") if isinstance(timeline, dict) else None
    states = [state for state in (raw_states if isinstance(raw_states, list) else [])
              if isinstance(state, dict) and isinstance(state.get("beneficiaries_scope"), dict)
              and state["beneficiaries_scope"].get("eligibility")]
    decisions = [row for row in result.get("mechanics_trace", [])
                 if isinstance(row, dict) and isinstance(row.get("beneficiary_selection"), dict)
                 and row["beneficiary_selection"].get("contract") == "beneficiary-eligibility-v1"]
    if not states and not decisions:
        return []
    window_kind, duration, limit, window_note = _beneficiary_window(result)
    invalid_times = sum(not _beneficiary_time(row.get("t")) for row in decisions)
    outside_window = 0
    if window_kind != "unknown":
        in_window = [row for row in decisions if _beneficiary_time(row.get("t"))
                     and 0 <= row["t"] < duration and row["t"] <= limit]
        outside_window = len(decisions) - len(in_window) - invalid_times
        decisions = in_window
    unshown_states = len(states) if window_kind != "receipt" else 0
    if window_kind != "receipt":
        states = []
    names = (display or {}).get("characters", {})

    def character(value):
        return names.get(value, value) if value is not None else "未提供"

    def joined(values):
        return "、".join(character(value) for value in values) or "无"

    def selection_rows(selection):
        return [["选人时点（秒）", selection.get("selection_t"), "当时前台", character(selection.get("frontline"))],
                ["基础候选", joined(selection.get("base_candidates", [])),
                 "实际接收", joined(selection.get("recipients", []))],
                ["排除记录", "、".join(f"{character(item.get('character'))}（{ {'excluded_owner': '提供者被排除', 'not_in_whitelist': '不在资格名单内'}.get(item.get('reason'), '原因见原始回执')}）"
                                     for item in selection.get("excluded", [])) or "无"],
                ["关联触发事件", selection.get("event_id")]]

    title = ("受益者选人诊断（范围未核实）" if window_kind == "unknown"
             else "受益者资格与实际选人（只读计算回执）")
    rows = [[], [title], ["记录范围", window_note],
            ["解释", "资格限定接收角色，不是伤害类型筛选；实际命中仍须检查其他条件。"]]
    if window_kind != "unknown":
        rows += [["输入轴结束（秒，不含）", duration, "已执行记录截至（秒，含）", limit],
                 ["未列入本轴记录", f"窗口之外：{outside_window} 条；无效处理时点：{invalid_times} 条。完整原始流水仍保留在计算包中。"]]
    elif invalid_times:
        rows.append(["无效处理时点", f"{invalid_times} 条，仅保留为范围未核实的诊断。"])
    if unshown_states:
        rows.append(["未展示持续状态", f"{unshown_states} 项；没有合法持续窗口，不显示为已确认区间。"])
    for state in states:
        scope = state["beneficiaries_scope"]
        eligibility = scope["eligibility"]
        rows += [[], ["来源", state.get("name_zh", "机制增益"), "提供者", character(state.get("owner"))],
                 ["来源依据", state.get("source", ""), "证据状态", {"confirmed": "已确认", "assumed": "采用假设", "unknown": "尚未核实", "pending": "待核实"}.get(state.get("evidence_status"), "见原始回执")],
                 ["基础选人方式", {"frontline": "触发时前台" if scope.get("scope") == "locked" else "跟随当前前台", "team": "队伍", "self": "提供者本人", "characters": "指定角色"}.get(scope.get("mode"), "见原始规则")],
                 ["资格", "排除提供者本人" if eligibility.get("exclude_owner") is True else "按资格名单", "资格名单", joined(eligibility.get("characters", [])) if "characters" in eligibility else "未额外限定"],
                 ["实例接收角色", character(state.get("beneficiary")), "持续说明", "触发时锁定，切走后保留至到期；不转给新前台" if scope.get("scope") == "locked" else "持续条表示激活状态；仅当前前台可用，切走后不提供该项收益"]]
        for effect in state.get("effects", []):
            rows.append(["收益", BENEFIT_TYPES.get(effect.get("benefit_type_id"), {}).get("label", "收益类型见原始规则"),
                         "每层声明值（比率以1表示100%）", json.dumps(effect.get("value"), ensure_ascii=False)])
        for part in state.get("slices", []):
            rows += [["开始（秒，含）", part.get("start"), "结束（秒，不含）", part.get("end")],
                     ["激活／刷新（秒）", part.get("activation_time"), "规则到期（秒）", part.get("rule_expires_at")]]
            selection = part.get("beneficiary_selection")
            rows += selection_rows(selection) if selection else [["选人回执", "未提供；仅显示已有实例名单，不推测历史前台。"]]
    for row in decisions:
        selection = row["beneficiary_selection"]
        outcome = ("无人符合资格；未激活，不刷新或清除已有残留"
                   if row.get("reason") == "no_eligible_beneficiary" else "已激活／刷新")
        rows += [[], ["诊断规则" if window_kind == "unknown" else "规则",
                      row.get("name_zh") or row.get("rule_id"), "处理时点（秒）",
                      row.get("t") if _beneficiary_time(row.get("t")) else "未提供或无效"],
                 ["诊断记录结果（范围未核实）" if window_kind == "unknown" else "结果", outcome]]
        rows += selection_rows(selection)
    return rows


def coverage_rows(result: dict, display: dict | None = None) -> list[list]:
    if result.get("coverage_interpretation", {}).get("kind") == "hit_projection":
        rows = [["覆盖率解释", "说明"], ["逐击数值投影", result["coverage_interpretation"]["message"]]]
        timeline = result.get("buff_state_timeline", {})
        if _beneficiary_window(result)[0] == "receipt":
            from genshin_app.presentation import buff_presentation

            names = (display or {}).get("characters", {})
            has_sampling = any(buff_presentation(state.get("buff") or {})
                               for state in timeline.get("states", []))
            rows += [[], ["增益状态与计算采样（已保存回执；不代表游戏Buff寿命）" if has_sampling
                          else "持续 Buff 状态（已保存回执；不代表每击必定受益）"],
                     ["增益名称", "提供者", "受益者", "开始秒数", "结束秒数", "状态", "层数", "剩余次数", "结束原因"]
                     + (["展示用途", "用途说明"] if has_sampling else [])]
            reasons = {"refresh": "刷新", "layer_expiry": "部分层数到期", "quota_change": "次数变化",
                       "natural_expiry": "自然到期", "execution_limit": "执行中断", "window_end": "轴窗口结束"}
            for state in timeline.get("states", []):
                purpose = buff_presentation(state.get("buff") or {})
                for part in state.get("slices", []):
                    rows.append([state.get("name_zh", "增益名称待补"), names.get(state.get("owner"), state.get("owner") or "全队"),
                                 names.get(state.get("beneficiary"), state.get("beneficiary") or "全队"),
                                 part["start"], part["end"], "计算采样编码" if purpose else
                                 "次数耗尽" if part.get("status") == "exhausted" else "规则有效",
                                 part.get("layers"), part.get("remaining_quota") if part.get("remaining_quota") is not None else "不限制次数",
                                 reasons.get(part.get("end_reason"), "其他结束原因")]
                                + (["计算采样窗口（非游戏Buff寿命）" if purpose else "声明状态区间",
                                    purpose["reason"] if purpose else ""] if has_sampling else []))
            for excluded in timeline.get("excluded", []):
                rows.append(["未画持续区间", excluded.get("name_zh", "增益名称待补"), excluded.get("message", "")])
        elif "buff_state_timeline" in result:
            rows += [[], ["持续状态范围未核实", _beneficiary_window(result)[3]],
                     ["未画持续区间", "原始记录仍保留在计算包中，不能据此认定本轴持续覆盖。"]]
        return rows + beneficiary_eligibility_rows(result, display)
    rows: list[list] = [["增益名称" if display else "增益标识", "来源", "覆盖率"]]
    rows += [[display.get("buffs", {}).get(c["id"], "增益（中文名待补）") if display else c["id"],
              label("", c["source"], "中文来源说明待补") if display else c["source"], c["uptime"]]
             for c in result.get("buffs_coverage", [])]
    return rows + beneficiary_eligibility_rows(result, display)


def action_resource_rows(result: dict) -> list[list]:
    """Read-only materialized resource provenance; never resolve or infer rules."""
    records = result.get("action_resource_compilations") or []
    if not records:
        return []
    rows = [["动作资源来源", "本次执行副本的基础资料快照；Excel不重演冷却或能量状态机。"],
            ["用途边界", "declared_not_verified：普通固定耗能用途由方案声明，并非自动验证的游戏规则。"],
            ["身份覆盖", "explicit_sources_only：仅核对显式关联；未知手工能力的共享冷却关系未获证明。"]]

    def evidence(title, value):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        # 15000 code points also fit Excel's UTF-16 limit for non-BMP text.
        for offset in range(0, max(1, len(text)), 15000):
            rows.append([title if offset == 0 else "续", text[offset:offset + 15000]])

    names = {"base_cooldown_seconds": "基础冷却（秒）", "base_charge_count": "基础次数",
             "base_energy_cost": "基础元素能量", "cost_element": "耗能元素"}
    for record in records:
        rows += [[], ["能力", record.get("ability_id", ""), "角色", record.get("actor", ""),
                      "资料编译状态", record.get("status", "pending")]]
        if record.get("energy_delivery"):
            rows.append(["扣能交付", "动作接纳时自动扣能；系统流水只记录已发生的提交，不是可编辑的排队事件。"])
            evidence("自动扣能协议与父动作映射", record["energy_delivery"])
        for key, title in (("purpose", "用途"), ("variant", "变体声明"), ("interpretation_status", "依据状态"),
                           ("interpretation_basis", "用途依据"), ("gameplay_interpretation", "游戏解释状态"),
                           ("identity_coverage", "身份覆盖范围"), ("resolved_selection", "精确技能身份")):
            evidence(title, record.get(key, "待补"))
        for key, title in (("name", "技能中文名"), ("description", "技能原文")):
            item = (record.get("descriptions") or {}).get(key) or {}
            evidence(title, item.get("text") if item.get("text") is not None else "待补")
        rows.append(["资料字段", "状态", "规范值", "原始字段", "原始值"])
        for key, title in names.items():
            item = (record.get("projection") or {}).get(key) or {}
            rows.append([title, item.get("status", "pending"), item.get("value") if item.get("value") is not None else "待补",
                         item.get("source_field", ""), item.get("raw_value") if item.get("raw_value") is not None else "未提供"])
        for key, title in (("compiled_values", "本次编译值"), ("action_spend_links", "动作与扣能映射"),
                           ("target_paths", "编译目标路径"), ("known_aliases", "已知同源别名"),
                           ("explicit_modifiers", "显式修改事件"), ("snapshot", "资料快照"),
                           ("resource_fingerprint", "资源指纹"), ("scope_fingerprint", "用途范围指纹"),
                           ("compilation_fingerprint", "编译指纹"), ("required_confirmation", "本次确认要求"),
                           ("identity_sources", "身份节点证据"), ("projection", "逐字段原始指针与SHA"),
                           ("descriptions", "中文原文关系证据"), ("issues", "问题")):
            evidence(title, record.get(key, "待补"))
    automatic = (result.get("rotation_check") or {}).get("automatic_energy_delivery")
    if automatic:
        rows += [[], ["自动扣能统计口径", "计划分母为已报告的父动作；检查提前结束而未访问的后续窗口单列，不补造施放。"],
                 ["历史能量", "系统扣能行的before/after是动作提交瞬间值，不包含随后退款或产球；Excel不重新检查循环。"]]
        evidence("自动扣能完整统计", automatic)
    return rows


def energy_generation_rows(result: dict) -> list[list]:
    """Read-only source and actual resource accounting; no inferred dispatch."""
    records = result.get("energy_generation_compilations") or []
    if not records:
        return []
    rows = [["技能产球来源", "技能实际命中驱动参考产球规则；本表只保留本次计算证据。"],
            ["支持范围", "仅已核对的初始命中与固定变体，不等于完整技能或游戏原生模拟。"],
            ["计时假设", "本参考域不计受击停顿延寿；命中间隔由系统核对，不能修改为技能冷却。"],
            ["编辑范围", "改动作、场景、来源或两段时间后回App重算；Excel不重演产球与收取队列。"]]

    def evidence(title, value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append([title if offset == 0 else "续", encoded[offset:offset + 15000]])

    for record in records:
        resource, calibration = record.get("resource_projection") or {}, record.get("calibration") or {}
        certificate = record.get("trigger_certificate") or {}
        rows += [[], ["来源标识", record.get("id"), "来源状态", "范围内就绪" if record.get("status") == "ready" else "待补充"],
                 ["每批数量", resource.get("count"), "元素", label("elements", resource.get("element"), "待核实")],
                 ["能量类别", {"particle": "元素微粒", "orb": "元素晶球"}.get(resource.get("particle_type"), "待核实")],
                 ["命中到产球（秒）", calibration.get("spawn_delay_seconds"), "产球到收取（秒）", calibration.get("travel_seconds")],
                 ["计时范围", "不计受击停顿的参考模型" if certificate.get("hitlag_policy") == "zero_hitlag_reference" else "待核实"],
                 ["来源动作", "来源命中", "命中时点（秒）", "同刻顺序", "命中声明"]]
        for parent in record.get("parent_links") or []:
            rows.append([parent.get("action_id"), parent.get("hit_id"), parent.get("t"), parent.get("order"),
                         "命中" if parent.get("landed") is True else "未命中" if parent.get("landed") is False else "待核实"])
        evidence("完整来源、校准、接管清单与执行规则", record)
    summary = result.get("energy_generation_summary")
    if summary:
        rows += [[], ["本次产球与收取统计", "以实际事件流水为准；未报告的后续动作不推断为成功或零产球。"]]
        for key, title in (
            ("planned_parent_hits", "计划来源命中"), ("reported_parent_hits", "已报告来源命中"),
            ("eligible_parent_hits", "具备产球资格的命中"), ("missed_parent_hits", "未命中"),
            ("offfield_parent_hits", "提供者不在前台"), ("unverified_parent_hits", "未能验证的命中"),
            ("unreported_parent_hits", "尚未报告的命中"), ("spawned_batches", "已产球批次"),
            ("spawned_particles", "已产生数量"), ("credited_batches", "已收取批次"),
            ("credited_particles", "已收取数量"), ("in_flight_particles", "仍在途数量"),
        ):
            rows.append([title, summary.get(key, "待核实")])
        evidence("完整执行统计", summary)
    return rows


def limited_hit_benefit_rows(result: dict) -> list[list]:
    """Materialized source and actual per-recipient quota/current-stat history."""
    records = result.get("limited_hit_benefit_compilations") or []
    if not records:
        return []
    rows = [["有限次数收益", "已核对来源的有限参考适配；完整技能仍为部分支持。"],
            ["激活与消费", "由成功施放激活，各角色独立计次；施放初击未命中仍可激活，漏击不扣次数。"],
            ["攻击力取样", "每次符合条件的命中读取提供者当前攻击力；不沿用接收者旧面板快照。"],
            ["编辑范围", "来源、激活校准、次数、动作、提供者攻击力及其贡献值改变后回App重算；Excel不重演配额与取样队列。"]]

    def evidence(title, value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append([title if offset == 0 else "续", encoded[offset:offset + 15000]])

    names = {}
    for record in records:
        certificate = record.get("benefit_certificate") or {}
        rows += [[], ["来源标识", record.get("id"), "提供者", record.get("actor")],
                 ["来源状态", "范围内就绪" if record.get("status") == "ready" else "待完善", "完整来源", "部分"],
                 ["攻击力系数", certificate.get("coefficient"), "持续（秒）", certificate.get("duration_seconds")],
                 ["每位角色次数", certificate.get("capacity"), "重复施放", "刷新并重置，非叠加"],
                 ["施放到激活（秒）", (record.get("calibration") or {}).get("activation_offset_seconds")]]
        for rule in record.get("rules", []):
            names[rule["id"]] = rule.get("name_zh", "有限次数收益")
        evidence("完整来源、确认、校准与执行证书", record)
    rows += [[], ["时刻（秒）", "规则", "记录类别", "实际执行记录"]]
    labels = {"activation": "激活或刷新", "expiry": "到期", "quota": "次数检查与消费", "effect_applied": "收益参与伤害"}
    for category, entries in (("机制流水", result.get("mechanics_trace", [])), ("攻击力取样", result.get("sampling_trace", []))):
        for row in entries:
            if row.get("rule_id") not in names:
                continue
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
            for offset in range(0, max(1, len(encoded)), 15000):
                rows.append([row.get("t", row.get("event_t", "")) if offset == 0 else "续", names[row["rule_id"]],
                             labels.get(row.get("type"), category), encoded[offset:offset + 15000]])
    summary = result.get("limited_hit_benefit_summary")
    if summary:
        rows.append([])
        for key, title in (("activations", "实际激活次数"), ("consumed_hits", "实际扣次命中"),
                           ("exhausted_hits", "配额耗尽的命中"), ("applied_benefits", "实际应用收益"),
                           ("sampled_hits", "实际攻击力取样")):
            rows.append([title, summary.get(key)])
        evidence("完整实际执行统计", summary)
    return rows


def mechanism_rows(result: dict) -> list[list]:
    """Reviewed generic rules and recorded state; no skill-specific rendering."""
    from genshin_engine.registry import BENEFIT_TYPES

    records = result.get("mechanism_compilations") or []
    if not records:
        return []
    rows = [["通用机制来源", "受审资料与本轴资格范围内执行；完整来源仍为部分支持。"],
            ["编辑范围", "来源、触发、次数、取样属性与贡献值为固定输入；修改后回App重新计算。"],
            ["工作簿能力", "展示本次实际流水及逐击公式；不重演任意状态机、附着或配额。"]]

    def evidence(label, value):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(text)), 15000):
            rows.append([label if not offset else "续", text[offset:offset + 15000]])

    own = set()
    for record in records:
        rows += [[], ["来源标识", record["id"], "提供者", record["actor"]]]
        for rule in record.get("rules", []):
            own.add(rule["id"])
            rows.append(["规则", rule.get("name_zh", rule["id"]), "触发", {"action_start": "动作开始", "elemental_reaction": "元素反应", "hit": "实际命中"}.get(rule["trigger"]["event"], rule["trigger"]["event"])])
            if record.get('projection', {}).get('execution_qualification', {}).get('schema_version') == 3:
                stack = rule['stacking']
                rows.append(['触发阶段', '该击处理后', '内部冷却（秒）', rule['timing']['cooldown'],
                             '持续（秒）', rule['timing']['duration']])
                rows.append(['层数策略', '增加层数并共同刷新' if stack['policy'] == 'add_and_refresh' else '再次触发刷新',
                             '层数上限', stack['max']])
            for effect in rule["effects"]:
                rows.append(["收益", BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('label', '待核实收益'),
                             "取样域", {'self': '自身属性', 'provider': '提供者属性', 'enemy': '敌方属性'}.get(effect['domain'], '待核实')])
            if rule.get("quota"):
                rows.append(["次数池", '各受益者独立' if rule['quota']['scope'] == 'per_beneficiary' else '共同使用',
                             "容量", rule["quota"]["capacity"], "刷新", '重置次数' if rule['quota']['refresh'] == 'reset' else '按来源规则'])
        evidence("完整物化来源、资格、父动作与执行规则", record)
    for field, title in (("mechanics_trace", "实际机制流水"), ("sampling_trace", "实际属性取样")):
        for row in result.get(field, []):
            if row.get("rule_id") in own:
                evidence(title, row)
    evidence("实际执行统计", result.get("mechanism_summary"))
    evidence("离线绑定重建上下文", result.get("mechanism_replay_context"))
    evidence("实际执行模型与输入模板", result.get("mechanism_execution"))
    return rows


def state_runtime_rows(result: dict) -> list[list]:
    record = result.get('state_runtime')
    if not record:
        return []
    labels = {'lifecycle_start':'开始', 'lifecycle_established':'建立', 'lifecycle_end':'结束',
              'dispatch':'发出定时指令', 'timer_tick':'定时触发', 'state_dispatch_completed':'指令执行', 'state_dispatch_failed':'指令失败',
              'state_control_completed':'累计控制已提交','state_control_failed':'累计控制失败',
              'state_batch_completed':'控制批次已完成', 'state_delivery_failed':'控制交付失败',
              'activated':'收益已激活', 'not_activated':'条件不满足或冷却中', 'pending':'待核实',
              'resource_operation':'资源操作已处理', 'cancelled':'已取消', 'failed':'失败'}
    rows = [['生命周期流水', '声明的定时事件与实际执行分列；领域结束与已发放收益到期分别记录。'],
            ['修改方式', '修改领域、定时、来源或动作后须回App重算，本页不重演状态机。']]
    positioned = record.get('contract') == 'finite-state-mechanics-bridge-v2'
    if positioned:
        rows.append(['实际控制状态', '签发不等于生效；逐步提交与整批完成分开。交付失败时仅保留实际完成前缀。'])
        labels['state_control_completed'] = '领域控制已提交'
    for field, title in [('issued_trace','调度记录'), ('execution_trace','实际执行记录')]:
        rows += [[], [title], ['秒数','过程','领域标识','定时器','次数','执行结果','操作类别','目标','来源事件','原因']
                 + (['控制序号','批次 / 完成步数','实例标识'] if positioned else [])]
        for row in record.get(field, []):
            payload = row.get('payload') or {}
            outcome = 'failed' if row.get('type') in ('state_dispatch_failed','state_control_failed','state_delivery_failed') else row.get('outcome')
            if row.get('type') == 'state_control_completed':
                outcome = labels['state_control_completed']
            elif row.get('type') == 'state_batch_completed':
                outcome = '控制批次已完成'
            rows.append([row['time_us']/1_000_000 if 'time_us' in row else None,
                         labels.get(row.get('type'),row.get('type')),row.get('lifecycle_id'),row.get('timer_id'),
                         row.get('ordinal'),labels.get(outcome,outcome),
                         {'dispatch_rule':'激活收益','resource_operation':'资源操作', 'start_lifecycle':'开始领域', 'end_lifecycle':'结束领域',
                          'lifecycle_start':'开始领域','lifecycle_established':'建立领域','lifecycle_closing':'领域结束处理中',
                          'lifecycle_end':'领域已结束','timer_tick':'定时触发','dispatch':'派发定时指令'}.get(payload.get('kind') or (row.get('kind') if positioned else None), payload.get('kind')),
                         payload.get('ref_id') or (row.get('lifecycle_id') if positioned else None),row.get('source_event_id') or row.get('event_run_id') or (row.get('source_event') or {}).get('event_id'),
                         row.get('message') or row.get('reason')]
                         + ([row.get('control_sequence'), f"{row.get('batch_id', '—')} / {row.get('step_count', row.get('applied_steps', '—'))}",
                             row.get('generation_uid')] if positioned else []))
    rows += [[], ['完整只读生命周期账', '有序分块保留完整来源、实例与执行身份，不增加第二份收益。']]
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    rows += [[f'原文块 {offset//480+1}',encoded[offset:offset+480]] for offset in range(0,len(encoded),480)]
    return rows


def hit_parameter_rows(result: dict) -> list[list]:
    records = result.get('hit_parameter_sources')
    if not records:
        return []
    rows = [['技能参数来源','只读原始表参数；用途由方案声明，其它命座改动未执行。'],
            ['修改方式','更改角色、命座、基础等级、参数或用途后回App重新核对并计算。']]
    for record in records:
        level = record.get('level') or {}
        rows += [[], ['命中标识',record.get('hit_id'),'角色',record.get('actor')],
                 ['基础等级',level.get('base_level'),'命座加级',level.get('extra_level')],
                 ['有效等级',level.get('effective_level'),'原始参数',(record.get('scalar_projection') or {}).get('value')],
                 ['倍率用途依据',record.get('interpretation_basis')],
                 ['其它操作','尚未执行；静态等级就绪不等于完整技能自动执行']]
        for contribution in (record.get('level_profile') or {}).get('contributions',[]):
            rows.append(['命座贡献',contribution.get('rank'),'加级',contribution.get('extra_level'),
                         '作用于所选技能组',contribution.get('matches_selected_group')])
        for description in (record.get('parameter_descriptions') or {}).get('matched',[]):
            rows.append(['对应原文',description.get('text')])
    rows += [[], ['完整只读参数来源','有序分块保存全部等级依据、节点和审核身份。']]
    encoded = json.dumps(records,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    rows += [[f'原文块 {offset//480+1}',encoded[offset:offset+480]] for offset in range(0,len(encoded),480)]
    return rows


def attribute_ledger_rows(result: dict) -> list[list]:
    from genshin_app.attribute_stat_reporting import attribute_stat_rows
    stat_rows=attribute_stat_rows(result)
    if stat_rows is not None:
        return stat_rows
    ledger = result.get('attribute_ledger')
    if not ledger:
        return []
    basis_labels = {'total':'总精通','nonextra':'非额外精通','base':'明确基础量'}
    phases = {'initial':'初始状态','pre':'事件前','post':'事件后','reaction_created':'反应产生时',
              'reaction_damage_pre':'反应出伤前','capture':'面板取样','trigger':'触发取样','damage_projection':'出伤属性投影'}
    rows = [['属性取样账', '实际执行只读回执；来源窗口0对应完整输入，附加检查窗口不增加伤害汇总。取样不代表已计入伤害，以伤害报告为准；修改来源或时点须回App重算。'],
            ['回执标识','角色','取值口径','时刻（秒）','来源窗口（0为完整输入，其余为附加检查）','阶段','状态','基底','实际取值','来源组件数']]
    for receipt in ledger.get('receipts', []):
        query = receipt.get('query', {}); frame = query.get('frame', {})
        rows.append([receipt.get('receipt_id'),query.get('actor'),basis_labels.get(query.get('basis'),query.get('basis')),
                     frame['t_us']/1_000_000 if 't_us' in frame else None,frame.get('origin'),phases.get(frame.get('phase'),frame.get('phase')),
                     {'known':'已知','pending':'待核实','invalid':'无效'}.get(receipt.get('status'),receipt.get('status')),
                     receipt.get('baseline'),receipt.get('value'),len(receipt.get('contributions', []))])
    rows += [[], ['完整只读属性账','以下有序分块可重建原始JSON，数值未截断。']]
    encoded = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    for index in range(0,len(encoded),480):
        rows.append([f'原文块 {index//480+1}',encoded[index:index+480]])
    return rows


def resource_counter_rows(result: dict) -> list[list]:
    ledger = result.get('resource_counters')
    if not ledger:
        return []
    kinds = {'counter_accumulation':'实际消费累计','counter_reset':'重置累计','counter_window':'累计窗口变化'}
    reasons = {'accumulated':'已累计','outside_window':'累计窗口外','no_spend':'无实际消费',
               'created':'已创建','established':'已建立','closing':'正在结束','ended':'已结束'}
    def amount(value):
        if isinstance(value, dict):
            return '；'.join(f"{'合计' if k=='total' else k}：{v}" for k,v in sorted(value.items()))
        return value
    rows = [['资源累计流水','只读实际执行，数量为整数原子；单位量=原子数÷每单位原子数。含附加检查，不增加伤害汇总；收益取值另见资源读数流水。' if result.get('resource_counter_views') else '只读实际执行，数量为整数原子；单位量=原子数÷每单位原子数。含附加检查，不增加伤害汇总；尚未配置累计转伤害。'],
            ['时刻（秒）','累计项','过程','累计键','请求扣量','实际扣量','计入累计','之前','之后','每单位原子数','状态','生命周期','资源回执']]
    for row in ledger.get('trace', []):
        rows.append([row['time_us']/1_000_000 if 'time_us' in row else None,row.get('counter_id'),
                     kinds.get(row.get('type'),row.get('type')),
                     '合计' if row.get('key')=='total' else row.get('key'),
                     row.get('requested'),row.get('resource_accepted'),row.get('accepted'),
                     amount(row.get('before')),amount(row.get('after')),
                     ledger.get('counter_units',{}).get(row.get('counter_id')),
                     reasons.get(row.get('reason',row.get('phase')),row.get('reason',row.get('phase'))),
                     row.get('lifecycle_id'),row.get('resource_receipt_id')])
    rows += [[], ['完整只读资源累计账','含实际已提交状态与来源审核；不把已签发调度视为已执行。']]
    encoded = json.dumps(ledger,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    rows += [[f'原文块 {offset//480+1}',encoded[offset:offset+480]] for offset in range(0,len(encoded),480)]
    return rows


def resource_counter_view_rows(result: dict) -> list[list]:
    ledger = result.get('resource_counter_views')
    from genshin_app.counter_frame_reporting import (
        counter_view_rows,
        is_self_frame_report,
    )
    if is_self_frame_report(ledger):
        return counter_view_rows(ledger)
    if not ledger:
        return []
    rows = [['资源读数流水','只读实际提交账取值；原始累计、单位换算与封顶/阶梯分开。表达式单值不等于层数合成后收益，不再额外计入伤害。'],
            ['秒数','规则 / 收益','接收角色','累计 / 读数','原始原子数','每单位原子数','当前读数','单次表达式值','层数','合成收益','资源游标 / 控制游标','生命周期','取样回执']]
    for row in ledger.get('trace',[]):
        reading=row.get('reading',{}); cursor=reading.get('counter_cursor',{})
        rows.append([row.get('t'),f"{row.get('rule_id','')} / {row.get('effect_id','')}",row.get('character'),
                     f"{reading.get('counter_id','')} / {reading.get('view_id','')}",reading.get('raw_atoms'),reading.get('unit_scale'),
                     row.get('source_value'),row.get('single_value',row.get('value')),row.get('layer_count'),row.get('combined_value'),
                     json.dumps(cursor,ensure_ascii=False,sort_keys=True),(reading.get('lifecycle') or {}).get('id'),row.get('receipt_id')])
    rows += [[],['完整只读资源读数账','改变累计、单位、封顶、阶梯、来源或表达式须回App重算；本表不重演状态机。']]
    encoded=json.dumps(ledger,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    rows += [[f'原文块 {offset//480+1}',encoded[offset:offset+480]] for offset in range(0,len(encoded),480)]
    return rows


def attribute_callback_rows(result: dict) -> list[list]:
    ledger = result.get('attribute_callbacks')
    if not ledger:
        return []
    stages = {'callback_plan': '计划已生成', 'callback_batch_prepared': '批次取样已准备',
              'callback_committed': '回调已提交'}
    names = {row['rule_id']: row.get('name_zh') for row in (ledger.get('source_adoption') or {}).get('members', [])}
    rows = [['属性回调执行', '只读实际记录。计划、取样准备与实际提交分开；手工顺序是假设，不是数据库已认证的游戏事实。取样数值另见属性账；不增加伤害。'],
            ['秒数', '来源窗口', '父命中', '父执行标识', '过程', '规则', '来源部件', '提供者', '批次', '属性帧', '回调执行标识']]
    for row in ledger.get('trace', []):
        time_us = row.get('time_us')
        rows.append([time_us / 1_000_000 if type(time_us) is int else None, row.get('origin'),
                     row.get('parent_event_id'), row.get('parent_run_id'), stages.get(row.get('type'), row.get('type')),
                     f"{names[row['rule_id']]}（{row['rule_id']}）" if names.get(row.get('rule_id')) else row.get('rule_id'),
                     row.get('source_id'), row.get('owner'), row.get('batch'),
                     row.get('frame_id'), row.get('callback_run_id')])
    rows += [[], ['完整只读回调账', '含计划、依赖足迹、实际提交前缀及来源核对。修改顺序、动作或面板后回App重算；此表不执行事件队列。']]
    encoded = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    rows += [[f'原文块 {offset // 480 + 1}', encoded[offset:offset + 480]] for offset in range(0, len(encoded), 480)]
    return rows


def resource_threshold_rows(result: dict, field: str) -> list[list]:
    from genshin_app.resource_threshold_reporting import (
        COMPARISONS,
        FIELDS,
        TITLES,
        threshold_outcome,
        threshold_projection,
        threshold_time_seconds,
    )
    ledger = result.get(field)
    if not ledger:
        return []
    observer = field == FIELDS[0]
    lifecycle = any('lifecycle_id' in row for row in ledger.get('trace', []))
    rows = [[TITLES[FIELDS.index(field)], '只读实际流水。资源变化、阈值观察、签发与实际激活分开；观察或签发不代表Buff已生效，不额外计入伤害。'],
            ['秒数', '来源窗口', '父事件 / 执行标识', '过程结果', '观察项 / 绑定', '规则', '来源 / 提供者',
             '计量 / 比较方向', '阈值原子数', '之前', '之后', '来源操作阶段' if lifecycle else '阶段', '资源回执 / 区间', '执行标识']
             + (['领域操作', '目标领域', '目标实例'] if lifecycle else [])]
    for item in ledger.get('trace', []):
        row = threshold_projection(item, ledger, field)
        ident = row.get('observer_id' if observer else 'binding_id')
        name = f"{row['name_zh']}（{ident}）" if row.get('name_zh') else ident
        counter = row.get('input_kind') == 'counter_value_key' or isinstance(row.get('counter_observation'), dict)
        quantity = '消费累计' if counter else '资源余额'
        rows.append([threshold_time_seconds(row, field), row.get('origin'),
            f"{row.get('event_id', '—')} / {row.get('run_id', row.get('source_run_id', '—'))}",
            threshold_outcome(row, field), name, row.get('rule_id'),
            f"{row.get('source_id', '—')} / {row.get('owner', '—')}",
            f"{quantity} / {COMPARISONS.get(row.get('comparison'), row.get('comparison'))}", row.get('threshold_atoms'),
            row.get('before'), row.get('after'), {'pre': '事件前', 'post': '事件后'}.get(row.get('phase'), row.get('phase')),
            row.get('resource_receipt_id') or json.dumps(row.get('receipt_span'), ensure_ascii=False), row.get('dispatch_run_id')]
            + ([{'start_lifecycle':'开始领域','end_lifecycle':'结束领域'}.get((row.get('payload') or {}).get('kind')),
                row.get('lifecycle_id'), row.get('target_generation_uid')] if lifecycle else []))
    rows += [[], ['完整只读阈值账', '包含观察、调度授权、实际结果与来源审核；数值为整数原子，单位见原始资源定义。修改阈值、动作或来源须回App重算。']]
    encoded = json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    rows += [[f'原文块 {offset // 480 + 1}', encoded[offset:offset + 480]] for offset in range(0, len(encoded), 480)]
    return rows


def shared_pool_rows(result: dict) -> list[list]:
    record = result.get("shared_pools")
    if not record:
        return []
    scaled = record.get('schema_version') == 2
    reasons = {"consumed": "已消费", "spent":"已扣除", "empty":"余额为空", "insufficient": "余额不足", "ineligible": "不符合条件",
               "set": "设置余额", "granted": "已补充", "cooldown": "资源操作冷却中"}
    rows = [["共享资源流水", "本次实际执行快照；含附加检查轮，检查轮不额外计入伤害。"],
            ["修改方式", "池、补量、消费资格和来源关系改变后须回App重算；本页不重演资源状态机。"],
            ["秒数", "检查轮", "资源池", "操作", "角色/池键", "请求", "实际接受", "之前", "之后", "原因", "父事件", "来源"]]
    if scaled:
        rows[0].extend(['数量单位', '整数原子；实际单位量 = 原子数 ÷ 每单位原子数。'])
        rows[2].extend(['每单位原子数', '未扣足', '溢出'])
    for row in record.get("trace", []):
        rows.append([row.get("t"), row.get("cycle"), row["pool_id"],
                     "消费" if row["type"] == "consume" else {'set':'设置','spend':'扣除资源'}.get(row.get('operation'),'补充'),
                     row.get("actor") or ("全队" if row["key"] == "team" else row["key"]),
                     row["requested"], row["accepted"], row["before"], row["after"], reasons.get(row["reason"], row["reason"]),
                     row["event_id"], row["source_id"]])
        if scaled:
            rows[-1].extend([row.get('unit_scale'), row.get('unfulfilled'), row.get('wasted')])
    if scaled:
        rows += [[], ['完整只读共享资源账', '有序分块保留全部原子余额、实际扣量及来源，不重演状态机。']]
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',',':'))
        rows += [[f'原文块 {offset//480+1}',encoded[offset:offset+480]] for offset in range(0,len(encoded),480)]
    return rows


def snapshot_capture_rows(result: dict) -> list[list]:
    """Readable saved capture observations; never pretend Excel replays the queue."""
    captures = result.get('snapshot_captures')
    if not captures:
        return []
    rows = [['面板取样', '按成功施放动作的显式校准时点记录自身收益状态。'],
            ['依据边界', '校准为本轴声明；引擎未验证游戏内帧时点。'],
            ['编辑范围', '取样声明、动作与引用固定；修改后回App重新计算。Excel只计算固定事件结构下的局部数值情景。'],
            ['时刻（秒）', '记录类型', '取样标识', '父动作／使用事件', '状态', '检查轮次']]
    kinds = {'snapshot_capture': '完成取样', 'snapshot_capture_reference': '使用取样'}
    statuses = {'captured': '已取样', 'cancelled': '已取消'}
    for item in captures.get('trace', []):
        rows.append([item.get('t'), kinds.get(item.get('type'), '取样记录'), item.get('capture_id'),
                     item.get('action_id', item.get('event_id')), statuses.get(item.get('status'), '引用记录'),
                     item.get('cycle', 0) + 1])
    for title, value in (
        ('完整取样声明', (result.get('snapshot_capture_execution') or {}).get('mechanics_model', {}).get('snapshot_captures', [])),
        ('完整取样流水与限额', captures),
    ):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(text)), 15000):
            rows.append([title if not offset else '续', text[offset:offset + 15000]])
    return rows


def attack_metadata_rows(result: dict) -> list[list]:
    """One immutable source bundle per profile, without repeating it per hit."""
    records = result.get("attack_metadata_compilations") or []
    if not records:
        return []
    rows = [["攻击元信息来源", "精确攻击候选投影；局部资料就绪不表示整个技能已自动还原。"],
            ["换算规则", "原始攻击durability按参考规则换算为GU，未预扣底税；底税由元素引擎处理一次。"],
            ["编辑范围", "来源、单位、候选、共享计数或用途改变后回App重算；本表不重新运行派发或元素状态。"]]
    names = {"cryo": "冰", "hydro": "水", "anemo": "风"}
    for record in records:
        projection = record.get("projection") or {}
        gauge = projection.get("gauge") or {}
        rows += [[], ["动作档案", record.get("action_event_profile_id", ""), "原型", record.get("prototype_hit_id", "")],
                 ["资料状态", "范围内就绪" if record.get("status") == "ready" else "待补充", "技能覆盖", "部分"],
                 ["元素", names.get((projection.get("element") or {}).get("value"), "未知"),
                  "原始附着量（GU）", gauge.get("value"), "原始耐久字段", gauge.get("raw_durability")],
                 ["参数序号（零起始）", (projection.get("parameter") or {}).get("param_index"),
                  "参数标量", (projection.get("parameter") or {}).get("value")]]
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append(["完整来源与父链" if offset == 0 else "续", encoded[offset:offset + 15000]])
    return rows


def periodic_attack_rows(result: dict) -> list[list]:
    """Complete immutable source evidence for calibrated periodic contacts."""
    records = result.get("periodic_attack_compilations") or []
    if not records:
        return []
    rows = [["周期攻击来源", "创建与参数的结构关联，不证明角色动作原生启动；仅部分技能范围。"],
            ["执行方式", "数据库提供可证倍率、元素、附着与间隔；首击、次数、生命周期和共享身份按明确校准执行。"],
            ["编辑范围", "固定来源与校准改变后回App重新生成；本表不重演回调、附着或对象生命周期。"]]
    kinds = {"periodic_callback_attack": "周期回调攻击", "ability_start_attack": "独立启动攻击"}
    elements = {"cryo": "冰", "hydro": "水", "anemo": "风", "pyro": "火",
                "electro": "雷", "geo": "岩", "dendro": "草", "physical": "物理"}
    for record in records:
        rows += [[], ["动作档案", record.get("action_event_profile_id", ""), "资料状态",
                       "所选范围就绪" if record.get("status") == "ready" else "待完善"],
                 ["通道", "攻击类型", "元素", "原始附着量（GU）", "倍率", "来源周期（秒）"]]
        for channel in record.get("channels", []):
            projection = channel.get("projection") or {}
            timer = projection.get("timer") or {}
            rows.append([channel.get("channel_id", ""), kinds.get(channel.get("channel_kind"), "类型待核实"),
                elements.get((projection.get("element") or {}).get("value"), "未知"),
                (projection.get("gauge") or {}).get("value"), (projection.get("parameter") or {}).get("value"),
                timer.get("seconds") if timer.get("status") == "ready" else "启动需独立校准"])
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append(["完整创建、挂载、参数与未支持分支" if offset == 0 else "续", encoded[offset:offset + 15000]])
    return rows


def passive_source_rows(result: dict) -> list[list]:
    """Frozen inherent source, declared stat basis and actual lifecycle/sampling."""
    records = result.get("passive_compilations") or []
    if not records:
        return []
    rows = [["固有天赋来源", "普通冰水扩散增伤的有限投影，完整来源仅部分支持。"],
            ["属性口径", "逐来源保留属性契约：动态来源读取本次属性账的非额外精通；旧固定来源仅按已声明固定口径计算。" if any(r.get('attribute_evidence') for r in records) else "按明确声明将固定面板精通用于原文非额外精通；没有逆向识别匿名面板组成。"],
            ["取值与快照", "提供者在反应触发时取值，接收者自身快照决定其输出使用的增伤；扩散伤害不吃元素伤害加成。"],
            ["编辑范围", "来源系数由数据库取得且只读；培养、精通组成、场景或动作结构改变后回App重算。"]]
    names = {}
    for record in records:
        source = record.get("source") or {}
        title = source.get("descriptions", {}).get("name", {}).get("text") or "固有天赋"
        rows += [[], ["来源", title, "方案标识", record.get("id", "")],
                 ["执行范围", "声明范围内就绪" if record.get("status") == "ready" else "待完善", "完整来源", "部分"],
                 ["突破门槛判断", "声明条件达到门槛；不代表账号状态已核验"],
                 ["初始面板精通" if record.get('attribute_evidence') else "固定精通", (record.get("stat_basis") or {}).get("panel_value", ""), "依据", (record.get("stat_basis") or {}).get("source", "")]]
        if record.get('attribute_evidence'):
            rows.append(['动态取值','每次触发读取属性账的NONEXTRA；初始面板值不等于实际转换用值。完整依据与取样见来源和属性账。'])
        for rule in record.get("rules", []):
            names[rule["id"]] = rule["name_zh"]
            value = rule["effects"][0]["value"]
            rows.append(["自动规则", rule["name_zh"], "持续（秒）", rule["timing"]["duration"], "每点精通系数", value["factor"]])
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append(["完整来源与场景" if offset == 0 else "续", encoded[offset:offset + 15000]])
    rows += [[], ["时刻（秒）", "规则", "记录类别", "实际执行记录"]]
    labels = {"activation": "激活或续期", "expiry": "到期", "effect_applied": "收益参与伤害"}
    for kind, entries in (("生命周期", result.get("mechanics_trace", [])), ("触发取样", result.get("sampling_trace", []))):
        for row in entries:
            if row.get("rule_id") not in names:
                continue
            encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
            for offset in range(0, max(1, len(encoded)), 15000):
                rows.append([row.get("t", row.get("event_t", "")) if offset == 0 else "续", names[row["rule_id"]],
                    labels.get(row.get("type"), kind), encoded[offset:offset + 15000]])
    return rows


def reaction_benefit_rows(result: dict) -> list[list]:
    """Readonly source evidence and actual activations, without re-running reactions."""
    records = result.get("reaction_benefit_compilations") or []
    if not records:
        return []
    rows = [["反应收益来源", "普通冰水扩散原目标投影；完整来源仅部分支持。"],
            ["计算顺序", "父命中先结算，反应成立后刷新对应减抗，原目标扩散伤害读取新敌方收益。"],
            ["持有者条件", "新减抗要求持有者在场；已经生效的减抗持续计时，常驻扩散加成也可在后台适用。"],
            ["编辑范围", "来源、装备、场景或事件顺序改变后回App重算；本表不执行元素或收益状态机。"]]
    known_rules = {}
    for record in records:
        rows += [[], ["来源", record.get("binding_id", ""), "覆盖", "部分"],
                 ["执行状态", "声明范围内就绪" if record.get("status") == "ready" else "待补充"]]
        for rule in record.get("rules", []):
            known_rules[rule["id"]] = rule.get("name_zh", "反应减抗")
            rows.append(["自动规则", rule.get("name_zh", "反应减抗"), "持续（秒）", rule["timing"]["duration"],
                         "收益值", rule["effects"][0]["value"]])
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append(["完整来源与覆盖" if offset == 0 else "续", encoded[offset:offset + 15000]])
    rows += [[], ["时刻（秒）", "规则", "事件类型", "实际执行记录"]]
    names = {"activation": "激活或续期", "expiry": "到期", "effect_applied": "收益参与伤害"}
    for entry in result.get("mechanics_trace", []):
        if entry.get("rule_id") not in known_rules:
            continue
        encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(encoded)), 15000):
            rows.append([entry.get("t", "") if offset == 0 else "续", known_rules[entry["rule_id"]],
                         names.get(entry.get("type"), "机制记录"), encoded[offset:offset + 15000]])
    return rows


def action_event_rows(result: dict) -> list[list]:
    """One shared calibration record plus per-action emission mapping, no resimulation."""
    records = result.get("action_event_compilations") or []
    if not records:
        return []
    rows = [["动作派发来源", "一次校准档案复用至显式动作；校准声明不是数据库已证明的原生派发。"],
            ["支持范围", "部分技能范围；不自动增加动作、多轮、随机产球或未覆盖分支。"],
            ["编辑范围", "只读来源与实例快照。改档案、绑定、例外、动作或数据库来源后回App重新生成。"]]
    reported = {hit["id"] for hit in result.get("hits", [])}
    statuses = {"ready": "就绪", "pending": "待补充", "invalid": "无效"}
    for record in records:
        rows += [[], ["档案", record["profile_id"], "原型", record.get("prototype_hit_id", ""),
                      "状态", statuses.get(record.get("status"), "待补充")]]
        evidence = {key: value for key, value in record.items() if key != "generated"}
        text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(text)), 15000):
            rows.append(["共享档案与来源" if offset == 0 else "续", text[offset:offset + 15000]])
        multi = any("channel_id" in item for item in record.get("generated", []))
        rows.append(["动作", "技能定义", "派生命中", "时刻（秒）", "同刻次序", "声明命中", "已计入本窗口伤害", "实例证据"]
                    + (["攻击通道", "接触序号"] if multi else []))
        for item in record.get("generated", []):
            rows.append([item["action_id"], item["ability_id"], item["hit_id"], item["t"], item["order"],
                         "命中" if item["landed"] is True else "落空" if item["landed"] is False else "未知",
                         "是" if item["hit_id"] in reported else "否",
                         json.dumps(item, ensure_ascii=False, sort_keys=True)]
                        + ([item.get("channel_id", ""), item.get("ordinal", "")] if multi else []))
    return rows


def target_condition_rows(result: dict) -> list[list]:
    """Readonly per-hit state predicates and explicit overrides, never an Excel simulator."""
    if not result.get("fixed_effect_sources"):
        return []
    rows = [["目标条件收益", "逐次命中前取样；实验参考，非游戏真值。修改动作、目标或来源后须回App重新生成。"],
            ["来源有效性", "固定装备来源持续存在，无伪造持续时间；仅在经典命中的条件成立时加成。"],
            ["覆盖范围", "局部投影不能代表整个上游来源完整支持；未支持分支见来源回执。"]]
    if any(source.get("damage_scope") == "declared_damage_systems" for source in result["fixed_effect_sources"]):
        rows[0][1] = "按声明档案读取本击前目标状态；来源为Agent声明，不自动证明游戏机制完整。修改动作、目标或来源后回App重算。"
        rows[1][1] = "仅声明的角色、伤害体系、事件筛选及目标条件均满足时采用；各伤害公式仍按自身适用乘区消费。"
    for src in result["fixed_effect_sources"]:
        text = json.dumps(src, ensure_ascii=False, sort_keys=True)
        for offset in range(0, max(1, len(text)), 15000):
            rows.append([src.get("name_zh", "固定条件来源") if offset == 0 else "续", text[offset:offset + 15000]])
    rows.append(["时刻（秒）", "命中事件", "运行身份", "输入轮次", "来源", "收益", "目标", "自动条件",
                 "本轴微调", "实际采用", "原因", "收益数值", "目标状态快照", "条件树", "计入伤害"])
    names = {s["id"]: s["name_zh"] for s in result["fixed_effect_sources"]}
    reasons = {"missed": "未命中", "recipient_mismatch": "提供者不符", "damage_scope_mismatch": "伤害系统不适用",
               "filter_mismatch": "命中筛选不符", "unknown_target_state": "目标状态未知", "agent_excluded": "本轴明确排除",
               "agent_included": "本轴明确增补", "condition_true": "目标条件成立", "condition_false": "目标条件不成立"}
    for entry in result.get("target_condition_trace", []):
        truth = "未知或不适用" if entry["automatic_value"] is None else "成立" if entry["automatic_value"] else "不成立"
        rows.append([entry["t"], entry["event_id"], entry["run_id"], entry["cycle"],
                     names.get(entry["fixed_source_id"], entry["fixed_source_id"]), entry["effect_id"], entry.get("target_id"), truth,
                     {"include": "增补", "exclude": "排除", None: "无"}.get(entry.get("override")),
                     "是" if entry["applied"] else "否", reasons.get(entry["reason"], entry["reason"]), entry["value"],
                     json.dumps(entry.get("target_sample"), ensure_ascii=False, sort_keys=True),
                     json.dumps(entry["predicate"], ensure_ascii=False, sort_keys=True), "是" if entry["reported_damage"] else "否"])
    return rows


def elemental_state_rows(result: dict) -> list[list]:
    """Immutable experimental trace; never recompute gauge state in a sheet."""
    elemental = result.get("elemental") or {}
    if elemental.get("ruleset") != _FREEZE_RULESET:
        return []
    contract, acceptance = elemental.get("reference_contract") or {}, elemental.get("reference_acceptance") or {}
    rows = [["元素状态流水", "实验参考；不是游戏真值。只读计算快照，不重演冻结微分方程。"],
            ["规则集", elemental["ruleset"]],
            ["证据层级", elemental.get("evidence_tier", "未提供")],
            ["参考契约", contract.get("contract_id", ""), contract.get("contract_fingerprint", "")],
            ["范围确认", acceptance.get("acknowledged", ""), acceptance.get("source", ""),
             acceptance.get("contract_id", ""), acceptance.get("contract_fingerprint", "")],
            ["状态含义", "C=普通冰底，H=普通水底，F=冻结层，x=冻结历史秒数；缺少状态快照时留空，不补零。"],
            ["使用范围", "初态、附着、接触或参考契约改变后须回 App 重新计算并重新生成。层消费不另计伤害。"]]
    rows += [["模型来源", source] for source in contract.get("sources", [])]
    rows += [["模型假设", item] for item in contract.get("assumptions", [])]
    for kind in ("source_compilations", "reaction_damage_source_compilations"):
        for record in elemental.get(kind, []):
            identity = record.get("profile_id", record.get("reaction_type", ""))
            rows.append(["数据库来源确认", identity, record.get("status", ""), record.get("binding_basis", ""),
                         (record.get("snapshot") or {}).get("snapshot_id", ""), record.get("compilation_fingerprint", "")])
            details = {key: record[key] for key in ("source", "required_confirmation", "profile_fingerprint") if key in record}
            for index, profile in enumerate(record.get("profiles", [])):
                details[f"profile[{index}]"] = {key: profile[key] for key in ("selection", "sources", "content_fingerprint") if key in profile}
            for key, value in details.items():
                text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for offset in range(0, len(text), 30000):
                    rows.append(["数据库证据", identity, key, text[offset:offset + 30000]])
    rows += [["时序规则", contract.get("time", "")], []]
    header = ["记录类型", "时间（秒）", "事件标识", "动作标识", "角色标识", "目标标识", "原因或结果", "反应包",
              "前C（GU）", "前H（GU）", "前F（GU）", "前x（秒）", "前冻结衰减（GU/秒）",
              "后C（GU）", "后H（GU）", "后F（GU）", "后x（秒）", "后冻结衰减（GU/秒）",
              "反应标识", "反应类型", "反应元素", "消费层", "层前量（GU）", "层后量（GU）", "层消费（GU）",
              "剩余触发量（GU）", "允许", "状态", "来源"]
    rows.append(header)
    kinds = {"elemental_application": "元素附着判定", "elemental_state_transition": "自然状态推进",
             "reaction_damage_attempt": "反应伤害判定"}
    reasons = {"ordinary_aura_expired": "普通元素底自然到期", "frozen_expired": "冻结层自然到期",
               "freeze_history_recovered": "冻结历史恢复为零"}
    elements = {"cryo": "冰", "hydro": "水", "anemo": "风"}

    def state_values(state):
        if not isinstance(state, dict) or "ordinary_aura" not in state:
            return [""] * 5
        aura, frozen = state.get("ordinary_aura") or {}, state.get("frozen") or {}
        return [aura.get("remaining_gu", "") if aura.get("element") == element else 0 for element in ("cryo", "hydro")] + [
            frozen.get("remaining_gu", 0), state.get("freeze_history_seconds", ""), state.get("freeze_decay_gu_per_second", "")]

    # Preserve engine ordering, including natural expiry before same-time hits.
    for row in elemental.get("trace", []):
        reactions = row.get("reactions", [])
        identity = [kinds.get(row.get("type"), row.get("type", "")), row.get("t", ""), row.get("event_id", ""),
                    row.get("action_id", ""), row.get("character", ""), row.get("target_id", ""),
                    reasons.get(row.get("reason"), row.get("reason", row.get("outcome", ""))), row.get("package_id", "")]
        reaction_ids = "、".join(r.get("id", "") for r in reactions) or row.get("reaction_id", "")
        reaction_types = "、".join({"freeze": "冻结", "swirl": "扩散"}.get(r.get("type"), r.get("type", "")) for r in reactions)
        reaction_elements = "、".join(elements.get(r.get("reaction_element"), r.get("reaction_element", "")) for r in reactions)
        if row.get("type") == "reaction_damage_attempt":
            reaction_types = {"freeze": "冻结", "swirl": "扩散"}.get(row.get("reaction_type"), row.get("reaction_type", ""))
            reaction_elements = elements.get(row.get("reaction_element"), row.get("reaction_element", ""))
        rows.append([*identity, *state_values(row.get("before")), *state_values(row.get("after")),
                     reaction_ids, reaction_types, reaction_elements, *([""] * 5),
                     row.get("allowed", ""), row.get("status", ""), row.get("source", "")])
        for layer in row.get("layer_consumptions", []):
            rows.append(["层消费（不另计伤害）", *identity[1:], *([""] * 10), reaction_ids, reaction_types,
                         elements.get(layer.get("reaction_element"), layer.get("reaction_element", "")),
                         {"frozen": "冻结层", "cryo": "普通冰底", "hydro": "普通水底"}.get(layer.get("layer"), layer.get("layer", "")),
                         *[layer.get(key, "") for key in ("before_gu", "after_gu", "consumed_gu", "remaining_trigger_gu")],
                         "", "只读明细", row.get("source", "")])
    for name in ("input_window_state", "final_checked_state"):
        state = elemental.get(name)
        if isinstance(state, dict):
            rows.append(["输入窗口末态" if name == "input_window_state" else "最后检查态", state.get("t", ""), "", "", "",
                         state.get("target_id", ""), "只读快照", "", *([""] * 5), *state_values(state), *([""] * 9), "只读", "引擎状态回执"])
    return rows


# ---------- 最小 xlsx 写出器 ----------

def _col_name(i: int) -> str:
    name = ""
    while True:
        i, r = divmod(i, 26)
        name = chr(65 + r) + name
        if i == 0:
            return name
        i -= 1


def _display_width(text: str) -> int:
    return max((sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in line)
                for line in text.splitlines()), default=0)


def readable_equipment_rows(rows: list[list]) -> tuple[list[list], list[list]]:
    """Keep the equipment view compact; retain verbose source JSON in ordered chunks."""
    primary, sources = [], [["角色", "项目与属性", "原始资料", "原单元格", "分段"]]
    for ri, row in enumerate(rows, 1):
        shown = list(row)
        for ci, value in enumerate(row):
            if not isinstance(value, str) or len(value) <= 160 or value[:1] not in ('{', '['):
                continue
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                continue
            first = len(sources)+1
            for offset in range(0, len(value), 480):
                sources.append([row[0], ' / '.join(str(v) for v in row[1:3]), value[offset:offset+480],
                                f'角色与装备!{_col_name(ci)}{ri}', offset//480+1])
            locator = f'完整资料见「装备资料」第 {first}—{len(sources)} 行'
            shown[ci] = f'输入指纹 {decoded["input_fingerprint"]}；{locator}' if isinstance(decoded, dict) and 'input_fingerprint' in decoded else locator
        primary.append(shown)
    return primary, sources


def _sheet_xml(rows: list[list], *, readonly: bool = False, name: str = '') -> str:
    count = max((len(row) for row in rows), default=0)
    widths = [18.0] * count
    for row in rows:
        for ci, val in enumerate(row):
            if isinstance(val, str):
                widths[ci] = max(widths[ci], min(64, _display_width(val)+3))
    if name == '角色与装备':
        widths[:5] = [18, 25, 36, 42, 72][:count]
    elif name == '汇总' and count >= 2:
        widths[:2] = [48, 24]
    views = '<sheetViews><sheetView workbookViewId="0" showGridLines="0">'
    if len(rows) > 25 and name != '汇总':
        views += '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
    views += '</sheetView></sheetViews>'
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           views, '<sheetFormatPr defaultRowHeight="20"/>', '<cols>',
           *(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1)),
           '</cols>',
           '<sheetData>']
    for ri, row in enumerate(rows, start=1):
        lines = max((sum(max(1, math.ceil(_display_width(line)/max(1, widths[ci]-3)))
                         for line in str(val).splitlines())
                     for ci, val in enumerate(row) if isinstance(val, str)), default=1)
        height = min(409, max(22, 16*lines+6))
        out.append(f'<row r="{ri}" ht="{height}" customHeight="1">')
        for ci, val in enumerate(row):
            if val is None or val == "":
                continue
            ref = f"{_col_name(ci)}{ri}"
            if isinstance(val, bool):
                out.append(f'<c r="{ref}" s="1" t="b"><v>{int(val)}</v></c>')
            elif isinstance(val, (int, float)):
                style = 4 if isinstance(val, int) else 5 if val and (abs(val) < .0001 or abs(val) >= 1e12) else 3
                out.append(f'<c r="{ref}" s="{style}"><v>{val!r}</v></c>')
            else:
                style = 2 if ri == 1 or (name == '汇总' and row[0] in ('按角色', '按技能', '计算组成', '口径假设')) else 1
                out.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{spreadsheet_text(val, location=f"{name}!{ref}")}</t></is></c>')
        out.append('</row>')
    out.append('</sheetData>')
    if readonly:
        out.append('<sheetProtection sheet="1" objects="1" scenarios="1"/>')
    out.append('</worksheet>')
    return "".join(out)


def _write_xlsx(path: Path | BinaryIO, sheets: list[tuple[str, list[list]]]) -> None:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{overrides}</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    sheet_tags = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheets, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{sheet_tags}</sheets></workbook>'
    )
    wb_rels_inner = "".join(
        f'<Relationship Id="rId{i}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    ) + (
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{wb_rels_inner}</Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.0000"/>'
        '<numFmt numFmtId="165" formatCode="0.0000E+00"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font>'
        '<font><b/><sz val="11"/><name val="Microsoft YaHei"/><color rgb="FF243247"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE9EDF3"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
        '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        for i, (name, rows) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, name=name, readonly=name in (
                "外部补充伤害", "反应资源联动", "共享资源流水", "资源累计流水", "资源读数流水", "累计输出快照",
                "属性取样账", "属性回调执行", "资源阈值观察", "阈值收益执行", "生命周期流水", "技能参数来源", "面板取样", "通用机制来源",
                "伤害报告证明", "元素状态流水", "动作资源来源", "产球来源", "有限次数收益",
                "动作派发来源", "攻击元信息来源", "周期攻击来源", "反应收益来源", "反应触发精通",
                "固有天赋来源", "目标条件收益", "面板已含来源", "静态动作来源", "运行时动作接纳",
                "临时公共资料", "计算审计", "结果回执",
            )))


# ---------- 导出入口 ----------

@verification_scope
def export_result(result: dict, fmt: str, path: str | Path, *, display: dict | None = None,
                  receipt: dict | None = None, candidate_session=None,
                  _session=None) -> Path:
    """按格式写出结果文件，返回实际路径。未知格式抛 ValueError。"""
    from genshin_app.equipment.provisional_entities import (
        validate_current_session_usage_receipt,
    )
    validate_current_session_usage_receipt(
        result, receipt or {}, candidate_session,
    )
    formal_audit = formal_calculation_audit_payload(result, receipt, _session=_session)
    from genshin_app.equipment.attribute_receipt import (
        validate_attribute_result_receipt,
    )
    validate_attribute_result_receipt(result, receipt or {})
    from genshin_app.equipment.counter_frame_receipt import (
        validate_counter_frame_result_receipt,
    )
    validate_counter_frame_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.equipment.resource_reaction_receipt import (
        validate_resource_reaction_result_receipt,
    )
    validate_resource_reaction_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.static_inclusion_reporting import has_static_inclusion_signal
    if has_static_inclusion_signal(result, receipt):
        from genshin_app.equipment.static_inclusion_receipt import (
            validate_static_inclusion_result_receipt,
        )
        validate_static_inclusion_result_receipt(result, receipt or {})
    from genshin_app.equipment.external_damage import (
        validate_external_damage_result_receipt,
    )
    validate_external_damage_result_receipt(result, receipt or {})
    if result.get("external_damage") and fmt == "csv":
        raise ValueError("外部补充伤害需要完整来源与分项汇总，请导出 JSON 或 XLSX；CSV 不支持。")
    from genshin_app.equipment.mechanism_receipt import (
        validate_mechanism_result_receipt,
    )
    validate_mechanism_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.equipment.snapshot_capture_receipt import (
        validate_snapshot_capture_result_receipt,
    )
    validate_snapshot_capture_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.equipment.action_capacity_receipt import (
        validate_action_capacity_result_receipt,
    )
    validate_action_capacity_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.equipment.action_admission_receipt import (
        validate_action_admission_result_receipt,
    )
    validate_action_admission_result_receipt(result, receipt or {}, _session=_session)
    from genshin_app.equipment.damage_report_window_receipt import (
        validate_window_result,
    )
    validate_window_result(result, receipt or {}, _session=_session)
    if "damage_reporting" in result or "damage_reporting" in (receipt or {}) or result.get("hits") == [] and "damage_report_window" not in result:
        from genshin_app.equipment.damage_reporting_receipt import (
            validate_empty_damage_result_receipt,
        )
        validate_empty_damage_result_receipt(result, receipt or {})
        if fmt == "csv":
            raise ValueError("已验证零伤结果需要伤害报告证明，请导出 JSON 或 XLSX；CSV 不支持。")
    from genshin_app.equipment.files import file_sha
    from genshin_app.output_policy import (
        publish_ordinary_files,
        validate_database_outputs,
    )
    from genshin_app.paths import default_data_dir
    p = Path(path)
    database = default_data_dir() / "genshin.db"
    validate_database_outputs([p], database)
    expected_sha = file_sha(p)
    provisional_receipt = provisional_usage_receipt(result, receipt)
    bound_receipt = isinstance(receipt, dict) and "result_fingerprint" in receipt
    if fmt == "json":
        payload = result
        if provisional_receipt is not None:
            payload = {
                "result": result,
                "export_metadata": {
                    "result_fidelity": PROVISIONAL_RESULT_FIDELITY,
                    "human_label": PROVISIONAL_RESULT_LABEL,
                    "scope": "current_plan_only",
                    "database_authority": False,
                },
            }
        if bound_receipt:
            payload = {**payload, "result_receipt": copy.deepcopy(receipt)}
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    elif fmt == "csv":
        rows = hit_rows(result, display)
        if provisional_receipt is not None:
            rows[0] += [
                "result_fidelity", "result_notice",
                "provisional_entity_usage_json",
            ]
            encoded = json.dumps(
                provisional_receipt, ensure_ascii=False, separators=(",", ":"),
            )
            for row in rows[1:1 + len(result["hits"])]:
                row += [
                    PROVISIONAL_RESULT_FIDELITY, PROVISIONAL_RESULT_LABEL,
                    encoded,
                ]
        if formal_audit is not None:
            rows[0].append("formal_calculation_audit_json")
            encoded = json.dumps(
                formal_audit, ensure_ascii=False, separators=(",", ":"),
            )
            for index, row in enumerate(rows[1:1 + len(result["hits"])]):
                row.append(encoded if index == 0 else "")
        elif bound_receipt:
            rows[0].append("result_receipt_json")
            encoded = json.dumps(receipt, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            for index, row in enumerate(rows[1:1 + len(result["hits"])]):
                row.append(encoded if index == 0 else "")
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows(rows)
        content = buffer.getvalue().encode("utf-8-sig")
    elif fmt == "xlsx":
        sheets = [
            ("汇总", summary_rows(result, display)),
            ("逐事件明细", hit_rows(result, display)),
            ("增益覆盖率", coverage_rows(result, display)),
        ]
        external = external_damage_rows(result, display)
        if external:
            sheets.append(("外部补充伤害", external))
        proof = damage_reporting_rows(result)
        if proof:
            sheets.append(("伤害报告证明", proof))
        if result.get("rotation_check"):
            from genshin_app.rotation_check import check_sheets
            sheets.extend(check_sheets(result["rotation_check"], display))
        elemental = elemental_state_rows(result)
        if elemental:
            sheets.append(("元素状态流水", elemental))
        resources = action_resource_rows(result)
        if resources:
            sheets.append(("动作资源来源", resources))
        generation = energy_generation_rows(result)
        if generation:
            sheets.append(("产球来源", generation))
        limited = limited_hit_benefit_rows(result)
        if limited:
            sheets.append(("有限次数收益", limited))
        mechanisms = mechanism_rows(result)
        if mechanisms:
            sheets.append(("通用机制来源", mechanisms))
        captures = snapshot_capture_rows(result)
        if captures:
            sheets.append(("面板取样", captures))
        pools = shared_pool_rows(result)
        if pools:
            sheets.append(("共享资源流水", pools))
        counters = resource_counter_rows(result)
        if counters:
            sheets.append(('资源累计流水', counters))
        views = resource_counter_view_rows(result)
        if views:
            sheets.append(('资源读数流水', views))
        from genshin_app.reaction_resource_reporting import reaction_resource_sheet
        reaction_rows = reaction_resource_sheet(result)
        if reaction_rows:
            sheets.append(('反应资源联动', reaction_rows))
        from genshin_app.counter_frame_reporting import counter_frame_rows
        frames = counter_frame_rows(result.get('resource_counter_views'))
        if frames:
            sheets.append(('累计输出快照', frames))
        attributes = attribute_ledger_rows(result)
        if attributes:
            sheets.append(('属性取样账', attributes))
        callbacks = attribute_callback_rows(result)
        if callbacks:
            sheets.append(('属性回调执行', callbacks))
        from genshin_app.resource_threshold_reporting import FIELDS, TITLES
        for field, title in zip(FIELDS, TITLES):
            threshold_rows = resource_threshold_rows(result, field)
            if threshold_rows:
                sheets.append((title, threshold_rows))
        states = state_runtime_rows(result)
        if states:
            sheets.append(('生命周期流水', states))
        parameters = hit_parameter_rows(result)
        if parameters:
            sheets.append(('技能参数来源', parameters))
        dispatch = action_event_rows(result)
        if dispatch:
            sheets.append(("动作派发来源", dispatch))
        metadata = attack_metadata_rows(result)
        if metadata:
            sheets.append(("攻击元信息来源", metadata))
        periodic = periodic_attack_rows(result)
        if periodic:
            sheets.append(("周期攻击来源", periodic))
        benefits = reaction_benefit_rows(result)
        if benefits:
            sheets.append(("反应收益来源", benefits))
        from genshin_app.reaction_em_reporting import reaction_em_rows
        reaction_em = reaction_em_rows(result)
        if reaction_em:
            sheets.append(("反应触发精通", reaction_em))
        from genshin_app.static_inclusion_reporting import static_inclusion_rows
        static_rows = static_inclusion_rows(result)
        if static_rows:
            sheets.append(('面板已含来源', static_rows))
        from genshin_app.action_capacity_reporting import capacity_sheets
        sheets.extend(capacity_sheets(result, display))
        from genshin_app.action_admission_reporting import action_admission_sheets
        sheets.extend(action_admission_sheets(result, display))
        passives = passive_source_rows(result)
        if passives:
            sheets.append(("固有天赋来源", passives))
        conditions = target_condition_rows(result)
        if conditions:
            sheets.append(("目标条件收益", conditions))
        provisional_rows = provisional_entity_rows(result, receipt)
        if provisional_rows:
            sheets.append(("临时公共资料", provisional_rows))
        audit_rows = formal_audit_payload_rows(formal_audit)
        if audit_rows:
            sheets.append(("计算审计", audit_rows))
        elif bound_receipt:
            sheets.append(("结果回执", saved_result_receipt_rows(receipt)))
        buffer = io.BytesIO()
        _write_xlsx(buffer, sheets)
        content = buffer.getvalue()
    else:
        raise ValueError(f"未知导出格式 {fmt!r}（支持 {EXPORT_FORMATS}）")
    publish_ordinary_files([(p, content)], database, overwrite=True, expected_shas={p: expected_sha})
    return p
