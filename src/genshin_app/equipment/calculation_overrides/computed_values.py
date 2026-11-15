"""Reviewed per-event fixed values with aggregate re-derivation.

This is deliberately a post-formula event overlay, not a result editor.  It can
replace one bound damage event's numeric value, then derives character, skill,
total and DPS aggregates from the complete effective hit ledger.  It cannot
write elemental, Buff, resource or lifecycle state.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from ..damage_report_scope import scoped_hits
from ..verification_scope import verification_scope
from .schema import OverrideDeclarationError, fingerprint

CONTRACT = "calculation-computed-value-execution-v1"
SCOPED_CONTRACT = "calculation-computed-value-execution-v2"
RESULT_FIELD = "calculation_computed_value_overrides"
WINDOW_RECEIPT_FIELD = "calculation_computed_value_window"


def _number(value: Any, message: str) -> float | int:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise OverrideDeclarationError("override_computed_value_invalid", message)
    return value


def _damage_aggregates(result: dict[str, Any]) -> dict[str, Any]:
    hits = result.get("hits")
    if not isinstance(hits, list):
        raise OverrideDeclarationError(
            "override_computed_result_invalid", "引擎结果缺少逐事件伤害账"
        )
    total = 0.0
    by_skill: dict[str, float] = {}
    by_character: dict[str, float] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "逐事件伤害账成员无效"
            )
        value = _number(hit.get("expected"), "逐事件伤害不是有限非负值")
        character = hit.get("character")
        skill = hit.get("skill")
        if not isinstance(character, str) or not isinstance(skill, str):
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "逐事件伤害缺少角色或技能身份"
            )
        total += value
        if not math.isfinite(total):
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "逐事件伤害汇总溢出"
            )
        by_character[character] = by_character.get(character, 0.0) + value
        by_skill[skill] = by_skill.get(skill, 0.0) + value
    return {
        "total_damage": total,
        "by_character": by_character,
        "by_skill": by_skill,
    }


def _window_evidence(loop_duration: Any, *, dps_derived: bool, report=None) -> dict[str, Any]:
    seconds = None
    if type(loop_duration) in (int, float) and math.isfinite(loop_duration):
        seconds = _number(loop_duration, "固定单事件值重算需要有效窗口")
    if dps_derived and (seconds is None or seconds == 0):
        raise OverrideDeclarationError(
            "override_computed_result_invalid", "固定单事件值重算窗口必须大于零"
        )
    if report is not None and seconds != report["end"]:
        raise OverrideDeclarationError(
            "override_computed_result_invalid", "固定单事件值窗口与统计截止不一致"
        )
    evidence = {
        "source": "damage_report_window.end" if report else "resolved_template.loop_duration",
        "seconds": seconds,
        "dps_derived": dps_derived,
    }
    if report is not None:
        evidence["window_fingerprint"] = fingerprint(report)
    return evidence


def _approximation_projection(
    rows: list[dict[str, Any]], hit_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    approximate: list[dict[str, Any]] = []
    for row in rows:
        precision = row.get("precision")
        if not isinstance(precision, dict) or precision.get("kind") not in {
            "exact", "approximate",
        }:
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "固定单事件值精度回执无效"
            )
        if precision.get("state_effects") != "none":
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "固定单事件值不能改变状态"
            )
        if precision["kind"] == "approximate":
            approximate.append(row)
    disclosures = []
    for row in approximate:
        hit = hit_by_id.get(row.get("event_id"))
        if not isinstance(hit, dict):
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "近似事件缺少逐事件身份"
            )
        disclosures.append({
            "event_id": row["event_id"],
            "coverage_key": row["coverage_key"],
            "character": hit["character"],
            "skill": hit["skill"],
            "method": row["precision"]["method"],
            "uncertainty": row["precision"]["uncertainty"],
            "state_effects": "none",
        })
    return {
        "precision": "approximate" if approximate else "exact",
        "approximate_event_count": len(approximate),
        "approximate_by_character": sorted({
            item["character"] for item in disclosures
        }),
        "approximate_by_skill": sorted({item["skill"] for item in disclosures}),
        "approximation_disclosures": disclosures,
    }


def _aggregation_projection(
    result: dict[str, Any], rows: list[dict[str, Any]], window: dict[str, Any]
) -> dict[str, Any]:
    aggregates = _damage_aggregates(result)
    hit_by_id = {
        hit["id"]: hit for hit in result["hits"]
        if isinstance(hit, dict) and isinstance(hit.get("id"), str)
    }
    report_rows = [row for row in rows if row.get("report_included", True)]
    projection = {
        "window": copy.deepcopy(window),
        **aggregates,
        "dps": None,
        **_approximation_projection(report_rows, hit_by_id),
    }
    if window["dps_derived"]:
        projection["dps"] = aggregates["total_damage"] / window["seconds"]
    return projection


def _audit_projection(rows, members):
    audit = [row for row in rows if not row["report_included"]]
    return {
        "event_ids": [row["event_id"] for row in audit],
        "total_effective_expected": _number(
            sum(row["effective_expected"] for row in audit), "窗口外接管伤害汇总不是有限非负值"
        ),
        **_approximation_projection(audit, {identity: hit for identity, (hit, _) in members.items()}),
    }


def _reviewed_overlay(row, target):
    return {
        "override_id": row.get("override_id"), "coverage_key": row.get("coverage_key"),
        "target": target, "expected": row.get("effective_expected"),
        "precision": row.get("precision"), "source_fingerprint": row.get("source_fingerprint"),
    }


def _validate_reviewed_rows(result, result_receipt, proof):
    from .receipt import validate_execution_receipt

    owner = result.get("calculation_override_receipt")
    if not isinstance(owner, dict) or owner != result_receipt.get("calculation_override_receipt"):
        raise ValueError("固定单事件值缺少同一份已受审覆盖执行权")
    validate_execution_receipt(owner)
    if (owner.get("status") != "applied" or proof.get("effective_execution_fingerprint")
            != owner.get("effective_execution_fingerprint")):
        raise ValueError("固定单事件值与受审有效执行图不一致")
    applied = {row["override_id"]: row for row in owner["applied"]
               if row["mode"] == "replace_computed_value"}
    rows = proof.get("rows")
    if (not isinstance(rows, list) or len(rows) != len(applied)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get("override_id") for row in rows} != applied.keys()):
        raise ValueError("固定单事件值与受审覆盖成员范围不一致")
    for row in rows:
        accepted = applied[row["override_id"]]
        target = accepted["target"]
        if (target.get("domain") != "computed_event" or target.get("scope_id") != "result.hits"
                or target.get("entity_id") != row.get("event_id")
                or accepted.get("coverage_key") != row.get("coverage_key")
                or fingerprint(_reviewed_overlay(row, target)) != accepted.get("effective_target_fingerprint")):
            raise ValueError("固定单事件值、精度、来源或目标偏离已受审输入")


def validate_report_numeric_baselines(result, expected):
    """Compare report/audit numbers against a caller's independent replay.

    This pure comparator grants no execution or approval. Production callers
    supply ``expected`` only from their private verification session, never from
    a document's claimed baseline. Fixed values retain their accepted owner.
    """
    window, members = scoped_hits(result, require_complete=False)
    if window is None:
        return
    expected_window, baseline = scoped_hits(expected, require_complete=False)
    if expected_window != window or members.keys() != baseline.keys():
        raise ValueError("统计或窗口外审计伤害的完整执行基线不一致")
    proof = result.get(RESULT_FIELD) or {}
    raw_rows = proof.get("rows", [])
    if (not isinstance(raw_rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("event_id"), str)
            for row in raw_rows)):
        raise ValueError("固定单事件值伤害账的成员身份无效")
    rows = {row["event_id"]: row for row in raw_rows}
    if len(rows) != len(raw_rows):
        raise ValueError("固定单事件值伤害账的成员重复")
    for identity, (hit, included) in members.items():
        _number(hit.get("expected"), "统计或窗口外审计伤害不是有限非负数值")
        if hit.get("crit") is not None:
            _number(hit["crit"], "统计或窗口外审计暴击不是有限非负数值")
        original, actual_included = baseline[identity]
        if included != actual_included:
            raise ValueError("伤害事件的统计归属与完整执行不一致")
        row, marker = rows.get(identity), hit.get("calculation_override")
        if marker is None and row is None:
            if (hit.get("expected") != original.get("expected")
                    or hit.get("crit") != original.get("crit")):
                raise ValueError("统计或窗口外审计伤害的自动数值与完整执行不一致")
        elif (not isinstance(marker, dict) or row is None
                or type(row.get("report_included")) is not bool
                or row.get("automatic_expected") != original.get("expected")
                or row.get("automatic_crit") != original.get("crit")
                or row.get("report_included") != included
                or marker.get("report_included") != included
                or marker.get("automatic_expected") != original.get("expected")
                or marker.get("effective_expected") != row.get("effective_expected")
                or hit.get("expected") != row.get("effective_expected")
                or hit.get("crit") is not None):
            raise ValueError("固定单事件值的统计归属或自动数值基线与完整执行不一致")
    if rows.keys() - members.keys():
        raise ValueError("固定单事件值包含完整执行不存在的事件")


def apply_computed_value_overrides(
    result: dict[str, Any],
    overlays: list[dict[str, Any]] | None,
    execution_receipt: dict[str, Any] | None,
    *,
    loop_duration: Any,
) -> dict[str, Any] | None:
    """Apply reviewed event values and return a separate result-level proof."""

    if not overlays:
        return None
    if not isinstance(result, dict) or not isinstance(overlays, list):
        raise OverrideDeclarationError(
            "override_computed_result_invalid", "固定单事件值执行输入无效"
        )
    applied = {
        row.get("override_id"): row
        for row in (execution_receipt or {}).get("applied", [])
        if isinstance(row, dict) and row.get("mode") == "replace_computed_value"
    }
    if len(applied) != len(overlays):
        raise OverrideDeclarationError(
            "override_computed_receipt_mismatch", "固定单事件值与受审执行回执不一致"
        )
    try:
        report_window, members = scoped_hits(result)
    except ValueError as exc:
        raise OverrideDeclarationError("override_computed_result_invalid", str(exc)) from exc
    rows = []
    seen_hits: set[str] = set()
    for overlay in overlays:
        if not isinstance(overlay, dict) or set(overlay) != {
            "override_id", "coverage_key", "target", "expected", "precision",
            "source_fingerprint",
        }:
            raise OverrideDeclarationError(
                "override_computed_value_invalid", "固定单事件值有效图成员不完整"
            )
        override_id = overlay["override_id"]
        target = overlay["target"]
        receipt_row = applied.get(override_id)
        if (
            receipt_row is None
            or receipt_row.get("coverage_key") != overlay["coverage_key"]
            or receipt_row.get("target") != target
            or not isinstance(target, dict)
            or target.get("domain") != "computed_event"
            or target.get("scope_id") != "result.hits"
            or receipt_row.get("effective_target_fingerprint") != fingerprint(overlay)
        ):
            raise OverrideDeclarationError(
                "override_computed_receipt_mismatch", "固定单事件值目标未被精确受审"
            )
        hit_id = target.get("entity_id")
        if hit_id not in members or hit_id in seen_hits:
            raise OverrideDeclarationError(
                "override_computed_target_missing", "固定单事件值目标缺失、重复或被多次接管"
            )
        seen_hits.add(hit_id)
        hit, included = members[hit_id]
        automatic_expected = _number(
            hit.get("expected"), "自动单事件伤害不是有限非负值"
        )
        automatic_crit = hit.get("crit")
        if automatic_crit is not None:
            _number(automatic_crit, "自动单事件暴击值不是有限非负值")
        fixed = _number(overlay["expected"], "固定单事件值不是有限非负值")
        precision = copy.deepcopy(overlay.get("precision"))
        hit["expected"] = fixed
        # A fixed expected value does not prove a corresponding critical-hit
        # display value.  Keeping the old value would present mixed formulas.
        hit["crit"] = None
        hit["calculation_override"] = {
            "override_id": override_id,
            "coverage_key": overlay["coverage_key"],
            "automatic_expected": automatic_expected,
            "effective_expected": fixed,
            "precision": precision,
            "state_effects": "none",
        }
        if report_window is not None:
            hit["calculation_override"]["report_included"] = included
        rows.append({
            "override_id": override_id,
            "coverage_key": overlay["coverage_key"],
            "event_id": hit_id,
            "automatic_expected": automatic_expected,
            "automatic_crit": automatic_crit,
            "effective_expected": fixed,
            "precision": precision,
            "source_fingerprint": overlay["source_fingerprint"],
            "state_effects": "none",
        })
        if report_window is not None:
            rows[-1]["report_included"] = included
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise OverrideDeclarationError(
            "override_computed_result_invalid", "引擎结果缺少伤害汇总"
        )
    window = _window_evidence(
        loop_duration, dps_derived=summary.get("dps") is not None,
        report=report_window,
    )
    aggregation = _aggregation_projection(result, rows, window)
    for key, value in aggregation.items():
        if key != "window":
            summary[key] = copy.deepcopy(value)
    proof = {
        "contract": SCOPED_CONTRACT if report_window is not None else CONTRACT,
        "effective_execution_fingerprint": execution_receipt.get(
            "effective_execution_fingerprint"
        ),
        "rows": rows,
        "ledger_fingerprint": fingerprint(rows),
        "aggregation": aggregation,
    }
    if report_window is not None:
        proof["audit_aggregation"] = _audit_projection(rows, members)
    proof["proof_fingerprint"] = fingerprint(proof)
    result[RESULT_FIELD] = copy.deepcopy(proof)
    return proof


@verification_scope
def validate_computed_value_result_receipt(
    result: dict[str, Any], result_receipt: dict[str, Any], *, _session=None,
) -> None:
    """Verify that saved/exported aggregates still derive from fixed events."""

    proof = result.get(RESULT_FIELD)
    outer = result_receipt.get(RESULT_FIELD)
    if proof is None and outer is None:
        owners = (result.get("calculation_override_receipt"), result_receipt.get("calculation_override_receipt"))
        if any(isinstance(owner, dict) and any(
                isinstance(row, dict) and row.get("mode") == "replace_computed_value"
                for row in owner.get("applied", [])) for owner in owners):
            raise ValueError("已受审固定单事件值缺少实际执行证明")
        if any(isinstance(hit, dict) and hit.get("calculation_override") is not None
               for key in ("hits", "damage_report_audit_hits") for hit in result.get(key, [])):
            raise ValueError("固定单事件值标记缺少实际执行证明")
        return
    if not isinstance(proof, dict) or proof != outer:
        raise ValueError("固定单事件值结果与外层回执不一致")
    report_window, members = scoped_hits(result)
    scoped = report_window is not None
    expected_keys = {
        "contract", "effective_execution_fingerprint", "rows",
        "ledger_fingerprint", "aggregation", "proof_fingerprint",
    } | ({"audit_aggregation"} if scoped else set())
    if set(proof) != expected_keys or proof.get("contract") != (SCOPED_CONTRACT if scoped else CONTRACT):
        raise ValueError("固定单事件值结果回执字段不完整")
    unsigned = {key: copy.deepcopy(value) for key, value in proof.items()
                if key != "proof_fingerprint"}
    if (
        proof["proof_fingerprint"] != fingerprint(unsigned)
        or proof["ledger_fingerprint"] != fingerprint(proof["rows"])
        or not proof["rows"]
    ):
        raise ValueError("固定单事件值结果回执指纹无效")
    _validate_reviewed_rows(result, result_receipt, proof)
    automatic = None
    if scoped:
        # The scope receipt proves event membership. Fixed-value evidence must
        # additionally prove the true pre-overlay numbers, including tail audit.
        execution = result.get("damage_report_execution")
        replayed = _session.replay(execution).get("result")
        if not isinstance(replayed, dict):
            raise ValueError("固定单事件值缺少完整执行的自动数值基线")
        validate_report_numeric_baselines(result, replayed)
        _, automatic = scoped_hits(replayed)
    seen_events: set[str] = set()
    for row in proof["rows"]:
        if not isinstance(row, dict) or row.get("event_id") in seen_events:
            raise ValueError("固定单事件值与逐事件伤害账不一致")
        for key in ("automatic_expected", "effective_expected"):
            _number(row.get(key), "固定单事件值伤害账不是有限非负数值")
        if row.get("automatic_crit") is not None:
            _number(row["automatic_crit"], "固定单事件值自动暴击基线无效")
        member = members.get(row.get("event_id"))
        hit, included = member if member is not None else (None, None)
        marker = hit.get("calculation_override") if isinstance(hit, dict) else None
        if (
            not isinstance(marker, dict)
            or marker.get("override_id") != row.get("override_id")
            or marker.get("coverage_key") != row.get("coverage_key")
            or hit.get("expected") != row.get("effective_expected")
            or hit.get("crit") is not None
            or marker.get("state_effects") != "none"
            or marker.get("precision") != row.get("precision")
            or marker.get("automatic_expected") != row.get("automatic_expected")
            or marker.get("effective_expected") != row.get("effective_expected")
            or row.get("state_effects") != "none"
        ):
            raise ValueError("固定单事件值与逐事件伤害账不一致")
        if scoped:
            baseline = automatic.get(row["event_id"])
            if (type(row.get("report_included")) is not bool
                    or row["report_included"] != included
                    or marker.get("report_included") != included
                    or baseline is None or baseline[1] != included
                    or baseline[0].get("expected") != row["automatic_expected"]
                    or baseline[0].get("crit") != row["automatic_crit"]):
                raise ValueError("固定单事件值的统计归属或自动数值基线与完整执行不一致")
        seen_events.add(row["event_id"])
    # External supplements keep the engine summary untouched and publish their
    # own calculation_totals, so the engine summary must always match hits.
    aggregation = proof.get("aggregation")
    if not isinstance(aggregation, dict) or set(aggregation) != {
        "window", "total_damage", "by_character", "by_skill", "dps",
        "precision", "approximate_event_count", "approximate_by_character",
        "approximate_by_skill", "approximation_disclosures",
    }:
        raise ValueError("固定单事件值聚合回执字段不完整")
    window = aggregation.get("window")
    if not isinstance(window, dict) or set(window) != {
        "source", "seconds", "dps_derived",
    } | ({"window_fingerprint"} if scoped else set()) or window.get("source") != (
        "damage_report_window.end" if scoped else "resolved_template.loop_duration"
    ) or type(
        window.get("dps_derived")
    ) is not bool:
        raise ValueError("固定单事件值计算窗口证据无效")
    if result_receipt.get(WINDOW_RECEIPT_FIELD) != window:
        raise ValueError("固定单事件值计算窗口与外层回执不一致")
    try:
        if scoped and window != _window_evidence(
            report_window["end"], dps_derived=window["dps_derived"], report=report_window,
        ):
            raise ValueError("固定单事件值窗口与统计截止不一致")
        if window["seconds"] is not None:
            _number(window["seconds"], "固定单事件值计算窗口无效")
        if window["dps_derived"] and not window["seconds"]:
            raise OverrideDeclarationError(
                "override_computed_result_invalid", "固定单事件值计算窗口无效"
            )
        expected_aggregation = _aggregation_projection(
            result, proof["rows"], window
        )
    except OverrideDeclarationError as exc:
        raise ValueError(str(exc)) from exc
    if aggregation != expected_aggregation:
        raise ValueError("固定单事件值聚合回执不是由逐事件账派生")
    if scoped and proof["audit_aggregation"] != _audit_projection(proof["rows"], members):
        raise ValueError("固定单事件值的窗口外审计与逐事件账不一致")
    summary = result.get("summary") or {}
    for key, expected in expected_aggregation.items():
        if key != "window" and summary.get(key) != expected:
            raise ValueError("固定单事件值汇总不是由逐事件账派生")
