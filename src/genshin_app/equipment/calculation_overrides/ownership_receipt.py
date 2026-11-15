"""Formal one-owner-per-coverage proof for a calculated result.

The Composer's ownership audit is an internal construction record.  This
module binds that record to the exact effective graph and calculation input,
then carries the same proof in both result and result receipt.  It never grants
ownership: claims must already have passed the generic ownership registry.
"""

from __future__ import annotations

import copy
from typing import Any

from .ownership import ownership_registry
from .schema import fingerprint

AUDIT_CONTRACT = "calculation-coverage-ownership-audit-v1"
CONTRACT = "calculation-coverage-ownership-proof-v1"
FIELD = "calculation_ownership_proof"


def _effective_graph(report: dict[str, Any]) -> dict[str, Any]:
    plan = report.get("plan") if isinstance(report.get("plan"), dict) else {}
    return {
        "template": plan.get("resolved_template"),
        "rotation_model": report.get(
            "compiled_rotation_model", plan.get("rotation_model")
        ),
        "mechanics_model": report.get("mechanics_model"),
        "mechanics_matches": report.get("mechanics_matches"),
        "computed_value_overrides": report.get("computed_value_overrides", []),
    }


def _validate_audit(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {
            "contract", "status", "claims", "ownership_ledger_fingerprint",
        }
        or value.get("contract") != AUDIT_CONTRACT
        or value.get("status") != "verified"
        or not isinstance(value.get("claims"), list)
    ):
        raise ValueError("计算范围所有权审计无效")
    registry = ownership_registry(value["claims"])
    ordered = [copy.deepcopy(registry[key]) for key in sorted(registry)]
    if (
        value["claims"] != ordered
        or value.get("ownership_ledger_fingerprint") != fingerprint(ordered)
    ):
        raise ValueError("计算范围所有权账不能由唯一所有者重建")
    return copy.deepcopy(value)


def build_ownership_result_proof(report: dict[str, Any]) -> dict[str, Any]:
    audit = _validate_audit(report.get("calculation_ownership_audit"))
    input_fingerprint = report.get("input_fingerprint")
    if not isinstance(input_fingerprint, str) or len(input_fingerprint) != 64:
        raise ValueError("计算范围所有权证明缺少当前输入指纹")
    proof = {
        "contract": CONTRACT,
        "status": "verified",
        "claims": audit["claims"],
        "ownership_ledger_fingerprint": audit[
            "ownership_ledger_fingerprint"
        ],
        "input_fingerprint": input_fingerprint,
        "effective_graph_fingerprint": fingerprint(_effective_graph(report)),
    }
    proof["proof_fingerprint"] = fingerprint(proof)
    return proof


def validate_ownership_result_proof(
    result: dict[str, Any], receipt: dict[str, Any],
    report: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    result_value = result.get(FIELD)
    receipt_value = receipt.get(FIELD)
    if result_value is None and receipt_value is None:
        return None
    if result_value != receipt_value or not isinstance(result_value, dict):
        raise ValueError("结果与结果回执的计算范围所有权证明不一致")
    expected_keys = {
        "contract", "status", "claims", "ownership_ledger_fingerprint",
        "input_fingerprint", "effective_graph_fingerprint", "proof_fingerprint",
    }
    if (
        set(result_value) != expected_keys
        or result_value.get("contract") != CONTRACT
        or result_value.get("status") != "verified"
    ):
        raise ValueError("计算范围所有权证明合同无效")
    audit = _validate_audit({
        "contract": AUDIT_CONTRACT,
        "status": result_value["status"],
        "claims": result_value["claims"],
        "ownership_ledger_fingerprint": result_value[
            "ownership_ledger_fingerprint"
        ],
    })
    unsigned = {
        key: copy.deepcopy(value)
        for key, value in result_value.items()
        if key != "proof_fingerprint"
    }
    if result_value.get("proof_fingerprint") != fingerprint(unsigned):
        raise ValueError("计算范围所有权证明自身指纹无效")
    override = receipt.get("calculation_override_receipt")
    if isinstance(override, dict) and override.get("status") == "applied" and (
        override.get("ownership_ledger_fingerprint")
        != audit["ownership_ledger_fingerprint"]
    ):
        raise ValueError("覆盖回执与最终计算范围所有权账不一致")
    if report is not None:
        report_audit = _validate_audit(report.get("calculation_ownership_audit"))
        if (
            report_audit != audit
            or result_value.get("input_fingerprint")
            != report.get("input_fingerprint")
            or result_value.get("effective_graph_fingerprint")
            != fingerprint(_effective_graph(report))
        ):
            raise ValueError("计算范围所有权证明不属于当前有效图")
    return copy.deepcopy(result_value)


__all__ = [
    "CONTRACT", "FIELD", "build_ownership_result_proof",
    "validate_ownership_result_proof",
]
