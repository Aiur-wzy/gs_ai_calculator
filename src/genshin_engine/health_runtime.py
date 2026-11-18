"""Causal HP/Bond ledger. Caller owns queue, attributes and rule activation."""
from __future__ import annotations

import copy
import math
import operator

from genshin_engine.attribute_ledger import AttributeLedgerError

_COMPARE = {"lt": operator.lt, "le": operator.le, "eq": operator.eq,
            "ge": operator.ge, "gt": operator.gt}


def health_signature(states):
    return {cid:{key:value for key,value in state.items() if key not in ('revision','last_event_id')}
            for cid,state in states.items()}


def finite(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or positive and value == 0:
        raise AttributeLedgerError("health_value_invalid", f"{label}必须为有限{'正' if positive else '非负'}数", label)
    return value


class HealthRuntime:
    def __init__(self, declaration, template):
        self.declaration = copy.deepcopy(declaration)
        self.states, self.trace = {}, []
        chars = {c['id']: c for c in template['characters']}
        for row in declaration['characters']:
            cid = row['id']
            maximum = finite(chars[cid]['stats'].get('hp'), f'{cid}.max_hp', positive=True)
            self.states[cid] = {'max_hp': maximum, 'current_hp': finite(maximum*row['initial_hp_fraction'], f'{cid}.current_hp'),
                'bond': finite(maximum*row['initial_bond_fraction'], f'{cid}.bond'), 'hp_gained':0.0, 'hp_lost':0.0,
                'hp_change_fraction':0.0, 'overheal':0.0, 'bond_cleared':0.0,
                'last_event_id':None, 'revision':0}
        self.policies = {r['id']:r for r in declaration['characters']}

    def snapshot(self):
        return copy.deepcopy(self.states)

    def boundary(self):
        return health_signature(self.states)

    def sync_maximum(self, cid, maximum, *, event_id, time):
        maximum = finite(maximum, f'{cid}.max_hp', positive=True)
        before = self.states[cid]
        if maximum == before['max_hp']:
            return None
        state = copy.deepcopy(before)
        ratio = maximum / before['max_hp']
        p = self.policies[cid]
        state['current_hp'] = min(maximum, state['current_hp'] * ratio if p['max_hp_change_policy'] == 'preserve_fraction' else state['current_hp'])
        if p['bond_max_hp_policy'] == 'preserve_fraction':
            state['bond'] *= ratio
        state['max_hp'] = maximum
        return self._commit(cid, state, 'max_hp_sync', event_id, time, source=p['source'])

    def quantity(self, cid, quantity, states=None):
        state = (states if states is not None else self.states)[cid]
        if quantity == 'hp_fraction':
            return state['current_hp']/state['max_hp']
        if quantity == 'bond_fraction':
            return state['bond']/state['max_hp']
        return state[quantity]

    def expression(self, expr, owner, recipient, states=None):
        ids = list(self.states) if expr['subject'] == 'team' else [owner if expr['subject'] == 'owner' else recipient]
        raw = math.fsum(self.quantity(cid, expr['quantity'], states) for cid in ids)
        value = raw*expr['factor']+expr.get('add', 0)
        if not math.isfinite(value):
            raise AttributeLedgerError('health_value_invalid', '生命读数换算溢出', 'health_expression')
        return min(expr.get('max', math.inf), max(expr.get('min', -math.inf), value))

    def conditions(self):
        return {r['id']:_COMPARE[r['operator']](self.quantity(r['character'], r['quantity']), r['threshold'])
                for r in self.declaration['conditions']}

    def apply(self, event, amount, *, event_id, time):
        amount = finite(amount, 'health.amount')
        cid, operation = event['character'], event['operation']
        before = self.states[cid]
        state = copy.deepcopy(before)
        absorbed, overflow = 0.0, 0.0
        if operation == 'heal':
            if before['current_hp'] > 0:
                if not event.get('bypass_bond', False):
                    absorbed = min(state['bond'], amount)
                    state['bond'] -= absorbed
                    state['bond_cleared'] += absorbed
                if event.get('allow_hp_restore', True):
                    restored = min(amount-absorbed, state['max_hp']-state['current_hp'])
                    state['current_hp'] += restored
                    overflow = amount-absorbed-restored
                    state['overheal'] += overflow
        elif operation in ('hp_loss','hp_damage'):
            floor = min(before['current_hp'], event.get('remaining_hp_floor', 0))
            state['current_hp'] = max(floor, state['current_hp']-amount)
        elif operation == 'bond_grant':
            state['bond'] += amount
        elif operation == 'bond_reduce':
            absorbed = min(state['bond'], amount)
            state['bond'] -= absorbed
            state['bond_cleared'] += absorbed
        elif operation == 'reset_counters':
            for key in ('hp_gained','hp_lost','hp_change_fraction','overheal','bond_cleared'):
                state[key] = 0.0
        else:
            raise AttributeLedgerError('health_operation_invalid', '未知生命操作', operation)
        delta = state['current_hp']-before['current_hp']
        state['hp_gained'] += max(0, delta)
        state['hp_lost'] += max(0, -delta)
        state['hp_change_fraction'] += abs(delta)/before['max_hp']
        return self._commit(cid, state, operation, event_id, time, source=event['source'],
                            requested_amount=amount, actual_hp_delta=delta, bond_absorbed=absorbed, overheal=overflow)

    def _commit(self, cid, state, operation, event_id, time, **details):
        for key, value in state.items():
            if key not in ('last_event_id',):
                finite(value, f'{cid}.{key}')
        before = copy.deepcopy(self.states[cid])
        state['revision'] += 1
        state['last_event_id'] = event_id
        receipt = {'type':'health_change', 'character':cid, 'operation':operation,
                   'event_id':event_id, 'time_tick':time, 't':time/1_000_000,
                   'before':before, 'after':copy.deepcopy(state), **details}
        self.states[cid] = state
        self.trace.append(receipt)
        return receipt
