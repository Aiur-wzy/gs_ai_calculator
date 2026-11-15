"""Compact declaration and effective-execution coverage receipts."""
from __future__ import annotations

import copy
from typing import Any

from .schema import CONTRACT, fingerprint

BINDING_OVERLAY_CONTRACT = "calculation-override-binding-overlay-v1"


def build_binding_overlay(
    basis: dict[str, Any], relation_proofs: list[dict[str, Any]],
    applied: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind reviewed topology relations to their post-override targets.

    This is an override overlay, not a rewritten event-topology document.  It
    preserves the automatic binding fingerprint while proving how each exact
    accepted event now appears (or is suppressed) in the effective graph.
    """

    applied_by_id = {row["override_id"]: row for row in applied}
    rows = []
    for proof in relation_proofs:
        event = applied_by_id.get(proof["override_id"])
        if event is None:
            raise ValueError("覆盖绑定叠加层缺少已应用事件")
        rows.append({
            **copy.deepcopy(proof),
            "mode": event["mode"],
            "target": copy.deepcopy(event["target"]),
            "effective_target_fingerprint": event["effective_target_fingerprint"],
            "status": "suppressed" if event["mode"] == "suppress_event" else "effective",
        })
    overlay = {
        "contract": BINDING_OVERLAY_CONTRACT,
        "topology_fingerprint": basis.get("topology_fingerprint"),
        "execution_bindings_fingerprint": basis.get("execution_bindings_fingerprint"),
        "rows": rows,
    }
    overlay["overlay_fingerprint"] = fingerprint(overlay)
    return overlay


def build_declaration_receipt(
    review: dict[str, Any], ownership: dict[str, Any] | None = None
) -> dict[str, Any]:
    rows = review.get("items") if type(review) is dict else None
    if type(rows) is not list:
        raise ValueError("覆盖审阅回执缺少items")
    ready = [row for row in rows if row.get("status") == "declaration_ready"]
    pending = [row for row in rows if row.get("status") != "declaration_ready"]
    receipt = {
        "contract": CONTRACT,
        "receipt_kind": "declaration_review_only",
        "execution_connected": False,
        "automation_coverage": None,
        "override_coverage": [
            {
                "override_id": row["override_id"],
                "coverage_key": row["coverage_key"],
                "mode": row["mode"],
                "impact_domains": copy.deepcopy(row.get("impact_domains", [])),
                "status": row["status"],
            }
            for row in ready
        ],
        "pending_or_unsupported": [
            {
                "override_id": row["override_id"],
                "status": row["status"],
                "issue_codes": [issue["code"] for issue in row.get("issues", [])],
            }
            for row in pending
        ],
        "ownership_ledger_fingerprint": (
            ownership.get("ownership_ledger_fingerprint") if ownership else None
        ),
        # The ownership transaction covers only the reviewed coverage keys;
        # it is not a completeness proof for the full automatic graph.
        "ownership_scope": "declared_coverage_transaction_only",
        "review_fingerprint": review.get("review_fingerprint"),
    }
    receipt["receipt_fingerprint"] = fingerprint(receipt)
    return receipt


def build_execution_receipt(
    basis: dict[str, Any],
    review: dict[str, Any],
    *,
    status: str,
    ownership: dict[str, Any] | None = None,
    applied: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    target_candidates: list[dict[str, Any]] | None = None,
    effective_execution_fingerprint: str | None = None,
    binding_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the bounded authoritative receipt for the effective graph."""

    if status not in {"pending", "applied"}:
        raise ValueError("未知覆盖执行状态")
    rows = review.get("items", []) if isinstance(review, dict) else []
    receipt = {
        "contract": CONTRACT,
        "receipt_kind": "effective_execution",
        "execution_connected": True,
        "status": status,
        "basis_fingerprint": fingerprint(basis),
        "review_fingerprint": review.get("review_fingerprint") if isinstance(review, dict) else None,
        "effective_execution_fingerprint": effective_execution_fingerprint,
        "ownership_ledger_fingerprint": (
            ownership.get("ownership_ledger_fingerprint") if ownership else None
        ),
        "ownership_scope": "declared_coverage_transaction_only",
        "released_owner_ids": [
            row["owner_id"] for row in (ownership or {}).get("released", [])
        ],
        "installed_owner_ids": [
            row["owner_id"] for row in (ownership or {}).get("installed", [])
        ],
        "coverage": [
            {
                "override_id": row["override_id"],
                "coverage_key": row["coverage_key"],
                "mode": row["mode"],
                "impact_domains": copy.deepcopy(row.get("impact_domains", [])),
                "status": row["status"],
            }
            for row in rows
        ],
        # This is the minimum review material needed to turn a first-pass
        # declaration into a fresh accepted declaration.  It deliberately
        # publishes stable identities and hashes, not the potentially large
        # automatic event bodies.
        "target_candidates": copy.deepcopy(target_candidates or []),
        "applied": copy.deepcopy(applied or []),
        "binding_overlay": copy.deepcopy(binding_overlay),
        "issues": copy.deepcopy(issues or []),
    }
    receipt["receipt_fingerprint"] = fingerprint(receipt)
    return receipt


def validate_execution_receipt(value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise TypeError("覆盖执行回执不是对象")
    expected = {
        "contract", "receipt_kind", "execution_connected", "status",
        "basis_fingerprint", "review_fingerprint", "effective_execution_fingerprint",
        "ownership_ledger_fingerprint", "ownership_scope", "released_owner_ids",
        "installed_owner_ids",
        "coverage", "target_candidates", "applied", "binding_overlay", "issues",
        "receipt_fingerprint",
    }
    if set(value) != expected or value.get("contract") != CONTRACT:
        raise ValueError("覆盖执行回执字段不完整")
    if (value.get("receipt_kind") != "effective_execution"
            or value.get("execution_connected") is not True):
        raise ValueError("覆盖执行回执合同不一致")
    if value.get("ownership_scope") != "declared_coverage_transaction_only":
        raise ValueError("覆盖所有权证明范围不明确")
    unsigned = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key != "receipt_fingerprint"
    }
    if value.get("receipt_fingerprint") != fingerprint(unsigned):
        raise ValueError("覆盖执行回执指纹不一致")
    status = value.get("status")
    if status not in {"pending", "applied"}:
        raise ValueError("覆盖执行状态无效")
    for key in (
        "released_owner_ids", "installed_owner_ids", "coverage",
        "target_candidates", "applied", "issues",
    ):
        if type(value.get(key)) is not list:
            raise ValueError("覆盖执行回执列表字段无效")
    if status == "pending":
        if (
            value.get("effective_execution_fingerprint") is not None
            or value.get("ownership_ledger_fingerprint") is not None
            or value["released_owner_ids"]
            or value["installed_owner_ids"]
            or value["applied"]
            or value.get("binding_overlay") is not None
        ):
            raise ValueError("待处理覆盖不能携带已提交的执行或所有权证明")
        return
    if (
        not value.get("effective_execution_fingerprint")
        or not value.get("ownership_ledger_fingerprint")
        or not value["coverage"]
    ):
        raise ValueError("已应用覆盖缺少有效图或所有权证明")
    if (
        any(type(row) is not dict for row in value["coverage"])
        or any(type(row) is not dict for row in value["applied"])
        or any(
            not isinstance(owner_id, str) or not owner_id
            for owner_id in [
                *value["released_owner_ids"], *value["installed_owner_ids"],
            ]
        )
    ):
        raise ValueError("已应用覆盖的范围、事件或所有者格式无效")
    coverage_ids = [row.get("override_id") for row in value["coverage"]]
    applied_ids = [row.get("override_id") for row in value["applied"]]
    if (
        len(coverage_ids) != len(set(coverage_ids))
        or coverage_ids != applied_ids
        or any(
            row.get("status") != "declaration_ready"
            for row in value["coverage"]
        )
        or value["installed_owner_ids"]
        != [f"override:{override_id}" for override_id in coverage_ids]
    ):
        raise ValueError("已应用覆盖的范围、事件与安装所有者不一致")
    release_count = sum(
        row.get("mode") != "add_event" for row in value["coverage"]
    )
    if len(value["released_owner_ids"]) != release_count:
        raise ValueError("已应用覆盖的基线所有者释放记录不完整")
    coverage_by_id = {row["override_id"]: row for row in value["coverage"]}
    for row in value["applied"]:
        coverage = coverage_by_id[row["override_id"]]
        if (
            row.get("coverage_key") != coverage.get("coverage_key")
            or row.get("mode") != coverage.get("mode")
            or row.get("impact_domains") != coverage.get("impact_domains")
        ):
            raise ValueError("已应用覆盖与声明影响范围不一致")
    event_ids = [
        row["override_id"] for row in value["coverage"]
        if row["mode"] in {
            "add_event", "replace_event", "suppress_event",
            "replace_state_transition", "replace_computed_value",
        }
    ]
    overlay = value.get("binding_overlay")
    if event_ids:
        if not isinstance(overlay, dict):
            raise ValueError("事件覆盖缺少有效图绑定叠加证明")
        overlay_rows = overlay.get("rows")
        if (
            type(overlay_rows) is not list
            or any(type(row) is not dict for row in overlay_rows)
        ):
            raise ValueError("事件覆盖绑定叠加成员格式无效")
        unsigned_overlay = {
            key: copy.deepcopy(item)
            for key, item in overlay.items()
            if key != "overlay_fingerprint"
        }
        if (
            overlay.get("contract") != BINDING_OVERLAY_CONTRACT
            or overlay.get("overlay_fingerprint") != fingerprint(unsigned_overlay)
            or [row.get("override_id") for row in overlay_rows]
            != event_ids
        ):
            raise ValueError("事件覆盖绑定叠加证明不一致")
        if (
            not isinstance(overlay.get("topology_fingerprint"), str)
            or not isinstance(overlay.get("execution_bindings_fingerprint"), str)
        ):
            raise ValueError("事件覆盖绑定叠加证明缺少拓扑快照")
        applied_by_id = {row["override_id"]: row for row in value["applied"]}
        for row in overlay_rows:
            event = applied_by_id[row["override_id"]]
            target = event.get("target")
            if (
                row.get("mode") != event.get("mode")
                or row.get("target") != target
                or row.get("topology_node_id") != target.get("topology_node_id")
                or row.get("execution_binding_id")
                != target.get("execution_binding_id")
                or row.get("effective_target_fingerprint")
                != event.get("effective_target_fingerprint")
                or row.get("status")
                != ("suppressed" if event["mode"] == "suppress_event" else "effective")
                or row.get("projection")
                not in {
                    "exact", "manual_hit_dispatch_projection",
                    "computed_damage_event", "state_runtime_contract_member",
                }
            ):
                raise ValueError("事件覆盖绑定叠加成员与有效事件不一致")
    elif overlay is not None:
        raise ValueError("非事件覆盖不能携带事件绑定叠加证明")


def validate_execution_bundle_receipt(
    value: dict[str, Any], bundle: dict[str, Any]
) -> None:
    """Bind an applied receipt to the exact graph handed to the executor."""

    validate_execution_receipt(value)
    if value.get("status") != "applied":
        raise ValueError("未应用的覆盖回执不能进入引擎")
    normalized_bundle = copy.deepcopy(bundle)
    normalized_bundle.setdefault("computed_value_overrides", [])
    if value["effective_execution_fingerprint"] != fingerprint(normalized_bundle):
        raise ValueError("覆盖有效执行图与引擎输入不一致")
    from .baseline import ABSENT_TARGET, locate_target

    inspection_bundle = normalized_bundle
    for row in value["applied"]:
        if not isinstance(row.get("target"), dict):
            raise TypeError("已应用覆盖缺少稳定目标")
        if row["mode"] == "replace_computed_value":
            matches = [
                item
                for item in inspection_bundle.get("computed_value_overrides", [])
                if isinstance(item, dict)
                and item.get("override_id") == row["override_id"]
            ]
            if len(matches) != 1:
                raise ValueError("固定单事件值覆盖未进入有效执行图")
            expected = fingerprint(matches[0])
        else:
            _, _, index, actual = locate_target(
                inspection_bundle,
                row["target"],
                allow_absent=row["mode"] == "suppress_event",
            )
            if row["mode"] == "suppress_event":
                if index is not None or actual != ABSENT_TARGET:
                    raise ValueError("被排除事件仍存在于有效执行图")
                expected = fingerprint({"state": "suppressed"})
            else:
                if index is None:
                    raise ValueError("已应用覆盖目标未进入有效执行图")
                expected = fingerprint(actual)
        if row["effective_target_fingerprint"] != expected:
            raise ValueError("覆盖目标与有效执行图不一致")


def validate_execution_result_receipt(
    result: dict[str, Any], result_receipt: dict[str, Any]
) -> None:
    """Ensure saved/exported result and outer receipt retain the same proof."""

    result_value = result.get("calculation_override_receipt")
    receipt_value = result_receipt.get("calculation_override_receipt")
    if result_value is None and receipt_value is None:
        return
    if result_value != receipt_value:
        raise ValueError("计算结果与结果回执的覆盖证明不一致")
    validate_execution_receipt(result_value)
    if result_value.get("status") != "applied":
        raise ValueError("计算结果不能保存未应用的覆盖证明")
