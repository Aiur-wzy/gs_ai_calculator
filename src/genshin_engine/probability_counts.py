"""Deterministic expected/custom trigger counts; no random paths or sampling.

Owners supply eligible opportunities after their ordinary condition/cooldown
checks. Counts do not certify the probability of a legal full rotation.
"""
from dataclasses import dataclass
from fractions import Fraction

from genshin_engine.probabilistic_resource import probability


@dataclass(frozen=True, slots=True)
class TriggerCounts:
    opportunities: int
    successes: Fraction
    mode: str

    @property
    def failures(self):
        return self.opportunities - self.successes


def trigger_counts(opportunities, chance, *, mode='expected', successes=None):
    if type(opportunities) is not int or opportunities < 0:
        raise ValueError('符合条件的触发次数必须为非负整数。')
    chance = probability(chance)
    if mode == 'expected':
        if successes is not None:
            raise ValueError('期望次数不能同时指定触发次数；指定情景请使用custom。')
        return TriggerCounts(opportunities, opportunities * chance, mode)
    if mode != 'custom':
        raise ValueError('只支持expected期望次数或custom自定义次数。')
    if type(successes) is not int or not 0 <= successes <= opportunities:
        raise ValueError('自定义触发次数必须为0至符合条件次数之间的整数。')
    if (chance == 0 and successes) or (chance == 1 and successes != opportunities):
        raise ValueError('指定次数与0%或100%的确定性概率冲突。')
    return TriggerCounts(opportunities, Fraction(successes), mode)


@dataclass(frozen=True, slots=True)
class CountedChange:
    balance: Fraction
    consumed: Fraction
    created: Fraction
    overflow: Fraction
    executed: bool
    counts: TriggerCounts


def resource_change(balance, trial, *, mode='expected', success_count=None):
    """One eligible operation, using N*p rather than enumerating outcomes.

    The original full cost remains the precondition; an expected refund cannot
    fund the same not-yet-executed operation. Capacity is applied to the expected
    quantity, explicitly a count approximation when the boundary is nonlinear.
    """
    if type(balance) not in (int, Fraction) or not 0 <= balance <= trial.capacity:
        raise ValueError('资源余额必须非负且不超过容量。')
    enough = balance >= trial.cost
    counts = trigger_counts(int(enough), trial.chance, mode=mode,
                            successes=(success_count if enough else 0) if mode == 'custom' else success_count)
    if not enough:
        return CountedChange(Fraction(balance), Fraction(), Fraction(), Fraction(), False, counts)
    cost = trial.cost * (1-counts.successes) if trial.success_waives_cost else Fraction(trial.cost)
    grant = trial.base_grant + trial.success_grant * counts.successes
    raw = balance - cost + grant
    return CountedChange(min(Fraction(trial.capacity), raw), cost, grant,
                         max(Fraction(), raw-trial.capacity), True, counts)


def exact_atoms(value):
    """Shared pools already represent fractional units with integer atoms."""
    value = Fraction(value)
    if value.denominator != 1:
        raise ValueError('期望数量小于当前资源原子精度；请同比提高unit_scale及余额、容量、操作和消费者数量，'
                         f'至少还需放大{value.denominator}倍，不会把小数取整或枚举概率。')
    return value.numerator
