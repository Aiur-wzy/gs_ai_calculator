"""Proof-backed fixed-event metadata for the read-only formula workbook.

No formula is solved backwards from an override. Automatic values are kept
only as a comparison baseline; effective fixed values own the event total.
"""
from __future__ import annotations

import copy

from genshin_app.equipment.calculation_overrides.computed_values import (
    RESULT_FIELD,
    validate_computed_value_result_receipt,
)


def fixed_override_projection(result, receipt):
    validate_computed_value_result_receipt(result, receipt)
    proof = result.get(RESULT_FIELD) or {}
    rows = {row["event_id"]: copy.deepcopy(row) for row in proof.get("rows", [])
            if row.get("report_included", True)}
    audit_rows = {row["event_id"]: copy.deepcopy(row) for row in proof.get("rows", [])
                  if not row.get("report_included", True)}
    marked = {row.get("id") for row in result.get("hits", []) if row.get("calculation_override")}
    if marked != set(rows):
        raise ValueError("固定单事件值标记与完整证明的事件范围不一致")
    audit_marked = {row.get("id") for row in result.get("damage_report_audit_hits", [])
                    if row.get("calculation_override")}
    if audit_marked != set(audit_rows):
        raise ValueError("窗口外固定单事件值标记与完整审计范围不一致")
    return {"rows": rows, "audit_rows": audit_rows,
            "audit_aggregation": copy.deepcopy(proof.get("audit_aggregation")),
            "precision": proof.get("aggregation", {}).get("precision", "exact"),
            "approximation_disclosures": copy.deepcopy(proof.get("aggregation", {}).get("approximation_disclosures", []))}


def fixed_override_support(projection):
    support = {"event_count": len(projection["rows"]), "event_ids": list(projection["rows"]),
            "effective_values": "fixed_export_snapshot", "automatic_formulas": "comparison_baseline_only",
            "critical_hit": "not_derived_from_fixed_expected", "precision": projection["precision"],
            "after_input_edits": "regenerate_in_app_or_agent", "state_effects": "none"}
    if projection.get("audit_rows"):
        support.update(audit_event_ids=list(projection["audit_rows"]),
                       audit_event_count=len(projection["audit_rows"]),
                       audit_in_report=False)
    return support
