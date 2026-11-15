"""Freshness and acceptance review for override declarations."""
from __future__ import annotations

import copy
from typing import Any

from .identity import build_target_registry, stable_target_identity
from .schema import (
    CONTRACT,
    OverrideDeclarationError,
    execution_support,
    fingerprint,
    validate_declarations,
)


def acceptance_fingerprint(basis: dict[str, Any], item: dict[str, Any]) -> str:
    """Bind acceptance to every declaration field except the signature itself."""
    declaration = copy.deepcopy(item)
    declaration["accepted_fingerprint"] = None
    return fingerprint({"contract": CONTRACT, "basis": basis, "declaration": declaration})


def review_declarations(
    declarations: dict[str, Any],
    *,
    current_basis: dict[str, Any],
    current_targets: list[dict[str, Any]],
    plan_version: int = 3,
) -> dict[str, Any]:
    """Review current targets without materializing or executing a payload."""
    validate_declarations(declarations, plan_version=plan_version)
    if type(current_basis) is not dict or set(current_basis) != set(declarations["basis"]):
        raise OverrideDeclarationError("override_basis_invalid", "当前自动基线字段不完整")
    registry = build_target_registry(current_targets)
    basis_fresh = declarations["basis"] == current_basis
    rows: list[dict[str, Any]] = []
    for item in declarations["items"]:
        identity = stable_target_identity(item["target"])
        target = registry.get(identity)
        issues: list[dict[str, str]] = []
        expected = acceptance_fingerprint(declarations["basis"], item)
        support = execution_support(item["mode"])
        if not basis_fresh:
            issues.append({"code": "override_baseline_stale", "message": "自动基线或依赖合同已变化"})
        if target is None:
            issues.append({"code": "override_target_missing", "message": "稳定目标在当前自动基线中不存在"})
        else:
            actual_target = fingerprint(target["value"])
            if actual_target != item["automatic_target_fingerprint"]:
                issues.append({"code": "override_target_stale", "message": "目标自动值已变化"})
        if support != "implemented":
            issues.append({"code": "override_mode_not_implemented", "message": "该模式只有声明合同，尚未接入执行"})
        if item["accepted_fingerprint"] != expected:
            issues.append({"code": "override_confirmation_required", "message": "需要接受当前精确差异与影响范围"})
        if any(issue["code"] == "override_mode_not_implemented" for issue in issues):
            status = "unsupported"
        elif any(issue["code"].endswith("stale") for issue in issues):
            status = "stale"
        elif issues:
            status = "pending"
        else:
            status = "declaration_ready"
        rows.append(
            {
                "override_id": item["override_id"],
                "mode": item["mode"],
                "target_identity": identity,
                "coverage_key": item["coverage_key"],
                "impact_domains": copy.deepcopy(item["impact_domains"]),
                "execution_support": support,
                "status": status,
                "required_confirmation": {"accepted_fingerprint": expected},
                "issues": issues,
            }
        )
    counts = {status: sum(row["status"] == status for row in rows) for status in (
        "declaration_ready", "pending", "stale", "unsupported"
    )}
    return {
        "contract": CONTRACT,
        "execution_connected": True,
        "review_applies_payload": False,
        "basis_fresh": basis_fresh,
        "status": "declaration_ready" if rows and counts["declaration_ready"] == len(rows) else (
            "empty" if not rows else "pending"
        ),
        "counts": counts,
        "items": rows,
        "review_fingerprint": fingerprint(rows),
    }
