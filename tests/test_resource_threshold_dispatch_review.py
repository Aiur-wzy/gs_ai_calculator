"""Independent pure R1 constant-target preparation; actual dispatch remains separate."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_threshold_dispatch import DISPATCH_LIMITS
from genshin_engine.shared_pools import SharedPoolError, _canonical
from tests.test_mechanics_execution import rule
from tests.test_resource_threshold_dispatch import empty, frame, make, model, prepare


def test_exposed_binding_list_cannot_retarget_checked_definition():
    data = model()
    other = rule('unchecked', source_id='other-source', duration=7, domain='self')
    data['rules'].append(other)
    program = make(data)
    before = prepare(program)
    exposed = program.bindings
    exposed[0]['payload']['ref_id'] = 'unchecked'
    after = prepare(program)
    assert after == before


@pytest.mark.parametrize('field', ['max', 'add'])
def test_bool_stack_numbers_do_not_pass_whole_target_integer_contract(field):
    data = model()
    data['rules'][0]['stacking'][field] = True
    with pytest.raises(SharedPoolError):
        make(data)


@pytest.mark.parametrize('qualified', [False, True])
def test_declined_candidate_is_bounded_before_return(monkeypatch, qualified):
    program = make()
    state = empty()
    if qualified:
        state['cooldown'] = 1_000_000
    monkeypatch.setitem(DISPATCH_LIMITS, 'state_bytes', len(_canonical(state)))
    before = copy.deepcopy(state)
    with pytest.raises(SharedPoolError):
        prepare(program, state, qualified=qualified)
    assert state == before


def test_serial_limit_failure_leaves_input_and_refresh_candidate_is_detached():
    program, state = make(), empty()
    with pytest.raises(SharedPoolError):
        prepare(program, state, attribute_layer_serial=DISPATCH_LIMITS['events'])
    assert state == empty()
    first = prepare(program, state, attribute_layer_serial=0)
    before = copy.deepcopy(first)
    second = prepare(program, first['state'], frame=frame(200_000, 'dispatch:2'), attribute_layer_serial=1)
    assert first == before and second['attribute_layer_serial'] == 1
    assert second['state']['instances'][0]['attribute_layers'] == ['layer:0']
