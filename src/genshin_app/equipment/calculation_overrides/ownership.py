"""Pure ownership transaction for reviewed override declarations."""
from __future__ import annotations

import copy
import re
from typing import Any

from .identity import stable_target_identity
from .schema import CONTRACT_READY_MODES, OverrideDeclarationError, fingerprint

OWNER_KINDS = frozenset({
    "automatic", "manual", "external", "materialization", "override",
    "suppressed",
})


def _validate_claim(claim: Any) -> dict[str, Any]:
    keys = {"owner_id", "owner_kind", "coverage_key", "target_identity", "source_fingerprint"}
    if type(claim) is not dict or set(claim) != keys:
        raise OverrideDeclarationError("override_owner_invalid", "所有权记录字段不完整")
    if claim["owner_kind"] not in OWNER_KINDS:
        raise OverrideDeclarationError("override_owner_invalid", "所有权类型无效")
    if any(type(claim[key]) is not str or not claim[key] for key in keys - {"owner_kind"}):
        raise OverrideDeclarationError("override_owner_invalid", "所有权身份不能为空")
    if re.fullmatch(r"[0-9a-f]{64}", claim["source_fingerprint"]) is None:
        raise OverrideDeclarationError("override_owner_invalid", "所有权来源必须绑定SHA-256")
    return claim


def ownership_registry(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a one-owner-per-coverage registry."""
    if type(claims) is not list:
        raise OverrideDeclarationError("override_owner_invalid", "所有权记录必须是列表")
    registry: dict[str, dict[str, Any]] = {}
    owner_ids: set[str] = set()
    for raw in claims:
        claim = copy.deepcopy(_validate_claim(raw))
        if claim["owner_id"] in owner_ids:
            raise OverrideDeclarationError("override_owner_duplicate", "owner_id重复")
        if claim["coverage_key"] in registry:
            raise OverrideDeclarationError("override_coverage_duplicate", "同一coverage存在多个所有者")
        owner_ids.add(claim["owner_id"])
        registry[claim["coverage_key"]] = claim
    return registry


def _claim_for(item: dict[str, Any]) -> dict[str, Any]:
    mode = item["mode"]
    return {
        "owner_id": f"override:{item['override_id']}",
        "owner_kind": "suppressed" if mode == "suppress_event" else "override",
        "coverage_key": item["coverage_key"],
        "target_identity": stable_target_identity(item["target"]),
        "source_fingerprint": fingerprint(
            {
                "override_id": item["override_id"],
                "mode": mode,
                "payload": item["payload"],
                "accepted_fingerprint": item["accepted_fingerprint"],
            }
        ),
    }


def apply_ownership_transaction(
    claims: list[dict[str, Any]], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Atomically release exact prior claims and install override claims.

    The caller's objects are never mutated.  Any invalid item aborts the whole
    transaction, including earlier items in the same request.
    """
    baseline = ownership_registry(claims)
    working = copy.deepcopy(baseline)
    released: list[dict[str, Any]] = []
    installed: list[dict[str, Any]] = []
    seen_override_ids: set[str] = set()
    seen_coverage: set[str] = set()
    for item in items:
        if type(item) is not dict:
            raise OverrideDeclarationError("override_shape_invalid", "覆盖项必须是对象")
        mode = item.get("mode")
        if mode not in CONTRACT_READY_MODES:
            raise OverrideDeclarationError(
                "override_mode_not_implemented", "该覆盖模式尚未接入所有权事务"
            )
        override_id = item.get("override_id")
        coverage_key = item.get("coverage_key")
        if override_id in seen_override_ids or coverage_key in seen_coverage:
            raise OverrideDeclarationError("override_transaction_duplicate", "事务内覆盖身份或范围重复")
        seen_override_ids.add(override_id)
        seen_coverage.add(coverage_key)
        supersedes = item.get("supersedes")
        if type(supersedes) is not list:
            raise OverrideDeclarationError("override_supersedes_invalid", "supersedes必须是列表")
        current = working.get(coverage_key)
        if mode == "add_event":
            if supersedes or current is not None:
                raise OverrideDeclarationError(
                    "override_coverage_conflict", "新增事件不能覆盖已有所有权"
                )
        else:
            if current is None:
                raise OverrideDeclarationError("override_coverage_hole", "待替换范围没有当前所有者")
            if supersedes != [current["owner_id"]]:
                raise OverrideDeclarationError(
                    "override_supersedes_mismatch", "supersedes必须精确释放当前所有者"
                )
            if current["target_identity"] != stable_target_identity(item["target"]):
                raise OverrideDeclarationError(
                    "override_target_owner_mismatch", "覆盖目标与待释放所有权不一致"
                )
            released.append(copy.deepcopy(current))
            del working[coverage_key]
        claim = _claim_for(item)
        if claim["coverage_key"] in working:
            raise OverrideDeclarationError("override_coverage_duplicate", "覆盖安装后出现重复所有权")
        working[claim["coverage_key"]] = claim
        installed.append(copy.deepcopy(claim))
    final_claims = [copy.deepcopy(working[key]) for key in sorted(working)]
    return {
        "status": "committed",
        "baseline_fingerprint": fingerprint([baseline[key] for key in sorted(baseline)]),
        "released": released,
        "installed": installed,
        "claims": final_claims,
        "ownership_ledger_fingerprint": fingerprint(final_claims),
    }
