"""Target-qualified attribute effects remain per-damage roots, never panels."""
from __future__ import annotations

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.special_reactions import (
    direct_lunar_damage_profile_content_hash,
    direct_stellar_damage_profile_content_hash,
)
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_multistat_runtime import run, setup, value
from tests.test_direct_stellar_damage_contract import direct_stellar_profile
from tests.test_elemental_multiaura_v4 import aura, model
from tests.test_general_target_conditions import source
from tests.test_mechanics_execution import add, rule
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
)
from tests.test_special_reactions import direct_lunar_profile


def case(stat='atk', percent=False, amount=None):
    t, r, m = setup()
    t['hits'][0]['scaling_stat'] = stat
    m['elemental_model'] = model([aura('cryo', .7)], freezable=False)
    m['hit_events'][0]['application'] = {'profile_id': 'cryo', 'target_id': 'enemy', 'landed': True}
    m['fixed_effect_sources'] = [source()]
    effect = m['fixed_effect_sources'][0]['effects'][0]
    effect['benefit_type_id'] = 'benefit.stat_' + ('pct.' if percent else 'add.') + stat
    effect['value'] = amount if amount is not None else .2 if percent else 100
    return t, r, m


@pytest.mark.parametrize('stat,base,total', [('atk', 50, 100), ('hp', 15000, 30000), ('def', 500, 1000)])
@pytest.mark.parametrize('percent', [False, True])
def test_actual_condition_flat_and_percent_share_final_core_attribute_proof(stat, base, total, percent):
    out = run(*case(stat, percent))
    wanted = total + (.2 * base if percent else 100)
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == wanted
    rows = [c for rec in out['attribute_ledger']['receipts'] for c in rec['contributions'] if c.get('condition_ref')]
    assert rows and all(c['decision'] == 'included' for c in rows)
    assert all(c['condition_ref'] in out['attribute_ledger']['condition_sources'] for c in rows)
    assert all(c['evaluated_value'] == (.2 * base if percent else 100) for c in rows)


def test_negative_condition_delta_is_valid_but_not_double_applied():
    out = run(*case('hp', amount=-1000))
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == 29000


def test_conditional_hp_does_not_leak_into_later_ordinary_conversion():
    t, r, m = case('hp', amount=1000)
    second = dict(t['hits'][0], id='second', t=.4, scaling_stat='atk')
    t['hits'].append(second)
    m['hit_events'].append({'hit_id': 'second', 'action_id': 'cast', 'order': 20,
        'application': {'profile_id': 'cryo', 'target_id': 'enemy', 'landed': True}})
    add(m, rule('ordinary', 'benefit.stat_add.atk', value('hp', .01), domain='provider'))
    classify(m, 'ordinary', 'a', 'excluded')
    out = run(t, r, m)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [31000, 400]
    assert {s['source_value'] for s in out['sampling_trace'] if s.get('rule_id') == 'ordinary'} == {30000}


def test_target_qualified_em_does_not_rewrite_swirl_creation_panel():
    t, r, m = setup()
    r['actions'][0]['repeat'] = {'mode': 'once'}
    t['hits'][0]['element'] = 'anemo'
    m['elemental_model'] = model([aura('cryo', 1.5)], freezable=False)
    m['hit_events'][0]['application'] = {'profile_id': 'anemo', 'target_id': 'enemy', 'landed': True}
    m['fixed_effect_sources'] = [source()]
    src = m['fixed_effect_sources'][0]
    src['damage_systems'].append('transformative')
    src['effects'][0].update(benefit_type_id='benefit.em.all', value=100)
    src['effects'][0]['filter']['event'] = 'reaction_damage'
    out = run(t, r, m)
    hit = next(h for h in out['result']['hits'] if h['damage_system'] == 'transformative')
    assert hit['attribute_inputs']['em'] == 300
    provider = next(h for h in out['resolved_template']['hits'] if h['damage_system'] == 'transformative')
    assert provider['extensions']['elemental']['provider_stat_sample']['value'] == 200


def test_damage_only_conditional_source_cannot_mint_energy_recharge():
    t, r, m = case()
    m['fixed_effect_sources'][0]['effects'][0].update(benefit_type_id='benefit.energy_recharge.all', value=.5)
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(i['code'] == 'unsupported' and i['path'].endswith('.benefit_type_id') for i in out['issues'])


@pytest.mark.parametrize('system', ['stellar', 'lunar'])
@pytest.mark.parametrize('stat,total', [('hp', 30000), ('def', 1000), ('em', 200)])
def test_native_direct_special_condition_attribute_uses_same_qualified_final_root(system, stat, total):
    t, r, m = _application_fixture('electro', initial=[aura('cryo' if system == 'stellar' else 'hydro', 1.5, 'a')])
    t['characters'][0]['stats'].update(hp=30000, base_hp=15000, base_def=500, em=200, **{'def': 1000})
    r['abilities'][0].update(variant='tap', effective_level=10)
    m['attribute_model'] = setup()[2]['attribute_model']
    m['attribute_model']['initial_basis'] = [row for row in m['attribute_model']['initial_basis'] if row['actor'] == 'a']
    reaction = 'stellar_superconduct' if system == 'stellar' else 'lunar_electro'
    _enable(m, reaction)
    profile = direct_stellar_profile(character_id='a', ability_id='e') if system == 'stellar' else direct_lunar_profile(reaction)
    profile['parameters']['scaling_stat'] = stat
    profile['ability_binding'].update(character_id='a', ability_id='e', variant_id='tap', effective_level=10)
    fingerprint = direct_stellar_damage_profile_content_hash if system == 'stellar' else direct_lunar_damage_profile_content_hash
    profile['profile_content_hash'] = fingerprint(profile)
    m['special_reaction_events'] = [{'id': 'direct', 'kind': 'direct_' + system + '_damage',
        'action_id': 'cast', 'target_id': 'enemy', 'area_id': 'battlefield.primary', 't': 1, 'order': 20,
        'ability_profile': profile, 'status': 'assumed', 'source': 'Anonymous qualified root fixture'}]
    m['fixed_effect_sources'] = [source()]
    effect = m['fixed_effect_sources'][0]['effects'][0]
    effect.update(benefit_type_id='benefit.em.all' if stat == 'em' else 'benefit.stat_add.' + stat, value=100)
    effect['filter']['event'] = 'reaction_damage'
    effect['target_predicate']['elements'] = ['cryo', 'hydro']
    out = run(t, r, m)
    direct = next(h for h in out['resolved_template']['hits']
        if h.get('extensions', {}).get('special_reaction', {}).get('reaction_base') is False)
    actual = next(h for h in out['result']['hits'] if h['id'] == direct['id'])
    assert actual['attribute_inputs']['scaling'] == total + 100
    assert any(row['event_id'] == direct['id'] and row['applied'] for row in out['target_condition_trace'])
