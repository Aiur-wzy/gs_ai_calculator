"""Bounded saved-input consistency for v2 counter reads, never DB approval.

The proof is an offline replay contract, not a signature. Whole native packages
additionally bind their source-adoption report and actual compiled model. No
engine replay or new fields are added for v1 inputs without v2/self-frame residue.
"""
from __future__ import annotations

import copy
import json

from genshin_app.equipment.verification_scope import verification_scope

from .files import fingerprint, input_view
from .schema import PlanError

CONTRACT = 'counter-self-frame-receipt-v1'
MAX_EVIDENCE_BYTES = 8*1024*1024
MESSAGE = '资源输出帧、当前消费门或已保存的执行输入不一致，请重新计算。'
EXECUTION_KEYS = {'template', 'rotation_model', 'mechanics_model', 'matches'}
CHECK_APP_KEYS = {'input_fingerprint', 'panel_bindings', 'resolved_rotation_model', 'damage_interpretation'}
SECTIONS = ('resource_counter_views', 'resource_counters', 'shared_pools', 'state_runtime',
            'sampling_trace', 'mechanics_trace', 'hits', 'summary', 'mechanics',
            'mechanics_status', 'mechanics_issues', 'snapshot_captures')


def _same(left, right):
    if fingerprint(left) != fingerprint(right):
        raise ValueError(MESSAGE)


def _bounded(value):
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':')).iterencode(value):
        size += len(chunk.encode('utf-8'))
        if size > MAX_EVIDENCE_BYTES:
            raise ValueError('资源输出帧离线证明超过8MiB；未截断交付，也未标为fresh。')


def _declared(model):
    if not isinstance(model, dict):
        return False
    config = model.get('resource_counter_views')
    if isinstance(config, dict) and config.get('schema_version') == 2:
        return True
    return any(isinstance(effect.get('value'), dict)
               and effect['value'].get('kind') == 'counter_view' and effect['value'].get('sample') == 'self_frame'
               for rule in model.get('rules', []) if isinstance(rule, dict)
               for effect in rule.get('effects', []) if isinstance(effect, dict))


def _row_residue(row):
    if not isinstance(row, dict):
        return False
    expr = row.get('expression')
    return (row.get('sample') == 'self_frame' or row.get('type') in ('counter_frame_capture', 'counter_frame_read')
            or any(k in row for k in ('frame_selection', 'capture_context', 'gate_qualification'))
            or 'frame_id' in row and (row.get('kind') == 'counter_view' or row.get('type') == 'counter_view_read')
            or isinstance(expr, dict) and expr.get('kind') == 'counter_view' and expr.get('sample') == 'self_frame')


def _residue(result):
    if 'counter_frame_execution' in result or 'counter_frame_source_adoptions' in result:
        return True
    views = result.get('resource_counter_views')
    if isinstance(views, dict) and (views.get('contract') == 'resource-counter-views-self-frame-v2'
            or isinstance(views.get('historical_capture'), dict)
            or any(_row_residue(row) for row in views.get('trace', []))):
        return True
    if any(_row_residue(row) for key in ('sampling_trace', 'mechanics_trace') for row in result.get(key, [])):
        return True
    for key in ('snapshot_capture_execution', 'mechanism_execution', 'counter_frame_execution'):
        context = result.get(key)
        if isinstance(context, dict) and (_declared(context.get('mechanics_model')) or _declared(context.get('model'))):
            return True
    context = result.get('mechanism_replay_context') or {}
    return isinstance(context, dict) and _declared((context.get('plan') or {}).get('mechanics_model'))


def _ledger(result):
    fields = {key: copy.deepcopy(result.get(key)) for key in SECTIONS}
    for key in ('resource_counter_views', 'resource_counters', 'shared_pools', 'state_runtime'):
        if isinstance(fields[key], dict):
            fields[key].pop('source_adoptions', None)
    fields['rotation_check'] = {k: v for k, v in (result.get('rotation_check') or {}).items() if k not in CHECK_APP_KEYS}
    return fields


def _adoptions(result):
    return result.get('counter_frame_source_adoptions', (result.get('resource_counter_views') or {}).get('source_adoptions'))


def _proof(result, receipt):
    return {'contract': CONTRACT, 'verification_scope': 'saved_input_replay_not_current_database_approval',
            'input_fingerprint': receipt.get('input_fingerprint'),
            'output_fingerprint': receipt.get('output_fingerprint'),
            'source_fingerprint': receipt.get('source_fingerprint'),
            'execution_fingerprint': fingerprint(result['counter_frame_execution']),
            'ledger_fingerprint': fingerprint(_ledger(result)),
            'source_adoptions_fingerprint': fingerprint(_adoptions(result))}


def make_counter_frame_receipt(plan, report, result):
    """Prepare all evidence before adding execution data; oversize is not fresh."""
    if not (_declared(report.get('mechanics_model')) or _declared(plan.get('mechanics_model'))):
        if _residue(result):
            raise ValueError(MESSAGE)
        return None
    execution = {'template': report['mechanics_input_template'],
                 'rotation_model': result['rotation_check']['resolved_rotation_model'],
                 'mechanics_model': report['mechanics_model'], 'matches': report.get('mechanics_matches', [])}
    adoptions = report.get('resource_counter_view_adoptions', [])
    if 'resource_counter_views' in result:
        _same(result['resource_counter_views'].get('source_adoptions'), adoptions)
    try:
        # Check bounded raw sections before making a detached ledger projection.
        from .replay_numeric_projection import numeric_budget_result
        _bounded([execution, numeric_budget_result({key: result.get(key) for key in SECTIONS}), result.get('rotation_check'), adoptions])
        candidate = copy.deepcopy(execution)
        source_candidate = copy.deepcopy(adoptions)
        shadow = {**result, 'counter_frame_execution': candidate, 'counter_frame_source_adoptions': source_candidate}
        proof = _proof(shadow, {k: report[k] for k in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')})
        _bounded([candidate, numeric_budget_result({key: result.get(key) for key in SECTIONS}), result.get('rotation_check'), source_candidate, proof])
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise PlanError(str(exc), 'counter_frame_proof_pending', {
            'status': 'proof_pending', 'fresh': False, 'execution_status': result.get('mechanics_status'),
            'actual_result': result}) from exc
    result['counter_frame_execution'] = candidate
    result['counter_frame_source_adoptions'] = source_candidate
    return proof


def _validate_result(result, receipt, _session):
    execution, proof = result.get('counter_frame_execution'), receipt.get('counter_self_frames')
    if (type(execution) is not dict or set(execution) != EXECUTION_KEYS or type(proof) is not dict
            or type(execution.get('mechanics_model')) is not dict or type(execution.get('matches')) is not list
            or type(result.get('counter_frame_source_adoptions')) is not list):
        raise ValueError(MESSAGE)
    from .replay_numeric_projection import numeric_budget_result
    _bounded([execution, numeric_budget_result({key: result.get(key) for key in SECTIONS}), result.get('rotation_check'), _adoptions(result), proof])
    if 'resource_counter_views' in result:
        _same(result['resource_counter_views'].get('source_adoptions'), _adoptions(result))
    _same(proof, _proof(result, receipt))
    _same(receipt.get('result_fingerprint'), fingerprint(result))
    check = result['rotation_check']
    _same(check.get('resolved_rotation_model'), execution['rotation_model'])
    _same(check.get('input_fingerprint'), fingerprint(execution))
    replay = _session.replay(execution)
    expected = replay.get('result')
    if expected is None or replay.get('resolved_template') is None:
        raise ValueError(MESSAGE)
    _same(fingerprint(replay['resolved_template']), receipt.get('output_fingerprint'))
    if not expected.get('mechanics', {}).get('complete_first_cycle', False):
        expected['summary']['dps'] = None
    expected.update(rotation_check=replay['rotation_check'], mechanics_trace=replay['mechanics_trace'],
                    sampling_trace=replay.get('sampling_trace', []), mechanics_status=replay['status'],
                    mechanics_issues=replay.get('issues', []))
    from .replay_numeric_projection import replay_numeric_ledgers
    _same(*replay_numeric_ledgers(_ledger(expected), _ledger(result), result, receipt))


@verification_scope
def validate_counter_frame_result_receipt(result, receipt, *, _session=None):
    try:
        if not (_residue(result) or 'counter_self_frames' in receipt):
            return
        _validate_result(result, receipt, _session)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_counter_frame_receipt(plan, result, report, receipt, *, _session=None):
    try:
        if not (_declared(plan.get('mechanics_model')) or _declared(report.get('mechanics_model'))
                or _residue(result) or 'counter_self_frames' in receipt):
            return
        _validate_result(result, receipt, _session)
        execution = result['counter_frame_execution']
        _same(fingerprint(input_view(plan)), receipt.get('input_fingerprint'))
        _same(report['input_fingerprint'], receipt.get('input_fingerprint'))
        _same(input_view(plan), input_view(report['plan']))
        _same(report['mechanics_model'], execution['mechanics_model'])
        _same(report['mechanics_input_template'], execution['template'])
        _same(report.get('mechanics_matches', []), execution['matches'])
        _same(_adoptions(result), report.get('resource_counter_view_adoptions', []))
        _same(report['output_fingerprint'], receipt.get('output_fingerprint'))
        _same(fingerprint(report['plan']['resolved_template']), receipt.get('output_fingerprint'))
        _same(fingerprint(plan['resolved_template']), receipt.get('output_fingerprint'))
        _same(report['source_fingerprint'], receipt.get('source_fingerprint'))
        from genshin_app.rotation_check import resolve_rotation_model
        declared_rotation = report.get('compiled_rotation_model', plan['rotation_model'])
        _bounded([report['mechanics_input_template'], declared_rotation])
        resolved, _ = resolve_rotation_model(report['mechanics_input_template'], declared_rotation)
        _same(resolved, execution['rotation_model'])
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
