"""A weapon assumption can be authored with the existing charge restore input."""

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_elemental_multiaura_v4 import model
from tests.test_mechanics_execution import EVIDENCE, action, fixture


def weapon_scenario(*, first_landed, assume_success, extra_cast=False):
    template, rotation, mechanics = fixture((1.2, 2.2, 30.2))
    template['loop_duration'] = rotation['period'] = 40
    rotation['abilities'][0]['cooldown'] = {
        'group': 'skill', 'duration': 15, 'charges': 2,
        'recovery': 'sequential', 'start': 'action_start',
    }
    rotation['initial_cooldowns'] = [{'character': 'a', 'group': 'skill', 'ready_in': []}]
    casts = [0, 1, 2, 30, 31] + ([32] if extra_cast else [])
    rotation['actions'] = [
        {**action(f'cast.{i}', start=t, end=t+.05, order=i), 'repeat': {'mode': 'once'}}
        for i, t in enumerate(casts)
    ]
    mechanics['elemental_model'] = model(freezable=False)
    for i, descriptor in enumerate(mechanics['hit_events']):
        descriptor.update(action_id=f'cast.{i+1}', application={
            'profile_id': 'cryo', 'target_id': 'enemy', 'landed': first_landed or i != 0,
        })
    # This fixture authors the weapon's 16-second opportunity window. Only the
    # declared eligible hits emit a one-charge restore. The engine checks the
    # skill cooldown and charge count without inferring the weapon passive.
    if first_landed and assume_success:
        rotation['events'] = [{
            'id': f'restore.{i}', 'type': 'cooldown_change', 'character': 'a',
            'group': 'skill', 'operation': 'restore', 'amount': 1, 't': t,
            'order': 20+i, 'repeat': {'mode': 'once'}, **EVIDENCE,
        } for i, t in enumerate((1.2, 30.2))]
    return template, rotation, mechanics


@pytest.mark.parametrize(('landed', 'assume_success'), [(False, True), (True, False)])
def test_missed_hit_or_explicit_no_proc_has_no_charge_restore(landed, assume_success):
    out = execute_mechanics(*weapon_scenario(first_landed=landed, assume_success=assume_success))
    rows = out['rotation_check']['event_log']
    assert rows, out['issues']
    first_hit = next(row for row in rows if row['id'] == 'h0')
    assert first_hit['status'] == ('passed' if landed else 'missed')
    assert not [row for row in rows if row['type'] == 'cooldown_change']
    assert next(row for row in rows if row['id'] == 'cast.2')['status'] == 'failed'


@pytest.mark.parametrize('extra_cast', [False, True])
def test_assumed_proc_restores_one_charge_per_declared_eligible_hit(extra_cast):
    out = execute_mechanics(*weapon_scenario(first_landed=True, assume_success=True, extra_cast=extra_cast))
    rows = out['rotation_check']['event_log']
    assert rows, out['issues']
    restores = [row for row in rows if row['type'] == 'cooldown_change']
    assert [(row['t'], row['status']) for row in restores] == [(1.2, 'passed'), (30.2, 'passed')]
    assert next(row for row in rows if row['id'] == 'h1')['status'] == 'passed'
    assert next(row for row in rows if row['id'] == 'cast.4')['status'] == 'passed'
    if extra_cast:
        assert next(row for row in rows if row['id'] == 'cast.5')['status'] == 'failed'
    else:
        assert out['result'] is not None, out['issues']
