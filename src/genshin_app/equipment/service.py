"""Native plan runtime shared by CLI and desktop bridge."""
from __future__ import annotations

import copy
import csv
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from genshin_app.equipment.verification_scope import verification_scope
from genshin_app.export import (
    PROVISIONAL_RESULT_FIDELITY,
    PROVISIONAL_RESULT_LABEL,
    _write_xlsx,
    coverage_rows,
    formal_audit_payload_rows,
    formal_calculation_audit_payload,
    hit_rows,
    provisional_entity_rows,
    summary_rows,
)
from genshin_app.presentation import display_names
from genshin_app.rotation_check import attach_rotation_check, check_sheets
from genshin_db.locking import database_lock
from genshin_engine import calculate
from genshin_engine.elemental_schema import FREEZE_RULESET
from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.registry import get_registry

from .composer import compose_equipment_plan
from .damage_reporting_receipt import (
    damage_reporting_contract,
    validate_empty_damage_result_receipt,
)
from .energy_generation_receipt import (
    energy_generation_resource_ledger_fingerprint,
    energy_generation_summary,
    validate_energy_generation_result_receipt,
)
from .external_damage import (
    validate_external_damage_result_receipt,
)
from .external_damage_schema import external_damage_contract
from .files import fingerprint, publish_files, read_plan
from .limited_hit_benefit_receipt import (
    limited_hit_benefit_ledger_fingerprint,
    limited_hit_benefit_summary,
    validate_limited_hit_benefit_result_receipt,
)
from .mechanism_receipt import (
    mechanism_summary,
    validate_mechanism_result_receipt,
)
from .reaction_benefit_receipt import (
    reaction_benefit_records,
    validate_reaction_benefit_shared_receipt,
)
from .schema import (
    COMPOSER_VERSION,
    MATCH,
    PlanError,
    new_plan,
    validate_plan,
)
from .snapshot_capture_receipt import (
    validate_snapshot_capture_result_receipt,
)
from .summary import equipment_rows


def _declared_reaction_verification(template, mechanics_model):
    """Describe reactions that came from the input instead of the elemental runtime.

    A declared reaction is still a valid damage input for compatibility, but it is
    not evidence that aura, application eligibility or the ICD counter was
    replayed.  Keep that distinction machine-readable so both the UI and an Agent
    can require a per-attack review instead of treating the result as simulated.
    """
    if mechanics_model.get("elemental_model") is not None:
        return None
    hit_ids = []
    for index, hit in enumerate(template.get("hits", [])):
        reaction = hit.get("reaction")
        if reaction is None or reaction == {}:
            continue
        hit_ids.append(hit.get("id") or f"hit[{index}]")
    if not hit_ids:
        return None
    shown = hit_ids[:64]
    return {
        "status": "unverified",
        "mode": "declared_not_simulated",
        "declared_reaction_hit_count": len(hit_ids),
        "hit_ids": shown,
        "omitted_hit_count": len(hit_ids) - len(shown),
        "verified_dimensions": ["declared_damage_formula"],
        "unverified_dimensions": [
            "elemental_aura_before_hit",
            "elemental_application_eligibility",
            "icd_counter_identity_and_state",
            "reaction_identity_and_order",
        ],
        "required_follow_up": (
            "按具体攻击组件补齐元素、GU、附着计数标签、规则组与证据，启用元素模型重放；"
            "暂不支持的部分须由 Agent 逐项查证并标为外部补充。"
        ),
    }


def _elemental_runtime_verifications(elemental):
    """Summarize generic elemental execution without asserting character truth."""
    if not isinstance(elemental, dict) or elemental.get("mode") != "simulated":
        return None, None
    trace = elemental.get("trace")
    if not isinstance(trace, list):
        return None, None
    applications = [row for row in trace
                    if isinstance(row, dict) and row.get("type") == "elemental_application"]
    if any(row.get("status") != "executed" for row in applications):
        return None, None
    reactions = [row for row in applications if row.get("reaction_occurred") is True]
    common = {
        "status": "passed" if applications else "failed",
        "coverage_status": "complete_input_window" if applications else "empty_input_window",
        "mode": "elemental_runtime_simulated",
    }
    return (
        {**common, "evaluated_application_count": len(applications),
         "reaction_event_count": len(reactions)},
        {**common, "executed_application_count": len(applications)},
    )


def load_plan(path, db_path=None):
    loaded = read_plan(path)
    if db_path is not None:
        report = compose_equipment_plan(loaded["plan"], db_path)
        loaded.update(plan=report["plan"], report=report)
    else:
        loaded["report"] = None
    return loaded


def require_ready(report):
    from genshin_app.task_capability import CONTRACT, classify_task_issues

    missing_inputs = report.get("missing_inputs", [])
    task_capability = report.get("task_capability")
    if task_capability is None:
        # Compatibility for small compiler tests and reports produced before
        # the trusted-resolution bridge. Legacy rows remain conservative.
        task_capability = classify_task_issues(missing_inputs)
    else:
        normalized = classify_task_issues(missing_inputs)
        if (
            not isinstance(task_capability, dict)
            or task_capability.get("contract") != CONTRACT
            or [row["issue_id"] for row in task_capability.get("issues", [])]
            != [row["issue_id"] for row in normalized["issues"]]
        ):
            raise PlanError(
                "任务能力回执与当前缺项不一致",
                "task_capability_receipt_invalid",
            )
    if task_capability["blocking"]:
        raise PlanError("方案尚有待处理输入，无法计算或导出结果", "missing_input", {
            "missing_inputs": report["missing_inputs"], "state": report["state"],
            "task_capability": task_capability,
            "context_fingerprint": report["context_fingerprint"],
            "effect_requirements": report["effect_requirements"], "match_previews": report["match_previews"],
            **({'static_inclusion_compilations': report['static_inclusion_compilations']}
               if report.get('static_inclusion_compilations') else {}),
            "upstream_compilations": report.get("upstream_compilations", []),
            "elemental_compilations": report.get("elemental_compilations", []),
            "reaction_damage_compilations": report.get("reaction_damage_compilations", []),
            "hit_parameter_compilations": report.get("hit_parameter_compilations", []),
            "action_admission_compilations": report.get("action_admission_compilations", []),
            "action_resource_compilations": report.get("action_resource_compilations", []),
            **({"action_capacity_compilations": report["action_capacity_compilations"]} if report.get("action_capacity_compilations") else {}),
            "action_event_compilations": report.get("action_event_compilations", []),
            "attack_metadata_compilations": report.get("attack_metadata_compilations", []),
            "periodic_attack_compilations": report.get("periodic_attack_compilations", []),
            "passive_compilations": report.get("passive_compilations", []),
            "energy_generation_compilations": report.get("energy_generation_compilations", []),
            "limited_hit_benefit_compilations": report.get("limited_hit_benefit_compilations", []),
            "mechanism_compilations": report.get("mechanism_compilations", []),
            "attribute_source_adoption": report.get("attribute_source_adoption"),
            **({"state_runtime_source_adoptions":report['state_runtime_source_adoptions']}
               if report.get('state_runtime_source_adoptions') else {}),
            **({'resource_counter_source_adoptions':report['resource_counter_source_adoptions']}
               if report.get('resource_counter_source_adoptions') else {}),
            **({'resource_counter_view_adoptions':report['resource_counter_view_adoptions']}
               if report.get('resource_counter_view_adoptions') else {}),
            **({'attribute_callback_source_adoption':report['attribute_callback_source_adoption']}
               if report.get('attribute_callback_source_adoption') else {}),
            **({key: report[key] for key in ('resource_threshold_observer_adoptions', 'resource_threshold_trigger_adoptions')
                if report.get(key)})})
    # A trusted resolution is meaningful only for the exact graph that the
    # Composer classified. This check prevents a cached ready receipt from
    # surviving later template/rotation/mechanics edits.
    if task_capability.get("resolved"):
        plan = report.get("plan") or {}
        expected = fingerprint({
            "template": plan.get("resolved_template"),
            "rotation_model": report.get(
                "compiled_rotation_model", plan.get("rotation_model")),
            "mechanics_model": report.get("mechanics_model"),
            "mechanics_matches": report.get("mechanics_matches"),
        })
        actual = (task_capability.get("derivation") or {}).get(
            "effective_graph_fingerprint")
        if actual != expected:
            raise PlanError(
                "任务能力回执对应的有效图已经过期，必须重新组合",
                "task_capability_receipt_stale",
                {"expected": expected, "actual": actual},
            )


@verification_scope
def run_plan_calc(
    plan, db_path, *, capture_display=False, candidate_session=None,
    _readonly_provisional_context=None,
    _session=None,
):
    """Calculate against one fully materialized source snapshot.

    Database updates may publish after the read phase. The detached calculation
    and optional export names retain the captured source, never the latest DB.
    """
    from genshin_db import queries
    from genshin_db.equipment_queries import equipment_read_session
    from genshin_runtime.operation_timing import timed_call

    from .damage_report_window_receipt import require_window_plan_support

    require_window_plan_support(plan)

    path = Path(db_path)
    if _readonly_provisional_context is not None:
        from .provisional_readonly import ReadOnlyProvisionalProjection
        if (candidate_session is not None or capture_display
                or type(_readonly_provisional_context) is not ReadOnlyProvisionalProjection):
            raise PlanError("只读临时事实试算不能与正式会话／交付混用", "energy_trial_authorization_scope_changed")
        _readonly_provisional_context.validate_input(plan, path)
    # Manual plans also work without a DB; preserve the no-create read contract.
    context = equipment_read_session(path) if path.exists() else database_lock(path)
    display_context = None
    resolved_provisional_uses = []
    with context:
        report = timed_call("compose", compose_equipment_plan,
            plan, path, candidate_session=candidate_session,
            _formal_provisional=candidate_session is not None or _readonly_provisional_context is not None,
            _resolved_provisional_uses=resolved_provisional_uses,
            _readonly_provisional_context=_readonly_provisional_context,
        )
        require_ready(report)
        plan = report["plan"]
        from genshin_app.task_evidence_scope import database_snapshot, evidence_scope
        task_evidence_scope = evidence_scope(database_snapshot(path), database_path=path)
        from .provisional_entities import (
            FORMAL_RESULT_FIDELITY,
            PROVISIONAL_RESULT_FIDELITY,
        )
        result_fidelity = (
            PROVISIONAL_RESULT_FIDELITY
            if resolved_provisional_uses else FORMAL_RESULT_FIDELITY
        )
        # Derive fidelity from actual trusted consumer resolution.  Registry
        # presence and retained whiteboard provenance are not executable use.
        report["result_fidelity"] = result_fidelity
        report["plan"]["resolution"]["result_fidelity"] = result_fidelity
        if capture_display:
            line = plan["manual_template"].get("meta", {}).get("version_line", "release")
            warnings = []
            try:
                catalog = queries.get_display_catalog(path, line, entity_types=["character"])
            except (OSError, sqlite3.Error, ValueError):
                catalog = {}
                warnings.append("中文名称目录不可用，使用模板内名称；未命名对象显示待补提示")
            display_context = {"catalog": catalog, "warnings": warnings, "receipt": {
                "kind": "calculation_snapshot", "version_line": line,
                "catalog_fingerprint": fingerprint(catalog),
                "status": "template_fallback" if warnings else "ready"}}

    materialization_receipt = (report.get("action_automation_audit") or {}).get(
        "materialization_receipt")
    if materialization_receipt is not None:
        report["action_materialization_receipt"] = copy.deepcopy(materialization_receipt)

    override_receipt = report.get("calculation_override_receipt")
    if override_receipt is not None:
        from .calculation_overrides import validate_execution_bundle_receipt
        try:
            validate_execution_bundle_receipt(override_receipt, {
                "template": report["plan"]["resolved_template"],
                "rotation_model": report.get(
                    "compiled_rotation_model", report["plan"].get("rotation_model")
                ),
                "mechanics_model": report.get("mechanics_model"),
                "mechanics_matches": report.get("mechanics_matches"),
                "computed_value_overrides": report.get(
                    "computed_value_overrides", []
                ),
            })
        except (TypeError, ValueError) as exc:
            raise PlanError(
                str(exc), "calculation_override_pending",
                {"calculation_override_receipt": override_receipt},
            ) from exc

    if plan["plan_version"] == 3:
        from genshin_app.rotation_check import (
            describe_rotation_check,
            resolve_rotation_model,
        )
        from genshin_engine.mechanics import execute_mechanics
        report["mechanics_input_template"] = copy.deepcopy(report["plan"]["resolved_template"])
        model, bindings = resolve_rotation_model(report["plan"]["resolved_template"],
                                                 report.get("compiled_rotation_model", plan["rotation_model"]))
        from .energy_policy import require_current_energy_rotation, uses_direct_energy
        require_current_energy_rotation(model,
            path="execution.rotation_model",
            allow_none=not uses_direct_energy(model, report["mechanics_model"]))
        execution = {"template": report["plan"]["resolved_template"], "rotation_model": model,
                     "mechanics_model": report["mechanics_model"], "matches": report["mechanics_matches"]}
        executed = (timed_call("execute_and_retain", _session.execute_fresh, execution)
                    if model and "damage_report" in model else timed_call("engine_execute", execute_mechanics, **execution))
        if executed.get("result") is None or executed.get("resolved_template") is None:
            raise PlanError("机制输入尚不可执行", "mechanics_pending", executed)
        from .mechanism_contact_receipt import verify_contact_execution_scope
        try:
            verify_contact_execution_scope(executed, report.get("mechanism_compilations", []))
        except ValueError as exc:
            raise PlanError(str(exc), "mechanism_pending", {"issues": [str(exc)]}) from exc
        from .reaction_em_execution import verify_reaction_em_execution
        reaction_em_execution = verify_reaction_em_execution(executed, report.get('upstream_compilations', []))
        result = executed["result"]
        if reaction_em_execution is not None:
            from genshin_engine.runtime_reaction_em_compiler import (
                PROFILE_ID as EM_PROFILE_ID,
            )
            result['reaction_em_execution'] = reaction_em_execution
            result['reaction_em_compilations'] = copy.deepcopy([
                row for row in report['upstream_compilations'] if row.get('profile_id') == EM_PROFILE_ID])
        if result.get('state_runtime'):
            result['state_runtime']['source_adoptions'] = copy.deepcopy(report.get('state_runtime_source_adoptions', []))
        if result.get('resource_counters'):
            result['resource_counters']['source_adoptions'] = copy.deepcopy(report.get('resource_counter_source_adoptions', []))
        if result.get('resource_counter_views'):
            result['resource_counter_views']['source_adoptions'] = copy.deepcopy(report.get('resource_counter_view_adoptions', []))
        if result.get('attribute_ledger'):
            result['attribute_ledger']['source_adoption'] = copy.deepcopy(report.get('attribute_source_adoption'))
        if report.get('resource_reaction_source_adoption') is not None:
            result['resource_reaction_source_adoption'] = copy.deepcopy(report['resource_reaction_source_adoption'])
        if result.get('attribute_callbacks'):
            result['attribute_callbacks']['source_adoption'] = copy.deepcopy(report.get('attribute_callback_source_adoption'))
        for suffix in ('observer', 'trigger'):
            if result.get(f'resource_threshold_{suffix}s'):
                result[f'resource_threshold_{suffix}s']['source_adoptions'] = copy.deepcopy(report.get(f'resource_threshold_{suffix}_adoptions', []))
        result["rotation_check"] = describe_rotation_check(
            executed["rotation_check"], report["mechanics_input_template"], model, bindings,
            mechanics_model=report["mechanics_model"], matches=report["mechanics_matches"])
        complete = result.get("mechanics", {}).get("complete_first_cycle", False)
        result["rotation_check"]["damage_interpretation"].update(
            kind="planned_damage" if complete else "executed_prefix",
            complete_first_cycle=complete, reported_hit_count=len(result["hits"]),
            message=("伤害来自完整输入窗口内的已执行命中；多轮需显式输入多轮动作，未模拟元素底、体力和实际命中。"
                     if complete else "仅统计输入窗口中断前已执行的命中，不是完整轴总伤；后续事件未验证，不能据此计算循环 DPS。"))
        reaction_verification = _declared_reaction_verification(
            report["mechanics_input_template"], report["mechanics_model"])
        if reaction_verification is not None:
            interpretation = result["rotation_check"]["damage_interpretation"]
            interpretation["reaction_verification"] = reaction_verification
            interpretation["message"] += (
                f" 其中 {reaction_verification['declared_reaction_hit_count']} 个反应命中来自预填声明；"
                "元素底、附着资格、ICD 计数身份和反应顺序尚未由引擎复核，总伤不能作为原生反应回放结果。"
            )
        if complete and result.get("elemental"):
            interpretation = result["rotation_check"]["damage_interpretation"]
            runtime_reaction, runtime_application = _elemental_runtime_verifications(
                result["elemental"])
            if runtime_reaction is not None:
                interpretation["reaction_verification"] = runtime_reaction
            if runtime_application is not None:
                interpretation["application_verification"] = runtime_application
            interpretation["message"] = "伤害来自完整输入窗口内的已执行命中；已按声明档案模拟普通单目标附着与受支持反应，未模拟体力、真实碰撞或未声明机制。"
        scoped_window = result.get('damage_report_window') or {}
        scoped_finite_complete = (scoped_window.get('contract') == 'damage-report-window-v1'
            and scoped_window.get('status') == 'complete'
            and scoped_window.get('required_validation', {}).get('status') == 'complete')
        if not complete and not scoped_finite_complete:
            result["summary"]["dps"] = None
        result["mechanics_trace"] = executed["mechanics_trace"]
        result["sampling_trace"] = executed.get("sampling_trace", [])
        result["mechanics_status"] = executed["status"]
        result["mechanics_issues"] = executed.get("issues", [])
        result["coverage_interpretation"] = {
            "kind": "hit_projection", "actual_uptime": None,
            "state_timeline": "buff_state_timeline",
            "message": "逐击数值投影不代表持续覆盖；有限时增益见持续 Buff 状态，具体命中是否受益仍以逐击计算为准。"}
        report["plan"]["resolved_template"] = executed["resolved_template"]
        report["output_fingerprint"] = fingerprint(executed["resolved_template"])
        report["plan"]["resolution"]["output_fingerprint"] = report["output_fingerprint"]
    else:
        from .energy_policy import require_current_energy_rotation
        require_current_energy_rotation(report.get("compiled_rotation_model", plan.get("rotation_model")),
            path="execution.rotation_model")
        result = calculate(report["plan"]["resolved_template"])
        result = attach_rotation_check(result, report["plan"]["resolved_template"],
                                       report.get("compiled_rotation_model", report["plan"].get("rotation_model")), native=True)
    # Producer identity is part of the executed result before any downstream
    # proof fingerprints the whole calculation. Never append it after sealing.
    from .attribute_budget_binding import attach_nonconsumer_budget
    attach_nonconsumer_budget(result, report.get('mechanics_model'))
    from .energy_policy import attach_energy_policy
    attach_energy_policy(result, model if plan['plan_version'] == 3 else result['rotation_check'].get('resolved_rotation_model'), report.get('mechanics_model'))
    if report.get("action_materialization_receipt"):
        result["action_materialization_receipt"] = copy.deepcopy(
            report["action_materialization_receipt"])
        result["action_materialization_source"] = copy.deepcopy(
            report["action_materialization_source"])
    if report.get("upstream_semantics"):
        result["upstream_semantics"] = copy.deepcopy(report["upstream_semantics"])
    reaction_benefits = reaction_benefit_records(report)
    if reaction_benefits:
        result["reaction_benefit_compilations"] = copy.deepcopy(reaction_benefits)
    if report.get("elemental_compilations"):
        result["elemental"]["source_compilations"] = copy.deepcopy(report["elemental_compilations"])
    if report.get("hit_parameter_compilations"):
        result["hit_parameter_sources"] = copy.deepcopy(report["hit_parameter_compilations"])
    if report.get("action_admission_compilations"):
        result["action_admission_compilations"] = copy.deepcopy(report["action_admission_compilations"])
    if report.get("action_resource_compilations"):
        result["action_resource_compilations"] = copy.deepcopy(report["action_resource_compilations"])
    if report.get("action_event_compilations") or report.get("action_materialization_receipt"):
        result["action_event_compilations"] = copy.deepcopy(
            report.get("action_event_compilations", []))
    if report.get("action_event_compilations"):
        report["action_event_execution_rotation_model"] = copy.deepcopy(model)
    if report.get("attack_metadata_compilations"):
        result["attack_metadata_compilations"] = copy.deepcopy(report["attack_metadata_compilations"])
    if report.get("periodic_attack_compilations"):
        result["periodic_attack_compilations"] = copy.deepcopy(report["periodic_attack_compilations"])
    if report.get("passive_compilations"):
        result["passive_compilations"] = copy.deepcopy(report["passive_compilations"])
    if report.get("energy_generation_compilations"):
        result["energy_generation_compilations"] = copy.deepcopy(report["energy_generation_compilations"])
        result["energy_generation_summary"] = energy_generation_summary(result)
        report["energy_generation_summary"] = copy.deepcopy(result["energy_generation_summary"])
        report["energy_generation_resource_ledger_fingerprint"] = energy_generation_resource_ledger_fingerprint(result)
    if report.get("limited_hit_benefit_compilations"):
        result["limited_hit_benefit_compilations"] = copy.deepcopy(report["limited_hit_benefit_compilations"])
        result["limited_hit_benefit_summary"] = limited_hit_benefit_summary(result)
        report["limited_hit_benefit_summary"] = copy.deepcopy(result["limited_hit_benefit_summary"])
        report["limited_hit_benefit_ledger_fingerprint"] = limited_hit_benefit_ledger_fingerprint(result)
    if report.get("mechanism_compilations"):
        result["mechanism_compilations"] = copy.deepcopy(report["mechanism_compilations"])
        result["mechanism_replay_context"] = copy.deepcopy(report["mechanism_replay_context"])
        result["mechanism_execution"] = {"model": copy.deepcopy(report["mechanics_model"]),
            "template": copy.deepcopy(report["mechanics_input_template"])}
        from .mechanism_source_schema import CONTACT_COMPILER
        if any(r['compiler'] == CONTACT_COMPILER for r in report['mechanism_compilations']):
            result['mechanism_execution'].update(rotation_model=copy.deepcopy(model),
                matches=copy.deepcopy(report['mechanics_matches']))
        result["mechanism_summary"] = mechanism_summary(result)
    if report.get("reaction_damage_compilations"):
        result["elemental"]["reaction_damage_source_compilations"] = copy.deepcopy(report["reaction_damage_compilations"])
    from .attribute_receipt import attach_attribute_execution_binding
    attach_attribute_execution_binding(report, result)
    from .static_inclusion_receipt import attach_static_inclusion_result
    attach_static_inclusion_result(report, result)
    computed_value_proof = None
    if report.get("computed_value_overrides"):
        from .calculation_overrides import apply_computed_value_overrides

        try:
            computed_value_proof = apply_computed_value_overrides(
                result,
                report["computed_value_overrides"],
                override_receipt,
                loop_duration=report["plan"]["resolved_template"].get(
                    "loop_duration"
                ),
            )
        except ValueError as exc:
            raise PlanError(
                str(exc), "calculation_override_pending",
                {"calculation_override_receipt": override_receipt},
            ) from exc
    if override_receipt is not None:
        result["calculation_override_receipt"] = copy.deepcopy(override_receipt)
    from genshin_app.default_assistance import safe_default_assistance
    report["default_assistance"] = safe_default_assistance(plan, db_path, result=result, report=report)
    # Factual read-only scope is sealed with the result and survives direct
    # exports. It neither adds missing inputs nor supplies execution authority.
    result["validation_scope"] = report["default_assistance"]["validation_scope"]
    from .calculation_sealing import seal_fresh_calculation
    calculated = seal_fresh_calculation(
        plan, result, report,
        model=model if plan["plan_version"] == 3 else None,
        complete=complete if plan["plan_version"] == 3 else False,
        reaction_benefits=reaction_benefits,
        override_receipt=override_receipt, computed_value_proof=computed_value_proof,
        display_context=display_context, resolved_provisional_uses=resolved_provisional_uses,
        candidate_session=candidate_session, calculated_at=datetime.now(UTC).isoformat(), _session=_session,
        _readonly_provisional_context=_readonly_provisional_context,
        task_evidence_scope=task_evidence_scope,
    )
    if _readonly_provisional_context is not None:
        _readonly_provisional_context.bind_fresh_result(calculated)
    return calculated


@verification_scope
def result_bytes(
    result, receipt, fmt, display=None, equipment_summary=None, *,
    candidate_session=None, _session=None,
):
    formal_audit = _validate_result_export(result, receipt, candidate_session=candidate_session, _session=_session)
    return _encode_validated_result(result, receipt, fmt, display, equipment_summary, formal_audit=formal_audit)


def _validate_result_export(result, receipt, *, candidate_session=None, _session=None):
    from .calculation_overrides.ownership_receipt import (
        validate_ownership_result_proof,
    )
    from .energy_delivery_receipt import validate_automatic_energy_result_receipt
    from .execution_verification import ExecutionVerificationError
    from .provisional_entities import (
        ProvisionalEntityError,
        validate_current_session_usage_receipt,
    )
    try:
        validate_ownership_result_proof(result, receipt)
        formal_audit = formal_calculation_audit_payload(result, receipt, _session=_session)
    except (PlanError, ExecutionCancelled):
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanError(
            "正式计算审计与本次结果不一致，请重新计算后导出。",
            "source_changed",
        ) from exc
    try:
        validate_current_session_usage_receipt(
            result, receipt, candidate_session,
        )
    except ProvisionalEntityError as exc:
        raise PlanError(str(exc), exc.reason, exc.details) from exc
    try:
        from .calculation_overrides import (
            validate_computed_value_result_receipt,
            validate_execution_result_receipt,
        )
        validate_execution_result_receipt(result, receipt)
        validate_computed_value_result_receipt(result, receipt, _session=_session)
        from .attribute_receipt import validate_attribute_result_receipt
        validate_attribute_result_receipt(result, receipt)
        from .counter_frame_receipt import validate_counter_frame_result_receipt
        validate_counter_frame_result_receipt(result, receipt, _session=_session)
        from .resource_reaction_receipt import validate_resource_reaction_result_receipt
        validate_resource_reaction_result_receipt(result, receipt, _session=_session)
        from .static_inclusion_receipt import validate_static_inclusion_result_receipt
        validate_static_inclusion_result_receipt(result, receipt)
        validate_automatic_energy_result_receipt(result, receipt, _session=_session)
        from .action_capacity_receipt import validate_action_capacity_result_receipt
        validate_action_capacity_result_receipt(result, receipt, _session=_session)
        from .action_admission_receipt import validate_action_admission_result_receipt
        validate_action_admission_result_receipt(result, receipt, _session=_session)
        validate_energy_generation_result_receipt(result, receipt)
        validate_limited_hit_benefit_result_receipt(result, receipt)
        validate_mechanism_result_receipt(result, receipt, _session=_session)
        validate_empty_damage_result_receipt(result, receipt)
        from .damage_report_window_receipt import validate_window_result
        validate_window_result(result, receipt, _session=_session)
        from .special_damage_echo_receipt import validate_echo_result
        validate_echo_result(result, receipt, _session=_session)
        validate_snapshot_capture_result_receipt(result, receipt, _session=_session)
        validate_external_damage_result_receipt(result, receipt)
        from .agent_execution_receipt import (
            validate_agent_execution_result_receipt,
        )
        validate_agent_execution_result_receipt(result, receipt)
        from .action_automation_audit import (
            validate_action_materialization_result_receipt,
        )
        validate_action_materialization_result_receipt(result, receipt)
    except ExecutionVerificationError as exc:
        if exc.code == 'energy_policy_replay_incompatible':
            raise PlanError(str(exc), exc.code, getattr(exc, 'details', None)) from exc
        from .resource_reaction_receipt import has_v2_material
        if has_v2_material(result, receipt):
            raise PlanError(str(exc), 'resource_reaction_proof_pending',
                            {'status': 'proof_pending', 'fresh': False, 'actual_result': result,
                             'boundary_code': exc.code}) from exc
        raise PlanError(str(exc), "source_changed") from exc
    except (PlanError, ExecutionCancelled):
        raise
    except (TypeError, ValueError) as exc:
        raise PlanError(str(exc), getattr(exc, 'reason', 'source_changed'), getattr(exc, 'details', None)) from exc
    if (result.get("periodic_attack_compilations") or receipt.get("periodic_attacks")
            or any(r.get("kind") in ("calibrated_multi_contact_v1", "calibrated_multi_application_v2")
                   for r in result.get("action_event_compilations", []))):
        from .periodic_receipt import validate_periodic_result_receipt
        try:
            validate_periodic_result_receipt(result, receipt)
        except (PlanError, ExecutionCancelled):
            raise
        except ValueError as exc:
            raise PlanError(str(exc), "source_changed") from exc
    if result.get("passive_compilations") or receipt.get("passives"):
        from .passive_receipt import validate_passive_result_receipt
        try:
            validate_passive_result_receipt(result, receipt)
        except (PlanError, ExecutionCancelled):
            raise
        except ValueError as exc:
            raise PlanError(str(exc), "source_changed") from exc
    benefits = result.get("reaction_benefit_compilations")
    try:
        from .reaction_em_receipt import validate_reaction_em_result
        validate_reaction_em_result(result, receipt)
        validate_reaction_benefit_shared_receipt(benefits, receipt)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise PlanError(str(exc), "source_changed") from exc
    if ((benefits or receipt.get("reaction_benefits"))
            and (not benefits or (receipt.get("reaction_benefits") or {}).get("compilations_fingerprint") != fingerprint(benefits)
                 or receipt.get("result_fingerprint") != fingerprint(result))):
        raise PlanError("反应收益来源与结果回执不一致，请重新计算后导出", "source_changed")
    metadata = result.get("attack_metadata_compilations")
    if ((metadata or receipt.get("attack_metadata"))
            and (not metadata or (receipt.get("attack_metadata") or {}).get("compilations_fingerprint") != fingerprint(metadata)
                 or receipt.get("result_fingerprint") != fingerprint(result))):
        raise PlanError("攻击元信息来源与结果回执不一致，请重新计算后导出", "source_changed")
    return formal_audit


def _encode_validated_result(result, receipt, fmt, display=None, equipment_summary=None, *, formal_audit):
    """Encode a result retained after this process validated its export proof."""
    provisional_execution_receipt = receipt.get("provisional_entity_usage_receipt")
    if fmt == "json":
        from .calculation_wire import result_only_bytes
        return result_only_bytes(result, receipt, equipment_summary)
    rows = hit_rows(result, display)
    if fmt == "csv":
        if result.get("external_damage"):
            raise PlanError("外部补充伤害需要保留引擎与外部两部分明细，请导出JSON或Excel。",
                            "unsupported_external_damage_export")
        if result.get("damage_reporting"):
            raise PlanError("明确全未命中的零结果需要保留执行证明，请导出JSON或Excel；CSV暂不支持。",
                            "unsupported_zero_damage_export")
        keys = ["plan_id", "revision", "input_fingerprint", "output_fingerprint", "source_fingerprint",
                "template_version", "engine_version", "calculated_at", "result_fingerprint"]
        rows[0] += [*keys, "data_versions_json", "assumptions_json", "hit_id", "matched_effect_refs_json"]
        if receipt.get("upstream_semantics"):
            rows[0] += ["upstream_semantics_json", "upstream_sources_json"]
        if receipt.get("display_source"):
            rows[0].append("display_source_json")
        if receipt.get("elemental_reference"):
            rows[0].append("elemental_reference_json")
        if provisional_execution_receipt is not None:
            rows[0] += [
                "result_fidelity", "result_notice",
                "provisional_entity_usage_json",
            ]
        if formal_audit is not None:
            rows[0].append("formal_calculation_audit_json")
        if result.get("passive_compilations"):
            rows[0].append("passive_sources_json")
        if result.get("periodic_attack_compilations"):
            rows[0].append("periodic_attack_sources_json")
        if result.get("energy_generation_compilations"):
            rows[0].append("energy_generation_json")
            energy_csv = json.dumps({"proof": receipt["energy_generation"],
                "summary": result["energy_generation_summary"]}, ensure_ascii=False, separators=(",", ":"))
        if result.get("limited_hit_benefit_compilations"):
            rows[0].append("limited_hit_benefit_json")
            benefit_csv = json.dumps({"proof": receipt["limited_hit_benefit"],
                "summary": result["limited_hit_benefit_summary"]}, ensure_ascii=False, separators=(",", ":"))
        if result.get("mechanism_compilations"):
            rows[0].append("mechanism_sources_json")
            mechanism_csv = json.dumps({"proof": receipt["mechanisms"], "records": result["mechanism_compilations"],
                "replay_context": result["mechanism_replay_context"], "execution": result["mechanism_execution"],
                "summary": result["mechanism_summary"]}, ensure_ascii=False, separators=(",", ":"))
        if result.get('snapshot_captures'):
            rows[0].append('snapshot_captures_json')
            capture_csv = json.dumps({'proof': receipt['snapshot_captures'],
                'execution': result['snapshot_capture_execution'], 'captures': result['snapshot_captures']},
                ensure_ascii=False, separators=(',', ':'))
        automatic_proof = (receipt.get("action_resources") or {}).get("automatic_energy_delivery")
        if automatic_proof is not None:
            rows[0].append("automatic_energy_delivery_json")
            automatic_csv = json.dumps({"proof": automatic_proof,
                "summary": result["rotation_check"]["automatic_energy_delivery"]}, ensure_ascii=False, separators=(",", ":"))
        for hit_index, (row, hit) in enumerate(
            zip(rows[1:1 + len(result["hits"])], result["hits"], strict=True)
        ):
            row += [*[receipt[k] for k in keys], json.dumps(receipt["data_versions"], ensure_ascii=False),
                    json.dumps(receipt["assumptions"], ensure_ascii=False), hit["id"],
                    json.dumps(hit["matched_effect_refs"], ensure_ascii=False)]
            if receipt.get("upstream_semantics"):
                row += [json.dumps(receipt[k], ensure_ascii=False) for k in ("upstream_semantics", "upstream_sources")]
            if receipt.get("display_source"):
                row.append(json.dumps(receipt["display_source"], ensure_ascii=False))
            if receipt.get("elemental_reference"):
                row.append(json.dumps(receipt["elemental_reference"], ensure_ascii=False))
            if provisional_execution_receipt is not None:
                row += [
                    PROVISIONAL_RESULT_FIDELITY, PROVISIONAL_RESULT_LABEL,
                    json.dumps(
                        provisional_execution_receipt, ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            if formal_audit is not None:
                row.append(
                    json.dumps(
                        formal_audit, ensure_ascii=False, separators=(",", ":"),
                    ) if hit_index == 0 else ""
                )
            if result.get("passive_compilations"):
                row.append(json.dumps(result["passive_compilations"], ensure_ascii=False))
            if result.get("periodic_attack_compilations"):
                row.append(json.dumps(result["periodic_attack_compilations"], ensure_ascii=False))
            if result.get("energy_generation_compilations"):
                row.append(energy_csv)
            if result.get("limited_hit_benefit_compilations"):
                row.append(benefit_csv)
            if result.get("mechanism_compilations"):
                row.append(mechanism_csv)
            if result.get('snapshot_captures'):
                row.append(capture_csv)
            if automatic_proof is not None:
                row.append(automatic_csv)
        out = io.StringIO(newline="")
        csv.writer(out).writerows(rows)
        return out.getvalue().encode("utf-8-sig")
    if fmt == "xlsx":
        rows[0] += ["hit_id", "matched_effect_refs_json"]
        for row, hit in zip(rows[1:1 + len(result["hits"])], result["hits"], strict=True):
            row += [hit["id"], json.dumps(hit["matched_effect_refs"], ensure_ascii=False)]
        source_rows = [["字段", "内容"], *[[key, json.dumps(value, ensure_ascii=False)
                       if isinstance(value, (dict, list)) else value] for key, value in receipt.items()]]
        sheets = [("汇总", summary_rows(result, display)), ("逐事件明细", rows),
                  ("增益覆盖率", coverage_rows(result, display)), ("来源与假设", source_rows)]
        if equipment_summary:
            from genshin_app.export import readable_equipment_rows
            equipment_view, equipment_sources = readable_equipment_rows(equipment_rows(equipment_summary))
            sheets.append(("角色与装备", equipment_view))
        if result.get("rotation_check"):
            sheets.extend(check_sheets(result["rotation_check"], display))
        if result.get("elemental", {}).get("ruleset") == FREEZE_RULESET:
            from genshin_app.export import elemental_state_rows
            sheets.append(("元素状态流水", elemental_state_rows(result)))
        from genshin_app.action_capacity_reporting import capacity_sheets
        sheets.extend(capacity_sheets(result, display))
        from genshin_app.action_admission_reporting import action_admission_sheets
        sheets.extend(action_admission_sheets(result, display))
        if result.get("action_resource_compilations"):
            from genshin_app.export import action_resource_rows
            sheets.append(("动作资源来源", action_resource_rows(result)))
        if result.get("action_event_compilations"):
            from genshin_app.export import action_event_rows
            sheets.append(("动作派发来源", action_event_rows(result)))
        if result.get("attack_metadata_compilations"):
            from genshin_app.export import attack_metadata_rows
            sheets.append(("攻击元信息来源", attack_metadata_rows(result)))
        if result.get("reaction_benefit_compilations"):
            from genshin_app.export import reaction_benefit_rows
            sheets.append(("反应收益来源", reaction_benefit_rows(result)))
        if result.get('reaction_em_compilations'):
            from genshin_app.reaction_em_reporting import reaction_em_rows
            sheets.append(('反应触发精通', reaction_em_rows(result)))
        from genshin_app.static_inclusion_reporting import static_inclusion_rows
        static_rows = static_inclusion_rows(result)
        if static_rows:
            sheets.append(('面板已含来源', static_rows))
        if result.get("passive_compilations"):
            from genshin_app.export import passive_source_rows
            sheets.append(("固有天赋来源", passive_source_rows(result)))
        if result.get("periodic_attack_compilations"):
            from genshin_app.export import periodic_attack_rows
            sheets.append(("周期攻击来源", periodic_attack_rows(result)))
        if result.get("energy_generation_compilations"):
            from genshin_app.export import energy_generation_rows
            sheets.append(("产球来源", energy_generation_rows(result)))
        if result.get("limited_hit_benefit_compilations"):
            from genshin_app.export import limited_hit_benefit_rows
            sheets.append(("有限次数收益", limited_hit_benefit_rows(result)))
        if result.get("mechanism_compilations"):
            from genshin_app.export import mechanism_rows
            sheets.append(("通用机制来源", mechanism_rows(result)))
        if result.get('snapshot_captures'):
            from genshin_app.export import snapshot_capture_rows
            sheets.append(('面板取样', snapshot_capture_rows(result)))
        if result.get('shared_pools'):
            from genshin_app.export import shared_pool_rows
            sheets.append(('共享资源流水', shared_pool_rows(result)))
        if result.get('attribute_ledger'):
            from genshin_app.export import attribute_ledger_rows
            sheets.append(('属性取样账', attribute_ledger_rows(result)))
        if result.get('state_runtime'):
            from genshin_app.export import state_runtime_rows
            sheets.append(('生命周期流水', state_runtime_rows(result)))
        if result.get('resource_counters'):
            from genshin_app.export import resource_counter_rows
            sheets.append(('资源累计流水', resource_counter_rows(result)))
        if result.get('resource_counter_views'):
            from genshin_app.export import resource_counter_view_rows
            sheets.append(('资源读数流水', resource_counter_view_rows(result)))
        from genshin_app.reaction_resource_reporting import reaction_resource_sheet
        reaction_rows = reaction_resource_sheet(result)
        if reaction_rows:
            sheets.append(('反应资源联动', reaction_rows))
        from genshin_app.counter_frame_reporting import counter_frame_rows
        frames = counter_frame_rows(result.get('resource_counter_views'))
        if frames:
            sheets.append(('累计输出快照', frames))
        if result.get('attribute_callbacks'):
            from genshin_app.export import attribute_callback_rows
            sheets.append(('属性回调执行', attribute_callback_rows(result)))
        from genshin_app.export import resource_threshold_rows
        from genshin_app.resource_threshold_reporting import FIELDS, TITLES
        for field, title in zip(FIELDS, TITLES):
            if result.get(field):
                sheets.append((title, resource_threshold_rows(result, field)))
        if result.get('hit_parameter_sources'):
            from genshin_app.export import hit_parameter_rows
            sheets.append(('技能参数来源', hit_parameter_rows(result)))
        if result.get("damage_reporting"):
            from genshin_app.export import damage_reporting_rows
            sheets.append(("伤害报告证明", damage_reporting_rows(result)))
        if result.get("external_damage"):
            from genshin_app.export import external_damage_rows
            sheets.append(("外部补充伤害", external_damage_rows(result, display)))
        if result.get("fixed_effect_sources"):
            from genshin_app.export import target_condition_rows
            sheets.append(("目标条件收益", target_condition_rows(result)))
        if equipment_summary and len(equipment_sources) > 1:
            sheets.append(("装备资料", equipment_sources))
        provisional_rows = provisional_entity_rows(result, receipt)
        if provisional_rows:
            sheets.append(("临时公共资料", provisional_rows))
        audit_rows = formal_audit_payload_rows(formal_audit)
        if audit_rows:
            sheets.append(("计算审计", audit_rows))
        buffer = io.BytesIO()
        _write_xlsx(buffer, sheets)
        return buffer.getvalue()
    raise PlanError("导出格式仅支持 json/csv/xlsx")


@verification_scope
def export_plan_result(
    plan, db_path, fmt, path, *, overwrite=False, expected_file_sha=None,
    candidate_session=None, _session=None,
):
    from genshin_app.output_policy import validate_database_outputs
    validate_database_outputs([path], db_path)
    calculated = run_plan_calc(
        plan, db_path, capture_display=fmt in ("csv", "xlsx"),
        candidate_session=candidate_session, _session=_session,
    )
    from genshin_app.calculation_completion import require_calculation_complete
    require_calculation_complete(calculated)
    names, display_warnings = None, []
    if fmt in ("csv", "xlsx"):
        context = calculated["display_context"]
        display_warnings = context["warnings"]
        names = display_names(calculated["plan"], context["catalog"])
    content = result_bytes(
        calculated["result"], calculated["result_receipt"], fmt, names,
        calculated["equipment_summary"], candidate_session=candidate_session, _session=_session,
    )
    publish_files([(path, content)], overwrite=overwrite,
                  expected_shas={path: expected_file_sha} if expected_file_sha is not None else None,
                  input_paths=[db_path])
    return {"path": str(Path(path).resolve()), "result_receipt": calculated["result_receipt"],
            "display_warnings": display_warnings,
            "plan": calculated["plan"]}


def require_standalone_resolved(calculated):
    if calculated.get("plan", {}).get("external_damage_supplements") or calculated.get("result", {}).get("external_damage"):
        raise PlanError("独立伤害模板不能保留外部补充与含补充合计，请保存原生方案或完整计算包。",
                        "unsupported_external_damage_export")
    if calculated["result"].get("damage_reporting"):
        raise PlanError("零结果投影需要实际执行证明，不能导出为空的独立伤害模板；请保存原生方案或完整计算包。",
                        "unsupported_zero_damage_export")


def export_resolved_plan(plan, db_path, path, *, overwrite=False, expected_file_sha=None):
    from genshin_app.output_policy import validate_database_outputs
    validate_database_outputs([path], db_path)
    validate_plan(plan)
    require_standalone_resolved({"plan": plan, "result": {}})
    if plan["plan_version"] == 3:
        report = run_plan_calc(plan, db_path)
        from genshin_app.calculation_completion import require_calculation_complete
        require_calculation_complete(report)
        require_standalone_resolved(report)
    else:
        report = compose_equipment_plan(plan, db_path)
        require_ready(report)
    content = json.dumps(report["plan"]["resolved_template"], ensure_ascii=False,
                         indent=2, allow_nan=False).encode("utf-8")
    published = publish_files([(path, content)], overwrite=overwrite,
                    expected_shas={path: expected_file_sha} if expected_file_sha is not None else None,
                    input_paths=[db_path])
    return {"path": str(Path(path).resolve()), "plan": report["plan"], **published}


def detach_character(plan, character_id, db_path):
    """Explicitly freeze one managed character, retaining other characters' gear inputs.

    This operation requires a current ready plan. Cross-character conversions from the
    removed basis become documented numeric constants. Existing manual-context and
    rotation confirmations are rechecked normally; the operation cannot waive them.
    """
    validate_plan(plan)
    if plan["plan_version"] == 3:
        raise PlanError("机制方案的规则仍依赖角色来源；如需固定数值，请导出本次执行快照并另建手工方案",
                        "unsupported_detach")
    with database_lock(Path(db_path)):
        current = run_plan_calc(plan, db_path)
        updated = copy.deepcopy(current["plan"])
        if character_id not in updated["equipment_inputs"]:
            raise PlanError("该角色未启用装备管理")
        old_equipment = updated["equipment_inputs"].pop(character_id)
        resolved_char = next(c for c in current["plan"]["resolved_template"]["characters"] if c["id"] == character_id)
        manual_char = next(c for c in updated["manual_template"]["characters"] if c["id"] == character_id)
        manual_char.update(stats=copy.deepcopy(resolved_char["stats"]), level=resolved_char["level"])
        frozen_source = {"kind": "frozen_equipment", "character_id": character_id,
                         "data_version": old_equipment["data_version"],
                         "input_fingerprint": current["result_receipt"]["input_fingerprint"],
                         "note": "用户明确解除装备管理，固化本次静态面板及以此角色为基数的已确认转化"}
        changed_owners = set()
        stages = current["report"]["contribution_report"]["characters"][character_id]
        for binding in updated["effect_bindings"]:
            if binding["provider_character_id"] == character_id and binding["owner"]["kind"] in ("weapon", "artifact_set"):
                original_owner = binding["owner"]
                binding["owner"] = {"kind": "manual", "note": f"解除 {character_id} 装备管理时固化的原装备效果"}
                binding["source_refs"].append({**frozen_source, "original_owner": original_owner})
                changed_owners.add(binding["binding_id"])
            for part in binding["parts"]:
                if part["state"] != "resolved":
                    continue
                rule, delivery = part["rule"], part["delivery"]
                if delivery["kind"] == "panel" and delivery["target_character_id"] == character_id:
                    part.update(state="excluded", rule=None, delivery=None,
                                reason=f"此项已包含在 {character_id} 的固化静态面板；后续来源变化不再驱动该项")
                elif rule["kind"] == "stat_scale" and rule["from"]["character_id"] == character_id:
                    value = rule["factor"] * stages[rule["from"]["stage"]][rule["from"]["stat"]] + rule.get("offset", 0)
                    value = max(rule.get("min", float("-inf")), min(rule.get("max", float("inf")), value))
                    part["rule"] = {"kind": "constant", "value": value}
                    part["dependencies"] = {"mode": "source_only", "context_fingerprint": None,
                        "note": f"用户解除 {character_id} 管理；原 {rule['from']['stage']}.{rule['from']['stat']} 转化固化于本次数值"}
                    binding["source_refs"].append(copy.deepcopy(frozen_source))
                    changed_owners.add(binding["binding_id"])
        updated["manual_template"].setdefault("meta", {}).setdefault("assumptions", []).append(
            f"{character_id} 已解除装备管理：静态面板与以该角色为基数的转化固定为本次值；其余角色仍可换装")
        report = compose_equipment_plan(updated, db_path)
        # The user's explicit freeze changes provenance identity, not the reviewed meaning.
        for requirement in report["effect_requirements"]:
            if requirement["binding_id"] in changed_owners:
                binding = next(b for b in updated["effect_bindings"] if b["binding_id"] == requirement["binding_id"])
                binding["source_fingerprint"] = requirement["source_fingerprint"]
                binding["coverage"]["source_fingerprint"] = requirement["source_fingerprint"]
                binding["coverage"]["note"] += "；用户显式解除管理，原已核对效果按当前数值固化"
        return compose_equipment_plan(updated, db_path)


def pending_example():
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "char1", "level": 90, "stats": {"atk": 100}}],
        hits=[{"id": "hit.example", "damage_type_ids": ["damage.other"], "t": 1,
               "character": "char1", "skill": "字段示例", "multiplier": 1,
               "damage_system": "classic", "element": "physical"}])
    from .schema import SLOTS
    plan["plan_id"] = "871f4105-127a-4b55-a869-930a39c69b15"
    plan["equipment_inputs"] = {"char1": {"character_ref": None, "level": 90,
        "basis": {"mode": "manual", "character_base_atk": 100, "character_base_hp": 10000,
            "character_base_def": 500, "crit_rate": 0.05, "crit_dmg": 0.5, "energy_recharge": 1,
            "em": 0, "dmg_bonus": {}, "ascension_contributions": [],
            "source_refs": [{"kind": "manual", "note": "字段与算术示例，不代表真实角色"}]},
        "weapon": {"weapon_id": 13501, "level": 90, "ascension": 6,
            "refinements": [{"source_index": 0, "affix_group_id": 113501, "refinement": 1}], "selected_by": "agent"},
        "artifacts": {slot: {"state": "empty", "reason": "示例明确未计入"} for slot in SLOTS},
        "manual_contributions": [], "data_version": None}}
    return plan


def panel_example():
    """Current authoring path: given stats and set identities, without individual rolls."""
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "char1", "level": 90, "stats": {
            "atk": 1800, "crit_rate": 0.6, "crit_dmg": 1.2, "energy_recharge": 2.0}}],
        hits=[{"id": "hit.example", "damage_type_ids": ["damage.elemental_burst"], "t": 1,
               "character": "char1", "skill": "字段示例", "multiplier": 1,
               "damage_system": "classic", "element": "physical"}])
    plan["plan_id"] = "f51c548b-01c2-4714-9c03-35331c9db618"
    plan["equipment_inputs"] = {"char1": {"mode": "panel", "panel_policy": "static_included",
        "character_ref": None, "weapon": None, "artifact_sets": [{"set_id": 15020, "count": 4}],
        "source_refs": [{"kind": "manual", "note": "字段示例：用户给定初始面板，已含两件套等常驻属性，不代表真实角色"}],
        "data_version": None}}
    return plan


def plan_capabilities():
    from genshin_app.agent_chat.native_event_topology_contracts import (
        event_topology_contracts,
    )
    from genshin_engine.automatic_energy import (
        capabilities as automatic_energy_capabilities,
    )
    from genshin_engine.runtime_compiler import compiler_caps
    from genshin_engine.runtime_passive_attribute_compiler import (
        passive_attribute_compiler_caps,
    )
    from genshin_engine.runtime_passive_benefit_compiler import (
        passive_benefit_compiler_caps,
    )
    from genshin_engine.runtime_reaction_benefit_compiler import (
        reaction_benefit_compiler_caps,
    )
    from genshin_engine.runtime_reaction_em_compiler import reaction_em_compiler_caps
    from genshin_engine.runtime_target_state_compiler import target_state_compiler_caps

    from .action_admission_catalog import action_admission_catalog_capabilities
    from .action_admissions import capabilities as action_admission_capabilities
    from .action_capacities import capabilities as action_capacity_capabilities
    from .action_capacity_catalog import action_capacity_catalog_capabilities
    from .action_dispatch_source_schema import action_dispatch_source_capabilities
    from .action_events import action_event_capabilities
    from .agent_execution_materialization import (
        agent_execution_materialization_capabilities,
    )
    from .attack_metadata import attack_metadata_capabilities
    from .attack_source_assembly import attack_source_assembly_capabilities
    from .attribute_recovery import recovery_capabilities
    from .attributes import attribute_source_contract
    from .calculation_overrides.schema import (
        capabilities as calculation_override_capabilities,
    )
    from .delivery import delivery_capabilities
    from .energy_generation_schema import energy_generation_contract
    from .finite_action_schema import finite_action_capabilities
    from .limited_hit_benefit_schema import limited_hit_benefit_contract
    from .mechanism_source_schema import mechanism_source_contract
    from .panel_reference import panel_reference_contract
    from .periodic_lifecycle import periodic_lifecycle_capabilities
    from .periodic_schema import periodic_attack_capabilities
    from .periodic_source_assembly import periodic_source_assembly_capabilities
    from .plan_description import (
        app_mechanics_capabilities,
        plan_description_schema,
        plan_semantic_constraints,
    )
    from .provisional_entities import capabilities as provisional_entity_capabilities
    from .rotation_authoring import app_rotation_capabilities
    from .static_inclusion_sources import static_inclusion_capabilities
    from .verification_budget import verification_budget_contract

    schema = plan_description_schema()
    return {"kind": "genshin-plan", "supported_plan_versions": [1, 2, 3], "composer_version": COMPOSER_VERSION,
            'execution_verification_budget': verification_budget_contract(),
            'event_topology_contracts': event_topology_contracts(),
            'panel_reference_contract': panel_reference_contract(),
            'calculation_completion_contract': {'contract': 'calculation-completion-v1',
                'source': 'actual execution result; cached completion markers have no authority',
                'axes': ['damage_report', 'required_validation', 'optional_audit', 'steady_state'],
                'cli_diagnostic': 'nonzero calculation_incomplete; JSON may retain typed diagnostic prefix'},
            "agent_execution_materialization_contract": agent_execution_materialization_capabilities(),
            "calculation_override_contract": calculation_override_capabilities(),
            "provisional_entity_contract": provisional_entity_capabilities(),
            "action_admission_source_contract": action_admission_capabilities(),
            "action_admission_catalog_contract": action_admission_catalog_capabilities(),
            "action_capacity_source_contract": action_capacity_capabilities(),
            "action_capacity_catalog_contract": action_capacity_catalog_capabilities(),
            "attack_source_assembly_contract": attack_source_assembly_capabilities(),
            "periodic_source_assembly_contract": periodic_source_assembly_capabilities(),
            "passive_source_contract": {**passive_benefit_compiler_caps(), "field": "passive_sources",
                "report_field": "passive_compilations", "progression_field": "equipment_inputs[actor].progression",
                "confirmable_issue_codes": ["passive_confirmation_required"],
                "max_sources": 32, "max_receipt_bytes": 8 * 1024 * 1024,
                "identity_coverage": "explicit_passive_sources_and_provider_anemo_parents"},
            "passive_attribute_source_contract": {**passive_attribute_compiler_caps(),
                "field":"passive_sources","report_field":"passive_compilations",
                "progression_field":"equipment_inputs[actor].progression",
                "confirmable_issue_codes":["passive_confirmation_required"]},
            "supported_template_versions": [1, 2], "rule_kinds": ["constant", "stat_scale", "explicit_hit", "mechanics", "fixed_effect_source"],
            "default_input_mode": "panel",
            "equipment_input_modes": {
                "panel": {"mode_field": "panel", "panel_policy": "static_included",
                    "stats_source": "manual_template.characters[].stats；等级同取该角色 level",
                    "artifact_selection": "artifact_sets=[{set_id,count}]；同套装仅一行，总件数不超过五件",
                    "weapon_selection": "weapon 可为 null；选择时只核对被动来源与精炼，不加算武器成长属性",
                    "source_refs": "记录用户或题面给定面板的依据；已含两件套等常驻属性",
                    "per_piece_artifacts": False,
                    "limits": ["不读取单件、主词条和副词条，不要求五槽",
                               "初始面板原样保留，不接受对该角色的 panel delivery",
                               "已包含常驻部件以 excluded + reason 覆盖核对；条件效果继续通过 buff/hit 交付",
                               "static 只读取给定属性；base 仅读取显式 base_atk/base_hp/base_def，缺失为 pending"]},
                "legacy_equipment": {"mode_field": "absent", "compatibility": "旧 basis + weapon + artifacts 完整配装行为保持",
                    "future_work": ["Agent 自动主词条分配", "按预算分配副词条"]}},
            "equipment_summary": {"summary_version": 1, "contexts": ["applied", "draft", "calculation_snapshot"],
                "compose_field": "equipment_summary", "calc_field": "equipment_summary",
                "authoritative_inputs": "equipment_inputs + 本次组合报告；不使用文件内未核验的 resolution 缓存",
                "manual_character_status": "manual_resolved：数值快照，装备来源未提供",
                "exports": ["json", "xlsx（角色与装备表）"]},
            "stat_stages": ["base", "static"], "schema": schema,
            "rotation_contract": app_rotation_capabilities(),
            "mechanics_contract": app_mechanics_capabilities(),
            "attribute_source_contract": attribute_source_contract(),
            "attribute_conversion_recovery": recovery_capabilities(),
            "static_inclusion_contract": static_inclusion_capabilities(),
            "attribute_callback_source_contract": {
                "contract": "attribute-callback-source-adoption-v1", "field": "mechanics_model.attribute_callbacks",
                "approval": "逐成员实际来源需当前manual_context及非空依据；整份原生顺序进入上下文，不自动批准数据库游戏事实",
                "source_authority": "caller_assumed_order_not_game_source_approval",
                "exclusion": "先校验完整原图；仅明确排除可裁成员和相关边，未交付或待确认不能当作排除；保留未排除成员的新执行合同",
                "result": "attribute_callbacks保留实际父命中、依赖足迹、计划、批次取样及实际提交；计划成功不代表效果已提交",
                "scope": "执行范围按mechanics_contract.attribute_callbacks发布的v1/v2与实际属性读写足迹核对；数据库同proc资格独立检查",
                "exports": ["属性回调执行（只读）", "修改顺序或依赖后须回App重新生成"]},
            "resource_threshold_source_contract": {
                "contract": "resource-threshold-source-adoption-v1",
                "fields": ["mechanics_model.resource_threshold_observers", "mechanics_model.resource_threshold_triggers"],
                "approval": "原始完整图先验证；每个观察项、触发绑定及全部依赖须有当前manual_context确认，source_only不批准新阈值图",
                "exclusion": "明确排除沿来源与依赖关闭执行副本；不能缩减观察原因或复活待确认规则；独立资源操作保持执行",
                "execution": "仅运行时mechanics_contract公布的版本和触发范围；实际消费/补充之后的跨阈值变化才可触发，初始余额高于阈值不代表曾触发",
                "result": "观察流水不等于执行；resource_threshold_triggers.trace的实际完成及outcome分别确认规则激活或领域开始/保留/结束，领域开始不等于发放Buff，issued/claimed不作为执行证明",
                "lifecycle_versions": "state_runtime2与resource_threshold_triggers3共同声明有限领域目标；起止typed绑定与全部实际依赖整体核对，独立源不借字符串前缀关联",
                "exports": ["资源阈值观察（只读）", "阈值收益执行（只读）", "更改参数须回App重算"],
                "database_automation": "当前为受审声明图，来源自动装配和更多阈值目标仍需分别验收"},
            "reaction_resource_source_contract": {
                "field": "mechanics_model.resource_reaction_contract", "supported_versions": [1, 2],
                "v2_gate": "lifecycle_gates明确绑定资源operation_id与lifecycle_id；当前代在实际建立后、结束前才可尝试收入",
                "approval": "原始完整声明先验证；操作与领域分别需要当前manual_context来源确认，不从同名或同提供者推断批准",
                "exclusion": "排除领域会排除依赖它的收入；只排除收入不会自动移除仍有用的领域。pending不能当作无效果",
                "saved_proof": "v2采用记录保留原始与执行声明及领域来源；保存重演只核对输入/执行一致性，不认证当前数据库或游戏真值",
                "exports": ["中文查询", "反应资源联动只读GUI", "三类Excel的领域资格及原始记录"],
                "database_automation": "目前仍需受审声明，游戏来源自动装配和真实队伍外部资格替换须单独验收"},
            "state_runtime_source_contract": {"field":"mechanics_model.state_runtime",
                "v2_admission":"原始声明须具有阈值触发v3，或合法非空的反应资源v2领域门控；显式空门控不单独放行v2生命周期",
                "source_identity":"完整生命周期、timer与hook路由绑定实际同源mechanics规则及共享资源操作",
                "approval":"整图逐来源当前manual_context及非空依据；source_only不批准新生命周期",
                "exclusion":"明确排除沿生命周期、规则、池与消费者依赖闭包裁剪执行副本；pending保留原问题，不能解释为无效果",
                "result":"result.state_runtime分别保存调度与真实执行记录；v2另保留applied控制游标/实际实例，逐步应用与整批完成分开；领域结束不抹去已发放收益的独立TTL",
                "database_automation":"当前为声明式受审图；上游事实自动装配与连续资源仍未提供"},
            "shared_pool_source_contract": {"field": "mechanics_model.shared_pools",
                "versions": {"1":"旧整数次数池", "2":"整数原子池；unit_scale定义每单位原子数，spend按实际accepted扣量"},
                "spend": "v2 fixed或all_available；all_or_nothing或up_to_available；未扣足与补充溢出分开，不从余额下降推断消费",
                "source_identity": "已登记mechanics来源分项；池与操作owner同提供者，consumer与收益gate同源且一一对应",
                "approval": "每项须有当前context_fingerprint的manual_context核对；source_only或自动规则身份不批准新资源图",
                "exclusion": "显式排除按依赖闭包移除执行副本；待核实来源保持pending，原生声明保留",
                "result": "result.shared_pools的只读状态和消费回执；Excel更改资源须回App重算",
                "database_automation": "本契约只执行受核对的声明；数据库资源事实自动装配尚未提供"},
            "action_event_contract": action_event_capabilities(),
            "finite_action_contract": finite_action_capabilities(),
            "action_dispatch_source_contract": action_dispatch_source_capabilities(),
            "periodic_attack_contract": periodic_attack_capabilities(),
            "periodic_lifecycle_contract": periodic_lifecycle_capabilities(),
            "energy_generation_contract": energy_generation_contract(),
            "limited_hit_benefit_contract": limited_hit_benefit_contract(),
            "mechanism_source_contract": mechanism_source_contract(),
            "damage_reporting_contract": damage_reporting_contract(),
            "external_damage_contract": external_damage_contract(),
            "attack_metadata_contract": {**attack_metadata_capabilities(), "field": "attack_metadata_sources",
                "query_action": "attack_metadata_profile", "max_sources": 32,
                "input": "一条来源唯一拥有动作档案、命中原型及附着输出槽位；原型省略元素/倍率，附着档案不写入原生",
                "review": "精确候选新鲜查询；使用动作档案accepted_profile_fingerprint一次综合确认",
                "scope": "受审结构的C0/10级E单ATK元信息；整技能部分支持、校准派发，时序/接触/共享ICD身份仍需声明",
                "duplicate_delivery": "受管原型不得手填element/multiplier或另交倍率来源；受管附着不得另交档案/来源",
                "exports": ["攻击元信息来源（只读）", "固定来源活公式工作簿"]},
            "hit_parameter_source_contract": {"compiler": "database-hit-parameter-v1", "field": "hit_parameter_sources",
                "level_contract": "level-bound-parameter-v1", "level_compiler":"database-hit-parameter-v2",
                "input": "原生v3的既有classic命中省略multiplier；声明hit_id、selection、param_index、资料线、scaling_stat、用途与证据状态",
                "scope": "显式有效等级按精确等级记录与已核实激活加级关系查询；无统一C0/10级门。level-bound-parameter-v1以base_level默认10及静态命座关系定位。其它命座机制未执行，单属性倍率用途仍由Agent/用户核实",
                "review": "本次hit_parameter_compilations给来源与required_confirmation；新契约还绑定完整expected_level_fingerprint，快照/节点/等级依据变更须复核",
                "freshness": "重新查询只填执行副本，保存的原生输入保留来源声明；缓存resolution不可信",
                "identity": "同actor的DB角色/资料线/命座一致，已有装备角色引用须匹配，scaling_stat必须与目标命中显式一致"},
            "action_resource_source_contract": {"compiler": "database-action-resource-v1", "field": "action_resource_sources",
                "max_items": 64, "plan_version": 3,
                "energy_delivery": automatic_energy_capabilities(),
                "input": "绑定已有能力；省略CD与能量数字。旧模式填已有energy_spend；action_start模式不填消耗事件，动作接纳后留只读审计。不生成动作。",
                "scope": "C0、单次充能、常规固定耗能Q；E、命座或特殊资源模式pending；用途由提供者assumed/confirmed声明",
                "identity_coverage": "explicit_sources_only", "gameplay_interpretation": "declared_not_verified",
                "review": "本次action_resource_compilations给完整中文资料、目标路径和required_confirmation；确认snapshot/resource/scope三个指纹",
                "freshness": "同快照fresh装配report.compiled_rotation_model；原生保持省略数字，旧resolution不可信",
                "modifiers": "已有退款、产球、冷却修改保留，由原执行模型处理；不因存在修改就拒绝基础值"},
            "elemental_source_contract": {"compiler": "database-icd-v1", "field": "elemental_sources",
                "input": "每个profile_id显式声明application_mode。sequence用counter_tag（附着计数标签/ICD Tag）绑定具体攻击共享身份，rule_group（附着规则组）只读自数据库attenuationGroup；no_icd/no_application不填两者；pending阻止确定性结果。旧shared_group/attenuation_group只兼容读取，缺mode或unknown统一迁移为pending",
                "review": "compose返回elemental_compilations及required_confirmation；复核条目和实际攻击共享关系后确认，不能只改指纹绕过来源变化",
                "freshness": "每次compose从当前同快照DB重新装配；不信任已序列化的resolution或resolved_template",
                "scope": "序列模式只自动提供已核实规则组的有限二元序列和微秒计时参数；计数标签须来自具体攻击attenuationTag或verification.status=confirmed的逐段结构化证据声明，不按动作类别推断；自由文本保持pending；no_icd与no_application同样须逐段结构化正向证据",
                "research_report": "可在elemental_sources[].research_report保存实际检索、来源取舍、剩余未知与数据库回填计划；该字段永远无执行权，不代替verification"},
            "reaction_damage_source_contract": {"compiler": "database-swirl-damage-v1", "field": "reaction_damage_sources",
                "dynamic_compiler": "database-swirl-damage-v2",
                "input": "原生v3、gauge-swirl-v2/gauge-freeze-reference-v1/gauge-swirl-dynamic-v3、swirl_damage.icd=unknown；最多一项swirl全局来源；参考模型另需明确假设确认",
                "review": "旧规则确认冰水两份来源；动态v3需冰水火三份独立原目标攻击与damageSequence及新编译指纹，不能复用旧确认或附着通道确认",
                "scope": "DB仅编译有限二元伤害序列/计时，普通原目标归属与次序由显式有限规则集承担；一般能力/Alter/Star/传播未执行",
                "freshness": "每次compose/calc同快照重查；保存原生unknown，占位只在执行副本填充"},
            "upstream_reaction_benefit_contract": {**reaction_benefit_compiler_caps(), "recipe_version": 2,
                "binding_field": "upstream_recipe", "report_field": "upstream_compilations",
                "input_policy": "仅保存装备来源和场景；按实际反应自动触发，私有副本生成常驻收益及冰水独立减抗规则",
                "coverage": "普通冰水原目标投影；完整来源仅部分支持，未知结构保留待补",
                "exports": ["反应收益来源（只读）", "固定来源活公式工作簿"]},
            'upstream_reaction_em_contract': {**reaction_em_compiler_caps(), 'recipe_version': 2,
                'input_policy': '原生只保存来源与明确场景；空parts、无手工复制的自动规则。重新配装或改轴后重新计算。',
                'exports': ['反应触发精通（只读）', '属性取样账', '实际反应与附着父发生证明']},
            "upstream_target_state_contract": {**target_state_compiler_caps(), "recipe_version": 2,
                "binding_field": "upstream_recipe", "report_field": "upstream_compilations",
                "input_policy": "原生仅保留配方；完整来源本次重查，固定条件分项只在执行副本生成",
                "coverage": "projection只确认当前经典分支；whole_source_status=partial及所有未支持分支持续展示",
                "target_scope": "已接受普通可冻结参考目标；每次命中前取样，角色快照不冻结目标条件"},
            "upstream_contract": {**compiler_caps(), "recipe_version": 1,
                "binding_field": "upstream_recipe", "report_field": "upstream_compilations",
                "ability_refs": "动态爆发来源须映射本地ability_id到提供者数据库slot=burst的skill_id",
                "input_policy": "parts为空；每次compose重查当前装备并编译，只读规则在report，原始plan不被改写",
                "static_policy": "分类加成若无独立承载，included保持pending；execute须明确该部件尚未包含于输入面板",
                "scope": "装备有限子集；动作与命中仍由输入声明，接受精确语义假设不代表游戏实测认证"},
            "effect_scopes": {"time": True, "target": True, "frontline": True,
                "element": True, "reaction": True, "damage_types": True, "effect_hit_ids": True,
                "limits": ["stat_scale 不读取动态 Buff 或其他转化产物", "文本不会自动转为执行规则"]},
            "engine_v2": {**get_registry(), "rotation_tuning_schema": copy.deepcopy(MATCH),
                          "match_operations": ["adjust", "replace"]},
            "examples": {"panel_plan": panel_example(), "pending_plan": pending_example()},
            "gui_editing_operations": ["apply_data_update", "detach_character", "export_resolved_plan",
                                       "enable_mechanics", "validate_mechanics_model", "query_context", "export_formula_workbook"],
            "calculation_delivery": delivery_capabilities(),
            "formula_workbook": {"command": "equipment workbook", "gui": "export_formula_workbook",
                "input": "当前可计算的原生方案", "support": "返回逐项支持范围与失效条件；结构性改轴需要重新生成",
                "editable": ["给定面板", "逐击倍率", "已映射的增益参数"],
                "limits": ["Excel 修改不回写方案", "充能效率改动会使原循环结论失效"]},
            "semantic_constraints": plan_semantic_constraints(),
            "delivery_ids": {"buff": "generated_id('buff', binding_id, part_id)",
                "effect": "delivery.effect.id（同 Buff 内唯一）", "hit": "generated_id('hit', binding_id, part_id)",
                "discovery": "compose 返回 contribution_report.generated_outputs，可据此添加轴微调"}}
