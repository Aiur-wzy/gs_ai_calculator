"""One read-only completion projection for commands, views and publications.

Transport success is separate from task admission. This function derives facts
from execution output; a cached caller-supplied completion marker is ignored.
"""
from __future__ import annotations

import math

CONTRACT = 'calculation-completion-v1'
BAD = {'failed', 'invalid', 'pending', 'not_verified', 'not_checked'}


def has_valid_reporting_scope(result):
    """Only a valid reporting contract may distinguish optional later cycles."""
    if not isinstance(result, dict) or 'damage_report_window' not in result:
        return False
    from .equipment.damage_report_scope import reporting_window
    try:
        return reporting_window(result) is not None
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError, RecursionError):
        return False


def execution_issue_rows(result):
    """Return existing execution diagnostics without changing their evidence."""
    rotation = result.get('rotation_check')
    rotation = rotation if isinstance(rotation, dict) else {}
    rows = []
    for value in (rotation.get('issues', []), result.get('mechanics_issues', [])):
        if isinstance(value, (list, tuple)):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def execution_issue_is_blocking(row):
    if row.get('blocking') is True or row.get('severity') in {'error', 'pending', 'failed', 'invalid'}:
        return True
    return not (row.get('blocking') is False or row.get('severity') in {'info', 'warning', 'notice'})


def execution_issue_is_required(row, *, valid_reporting_scope):
    # Origin zero covers the entire explicit input, including both user rounds.
    # Time after the DPS cutoff does not turn a required action into an audit.
    cycle = row.get('cycle', row.get('origin', 0))
    return not (valid_reporting_scope and type(cycle) is int and cycle >= 1)


def _execution_completion(calculated=None, *, result=None, report=None):
    calculated = calculated if isinstance(calculated, dict) else {}
    result = result if isinstance(result, dict) else calculated.get('result', {})
    report = report if isinstance(report, dict) else calculated.get('report', {})
    result = result if isinstance(result, dict) else {}
    rotation = result.get('rotation_check') or {}
    mechanics = result.get('mechanics') or {}
    raw_interpretation = rotation.get('damage_interpretation')
    interpretation = (raw_interpretation if isinstance(raw_interpretation, dict) else
                      {'kind': raw_interpretation} if isinstance(raw_interpretation, str) else {})
    reporting = result.get('damage_report_window')
    reasons = []
    optional = 'not_requested'
    scoped = isinstance(reporting, dict) and reporting.get('contract') == 'damage-report-window-v1'
    valid_scope = scoped and has_valid_reporting_scope(result)
    if scoped:
        window = reporting.get('status', 'pending')
        required = reporting.get('required_validation', {}).get('status', 'pending')
        optional = reporting.get('optional_audit', {}).get('status', 'not_requested')
        complete_window = window == 'complete'
        if not valid_scope:
            complete_window, window = False, 'pending'
            required = 'failed' if required == 'failed' else 'pending'
            reasons.append('damage_report_scope_invalid')
    else:
        complete_window = interpretation.get('complete_first_cycle', mechanics.get('complete_first_cycle'))
        has_execution = bool(mechanics) or bool(result.get('mechanics_status'))
        if not has_execution and rotation.get('status') in (None, 'not_checked'):
            complete_window = bool(result)  # Plain resolved formula, no execution claim.
        complete_window = complete_window is True
        window = 'complete' if complete_window else 'pending'
        states = [rotation.get('status'), result.get('mechanics_status'), mechanics.get('execution_status')]
        required = ('failed' if any(s in ('failed', 'invalid') for s in states) else
                    'pending' if any(s in ('pending', 'not_verified') for s in states) else 'complete')
        for name in ('action_check', 'energy_check'):
            check = rotation.get(name) or {}
            state = check.get('opening_status', check.get('status'))
            if state in BAD and not (state == 'not_checked' and not has_execution):
                required = 'failed' if state in ('failed', 'invalid') else 'pending'
        if interpretation.get('kind') == 'executed_prefix':
            complete_window, window = False, 'pending'
        if reporting is not None:
            complete_window, window, required = False, 'pending', 'pending'
            reasons.append('unknown_damage_report_contract')
    blocking_rows = [row for row in execution_issue_rows(result) if execution_issue_is_blocking(row)]
    required_rows = [row for row in blocking_rows
                     if execution_issue_is_required(row, valid_reporting_scope=valid_scope)]
    optional_rows = [row for row in blocking_rows
                     if not execution_issue_is_required(row, valid_reporting_scope=valid_scope)]
    if required_rows:
        required = ('failed' if required == 'failed' or any(row.get('severity') in
                    {'error', 'failed', 'invalid'} for row in required_rows) else 'pending')
        reasons.append('required_execution_issue')
    if optional_rows:
        optional = ('failed' if any(row.get('severity') in {'error', 'failed', 'invalid'}
                                  for row in optional_rows) else 'pending')
    if valid_scope:
        # These flags describe origin zero, unlike aggregate statuses which
        # may include a failed optional later cycle. A saved window cannot
        # overrule contradictory evidence about its own required input.
        flags = [owner[key] for owner, key in (
            (interpretation, 'complete_first_cycle'), (mechanics, 'complete_first_cycle'))
            if key in owner]
        if any(value is not True for value in flags):
            complete_window, window, required = False, 'pending', 'pending'
            reasons.append('required_input_completeness_conflict')
        opening = (rotation.get('energy_check') or {}).get('opening_status')
        if opening in BAD:
            required = 'failed' if opening in {'failed', 'invalid'} else 'pending'
        aggregate = [rotation.get('status'), result.get('mechanics_status'),
                     mechanics.get('execution_status'),
                     (rotation.get('action_check') or {}).get('status')]
        if optional not in {'failed', 'pending'} and (
                any(state in BAD for state in aggregate)
                or interpretation.get('kind') == 'executed_prefix'):
            required = ('failed' if any(state in {'failed', 'invalid'} for state in aggregate)
                        else 'pending')
            reasons.append('required_execution_status_conflict')
    if not complete_window:
        reasons.append('damage_report_incomplete')
    if required != 'complete':
        reasons.append('required_validation_' + str(required))
    if report.get('state') not in (None, 'ready'):
        reasons.append('assembly_not_ready')
    material = result.get('action_materialization_receipt') or {}
    if not scoped and (material.get('status') in ('invalid', 'incomplete') or (
            material.get('calculation_class') == 'diagnostic' and material.get('status') != 'unknown')):
        reasons.append('required_materialization_incomplete')
    summary = result.get('calculation_totals', result.get('summary', {}))
    total = summary.get('total_damage') if isinstance(summary, dict) else None
    try:
        numeric_available = type(total) in (int, float) and math.isfinite(total) and total >= 0
    except OverflowError:
        numeric_available = False
    if not numeric_available:
        reasons.append('numeric_result_unavailable')
    accepted = not reasons
    from .equipment.periodic_execution_scope import periodic_steady_state_allowed
    steady_allowed = periodic_steady_state_allowed(result)
    return {'contract': CONTRACT, 'command_status': 'completed',
            'calculation_status': 'complete' if accepted else 'diagnostic',
            'task_admission': 'accepted' if accepted else 'failed' if required == 'failed' else 'pending',
            'complete_input_window': complete_window, 'damage_report': window,
            'required_validation': required, 'optional_audit': optional,
            'steady_state': 'proven' if steady_allowed and not blocking_rows and rotation.get('status') == 'passed' and rotation.get('energy_check', {}).get('cycle_status') == 'passed' else 'unproven',
            'reasons': reasons}



def advisory_execution_completion(calculated):
    """Same real execution checks, expressly without formal-result authority."""
    value = _execution_completion(calculated)
    return {**value, "execution_admission": value["task_admission"],
            "task_admission": "not_requested", "formal_result_authority": False}


def calculation_completion(calculated=None, *, result=None, report=None):
    value = _execution_completion(calculated, result=result, report=report)
    from .equipment.provisional_readonly import FIELD_NAME
    document = calculated if isinstance(calculated, dict) else {}
    actual = result if isinstance(result, dict) else document.get("result", {})
    receipt = document.get("result_receipt", {})
    if any(isinstance(owner, dict) and FIELD_NAME in owner for owner in (actual, receipt)):
        return {**value, "calculation_status": "diagnostic", "task_admission": "pending",
                "reasons": [*value["reasons"], "advisory_projection_not_formal_result"]}
    return value


def require_calculation_complete(calculated, *, files=None):
    completion = calculation_completion(calculated)
    if completion['task_admission'] != 'accepted':
        from genshin_app.equipment.schema import PlanError
        from genshin_app.failure_analysis import calculation_feedback
        feedback = calculation_feedback({**calculated, 'completion': completion})
        recovery = {'action': 'correct_execution_inputs', 'retry_same_request': False}
        paths = files.get('published_paths', []) if isinstance(files, dict) else []
        if paths:
            failure = feedback.get('failure_summary', {}).get('first_rejected_event', {})
            recovery.update(action='inspect_saved_execution', cli='equipment diagnose',
                            calculated_path=paths[0],
                            request={'view': 'assistance',
                                     'section': 'energy' if failure.get('deficit', 0) > 0 else 'all'})
        raise PlanError('正式计算未完成；保存内容仅为真实诊断前缀。', 'calculation_incomplete',
                        {'completion': completion, 'execution_feedback': feedback, 'recovery': recovery,
                         **({'files': files} if files else {})})
    return completion
