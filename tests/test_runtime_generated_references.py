"""Actual automatic reaction references round-trip without guessed source IDs."""
from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import aura
from test_mechanics_execution import add, fixture, rule, second_character
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.runtime_reference_audit import RuntimeReferenceAudit


def scene(family):
    element, initial = {
        'stellar_swirl': ('anemo', 'cryo'), 'swirl': ('anemo', 'cryo'),
        'bloom': ('hydro', 'dendro'), 'lunar_electro': ('electro', 'hydro'),
        'lunar_crystallize': ('geo', 'hydro'),
    }[family]
    times = (.2, .4, .6) if family == 'lunar_crystallize' else (.2,)
    template, rotation, mechanics = _application_fixture(element, initial=[aura(initial, 2.4, 'b')], times=times)
    second_character(template, rotation, 'b')
    for character in template['characters']:
        character['stats'].update(crit_rate=1, crit_dmg=1)
    for hit, descriptor in zip(template['hits'], mechanics['hit_events'], strict=True):
        hit['element'] = element
        descriptor['application'] = {'profile_id': element, 'target_id': 'enemy', 'landed': True,
                                     'area_id': 'battlefield.primary'}
    if family != 'swirl':
        _enable(mechanics, 'lunar_bloom' if family == 'bloom' else family)
    return template, rotation, mechanics


def references(out):
    assert out['result'] is not None, out['issues']
    return [copy.deepcopy(row['execution_ref']) for row in out['mechanics_trace']
            if row.get('type') == 'reaction_damage' and row.get('reported_damage') and row.get('cycle') == 0]


def filtered(mechanics, ref):
    boost = rule('precise', 'benefit.res_shred.all', .1, duration=9, domain='enemy',
                 beneficiaries={'mode': 'team', 'scope': 'locked'})
    boost['effects'][0]['filter'] = {'event': 'reaction_damage', 'phase': 'pre', 'event_refs': [ref]}
    add(mechanics, boost)


@pytest.mark.parametrize('family', ['stellar_swirl', 'swirl', 'bloom', 'lunar_electro', 'lunar_crystallize'])
def test_every_actual_automatic_component_can_be_selected_from_unchanged_output(family):
    template, rotation, mechanics = scene(family)
    baseline = execute_mechanics(template, rotation, mechanics)
    refs = references(baseline)
    assert refs
    for ref in refs:
        model = copy.deepcopy(mechanics)
        filtered(model, ref)
        actual = execute_mechanics(template, rotation, model)
        assert actual['result'] is not None and not actual['issues'], (ref, actual['issues'])
        assert any(any(effect['effect_id'] == 'precise:value:0' for effect in hit.get('matched_effect_refs', []))
                   for hit in actual['result']['hits'])
        for row in actual['mechanics_trace']:
            if row.get('type') == 'runtime_reference_source_resolution':
                assert row['status'] == 'resolved' and row['input_roots']


@pytest.mark.parametrize('index', [0, 2])
def test_immediate_and_delayed_reference_survive_time_move_and_allow_explicit_match(index):
    template, rotation, mechanics = scene('stellar_swirl')
    add(mechanics, rule('precise', 'benefit.crit_dmg.all', .5, duration=9,
        beneficiaries={'mode': 'team', 'scope': 'locked'}))
    mechanics['rules'][0]['effects'][0]['filter'] = {'event': 'reaction_damage', 'phase': 'pre'}
    ref = references(execute_mechanics(template, rotation, mechanics))[index]
    template['hits'][0]['t'] = .3
    actual = execute_mechanics(template, rotation, mechanics, [
        {'rule_id': 'precise', 'effect_id': 'value', 'operation': 'exclude', 'event_ref': ref}])
    assert actual['result'] is not None and not actual['issues'], actual['issues']
    assert next(row for row in actual['mechanics_trace'] if row.get('type') == 'runtime_match_resolution')['status'] == 'resolved'


@pytest.mark.parametrize('change', ['fake_source', 'wrong_origin', 'remove_reaction'])
def test_unproven_generated_source_cannot_be_admitted(change):
    template, rotation, mechanics = scene('stellar_swirl')
    ref = references(execute_mechanics(template, rotation, mechanics))[0]
    if change == 'fake_source':
        ref['source_event_id'] += ':invented'
    elif change == 'wrong_origin':
        ref['origin'] = 19
    else:
        mechanics['elemental_model']['target']['initial_auras'] = []
    filtered(mechanics, ref)
    actual = execute_mechanics(template, rotation, mechanics)
    assert actual['status'] == 'invalid' and actual['result'] is None
    issue = next(row for row in actual['issues'] if row['code'] == 'runtime_selector_reference')
    assert issue['reference'] == ref
    assert issue['path'] == 'mechanics_model.rules[0].effects[0].filter.event_refs[0]'
    assert issue['resolution'] == 'source_unresolved'


def test_proven_source_with_nonmatching_character_is_an_empty_filter_not_an_extra_buff():
    template, rotation, mechanics = scene('stellar_swirl')
    second_character(template, rotation, 'c')
    baseline = execute_mechanics(template, rotation, mechanics)
    ref = references(baseline)[0]
    ref['character'] = 'c'
    filtered(mechanics, ref)
    actual = execute_mechanics(template, rotation, mechanics)
    assert not actual['issues'] and actual['result'] is not None
    assert actual['result']['summary']['total_damage'] == baseline['result']['summary']['total_damage']
    resolution = next(row for row in actual['mechanics_trace'] if row.get('type') == 'runtime_reference_source_resolution')
    assert resolution['status'] == 'no_match' and resolution['input_roots']


@pytest.mark.parametrize('extension', ['special_reaction', 'elemental'])
def test_author_extensions_cannot_self_authorize_generated_source(extension):
    template, rotation, mechanics = fixture((.2,))
    key = 'request_id' if extension == 'special_reaction' else 'parent_event_id'
    template['hits'][0]['extensions'] = {extension: {key: 'invented', 'lineage': 'h0'}}
    baseline = execute_mechanics(template, rotation, mechanics)
    ref = baseline['resolved_template']['hits'][0]['extensions']['execution_ref']
    assert ref['source_event_id'] == 'invented'
    boost = rule()
    boost['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'event_refs': [ref]}
    add(mechanics, boost)
    actual = execute_mechanics(template, rotation, mechanics)
    assert actual['status'] == 'invalid' and actual['result'] is None


def test_actual_multiple_damage_occurrences_do_not_silently_resolve_an_exact_reference():
    from types import SimpleNamespace
    ref = {'contract': 'runtime-damage-match-v2', 'source_event_id': 'opaque-child',
        'component_id': 'part', 'occurrence': 0, 'character': 'a', 'damage_system': 'stellar', 'origin': 0}
    audit = RuntimeReferenceAudit({'runtime': [{'id': 'root'}], '_deferred_runtime_references': [
        {'kind': 'selector', 'reference': ref, 'path': 'rules[0].effects[0].filter'}]})
    audit.input_event({'id': 'root', 'origin': 0, 'run_id': '0:root'})
    audit.generated({'event_id': 'opaque-child', 'parent_reaction_id': 'root'}, 0)
    for identity in ('target-A', 'target-B'):
        event = {'id': identity, 'origin': 0, 'run_id': identity}
        audit.projected(event, 'opaque-child')
        audit.damage(event, {'hit': {'id': identity}}, ref)
    runtime = SimpleNamespace(trace=[], issues=[])
    assert audit.finish(runtime)
    assert runtime.issues[0]['code'] == 'runtime_selector_ambiguous'


def test_automatic_damage_from_pattern_keeps_its_real_occurrence_after_time_move():
    template, rotation, mechanics = scene('stellar_swirl')
    mechanics['hit_events'] = []
    mechanics['hit_patterns'] = [{'id': 'opaque-channel', 'prototype_hit_id': 'h0', 'action_id': 'cast',
        'offsets': [.2, .6], 'orders': [10, 11],
        'application': {'profile_id': 'anemo', 'target_id': 'enemy', 'landed': True}}]
    baseline = execute_mechanics(template, rotation, mechanics)
    refs = [ref for ref in references(baseline) if ref['reaction_type'] == 'stellar_swirl_anemo' and ref['character'] == 'b']
    assert len(refs) == 2 and refs[0] != refs[1]
    filtered(mechanics, refs[1])
    mechanics['hit_patterns'][0]['offsets'] = [.3, .7]
    original = copy.deepcopy((template, rotation, mechanics))
    actual = execute_mechanics(template, rotation, mechanics)
    assert actual['result'] is not None and not actual['issues'], actual['issues']
    applied = [hit for hit in actual['result']['hits'] if hit.get('matched_effect_refs')]
    assert len(applied) == 1 and applied[0]['t'] == .7
    assert (template, rotation, mechanics) == original


def test_incomplete_execution_does_not_mislabel_unreached_reference_as_input_corruption():
    from types import SimpleNamespace
    ref = {'contract': 'runtime-damage-match-v2', 'source_event_id': 'later-child',
        'component_id': 'part', 'occurrence': 0, 'character': 'a', 'damage_system': 'stellar', 'origin': 0}
    audit = RuntimeReferenceAudit({'runtime': [{'id': 'root'}], '_deferred_runtime_references': [
        {'kind': 'selector', 'reference': ref, 'path': 'rules[0].effects[0].filter'}]})
    runtime = SimpleNamespace(trace=[], issues=[])
    assert audit.finish(runtime, execution_complete=False)
    assert runtime.trace[0]['status'] == 'source_not_reached'
    assert runtime.issues[0]['severity'] == 'pending'
