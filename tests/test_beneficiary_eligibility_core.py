"""Generic queue oracles; no character-specific branch or answer fixture."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.beneficiary_selection import (
    effective_recipient,
    possible_recipients,
    select_recipients,
)
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from tests.test_mechanics_execution import (
    ability,
    action,
    add,
    condition,
    fixture,
    rule,
    second_character,
)

QUALIFY = {"contract": "beneficiary-eligibility-v1", "exclude_owner": True}


def scene(*, scope="locked", delay=0, cooldown=0, anchor="success"):
    t, r, m = fixture((.15, .6, .8, 1.1, 1.4, 1.5, 1.6, 2.0))
    second_character(t, r, "b")
    second_character(t, r, "c")
    for char in t['characters']:
        char['stats']['atk'] = 100
    r['abilities'].extend([ability('ab', 'a', 'action.switch'), ability('bc', 'b', 'action.switch'),
                           ability('ca', 'c', 'action.switch'), ability('be', 'b'), ability('ce', 'c')])
    r['actions'].extend([action('switch_b', 'ab', .2, .21, switch_to='b'),
                         action('switch_c', 'bc', .7, .71, switch_to='c'),
                         action('switch_a', 'ca', 1.2, 1.21, switch_to='a'),
                         action('b_cast', 'be', .25, .26), action('c_cast', 'ce', .75, .76)])
    for event_id, time in [('tick0', .12), ('tick1', .5), ('tick2', 1), ('tick3', 1.3)]:
        condition(m, event_id, time)
    for h, actor in zip(t['hits'], ['a', 'b', 'b', 'c', 'b', 'b', 'c', 'c'], strict=True):
        h['character'] = actor
    for h, descriptor in zip(t['hits'], m['hit_events'], strict=True):
        descriptor['action_id'] = 'cast' if h['character'] == 'a' else h['character'] + '_cast'
    row = rule('qualified', 'benefit.stat_add.atk', 100,
               trigger={'event': 'condition', 'phase': 'post', 'event_ids': ['tick0', 'tick1', 'tick2', 'tick3']},
               beneficiaries={'mode': 'frontline', 'scope': scope, 'eligibility': copy.deepcopy(QUALIFY)},
               timing={'delay': delay, 'duration': 1, 'cooldown': cooldown, 'cooldown_anchor': anchor})
    add(m, row)
    return t, r, m


def checked(t, r, m, matches=None):
    out = execute_mechanics(t, r, m, matches)
    assert out['result'] is not None, out['issues']
    return out


def test_locked_foreground_ticks_linger_and_expire_without_owner_delivery():
    t, r, m = scene()
    before = copy.deepcopy((t, r, m))
    out = checked(t, r, m)
    assert [h['expected'] for h in out['result']['hits']] == [45, 90, 90, 90, 90, 45, 90, 45]
    activations = [x for x in out['mechanics_trace'] if x['type'] == 'activation' and x['cycle'] == 0]
    assert [(x['t'], x['beneficiaries'], x['expires_at']) for x in activations] == [(.5, ['b'], [1.5]), (1, ['c'], [2])]
    refused = [x for x in out['mechanics_trace'] if x.get('reason') == 'no_eligible_beneficiary' and x['cycle'] == 0]
    assert [x['event_id'] for x in refused] == ['tick0', 'tick3']
    assert all(x['beneficiary_selection']['recipients'] == [] for x in refused)
    assert (t, r, m) == before


def test_include_cannot_override_recipient_eligibility():
    t, r, m = scene()
    out = checked(t, r, m, [{'rule_id': 'qualified', 'effect_id': 'value', 'hit_id': 'h0', 'operation': 'include'}])
    assert out['result']['hits'][0]['expected'] == 45


def test_dynamic_scope_does_not_promise_locked_linger():
    t, r, m = scene(scope='current_frontline')
    out = checked(t, r, m)
    assert [h['expected'] for h in out['result']['hits']] == [45, 90, 45, 90, 45, 45, 45, 45]


@pytest.mark.parametrize(('anchor', 'times'), [('attempt', [1]), ('success', [.5])])
def test_empty_selection_attempt_and_success_cooldowns(anchor, times):
    t, r, m = scene(cooldown=.8, anchor=anchor)
    out = checked(t, r, m)
    assert [x['t'] for x in out['mechanics_trace'] if x['type'] == 'activation' and x['cycle'] == 0] == times


def test_delay_carries_selected_actor_across_switch():
    t, r, m = scene(delay=.3)
    out = checked(t, r, m)
    activations = [x for x in out['mechanics_trace'] if x['type'] == 'activation' and x['cycle'] == 0]
    assert [(x['t'], x['beneficiaries'], x['beneficiary_selection']['frontline']) for x in activations] == [(.8, ['b'], 'b'), (1.3, ['c'], 'c')]
    assert [x['beneficiary_selection']['selection_t'] for x in activations] == [.5, 1]


@pytest.mark.parametrize('eligibility', [
    {'contract': 'beneficiary-eligibility-v1'},
    {**QUALIFY, 'exclude_owner': False}, {**QUALIFY, 'exclude_owner': 1},
    {**QUALIFY, 'characters': []}, {**QUALIFY, 'characters': ['b', 'b']},
    {**QUALIFY, 'characters': ['b'], 'unknown': True},
])
def test_closed_schema_and_nontrivial_selector(eligibility):
    _, _, m = scene()
    m['rules'][0]['beneficiaries']['eligibility'] = eligibility
    assert validate_mechanics_model(m)['status'] != 'valid'


@pytest.mark.parametrize('names', [['missing'], ['a']])
def test_unknown_or_permanently_empty_eligibility_rejected(names):
    t, r, m = scene()
    m['rules'][0]['beneficiaries']['eligibility']['characters'] = names
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    assert any(x['code'].startswith('beneficiary_eligibility_') for x in out['issues'])


@pytest.mark.parametrize('combination', ['next_switch', 'resource', 'enemy'])
def test_unimplemented_combinations_are_explicit(combination):
    _, _, m = scene()
    row = m['rules'][0]
    if combination == 'next_switch':
        row['beneficiaries']['mode'] = 'next_switch'
    elif combination == 'resource':
        row['resources'] = [{'id': 'energy', 'kind': 'energy_direct', 'delay': 0, 'amounts': {'a': 1}, 'source_part': 'energy'}]
    else:
        row['effects'][0].update(benefit_type_id='benefit.res_shred.cryo', domain='enemy', value=.1)
    assert any(x['code'] == 'beneficiary_eligibility_unsupported' for x in validate_mechanics_model(m)['issues'])


def test_empty_resources_is_not_a_side_effect():
    t, r, m = scene()
    m['rules'][0]['resources'] = []
    checked(t, r, m)


def test_initial_state_cannot_invent_excluded_actor():
    t, r, m = scene()
    m['initial_state']['rules'][0]['instances'] = [{'beneficiaries': ['a'], 'remaining': [1]}]
    out = execute_mechanics(t, r, m)
    assert any(x['code'] == 'beneficiary_initial_qualification' for x in out['issues'])


def test_initial_locked_actor_need_not_be_current_frontline():
    t, r, m = scene()
    m['initial_state']['rules'][0]['instances'] = [{'beneficiaries': ['b'], 'remaining': [.4]}]
    checked(t, r, m)


def test_possible_is_static_and_selection_order_is_roster_order():
    row = scene()[2]['rules'][0]
    row['beneficiaries']['eligibility']['characters'] = ['c', 'b']
    assert possible_recipients(row, ['a', 'b', 'c']) == ['b', 'c']
    assert select_recipients(row, ['a', 'b', 'c'], 'a') == []
    assert select_recipients(row, ['a', 'b', 'c'], 'c') == ['c']
    assert effective_recipient(row, {'beneficiaries': ['b']}, 'b', 'c')


def test_new_receipts_share_existing_capture_byte_limit(monkeypatch):
    from genshin_engine import snapshot_capture
    monkeypatch.setattr(snapshot_capture, 'MAX_SNAPSHOT_CAPTURE_BYTES', 100)
    out = execute_mechanics(*scene())
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'snapshot_capture_limit' for i in out['issues'])


def test_new_selections_share_event_budget_and_keep_actual_prefix(monkeypatch):
    from genshin_engine import mechanics
    monkeypatch.setattr(mechanics, 'MAX_MECHANICS_EVENTS', 1)
    out = execute_mechanics(*scene())
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'beneficiary_selection_event_limit' for i in out['issues'])
