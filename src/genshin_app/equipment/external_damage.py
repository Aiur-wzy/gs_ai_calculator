"""Separate external damage ledger, fresh acceptance and bounded offline consistency.

This authenticates neither game facts nor a self-consistently rewritten document.
It never dispatches events or changes the engine's damage/resource/benefit ledger.
"""
from __future__ import annotations

import copy
import math

from .damage_report_scope import report_includes, reporting_window
from .external_damage_schema import CONTRACT, FIELD, precision_of, ticks, validate_items
from .files import (
    context_view,
    execution_input_view,
    fingerprint,
    input_view,
    json_bytes,
)

MESSAGE = "外部补充伤害的输入依据、执行窗口、明细或合计不一致，请重新复核并计算。"
MAX_BASIS_BYTES = 8 * 1024 * 1024


def _same(a, b):
    if fingerprint(a) != fingerprint(b):
        raise ValueError(MESSAGE)


def _context(plan):
    return context_view(execution_input_view(plan))


def _source_dependencies(components):
    if type(components) is not list:
        raise ValueError(MESSAGE)
    normalized = []
    for row in components:
        # Only this explicit App-generated record has an administrative execution
        # hash. Never strip identically named fields recursively from source data.
        if type(row) is dict and row.get("kind") == "mechanism":
            if set(row) != {"kind", "id", "source_fingerprint", "accepted_fingerprint", "execution_fingerprint"}:
                raise ValueError(MESSAGE)
            row = {key: value for key, value in row.items() if key != "execution_fingerprint"}
        normalized.append(row)
    return sorted(normalized, key=fingerprint)


def _basis(plan, source_components):
    value = {"context": _context(plan), "source_fingerprint": fingerprint(_source_dependencies(source_components))}
    if len(json_bytes([value, source_components])) > MAX_BASIS_BYTES:
        raise ValueError("外部补充的离线业务依据超过8MiB")
    return value


def _accept(item, basis):
    return fingerprint({"contract": CONTRACT, "basis": basis,
                        "item": {k: v for k, v in item.items() if k != "accepted_fingerprint"}})


def _window(context):
    duration = context["manual_template"].get("loop_duration")
    if duration is None or ticks(duration) <= 0:
        raise ValueError("外部补充需要明确且大于零的完整输入窗口")
    model = context.get("rotation_model")
    if model is not None and ticks(model.get("period")) != ticks(duration):
        raise ValueError("外部补充窗口与动作检查窗口不一致")
    return duration


def _report_scope(result, context):
    declaration = (context.get("rotation_model") or {}).get("damage_report")
    window = reporting_window(result, require_complete=True)
    if declaration is None:
        if window is not None:
            raise ValueError(MESSAGE)
        return None
    if (window is None or declaration.get("contract") != window["contract"]
            or declaration.get("mode") != window.get("mode")
            or ticks(_window(context)) != ticks(window["execution_period"])
            or declaration.get("mode") == "explicit"
            and ticks(declaration.get("end")) != ticks(window["end"])):
        raise ValueError(MESSAGE)
    return window


def _ids(value):
    """Only explicit event identity keys, never arbitrary evidence/display strings."""
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            yield value["id"]
        for child in value.values():
            yield from _ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _ids(child)


def _validate_members(items, context, *, template=None, model=None, result=None):
    window = ticks(_window(context))
    scoped = (context.get("rotation_model") or {}).get("damage_report") is not None
    characters = {c["id"] for c in context["manual_template"]["characters"]}
    rotation = context.get("rotation_model") or {}
    actions = {a["id"]: a for a in rotation.get("actions", [])}
    abilities = {a["id"]: a for a in rotation.get("abilities", [])}
    occupied = set(_ids(context)) | set(_ids(template)) | set(_ids(model))
    if result is not None:
        # Explanation references describe existing/externally declared events;
        # they are not additional executable identities or damage ownership.
        occupied.update(_ids({key: value for key, value in _core(result).items()
                              if key != "damage_explanations"}))
    for item in items:
        at = ticks(item["t"])
        if item["character"] not in characters or at > window or at == window and not scoped:
            raise ValueError("外部补充角色不存在或时点超出完整输入窗口")
        if item["event_id"] in occupied or item["id"] in occupied or item["coverage_key"] in occupied:
            raise ValueError("外部补充身份与既有引擎对象冲突，不能替换或重复既有伤害")
        parent = item["action_id"]
        if parent is not None:
            action = actions.get(parent)
            ability = abilities.get(action.get("ability_id")) if action else None
            if (action is None or ability is None or ability.get("character") != item["character"]
                    or ticks(item["t"]) < ticks(action.get("start"))):
                raise ValueError("外部补充父动作不存在、归属不同或发生在父动作之前")
            if result is not None:
                actual = [r for r in result["rotation_check"]["event_log"] if
                          r.get("type") == "action" and r.get("id") == parent and r.get("source_cycle") == 0]
                if (len(actual) != 1 or actual[0].get("status") != "passed"
                        or actual[0].get("character") != item["character"]
                        or ticks(actual[0]["t"]) != ticks(action["start"])):
                    raise ValueError("外部补充父动作没有在本窗口实际成功执行")


def review_external_damage(plan, source_fingerprint, *, source_components, template=None, model=None):
    items = plan.get(FIELD) or []
    if not items:
        return None
    validate_items(items)
    _same(source_fingerprint, fingerprint(sorted(source_components, key=fingerprint)))
    basis = _basis(plan, source_components)
    rows, issues = [], []
    for item in items:
        local, candidate = [], None
        try:
            _validate_members([item], basis["context"], template=template, model=model)
            candidate = _accept(item, basis)
            if item["accepted_fingerprint"] != candidate:
                local.append({"code": "external_damage_confirmation_required", "message": "需核对当前输入、来源及无重复说明后接受外部补充"})
        except ValueError as exc:
            local.append({"code": "external_damage_invalid", "message": str(exc)})
        for issue in local:
            issue.update(path=f"{FIELD}.{item['id']}", action="review_external_damage")
        rows.append({"id": item["id"], "status": "pending" if local else "ready",
                     "accepted_fingerprint": candidate,
                     "required_confirmation": {"accepted_fingerprint": candidate} if candidate else None,
                     "issues": local})
        issues.extend(local)
    return {"contract": CONTRACT, "status": "pending" if issues else "ready",
            "basis_fingerprint": fingerprint(basis), "items": rows, "issues": issues}


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(MESSAGE)
    return value


def _equal_number(a, b):
    if not math.isclose(_number(a), _number(b), rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError(MESSAGE)


def _complete(result, context):
    if _report_scope(result, context) is not None:
        # Optional extra audit cycles cannot invalidate an already complete
        # required origin. The versioned window proof independently replays it.
        return
    check = result.get("rotation_check") or {}
    mechanics = result.get("mechanics") or {}
    interpretation = check.get("damage_interpretation") or {}
    if (any(status in ("failed", "pending", "invalid", "not_verified") for status in
            (check.get("status"), result.get("mechanics_status"), mechanics.get("execution_status")))
            or interpretation.get("kind") == "executed_prefix"
            or context.get("plan_version") == 3 and mechanics.get("complete_first_cycle") is not True
            or context.get("rotation_model") is not None and check.get("status") not in ("passed", "unproven")):
        raise ValueError("外部补充不能用于失败、待验证或未完成的伤害窗口")


def _totals(result, items, context):
    _complete(result, context)
    summary = result["summary"]
    by_character, by_skill, engine = {}, {}, 0.0
    for hit in result["hits"]:
        value = _number(hit["expected"])
        engine += value
        by_character[hit["character"]] = by_character.get(hit["character"], 0.0) + value
        by_skill[hit["skill"]] = by_skill.get(hit["skill"], 0.0) + value
    _equal_number(engine, summary["total_damage"])
    for key, grouped in (("by_character", by_character), ("by_skill", by_skill)):
        if set(summary[key]) != set(grouped):
            raise ValueError(MESSAGE)
        for identity, value in grouped.items():
            _equal_number(summary[key][identity], value)
    scope = _report_scope(result, context)
    duration = scope["end"] if scope is not None else _window(context)
    _equal_number(summary["dps"], engine / duration)
    external = 0.0
    approximate_items = copy.deepcopy(
        summary.get("approximation_disclosures", [])
        if summary.get("precision") == "approximate" else []
    )
    approximate_characters: set[str] = set(
        summary.get("approximate_by_character", [])
        if summary.get("precision") == "approximate" else []
    )
    approximate_skills: set[str] = set(
        summary.get("approximate_by_skill", [])
        if summary.get("precision") == "approximate" else []
    )
    included = [item for item in items if scope is None or report_includes(scope, item["t"])]
    for item in included:
        value = _number(item["expected"])
        external += value
        by_character[item["character"]] = by_character.get(item["character"], 0.0) + value
        by_skill[item["skill"]] = by_skill.get(item["skill"], 0.0) + value
        if precision_of(item) == "approximate":
            approximate_items.append({
                "event_id": item["event_id"],
                "coverage_key": item["coverage_key"],
                "character": item["character"],
                "skill": item["skill"],
                "method": item["precision"]["method"],
                "uncertainty": item["precision"]["uncertainty"],
                "state_effects": item["precision"]["state_effects"],
            })
            approximate_characters.add(item["character"])
            approximate_skills.add(item["skill"])
    total = engine + external
    _number(total)
    totals = {"total_damage": total, "dps": total / duration, "by_character": by_character,
            "by_skill": by_skill, "engine_total_damage": engine, "external_total_damage": external,
            "external_count": len(included), "evidence_tier": "includes_external_calculation"}
    if scope is not None:
        totals.update(external_declared_count=len(items), external_excluded_count=len(items) - len(included),
                      damage_report_window_fingerprint=fingerprint(scope))
    if approximate_items:
        totals.update({
            "precision": "approximate",
            "approximate_event_count": len(approximate_items),
            "approximate_by_character": sorted(approximate_characters),
            "approximate_by_skill": sorted(approximate_skills),
            "approximation_disclosures": approximate_items,
        })
    return totals


def _core(result):
    return {k: v for k, v in result.items() if k not in ("external_damage", "calculation_totals")}


def _proof(result):
    ledger = result["external_damage"]
    return {"contract": CONTRACT, "item_count": len(ledger["items"]),
            "basis_fingerprint": fingerprint(ledger["basis"]),
            "items_fingerprint": fingerprint(ledger["items"]),
            "source_components_fingerprint": fingerprint(ledger["source_components"]),
            "source_fingerprint": fingerprint(sorted(ledger["source_components"], key=fingerprint)),
            "engine_result_fingerprint": fingerprint(_core(result)),
            "calculation_totals_fingerprint": fingerprint(result["calculation_totals"]),
            "evidence_tier": "includes_external_calculation"}


def attach_external_damage(plan, result, report):
    """Mutate only the two supplemental result keys; return their separate proof."""
    items = plan.get(FIELD) or []
    if not items:
        return None
    components = report["external_damage_source_components"]
    review = review_external_damage(plan, report["source_fingerprint"], source_components=components)
    if review["status"] != "ready":
        raise ValueError(MESSAGE)
    basis = _basis(plan, components)
    _validate_members(items, basis["context"], result=result)
    totals = _totals(result, items, basis["context"])
    result["external_damage"] = {"contract": CONTRACT, "items": copy.deepcopy(items), "basis": basis,
                                 "source_components": copy.deepcopy(components)}
    result["calculation_totals"] = totals
    return _proof(result)


def validate_external_damage_result_receipt(result, receipt):
    if not any(key in obj for obj, key in ((result, "external_damage"), (result, "calculation_totals"), (receipt, "external_damage"))):
        return
    try:
        ledger = result["external_damage"]
        if type(ledger) is not dict or set(ledger) != {"contract", "items", "basis", "source_components"} or ledger["contract"] != CONTRACT:
            raise ValueError(MESSAGE)
        items, basis = ledger["items"], ledger["basis"]
        validate_items(items)
        if (not items or type(basis) is not dict or set(basis) != {"context", "source_fingerprint"}
                or len(json_bytes([basis, ledger["source_components"]])) > MAX_BASIS_BYTES or FIELD in basis["context"]):
            raise ValueError(MESSAGE)
        _same(basis["source_fingerprint"], fingerprint(_source_dependencies(ledger["source_components"])))
        _same(fingerprint(sorted(ledger["source_components"], key=fingerprint)), receipt["source_fingerprint"])
        for item in items:
            _same(item["accepted_fingerprint"], _accept(item, basis))
        _validate_members(items, basis["context"], result=result)
        _same(result["calculation_totals"], _totals(result, items, basis["context"]))
        _same(receipt["external_damage"], _proof(result))
        _same(receipt["result_fingerprint"], fingerprint(result))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_external_damage_receipt(plan, result, report, receipt):
    if not (plan.get(FIELD) or report.get("external_damage_review") or result.get("external_damage")
            or "calculation_totals" in result or "external_damage" in receipt):
        return
    validate_external_damage_result_receipt(result, receipt)
    try:
        _same(result["external_damage"]["items"], plan[FIELD])
        components = report["external_damage_source_components"]
        _same(result["external_damage"]["source_components"], components)
        _same(result["external_damage"]["basis"], _basis(plan, components))
        _same(receipt["input_fingerprint"], fingerprint(input_view(plan)))
        _same(report["external_damage_review"], review_external_damage(plan, report["source_fingerprint"], source_components=components))
        _validate_members(plan[FIELD], _context(plan), template=plan["resolved_template"],
                          model=report.get("mechanics_model"), result=result)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
