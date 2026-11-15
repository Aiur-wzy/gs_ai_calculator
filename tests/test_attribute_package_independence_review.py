"""Independent public-entry guards for actual, rather than possible, closures."""
import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_multistat_runtime import run, setup, value
from tests.test_elemental_multiaura_v4 import aura, model
from tests.test_mechanics_execution import add, condition, rule


def packet_case():
    t, r, m = setup()
    r['actions'][0]['repeat'] = {'mode': 'once'}
    t['hits'][0]['element'] = 'anemo'
    m['elemental_model'] = model([aura('hydro', .2), aura('cryo', .7)], freezable=False)
    m['hit_events'][0]['application'] = {'profile_id': 'anemo', 'target_id': 'enemy', 'landed': True}
    return t, r, m


def gift(m, benefit='benefit.stat_add.hp', amount=100):
    add(m, rule('gift', benefit, amount, domain='provider',
        beneficiaries={'mode': 'characters', 'characters': ['b'], 'scope': 'locked'},
        trigger={'event': 'elemental_reaction', 'phase': 'post', 'reaction_types': ['swirl']}))
    classify(m, 'gift', 'a', 'included')


def converter(m, *, locked=False):
    condition(m, 'future', 5)
    add(m, rule('converter', 'benefit.em.all', value('hp', .01), owner='b', domain='provider',
        beneficiaries={'mode': 'frontline', 'scope': 'locked'} if locked else
                      {'mode': 'characters', 'characters': ['a'], 'scope': 'locked'},
        trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['future']}))
    classify(m, 'converter', 'b', 'included')
    if locked:
        row = next(s for s in m['initial_state']['rules'] if s['rule_id'] == 'converter')
        row['instances'] = [{'beneficiaries': ['b'], 'remaining': [1]}]


def test_future_inactive_hp_to_trigger_em_does_not_reject_current_independent_gift():
    t, r, m = packet_case()
    gift(m)
    baseline = run(t, r, m)
    converter(m)
    out = run(t, r, m)
    assert out['result']['summary']['total_damage'] == baseline['result']['summary']['total_damage']


def test_locked_other_recipient_is_not_the_whole_execution_possible_recipient_set():
    t, r, m = packet_case()
    gift(m)
    converter(m, locked=True)
    out = run(t, r, m)
    assert len(out['result']['hits']) == 3


def test_independent_ordinary_er_write_is_recognized_as_a_real_scalar_output():
    t, r, m = packet_case()
    gift(m, 'benefit.energy_recharge.all', .2)
    out = run(t, r, m)
    assert len(out['result']['hits']) == 3


@pytest.mark.parametrize('source_stat,benefit,amount,factor', [
    ('hp', 'benefit.stat_add.hp', 100, .01),
    ('energy_recharge', 'benefit.energy_recharge.all', .2, 100),
])
def test_actual_active_conversion_keeps_cross_actor_current_feedback_pending(source_stat, benefit, amount, factor):
    t, r, m = packet_case()
    gift(m, benefit, amount)
    add(m, rule('current', 'benefit.em.all', value(source_stat, factor), owner='b', domain='provider',
        beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}))
    classify(m, 'current', 'b', 'included')
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    pending = next(i for i in out['issues'] if i['code'] == 'freeze_package_effect_ambiguous')
    assert {'actor': 'b', 'stat': source_stat, 'basis': 'total'} in pending['attribute_reads']


def test_same_packet_future_activation_is_in_dependency_closure():
    t, r, m = packet_case()
    gift(m)
    add(m, rule('same_packet', 'benefit.em.all', value('hp', .01), owner='b', domain='provider',
        beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'},
        trigger={'event': 'elemental_reaction', 'phase': 'post', 'reaction_types': ['swirl']}))
    classify(m, 'same_packet', 'b', 'included')
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    assert any(i['code'] == 'freeze_package_effect_ambiguous' for i in out['issues'])


def test_explicit_nonextra_exclusion_does_not_turn_into_a_false_total_dependency():
    t, r, m = packet_case()
    gift(m)
    m['attribute_model']['effect_classifications'][0]['nonextra_inclusion'] = 'excluded'
    add(m, rule('nonextra', 'benefit.em.all', value('hp', .01, basis='nonextra'), owner='b', domain='provider',
        beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}))
    classify(m, 'nonextra', 'b', 'included')
    assert len(run(t, r, m)['result']['hits']) == 3
