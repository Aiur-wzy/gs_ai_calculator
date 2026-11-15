"""Small read-only failure outline; never invents causal relationships."""
from __future__ import annotations

import math
from collections.abc import Mapping
from itertools import islice

from genshin_app.diagnostic_facts import DIAGNOSTIC_FACT_FIELDS


def _small_fact(value, depth=0, budget=None):
    budget = [4096, False] if budget is None else budget
    if len(budget) == 1:
        budget.append(False)
    budget[0] -= 16
    if budget[0] <= 0:
        budget[1] = True
        return {"detail_omitted": True}
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value[:min(1024, max(0, budget[0] // 4))]
        budget[1] |= len(text) < len(value)
        budget[0] -= len(text.encode("utf-8"))
        return text
    if depth >= 5:
        budget[1] = True
        return {"detail_omitted": True}
    if isinstance(value, Mapping):
        result = {}
        budget[1] |= len(value) > 16
        for key, child in islice(value.items(), 16):
            if budget[0] <= 0:
                budget[1] = True
                result["detail_omitted"] = True
                break
            result[str(key)[:128]] = _small_fact(child, depth + 1, budget)
        return result
    if isinstance(value, (list, tuple)):
        result = []
        budget[1] |= len(value) > 8
        for child in value[:8]:
            if budget[0] <= 0:
                budget[1] = True
                result.append({"detail_omitted": True})
                break
            result.append(_small_fact(child, depth + 1, budget))
        return result
    return None


def _identity(row, *, detailed=True):
    fields = ("code", "reason", "path", "event_id", "action_id", "run_id", "cycle", "t")
    if detailed:
        fields += ("message", "repair_hint", "field", "engine_execution")
    value = {key: row[key][:512] if isinstance(row[key], str) else row[key]
        for key in fields
        if key in row and (isinstance(row[key], str) or type(row[key]) is int
                          or type(row[key]) is float and math.isfinite(row[key]))}
    # Exact producer facts are actionable evidence, unlike a generic message.
    # Preserve them in short receipts without copying arbitrary trace bodies.
    budget = [4096, any(isinstance(row.get(key), str) and len(row[key]) > 512 for key in fields)]
    for source in ((row, row.get("details")) if detailed else ()):
        if not isinstance(source, Mapping):
            continue
        for key in DIAGNOSTIC_FACT_FIELDS:
            if key in source:
                value[key] = _small_fact(source[key], budget=budget)
    if budget[1]:
        value["details_truncated"] = True
    return value


def _rejected_event(row):
    fields = {key: row[key][:512] for key in (
        "id", "type", "status", "character", "action_type_id", "cooldown_group", "reason")
              if isinstance(row.get(key), str)}
    for key in ("before", "required", "deficit", "charges_before", "charges_after", "next_ready_at"):
        value = row.get(key)
        if type(value) is int or type(value) is float and math.isfinite(value):
            fields[key] = value
    return {**_identity(row), **fields}


def failure_outline(details):
    """Use producer order and the actual execution log, not message keywords.

    Extra issues have unknown dependency unless the producer supplies it. A
    later ``not_verified`` event is explicitly not an independent rejection.
    """
    pending, seen, issues, rotation, pointers = [(details, "")], set(), [], None, {}
    while pending:
        current, pointer = pending.pop()
        if not isinstance(current, (Mapping, list, tuple)) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Mapping):
            if isinstance(current.get("rotation_check"), Mapping):
                rotation = current["rotation_check"]
            if (isinstance(current.get("code"), str) or isinstance(current.get("reason"), str)
                    and not any(isinstance(current.get(key), (Mapping, list, tuple)) for key in ("missing_inputs", "issues", "details"))):
                issues.append(current)
                pointers[id(current)] = pointer
            pending.extend((current[key], pointer + "/" + key)
                           for key in reversed(("missing_inputs", "issues", "details")) if key in current)
        else:
            pending.extend((current[i], pointer + "/" + str(i)) for i in range(len(current) - 1, -1, -1))
    if rotation is None and isinstance(details, Mapping) and isinstance(details.get("event_log"), list):
        rotation = details
    log = rotation.get("event_log", []) if rotation is not None else []
    rejected = next((row for row in log if isinstance(row, Mapping) and row.get("status") == "failed"), None)
    unverified = [row for row in log if isinstance(row, Mapping) and row.get("status") == "not_verified"]
    if not issues and rejected is None and not unverified:
        return None
    primary = next((row for row in issues if rejected is not None
        and row.get("event_id", row.get("path")) == rejected.get("id")), issues[0] if issues else None)
    value = {"contract": "genshin-failure-outline-v1", "reported_issue_count": len(issues),
        "primary_reported_issue": _identity(primary) if primary is not None else None,
        "additional_reported_issues": [_identity(row, detailed=False) for row in islice((row for row in issues if row is not primary), 8)],
        "additional_issues_truncated": max(0, len(issues) - (1 if primary is not None else 0)) > 8,
        "dependency_interpretation": "未提供因果证据的其他问题保持独立性未知；未验证事件不等于独立失败。"}
    if primary is not None:
        value["primary_reported_issue"].setdefault("detail_ref", {
            "container": "details", "pointer": pointers[id(primary)]})
    if rejected is not None:
        value["first_rejected_event"] = _rejected_event(rejected)
    if unverified:
        value["subsequent_unverified_event_count"] = len(unverified)
        value["unverified_event_samples"] = [{**_identity(row), "id": row.get("id")} for row in unverified[:3]]
    return value


def _trace_state(result, section, declared):
    saved = result.get(section)
    rows = saved.get("trace") if isinstance(saved, Mapping) else None
    if not isinstance(rows, list):
        legacy = "special_reaction_trace" if section == "special_reactions" else "elemental_trace"
        rows = result.get(legacy)
    if isinstance(rows, list):
        return ("trace_saved" if rows else "trace_empty", len(rows))
    return ("declared_without_trace" if declared else "not_declared", None)


def calculation_feedback(calculated):
    """Bounded description of saved execution, independent of admission."""
    result = calculated.get("result") or {}
    report = calculated.get("report") or {}
    plan = calculated.get("plan") or {}
    completion = calculated.get("completion") or {}
    mechanics = plan.get("mechanics_model") or {}
    hits = result.get("hits") or []
    manual_hits = (plan.get("manual_template") or {}).get("hits")
    elemental, elemental_count = _trace_state(result, "elemental", mechanics.get("elemental_model"))
    special, special_count = _trace_state(result, "special_reactions", mechanics.get("special_reaction_model"))
    feedback = {
        "composition": report.get("state"),
        "composition_missing_count": len(report.get("missing_inputs") or []),
        "execution": completion.get("calculation_status", "unknown"),
        "complete_input_window": completion.get("complete_input_window"),
        "elemental": elemental,
        "special_reactions": special,
        "trace_event_counts": {"elemental": elemental_count, "special_reactions": special_count},
        "reported_hits": len(hits),
        "declared_manual_hits": len(manual_hits) if isinstance(manual_hits, list) else None,
    }
    if feedback["execution"] != "complete":
        outline = failure_outline({"rotation_check": result.get("rotation_check"),
                                   "issues": result.get("mechanics_issues", result.get("issues"))})
        if outline is not None:
            feedback["failure_summary"] = outline
        times = (row.get("t") for row in hits if isinstance(row, Mapping))
        feedback["last_reported_hit_t"] = max(
            (t for t in times if type(t) is int or type(t) is float and math.isfinite(t)), default=None)
    return feedback
