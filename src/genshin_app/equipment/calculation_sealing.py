"""Fresh-only receipt construction over an already-executed immutable draft.

Capacity candidates are private; only the chosen complete proof set escapes.
The producer rotation is not run again and no database is read here. Existing
typed validators may reuse an independent replay through the same session.
"""
from __future__ import annotations

import copy
from datetime import UTC, datetime

from genshin_app.version import APP_VERSION
from genshin_engine.elemental_schema import FREEZE_RULESET

from .action_event_schema import action_event_receipt_compiler
from .damage_reporting_receipt import (
    make_empty_damage_receipt,
    validate_empty_damage_receipt,
)
from .energy_generation_receipt import (
    make_energy_generation_receipt,
    validate_energy_generation_receipt,
)
from .external_damage import (
    attach_external_damage,
    validate_external_damage_receipt,
)
from .files import fingerprint
from .limited_hit_benefit_receipt import (
    make_limited_hit_benefit_receipt,
    validate_limited_hit_benefit_receipt,
)
from .mechanism_receipt import (
    make_mechanism_receipt,
    validate_mechanism_receipt,
)
from .reaction_benefit_receipt import (
    UPSTREAM_RECEIPT_FIELDS,
    make_reaction_benefit_receipt,
)
from .schema import (
    PlanError,
)
from .snapshot_capture_receipt import (
    make_snapshot_capture_receipt,
    validate_snapshot_capture_receipt,
)
from .summary import build_equipment_summary


def seal_fresh_calculation(
    plan, result, report, *, model=None, complete=False, reaction_benefits=None,
    override_receipt=None, computed_value_proof=None, display_context=None,
    resolved_provisional_uses=(), candidate_session=None, calculated_at=None, _session=None,
    _readonly_provisional_context=None, task_evidence_scope=None,
):
    from .fresh_result_capacity import finish_fresh_result

    # The builder works on a private copy for each capacity attempt. Keep the
    # unchanged inputs as its source instead of copying the full audit twice.
    seed_result, seed_report = result, report
    calculated_at = calculated_at or datetime.now(UTC).isoformat()
    provisional_receipts = []

    def build_candidate(maximum_bytes):
        result, report = copy.deepcopy(seed_result), copy.deepcopy(seed_report)
        from genshin_app.damage_explanation_reporting import (
            assemble_damage_explanations,
        )
        assemble_damage_explanations(plan, result, report, maximum_bytes=maximum_bytes)
        from genshin_app.advisory_observation import observe_advisory
        from genshin_app.effect_observation import observe_effects
        def observe_summary():
            observation = observe_effects(plan, result, report)
            return {"summary": observation["summary"], "interpretation": observation["interpretation"],
                    "attention_samples": [row for row in observation["effects"]
                        if row["state"] != "formula_consumed"][:8]}
        report.setdefault("default_assistance", {})["observations"] = observe_advisory(
            "effect_observation", observe_summary, fallback={"summary": None, "attention_samples": []})
        from .agent_execution_receipt import attach_agent_execution_result
        from .calculation_overrides.ownership_receipt import (
            FIELD as OWNERSHIP_PROOF_FIELD,
        )
        from .calculation_overrides.ownership_receipt import (
            build_ownership_result_proof,
        )
        agent_execution_proof = attach_agent_execution_result(plan, result, report)
        from genshin_app.damage_count_observation import observe_damage_counts
        report["default_assistance"]["damage_counts"] = observe_advisory(
            "damage_counts", lambda: observe_damage_counts(plan, result, report), fallback={})
        result["result_fidelity"] = report["result_fidelity"]
        ownership_proof = build_ownership_result_proof(report)
        result[OWNERSHIP_PROOF_FIELD] = copy.deepcopy(ownership_proof)
        report[OWNERSHIP_PROOF_FIELD] = copy.deepcopy(ownership_proof)
        if task_evidence_scope is not None:
            result["task_evidence_scope"] = copy.deepcopy(task_evidence_scope)
        receipt = {"plan_id": plan["plan_id"], "revision": plan["revision"],
                   **{key: report[key] for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint")},
                   "template_version": 2, "engine_version": APP_VERSION,
                   "calculated_at": calculated_at, "result_fingerprint": fingerprint(result),
                   "data_versions": report["data_versions"], "assumptions": report["assumptions"]}
        if task_evidence_scope is not None:
            receipt["task_evidence_scope"] = copy.deepcopy(task_evidence_scope)
        # This immutable policy was produced by the actual execution. Source
        # reconstruction validators consume it below, so place its one receipt
        # copy before those validators, not after their checks or the size gate.
        if 'energy_settlement_policy' in result:
            receipt['energy_settlement_policy'] = copy.deepcopy(result['energy_settlement_policy'])
        if override_receipt is not None:
            receipt["calculation_override_receipt"] = copy.deepcopy(override_receipt)
        if agent_execution_proof is not None:
            receipt["agent_execution_proof"] = agent_execution_proof
        receipt[OWNERSHIP_PROOF_FIELD] = copy.deepcopy(ownership_proof)
        receipt["result_fidelity"] = report["result_fidelity"]
        if computed_value_proof is not None:
            receipt["calculation_computed_value_overrides"] = copy.deepcopy(
                computed_value_proof
            )
            receipt["calculation_computed_value_window"] = copy.deepcopy(
                computed_value_proof["aggregation"]["window"]
            )
        if report.get("action_materialization_receipt"):
            receipt["action_materialization_receipt"] = copy.deepcopy(
                report["action_materialization_receipt"])
            if "action_event_compilations" in result:
                receipt["action_materialization_proof"] = {
                    "contract": "action-materialization-proof-v1",
                    "public_receipt_fingerprint": fingerprint(
                        report["action_materialization_receipt"]),
                "action_event_compilations_fingerprint": fingerprint(
                    result["action_event_compilations"]),
                "source_fingerprint": fingerprint(
                    result["action_materialization_source"]),
                }
        if reaction_benefits:
            receipt["reaction_benefits"] = make_reaction_benefit_receipt(plan, report)
        if result.get('reaction_em_compilations'):
            from .reaction_em_receipt import make_reaction_em_receipt
            receipt['reaction_em'] = make_reaction_em_receipt(plan, report, result)
        if report.get("energy_generation_compilations"):
            receipt["energy_generation"] = make_energy_generation_receipt(plan, report)
        if report.get("limited_hit_benefit_compilations"):
            receipt["limited_hit_benefit"] = make_limited_hit_benefit_receipt(plan, report)
        if report.get("passive_compilations"):
            from .passive_receipt import make_passive_receipt
            receipt["passives"] = make_passive_receipt(plan, report)
        if report.get("periodic_attack_compilations"):
            from .periodic_receipt import make_periodic_receipt
            receipt["periodic_attacks"] = make_periodic_receipt(plan, report)
        if report.get("upstream_semantics"):
            receipt["upstream_semantics"] = copy.deepcopy(report["upstream_semantics"])
            receipt["upstream_sources"] = [{k: c.get(k) for k in UPSTREAM_RECEIPT_FIELDS}
                for c in report["upstream_compilations"]]
        if result.get("fixed_effect_sources"):
            condition_profiles = sorted({source["target_state_profile"] for source in result["fixed_effect_sources"]})
            receipt["target_conditions"] = {
                "profile_id": condition_profiles[0] if len(condition_profiles) == 1 else "mixed-target-profiles",
                "profile_ids": condition_profiles, "sample_phase": "before_application",
                "fixed_sources_fingerprint": fingerprint(result["fixed_effect_sources"]),
                "trace_fingerprint": fingerprint(result["target_condition_trace"]),
                "compiled_model_fingerprint": fingerprint(report["mechanics_model"]),
                "upstream_compilations_fingerprint": fingerprint(report.get("upstream_compilations", [])),
                "evidence_tier": "experimental_reference" if condition_profiles == ["freeze-reference-target-v1"] else "declared_elemental_execution"}
        if report.get("elemental_compilations"):
            receipt["elemental_sources"] = [{k: item.get(k) for k in ("profile_id", "snapshot", "source", "compiler", "compilation_fingerprint")}
                                          for item in report["elemental_compilations"]]
        if report.get("hit_parameter_compilations"):
            receipt["hit_parameter_sources"] = [{k: item[k] for k in ("hit_id", "snapshot", "source", "compiler", "compilation_fingerprint")}
                                              for item in report["hit_parameter_compilations"]]
        if report.get("action_resource_compilations"):
            receipt["action_resources"] = {
                "compiled_rotation_model_fingerprint": report["compiled_rotation_model_fingerprint"],
                "compilations_fingerprint": fingerprint(report["action_resource_compilations"]),
                "identity_coverage": "explicit_sources_only"}
            from .energy_delivery_receipt import make_automatic_energy_receipt
            automatic_receipt = make_automatic_energy_receipt(plan, report, result, _session=_session)
            if automatic_receipt is not None:
                receipt["action_resources"]["automatic_energy_delivery"] = automatic_receipt
                receipt["result_fingerprint"] = fingerprint(result)
        if report.get("reaction_damage_compilations"):
            receipt["reaction_damage_sources"] = [{k: item[k] for k in ("reaction_type", "snapshot", "profile_fingerprint", "compiler", "compilation_fingerprint")}
                                                for item in report["reaction_damage_compilations"]]
        if report.get("action_event_compilations"):
            source_compilations = {key: report.get(key, []) for key in
                ("hit_parameter_compilations", "elemental_compilations", "reaction_damage_compilations")}
            if report.get("attack_metadata_compilations"):
                source_compilations["attack_metadata_compilations"] = report["attack_metadata_compilations"]
            if report.get("periodic_attack_compilations"):
                source_compilations["periodic_attack_compilations"] = report["periodic_attack_compilations"]
            if report.get("energy_generation_compilations"):
                source_compilations["energy_generation_compilations"] = report["energy_generation_compilations"]
            if report.get("limited_hit_benefit_compilations"):
                source_compilations["limited_hit_benefit_compilations"] = report["limited_hit_benefit_compilations"]
            if report.get("mechanism_compilations"):
                source_compilations["mechanism_compilations"] = report["mechanism_compilations"]
            receipt["action_events"] = {
                "compiler": action_event_receipt_compiler(report["action_event_compilations"]), "dispatch_mode": "calibrated",
                "skill_coverage": "partial", "resource_coverage": "not_generated",
                "compilations_fingerprint": fingerprint(report["action_event_compilations"]),
                "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
                "execution_rotation_model_fingerprint": fingerprint(report["action_event_execution_rotation_model"]),
                "mechanics_input_template_fingerprint": fingerprint(report["mechanics_input_template"]),
                "source_compilations_fingerprint": fingerprint(source_compilations),
            }
        if report.get("attack_metadata_compilations"):
            from .action_dispatch_sources import action_dispatch_execution_plan
            attack_execution_plan = action_dispatch_execution_plan(plan, report)
            dispatch_materialization = report.get("action_dispatch_materialization") or {}
            attack_metadata_receipt = {
                "compiler": "database-attack-metadata-v1",
                "compilations_fingerprint": fingerprint(report["attack_metadata_compilations"]),
                "input_sources_fingerprint": fingerprint(
                    attack_execution_plan.get("attack_metadata_sources", [])),
            }
            dispatch_fingerprint = dispatch_materialization.get("content_fingerprint")
            if dispatch_fingerprint is not None:
                attack_metadata_receipt[
                    "action_dispatch_materialization_fingerprint"] = dispatch_fingerprint
            receipt["attack_metadata"] = attack_metadata_receipt
        if result.get("elemental", {}).get("ruleset") == FREEZE_RULESET:
            receipt["elemental_reference"] = {
                k: copy.deepcopy(result["elemental"].get(k)) for k in
                ("ruleset", "evidence_tier", "reference_contract", "reference_acceptance")}
            if complete:
                result["rotation_check"]["damage_interpretation"]["message"] = (
                    "伤害来自完整显式输入窗口；元素状态按已接受的实验参考模型计算，"
                    "含续冻合并与反应聚合约定。确认假设不代表已验证游戏真值；目标条件收益仅执行本次声明的受支持来源。")
                receipt["result_fingerprint"] = fingerprint(result)
        if result.get("damage_reporting"):
            count = result["damage_reporting"]["planned_contacts"]
            result["rotation_check"]["damage_interpretation"]["message"] = (
                f"完整输入窗口内{count}个已声明伤害接触全部明确未命中，总伤和窗口DPS为0；"
                "动作、收益激活与资源流水仍保留，循环是否成立见独立检查。")
            report["damage_reporting"] = copy.deepcopy(result["damage_reporting"])
            report["damage_reporting_execution_rotation_model"] = copy.deepcopy(model)
            receipt["damage_reporting"] = make_empty_damage_receipt(plan, result, report)
            receipt["result_fingerprint"] = fingerprint(result)
        if report.get("mechanism_compilations"):
            receipt["mechanisms"] = make_mechanism_receipt(plan, report, result)
            receipt["result_fingerprint"] = fingerprint(result)
        from .damage_report_window_receipt import make_window_receipt
        window_proof = make_window_receipt(plan, result, report, model)
        if window_proof is not None:
            receipt["damage_report_window"] = window_proof
            receipt["result_fingerprint"] = fingerprint(result)
        from .special_damage_echo_receipt import make_echo_receipt
        echo_proof = make_echo_receipt(plan, result, report, model)
        if echo_proof is not None:
            receipt['special_damage_echoes'] = echo_proof
            receipt['result_fingerprint'] = fingerprint(result)
        capture_proof = make_snapshot_capture_receipt(plan, report, result)
        if capture_proof is not None:
            receipt['snapshot_captures'] = capture_proof
            receipt['result_fingerprint'] = fingerprint(result)
        from .attribute_receipt import (
            make_attribute_receipt,
            validate_attribute_receipt,
        )
        attribute_proof = make_attribute_receipt(plan, report, result)
        if attribute_proof is not None:
            receipt['attribute_ledger'] = attribute_proof
            validate_attribute_receipt(plan, result, report, receipt)
        from .counter_frame_receipt import (
            make_counter_frame_receipt,
            validate_counter_frame_receipt,
        )
        from .static_inclusion_receipt import (
            make_static_inclusion_receipt,
            validate_static_inclusion_receipt,
        )
        counter_frame_proof = make_counter_frame_receipt(plan, report, result)
        if counter_frame_proof is not None:
            receipt['counter_self_frames'] = counter_frame_proof
            receipt['result_fingerprint'] = fingerprint(result)
            validate_counter_frame_receipt(plan, result, report, receipt, _session=_session)
        summary = build_equipment_summary(report["plan"], report, kind="calculation_snapshot")
        from .resource_reaction_receipt import (
            make_resource_reaction_receipt,
            validate_resource_reaction_receipt,
        )
        resource_reaction_proof = make_resource_reaction_receipt(plan, report, result, _session=_session)
        if resource_reaction_proof is not None:
            receipt['resource_reactions'] = resource_reaction_proof
            receipt['result_fingerprint'] = fingerprint(result)
            from .execution_verification import ExecutionVerificationError
            try:
                validate_resource_reaction_receipt(plan, result, report, receipt, _session=_session)
            except ExecutionVerificationError as exc:
                raise PlanError(str(exc), 'resource_reaction_proof_pending',
                                {'status': 'proof_pending', 'fresh': False, 'actual_result': result,
                                 'boundary_code': exc.code}) from exc
        from .action_capacity_receipt import (
            make_action_capacity_receipt,
            validate_action_capacity_receipt,
        )
        capacity_proof = make_action_capacity_receipt(plan, report, result, _session=_session)
        if capacity_proof is not None:
            receipt["action_capacities"] = capacity_proof
            receipt["result_fingerprint"] = fingerprint(result)
            validate_action_capacity_receipt(plan, result, report, receipt, _session=_session)
        from .action_admission_receipt import (
            make_action_admission_receipt,
            validate_action_admission_receipt,
        )
        admission_proof = make_action_admission_receipt(plan, report, result, _session=_session)
        if admission_proof is not None:
            receipt["action_admissions"] = admission_proof
            receipt["result_fingerprint"] = fingerprint(result)
            validate_action_admission_receipt(plan, result, report, receipt, _session=_session)
        # External numeric proof fingerprints the complete engine result.  Attach
        # it only after every typed receipt above has added its execution evidence.
        try:
            external_proof = attach_external_damage(plan, result, report)
        except ValueError as exc:
            raise PlanError(str(exc), "external_damage_pending") from exc
        if external_proof is not None:
            receipt["external_damage"] = external_proof
            receipt["result_fingerprint"] = fingerprint(result)
        from genshin_app.damage_explanation_reporting import make_explanation_receipt
        receipt["damage_explanations"] = make_explanation_receipt(result)
        summary["context"]["calculated_at"] = receipt["calculated_at"]
        report["equipment_summary"] = copy.deepcopy(summary)
        if report.get("passive_compilations"):
            from .passive_receipt import validate_passive_receipt
            validate_passive_receipt(plan, result, report, receipt)
        if report.get("periodic_attack_compilations"):
            from .periodic_receipt import validate_periodic_receipt
            validate_periodic_receipt(plan, result, report, receipt)
        if (receipt.get("action_resources") or {}).get("automatic_energy_delivery") is not None:
            from .energy_delivery_receipt import validate_automatic_energy_receipt
            validate_automatic_energy_receipt(plan, result, report, receipt, _session=_session)
        validate_energy_generation_receipt(plan, result, report, receipt)
        validate_limited_hit_benefit_receipt(plan, result, report, receipt)
        validate_mechanism_receipt(plan, result, report, receipt, _session=_session)
        validate_empty_damage_receipt(plan, result, report, receipt)
        validate_snapshot_capture_receipt(plan, result, report, receipt, _session=_session)
        validate_action_admission_receipt(plan, result, report, receipt, _session=_session)
        validate_external_damage_receipt(plan, result, report, receipt)
        if agent_execution_proof is not None:
            # Result fields are complete at this point.  Bind the D1 proof to the
            # final result rather than the earlier pre-receipt projection.
            receipt["result_fingerprint"] = fingerprint(result)
        from .agent_execution_receipt import validate_agent_execution_result_receipt
        validate_agent_execution_result_receipt(result, receipt)
        from .action_automation_audit import (
            validate_action_materialization_result_receipt,
        )
        validate_action_materialization_result_receipt(result, receipt)
        from .calculation_overrides import (
            validate_computed_value_result_receipt,
            validate_execution_result_receipt,
        )
        validate_execution_result_receipt(result, receipt)
        validate_computed_value_result_receipt(result, receipt, _session=_session)
        from .calculation_overrides.ownership_receipt import (
            validate_ownership_result_proof,
        )
        validate_ownership_result_proof(result, receipt, report)
        if resolved_provisional_uses:
            if _readonly_provisional_context is not None:
                from .provisional_readonly import FIELD_NAME
                evidence = _readonly_provisional_context.execution_evidence(plan, resolved_provisional_uses)
                result[FIELD_NAME] = copy.deepcopy(evidence)
                receipt[FIELD_NAME] = copy.deepcopy(evidence)
                report[FIELD_NAME] = copy.deepcopy(evidence)
            else:
                if candidate_session is None:
                    raise PlanError("临时公共实体缺少当前会话使用回执", "provisional_entity_session_required")
                if not provisional_receipts:
                    provisional_receipts.append(candidate_session.publish_provisional_usage_receipt(
                        resolved_uses=resolved_provisional_uses))
                provisional_usage_receipt = provisional_receipts[0]
                result["provisional_entity_usage_receipt"] = copy.deepcopy(provisional_usage_receipt)
                receipt["provisional_entity_usage_receipt"] = copy.deepcopy(provisional_usage_receipt)
                report["provisional_entity_usage_receipt"] = copy.deepcopy(provisional_usage_receipt)
        receipt['result_fingerprint'] = fingerprint(result)
        from .damage_report_window_receipt import validate_window_calculation
        validate_window_calculation(plan, result, report, receipt, _session=_session)
        from .special_damage_echo_receipt import validate_echo_calculation
        validate_echo_calculation(plan, result, report, receipt, _session=_session)
        # This proof binds the COMPLETE result. Resource/counter/provisional
        # makers above may append actual execution evidence; sealing earlier
        # produced a stale calculation fingerprint on an unchanged export.
        static_proof = make_static_inclusion_receipt(plan, report, result)
        if static_proof is not None:
            receipt['static_inclusions'] = static_proof
            validate_static_inclusion_receipt(plan, result, report, receipt)
        for field in ("attribute_execution_budget", "calculation_producer_contract"):
            if field in result:
                receipt[field] = copy.deepcopy(result[field])
        from genshin_app.damage_explanation_reporting import (
            validate_damage_explanations,
        )
        validate_damage_explanations(result, receipt, report=report)
        calculated = {"plan": report["plan"], "result": result, "result_receipt": receipt, "report": report,
                      "equipment_summary": summary, "presentation_issues": report["presentation_issues"]}
        if display_context is not None:
            calculated["display_context"] = display_context
            receipt["display_source"] = copy.deepcopy(display_context["receipt"])
        # Completion is part of the delivered envelope. Adding it after the
        # capacity decision could turn an accepted private candidate into an
        # oversized public file, even when every numerical proof fit.
        from genshin_app.calculation_completion import calculation_completion
        calculated['completion'] = calculation_completion(calculated)
        return calculated

    return finish_fresh_result(build_candidate)
