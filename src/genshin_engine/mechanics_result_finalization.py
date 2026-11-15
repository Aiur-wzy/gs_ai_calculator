"""mechanics result finalization in the single Mechanics execution pipeline."""

from __future__ import annotations

import copy

from genshin_engine.damage_report_window import (
    filter_report_template,
    resolve_report,
    tick,
)
from genshin_engine.mechanics_execution_context import MechanicsExecutionContext
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices


def project_execution_result(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    from genshin_engine.runtime_event_match import finalize_runtime_matches
    finalize_runtime_matches(run.executor)
    from genshin_engine.runtime_reference_audit import audit_of
    reference_audit = audit_of(run.executor)
    if reference_audit is not None and not reference_audit.finish(run.executor,
            execution_complete=run.simulation_result['status'] in ('passed', 'unproven')):
        run.output.update(status='invalid', result=None, resolved_template=None,
            rotation_check=run.simulation_result, mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples)
        run.output['issues'].extend(run.simulation_result['issues'] + run.executor.issues)
        return False
    empty_derivation = services.is_swirl_v2(run.request.mechanics_model) and not run.projected["hits"] and not run.executor.hit_rows
    if empty_derivation:
        run.executor.issues.append(services.issue("no_reported_damage", "无输入命中且没有产生可计入伤害的派生事件，未发布伤害或DPS数字。",
                                      "mechanics_model.application_events", severity="pending"))
    resolved = copy.deepcopy(run.projected)
    resolved["hits"] = run.executor.hit_rows
    resolved["buffs"] = run.executor.projected_buffs
    resolved["loop_duration"] = run.rotation["period"]
    calculated = None
    empty_coverage = None
    if (not run.executor.hit_rows and not empty_derivation
            and run.simulation_result["status"] in ("passed", "unproven") and not run.executor.issues
            and services.candidate_sources_known(run.expanded, run.request.mechanics_model, run.data["period"])):
        candidates = services.collect_damage_candidates(run.expanded, run.data["period"])
        empty_coverage = services.classify_verified_empty_damage(candidates, run.simulation_result,
            period_ticks=run.data["period"], mechanics_model=run.request.mechanics_model,
            issues=run.output["issues"], reported_damage_events=0)
    if run.executor.hit_rows:
        include_attributes = bool(run.executor.attribute_runtime and run.executor.attribute_runtime.multistat)
        first_calculation = services.calculate(resolved, include_attribute_inputs=include_attributes)
        run.executor.special_damage_weighting = services.apply_special_damage_weights(
            resolved, first_calculation, run.executor.special_damage_groups)
        run.executor.trace.extend(copy.deepcopy(run.executor.special_damage_weighting))
        run.executor.special_trace.extend(copy.deepcopy(run.executor.special_damage_weighting))
        calculated = (services.calculate(resolved, include_attribute_inputs=include_attributes)
                      if run.executor.special_damage_weighting else first_calculation)
    report_window = resolve_report(run.executor, run.rotation, run.simulation_result)
    if report_window is not None:
        audit_hits = copy.deepcopy(calculated.get("hits", [])) if calculated else []
        original_count = len(resolved["hits"])
        filtered = filter_report_template(resolved, report_window)
        # An explicit window commonly equals the submitted period. In that
        # case filtering changes no formula input: keep the already validated
        # per-hit result, including explanations and contribution weighting.
        # Any duration, hit, or effect change still runs the full evaluator.
        projection_unchanged = calculated is not None and filtered == resolved
        resolved = filtered
        report_window.update(included_hit_count=len(resolved["hits"]),
                             excluded_hit_count=original_count-len(resolved["hits"]))
        if (report_window["end"] is not None and tick(report_window["end"]) > 0
                and (run.executor.hit_rows or empty_coverage is not None)):
            run.executor.special_damage_weighting = [w for w in run.executor.special_damage_weighting
                if tick(w["t"]) <= tick(report_window["end"])]
            if not projection_unchanged:
                calculated = (services.calculate(resolved, include_attribute_inputs=bool(
                    run.executor.attribute_runtime and run.executor.attribute_runtime.multistat)) if resolved["hits"]
                    else services.verified_empty_result(resolved))
        if calculated is not None:
            report_ids = {h["id"] for h in calculated["hits"]}
            calculated["damage_report_audit_hits"] = [h for h in audit_hits if h["id"] not in report_ids]
            calculated["damage_report_window"] = copy.deepcopy(report_window)
            if report_window["status"] != "complete":
                calculated["summary"]["dps"] = None
    # The projection intentionally has no hand snapshot flag: every effect is
    # frozen to its correctly sampled numeric contribution for this hit.
    run.output.update(status=run.simulation_result["status"], rotation_check=run.simulation_result,
                  mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples,
                  resolved_template=resolved if run.executor.hit_rows or report_window and empty_coverage is not None else None,
                  result=calculated if calculated is not None else {
                      "summary": {"total_damage": 0, "dps": None, "by_skill": {}, "by_character": {}},
                      "hits": [], "buffs_coverage": [], "assumptions": ["没有已验证的计入总伤命中；不能据此认为计划伤害为零。"]})
    for row in run.output["result"]["hits"]:
        expected_attribute = run.executor.attribute_scaling_receipts.get(row["id"])
        if expected_attribute is not None and row["attribute_inputs"]["scaling"] != expected_attribute["value"]:
            raise services.attribute_error("attribute_projection_mismatch", "属性账与真实伤害缩放输入不一致", row["id"])
    if run.fixed_sources:
        run.output.update(fixed_effect_sources=copy.deepcopy(run.fixed_sources), target_condition_trace=run.executor.target_condition_trace)
        run.output["result"].update(fixed_effect_sources=copy.deepcopy(run.fixed_sources),
                                target_condition_trace=copy.deepcopy(run.executor.target_condition_trace))
    run.output["issues"].extend(run.simulation_result["issues"] + run.executor.issues)
    if run.executor.issues and run.output["status"] not in ("invalid", "failed"):
        run.output["status"] = "pending"
        run.simulation_result["status"] = "pending"
        run.simulation_result["energy_check"].update(status="not_verified", cycle_status="not_verified", proven_cycle=None)
        run.simulation_result["energy_check"]["proof_scope"]["kind"] = "checked_windows"
        run.simulation_result["action_check"]["status"] = "not_verified"
    if report_window is None and empty_coverage is not None:
        coverage = empty_coverage
        if coverage is not None:
            run.output["resolved_template"] = resolved
            run.output["result"] = services.verified_empty_result(resolved)
            run.output["result"]["damage_reporting"] = coverage
            if run.fixed_sources:
                run.output["result"].update(fixed_effect_sources=copy.deepcopy(run.fixed_sources),
                    target_condition_trace=copy.deepcopy(run.executor.target_condition_trace))
    run.simulation_result["unchecked_constraints"] = [x for x in run.simulation_result["unchecked_constraints"] if x != "伤害稳态与 Buff 刷新"] + ["未声明的机制完整性", "后续轮次伤害汇总与实战 DPS"]
    run.simulation_result["damage_interpretation"] = "完整显式输入窗口（origin0）的已执行事件数值投影；内部检查窗口不是游戏第一轮，不自动复制动作，不证明实战稳态DPS。"
    run.output["result"]["mechanics"] = {"schema_version": 1, "scope": "verified_first_cycle_prefix",
                                      "complete_first_cycle": run.simulation_result["energy_check"]["completed_cycles"] >= 1,
                                      "execution_status": run.output["status"]}
    if run.executor.elemental:
        run.output["elemental_trace"] = run.executor.elemental.trace
        run.output["result"]["elemental"] = {"ruleset": run.executor.elemental.model["ruleset"], "mode": "simulated",
            "input_window_state": run.executor.elemental.window_state,
            "final_checked_state": run.executor.elemental.state(), "trace": run.executor.elemental.trace}
        if services.is_freeze_reference(run.request.mechanics_model):
            run.output["result"]["elemental"].update(evidence_tier="experimental_reference",
                reference_contract=copy.deepcopy(services.reference_contract),
                reference_acceptance=copy.deepcopy(run.executor.elemental.model["reference_acceptance"]))
        if services.is_swirl_v2(run.request.mechanics_model):
            run.output["result"]["elemental"]["event_counts"] = {"total_scheduled_including_synchronous": run.executor.event_budget_count,
                "synchronous": dict(run.executor.synchronous_counts), "limit": services.event_limit}
    if empty_derivation:
        run.output["result"] = None
    return True


def attach_execution_reports(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> None:
    if run.has_executor and run.executor.captures:
        run.output["snapshot_capture_trace"] = run.executor.capture_trace
        if run.output.get("result") is not None:
            run.output["result"]["snapshot_captures"] = {
                "contract": "action-snapshot-capture-v1", "trace": copy.deepcopy(run.executor.capture_trace),
                "storage_bytes": run.executor.capture_bytes, "max_storage_bytes": services.capture_contract.MAX_SNAPSHOT_CAPTURE_BYTES,
                "total_scheduled_events": run.executor.event_budget_count, "max_events": services.event_limit}
    if run.has_executor:
        from genshin_engine.target_condition_evidence import attach_target_observations
        attach_target_observations(run.output, run.executor)
        from collections import Counter
        executed = [row for row in run.executor.trace if row.get('type') in ('hit', 'reaction_damage')
                    and row.get('status') == 'executed']
        dispositions = Counter(row.get('report_disposition', 'unknown') for row in executed)
        final_hits = run.output.get('result', {}).get('hits', []) if isinstance(run.output.get('result'), dict) else []
        counts = {'contract': 'damage-execution-counts-v1', 'scope': 'engine_execution_only',
            'executed': len(executed), 'dispositions': dict(dispositions),
            'included_before_report_cutoff': dispositions.get('included_before_report_cutoff', 0),
            'included_after_report_cutoff': len(final_hits) if run.output.get('result') is not None else None,
            'note': '上游作者辅助或App预先裁掉的输入不在引擎可见范围；不能据此声称没有前层排除。'}
        run.output['damage_execution_counts'] = counts
        if isinstance(run.output.get('result'), dict):
            run.output['result']['damage_execution_counts'] = counts
        if run.executor.target_status_runtime.known:
            report = {'contract': 'declared-target-status-v1', 'trace': copy.deepcopy(run.executor.target_status_runtime.trace),
                      'evidence_tier': 'declared_status_execution'}
            run.output['target_status'] = report
            if isinstance(run.output.get('result'), dict):
                run.output['result']['target_status'] = report
        services.attach_shared_pool_report(run.output, run.executor, services.event_limit)
        services.attach_attribute_report(run.output, run.executor, services.event_limit)
        services.attach_state_runtime_report(run.output, run.executor, services.event_limit)
        services.attach_special_reaction_report(run.output, run.executor, services.event_limit)
        services.attach_buff_state_timeline(run.output, run.request.mechanics_model, run.projected, run.rotation["period"])
        if run.executor.resource_reactions:
            run.executor.resource_reactions.attach(run.output)

