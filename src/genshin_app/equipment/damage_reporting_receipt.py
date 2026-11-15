"""Offline proof of an explicit, completely missed damage window.

The checks bind the actual execution ledger to the compiled input. They neither
run the simulation again nor authenticate a completely rewritten evidence set.
"""
from __future__ import annotations

import json
import math
import re

from .files import fingerprint, input_view

CONTRACT = "verified-empty-damage-v1"
MESSAGE = "零伤害窗口的接触覆盖、执行流水或来源回执不一致，请回 App 重新计算。"
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _reject():
    raise ValueError(MESSAGE)


def _bounded(value):
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf8"))
        if size > MAX_EVIDENCE_BYTES:
            raise ValueError("零伤害窗口的完整证明超过8MiB，请缩短输入窗口；未截断交付。")
    return value


def _same(left, right):
    if fingerprint(left) != fingerprint(right):
        _reject()


def _needed(result, receipt):
    return ("damage_reporting" in result or "damage_reporting" in receipt
            or result.get("hits") == [] and "damage_report_window" not in result)


def _ledger(result):
    elemental = result.get("elemental") or {}
    return _bounded({
        "rotation_check": result.get("rotation_check"),
        "mechanics": result.get("mechanics"),
        "mechanics_status": result.get("mechanics_status"),
        "mechanics_issues": result.get("mechanics_issues"),
        "mechanics_trace": result.get("mechanics_trace"),
        "sampling_trace": result.get("sampling_trace"),
        "elemental_trace": elemental.get("trace"),
        "elemental_input_window_state": elemental.get("input_window_state"),
    })


def _proof(result, report):
    return {
        "contract": CONTRACT,
        "coverage_fingerprint": fingerprint(result["damage_reporting"]),
        "execution_ledger_fingerprint": fingerprint(_ledger(result)),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "execution_rotation_model_fingerprint": fingerprint(report["damage_reporting_execution_rotation_model"]),
        "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
    }


def make_empty_damage_receipt(plan, result, report):
    if plan.get("plan_version") != 3 or not result.get("damage_reporting"):
        _reject()
    return _proof(result, report)


def _validate_result(result, receipt):
    from genshin_engine.damage_reporting import classify_verified_empty_damage

    coverage = result.get("damage_reporting")
    proof = receipt.get("damage_reporting")
    if not isinstance(coverage, dict) or not isinstance(proof, dict):
        _reject()
    _bounded(coverage)
    if set(proof) != {
            "contract", "coverage_fingerprint", "execution_ledger_fingerprint",
            "compiled_mechanics_model_fingerprint", "execution_rotation_model_fingerprint",
            "mechanics_input_template_fingerprint"} or proof["contract"] != CONTRACT:
        _reject()
    if any(not isinstance(value, str) or not _SHA.fullmatch(value)
           for key, value in proof.items() if key != "contract"):
        _reject()
    if coverage.get("contract") != CONTRACT or result.get("hits") != []:
        _reject()
    summary = result.get("summary", {})
    for key in ("total_damage", "dps"):
        value = summary.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value != 0:
            _reject()
    if summary.get("by_skill") != {} or summary.get("by_character") != {}:
        _reject()
    mechanics, rotation = result.get("mechanics"), result.get("rotation_check")
    if not isinstance(mechanics, dict) or not isinstance(rotation, dict):
        _reject()
    if (mechanics.get("complete_first_cycle") is not True
            or result.get("mechanics_status") not in ("passed", "unproven")
            or mechanics.get("execution_status") != result["mechanics_status"]
            or rotation.get("status") != result["mechanics_status"]):
        _reject()
    interpretation = rotation.get("damage_interpretation") or {}
    if (interpretation.get("kind") != "planned_damage"
            or interpretation.get("complete_first_cycle") is not True
            or type(interpretation.get("reported_hit_count")) is not int
            or interpretation["reported_hit_count"] != 0):
        _reject()
    scope = coverage.get("scope")
    if not isinstance(scope, dict) or type(scope.get("end_tick")) is not int or scope["end_tick"] <= 0:
        _reject()
    contacts = coverage.get("contacts")
    if not isinstance(contacts, list) or not contacts or len(contacts) > 40000:
        _reject()
    keys = {"event_id", "run_id", "action_id", "tick", "order", "report", "landed", "outcome"}
    if any(not isinstance(row, dict) or set(row) != keys or row["outcome"] != "missed" for row in contacts):
        _reject()
    # The result-only export has no source model. All actual candidate rows must
    # still be covered; the complete validator additionally reassembles the model.
    if any(row.get("type") == "elemental_application" for row in rotation.get("event_log", [])):
        _reject()
    candidates = [{key: value for key, value in row.items() if key != "outcome"} for row in contacts]
    rebuilt = classify_verified_empty_damage(
        candidates, rotation, period_ticks=scope["end_tick"],
        mechanics_model={"application_events": []}, issues=result.get("mechanics_issues", []),
        reported_damage_events=0)
    _same(coverage, rebuilt)
    if proof["coverage_fingerprint"] != fingerprint(coverage) or proof["execution_ledger_fingerprint"] != fingerprint(_ledger(result)):
        _reject()
    if receipt.get("result_fingerprint") != fingerprint(result):
        _reject()


def validate_empty_damage_result_receipt(result, receipt):
    if not _needed(result, receipt):
        return
    try:
        _validate_result(result, receipt)
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_empty_damage_receipt(plan, result, report, receipt):
    needed = (_needed(result, receipt) or "damage_reporting" in report
              or "damage_report_window" not in result and plan.get("plan_version") == 3 and isinstance(plan.get("resolved_template"), dict)
              and plan["resolved_template"].get("hits") == [])
    if not needed:
        return
    from genshin_engine.damage_reporting import (
        _ticks,
        assemble_damage_candidates,
        classify_verified_empty_damage,
    )

    try:
        _validate_result(result, receipt)
        if plan.get("plan_version") != 3 or receipt.get("input_fingerprint") != fingerprint(input_view(plan)):
            _reject()
        _same(report.get("damage_reporting"), result["damage_reporting"])
        _same(receipt["damage_reporting"], _proof(result, report))
        execution = report["damage_reporting_execution_rotation_model"]
        model, template = report["mechanics_model"], report["mechanics_input_template"]
        # Verify the ordinary binding operation as well as its stored hash.
        from genshin_app.rotation_check import resolve_rotation_model
        expected, _ = resolve_rotation_model(template, report.get("compiled_rotation_model", plan["rotation_model"]))
        _same(execution, expected)
        if report.get("action_event_execution_rotation_model") is not None:
            _same(execution, report["action_event_execution_rotation_model"])
        candidates = assemble_damage_candidates(template, execution, model)
        rebuilt = classify_verified_empty_damage(
            candidates, result["rotation_check"], period_ticks=_ticks(execution["period"]),
            mechanics_model=model, issues=result.get("mechanics_issues", []), reported_damage_events=0)
        _same(result["damage_reporting"], rebuilt)
        resolved = plan["resolved_template"]
        if resolved.get("hits") != [] or _ticks(resolved["loop_duration"]) != _ticks(execution["period"]):
            _reject()
        if receipt.get("output_fingerprint") != fingerprint(resolved):
            _reject()
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def damage_reporting_contract():
    return {
        "contract": CONTRACT,
        "scope": "完整输入窗口内非空、计入报告的实际接触全部明确未命中；来源及执行校验仍有效",
        "unsupported": ["空草稿", "仅隐藏伤害", "独立无伤附着", "失败或未验证前缀"],
        "zero_is_not_cycle_proof": True,
        "exports": {"json": True, "xlsx": True, "formula_xlsx": True, "deliver": True,
                    "csv": False, "standalone_resolved_template": False},
        "max_evidence_bytes": MAX_EVIDENCE_BYTES,
        "change_contact_or_axis": "回App重新计算；面板编辑不会重新判定命中",
    }
