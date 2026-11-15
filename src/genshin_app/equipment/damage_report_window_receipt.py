"""Independent replay of reporting scope; never repairs an old calculation.

The full input context is retained once, not once per hit. This verifies saved
input consistency, not authenticity of an entirely rewritten evidence set.
"""
from __future__ import annotations

import copy
import math

from .files import fingerprint, input_view
from .verification_scope import verification_scope

CONTRACT = "damage-report-window-receipt-v1"
MESSAGE = "DPS统计截止、完整动作验证或来源绑定不一致，请在App重新计算。"


def require_window_plan_support(plan):
    """The legacy scalar calculator cannot provide a causal window receipt."""
    model = plan.get("rotation_model") if isinstance(plan, dict) else None
    if (isinstance(plan, dict) and plan.get("plan_version") in (1, 2)
            and isinstance(model, dict) and model.get("damage_report") is not None):
        from .schema import PlanError
        raise PlanError(
            "这份旧格式方案包含统计截止设置，但旧计算路径不会执行伤害裁剪。"
            "先用 enable-mechanics 保留原输入并升级到v3，核对动作与命中绑定后重新计算；"
            "普通不带此设置的v2方案仍可计算。本次尚未执行。",
            "damage_report_requires_causal_execution",
            {"path": "/rotation_model/damage_report", "plan_version": plan["plan_version"],
             "execution_state": "not_started", "supported_plan_versions": [3],
             "recovery": {"action": "enable_mechanics_and_review_bindings", "retry_same_request": False,
                "cli": "python -m genshin_app equipment enable-mechanics --plan input.plan.json --output migrated.plan.json",
                "python": "genshin_app.equipment.mechanics.enable_mechanics(plan)",
                "next": "保留现有damage_report；查看迁移后mechanics_model与动作/命中绑定，补充真实缺项，再equipment calc。迁移不是计算通过证明。"}})


def _same(left, right):
    if fingerprint(left) != fingerprint(right):
        raise ValueError(MESSAGE)


def _members(result):
    return {key: [{"id": h["id"], "t": h["t"]} for h in result[key]]
            for key in ("hits", "damage_report_audit_hits")}


def _proof(result):
    return {"contract": CONTRACT,
            "execution_fingerprint": fingerprint(result["damage_report_execution"]),
            "window_fingerprint": fingerprint(result["damage_report_window"]),
            "reported_members_fingerprint": fingerprint(_members(result))}


def _validate_summary(result):
    total, groups = 0., {"by_character": {}, "by_skill": {}}
    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(MESSAGE)
        return value
    def equal(left, right):
        if not math.isclose(number(left), number(right), rel_tol=1e-12, abs_tol=1e-8):
            raise ValueError(MESSAGE)
    for hit in result["hits"]:
        amount = number(hit["expected"])
        total += amount
        for key, identity in (("by_character", hit["character"]), ("by_skill", hit["skill"])):
            groups[key][identity] = groups[key].get(identity, 0.) + amount
    summary = result["summary"]
    equal(total, summary["total_damage"])
    for key, expected in groups.items():
        if set(expected) != set(summary[key]):
            raise ValueError(MESSAGE)
        for identity, amount in expected.items():
            equal(amount, summary[key][identity])
    window = result["damage_report_window"]
    if window["status"] == "complete":
        equal(total / number(window["end"]), summary["dps"])
    elif summary["dps"] is not None:
        raise ValueError(MESSAGE)


def make_window_receipt(plan, result, report, model):
    if "damage_report_window" not in result:
        return None
    if plan.get("plan_version") != 3 or model is None or "damage_report" not in model:
        raise ValueError(MESSAGE)
    result["damage_report_execution"] = copy.deepcopy({
        "template": report["mechanics_input_template"], "rotation_model": model,
        "mechanics_model": report["mechanics_model"], "matches": report["mechanics_matches"]})
    return _proof(result)


@verification_scope
def validate_window_result(result, receipt, *, _session=None):
    needed = any(key in result for key in ("damage_report_window", "damage_report_execution")) or "damage_report_window" in receipt
    if not needed:
        return
    try:
        _same(receipt["damage_report_window"], _proof(result))
        result_digest = fingerprint(result)
        _same(receipt["result_fingerprint"], result_digest)
        # Recheck both objects by content before sharing the already-verified
        # window inside this operation. A later edit cannot borrow the proof.
        identity = (result_digest, fingerprint(receipt))
        if identity in _session._verified_windows:
            return
        execution = result["damage_report_execution"]
        if "damage_report" not in execution["rotation_model"]:
            raise ValueError(MESSAGE)
        actual = _session.replay(execution, _result_fields=(
            "damage_report_window", "hits", "damage_report_audit_hits", "summary",
        ))
        expected = actual.get("result")
        if expected is None or actual.get("resolved_template") is None:
            raise ValueError(MESSAGE)
        _same(expected.get("damage_report_window"), result["damage_report_window"])
        _same(_members(expected), _members(result))
        from .calculation_overrides.computed_values import (
            validate_report_numeric_baselines,
        )
        validate_report_numeric_baselines(result, expected)
        _validate_summary(result)
        from .calculation_overrides.computed_values import (
            validate_computed_value_result_receipt,
        )
        validate_computed_value_result_receipt(result, receipt, _session=_session)
        _same(fingerprint(actual["resolved_template"]), receipt["output_fingerprint"])
        # A zero scope is legitimate only when this same replay produced no
        # reported hits. It is not the older "all contacts missed" contract.
        if result["hits"] == []:
            for key in ("total_damage", "dps", "by_skill", "by_character"):
                _same(expected["summary"][key], result["summary"][key])
        _session._verified_windows.add(identity)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_window_calculation(plan, result, report, receipt, *, _session=None):
    require_window_plan_support(plan)
    declared = (plan.get("rotation_model") or {}).get("damage_report")
    if declared is None and not any(key in result for key in ("damage_report_window", "damage_report_execution")) and "damage_report_window" not in receipt:
        return
    validate_window_result(result, receipt, _session=_session)
    execution = result.get("damage_report_execution")
    if not isinstance(execution, dict):
        raise ValueError(MESSAGE)  # noqa: TRY004 - public verification errors retain ValueError ABI
    _same(receipt["input_fingerprint"], fingerprint(input_view(plan)))
    _same(execution["rotation_model"]["damage_report"], declared)
    _same(execution["template"], report["mechanics_input_template"])
    _same(execution["mechanics_model"], report["mechanics_model"])
    _same(execution["matches"], report["mechanics_matches"])
    from genshin_app.rotation_check import resolve_rotation_model
    expected, _ = resolve_rotation_model(execution["template"], report.get("compiled_rotation_model", plan["rotation_model"]))
    _same(execution["rotation_model"], expected)
