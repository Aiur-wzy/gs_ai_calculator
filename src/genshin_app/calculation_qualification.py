"""Qualification of a staged calculation without reviewing character semantics.

The engine owns generic execution, binding and numeric checks.  Character-specific
event inventory remains an explicit Agent declaration in ``event-topology-v1``.
"""
from __future__ import annotations

import copy

from genshin_app.calculation_coverage import qualify_calculation_coverage

CONTRACT = "staged-calculation-acceptance-v1"
ENERGY_INTENTS = frozenset(("single_window_legal", "multi_round_legal", "sustainable_loop"))
_HARD_FAILURES = frozenset(("failed", "pending", "invalid", "rejected", "incomplete"))
_VERIFIED = frozenset(("passed", "verified"))
_COVERED = frozenset(("complete", "complete_input_window", "covered", "full", "passed",
                      "verified"))


def _issue(issues, code, message, path=""):
    issues.append({"code": code, "path": path, "message": message})


def _active_topology_kinds(receipt):
    """Return active kinds and whether the bounded receipt summary is coherent."""
    if not isinstance(receipt, dict):
        return set(), False
    raw_kinds = receipt.get("active_node_kinds")
    raw_counts = receipt.get("active_node_kind_counts")
    if not isinstance(raw_kinds, list) or not isinstance(raw_counts, dict):
        return set(), False

    # Keep a conservative union for failure handling: a forged zero count or a
    # disagreement between the two projections must not make an active generic
    # event disappear from the acceptance gate.
    kinds = {item for item in raw_kinds if isinstance(item, str)}
    kinds.update(key for key in raw_counts if isinstance(key, str))
    counts_valid = (len(raw_counts) <= 64
                    and all(type(value) is int and value > 0
                            for value in raw_counts.values()))
    kinds_are_strings = all(isinstance(item, str) for item in raw_kinds)
    ordered_valid = (len(raw_kinds) <= 64 and kinds_are_strings
                     and raw_kinds == sorted(set(raw_kinds)))
    receipt_counts = receipt.get("counts")
    active_count = (receipt_counts.get("active_nodes")
                    if isinstance(receipt_counts, dict) else None)
    coherent = (ordered_valid and counts_valid and set(raw_kinds) == set(raw_counts)
                and type(active_count) is int and active_count >= 0
                and sum(raw_counts.values()) == active_count)
    return kinds, coherent


def _coverage_passed(receipt):
    """Require a positive generic execution decision and explicit coverage."""
    if not isinstance(receipt, dict) or receipt.get("status") not in _VERIFIED:
        return False
    coverage = receipt.get("coverage_status", receipt.get("coverage"))
    if isinstance(coverage, str):
        return coverage in _COVERED
    if isinstance(coverage, dict):
        return coverage.get("status") in _COVERED
    return False


def _qualify_calculation_checks(
    calculated, *, topology_receipt, topology_binding_receipt, energy_intent,
    trusted_provisional_usage_receipt=None, candidate_revision=None,
    stage_revision=None, database_snapshot_fingerprint=None, require_auxiliary_topology=True,
    _readonly_provisional_context=None,
):
    """Return a compact, deterministic acceptance decision.

    ``unknown`` action materialization is deliberately informational: the current
    engine does not independently certify character-specific dispatch.  A declared
    incomplete topology, a generic execution failure, or an incomplete input window
    still rejects publication.
    """
    issues = []
    if energy_intent not in ENERGY_INTENTS:
        _issue(issues, "energy_intent_missing", "尚未声明本次轴的能量证明范围", "energy_intent")
    topology_status = topology_receipt.get("status") if isinstance(topology_receipt, dict) else None
    if require_auxiliary_topology and topology_status != "numeric_ready":
        _issue(issues, "event_topology_numeric_incomplete",
               "事件拓扑仍有计算必需的数值或绑定缺项", "event_topology")
    binding_status = (topology_binding_receipt.get("status")
                      if isinstance(topology_binding_receipt, dict) else None)
    if require_auxiliary_topology and binding_status != "complete":
        _issue(issues, "event_topology_execution_binding_incomplete",
               "事件拓扑尚未完整映射到当前可执行输入", "event_topology_bindings")

    active_kinds, active_kinds_valid = _active_topology_kinds(topology_receipt)
    if require_auxiliary_topology and topology_status == "numeric_ready" and not active_kinds_valid:
        _issue(issues, "event_topology_active_kind_summary_invalid",
               "事件拓扑缺少可信的活跃节点种类摘要，不能决定通用运行时核验范围",
               "event_topology.active_node_kinds")

    if not require_auxiliary_topology:
        active_kinds = set()  # Actual consumed models are checked by execution/coverage, not an auxiliary inventory.

    result = calculated.get("result") if isinstance(calculated, dict) else None
    if not isinstance(result, dict):
        _issue(issues, "calculation_result_missing", "没有可核验的计算结果", "result")
        result = {}
    rotation = result.get("rotation_check") or {}
    action = rotation.get("action_check") or {}
    energy = rotation.get("energy_check") or {}
    interpretation = rotation.get("damage_interpretation") or {}
    from genshin_app.calculation_completion import (
        advisory_execution_completion,
        calculation_completion,
    )
    completion = (advisory_execution_completion(calculated)
                  if _readonly_provisional_context is not None else calculation_completion(calculated))
    if completion.get('execution_admission', completion['task_admission']) != 'accepted':
        _issue(issues, 'calculation_incomplete',
               '当前数值或必需执行验证尚未完成，统计窗口完成标记不能替代实际检查',
               'calculation_completion')
    scoped = result.get('damage_report_window', {}).get('contract') == 'damage-report-window-v1'
    scoped_required = scoped and completion['required_validation'] == 'complete'

    if action.get("status") != "passed" and not scoped_required:
        _issue(issues, "generic_action_validation_failed",
               "通用动作时序、冷却或接纳检查未通过", "result.rotation_check.action_check")
    if not scoped and (not isinstance(interpretation, dict) or interpretation.get("kind") == "executed_prefix"):
        _issue(issues, "executed_prefix_only", "计算只覆盖已执行前缀，不能作为完整轴结果",
               "result.rotation_check.damage_interpretation")
    complete_window = completion['complete_input_window'] if scoped else (interpretation.get("complete_first_cycle")
                       if isinstance(interpretation, dict) else None)
    if not scoped and complete_window is not True:
        complete_window = (result.get("mechanics") or {}).get("complete_first_cycle")
    if complete_window is not True:
        _issue(issues, "input_window_incomplete", "显式输入窗口没有完整执行",
               "result.mechanics.complete_first_cycle")

    reaction_verification = (interpretation.get("reaction_verification")
                             if isinstance(interpretation, dict) else None)
    if ("reaction_candidate" in active_kinds
            and (not isinstance(reaction_verification, dict)
                 or reaction_verification.get("status") not in _VERIFIED)):
        _issue(issues, "generic_elemental_runtime_unverified",
               "活跃反应候选尚未由通用元素底、附着与ICD运行时复核",
               "result.rotation_check.damage_interpretation.reaction_verification")

    application_verification = (interpretation.get("application_verification")
                                if isinstance(interpretation, dict) else None)
    if "application" in active_kinds and not _coverage_passed(application_verification):
        _issue(issues, "generic_elemental_application_unverified",
               "活跃元素附着事件缺少覆盖完整且通过的通用元素执行回执",
               "result.rotation_check.damage_interpretation.application_verification")

    if energy_intent in ("single_window_legal", "multi_round_legal"):
        if energy.get("opening_status") != "passed" and not scoped_required:
            _issue(issues, "explicit_window_energy_failed",
                   "显式输入窗口内存在能量不足或未核验的爆发动作",
                   "result.rotation_check.energy_check.opening_status")
    elif (energy_intent == "sustainable_loop"
          and (completion['steady_state'] != 'proven' or energy.get("status") != "passed" or energy.get("proven_cycle") is None
               or rotation.get("status") != "passed")):
        _issue(issues, "sustainable_energy_unproven",
               "稳态循环尚未通过跨轮能量收敛证明",
               "result.rotation_check.energy_check")

    mechanics_status = result.get("mechanics_status")
    mechanics_execution = (result.get("mechanics") or {}).get("execution_status")
    if mechanics_status not in ("passed", "ready", "unproven") and not scoped_required:
        _issue(issues, "generic_mechanics_validation_failed", "通用机制执行检查未通过",
               "result.mechanics_status")
    if mechanics_execution in _HARD_FAILURES and not scoped_required:
        _issue(issues, "generic_mechanics_validation_failed", "通用机制执行检查未通过",
               "result.mechanics.execution_status")

    materialization = result.get("action_materialization_receipt")
    materialization_status = None
    if isinstance(materialization, dict):
        materialization_status = materialization.get("status")
        calculation_class = materialization.get("calculation_class")
        if not scoped and (materialization_status in ("invalid", "incomplete")
                or calculation_class == "diagnostic" and materialization_status != "unknown"):
            _issue(issues, "declared_event_materialization_incomplete",
                   "已声明的动作事件没有完整物化", "result.action_materialization_receipt")

    coverage = qualify_calculation_coverage(
        calculated,
        topology_receipt=topology_receipt,
        topology_binding_receipt=topology_binding_receipt,
        energy_intent=energy_intent,
        trusted_provisional_usage_receipt=trusted_provisional_usage_receipt,
        candidate_revision=candidate_revision,
        stage_revision=stage_revision,
        database_snapshot_fingerprint=database_snapshot_fingerprint,
        require_auxiliary_topology=require_auxiliary_topology,
        _readonly_provisional_context=_readonly_provisional_context,
    )
    for row in coverage["blocking_issues"]:
        _issue(issues, row["code"], row["message"], "calculation_coverage")

    accepted = not issues
    return {
        "contract": CONTRACT,
        "status": "accepted" if accepted else "rejected",
        "can_accept": accepted,
        'completion': completion,
        "diagnostic_attempt": not accepted and isinstance(calculated, dict),
        "energy_intent": energy_intent,
        "character_semantics": "agent_declared",
        "event_inventory": ("not_required" if not require_auxiliary_topology else
                            "declared_complete" if topology_status == "numeric_ready" else "incomplete"),
        "numeric_and_binding_validation": ("passed" if not require_auxiliary_topology and accepted
                                            or topology_status == "numeric_ready" and binding_status == "complete"
                                            else "failed"),
        "generic_engine_validation": "passed" if accepted else "failed",
        "result_fidelity": coverage.get("result_fidelity", "not_checked"),
        "action_materialization": {
            "status": materialization_status,
            "unknown_is_blocking": False,
        },
        "calculation_coverage": coverage,
        "issues": copy.deepcopy(issues),
    }


def qualify_calculation(
    calculated, *, topology_receipt, topology_binding_receipt, energy_intent,
    trusted_provisional_usage_receipt=None, candidate_revision=None,
    stage_revision=None, database_snapshot_fingerprint=None, require_auxiliary_topology=True,
):
    """Formal result qualification; advisory scopes never enter this adapter."""
    return _qualify_calculation_checks(calculated, topology_receipt=topology_receipt,
        topology_binding_receipt=topology_binding_receipt, energy_intent=energy_intent,
        trusted_provisional_usage_receipt=trusted_provisional_usage_receipt,
        candidate_revision=candidate_revision, stage_revision=stage_revision,
        database_snapshot_fingerprint=database_snapshot_fingerprint,
        require_auxiliary_topology=require_auxiliary_topology)


def qualify_auxiliary_execution(calculated, *, energy_intent, readonly_context=None):
    """The same real checks, with no request for formal publication authority."""
    if readonly_context is not None:
        from genshin_app.equipment.provisional_readonly import (
            ReadOnlyProvisionalProjection,
        )
        from genshin_app.equipment.schema import PlanError
        if type(readonly_context) is not ReadOnlyProvisionalProjection:
            raise PlanError("只读执行核验需要App签发的当前上下文", "energy_trial_result_untrusted")
    checked = _qualify_calculation_checks(calculated, topology_receipt=None, topology_binding_receipt=None,
        energy_intent=energy_intent, require_auxiliary_topology=False,
        _readonly_provisional_context=readonly_context)
    passed = checked["can_accept"]
    energy_codes = {"energy_intent_missing", "explicit_window_energy_failed", "sustainable_energy_unproven"}
    return {key: value for key, value in checked.items() if key not in {"can_accept", "status"}} | {
        "contract": "advisory-execution-qualification-v1", "purpose": "readonly_comparison",
        "status": "passed" if passed else "failed", "execution_passed": passed,
        "energy_validation": "failed" if any(row["code"] in energy_codes for row in checked["issues"]) else "passed",
        "formal_result_qualification": "not_applicable", "formal_result_authority": False}


def pending_acceptance(*, energy_intent=None, topology_status=None, binding_status=None):
    event_inventory = (
        "declared_complete" if topology_status in ("topology_ready", "numeric_ready")
        else "incomplete" if topology_status is not None
        else "not_declared"
    )
    numeric_status = (
        "ready" if topology_status == "numeric_ready" and binding_status == "complete"
        else "incomplete" if topology_status is not None or binding_status is not None
        else "not_checked"
    )
    return {
        "contract": CONTRACT,
        "status": "not_attempted",
        "can_accept": False,
        "diagnostic_attempt": False,
        "energy_intent": energy_intent,
        "character_semantics": "agent_declared",
        "event_inventory": event_inventory,
        "numeric_and_binding_validation": numeric_status,
        "generic_engine_validation": "not_checked",
        "calculation_coverage": {
            "contract": "staged-calculation-coverage-v1",
            "scope": "current_declared_effective_graph_generic_verification_dimensions",
            "status": "not_checked",
            "complete": False,
            "automatic_verified": 0,
            "agent_materialized": 0,
            "agent_overridden": 0,
            "user_confirmed_provisional": 0,
            "agent_reported_missing_entity": 0,
            "user_confirmed_public_data_patch": 0,
            "provisional_entity_total": 0,
            "provisional_entity_usage_receipt": None,
            "result_fidelity": "not_checked",
            "external_fixed_value": 0,
            "unresolved": 0,
            "automatic_coverage_receipt": None,
            "issues": [],
            "blocking_issues": [],
        },
        "issues": [],
    }


def qualify_execution(calculated, *, energy_intent="single_window_legal", trusted_context=None):
    """Common CLI/native qualification; no auxiliary inventory is required.

    Trusted context is a live App service object, never reconstructed from JSON.
    Actual materialization, ownership and public-data checks remain unchanged.
    """
    kwargs = {}
    if trusted_context is not None:
        from genshin_app.equipment.files import fingerprint
        kwargs = {"trusted_provisional_usage_receipt": trusted_context.provisional_usage_receipt(),
            "candidate_revision": trusted_context.revision,
            "stage_revision": getattr(trusted_context, "stage_state", {}).get("stage_revision", 0),
            "database_snapshot_fingerprint": fingerprint(trusted_context.database_snapshot)}
    return qualify_calculation(calculated, topology_receipt=None, topology_binding_receipt=None,
        energy_intent=energy_intent or "single_window_legal", require_auxiliary_topology=False, **kwargs)


def qualification_for_claim(calculated, *, energy_intent=None, trusted_context=None):
    """Shared admission for an execution claim, preserving plain formula mode.

    No action/energy/element claim is manufactured from a numeric-only template.
    Declared execution models use the same qualifier for every caller.
    """
    from genshin_app.calculation_completion import calculation_completion
    from genshin_app.equipment.schema import PlanError
    if energy_intent is not None and energy_intent not in ENERGY_INTENTS:
        raise PlanError("energy_intent须为单窗、多轮或可持续循环的既有选项", "invalid_energy_intent")
    value = calculated if isinstance(calculated, dict) else {}
    plan = value.get("plan") if isinstance(value.get("plan"), dict) else {}
    claimed = (energy_intent is not None or isinstance(plan.get("rotation_model"), dict)
               or isinstance(plan.get("mechanics_model"), dict))
    if claimed:
        return {**qualify_execution(calculated, energy_intent=energy_intent or "single_window_legal",
                                  trusted_context=trusted_context), "claim": "declared_execution"}
    completion = calculation_completion(calculated)
    accepted = completion["task_admission"] == "accepted"
    return {"contract": "calculation-claim-qualification-v1", "claim": "formula_only",
        "status": "accepted" if accepted else "rejected", "can_accept": accepted,
        "completion": completion, "energy_intent": None, "event_inventory": "not_claimed",
        "issues": [] if accepted else [{"code": "calculation_incomplete", "path": "calculation_completion",
            "message": "普通公式也需要真实数值计算完整；不因未声明动作而额外要求动作闭环。"}]}
