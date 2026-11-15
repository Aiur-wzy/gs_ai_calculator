"""Contact scopes and saved inputs checked with the existing D20 executor."""
from __future__ import annotations

import copy

from genshin_app.equipment.verification_scope import verification_scope

from .files import fingerprint
from .mechanism_execution_scope import need


def verify_contact_parent_receipt(context, execution, result, receipt):
    """A contact mechanism cannot stand in for its separate dispatch proof."""
    elemental = result.get('elemental') or {}
    sources = {'hit_parameter_compilations': result.get('hit_parameter_sources', []),
               'elemental_compilations': elemental.get('source_compilations', []),
               'reaction_damage_compilations': elemental.get('reaction_damage_source_compilations', [])}
    for key in ('attack_metadata_compilations', 'periodic_attack_compilations',
                'energy_generation_compilations', 'limited_hit_benefit_compilations', 'mechanism_compilations'):
        if result.get(key):
            sources[key] = result[key]
    expected = {'compiler': 'calibrated-action-events-v1', 'dispatch_mode': 'calibrated',
        'skill_coverage': 'partial', 'resource_coverage': 'not_generated',
        'compilations_fingerprint': fingerprint(context['action_event_compilations']),
        'compiled_mechanics_model_fingerprint': fingerprint(execution['model']),
        'execution_rotation_model_fingerprint': fingerprint(execution['rotation_model']),
        'mechanics_input_template_fingerprint': fingerprint(execution['template']),
        'source_compilations_fingerprint': fingerprint(sources)}
    need(bool(context['action_event_compilations']) and receipt.get('action_events') == expected,
         '实际命中的完整动作派发凭证或来源集合缺失、不一致')


def verify_contact_execution_scope(executed, records):
    restricted = {r['actor'] for r in records if
        r.get('projection', {}).get('execution_qualification', {}).get('scenario_requirements', {}).get(
            'no_provider_transformative_damage') is True}
    for event in executed.get('elemental_trace', []):
        if event.get('type') == 'reaction_damage_attempt' and event.get('character') in restricted:
            need(event.get('allowed') is False,
                 '提供者产生了当前命中触发来源尚未覆盖的转化反应伤害；本轴机制范围待补')


def _matches(plan, model):
    """Reconstruct declared selections; this does not simulate timing or stacks."""
    hits = {r['hit_id'] for r in model['hit_events']}
    for pattern in model['hit_patterns']:
        hits.update(f"{pattern['id']}:{i + 1}" for i in range(len(pattern['offsets'])))
    rules = {r['id']: r for r in model['rules']}
    fixed = {r['id']: r for r in model.get('fixed_effect_sources', [])}
    groups = [[], []]
    for match in plan.get('rotation_tuning', {}).get('buff_hit_matches', []):
        ref = match['effect_ref']
        is_fixed = 'fixed_source_id' in ref
        key = 'fixed_source_id' if is_fixed else 'rule_id'
        rid = ref['fixed_source_id'] if is_fixed else ref['buff_id']
        sources = fixed if is_fixed else rules
        if not is_fixed and rid not in sources:
            continue  # Template buff selections are independently reconstructed.
        need(rid in sources and match['state'] == 'confirmed'
             and any(e['id'] == ref['effect_id'] for e in sources[rid]['effects']),
             '保存的收益微调没有有效的来源或收益')
        requested = set(match.get('hit_ids', []) + match.get('include_hit_ids', []) + match.get('exclude_hit_ids', []))
        need(requested <= hits, '保存的收益微调引用不存在的实际命中')
        include, exclude = set(match.get('include_hit_ids', [])), set(match.get('exclude_hit_ids', []))
        if match['mode'] == 'replace':
            include = set(match['hit_ids'])
            exclude = hits - include
        for operation, ids in (('include', include), ('exclude', exclude)):
            groups[0 if is_fixed else 1].extend({key: rid, 'effect_id': ref['effect_id'],
                'hit_id': hid, 'operation': operation} for hid in sorted(ids))
    return groups[0] + groups[1]


@verification_scope
def verify_contact_execution(context, rebuilt_model, execution, result, receipt, records, *, _session=None):

    from .mechanism_cast_receipt import verify_cast_execution
    from .snapshot_capture_receipt import _ledger

    verify_contact_parent_receipt(context, execution, result, receipt)
    model = copy.deepcopy(rebuilt_model)
    excluded = {p['rule']['fixed_source_id'] for b in context['plan'].get('effect_bindings', []) for p in b['parts']
                if p['state'] == 'excluded' and (p.get('rule') or {}).get('kind') == 'fixed_effect_source'}
    if 'fixed_effect_sources' in model:
        model['fixed_effect_sources'] = [r for r in model['fixed_effect_sources'] if r['id'] not in excluded]
    verify_cast_execution(context, model, execution, result)
    need(execution['matches'] == _matches(context['plan'], execution['model']), '保存的收益关联与本轴声明不一致')
    need(execution['rotation_model'] == result['rotation_check']['resolved_rotation_model'], '实际执行轴不一致')
    inputs = {'template': execution['template'], 'rotation_model': execution['rotation_model'],
              'mechanics_model': execution['model'], 'matches': execution['matches']}
    need(fingerprint(inputs) == result['rotation_check']['input_fingerprint'], '实际求值输入与动作检查不一致')
    # Same queue, bounded by the enclosing 8 MiB receipt check. In particular,
    # no second implementation of refresh/CD/frontline/snapshot rules lives here.
    replay = _session.replay(inputs)
    expected = replay.get('result')
    need(expected is not None and replay.get('resolved_template') is not None, '保存的命中执行输入无法重建')
    verify_contact_execution_scope(replay, records)
    need(fingerprint(replay['resolved_template']) == receipt['output_fingerprint'], '保存的实际输出模板不一致')
    if not expected.get('mechanics', {}).get('complete_first_cycle', False):
        expected['summary']['dps'] = None
    expected.update(rotation_check=replay['rotation_check'], mechanics_trace=replay['mechanics_trace'],
                    sampling_trace=replay.get('sampling_trace', []), mechanics_status=replay['status'],
                    mechanics_issues=replay.get('issues', []))
    from .replay_numeric_projection import replay_numeric_ledgers
    expected_ledger, actual_ledger = replay_numeric_ledgers(_ledger(expected), _ledger(result), result, receipt)
    need(fingerprint(expected_ledger) == fingerprint(actual_ledger), '命中触发的实际层数、时点或伤害流水不一致')
    for key in ('damage_reporting', 'fixed_effect_sources', 'target_condition_trace'):
        need(expected.get(key) == result.get(key), '命中相关投影或报表范围不一致')
    for key, value in expected.get('elemental', {}).items():
        need(value == result.get('elemental', {}).get(key), '实际反应、元素底或附着流水不一致')
