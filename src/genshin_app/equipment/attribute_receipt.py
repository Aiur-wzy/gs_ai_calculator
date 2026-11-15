"""Offline consistency of actual v3 attribute output, without engine replay.

The result-only entry validates saved output, not absent native input or source
authority. The full entry additionally binds native and compiled execution data.
"""
from __future__ import annotations

import json
import re

from .files import fingerprint, input_view

CONTRACT = 'attribute-execution-receipt-v1'
MODEL = 'attribute-ledger-v3'
MODELS = (MODEL, 'attribute-ledger-v4')
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MESSAGE = '属性账、原生面板或本次编译执行回执不一致，请回 App 重新计算。'
HASH_KEYS = {'input_fingerprint', 'output_fingerprint', 'source_fingerprint', 'execution_fingerprint',
             'model_fingerprint', 'template_fingerprint', 'rotation_fingerprint', 'matches_fingerprint',
             'ledger_fingerprint', 'sampling_fingerprint', 'hits_fingerprint'}
SCOPE = 'saved_attribute_execution_consistency_no_replay'
BINDING_KEYS = {'execution_fingerprint', 'model_fingerprint', 'template_fingerprint',
                'rotation_fingerprint', 'matches_fingerprint'}


def _declared(model):
    return isinstance(model, dict) and (model.get('attribute_model') or {}).get('contract') in MODELS


def _same(left, right):
    if fingerprint(left) != fingerprint(right):
        raise ValueError(MESSAGE)


def _bounded(value):
    size = 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
        size += len(chunk.encode('utf8'))
        if size > MAX_EVIDENCE_BYTES:
            from .schema import PlanError
            raise PlanError('属性执行证明超过发布预算；未将数值或来源判为错误。', 'attribute_evidence_limit',
                {'resource':'attribute_execution_evidence','actual_bytes':size,'limit':MAX_EVIDENCE_BYTES,
                 'recovery':{'action':'report_capacity_boundary','retry_same_request':False}})


def _projection(result):
    return {'ledger_fingerprint': fingerprint(result['attribute_ledger']),
            'sampling_fingerprint': fingerprint(result['sampling_trace']),
            'hits_fingerprint': fingerprint(result['hits'])}


def _execution(report, result):
    return {'template': report['mechanics_input_template'],
            'mechanics_model': report['mechanics_model'],
            'rotation_model': result['rotation_check']['resolved_rotation_model'],
            'matches': report.get('mechanics_matches', [])}


def _binding(execution):
    return {'execution_fingerprint': fingerprint(execution),
            **{name + '_fingerprint': fingerprint(execution[key]) for name, key in
               (('model', 'mechanics_model'), ('template', 'template'), ('rotation', 'rotation_model'), ('matches', 'matches'))}}


def attach_attribute_execution_binding(report, result):
    """Attach hashes of actual inputs before the service seals result_fingerprint."""
    if _declared(report.get('mechanics_model')):
        if (result.get('attribute_ledger') or {}).get('contract') not in MODELS:
            raise ValueError(MESSAGE)
        execution = _execution(report, result)
        _bounded(execution)
        result['attribute_ledger']['execution_binding'] = _binding(execution)


def make_attribute_receipt(plan, report, result):
    if not _declared(report.get('mechanics_model')):
        if _declared(plan.get('mechanics_model')) or (result.get('attribute_ledger') or {}).get('contract') in MODELS:
            raise ValueError(MESSAGE)
        return None
    execution = _execution(report, result)
    from .replay_numeric_projection import numeric_budget_hits
    _bounded([execution, result.get('attribute_ledger'), result.get('sampling_trace'), numeric_budget_hits(result.get('hits'))])
    binding = _binding(execution)
    _same(binding, result['attribute_ledger']['execution_binding'])
    proof = {'contract': CONTRACT, 'verification_scope': SCOPE,
             **{key: report[key] for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')},
             **binding,
             **_projection(result)}
    _same(proof['execution_fingerprint'], result['rotation_check']['input_fingerprint'])
    return proof


def _validate_result(result, receipt):
    ledger, proof = result.get('attribute_ledger'), receipt.get('attribute_ledger')
    if (not isinstance(ledger, dict) or ledger.get('contract') not in MODELS or not isinstance(proof, dict)
            or set(proof) != HASH_KEYS | {'contract', 'verification_scope'}
            or proof.get('contract') != CONTRACT or proof.get('verification_scope') != SCOPE
            or any(not isinstance(proof.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', proof[key]) for key in HASH_KEYS)):
        raise ValueError(MESSAGE)
    from .replay_numeric_projection import numeric_budget_hits
    _bounded([ledger, result.get('sampling_trace'), numeric_budget_hits(result.get('hits')), proof])
    if not isinstance(ledger.get('declarations'), dict) or ledger['declarations'].get('contract') != ledger['contract']:
        raise ValueError(MESSAGE)
    _same(ledger['execution_binding'], {key: proof[key] for key in BINDING_KEYS})
    for key, value in _projection(result).items():
        _same(proof[key], value)
    for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint'):
        _same(proof[key], receipt[key])
    _same(proof['execution_fingerprint'], result['rotation_check']['input_fingerprint'])
    _same(proof['rotation_fingerprint'], fingerprint(result['rotation_check']['resolved_rotation_model']))
    _same(receipt['result_fingerprint'], fingerprint(result))


def validate_attribute_result_receipt(result, receipt):
    try:
        if (result.get('attribute_ledger') or {}).get('contract') not in MODELS and 'attribute_ledger' not in receipt:
            return
        _validate_result(result, receipt)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_attribute_receipt(plan, result, report, receipt):
    try:
        if not (_declared(plan.get('mechanics_model')) or _declared(report.get('mechanics_model'))
                or (result.get('attribute_ledger') or {}).get('contract') in MODELS or 'attribute_ledger' in receipt):
            return
        _validate_result(result, receipt)
        if not _declared(plan.get('mechanics_model')) or not _declared(report.get('mechanics_model')):
            raise ValueError(MESSAGE)
        _same(receipt['input_fingerprint'], fingerprint(input_view(plan)))
        _same(input_view(plan), input_view(report['plan']))
        _same(receipt['output_fingerprint'], fingerprint(plan['resolved_template']))
        _same(receipt['output_fingerprint'], fingerprint(report['plan']['resolved_template']))
        _same(result['attribute_ledger']['declarations'], report['mechanics_model']['attribute_model'])
        _same(result['attribute_ledger']['source_adoption'], report['attribute_source_adoption'])
        from genshin_app.rotation_check import resolve_rotation_model
        resolved, _ = resolve_rotation_model(report['mechanics_input_template'],
            report.get('compiled_rotation_model', plan['rotation_model']))
        _same(resolved, result['rotation_check']['resolved_rotation_model'])
        _same(receipt['attribute_ledger'], make_attribute_receipt(plan, report, result))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
