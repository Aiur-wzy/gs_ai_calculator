"""Bind a cast receipt to its real executor inputs without a dummy hit receipt."""
from __future__ import annotations

import copy
import math

from genshin_app.rotation_check import resolve_rotation_model
from genshin_engine.registry import effect_matches_hit

from .files import fingerprint
from .mechanism_execution_scope import need

ACTION_ADMISSION_CONTRACT = 'action-admission-execution-v1'
ACTION_ADMISSION_PROFILE = 'successful_action_v2'


def _v2_records(records):
    return [record for record in records
            if (record.get('projection') or {}).get('execution_qualification', {}).get('parent', {}).get(
                'profile_kind') == ACTION_ADMISSION_PROFILE]


def _integer(value, message):
    need(type(value) is int and value >= 0, message)
    return value


def _number(value, message):
    need(type(value) in (int, float) and math.isfinite(value), message)
    return value


def verify_action_admission_execution(result, execution, records):
    """Offline-bind v2 activations to committed core admission receipts."""
    selected = _v2_records(records)
    if not selected:
        return
    from genshin_engine import action_admission_audit as audit

    check = result['rotation_check']
    summary = check.get('action_admission_execution')
    required = {'contract', 'count_scope', 'planned_actions', 'reported_actions',
        'unreported_actions', 'committed_actions', 'rejected_actions',
        'not_committed_actions', 'not_verified_actions', 'receipts', 'receipt_count',
        'receipts_fingerprint', 'receipt_bytes', 'limits'}
    need(type(summary) is dict and required <= set(summary)
         and not set(summary) - required - {'termination'}, '动作接纳执行总表缺失或字段不受支持')
    need(summary['contract'] == ACTION_ADMISSION_CONTRACT
         and summary['count_scope'] == 'reported_action_rows'
         and summary['limits'] == audit.capabilities()['limits'], '动作接纳执行合同或预算不一致')
    for key in ('planned_actions', 'reported_actions', 'unreported_actions',
                'committed_actions', 'rejected_actions',
                'not_committed_actions', 'not_verified_actions', 'receipt_count', 'receipt_bytes'):
        _integer(summary[key], '动作接纳执行计数必须为非负整数')
    receipts = summary['receipts']
    need(type(receipts) is list and len(receipts) <= audit.MAX_ACTION_ADMISSION_RECEIPTS
         and summary['receipt_count'] == len(receipts) == summary['committed_actions']
         and summary['receipts_fingerprint'] == audit.fingerprint(receipts)
         and summary['receipt_bytes'] == len(audit.canonical(receipts))
         and summary['receipt_bytes'] <= audit.MAX_ACTION_ADMISSION_RECEIPT_BYTES,
         '动作接纳回执数量或指纹不一致')
    parents = [row for row in check.get('event_log', [])
               if row.get('type') == 'action' and 'action_admission' in row]
    counts = {status: sum(row['action_admission'].get('status') == status for row in parents)
              for status in ('committed', 'rejected', 'not_committed', 'not_verified')}
    need(summary['reported_actions'] == len(parents) == sum(counts.values())
         and summary['planned_actions'] >= summary['reported_actions']
         and summary['unreported_actions'] == summary['planned_actions'] - summary['reported_actions']
         and all(summary[status + '_actions'] == count for status, count in counts.items()),
         '动作接纳父动作计数与总表不一致')

    rotation = check['resolved_rotation_model']
    abilities = {ability['id']: ability for ability in rotation['abilities']}
    actions = {action['id']: action for action in rotation['actions']}
    parent_by_run = {}
    for parent in parents:
        admission = parent['action_admission']
        status = admission.get('status')
        need(status in counts and admission.get('contract') == ACTION_ADMISSION_CONTRACT,
             '动作接纳父动作状态或合同不受支持')
        if status == 'committed':
            need(set(admission) == {'contract', 'binding_id', 'source_fingerprint',
                 'static_certificate_fingerprint', 'status', 'certificate'},
                 '已提交动作接纳父行字段不完整')
        else:
            need('certificate' not in admission, '未提交动作不能携带成功证书')
        need(parent.get('run_id') not in parent_by_run, '动作接纳父动作run_id重复')
        parent_by_run[parent.get('run_id')] = parent

    receipt_by_origin = {}
    receipt_keys = {'id', 'run_id', 'contract', 'phase', 'parent_run_id', 'binding_id', 'model_ref',
        'source_fingerprint', 'static_certificate_fingerprint', 'actor', 'ability_id', 'action_id',
        'action_type_id', 'variant', 'cycle', 'source_cycle', 't', 'gates', 'energy_before',
        'energy_after', 'energy_required', 'energy_amount', 'charges_before', 'charges_after',
        'next_ready_at', 'cooldown_group', 'frontline_before', 'frontline_after', 'status'}
    for receipt in receipts:
        need(type(receipt) is dict and set(receipt) == receipt_keys
             and receipt['contract'] == ACTION_ADMISSION_CONTRACT
             and receipt['phase'] == 'action_admission' and receipt['status'] == 'committed'
             and receipt['gates'] == {'frontline': 'passed', 'occupation': 'passed',
                                      'energy': 'passed', 'cooldown': 'passed'},
             '动作接纳成功回执结构或门禁结果不一致')
        action = actions.get(receipt['action_id'])
        ability = abilities.get(receipt['ability_id'])
        marker = (ability or {}).get('action_admission_source')
        parent = parent_by_run.get(receipt['parent_run_id'])
        expected_id = audit.certificate_id(receipt['action_id'])
        reference = {'contract': ACTION_ADMISSION_CONTRACT, 'certificate_id': receipt['id'],
                     'run_id': receipt['run_id']}
        need(action is not None and action['ability_id'] == receipt['ability_id'] and ability is not None
             and type(marker) is dict and parent is not None
             and receipt['id'] == expected_id
             and receipt['run_id'] == f"{receipt['source_cycle']}:{expected_id}"
             and parent.get('action_id') == receipt['action_id']
             and parent['action_admission']['certificate'] == reference
             and parent['action_admission']['status'] == 'committed',
             '动作接纳成功回执没有唯一对应的已提交父动作')
        need(receipt['binding_id'] == marker.get('binding_id') == parent['action_admission']['binding_id']
             and receipt['model_ref'] == marker.get('model_ref')
             and receipt['source_fingerprint'] == marker.get('source_fingerprint') == parent['action_admission']['source_fingerprint']
             and receipt['static_certificate_fingerprint'] == marker.get('static_certificate_fingerprint')
             == parent['action_admission']['static_certificate_fingerprint'],
             '动作接纳成功回执与静态来源标记不一致')
        need(receipt['actor'] == ability['character'] == parent.get('character')
             and receipt['action_type_id'] == ability['action_type_id']
             and receipt['variant'] == ability.get('variant')
             and receipt['cycle'] == parent.get('cycle')
             and receipt['source_cycle'] == parent.get('source_cycle')
             and _number(receipt['t'], '动作接纳时点无效') == parent.get('t')
             and receipt['frontline_before'] == parent.get('frontline')
             and receipt['frontline_after'] == parent.get('frontline_after'),
             '动作接纳成功回执与实际父动作身份或状态不一致')
        energy = ability.get('energy') or {}
        cooldown = ability.get('cooldown') or {}
        for key in ('energy_before', 'energy_after', 'energy_required', 'energy_amount',
                    'charges_before', 'charges_after', 'next_ready_at'):
            _number(receipt[key], '动作接纳资源数值无效')
        need(receipt['energy_required'] == energy.get('required')
             and receipt['energy_amount'] == energy.get('amount')
             and math.isclose(receipt['energy_after'], max(0, receipt['energy_before'] - receipt['energy_amount']),
                              rel_tol=1e-12, abs_tol=1e-8)
             and receipt['charges_after'] == receipt['charges_before'] - 1
             and receipt['cooldown_group'] == cooldown.get('group'),
             '动作接纳成功回执的能量或冷却转换不一致')
        key = (receipt['source_cycle'], receipt['action_id'])
        need(key not in receipt_by_origin, '同一动作来源轮次存在重复动作接纳成功回执')
        receipt_by_origin[key] = reference

    committed_refs = [row['action_admission']['certificate'] for row in parents
                      if row['action_admission']['status'] == 'committed']
    need(sorted(committed_refs, key=audit.fingerprint)
         == sorted(({'contract': ACTION_ADMISSION_CONTRACT, 'certificate_id': row['id'],
                     'run_id': row['run_id']} for row in receipts), key=audit.fingerprint),
         '已提交动作与动作接纳成功回执不是一一对应')

    v2_rule_ids = {rule['id'] for record in selected for rule in record['rules']}
    expected_actions = {parent['action_id'] for record in selected for parent in record['parent_graph']['parents']}
    for rule in execution['model']['rules']:
        if rule['id'] in v2_rule_ids:
            need(rule['trigger'].get('action_admission') == {'contract': ACTION_ADMISSION_CONTRACT}
                 and set(rule['trigger'].get('action_ids', [])) <= expected_actions,
                 'successful_action_v2规则没有保留动作接纳筛选')
    for trace in result.get('mechanics_trace', []):
        if trace.get('type') != 'activation' or trace.get('rule_id') not in v2_rule_ids:
            continue
        reference = receipt_by_origin.get((trace.get('cycle'), trace.get('event_id')))
        need(reference is not None and trace.get('action_admission_certificate') == reference,
             'successful_action_v2激活没有引用可核对的已提交动作接纳回执')


def verify_cast_execution(context, rebuilt_model, execution, result, *, records=()):
    plan = context['plan']
    expected = copy.deepcopy(context['template'])
    hits = {r['id']: r for r in expected['hits']}
    buffs = {(b['id'], e['id']): e for b in expected.get('buffs', []) for e in b['effects']}
    originals = copy.deepcopy(buffs)
    # The only post-mechanism template transform is the already declared
    # rotation-local hit selection. Reconstruct it, never bless arbitrary edits.
    for match in plan.get('rotation_tuning', {}).get('buff_hit_matches', []):
        ref = match['effect_ref']
        key = (ref.get('buff_id'), ref.get('effect_id'))
        if key not in buffs:
            continue  # Mechanic and fixed-source matches do not mutate template buffs.
        need(match['state'] == 'confirmed', '保存的轴关联未确认')
        original = originals[key]
        if match['mode'] == 'replace':
            final = set(match['hit_ids'])
        else:
            need(original.get('applies_to') is not None and all(h.get('damage_type_ids') is not None for h in hits.values()),
                 '保存的轴关联基础范围未明确')
            base = {hid for hid, hit in hits.items() if effect_matches_hit(original, hit)}
            final = (base | set(match['include_hit_ids'])) - set(match['exclude_hit_ids'])
        need(final <= hits.keys(), '保存的轴关联命中不存在')
        effect = buffs[key]
        effect['applies_to'] = {'mode': 'hits', 'hit_ids': sorted(final)} if final else {'mode': 'none'}
        if not final:
            effect.setdefault('extensions', {})['exclusion_reason'] = match['reason']
    actual = copy.deepcopy(execution['template'])
    expected.pop('meta', None)
    actual.pop('meta', None)
    need(fingerprint(expected) == fingerprint(actual), '施放资格模板与实际求值模板不一致')
    rotation, _ = resolve_rotation_model(execution['template'], plan['rotation_model'])
    need(fingerprint(rotation) == fingerprint(result['rotation_check']['resolved_rotation_model']),
         '施放资格动作轴与实际执行轴不一致')
    model = copy.deepcopy(rebuilt_model)
    excluded = {p['rule']['rule_id'] for b in plan.get('effect_bindings', []) for p in b['parts']
                if p['state'] == 'excluded' and (p.get('rule') or {}).get('kind') == 'mechanics'}
    model['rules'] = [r for r in model['rules'] if r['id'] not in excluded]
    model['initial_state']['rules'] = [r for r in model['initial_state']['rules'] if r['rule_id'] not in excluded]
    if 'resource_ownership' in model:
        model['resource_ownership'] = [r for r in model['resource_ownership'] if r['rule_id'] not in excluded]
    need(fingerprint(model) == fingerprint(execution['model']), '施放资格事件或规则与实际执行模型不一致')
    verify_action_admission_execution(result, execution, records)
