"""Health adapter into the existing mechanics clock, attributes and rules."""
from __future__ import annotations

import copy
import math

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.health_runtime import finite


def validate_health_declarations(model):
    from genshin_engine.mechanics_validation import _tick
    from genshin_engine.registry import BENEFIT_TYPES
    from genshin_engine.rotation_port import rotation_issue
    issues = []
    health = model.get('health_model')
    expressions = [(rule, effect) for rule in model['rules'] for effect in rule['effects']
                   if isinstance(effect['value'], dict) and effect['value'].get('kind') == 'health']
    if health is None:
        if expressions or any(r['trigger'].get('event') == 'health_change' for r in model['rules']):
            issues.append(rotation_issue('health_model_missing', '生命表达式及触发需要health_model。', 'health_model'))
        return issues
    for group in ('characters', 'conditions', 'events'):
        for row in health[group]:
            if row['status'] == 'pending':
                issues.append(rotation_issue('health_source_pending', '生命输入来源尚未确定。', row['id'], severity='pending'))
    for row in health['events']:
        try:
            _tick(row['t'])
        except ValueError as exc:
            issues.append(rotation_issue('time_precision', str(exc), row['id']))
        repeat = row['repeat']
        cycles = repeat.get('cycles', [])
        if (repeat['mode'] == 'cycles' and (not cycles or len(cycles) != len(set(cycles)))) or (repeat['mode'] != 'cycles' and 'cycles' in repeat):
            issues.append(rotation_issue('repeat', '生命事件的重复轮次必须非空且不重复；其他模式不填写cycles。', row['id']))
    for rule, effect in expressions:
        info = BENEFIT_TYPES.get(effect['benefit_type_id'], {})
        if (info.get('stat') == 'hp' and info.get('zone') in ('stat_add', 'stat_pct')
                and effect['value']['sample'] == 'current'
                and effect['value']['quantity'] in ('current_hp', 'max_hp', 'hp_fraction', 'bond', 'bond_fraction')):
            issues.append(rotation_issue('health_feedback_ambiguous',
                '当前生命依赖最大生命，不能同时用当前生命反向定义最大生命；请指定trigger或event取样断开反馈。', rule['id']))
    return issues


def read_health_expression(runtime, expr, owner, recipient, *, instance=None, snapshot=None, event=None):
    health = runtime.health_runtime
    if health is None:
        raise AttributeLedgerError('health_model_missing', '生命读数需要health_model', 'health_model')
    sample = snapshot
    if expr['sample'] == 'trigger':
        sample = (instance or {}).get('trigger_snapshot')
    elif expr['sample'] == 'event':
        context = event if event is not None else getattr(runtime, 'health_event_context', {})
        sample = runtime.snapshots.get((context.get('origin', 0), expr['event_id']))
    states = sample.get('health') if sample else None
    if expr['sample'] != 'current' and states is None:
        raise AttributeLedgerError('health_frame_missing', '生命取样事件尚未执行，不能改用当前生命', 'health_model', status='pending')
    return health.expression(expr, owner, recipient, states)


def current_stat(runtime, cid, stat):
    if runtime.attribute_runtime and runtime.attribute_runtime.supports_read(stat):
        return runtime.attribute_runtime.read(cid, 'total', runtime.snapshot(), stat=stat)['value']
    from genshin_engine.attribute_stats import STATS
    return runtime.ordinary_panel(cid, None, defer_stats=tuple(s for s in STATS if s != stat))[0].get(stat)


def sync_health(runtime, event):
    health = runtime.health_runtime
    if health is None:
        return
    # Freeze all reads before committing resizes: no party iteration-order effect.
    maxima = {cid:current_stat(runtime, cid, 'hp') for cid in health.states}
    before = health.snapshot()
    trace_mark, runtime_mark = len(health.trace), len(runtime.trace)
    changed = False
    try:
        for cid, value in maxima.items():
            receipt = health.sync_maximum(cid, value, event_id=event['run_id'], time=runtime.time)
            if receipt:
                changed = True
                runtime.trace.append(copy.deepcopy(receipt))
        if changed:
            if runtime.attribute_runtime:
                runtime.attribute_runtime.stage(event, 'pre')
            # Detect an indirect health -> other attribute -> max HP feedback.
            # No iterative solver and no silently reused previous-event answer.
            for cid, value in maxima.items():
                actual = current_stat(runtime, cid, 'hp')
                if not math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-9):
                    raise AttributeLedgerError('health_feedback_ambiguous',
                        '生命上限同步后反向改变了自身属性依赖；请对生命输入指定trigger/event取样。', cid)
    except Exception:
        health.states = before
        del health.trace[trace_mark:]
        del runtime.trace[runtime_mark:]
        raise
    runtime.conditions.update(health.conditions())


def handle_health_event(runtime, ev, row, payload):
    health = runtime.health_runtime
    if health is None:
        raise AttributeLedgerError('health_model_missing', '生命事件没有所属生命模型', ev['id'])
    cid = payload['character']
    event = {'id':ev['id'], 'run_id':ev['run_id'], 'origin':ev['origin'],
             'event':'health_change', 'character':cid, 'action_id':payload.get('action_id'),
             'health_operation':payload['operation'], 'conditions':dict(runtime.conditions)}
    runtime.phase(event, 'pre', ev)
    sync_health(runtime, ev)
    basis = payload['amount']['basis']
    raw = (1 if basis == 'flat' else health.quantity(cid, basis) if basis in ('current_hp','max_hp')
           else current_stat(runtime, payload.get('source_character', cid), basis.removeprefix('source_')))
    amount = finite(raw, 'health.basis')*payload['amount']['factor']+payload['amount'].get('add', 0)
    if payload['operation'] == 'heal':
        amount *= payload.get('healing_multiplier', 1)
    runtime.count_event('health_change')
    receipt = health.apply(payload, amount, event_id=ev['run_id'], time=runtime.time)
    receipt['cycle'] = ev['origin']
    runtime.trace.append(copy.deepcopy(receipt))
    runtime.conditions.update(health.conditions())
    if health.states[cid]['current_hp'] == 0:
        raise AttributeLedgerError('health_depleted',
            '当前生命已归零；死亡后的离场、召唤物和复苏尚未建模，不能继续按存活角色计算。', cid, status='pending')
    event.update(conditions=dict(runtime.conditions), health_receipt=copy.deepcopy(receipt))
    runtime.phase(event, 'post', ev)
    # A health-triggered max-HP buff takes effect after the heal/loss that
    # triggered it, and before the next queued hit. Resizing is not healing.
    sync_health(runtime, ev)
    if ev['id'] in runtime.snapshot_ids:
        runtime.snapshots[(ev['origin'], ev['id'])] = runtime.observed_snapshot(event)
    row.update(status='executed', health=copy.deepcopy(receipt))
    return runtime.queue


def validate_health_bindings(model, template, rotation):
    health = model.get('health_model')
    if health is None:
        return []
    issues = []
    from genshin_engine.rotation_port import rotation_issue
    def issue(message, path, **details):
        issues.append(rotation_issue('health_binding_invalid', message, path, **details))
    chars = {c['id']:c for c in template['characters']}
    ids = [r['id'] for r in health['characters']]
    if len(ids) != len(set(ids)) or not ids or not set(ids) <= chars.keys():
        issue('生命模型的角色必须属于队伍且不能重复。', 'health_model.characters')
    for r in health['characters']:
        if r['initial_hp_fraction'] == 0:
            issues.append(rotation_issue('health_depleted',
                '初始生命为零；请核实初始状态，当前接口不执行死亡后继续作战或复苏。', r['id'], severity='pending'))
        if r['id'] in chars:
            try:
                finite(chars[r['id']]['stats'].get('hp'), r['id']+'.hp', positive=True)
            except AttributeLedgerError:
                issue('生命模型角色必须提供正的最大生命面板。', 'health_model.characters', character=r['id'])
    conditions = [r['id'] for r in health['conditions']]
    explicit = {r['condition'] for r in model['condition_events']}
    if len(set(conditions)) != len(conditions) or set(conditions) & explicit:
        issue('自动生命条件不可重复或被手工条件覆盖。', 'health_model.conditions')
    for r in health['conditions']:
        if r['character'] not in ids:
            issue('生命条件角色未登记。', 'health_model.conditions', condition=r['id'])
    actions = {r['id']:r for r in rotation['actions']}
    for r in health['events']:
        if r['character'] not in ids or r.get('source_character', r['character']) not in chars:
            issue('生命事件的受益者或来源角色未登记。', r['id'])
        if 'action_id' in r and r['action_id'] not in actions:
            issue('生命事件的来源动作不存在。', r['id'])
        if 'action_id' in r and r['action_id'] in actions:
            a = actions[r['action_id']]
            if (r['t'],r['order']) <= (a['start'],a['order']) or r['repeat'] != a['repeat']:
                issue('生命事件须晚于来源动作，并使用同一重复范围。', r['id'])
        if r['operation'] != 'heal' and any(k in r for k in ('healing_multiplier','bypass_bond','allow_hp_restore')):
            issue('只有治疗事件可声明治疗修正。', r['id'])
        if r['operation'] not in ('hp_loss','hp_damage') and 'remaining_hp_floor' in r:
            issue('只有耗血或扣血事件可声明最低剩余生命。', r['id'])
        if r['operation'] == 'reset_counters' and r['amount'] != {'basis':'flat','factor':0}:
            issue('清空累计不携带数值变化；amount须为flat、factor=0。', r['id'])
    for rule in model['rules']:
        for effect in rule['effects']:
            expr = effect['value']
            if isinstance(expr, dict) and expr.get('kind') == 'health':
                if expr['subject'] == 'owner' and rule['owner'] not in ids:
                    issue('生命表达式的提供者未登记。', rule['id'])
                if expr['subject'] == 'recipient' and not set(chars) <= set(ids):
                    issue('受益者生命表达式需要登记全部可能的队伍角色。', rule['id'])
    return issues
