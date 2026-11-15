import copy

import pytest

from genshin_app.equipment.reaction_em_execution import verify_reaction_em_execution
from genshin_app.equipment.schema import PlanError
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.runtime_reaction_em_compiler import PROFILE_ID
from tests.test_runtime_reaction_em_source import prepared_queue


def executed():
    t, r, m = prepared_queue((.2, .3))
    out = execute_mechanics(t, r, m)
    comp = [{'profile_id': PROFILE_ID, 'status': 'ready', 'binding_id': 'test', 'rules': m['rules'],
             'source_identity': {'provider': 'a'}, 'app_scope': {'target_id': 'enemy'}}]
    return out, comp


def test_refresh_joins_its_actual_occurrence_not_first_instance_identity():
    out, comp = executed(); before = copy.deepcopy(out)
    proof = verify_reaction_em_execution(out, comp)
    assert [r['reaction_run_id'] for r in proof['activations']] == ['0:h0:reaction:0', '0:h1:reaction:0']
    assert [r['parent_run_id'] for r in proof['activations']] == ['0:h0', '0:h1']
    activations = [r for r in out['mechanics_trace'] if r['type'] == 'activation']
    assert activations[0]['instance_id'] == activations[1]['instance_id']
    assert out == before


@pytest.mark.parametrize('change', ['wrong_owner', 'wrong_target', 'missing_reaction', 'duplicate_reaction',
                                  'missing_parent', 'duplicate_parent', 'other_cycle', 'nearby_time',
                                  'duplicate_activation', 'changed_parent'])
def test_no_borrowing_missing_ambiguous_or_other_origin_occurrences(change):
    out, comp = executed(); trace = out['mechanics_trace']
    reaction = next(r for r in trace if r['type'] == 'elemental_reaction')
    parent = next(r for r in trace if r['type'] == 'elemental_application')
    activation = next(r for r in trace if r['type'] == 'activation')
    if change == 'wrong_owner': reaction['owner'] = 'b'
    elif change == 'wrong_target': reaction['target_id'] = 'different'
    elif change == 'missing_reaction': trace.remove(reaction)
    elif change == 'duplicate_reaction': trace.append(copy.deepcopy(reaction))
    elif change == 'missing_parent': trace.remove(parent)
    elif change == 'duplicate_parent': trace.append(copy.deepcopy(parent))
    elif change == 'other_cycle': reaction['cycle'] = 1
    elif change == 'nearby_time': reaction['t'] += .000001
    elif change == 'duplicate_activation': trace.append(copy.deepcopy(activation))
    else: parent['reaction']['parent_run_id'] = 'wrong-real-parent'
    with pytest.raises(PlanError) as error:
        verify_reaction_em_execution(out, comp)
    assert error.value.reason == 'reaction_em_pending' and error.value.details['result'] is None


def test_unused_source_is_valid_without_fabricated_activation():
    t, r, m = prepared_queue((.2, .3), owner='b')
    out = execute_mechanics(t, r, m)
    comp = [{'profile_id': PROFILE_ID, 'status': 'ready', 'binding_id': 'test', 'rules': m['rules'],
             'source_identity': {'provider': 'b'}, 'app_scope': {'target_id': 'enemy'}}]
    assert verify_reaction_em_execution(out, comp)['activations'] == []
    assert verify_reaction_em_execution({}, []) is None


@pytest.mark.parametrize('change', ['missing_activation', 'missing_queue', 'missing_parent', 'duplicate_parent',
                                  'wrong_frontline', 'wrong_origin', 'nearby_time', 'not_verified'])
def test_two_way_join_requires_actual_completed_parent_and_frontline(change):
    out, comp = executed()
    queue = out['rotation_check']['event_log']
    parent = next(r for r in queue if r['type'] == 'mechanics_hit')
    if change == 'missing_activation':
        out['mechanics_trace'] = [r for r in out['mechanics_trace'] if r['type'] != 'activation']
    elif change == 'missing_queue': out['rotation_check'].pop('event_log')
    elif change == 'missing_parent': queue.remove(parent)
    elif change == 'duplicate_parent': queue.append(copy.deepcopy(parent))
    elif change == 'wrong_frontline': parent['frontline'] = 'b'
    elif change == 'wrong_origin': parent['source_cycle'] = 1
    elif change == 'nearby_time': parent['t'] += .000001
    else: parent['status'] = 'not_verified'
    with pytest.raises(PlanError, match='反应|父命中|在场'):
        verify_reaction_em_execution(out, comp)


def test_actual_queue_joins_source_origin_not_wall_clock_cycle():
    out, comp = executed()
    for row in out['rotation_check']['event_log']:
        if row['type'] == 'mechanics_hit': row['cycle'] = 4
    assert verify_reaction_em_execution(out, comp)['verified_completed_parent_activations'] == 2


def test_no_aura_reaction_is_a_valid_zero_activation_case():
    t, r, m = prepared_queue((.2, .3))
    m['elemental_model']['target']['initial_aura'] = None
    # The actual model uses initial_state only for the separate freeze contract.
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out.get('issues')
    comp = [{'profile_id': PROFILE_ID, 'status': 'ready', 'binding_id': 'test', 'rules': m['rules'],
             'source_identity': {'provider': 'a'}, 'app_scope': {'target_id': 'enemy'}}]
    assert verify_reaction_em_execution(out, comp)['activations'] == []


def test_offfield_reactions_keep_existing_buff_but_do_not_require_refresh():
    from tests.test_mechanics_execution import ability, action
    t, r, m = prepared_queue((.2, .3, 8.2))
    r['abilities'].append(ability('switch_b', 'a', 'action.switch'))
    r['actions'].append(action('leave', 'switch_b', .25, .26, 20, switch_to='b', repeat={'mode': 'once'}))
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out.get('issues')
    comp = [{'profile_id': PROFILE_ID, 'status': 'ready', 'binding_id': 'test', 'rules': m['rules'],
             'source_identity': {'provider': 'a'}, 'app_scope': {'target_id': 'enemy'}}]
    proof = verify_reaction_em_execution(out, comp)
    assert len(proof['activations']) == proof['verified_completed_parent_activations'] == 1


def test_unapproved_reaction_element_is_not_a_silently_ignored_branch():
    out, comp = executed()
    reaction = next(r for r in out['mechanics_trace'] if r['type'] == 'elemental_reaction')
    reaction['reaction_element'] = 'electro'
    with pytest.raises(PlanError):
        verify_reaction_em_execution(out, comp)


def test_completed_application_cannot_lose_both_reaction_and_activation():
    out, comp = executed()
    out['mechanics_trace'] = [r for r in out['mechanics_trace']
                              if r['type'] not in ('activation', 'elemental_reaction')]
    with pytest.raises(PlanError, match='缺少唯一对应的实际反应回调'):
        verify_reaction_em_execution(out, comp)
