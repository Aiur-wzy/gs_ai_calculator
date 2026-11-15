"""Read-only ER candidates executed by the real shared calculation service.

The caller chooses trial allocations. This does not optimize globally, insert
resources, change the original plan, or re-approve stale author/trust evidence.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from genshin_app.calculation_assistance import (
    MAX_RESPONSE_BYTES,
    energy_summary,
    inspect_calculation_assistance,
)
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.provisional_entities import ProvisionalEntityError
from genshin_app.equipment.schema import STATS, PlanError
from genshin_engine.execution_control import (
    ExecutionCancelled,
    check_execution_cancelled,
)

CONTRACT = "genshin-energy-trials-v1"
MAX_TRIALS = 64
MAX_REQUEST_BYTES = 256 * 1024


def energy_trials_contract():
    return {"contract": CONTRACT, "authority": "advisory_only", "candidate_effect": "none", "acceptance_effect": "none",
        "request": {"trials": "1..64 explicit {id,stats?:{character:{stat:value}},operations?:[ordinary input patch],note?,roll_costs?}; stats or operations required",
            "include_baseline": "boolean, default true", "note": "optional task explanation",
            "observations": "optional common declared-observation requests evaluated against each actual calculated copy"},
        "stats": list(STATS), "units": "ER is ratio (2=200%); other stats use the normal panel contract",
        "limits": {"trials": MAX_TRIALS, "request_bytes": MAX_REQUEST_BYTES, "response_bytes": MAX_RESPONSE_BYTES},
        "execution": "Each candidate calls the actual run_plan_calc; no income division, monotonicity assumption or automatic particles.",
        "input_variants": "operations use the same protected input editor. Compare an explicit resource delay, an existing effect enabled/removed, or another declared assumption; no result edits or source re-approval. Original input is unchanged.",
        "execution_qualification": {"when": "an energy_intent is provided by the calling workflow",
            "actual": "execution_passed / generic_engine_validation / energy_validation, with actual issue rows",
            "formal": "formal_result_qualification=not_applicable; no can_accept field on the auxiliary qualification",
            "public_facts": "App-only opaque context validates the actual fresh result and unchanged original authority; no formal usage receipt is required or minted for the trial"},
        "scope": "Explicit panel/manual actors only. Cost is author-declared comparison metadata, not proof of a legal artifact budget. "
                 "Changes can invalidate attribute/source evidence; retain rejection without re-signing. Original trusted public facts "
                 "can be resolved through an App-issued read-only projection for explicit panel-stat changes only; "
                 "no trial proof becomes formal approval or modifies the original authority ledger. "
                 "Only tested candidates are reported, never a globally minimum/optimal ER. No formal result is published."}


def _request(request):
    def invalid(message, path, expected, actual=None):
        raise PlanError(message, "invalid_energy_trial_request", {"path": path, "expected": expected,
            "actual": actual, "execution_state": "not_started", "recovery": {
                "action": "correct_trial_request", "retry_same_request": False,
                "example": {"trials": [{"id": "er200", "stats": {"actor-id": {"energy_recharge": 2}}}],
                            "include_baseline": True}}})
    if (not isinstance(request, dict) or set(request) - {"trials", "include_baseline", "note", "observations"}
            or type(request.get("include_baseline", True)) is not bool
            or not isinstance(request.get("trials"), list) or not 1 <= len(request["trials"]) <= MAX_TRIALS):
        invalid("试算需要trials数组，不是单个角色面板；本次未执行任何计算。", "/trials", "1..64 {id,stats或operations}；include_baseline为boolean")
    try:
        if len(json_bytes(request)) > MAX_REQUEST_BYTES:
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise PlanError("ER试算请求须为256KiB以内的有限JSON", "invalid_energy_trial_request") from None
    from genshin_app.declared_observations import validate_observations
    validate_observations(request.get("observations", []))
    seen = set()
    for index, trial in enumerate(request["trials"]):
        if (not isinstance(trial, dict) or set(trial) - {"id", "stats", "operations", "roll_costs", "note"}
                or not isinstance(trial.get("id"), str) or not 1 <= len(trial["id"]) <= 128
                or trial["id"] in seen or trial["id"] == "baseline"
                or not ({"stats", "operations"} & trial.keys())
                or "stats" in trial and (not isinstance(trial["stats"], dict) or not 1 <= len(trial["stats"]) <= 4)):
            invalid("试算项缺少明确修改，或身份重复。", f"/trials/{index}", "唯一非baseline的id；stats含1..4名角色，或operations含输入修改")
        seen.add(trial["id"])
        if "operations" in trial and (not isinstance(trial["operations"], list)
                or not 1 <= len(trial["operations"]) <= 1024
                or any(not isinstance(op, dict) for op in trial["operations"])):
            invalid("operations需要普通输入修改数组。", f"/trials/{index}/operations", "1..1024 {op,path或selector,value?}")
        for actor, stats in trial.get("stats", {}).items():
            if (not isinstance(actor, str) or not 1 <= len(actor) <= 256 or not isinstance(stats, dict) or not stats
                    or set(stats) - set(STATS) or any(type(v) not in (int, float) or not math.isfinite(v) for v in stats.values())
                    or "energy_recharge" in stats and stats["energy_recharge"] <= 0):
                invalid("试算面板字段或数值不合法，充能效率按小数比例填写。", f"/trials/{index}/stats/{actor}",
                        {"allowed_stats": list(STATS), "energy_recharge": "有限数值且>0；2表示200%"}, stats)
        if "note" in trial and (not isinstance(trial["note"], str) or len(trial["note"]) > 4096):
            raise PlanError("试算说明须为0..4096字符", "invalid_energy_trial_request")
        if "roll_costs" in trial and (not isinstance(trial["roll_costs"], dict)
                or any(not isinstance(k, str) or type(v) not in (int, float) or not math.isfinite(v)
                       for k, v in trial["roll_costs"].items())):
            raise PlanError("roll_costs为作者声明的名称到有限数值映射", "invalid_energy_trial_request")
    return copy.deepcopy(request)


def _apply_stats(plan, trial):
    candidate = copy.deepcopy(plan)
    if trial.get("operations"):
        from genshin_app.input_editing import prepare_input_edit
        candidate = prepare_input_edit(candidate, {"operations": trial["operations"]})
    actors = {row["id"]: row for row in candidate.get("manual_template", {}).get("characters", [])}
    for actor, changes in trial.get("stats", {}).items():
        if actor not in actors:
            raise PlanError("试算角色不在当前输入中", "energy_trial_actor_missing", {"character": actor})
        gear = candidate.get("equipment_inputs", {}).get(actor)
        if gear is not None and gear.get("mode") != "panel":
            raise PlanError("该角色面板由基础和装备重新构造，不能只改显示值；请先提供等价的明确试算面板输入",
                            "energy_trial_panel_not_explicit", {"character": actor})
        actors[actor]["stats"].update(changes)
    # These two fields are Composer caches, not author evidence. Real source
    # fingerprints, classifications and approvals are deliberately untouched.
    candidate["resolution"], candidate["resolved_template"] = {}, None
    return candidate


def _uses_provisional(plan):
    for gear in plan.get("equipment_inputs", {}).values():
        refs = [gear.get("character_entity_ref"), (gear.get("weapon") or {}).get("entity_ref"),
                *[row.get("entity_ref") for row in gear.get("artifact_sets", [])]]
        if any(isinstance(ref, dict) and ref.get("kind") == "provisional" for ref in refs):
            return True
    return False


def _rejection(error):
    from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
    details = error.details if isinstance(error.details, (dict, list)) else {}
    return {"reason": error.reason, **classify_domain_failure(error.reason, details=details),
            **tool_failure_guidance(error.reason, details=details)}


def _qualification_summary(value):
    issues = value.get("issues", [])
    return {**{key: value[key] for key in ("contract", "status", "can_accept", "energy_intent",
              "generic_engine_validation", "event_inventory", "purpose", "execution_passed", "energy_validation",
              "formal_result_qualification", "formal_result_authority") if key in value},
        "issue_count": len(issues), "issues": [{key: row[key] for key in ("code", "path", "message") if key in row}
            for row in issues[:8] if isinstance(row, dict)], "issues_truncated": len(issues) > 8,
        "scope": "actual execution of this tested copy; formal publication is not requested; no approval/adoption authority"}


def trial_energy_recharge(plan, db_path, request, *, candidate_session=None, energy_intent=None):
    """Return tested snapshots; never pass a live writable session to sealing."""
    from genshin_app.calculation_completion import calculation_completion
    from genshin_app.equipment.service import run_plan_calc

    req = _request(request)
    if not isinstance(plan, dict) or plan.get("kind") != "genshin-plan":
        raise PlanError("ER试算需要原生方案输入", "invalid_energy_trial_input")
    # Bounds and finite JSON are the same native input size, without mutating
    # a candidate just to obtain a usable initial template.
    try:
        if len(json_bytes(plan)) > 8 * 1024 * 1024:
            raise ValueError
    except (TypeError, ValueError, RecursionError):
        raise PlanError("试算方案须为8MiB以内的有限JSON", "invalid_energy_trial_input") from None
    source = copy.deepcopy(plan)
    source_hash = fingerprint(input_view(source))
    trials = ([{"id": "baseline", "stats": {}}] if req.get("include_baseline", True) else []) + req["trials"]
    results, cancelled = [], False
    for declaration in trials:
        row = {"id": declaration["id"], "changes": copy.deepcopy(declaration.get("stats", {})),
               "input_operations": copy.deepcopy(declaration.get("operations", [])),
               "roll_costs": copy.deepcopy(declaration.get("roll_costs", {})), "note": declaration.get("note"),
               "adoption_authority": "none"}
        try:
            check_execution_cancelled()
            trial = _apply_stats(source, declaration)
            row["input_fingerprint"] = fingerprint(input_view(trial))
            options = {}
            readonly = _uses_provisional(trial)
            if readonly:
                if declaration.get("operations"):
                    raise PlanError("临时公共实体的输入操作试算尚无等价授权证明；可先只做面板stats试算，或回当前候选正式修改并重新核验。",
                        "energy_trial_authorization_scope_changed", {"category": "request_error", "execution_state": "not_started"})
                if candidate_session is None:
                    raise PlanError("临时公共事实试算需要原App会话核验当前授权；独立文件不能继承批准",
                        "energy_trial_trust_scope_unavailable", {"category": "request_error"})
                from genshin_app.agent_chat.provisional_authority import (
                    prepare_readonly_projection,
                )
                options["_readonly_provisional_context"] = prepare_readonly_projection(
                    candidate_session, trial, declaration.get("stats", {}))
            calculated = run_plan_calc(trial, Path(db_path), candidate_session=None, **options)
            if readonly:
                from genshin_app.calculation_completion import (
                    advisory_execution_completion,
                )
                completion = advisory_execution_completion(calculated)
                row["read_only_public_facts"] = copy.deepcopy(calculated["result"].get("advisory_public_fact_projection"))
            else:
                completion = calculation_completion(calculated)
            row.update(status="tested", calculation=completion,
                energy=energy_summary(calculated["result"], calculated.get("plan", trial)))
            if req.get("observations"):
                from genshin_app.advisory_observation import observe_advisory
                row["observations"] = observe_advisory("input_trial_observations", lambda calculated=calculated, trial=trial: inspect_calculation_assistance(
                    calculated, {"view": "coverage", "observations": req["observations"]},
                    plan=calculated.get("plan", trial))["declared_observations"], fallback={"items": []})
            if energy_intent is not None:
                from genshin_app.calculation_qualification import (
                    qualify_auxiliary_execution,
                )
                qualification = qualify_auxiliary_execution(calculated, energy_intent=energy_intent,
                    readonly_context=options.get("_readonly_provisional_context"))
                row["energy_qualification"] = _qualification_summary(qualification)
            total = calculated["result"].get("calculation_totals", calculated["result"].get("summary", {}))
            if completion.get("execution_admission", completion["task_admission"]) == "accepted":
                row["tested_damage_summary"] = copy.deepcopy(total)
        except ExecutionCancelled:
            row.update(status="cancelled", reason="execution_cancelled")
            results.append(row)
            cancelled = True
            break
        except (PlanError, ProvisionalEntityError) as error:
            row.update(status="incomplete", failure=_rejection(error))
            if error.reason == "mechanics_pending" and isinstance(error.details, dict):
                row["energy"] = energy_summary(error.details, trial)
                row["failure_summary"] = inspect_calculation_assistance(
                    {"code": 1, "reason": error.reason, "details": error.details}, {"view": "energy"}, plan=trial)["failure_summary"]
        results.append(row)
    return {"contract": CONTRACT, "authority": "advisory_only", "candidate_effect": "none", "acceptance_effect": "none",
        "source_input_fingerprint": source_hash, "energy_intent": energy_intent,
        "status": "cancelled" if cancelled else "completed", "requested": len(trials),
        "completed": sum(row["status"] != "cancelled" for row in results),
        "not_tested_ids": [row["id"] for row in trials if row["id"] not in {
            result["id"] for result in results if result["status"] != "cancelled"}], "trials": results,
        "interpretation": "仅比较本次实际测试的输入；缺事实/证据/授权的候选保持未完成。"
                          "不自动补资源或重签属性/来源批准，不给出全局最低ER或最佳配装。"}
