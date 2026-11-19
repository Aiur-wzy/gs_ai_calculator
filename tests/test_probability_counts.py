from fractions import Fraction

import pytest

from genshin_engine.probabilistic_resource import ResourceTrial
from genshin_engine.probability_counts import resource_change, trigger_counts


def test_default_count_is_linear_even_for_large_input():
    count = trigger_counts(10**12, .34)
    assert count.successes == 340000000000 and count.failures == 660000000000
    assert count.mode == 'expected'


def test_fractional_count_is_not_rounded_or_expanded():
    assert trigger_counts(3, .34).successes == Fraction(51, 50)
    assert trigger_counts(3, .34, mode='custom', successes=2).successes == 2


@pytest.mark.parametrize('opportunities,chance,options', [
    (True,.5,{}), (-1,.5,{}), (3,float('nan'),{}), (3,.5,{'successes':2}),
    (3,.5,{'mode':'custom','successes':4}), (3,0,{'mode':'custom','successes':1}),
    (3,1,{'mode':'custom','successes':2}), (3,.5,{'mode':'sample'}),
])
def test_invalid_count_or_scenario_is_explicit(opportunities,chance,options):
    with pytest.raises(ValueError):
        trigger_counts(opportunities,chance,**options)


def test_refund_waiver_and_extra_stack_have_different_accounting():
    refund = resource_change(3,ResourceTrial(1,0,1,False,.34,5))
    waiver = resource_change(3,ResourceTrial(1,0,0,True,.34,5))
    extra = resource_change(3,ResourceTrial(0,1,1,False,.34,5))
    assert refund.balance == waiver.balance == Fraction(117,50)
    assert refund.consumed == 1 and refund.created == Fraction(17,50)
    assert waiver.consumed == Fraction(33,50) and waiver.created == 0
    assert extra.balance == Fraction(217,50)


def test_capacity_and_insufficient_cost_are_deterministic_count_policy():
    capped=resource_change(5,ResourceTrial(0,1,1,False,.5,5))
    assert capped.balance == 5 and capped.overflow == Fraction(3,2)
    blocked=resource_change(0,ResourceTrial(1,0,1,False,.5,5))
    assert not blocked.executed and blocked.created == blocked.counts.successes == 0
