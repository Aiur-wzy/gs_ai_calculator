"""Custom probability outcomes for existing integer operations.

No character dispatch and no second ledger. Spending and grants keep their
original conservation equations; a waived spend is successful with zero debit.
"""
from __future__ import annotations

from fractions import Fraction

from genshin_engine.probabilistic_resource import ResourceTrial
from genshin_engine.probabilistic_resource_contract import validate_custom_outcome
from genshin_engine.probability_counts import exact_atoms, resource_change
from genshin_engine.rotation_schema import arr, obj
from genshin_engine.shared_pool_schema import COUNT, ID, LIMITS

RANDOM_EFFECT_SCHEMA = obj({
    'mode': {'enum': ['expected', 'custom'], 'default': 'expected'}, 'chance': {'type': 'number', 'minimum': 0, 'maximum': 1},
    'kind': {'enum': ['waive_spend', 'extra_grant']}, 'extra_amount': {**COUNT, 'minimum': 1},
    'outcomes': {**arr(obj({'run_id': ID, 'key': ID, 'success': {'type': 'boolean'}},
                          ['run_id', 'key', 'success'])), 'maxItems': LIMITS['receipts']},
}, ['chance', 'kind'])


def trial_spec(operation, pool):
    effect = operation['random_effect']
    amount = operation['amount']
    if type(amount) is not int:
        raise ValueError('概率资源必须明确每次整数数量，不支持all_available。')
    waive = effect['kind'] == 'waive_spend'
    if waive:
        if (operation['operation'] != 'spend' or operation.get('spend_policy') != 'all_or_nothing'
                or amount <= 0 or 'extra_amount' in effect):
            raise ValueError('waive_spend仅用于固定正数的all_or_nothing消费，不接受extra_amount。')
    elif operation['operation'] != 'grant' or 'extra_amount' not in effect:
        raise ValueError('extra_grant仅用于grant且须明确正数extra_amount。')
    grant = 0 if waive else amount
    extra = 0 if waive else effect['extra_amount']
    if grant + extra > LIMITS['count']:
        raise ValueError('基础与额外获得之和超过整数资源安全界。')
    capacity = LIMITS['count'] if pool['capacity'] == 'unbounded' else pool['capacity']
    return ResourceTrial(amount if waive else 0, grant, extra, waive, effect['chance'], capacity)


def validate_random_effect(operation, pool):
    effect = operation['random_effect']
    trial = trial_spec(operation, pool)
    if effect.get('mode', 'expected') == 'expected':
        if 'outcomes' in effect:
            raise ValueError('期望模式不接受outcomes；特定情景由Agent向用户确认后使用custom。')
        for value in (trial.cost * (1-trial.chance) if trial.success_waives_cost else trial.cost,
                      trial.base_grant + trial.success_grant * trial.chance):
            exact_atoms(value)
        return
    if 'outcomes' not in effect:
        raise ValueError('custom需要明确outcomes；默认期望模式无需指定结果。')
    keys = set(pool['initial']) if operation['targets']['mode'] == 'all_pool_keys' else set(operation['targets']['characters'])
    seen = set()
    for item in effect['outcomes']:
        identity = (item['run_id'], item['key'])
        if identity in seen or item['key'] not in keys:
            raise ValueError('概率指定结果的run_id/key重复或不属于操作目标。')
        seen.add(identity)
        validate_custom_outcome(trial, item['success'])


def resolve_operation(operation, pool, *, before, run_id, key, effective, inactive_reason=None):
    """Resolve one count-based operation, without sampling or branch replay."""
    trial = trial_spec(operation, pool)
    effect = operation['random_effect']
    mode = effect.get('mode', 'expected')
    enough = before >= trial.cost
    sampled = effective and enough
    outcome = None
    if sampled and mode == 'custom':
        entry = next((r for r in effect['outcomes'] if (r['run_id'], r['key']) == (run_id, key)), None)
        if entry is None:
            raise ValueError(f'自定义概率缺少本次结果：operation={operation["id"]}, run_id={run_id}, key={key}。请提供success，未自动掷骰。')
        outcome = entry['success']
    if effective:
        # Ineligible balance never rolls. The deterministic placeholder cannot
        # rescue it and is deliberately not reported as a sampled outcome.
        if enough:
            after = resource_change(before, trial, mode=mode,
                                    success_count=int(outcome) if mode == 'custom' else None)
        else:
            after = resource_change(before, trial, mode=mode,
                                    success_count=0 if mode == 'custom' else None)
    else:
        from genshin_engine.probability_counts import CountedChange, trigger_counts
        after = CountedChange(Fraction(before), Fraction(), Fraction(), Fraction(), False,
                              trigger_counts(0, trial.chance, mode=mode, successes=0 if mode=='custom' else None))
    if pool['capacity'] == 'unbounded' and after.overflow:
        raise ValueError('无玩法上限资源超出工程整数安全界，未截断概率收益。')
    spend = operation['operation'] == 'spend'
    requested = trial.cost if spend else (exact_atoms(after.created) if sampled else operation['amount'])
    accepted = exact_atoms(after.consumed if spend else after.balance-before)
    succeeded = bool(after.executed)
    reason = (inactive_reason if not effective else 'insufficient' if not enough else
              'waived' if spend and not accepted else 'spent' if spend else 'granted')
    fields = {'requested': requested, 'accepted': accepted, 'after': exact_atoms(after.balance),
              'succeeded': succeeded, 'reason': reason,
              'probability': {'contract': 'shared-resource-probability-v1', 'mode': mode,
                  'kind': effect['kind'], 'chance': effect['chance'], 'sampled': sampled, 'outcome': outcome,
                  'executed_units': trial.cost if spend and succeeded else int(succeeded),
                  'consumed_units': exact_atoms(after.consumed), 'created_units': exact_atoms(after.created),
                  'overflow_units': exact_atoms(after.overflow),
                  'interpretation': ('按期望次数计算；容量、阈值和后续连段采用该近似口径，不是全场随机模拟。'
                                     if mode == 'expected' else '指定触发情景，不是整场期望或实战保证。')}}
    if mode == 'expected':
        fields['probability'].update(eligible_count=after.counts.opportunities,
                                    expected_count=float(after.counts.successes),
                                    count_basis='eligible_times_probability')
    if spend:
        fields['unfulfilled'] = 0 if succeeded else requested
    else:
        fields['wasted'] = exact_atoms(max(0, after.created-accepted))
    return fields


def validate_probability_receipt(operation, pool, receipt):
    if 'random_effect' not in operation:
        if 'probability' in receipt:
            raise ValueError('无概率声明的资源操作不能附带概率回执。')
        return
    inactive = receipt['reason'] in ('cooldown', 'ineligible')
    expected = resolve_operation(operation, pool, before=receipt['before'], run_id=receipt['run_id'],
        key=receipt['key'], effective=receipt['qualified'] and not inactive,
        inactive_reason=receipt['reason'] if inactive else 'ineligible')
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError('概率资源回执与本次指定结果、真实成本或余额不一致。')
