"""Reconstruct declared extra reaction damage through the real execution owner."""
from __future__ import annotations

import copy
import math

from .files import fingerprint
from .verification_scope import verification_scope

CONTRACT = 'special-damage-echo-receipt-v1'
FIELD = 'special_damage_echoes'
MESSAGE = '额外特殊伤害与实际父事件、来源或独立重算不一致，请重新计算。'


def _same(left, right):
    if fingerprint(left) != fingerprint(right):
        raise ValueError(MESSAGE)


def _execution(result):
    return result.get('damage_report_execution', result.get('special_damage_echo_execution'))


def _proof(result):
    return {'contract': CONTRACT, 'execution_fingerprint': fingerprint(_execution(result)),
            'report_fingerprint': fingerprint(result[FIELD])}


def _summary(result, expected, duration):
    groups = {'by_skill': {}, 'by_character': {}}
    total = 0.
    for hit in result['hits']:
        value = hit['expected']
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(MESSAGE)
        total += value
        for field, identity in (('by_skill', hit['skill']), ('by_character', hit['character'])):
            groups[field][identity] = groups[field].get(identity, 0.) + value
    def number(actual, wanted):
        if (type(actual) not in (int, float) or not math.isfinite(actual)
                or not math.isclose(actual, wanted, rel_tol=1e-12, abs_tol=1e-8)):
            raise ValueError(MESSAGE)
    number(result['summary']['total_damage'], total)
    for field, group in groups.items():
        if result['summary'][field].keys() != group.keys():
            raise ValueError(MESSAGE)
        for identity, amount in group.items():
            number(result['summary'][field][identity], amount)
    if expected['summary']['dps'] is None:
        _same(result['summary']['dps'], None)
    else:
        number(result['summary']['dps'], total / duration)


def make_echo_receipt(plan, result, report, rotation):
    if not report.get('mechanics_model', {}).get(FIELD):
        return None
    if FIELD not in result or rotation is None or plan.get('plan_version') != 3:
        raise ValueError(MESSAGE)
    if 'damage_report_execution' not in result:
        result['special_damage_echo_execution'] = copy.deepcopy({
            'template': report['mechanics_input_template'], 'rotation_model': rotation,
            'mechanics_model': report['mechanics_model'], 'matches': report['mechanics_matches']})
    return _proof(result)


@verification_scope
def validate_echo_result(result, receipt, *, _session=None):
    context = _execution(result)
    needed = (FIELD in result or FIELD in receipt or 'special_damage_echo_execution' in result
              or isinstance(context, dict) and bool(context.get('mechanics_model', {}).get(FIELD))
              or any(row.get('type') == 'special_damage_echo'
                     for row in result.get('mechanics_trace', [])))
    if not needed:
        return
    try:
        _same(receipt[FIELD], _proof(result))
        _same(receipt['result_fingerprint'], fingerprint(result))
        if not context['mechanics_model'].get(FIELD):
            raise ValueError(MESSAGE)
        actual = _session.replay(context)
        expected = actual.get('result')
        if expected is None or actual.get('resolved_template') is None:
            raise ValueError(MESSAGE)
        _same(expected[FIELD], result[FIELD])
        _same(fingerprint(actual['resolved_template']), receipt['output_fingerprint'])
        # All native numbers, including independent crit ranking and consumed
        # pools, come from the replay. Approved fixed overrides retain their
        # existing separate proof; this feature does not accept result edits.
        from .calculation_overrides.computed_values import (
            validate_report_numeric_baselines,
        )
        validate_report_numeric_baselines(result, expected)
        from .damage_report_scope import scoped_hits
        _, saved = scoped_hits(result, require_complete=False)
        _, originals = scoped_hits(expected, require_complete=False)
        if saved.keys() != originals.keys():
            raise ValueError(MESSAGE)
        for identity, (hit, included) in saved.items():
            original, original_included = originals[identity]
            if included != original_included:
                raise ValueError(MESSAGE)
            marker = hit.get('calculation_override')
            # This one display reference is added by the separate, verified
            # explanation projection after execution; no semantic fields are
            # stripped from engine hits.
            ignored = {'explanation_ref'}
            if marker is not None:
                _same(marker.get('automatic_expected'), original.get('expected'))
                _same(marker.get('effective_expected'), hit.get('expected'))
                ignored |= {'expected', 'crit', 'calculation_override'}
            _same({key: value for key, value in hit.items() if key not in ignored},
                  {key: value for key, value in original.items() if key not in ignored})
        if 'damage_report_window' not in result:
            _summary(result, expected, actual['resolved_template']['loop_duration'])
        _same(expected.get('special_reactions'), result.get('special_reactions'))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_echo_calculation(plan, result, report, receipt, *, _session=None):
    validate_echo_result(result, receipt, _session=_session)
    declared = report.get('mechanics_model', {}).get(FIELD)
    if not declared:
        if FIELD in result or FIELD in receipt:
            raise ValueError(MESSAGE)
        return
    context = _execution(result)
    if not isinstance(context, dict):
        raise ValueError(MESSAGE)  # noqa: TRY004 - malformed saved evidence
    from .files import input_view
    _same(receipt['input_fingerprint'], fingerprint(input_view(plan)))
    _same(context['mechanics_model'], report['mechanics_model'])
    _same(context['template'], report['mechanics_input_template'])
    _same(context['matches'], report['mechanics_matches'])
    from genshin_app.rotation_check import resolve_rotation_model
    rotation, _ = resolve_rotation_model(context['template'], report.get('compiled_rotation_model', plan['rotation_model']))
    _same(context['rotation_model'], rotation)
