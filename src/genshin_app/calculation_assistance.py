"""Optional read-only explanations of declared inputs and actual execution.

No finding produced here changes engine qualification, user input or approval.
Unknown game facts remain unknown; this module never supplies character rules.
"""
from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from itertools import chain

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.failure_analysis import failure_outline

CONTRACT = "genshin-calculation-assistance-v1"
MAX_BYTES = CALCULATED_ARTIFACT_BYTES
MAX_EXPECTATIONS = 256
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def assistance_contract():
    from .declared_observations import observation_contract
    return {"contract": CONTRACT, "authority": "advisory_only", "candidate_effect": "none",
        "acceptance_effect": "none", "views": ["energy", "coverage", "all"],
        "request": {"view": "all (default), energy or coverage", "limit": "1..200; default50",
            "offset": "0-based row offset; default0", "expected": "optional source inventory: [{id,input_refs?,execution_ids?,note?}]",
            "observations": "optional explicit saved-evidence comparisons; see declared_observations"},
        "declared_observations": observation_contract(),
        "energy": {"report_cutoff": "在已验证的伤害统计截止时点，按实际到账和扣能流水读取余额；完整检查后的余额单独保留。"},
        "coverage": {"effect_matches": "按规则和效果ID对照筛选、受益者、应用流水及保存的逐击引用，可分页。",
                     "window": "未执行或不完整窗口中的未观察项保持未知；完整窗口中的零只表示未观察到应用。"},
        "limits": {"input_bytes": MAX_BYTES, "request_bytes": MAX_REQUEST_BYTES,
                   "response_bytes": MAX_RESPONSE_BYTES, "expectations": MAX_EXPECTATIONS},
        "interpretation": "只解释已声明输入和保存流水；未观察到不等于未触发，缺来源不等于零。"
                          "不要求填写预期表，不将提示写入missing_inputs，不修改计算资格。"}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _page(rows, limit, offset):
    return {"total": len(rows), "offset": offset, "items": rows[offset:offset + limit],
            "next_offset": offset + limit if offset + limit < len(rows) else None}


def _request(value):
    value = {} if value is None else value
    try:
        if len(json_bytes(value)) > MAX_REQUEST_BYTES:
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise PlanError("辅助查询须为256KiB以内的有限JSON", "invalid_assistance_query") from None
    if (not isinstance(value, dict) or set(value) - {"view", "limit", "offset", "expected", "observations"}
            or value.get("view", "all") not in {"all", "energy", "coverage"}
            or type(value.get("limit", 50)) is not int or not 1 <= value.get("limit", 50) <= 200
            or type(value.get("offset", 0)) is not int or value.get("offset", 0) < 0):
        raise PlanError("辅助查询需要有效view、limit(1..200)与offset(>=0)", "invalid_assistance_query")
    expected = value.get("expected", [])
    if not isinstance(expected, list) or len(expected) > MAX_EXPECTATIONS:
        raise PlanError("辅助预期列表须为0..256项", "invalid_assistance_query")
    for row in expected:
        if (not isinstance(row, dict) or set(row) - {"id", "input_refs", "execution_ids", "note"}
                or not isinstance(row.get("id"), str) or not 1 <= len(row["id"]) <= 256
                or any(not isinstance(row.get(key, []), list) or len(row.get(key, [])) > 128
                    or any(not isinstance(item, str) or len(item) > 2048 for item in row.get(key, []))
                    for key in ("input_refs", "execution_ids"))
                or "note" in row and (not isinstance(row["note"], str) or len(row["note"]) > 4096)):
            raise PlanError("辅助预期项仅接受id、input_refs、execution_ids和note", "invalid_assistance_query")
    from .declared_observations import validate_observations
    validate_observations(value.get("observations", []))
    return copy.deepcopy(value)


def _snapshot(value, plan):
    if not isinstance(value, dict) or len(json_bytes(value)) > MAX_BYTES:
        raise PlanError("辅助输入须为有界JSON对象", "invalid_assistance_input")
    raw = value.get("calculated", value)
    if value.get("kind") == "genshin-calculation-diagnostic" and value.get("schema_version") == 1:
        raw = value.get("diagnostic")
    if not isinstance(raw, dict):
        raise PlanError("辅助输入的计算或诊断内容须为对象", "invalid_assistance_input")
    source_plan = plan if plan is not None else raw.get("plan")
    if source_plan is None and raw.get("kind") == "genshin-plan":
        source_plan = raw
    source_plan = source_plan if isinstance(source_plan, dict) else {}
    if len(json_bytes(source_plan)) > 8 * 1024 * 1024:
        raise PlanError("辅助方案输入超过现有8MiB原生输入范围", "invalid_assistance_input")
    execution = raw.get("result")
    if not isinstance(execution, dict):
        execution = raw.get("details") if raw.get("code") == 1 else raw
    execution = execution if isinstance(execution, dict) else {}
    report = raw.get("report") if isinstance(raw.get("report"), dict) else {}
    return execution, source_plan, report


def _report_cutoff_energy(execution, rotation, model, log):
    window = execution.get("damage_report_window")
    if not isinstance(window, dict) or window.get("contract") != "damage-report-window-v1":
        return None
    end = window.get("end")
    scope = {"t": end, "boundary": "inclusive", "state": "not_verified", "balances": None,
             "source": "saved_energy_ledger", "message": "统计截止处的余额未验证。"}
    if (not _finite(end) or window.get("status") != "complete"
            or rotation.get("energy_check", {}).get("completed_cycles", 0) < 1):
        return scope
    characters = rotation.get("characters") or model.get("characters") or []
    balances = {row["id"]: row["initial_energy"] for row in characters
                if isinstance(row, dict) and isinstance(row.get("id"), str) and _finite(row.get("initial_energy"))}
    if not balances or len(balances) != len(characters) or not log:
        return scope

    def record(cid, before, after):
        if (cid not in balances or not _finite(before) or not _finite(after)
                or after < 0 or balances[cid] != before):
            return False
        balances[cid] = after
        return True

    # This is a projection of one existing ledger in its recorded order.
    # Action admissions are authoritative; their later spend rows are mirrors.
    for row in log:
        if not isinstance(row, dict) or not _finite(row.get("t")):
            return scope
        if row["t"] > end:
            break
        kind, status = row.get("type"), row.get("status")
        if row.get("source_cycle", row.get("cycle", 0)) != 0:
            continue
        if kind not in {"action", "energy_direct", "energy_credit", "energy_spend"}:
            continue
        if status != "passed":
            return scope
        if kind == "action" and isinstance(row.get("energy_admission"), dict):
            debit = row["energy_admission"]
            if not record(row.get("character"), debit.get("before"), debit.get("after")):
                return scope
        elif kind == "energy_spend" and not row.get("committed_at_action_start"):
            if not record(row.get("character"), row.get("before"), row.get("after")):
                return scope
        elif kind in {"energy_direct", "energy_credit"}:
            gains = row.get("energy")
            if not isinstance(gains, list) or not all(
                    isinstance(gain, dict) and record(gain.get("character"), gain.get("before"), gain.get("after"))
                    for gain in gains):
                return scope
    return {**scope, "state": "observed", "balances": balances,
            "message": "仅包含统计截止及此前实际到账与扣除。完整检查后的余额可能不同。"}


def energy_summary(execution, plan=None):
    plan = plan or {}
    rotation = execution.get("rotation_check", execution)
    rotation = rotation if isinstance(rotation, dict) else {}
    model = rotation.get("resolved_rotation_model") or plan.get("rotation_model") or {}
    abilities = {row["id"]: row for row in model.get("abilities", []) if isinstance(row, dict) and "id" in row}
    actions = {row["id"]: row for row in model.get("actions", []) if isinstance(row, dict) and "id" in row}
    events = {row["id"]: row for row in model.get("events", []) if isinstance(row, dict) and "id" in row}
    log = rotation.get("event_log", [])
    log = log if isinstance(log, list) else []
    explicit_spends = {}
    for row in log:
        if isinstance(row, dict) and row.get("type") == "energy_spend":
            event = events.get(row.get("id"), {})
            explicit_spends[(row.get("source_cycle", 0), event.get("action_id"))] = row
    income, income_ids, bursts = defaultdict(float), defaultdict(list), []
    for row in log:
        if not isinstance(row, dict):
            continue
        for gain in row.get("energy", []) if isinstance(row.get("energy"), list) else []:
            if isinstance(gain, dict) and _finite(gain.get("gain")) and isinstance(gain.get("character"), str):
                income[gain["character"]] += gain["gain"]
                income_ids[gain["character"]].append(row.get("id"))
        action_id = row.get("action_id", row.get("id"))
        ability = abilities.get(actions.get(action_id, {}).get("ability_id"), {})
        if row.get("type") != "action" or row.get("action_type_id", ability.get("action_type_id")) != "action.elemental_burst":
            continue
        character = row.get("character", ability.get("character"))
        contract = ability.get("energy") or {}
        admitted = row.get("energy_admission") or {}
        spend = explicit_spends.get((row.get("source_cycle", 0), action_id), {})
        before = admitted.get("before", row.get("before"))
        sample = "action_start" if _finite(before) else "unknown"
        if not _finite(before) and _finite(spend.get("before")):
            before, sample = spend["before"], "declared_spend_event; not proof of action-start balance"
        required = row.get("required", contract.get("required", spend.get("required")))
        amount = admitted.get("amount", contract.get("amount", spend.get("amount")))
        deficit = max(0, max(required, amount or 0) - before) if _finite(before) and _finite(required) else None
        bursts.append({"action_id": action_id, "run_id": row.get("run_id"), "character": character,
            "t": row.get("t"), "source_cycle": row.get("source_cycle"), "status": row.get("status"),
            "required": required, "spend": amount, "before": before,
            "after": admitted.get("after", spend.get("after")), "deficit": deficit,
            "balance_sample": sample, "credited_since_previous_q": None if row.get("status") == "not_verified" else income.get(character, 0),
            "credit_event_ids": income_ids[character][:16], "credit_event_count": len(income_ids[character]),
            "credit_event_ids_truncated": len(income_ids[character]) > 16,
            "income_scope": "actual saved credit log only; does not verify game source legality"})
        income[character] = 0
        income_ids[character] = []
    observed_actions = {row["action_id"] for row in bursts}
    planned_bursts = []
    for action in actions.values():
        ability = abilities.get(action.get("ability_id"), {})
        if ability.get("action_type_id") != "action.elemental_burst":
            continue
        declared_energy = ability.get("energy") or {}
        planned_bursts.append({"action_id": action.get("id"), "character": ability.get("character"),
            "t": action.get("start"), "required": declared_energy.get("required"),
            "spend": declared_energy.get("amount"), "execution_observed": action.get("id") in observed_actions})
        if action.get("id") not in observed_actions:
            bursts.append({"action_id": action.get("id"), "character": ability.get("character"),
                "t": action.get("start"), "required": declared_energy.get("required"),
                "spend": declared_energy.get("amount"), "status": "not_executed_or_not_recorded",
                "before": None, "after": None, "deficit": None, "balance_sample": "unknown",
                "credited_since_previous_q": None, "credit_event_ids": [], "credit_event_count": None})
    resource_counts = Counter(event.get("type") for event in events.values())
    cutoff = _report_cutoff_energy(execution, rotation, model, log)
    return {"state": "observed" if log else "not_checked", "bursts": bursts,
        "planned_burst_count": len(planned_bursts), "observed_burst_count": len(observed_actions),
        "declared_income_event_count": sum(resource_counts[key] for key in ("energy_spawn", "energy_credit", "energy_direct")),
        "declared_income_scope": "explicit rotation events only; derived or conditional income needs execution evidence",
        "planned_bursts": planned_bursts,
        "energy_check": copy.deepcopy(rotation.get("energy_check", {})),
        "characters": [{key: row[key] for key in ("id", "energy_recharge", "energy_capacity", "initial_energy", "final_energy") if key in row}
            for row in rotation.get("characters", []) if isinstance(row, dict)],
        **({"report_cutoff": cutoff} if cutoff is not None else {}),
        "interpretation": "充能转伤封顶不证明循环；只据真实逐Q余额检查当前输入，不外推未输入的下一轮。"}


def _pointer_exists(value, pointer):
    from genshin_app.cli_capability_query import _read
    try:
        _read(value, pointer)
        return True
    except PlanError:
        return False


def _effect_match_rows(execution, plan, trace):
    def object_field(parent, key):
        value = parent.get(key)
        return value if isinstance(value, dict) else {}

    def rows(parent, key):
        value = parent.get(key)
        return value if isinstance(value, list) else []

    applied, rejected = Counter(), Counter()
    sample, projected = {}, defaultdict(set)
    for event in trace:
        pair = (event.get("rule_id"), event.get("effect_id"))
        if not all(isinstance(value, str) for value in pair):
            continue
        if event.get("type") == "effect_applied":
            applied[pair] += 1
            sample.setdefault(pair, event.get("event_id"))
            if isinstance(event.get("projected_effect_id"), str):
                projected[pair].add(event["projected_effect_id"])
        elif event.get("type") == "effect_rejected":
            rejected[pair] += 1
    hit_matches = Counter()
    for hit in rows(execution, "hits"):
        if not isinstance(hit, dict):
            continue
        ids = {ref["effect_id"] for ref in rows(hit, "matched_effect_refs")
               if isinstance(ref, dict) and isinstance(ref.get("effect_id"), str)}
        hit_matches.update(ids)
    mechanics = object_field(plan, "mechanics_model")
    rules = rows(mechanics, "rules")
    complete = (isinstance(execution.get("mechanics_trace"), list)
                and object_field(execution, "mechanics").get("complete_first_cycle") is True
                and object_field(execution, "damage_report_window").get("status", "complete") == "complete")
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            continue
        for effect in rows(rule, "effects"):
            if not isinstance(effect, dict) or not isinstance(effect.get("id"), str):
                continue
            pair = (rule["id"], effect["id"])
            selector = object_field(effect, "filter")
            damage = sum(hit_matches[key] for key in projected[pair])
            yield {"rule_id": pair[0], "effect_id": pair[1], "owner": rule.get("owner"),
                   "beneficiaries": rule.get("beneficiaries"),
                   "benefit_type_id": effect.get("benefit_type_id"), "attribute_domain": effect.get("domain"),
                   "event_domain": selector.get("event"), "damage_type_ids": selector.get("damage_type_ids"),
                   "reaction_types": selector.get("reaction_types"),
                   "reaction_elements": selector.get("reaction_elements"), "applied_trace_rows": applied[pair],
                   "rejected_trace_rows": rejected[pair], "reported_hit_refs": damage,
                   "sample_event_id": sample.get(pair),
                   "match_state": ("application_observed" if applied[pair] else
                                   "zero_observed_in_complete_window" if complete else "not_observed_in_available_trace")}


def _coverage(execution, plan, report, expected):
    rotation = execution.get("rotation_check") or {}
    trace = [row for row in chain(*(execution.get(field) or []
             for field in ("mechanics_trace", "sampling_trace", "attribute_trace", "special_reaction_trace")),
             rotation.get("event_log") or []) if isinstance(row, dict)]
    identities = {value for row in trace for key, value in row.items()
        if key in {"id", "event_id", "action_id", "run_id", "rule_id", "effect_id", "source_id", "buff_id"} and isinstance(value, str)}
    rows = []
    for row in expected:
        missing = [pointer for pointer in row.get("input_refs", []) if not _pointer_exists(plan, pointer)]
        ids = row.get("execution_ids", [])
        rows.append({"id": row["id"], "input": "not_requested" if not row.get("input_refs") else "not_found" if missing else "present",
            "missing_input_refs": missing, "execution": "not_requested" if not ids else "observed" if all(i in identities for i in ids)
                else "not_observed_in_saved_trace" if trace else "unknown",
            "unobserved_execution_ids": [i for i in ids if i not in identities], "note": row.get("note"),
            "interpretation": "execution仅表示ID出现在流水，不代表触发、进入公式或增加伤害；实际消费见observations。"})
    parts = [{"binding_id": binding.get("binding_id"), "part_id": part.get("part_id"),
              "state": part.get("state"), "reason": part.get("reason"),
              "interpretation": "excluded理由由作者提供；不能据此认定已含、不适用或用户要求排除" if part.get("state") == "excluded" else "declared input"}
        for binding in plan.get("effect_bindings", []) if isinstance(binding, dict)
        for part in binding.get("parts", []) if isinstance(part, dict)]
    dispositions = Counter(row["state"] for row in parts)
    declared_hits = plan.get("manual_template", {}).get("hits", [])
    damage_rows = execution.get("rows", execution.get("hits", []))
    from genshin_app.damage_count_observation import observe_damage_counts
    return {"expected": rows, "effect_parts": parts, "effect_part_counts": dict(dispositions),
        "effect_matches": list(_effect_match_rows(execution, plan, trace)),
        "effect_match_scope": "应用流水和最终逐击引用分别计数；未观察到不能证明效果不存在或未触发。",
        "damage_stage_counts": observe_damage_counts(plan, execution, report),
        "counts": {"input_actions": len((plan.get("rotation_model") or {}).get("actions", [])),
            "input_manual_hits": len(declared_hits),
            "saved_damage_rows": len(damage_rows) if isinstance(damage_rows, list) else None,
            "saved_trace_rows": len(trace)},
        "panels": [{"character": row.get("id"), "input_stats": copy.deepcopy(row.get("stats", {}))}
            for row in plan.get("manual_template", {}).get("characters", []) if isinstance(row, dict)],
        "panel_composition_available": bool(report.get("contribution_report")),
        "sample_navigation": {"tool": "gs_review_result", "cli": "equipment review",
            "note": "用实际伤害/取样/属性流水按首击、属性变化、刷新、到期、切人和第二轮边界抽查；不把常量面板当动态收益。"},
        "interpretation": "数量只按各自保存对象统计，不把输入次数当执行次数，不证明游戏机制完整。"}


def _resource_hints(plan):
    events = (plan.get("rotation_model") or {}).get("events", [])
    groups, hints = defaultdict(list), []
    for event in events:
        if isinstance(event, dict) and event.get("type") == "energy_spawn":
            groups[(event.get("action_id"), event.get("t"), event.get("element"), event.get("particle_type"))].append(event.get("id"))
    for key, ids in groups.items():
        if len(ids) > 1:
            hints.append({"kind": "possible_duplicate_spawn", "event_ids": ids, "parent_action_id": key[0],
                "t": key[1], "message": "同一父动作和时点有多组同类产球；可能合法，请核对来源/共享冷却后自行决定。"})
    return hints


def inspect_calculation_assistance(value, request=None, *, plan=None):
    req = _request(request)
    try:
        execution, source_plan, report = _snapshot(value, plan)
    except (TypeError, ValueError, RecursionError) as error:
        if isinstance(error, PlanError):
            raise
        raise PlanError("辅助输入须为有限JSON，不能以未知数值生成结论", "invalid_assistance_input") from None
    limit, offset, view = req.get("limit", 50), req.get("offset", 0), req.get("view", "all")
    output = {"contract": CONTRACT, "authority": "advisory_only", "candidate_effect": "none", "acceptance_effect": "none",
        "input_fingerprint": fingerprint(input_view(source_plan)) if source_plan else None,
        "execution_fingerprint": fingerprint(execution), "validation": "read_only_projection; no new execution/approval proof",
        "failure_summary": failure_outline(execution), "view": view}
    if view in {"energy", "all"}:
        energy = energy_summary(execution, source_plan)
        energy["bursts"] = _page(energy["bursts"], limit, offset)
        output["energy"] = energy
        output["resource_hints"] = _page(_resource_hints(source_plan), limit, offset)
        output["declared_resource_events"] = _page([{key: row[key] for key in
            ("id", "type", "t", "credit_at", "action_id", "spawn_id", "count", "lost_count", "count_mode",
             "count_distribution", "element", "particle_type", "amounts", "repeat", "status", "source") if key in row}
            for row in (source_plan.get("rotation_model") or {}).get("events", []) if isinstance(row, dict)
                and row.get("type") in {"energy_spawn", "energy_credit", "energy_direct"}], limit, offset)
    if view in {"coverage", "all"}:
        from genshin_app.effect_observation import observe_effects
        observed = observe_effects(source_plan, execution, report)
        output["observations"] = {**observed,
            "effects": _page(observed["effects"], limit, offset),
            "resource_credits": _page(observed["resource_credits"], limit, offset),
            "external": _page(observed["external"], limit, offset)}
        coverage = _coverage(execution, source_plan, report, req.get("expected", []))
        coverage["expected"] = _page(coverage["expected"], limit, offset)
        coverage["effect_parts"] = _page(coverage["effect_parts"], limit, offset)
        coverage["effect_matches"] = _page(coverage["effect_matches"], limit, offset)
        output["coverage"] = coverage
    if req.get("observations"):
        from .declared_observations import observe_declared
        from .effect_observation import observe_effects
        effects = (observed if view in {"coverage", "all"} else
                   observe_effects(source_plan, execution, report)) if any(
                       row["source"]["resource"] == "effects" for row in req["observations"]) else {}
        output["declared_observations"] = observe_declared(
            req["observations"], plan=source_plan, result=execution, report=report,
            effects=effects, limit=limit, offset=offset)
    return output


def diagnose_assistance(value, request, *, plan=None, db_path=None, candidate_session=None, energy_intent=None):
    """The same optional diagnose operations behind CLI and native routing."""
    if not isinstance(request, dict):
        raise PlanError("辅助诊断请求须为对象", "invalid_assistance_query")
    mode = request.get("view")
    payload = {key: val for key, val in request.items() if key != "view"}
    if mode == "assistance":
        payload["view"] = payload.pop("section", "all")
        output = inspect_calculation_assistance(value, payload, plan=plan)
    elif mode in {"energy_trials", "input_trials"}:
        from genshin_app.energy_trials import trial_energy_recharge
        if db_path is None:
            raise PlanError("ER试算需要当前App数据库路径", "invalid_energy_trial_input")
        output = trial_energy_recharge(plan, db_path, payload, candidate_session=candidate_session,
                                       energy_intent=energy_intent)
    else:
        raise PlanError("辅助诊断view须为assistance、energy_trials或input_trials", "invalid_assistance_query")
    actual = len(json_bytes(output))
    if actual + 4096 > MAX_RESPONSE_BYTES:
        raise PlanError("辅助回执超过封套；请减少本次试算候选或用limit/offset定向读取，不影响正式计算。",
            "auxiliary_response_limit", {"actual_bytes": actual, "maximum_bytes": MAX_RESPONSE_BYTES,
                "recovery": {"action": "narrow_auxiliary_request", "retry_same_request": False}})
    return output
