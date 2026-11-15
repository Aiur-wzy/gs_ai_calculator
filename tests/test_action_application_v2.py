"""Explicit v3 application dispatch; old contact scope remains unchanged."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.action_event_schema import (
    action_event_receipt_compiler,
    validate_action_event_fields,
)
from genshin_app.equipment.action_events import (
    build_single_action_scope,
    compile_action_events,
)
from genshin_app.equipment.schema import PlanError
from tests.test_elemental_swirl import swirl_model
from tests.test_freeze_runtime import model as freeze_model
from tests.test_mechanics_execution import EVIDENCE, action, fixture

KIND = 'calibrated_single_application_v2'


def case(kind=KIND):
    t, r, m = fixture((0,))
    raw = copy.deepcopy(t)
    raw['hits'][0].pop('multiplier')
    raw['hits'][0]['scaling_stat'] = 'atk'
    t['hits'][0]['scaling_stat'] = 'atk'
    r['abilities'][0]['variant'] = 'tap'
    r['actions'] = [action('cast', end=1, repeat={'mode': 'once'})]
    m['hit_events'] = []
    profile = {'id': 'profile', 'kind': kind, 'prototype_hit_id': 'h0', 'variant': 'tap',
        'calibration': {'offset_seconds': .2, 'precision_seconds': .01, **EVIDENCE},
        'application_profile_id': 'cryo', 'default_landed': True, 'self_sampling': 'current',
        'accepted_profile_fingerprint': None}
    if kind == KIND:
        m['elemental_model'] = swirl_model('cryo')
        m['elemental_model']['ruleset'] = 'gauge-swirl-dynamic-v3'
    else:
        m['elemental_model'] = freeze_model()
        m['elemental_model']['contact_profiles'] = [{'id': 'safe', 'freeze_interaction': 'none', **EVIDENCE}]
        profile['contact_profile_id'] = 'safe'
    declaration = {'hit_id': 'h0', 'version_line': 'release', 'selection': {'character_id': 1, 'slot': 'skill', 'constellation': 0},
        'param_index': 0, 'scaling_stat': 'atk', 'purpose': 'single_stat_damage_multiplier', **EVIDENCE}
    parameter = {'hit_id': 'h0', 'status': 'ready', 'resolved_selection': {'character_id': 1, 'skill_depot_id': 2,
        'skill_id': 3, 'constellation': 0, 'matching_slots': ['skill']}, 'level': {'effective_level': 10}}
    plan = {'plan_version': 3, 'manual_template': raw, 'mechanics_model': m, 'rotation_model': r,
        'hit_parameter_sources': [declaration], 'action_event_profiles': [profile],
        'action_event_bindings': [{'ability_id': 'e', 'profile_id': 'profile'}], 'action_event_overrides': []}
    kwargs = {'parameter_compilations': [parameter], 'elemental_compilations': [],
              'reaction_compilations': [], 'missing': lambda *args: None}
    return plan, t, kwargs


def accepted(kind=KIND):
    plan, t, kwargs = case(kind)
    _, records, _ = compile_action_events(plan, t, **kwargs)
    assert records[0]['required_confirmation'], records
    plan['action_event_profiles'][0].update(records[0]['required_confirmation'])
    return plan, t, kwargs


def old_baseline():
    plan, t, kwargs = accepted('calibrated_single_contact_v1')
    return compile_action_events(plan, t, **kwargs)


def test_new_v3_dispatch_requires_new_acceptance_and_emits_no_freeze_contact():
    plan, t, kwargs = accepted()
    before = copy.deepcopy((plan, t, kwargs['parameter_compilations']))
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert (plan, t, kwargs['parameter_compilations']) == before
    assert records[0]['status'] == 'ready' and records[0]['compiler'] == 'calibrated-action-events-v2'
    assert len(model['hit_patterns']) == 1
    assert 'contact' not in model['hit_patterns'][0]
    assert records[0]['generated'][0]['t'] == .2


def test_new_v3_dispatch_accepts_exact_nonzero_constellation_and_level():
    plan, template, kwargs = case()
    plan['hit_parameter_sources'][0]['selection'].update(constellation=2, talent_level=13)
    kwargs['parameter_compilations'][0]['resolved_selection']['constellation'] = 2
    kwargs['parameter_compilations'][0]['level']['effective_level'] = 13
    _, records, _ = compile_action_events(plan, template, **kwargs)
    plan['action_event_profiles'][0].update(records[0]['required_confirmation'])
    model, records, _ = compile_action_events(plan, template, **kwargs)
    assert records[0]['status'] == 'ready'
    assert scope(plan, template, kwargs)['dependencies']['parameter']['effective_level'] == 13
    assert len(model['hit_patterns']) == 1


def test_new_kind_cannot_hide_contact_and_old_kind_still_requires_contact():
    plan, _, _ = case()
    plan['action_event_profiles'][0]['contact_profile_id'] = 'safe'
    with pytest.raises(PlanError):
        validate_action_event_fields(plan)
    plan, _, _ = case('calibrated_single_contact_v1')
    del plan['action_event_profiles'][0]['contact_profile_id']
    with pytest.raises(PlanError):
        validate_action_event_fields(plan)


@pytest.mark.parametrize('kind,other', [(KIND, 'gauge-freeze-reference-v1'),
    ('calibrated_single_contact_v1', 'gauge-swirl-dynamic-v3')])
def test_old_and_new_rulesets_cannot_borrow_each_others_admission(kind, other):
    plan, t, kwargs = case(kind)
    plan['mechanics_model']['elemental_model']['ruleset'] = other
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model is None
    assert records[0]['required_confirmation'] is None
    assert any(x['code'] == 'action_event_elemental_scope' for x in records[0]['issues'])


@pytest.mark.parametrize('mutation', ['parameter', 'calibration', 'application', 'level', 'constellation', 'variant', 'repeat'])
def test_new_dispatch_preserves_parameter_timing_and_once_guards(mutation):
    plan, t, kwargs = accepted()
    if mutation == 'parameter':
        t['hits'][0]['multiplier'] += 1
    elif mutation == 'calibration':
        plan['action_event_profiles'][0]['calibration']['offset_seconds'] = .3
    elif mutation == 'application':
        next(p for p in plan['mechanics_model']['elemental_model']['profiles'] if p['id'] == 'cryo')['source'] += ' changed'
    elif mutation == 'level':
        kwargs['parameter_compilations'][0]['level']['effective_level'] = 9
    elif mutation == 'constellation':
        kwargs['parameter_compilations'][0]['resolved_selection']['constellation'] = 2
    elif mutation == 'variant':
        plan['rotation_model']['abilities'][0]['variant'] = 'hold'
    else:
        plan['rotation_model']['actions'][0]['repeat'] = {'mode': 'each_cycle'}
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model is None and records[0]['status'] == 'pending'


def test_missed_v3_action_remains_explicit_false():
    plan, t, kwargs = case()
    plan['action_event_profiles'][0]['default_landed'] = False
    _, records, _ = compile_action_events(plan, t, **kwargs)
    plan['action_event_profiles'][0].update(records[0]['required_confirmation'])
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model['hit_patterns'][0]['application']['landed'] is False
    assert records[0]['stats']['missed_hits'] == 1


def scope(plan, template, kwargs):
    profile = plan['action_event_profiles'][0]
    model = plan['mechanics_model']
    return build_single_action_scope(profile, raw_hit=plan['manual_template']['hits'][0], hit=template['hits'][0],
        parameter=kwargs['parameter_compilations'][0], declaration=plan['hit_parameter_sources'][0],
        application=next(p for p in model['elemental_model']['profiles'] if p['id'] == profile['application_profile_id']),
        contact=None, metadata=None, elemental_record=None, reaction_compilations=[],
        selected_actions=plan['rotation_model']['actions'], rotation=plan['rotation_model'], model=model)[0]


def test_new_scope_has_its_own_complete_contract_and_rejects_unknown_kind():
    plan, t, kwargs = accepted()
    value = scope(plan, t, kwargs)
    assert value['compiler'] == 'calibrated-action-events-v2'
    assert value['dependencies']['application_contract']['contact'] == 'not_supported'
    assert value['dependencies']['ruleset'] == 'gauge-swirl-dynamic-v3'
    assert 'contact' not in value['dependencies'] and 'reference_fingerprint' not in value['dependencies']
    plan['action_event_profiles'][0]['kind'] = 'unreviewed_kind'
    with pytest.raises(ValueError, match='kind'):
        scope(plan, t, kwargs)


def test_accepted_old_fingerprint_cannot_authorize_new_kind():
    old, _, _ = accepted('calibrated_single_contact_v1')
    plan, t, kwargs = case()
    plan['action_event_profiles'][0]['accepted_profile_fingerprint'] = old['action_event_profiles'][0]['accepted_profile_fingerprint']
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model is None
    assert records[0]['profile_fingerprint'] != plan['action_event_profiles'][0]['accepted_profile_fingerprint']


@pytest.mark.parametrize('module', ['energy_generation_sources', 'limited_hit_benefit_sources'])
def test_new_kind_does_not_inherit_legacy_source_parent_qualification(module):
    import importlib
    parent = importlib.import_module('genshin_app.equipment.' + module)
    plan, t, kwargs = accepted()
    _, records, _ = compile_action_events(plan, t, **kwargs)
    with pytest.raises(ValueError):
        parent.parent_inputs(plan, t, {'action_event_profile_id': 'profile'}, records, kwargs['parameter_compilations'], [])


def test_mixed_new_and_freeze_profiles_are_atomically_pending():
    plan, t, kwargs = accepted()
    old, _, _ = case('calibrated_single_contact_v1')
    other = old['action_event_profiles'][0]
    other.update(id='old', prototype_hit_id='other')
    plan['action_event_profiles'].append(other)
    for template in (plan['manual_template'], t):
        template['hits'].append({**template['hits'][0], 'id': 'other'})
    plan['hit_parameter_sources'].append({**plan['hit_parameter_sources'][0], 'hit_id': 'other'})
    kwargs['parameter_compilations'].append({**kwargs['parameter_compilations'][0], 'hit_id': 'other'})
    plan['rotation_model']['abilities'].append({**plan['rotation_model']['abilities'][0], 'id': 'other_e'})
    plan['rotation_model']['actions'].append(action('other_cast', 'other_e', start=2, end=3, repeat={'mode': 'once'}))
    plan['action_event_bindings'].append({'ability_id': 'other_e', 'profile_id': 'old'})
    model, records, sources = compile_action_events(plan, t, **kwargs)
    assert model is None and sources == []
    assert all(r['generated'] == [] for r in records)
    assert action_event_receipt_compiler(records) == 'calibrated-action-events-v2'
    assert action_event_receipt_compiler([r for r in records if r['kind'] != KIND]) == 'calibrated-action-events-v1'


@pytest.mark.parametrize('value', [None, 'safe'])
def test_even_null_contact_field_cannot_enter_new_profile(value):
    plan, _, _ = case()
    plan['action_event_profiles'][0]['contact_profile_id'] = value
    with pytest.raises(PlanError):
        validate_action_event_fields(plan)
