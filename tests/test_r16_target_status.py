"""Declared statuses do not conflate field existence, aura and reaction facts."""
import copy

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.target_state import evaluate_target_predicate, general_target_sample
from tests.test_elemental_multiaura_v4 import aura, model
from tests.test_general_target_conditions import source
from tests.test_mechanics_execution import EVIDENCE, fixture


def inputs(times=(.2, .3, .5)):
    t, r, m = fixture(times)
    r['actions'][0]['repeat'] = {'mode': 'once'}
    m['elemental_model'] = model()
    m['elemental_model']['profiles'].append({'id': 'none', 'element': 'cryo', 'application': 'none', **EVIDENCE})
    for row in m['hit_events']:
        row['application'] = {'profile_id': 'none', 'target_id': 'enemy', 'landed': True}
    s = source()
    s['effects'][0]['target_predicate'] = {'kind': 'target_status', 'status_id': 'affected.special'}
    m['fixed_effect_sources'] = [s]
    return t, r, m


def event(eid, at, operation='apply', **overrides):
    row = {'id': eid, 't': at, 'order': 9, 'target_id': 'enemy', 'status_id': 'affected.special',
        'operation': operation, 'repeat': {'mode': 'once'}, 'duration': .1, **EVIDENCE, **overrides}
    if operation == 'remove':
        row.pop('duration')
    return row


def checks(out):
    assert out['result'] is not None, out['issues']
    return [row['automatic_value'] for row in out['target_condition_trace']]


def test_same_tick_order_and_half_open_expiry():
    t, r, m = inputs()
    m['target_status_events'] = [event('state', .2)]
    out = execute_mechanics(t, r, m)
    assert checks(out) == [True, False, False]
    assert out['target_status']['trace'][0]['evidence_tier'] == 'declared_status_execution'
    m['target_status_events'][0]['order'] = 11
    assert checks(execute_mechanics(t, r, m)) == [False, False, False]


def test_refresh_replaces_end_remove_and_other_target_do_not_leak():
    t, r, m = inputs()
    m['target_status_events'] = [event('start', .1, duration=.2), event('refresh', .25, 'refresh', duration=.3),
                               event('other', .29, 'remove', target_id='other')]
    assert checks(execute_mechanics(t, r, m)) == [True, True, True]
    m['target_status_events'].append(event('remove', .4, 'remove'))
    assert checks(execute_mechanics(t, r, m)) == [True, True, False]


def test_replacement_reaction_alias_triggers_once_and_keeps_actual_identity():
    from genshin_engine.target_status import TargetStatusRuntime
    rule = {key: value for key, value in event('from.reaction', 0).items()
            if key not in ('t', 'order', 'repeat')}
    rule['reaction_types'] = ['crystallize', 'lunar_crystallize']
    runtime = TargetStatusRuntime({'target_status_rules': [rule]})
    runtime.reactions([{'type': 'lunar_crystallize', 'run_id': '0:crystal',
        'reaction_trigger_tags': ['crystallize', 'lunar_crystallize']}],
        target_id='enemy', time=100000, cycle=0)
    assert len(runtime.trace) == 1
    assert runtime.trace[0]['reaction_type'] == 'lunar_crystallize'
    assert runtime.trace[0]['evidence_tier'] == 'declared_reaction_status_execution'


def test_declared_status_without_elemental_model_keeps_aura_unknown():
    t, r, m = inputs()
    m.pop('elemental_model')
    for descriptor in m['hit_events']:
        descriptor.pop('application')
        descriptor['target_id'] = 'enemy'
    m['target_status_events'] = [event('start', .1, duration=.3)]
    out = execute_mechanics(t, r, m)
    assert checks(out) == [True, True, False]
    assert out['target_condition_trace'][0]['evidence_tier'] == 'declared_status_execution'
    sample = next(iter(out['target_state_samples'].values()))
    assert sample['status'] == 'pending' and sample['auras'] is None
    assert sample['status_observations']['affected.special']['active'] is True
    assert evaluate_target_predicate({'kind': 'target_aura_in', 'elements': ['cryo']}, sample) is None


def test_descriptor_target_cannot_disagree_with_real_application_target():
    t, r, m = inputs()
    m['hit_events'][0]['target_id'] = 'another'
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid'
    assert any(row['code'] == 'target_identity_conflict' for row in out['issues'])


def test_real_reaction_triggers_status_but_not_its_own_precontact_condition():
    t, r, m = inputs((.2, .2, .4))
    m['elemental_model'] = model([aura('pyro', .8)])
    m['elemental_model']['profiles'].append({'id': 'none', 'element': 'cryo', 'application': 'none', **EVIDENCE})
    m['hit_events'][0]['application']['profile_id'] = 'cryo'
    m['target_status_rules'] = [{'id': 'reaction.rule', 'target_id': 'enemy', 'status_id': 'affected.special',
        'reaction_types': ['melt'], 'operation': 'refresh', 'duration': .1, **EVIDENCE}]
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert checks(out) == [False, True, False]
    assert out['target_status']['trace'][0]['reaction_type'] == 'melt'
    assert out['target_status']['trace'][0]['evidence_tier'] == 'declared_reaction_status_execution'
    assert (t, r, m) == before


def test_unknown_is_pending_instead_of_false_and_reserved_freeze_cannot_be_overwritten():
    t, r, m = inputs((.2,))
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and out['status'] == 'pending'
    m['target_status_events'] = [event('state', .1, status_id='element_freeze')]
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid' and any(i['code'] == 'reserved_id' for i in out['issues'])


def test_pure_samples_label_before_and_after_without_mutating_runtime():
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.target_status import TargetStatusRuntime
    elemental = ElementalRuntime(model())
    operation = event('state', .1)
    statuses = TargetStatusRuntime({'target_status_events': [operation]})
    args = {'event_id': 'probe', 'run_id': '0:probe', 'cycle': 0, 'time': 100000, 'order': 1, 'status_runtime': statuses}
    before = general_target_sample(elemental, **args)
    statuses.apply(operation, time=100000, event_id='0:state')
    after = general_target_sample(elemental, **args, phase='after_application')
    predicate = {'kind': 'target_status', 'status_id': 'affected.special'}
    assert evaluate_target_predicate(predicate, before) is False
    assert evaluate_target_predicate(predicate, after) is True
    assert after['phase'] == 'after_application'
