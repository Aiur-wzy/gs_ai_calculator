"""Saved static-inclusion consistency; never offline database authentication."""
from __future__ import annotations

import copy
import re

from genshin_app.static_inclusion_reporting import has_static_inclusion_signal

from .files import fingerprint, input_view
from .static_inclusion import compile_static_inclusion
from .static_inclusion_sources import (
    MAX_CANDIDATES,
    finite_size,
    graph_view,
    panel_fingerprint,
)

CONTRACT = 'static-inclusion-materialized-receipt-v1'
ADOPTION = 'static-inclusion-adoption-v1'
SCOPE = 'saved_static_inclusion_consistency_no_requery'
MESSAGE = '静态已含来源、当前配装面板或保存回执不一致，请回 App 重新计算。'
HASH_KEYS = ('input_fingerprint', 'output_fingerprint', 'source_fingerprint',
             'records_fingerprint', 'adoptions_fingerprint', 'final_graph_fingerprint', 'calculation_fingerprint')


class StaticInclusionMismatch(ValueError):
    def __init__(self, actual, expected, path, phase):
        import json

        from .receipt_difference import bounded_difference
        self.reason = 'static_inclusion_receipt_mismatch'
        self.details = {'contract': CONTRACT, 'phase': phase, 'path': path,
            'actual_fingerprint': fingerprint(actual), 'expected_fingerprint': fingerprint(expected),
            **bounded_difference(actual, expected, path)}
        super().__init__(MESSAGE + ' ' + json.dumps(self.details, ensure_ascii=False, separators=(',', ':')))


def _same(a, b, path='/', *, phase='saved_consistency'):
    if fingerprint(a) != fingerprint(b):
        raise StaticInclusionMismatch(a, b, path, phase)


def _adoptions(records):
    return [{'binding_id': r['binding_id'], 'resolved_by': {'kind': 'static_inclusion', 'id': r['id'],
             'compilation_fingerprint': r['compilation_fingerprint']},
             'requirement_fingerprint': r['requirement_fingerprint'],
             'issue_fingerprints': r['issue_fingerprints']} for r in records]


def _calculation(result):
    return {k: v for k, v in result.items() if k not in ('static_inclusion_compilations', 'static_inclusion_adoption')}


def attach_static_inclusion_result(report, result):
    records = report.get('static_inclusion_compilations')
    if not records:
        return
    if any(r['status'] != 'ready' for r in records):
        raise ValueError(MESSAGE)
    result['static_inclusion_compilations'] = copy.deepcopy(records)
    result['static_inclusion_adoption'] = {'contract': ADOPTION,
        **{k: report[k] for k in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')},
        'adoptions': _adoptions(records), 'final_graph_fingerprint': records[0]['final_graph_fingerprint']}


def make_static_inclusion_receipt(plan, report, result):
    records = report.get('static_inclusion_compilations')
    if not records:
        return None
    return {'contract': CONTRACT, 'verification_scope': SCOPE,
            **{k: report[k] for k in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')},
            'records_fingerprint': fingerprint(records),
            'adoptions_fingerprint': fingerprint(result['static_inclusion_adoption']),
            'final_graph_fingerprint': records[0]['final_graph_fingerprint'],
            'calculation_fingerprint': fingerprint(_calculation(result))}


def _records_valid(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_CANDIDATES:
        raise ValueError(MESSAGE)
    finite_size(records, 24 * 1024 * 1024)
    if len({r['id'] for r in records}) != len(records) or len({r['binding_id'] for r in records}) != len(records):
        raise ValueError(MESSAGE)
    for index, record in enumerate(records):
        path = f'/result/static_inclusion_compilations/{index}'
        if (record['status'] != 'ready' or record['source_authority'] != 'fresh_database_admission'
                or record['issues'] or len(record['issue_fingerprints']) != 1):
            raise ValueError(MESSAGE)
        _same(record['compilation_fingerprint'], fingerprint({k: v for k, v in record.items() if k != 'compilation_fingerprint'}), path + '/compilation_fingerprint')
        identity = record['shape_certificate']
        shape = compile_static_inclusion(record['source_material'], **{key: identity[key] for key in
            ('profile_id', 'profile_version', 'profile_fingerprint')})
        _same(record['shape_certificate'], shape, path + '/shape_certificate', phase='recompile_shape')
        if shape['status'] != 'shape_ready':
            raise ValueError(MESSAGE)
        declaration = {**copy.deepcopy(shape['declaration']), 'basis_authority': 'user_panel_policy'}
        _same(record['declaration'], declaration, path + '/declaration')
        component = record['source_component']
        _same(component['material_fingerprint'], fingerprint(record['source_material']), path + '/source_component/material_fingerprint')
        _same(component['shape_fingerprint'], fingerprint(shape), path + '/source_component/shape_fingerprint')
        for key in ('id', 'binding_id', 'provider_character_id', 'owner', 'context_fingerprint', 'requirement_fingerprint'):
            _same(component[key], record[key], path + '/source_component/' + key)
        _same(component['panel_fingerprint'], declaration['panel_fingerprint'], path + '/source_component/panel_fingerprint')
        if (component['kind'] != 'static_inclusion_candidate' or component['authority'] != 'fresh_material_candidate_not_admitted'
                or component['shape_status'] != 'shape_ready'
                or component['profile_fingerprint'] != shape['profile_fingerprint']
                or record['provider_character_id'] != declaration['provider']):
            raise ValueError(MESSAGE)
        owner = {'kind': 'artifact_set', 'set_id': declaration['set_id'],
                 'required_count': declaration['required_count'], 'affix_id': declaration['affix_id']}
        _same(record['owner'], owner, path + '/owner')
        normal = record['source_material']['normalized']
        _same(record['source_display'], {'name': normal.get('name'), 'description': normal.get('desc_template'),
             'name_source': normal.get('name_source'), 'description_source': normal.get('desc_source')}, path + '/source_display')


def _validate_result(result, receipt):
    records = result.get('static_inclusion_compilations')
    proof = receipt.get('static_inclusions')
    marker = result.get('static_inclusion_adoption')
    _records_valid(records)
    if (not isinstance(proof, dict) or set(proof) != {'contract', 'verification_scope', *HASH_KEYS}
            or proof['contract'] != CONTRACT or proof['verification_scope'] != SCOPE
            or any(type(proof[k]) is not str or not re.fullmatch(r'[0-9a-f]{64}', proof[k]) for k in HASH_KEYS)):
        raise ValueError(MESSAGE)
    expected_marker = {'contract': ADOPTION,
        **{k: receipt[k] for k in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint')},
        'adoptions': _adoptions(records), 'final_graph_fingerprint': proof['final_graph_fingerprint']}
    _same(marker, expected_marker, '/result/static_inclusion_adoption')
    _same(proof['records_fingerprint'], fingerprint(records), '/result_receipt/static_inclusions/records_fingerprint')
    _same(proof['calculation_fingerprint'], fingerprint(_calculation(result)), '/result_receipt/static_inclusions/calculation_fingerprint', phase='sealed_calculation')
    _same(proof['adoptions_fingerprint'], fingerprint(marker), '/result_receipt/static_inclusions/adoptions_fingerprint')
    for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint'):
        _same(proof[key], receipt[key], '/result_receipt/static_inclusions/' + key)
    for record in records:
        _same(record['source_fingerprint'], receipt['source_fingerprint'], '/result/static_inclusion_compilations/' + record['id'] + '/source_fingerprint')
        _same(record['final_graph_fingerprint'], proof['final_graph_fingerprint'], '/result/static_inclusion_compilations/' + record['id'] + '/final_graph_fingerprint')
    _same(receipt['result_fingerprint'], fingerprint(result), '/result_receipt/result_fingerprint')


def validate_static_inclusion_result_receipt(result, receipt):
    try:
        if not has_static_inclusion_signal(result, receipt):
            return
        _validate_result(result, receipt)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_static_inclusion_receipt(plan, result, report, receipt):
    try:
        native = plan.get('resolution') or {}
        requirements = report.get('effect_requirements', [])
        adopted = [r for r in requirements if (r.get('resolved_by') or {}).get('kind') == 'static_inclusion']
        if not has_static_inclusion_signal(result, receipt, plan=plan, report=report):
            return
        _validate_result(result, receipt)
        records = result['static_inclusion_compilations']
        _same(report['static_inclusion_compilations'], records, '/report/static_inclusion_compilations')
        _same(native['static_inclusion_compilations'], records, '/plan/resolution/static_inclusion_compilations')
        _same(report['plan']['resolution']['static_inclusion_compilations'], records, '/report/plan/resolution/static_inclusion_compilations')
        _same(input_view(plan), input_view(report['plan']), '/plan/input_view')
        _same(receipt['input_fingerprint'], fingerprint(input_view(plan)), '/result_receipt/input_fingerprint')
        _same(receipt['output_fingerprint'], fingerprint(plan['resolved_template']), '/result_receipt/output_fingerprint')
        _same(receipt['output_fingerprint'], fingerprint(report['plan']['resolved_template']), '/report/plan/resolved_template_fingerprint')
        for key in ('input_fingerprint', 'output_fingerprint', 'source_fingerprint'):
            _same(report[key], receipt[key], '/report/' + key); _same(native[key], receipt[key], '/plan/resolution/' + key)
        template = report.get('mechanics_input_template', plan['resolved_template'])
        graph = graph_view(template, report.get('mechanics_model'), report.get('mechanics_matches', []))
        _same(receipt['static_inclusions']['final_graph_fingerprint'], fingerprint(graph), '/result_receipt/static_inclusions/final_graph_fingerprint', phase='effective_graph')
        _same(native['effect_requirements'], requirements, '/plan/resolution/effect_requirements')
        _same(report['plan']['resolution']['effect_requirements'], requirements, '/report/plan/resolution/effect_requirements')
        _same(sorted(r['binding_id'] for r in adopted), sorted(r['binding_id'] for r in records), '/adopted/binding_ids')
        for record in records:
            req = [r for r in adopted if r['binding_id'] == record['binding_id']]
            if len(req) != 1:
                raise ValueError(MESSAGE)
            path = '/result/static_inclusion_compilations/' + record['id']
            _same(record['requirement_fingerprint'], fingerprint({k: v for k, v in req[0].items() if k != 'resolved_by'}), path + '/requirement_fingerprint')
            _same(req[0]['resolved_by'], next(r for r in _adoptions(records) if r['binding_id'] == record['binding_id'])['resolved_by'], '/effect_requirements/' + record['binding_id'] + '/resolved_by')
            _same(record['declaration']['panel_fingerprint'], panel_fingerprint(plan, record['provider_character_id']), path + '/declaration/panel_fingerprint', phase='current_panel')
        _same(receipt['static_inclusions'], make_static_inclusion_receipt(plan, report, result), '/result_receipt/static_inclusions')
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
