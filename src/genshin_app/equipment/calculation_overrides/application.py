"""Atomic AutomaticBaselineBundle -> EffectiveExecutionBundle materialization."""

from __future__ import annotations

import copy
from typing import Any

from .baseline import (
    automatic_bundle,
    automatic_owner_id,
    base_ownership_claims,
    baseline_owner,
    locate_target,
    target_rows,
)
from .ownership import apply_ownership_transaction, ownership_registry
from .receipt import build_binding_overlay, build_execution_receipt
from .review import review_declarations
from .schema import (
    FIELD,
    OverrideDeclarationError,
    execution_support,
    fingerprint,
    validate_declarations,
)


def _payload(item: dict[str, Any]) -> Any:
    payload = item["payload"]
    mode = item["mode"]
    if mode in {"add_event", "replace_event"}:
        if set(payload) not in ({"event"}, {"value"}):
            raise OverrideDeclarationError(
                "override_payload_shape", "事件新增或替换payload只能包含event/value"
            )
        value = payload.get("event", payload.get("value"))
        if type(value) is not dict:
            raise OverrideDeclarationError("override_payload_shape", "事件payload需要完整对象")
        return copy.deepcopy(value)
    if mode == "suppress_event":
        if (set(payload) != {"reason"} or not isinstance(payload["reason"], str)
                or not payload["reason"].strip()):
            raise OverrideDeclarationError(
                "override_payload_shape", "排除事件payload只接受非空reason"
            )
        return None
    if mode == "replace_input":
        if set(payload) != {"value"}:
            raise OverrideDeclarationError(
                "override_payload_shape", "输入替换payload只能包含value"
            )
        return copy.deepcopy(payload["value"])
    if mode == "replace_state_transition":
        if set(payload) != {"transition"} or type(payload["transition"]) is not dict:
            raise OverrideDeclarationError(
                "override_payload_shape",
                "状态迁移替换payload只能包含完整transition对象",
            )
        return copy.deepcopy(payload["transition"])
    if mode == "replace_computed_value":
        if set(payload) != {"expected", "precision"}:
            raise OverrideDeclarationError(
                "override_payload_shape",
                "固定单事件值payload必须包含expected与precision",
            )
        return copy.deepcopy(payload)
    raise OverrideDeclarationError("override_mode_not_implemented", "覆盖模式尚未接入执行")


def _payload_keeps_target_identity(
    value: dict[str, Any], target: dict[str, Any], id_key: str
) -> bool:
    if value.get(id_key) != target["entity_id"]:
        return False
    if (
        target["scope_id"] == "mechanics_model.hit_events"
        and target.get("action_id") is not None
    ):
        return value.get("action_id") == target["action_id"]
    return True


def _apply_one(bundle: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    target = item["target"]
    rows, id_key, index, _ = locate_target(
        bundle, target, allow_absent=item["mode"] == "add_event"
    )
    value = _payload(item)
    mode = item["mode"]
    if mode == "add_event":
        if index is not None:
            raise OverrideDeclarationError("override_add_target_exists", "新增目标已存在")
        if not _payload_keeps_target_identity(value, target, id_key):
            raise OverrideDeclarationError(
                "override_payload_identity_mismatch", "新增事件ID与稳定目标身份不一致"
            )
        rows.append(value)
    elif mode == "replace_event":
        if index is None or not _payload_keeps_target_identity(value, target, id_key):
            raise OverrideDeclarationError(
                "override_payload_identity_mismatch", "替换事件ID与稳定目标身份不一致"
            )
        rows[index] = value
    elif mode == "suppress_event":
        if index is None:
            raise OverrideDeclarationError("override_target_missing", "待排除事件不存在")
        del rows[index]
    elif mode == "replace_input":
        if index is None:
            raise OverrideDeclarationError("override_target_missing", "待替换输入不存在")
        rows[index][target["component_id"]] = value
    elif mode == "replace_state_transition":
        if index is None or not _payload_keeps_target_identity(value, target, id_key):
            raise OverrideDeclarationError(
                "override_payload_identity_mismatch",
                "状态迁移替换必须保留稳定事件身份",
            )
        rows[index] = value
    elif mode == "replace_computed_value":
        if index is None:
            raise OverrideDeclarationError(
                "override_target_missing", "待替换的计算事件不存在"
            )
        overlay = {
            "override_id": item["override_id"],
            "coverage_key": item["coverage_key"],
            "target": copy.deepcopy(target),
            "expected": value["expected"],
            "precision": copy.deepcopy(value.get("precision")),
            "source_fingerprint": fingerprint(item["source_refs"]),
        }
        bundle.setdefault("computed_value_overrides", []).append(overlay)
        value = overlay
    return {
        "override_id": item["override_id"],
        "mode": mode,
        "target": copy.deepcopy(target),
        "coverage_key": item["coverage_key"],
        "impact_domains": copy.deepcopy(item["impact_domains"]),
        "effective_target_fingerprint": (
            fingerprint({"state": "suppressed"}) if mode == "suppress_event"
            else fingerprint(value)
        ),
    }


def _validate_effective(bundle: dict[str, Any]) -> None:
    from genshin_engine import validate_template
    from genshin_engine.mechanics import validate_mechanics_model
    from genshin_engine.rotation import validate_rotation_model
    from genshin_engine.template import TemplateError

    mechanics = bundle["mechanics_model"]
    computed = bundle.get("computed_value_overrides")
    if not isinstance(computed, list) or len({row.get("override_id") for row in computed}) != len(computed):
        raise OverrideDeclarationError(
            "override_computed_value_invalid",
            "固定单事件值覆盖列表无效或身份重复",
        )
    try:
        elemental = (mechanics or {}).get("elemental_model") or {}
        from genshin_engine.elemental_schema import SWIRL_RULESETS

        validate_template(
            bundle["template"],
            allow_empty_hits=elemental.get("ruleset") in SWIRL_RULESETS
            and bool((mechanics or {}).get("application_events")),
        )
    except TemplateError as exc:
        raise OverrideDeclarationError("override_effective_template_invalid", str(exc)) from exc
    if bundle["rotation_model"] is not None:
        rotation = validate_rotation_model(bundle["rotation_model"])
        if rotation["issues"]:
            raise OverrideDeclarationError(
                "override_effective_rotation_invalid", rotation["issues"][0]["message"]
            )
    if mechanics is not None:
        validation = validate_mechanics_model(mechanics)
        if validation["issues"]:
            raise OverrideDeclarationError(
                "override_effective_mechanics_invalid", validation["issues"][0]["message"]
            )
    rotation = bundle["rotation_model"]
    if mechanics is not None and rotation is not None:
        hit_ids = {row["id"] for row in bundle["template"]["hits"]}
        action_ids = {row["id"] for row in rotation["actions"]}
        linked: list[str] = []
        for event in mechanics["hit_events"]:
            if event["hit_id"] not in hit_ids or event["action_id"] not in action_ids:
                raise OverrideDeclarationError(
                    "override_effective_reference_invalid",
                    "有效图中的独立命中引用了不存在的命中或动作",
                )
            linked.append(event["hit_id"])
        prototypes: list[str] = []
        for pattern in mechanics["hit_patterns"]:
            if (pattern["prototype_hit_id"] not in hit_ids
                    or pattern["action_id"] not in action_ids):
                raise OverrideDeclarationError(
                    "override_effective_reference_invalid",
                    "有效图中的多段模式引用了不存在的原型命中或动作",
                )
            prototypes.append(pattern["prototype_hit_id"])
        if len(linked + prototypes) != len(set(linked + prototypes)):
            raise OverrideDeclarationError(
                "override_effective_reference_duplicate", "有效图中的命中所有权重复"
            )
        if hit_ids != set(linked) | set(prototypes):
            raise OverrideDeclarationError(
                "override_effective_reference_incomplete", "有效图仍有未绑定的输入命中"
            )
        if any(
            event["action_id"] not in action_ids
            for event in mechanics.get("application_events", [])
        ):
            raise OverrideDeclarationError(
                "override_effective_reference_invalid", "有效图中的附着事件引用了不存在的动作"
            )


def materialize_calculation_overrides(
    plan: dict[str, Any],
    template: dict[str, Any],
    rotation_model: dict[str, Any] | None,
    mechanics_model: dict[str, Any] | None,
    mechanics_matches: Any = None,
    *,
    database_snapshot_fingerprint: str,
    engine_contract_fingerprint: str,
    additional_ownership_claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one effective bundle; failures leave the automatic bundle intact."""

    baseline = automatic_bundle(
        plan,
        template,
        rotation_model,
        mechanics_model,
        mechanics_matches,
        database_snapshot_fingerprint=database_snapshot_fingerprint,
        engine_contract_fingerprint=engine_contract_fingerprint,
    )
    automatic = baseline["bundle"]
    declarations = plan.get(FIELD)
    base_claims = base_ownership_claims(
        plan, additional_ownership_claims=additional_ownership_claims,
    )
    try:
        registry = ownership_registry(base_claims)
    except OverrideDeclarationError as exc:
        issue = {
            "code": exc.reason,
            "path": exc.path or "calculation_ownership",
            "message": str(exc),
        }
        receipt = build_execution_receipt(
            baseline["basis"], {"items": [], "review_fingerprint": None},
            status="pending", issues=[issue],
        )
        return {
            "status": "pending", "automatic": automatic,
            "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
            "receipt": receipt, "issues": [issue],
            "ownership_audit": None,
        }
    ordered_claims = [copy.deepcopy(registry[key]) for key in sorted(registry)]
    ownership_audit = {
        "contract": "calculation-coverage-ownership-audit-v1",
        "status": "verified",
        "claims": ordered_claims,
        "ownership_ledger_fingerprint": fingerprint(ordered_claims),
    }
    if declarations is None:
        return {
            "status": "not_declared", "automatic": automatic,
            "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
            "receipt": None, "issues": [],
            "ownership_audit": ownership_audit,
        }
    try:
        validate_declarations(declarations, plan_version=plan.get("plan_version"))
        if (
            declarations["basis"] != baseline["basis"]
            and any(
                item["accepted_fingerprint"] is not None
                for item in declarations["items"]
            )
        ):
            issue = {
                "code": "override_baseline_stale",
                "path": f"{FIELD}.basis",
                "message": "自动基线或依赖合同已变化",
            }
            receipt = build_execution_receipt(
                baseline["basis"], {"items": [], "review_fingerprint": None},
                status="pending", issues=[issue],
            )
            return {
                "status": "pending", "automatic": automatic,
                "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
                "receipt": receipt, "issues": [issue],
                "ownership_audit": ownership_audit,
            }
        if any(
            execution_support(item["mode"]) != "implemented"
            for item in declarations["items"]
        ):
            unsupported_targets = [
                {"target": copy.deepcopy(item["target"]), "value": {"state": "unsupported"}}
                for item in declarations["items"]
            ]
            review = review_declarations(
                declarations, current_basis=baseline["basis"],
                current_targets=unsupported_targets, plan_version=plan["plan_version"],
            )
            issues = [
                {**issue, "override_id": row["override_id"]}
                for row in review["items"] for issue in row["issues"]
            ]
            receipt = build_execution_receipt(
                baseline["basis"], review, status="pending", issues=issues
            )
            return {
                "status": "pending", "automatic": automatic,
                "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
                "receipt": receipt, "issues": issues,
                "ownership_audit": ownership_audit,
            }
        targets, claims, relation_proofs = target_rows(
            plan,
            automatic,
            declarations,
            additional_ownership_claims=additional_ownership_claims,
        )
        candidates = []
        for item, row in zip(declarations["items"], targets, strict=True):
            absent = row["value"] == {"state": "absent"}
            owner = None if absent else baseline_owner(row["target"])
            candidates.append({
                "override_id": item["override_id"],
                "target": copy.deepcopy(row["target"]),
                "automatic_target_fingerprint": fingerprint(row["value"]),
                "automatic_owner_id": None if absent else automatic_owner_id(row["target"]),
                "current_owner_id": None if owner is None else owner[0],
                "current_owner_kind": None if owner is None else owner[1],
            })
        review = review_declarations(
            declarations,
            current_basis=baseline["basis"],
            current_targets=targets,
            plan_version=plan["plan_version"],
        )
        if review["status"] != "declaration_ready":
            issues = [
                {**issue, "override_id": row["override_id"]}
                for row in review["items"] for issue in row["issues"]
            ]
            receipt = build_execution_receipt(
                baseline["basis"], review, status="pending", issues=issues,
                target_candidates=candidates,
            )
            return {
                "status": "pending", "automatic": automatic,
                "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
                "receipt": receipt, "issues": issues,
                "ownership_audit": ownership_audit,
            }
        ownership = apply_ownership_transaction(claims, declarations["items"])
        effective = copy.deepcopy(automatic)
        applied = [_apply_one(effective, item) for item in declarations["items"]]
        _validate_effective(effective)
        binding_overlay = build_binding_overlay(
            baseline["basis"], relation_proofs, applied
        ) if relation_proofs else None
        receipt = build_execution_receipt(
            baseline["basis"], review, status="applied", ownership=ownership,
            applied=applied, effective_execution_fingerprint=fingerprint(effective),
            target_candidates=candidates, binding_overlay=binding_overlay,
        )
        return {
            "status": "applied", "automatic": automatic, "effective": effective,
            "basis": baseline["basis"], "receipt": receipt, "issues": [],
            "ownership_audit": {
                "contract": "calculation-coverage-ownership-audit-v1",
                "status": "verified",
                "claims": copy.deepcopy(ownership["claims"]),
                "ownership_ledger_fingerprint": ownership[
                    "ownership_ledger_fingerprint"
                ],
            },
        }
    except OverrideDeclarationError as exc:
        issue = {"code": exc.reason, "path": exc.path or FIELD, "message": str(exc)}
        review = {"items": [], "review_fingerprint": None}
        receipt = build_execution_receipt(
            baseline["basis"], review, status="pending", issues=[issue]
        )
        return {
            "status": "pending", "automatic": automatic,
            "effective": copy.deepcopy(automatic), "basis": baseline["basis"],
            "receipt": receipt, "issues": [issue],
            "ownership_audit": ownership_audit,
        }
