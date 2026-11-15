"""Compact traces retain exact evidence without source-by-hit state copies."""
import copy
import json

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.target_condition_evidence import resolve_condition_observation
from tests.test_elemental_multiaura_v4 import aura, model
from tests.test_general_target_conditions import source
from tests.test_mechanics_execution import fixture


def setup():
    t, r, m = fixture((.2, .3))
    r['actions'][0]['repeat'] = {'mode': 'once'}
    m['elemental_model'] = model([aura('cryo', .8)])
    for row in m['hit_events']:
        row['application'] = {'profile_id': 'cryo', 'target_id': 'enemy', 'landed': True}
    m['fixed_effect_sources'] = []
    for i in range(16):
        declaration = source()
        declaration['id'] = f'source.{i}'
        declaration['source_id'] = f'part.{i}'
        declaration['source'] = 'long original knowledge preserved once ' * 300
        m['fixed_effect_sources'].append(declaration)
    return t, r, m


def test_shared_catalog_one_observation_per_event_and_exact_expansion():
    inputs = setup()
    out = execute_mechanics(*inputs)
    assert out['result'] is not None, out['issues']
    assert len(out['target_state_samples']) == 2
    checks = out['target_condition_trace']
    assert len(checks) == 32
    assert len({r['target_sample_ref'] for r in checks}) == 2
    assert all('state' not in r.get('target_sample', {}) and 'source' not in r for r in checks)
    expanded = resolve_condition_observation(checks[0], out['target_state_samples'], out['fixed_effect_sources'])
    assert expanded['source'] == inputs[2]['fixed_effect_sources'][0]['source']
    assert expanded['target_sample']['state']['auras'][0]['element'] == 'cryo'
    assert expanded['automatic_value'] is True
    compact = len(json.dumps(checks)) + len(json.dumps(out['target_state_samples']))
    legacy = len(json.dumps([resolve_condition_observation(r, out['target_state_samples'], out['fixed_effect_sources']) for r in checks]))
    assert compact < legacy / 8
    baseline = copy.deepcopy(inputs)
    baseline[2].pop('fixed_effect_sources')
    assert out['elemental_trace'] == execute_mechanics(*baseline)['elemental_trace']


def test_structurally_unmatched_source_never_samples_unknown_target():
    t, r, m = fixture((.2,))
    s = source()
    s['effects'][0]['filter']['elements'] = ['pyro']
    m['fixed_effect_sources'] = [s]
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert 'target_state_samples' not in out
    assert out['target_condition_trace'][0]['reason'] == 'filter_mismatch'
    assert 'target_sample_ref' not in out['target_condition_trace'][0]
