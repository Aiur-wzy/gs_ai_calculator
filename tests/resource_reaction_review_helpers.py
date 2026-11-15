"""Small live plans for independent saved-proof and presentation regression tests."""
import copy

from genshin_app.equipment import resource_reaction_receipt as proof
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_engine import mechanics
from tests.test_mechanics_execution import ability, action, second_character
from tests.test_resource_reaction_app import reaction_plan
from tests.test_shared_pool_app_delivery import authorize


def live_case(tmp_path, kind='income'):
    plan = reaction_plan()
    model = plan['mechanics_model']
    if kind == 'zero':
        model['elemental_model']['target']['initial_aura'] = None
    elif kind == 'no-match':
        model['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['melt']
    elif kind == 'excluded':
        next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'refill').update(
            state='excluded', reason='explicit engineering omission')
    elif kind == 'full-pool':
        model['shared_pools']['pools'][0].update(capacity=20, initial={'team': 20})
        model['shared_pools']['consumers'][0]['filter']['elements'] = ['hydro']
    elif kind == 'cooldown':
        model['shared_pools']['operations'][0]['cooldown']['seconds'] = 1
    elif kind == 'failed-prefix':
        second_character(plan['manual_template'], plan['rotation_model'])
        plan['manual_template']['characters'][-1]['stats']['energy_recharge'] = 1
        plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
        plan['rotation_model']['actions'].append(action(
            'later_failure', 'offfield_attempt', .5, .6, 30, repeat={'mode': 'once'}))
    elif kind != 'income':
        raise AssertionError(kind)
    db = tmp_path / 'absent.db'
    value = run_plan_calc(authorize(plan, db), db, capture_display=True)
    assert not db.exists()
    return value


def resign(value):
    value['result_receipt']['resource_reactions'] = proof._proof(value['result'], value['result_receipt'])
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])


def count_engine(monkeypatch):
    calls = []
    original = mechanics.execute_mechanics

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(mechanics, 'execute_mechanics', counted)
    return calls


def bad_complete_prefix(value):
    bad = copy.deepcopy(value)
    assert bad['result']['resource_reactions']['verified_window'] == 'executed_prefix'
    bad['result']['resource_reactions']['verified_window'] = 'complete_reported_input'
    resign(bad)
    return bad
