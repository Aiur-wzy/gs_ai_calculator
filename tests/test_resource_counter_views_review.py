"""Independent exact views, explicitly without authenticating caller state."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counter_views import CounterViewError, CounterViewProgram
from tests.test_resource_counter_views import setup


def test_view_declaration_order_does_not_change_identity_or_mathematics():
    conf, counters, state = setup(raw=399)
    conf['views'].append({**copy.deepcopy(conf['views'][0]), 'id': '冰',
                          'transform': {'kind': 'steps', 'step_atoms': 10}})
    a = CounterViewProgram(conf, counters)
    conf['views'].reverse()
    b = CounterViewProgram(conf, counters)
    for key in ('read', '冰'):
        assert a.read(key, state) == b.read(key, state)


def test_read_is_an_exact_fraction_even_when_float_would_lose_fraction():
    conf, counters, state = setup(raw=10**15-1, scale=999_983)
    conf['views'][0]['transform'] = {'kind': 'scaled'}
    out = CounterViewProgram(conf, counters).read('read', state)
    assert out['numerator'] == 10**15-1 and out['denominator'] == 999_983
    assert out['raw_atoms'] == 10**15-1 and out['remainder_atoms'] is None


def test_pure_forged_values_still_have_no_execution_or_source_authority():
    conf, counters, state = setup(raw=777)
    before = copy.deepcopy(state)
    out = CounterViewProgram(conf, counters).read('read', state)
    assert out['raw_atoms'] == 777 and out['numerator'] == 400
    assert out['sampling_authority'] == 'caller_supplied_state'
    assert out['qualification'] == out['source_approval'] == 'not_evaluated'
    assert state == before


def test_known_empty_recipient_key_returns_zero_but_missing_key_cannot():
    conf, counters, state = setup(scope='pool_key', key={'kind': 'recipient'})
    state['values']['count']['b'] = 0
    program = CounterViewProgram(conf, counters)
    assert program.read('read', state, recipient='b')['raw_atoms'] == 0
    del state['values']['count']['b']
    with pytest.raises(CounterViewError):
        program.read('read', state, recipient='b')


def test_mutating_original_counter_definition_cannot_rewrite_view_units():
    conf, counters, state = setup(raw=399)
    program = CounterViewProgram(conf, counters)
    counters._pools['pool']['unit_scale'] = 1
    out = program.read('read', state)
    assert (out['numerator'], out['denominator']) == (399, 10)
