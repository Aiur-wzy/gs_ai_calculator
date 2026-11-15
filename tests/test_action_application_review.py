"""Independent strict v2 admission, preserving old contact restrictions."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.action_event_schema import validate_action_event_fields
from genshin_app.equipment.action_events import compile_action_events
from genshin_app.equipment.schema import PlanError
from genshin_engine.mechanics import execute_mechanics
from tests.test_action_application_v2 import accepted, case, scope
from tests.test_mechanics_execution import EVIDENCE, action


def test_native_null_contact_is_forbidden_in_v2_not_treated_as_absent():
    plan, _, _ = case()
    plan['action_event_profiles'][0]['contact_profile_id'] = None
    with pytest.raises(PlanError):
        validate_action_event_fields(plan)


def test_direct_scope_cannot_accept_contact_material_hidden_from_profile():
    plan, t, kwargs = accepted()
    from genshin_app.equipment.action_events import build_single_action_scope
    with pytest.raises(ValueError, match='contact'):
        build_single_action_scope(plan['action_event_profiles'][0],
            raw_hit=plan['manual_template']['hits'][0], hit=t['hits'][0],
            parameter=kwargs['parameter_compilations'][0], declaration=plan['hit_parameter_sources'][0],
            application=plan['mechanics_model']['elemental_model']['profiles'][0],
            contact={'id': 'hidden'}, metadata=None, elemental_record=None, reaction_compilations=[],
            selected_actions=plan['rotation_model']['actions'], rotation=plan['rotation_model'],
            model=plan['mechanics_model'])


def test_executed_miss_has_no_damage_or_elemental_application():
    plan, t, kwargs = case()
    plan['action_event_profiles'][0]['default_landed'] = False
    _, records, _ = compile_action_events(plan, t, **kwargs)
    plan['action_event_profiles'][0].update(records[0]['required_confirmation'])
    model, records, _ = compile_action_events(plan, t, **kwargs)
    out = execute_mechanics(t, plan['rotation_model'], model)
    assert out['result'] is not None, out['issues']
    assert out['result']['hits'] == [] and out['result']['summary']['total_damage'] == 0
    applications = [row for row in out['elemental_trace'] if row['type'] == 'elemental_application']
    assert len(applications) == 1
    assert applications[0]['allowed'] is False and applications[0]['reason'] == 'missed'
    assert applications[0]['before'] == applications[0]['after']


def test_binding_actor_mismatch_is_not_approved_by_previously_valid_scope():
    plan, t, kwargs = accepted()
    plan['rotation_model']['abilities'][0]['character'] = 'other'
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model is None and records[0]['generated'] == []
    assert any(i['code'] == 'action_event_ability_scope' for i in records[0]['issues'])


def test_switch_cancels_original_occupation_even_after_calibrated_hit():
    plan, t, kwargs = accepted()
    rotation = plan['rotation_model']
    rotation['abilities'].append({'id': 'switch', 'character': 'a',
        'action_type_id': 'action.switch', 'cooldown': None, 'energy': None, **EVIDENCE})
    rotation['actions'].append(action('swap', 'switch', start=.5, end=.6,
        order=10, repeat={'mode': 'once'}))
    model, records, _ = compile_action_events(plan, t, **kwargs)
    assert model is None
    assert any(i['code'] == 'action_event_cancel' for i in records[0]['issues'])


def test_scope_return_mutation_cannot_change_application_contract_or_acceptance():
    plan, t, kwargs = accepted()
    baseline = copy.deepcopy(scope(plan, t, kwargs))
    first = scope(plan, t, kwargs)
    first['dependencies']['application_contract']['emission'] = 'unreviewed'
    assert scope(plan, t, kwargs) == baseline
