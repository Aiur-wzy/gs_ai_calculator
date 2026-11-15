"""Timing changes retain component identity, component changes do not."""
import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_direct_stellar_damage_contract import direct_stellar_profile
from tests.test_elemental_multiaura_v4 import aura
from tests.test_mechanics_execution import EVIDENCE, add, fixture, rule
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
)


def stellar():
    t, r, m = _application_fixture('electro', initial=[aura('cryo', .8, 'a')], times=(.2, 1.0))
    r['abilities'][0].update(variant='tap', effective_level=10)
    _enable(m, 'stellar_superconduct')
    t['hits'][1]['element'] = 'cryo'
    m['hit_events'][1]['application'] = {'profile_id': 'cryo', 'target_id': 'enemy', 'landed': True}
    m['special_reaction_events'] = [{'id': 'generated', 'kind': 'direct_stellar_damage',
        'action_id': 'cast', 'target_id': 'enemy', 'area_id': 'battlefield.primary', 't': 4.2, 'order': 20,
        'ability_profile': direct_stellar_profile(character_id='a', ability_id='e'), **EVIDENCE}]
    add(m, rule('star', benefit='benefit.stellar_mult.all', value=2, duration=10))
    m['rules'][-1]['effects'][0]['filter'] = {'event': 'reaction_damage', 'phase': 'pre'}
    return t, r, m


def stable_ref(data):
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    row = next(row for row in out['resolved_template']['hits'] if row['damage_system'] == 'stellar')
    return row['extensions']['execution_ref']


def test_direct_stellar_reference_survives_time_move_but_not_component_change():
    t, r, m = stellar()
    ref = stable_ref((t, r, m))
    assert ref['source_event_id'] == 'generated' and 'time_us' not in ref
    match = {'rule_id': 'star', 'effect_id': 'value', 'operation': 'exclude', 'event_ref': ref}
    before = execute_mechanics(t, r, m, [match])
    assert before['result'] is not None, before['issues']
    m['special_reaction_events'][0]['t'] = 4.3
    moved = execute_mechanics(t, r, m, [match])
    assert moved['result'] is not None, moved['issues']
    assert [r['status'] for r in moved['mechanics_trace'] if r['type'] == 'runtime_match_resolution'] == ['resolved']
    match['event_ref'] = {**ref, 'component_id': 'other.component'}
    invalid = execute_mechanics(t, r, m, [match])
    assert invalid['status'] == 'pending' and any(i['code'] == 'runtime_match_no_match' for i in invalid['issues'])
    assert invalid['result']['mechanics']['execution_status'] == 'pending'


def test_stable_refs_select_generated_damage_without_guessed_runtime_id():
    t, r, m = stellar()
    ref = stable_ref((t, r, m))
    m['rules'][0]['effects'][0]['filter']['event_refs'] = [ref]
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    supported = next(h['expected'] for h in out['result']['hits'] if h['damage_system'] == 'stellar')
    m['rules'][0]['effects'][0]['filter']['event_refs'][0]['component_id'] = 'unused.component'
    no_match = execute_mechanics(t, r, m)
    assert no_match['result'] is not None, no_match['issues']
    assert supported > next(h['expected'] for h in no_match['result']['hits'] if h['damage_system'] == 'stellar')
    m['rules'][0]['effects'][0]['filter']['event_refs'][0]['source_event_id'] = 'missing.input'
    rejected = execute_mechanics(t, r, m)
    assert rejected['result'] is None and any(i['code'] == 'runtime_selector_reference' for i in rejected['issues'])


def test_pattern_occurrences_are_explicit_not_inferred_from_id_text():
    t, r, m = fixture((.2,))
    r['actions'][0]['repeat'] = {'mode': 'once'}
    m['hit_events'] = []
    m['hit_patterns'] = [{'id': 'channel', 'prototype_hit_id': 'h0', 'action_id': 'cast',
        'offsets': [.2, .3, .4], 'orders': [10, 11, 12]}]
    add(m, rule(value=1, duration=10))
    base = execute_mechanics(t, r, m)
    refs = [row['extensions']['execution_ref'] for row in base['resolved_template']['hits']]
    assert [r['occurrence'] for r in refs] == [1, 2, 3]
    assert all(r['component_id'] == 'h0' and r['source_event_id'] == 'channel' for r in refs)
    m['rules'][0]['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'event_refs': [refs[1]]}
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == pytest.approx([45, 90, 45])
