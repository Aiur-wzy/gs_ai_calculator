"""V4 anonymous five-stat integration and adversarial numerical oracles."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_stat_runtime import setup as legacy_setup
from tests.test_attribute_stat_runtime import value
from tests.test_mechanics_execution import add, condition, rule


def setup(times=(.2,)):
    t, r, m = legacy_setup(times)
    for c in t['characters']:
        c['stats'].update(hp=30000, base_hp=15000, **{'def': 1000, 'base_def': 500, 'energy_recharge': 1})
    m['attribute_model'] = {'contract': 'attribute-ledger-v4', 'initial_basis': [
        {'actor': c['id'], 'stat': stat, 'included_components': [],
         'nonextra': {'mode': 'panel_total', 'status': 'assumed', 'source': 'Synthetic ordinary initial panel'}}
        for c in t['characters'] for stat in ('atk', 'hp', 'def', 'em', 'energy_recharge')], 'effect_classifications': []}
    return t, r, m


def run(t, r, m):
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert (t, r, m) == before
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert out['result'] is not None
    return out


def hp_to_atk(t, r, m, *, sample='current'):
    add(m, rule('source_hp', 'benefit.stat_pct.hp', .4, owner='b', domain='provider',
                beneficiaries={'mode': 'self', 'scope': 'locked'}),
        rule('convert', 'benefit.stat_add.atk', value('hp', .01, sample=sample), owner='b', domain='provider',
             beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}))
    classify(m, 'source_hp', 'b', 'included')
    classify(m, 'convert', 'b', 'excluded')


def test_dynamic_hp_to_frontline_atk_consumes_provider_and_base_once():
    t, r, m = setup((.2, .4))
    hp_to_atk(t, r, m)
    condition(m, 'later', .3)
    add(m, rule('later_hp', 'benefit.stat_add.hp', 5000, owner='b', domain='provider',
                beneficiaries={'mode': 'self', 'scope': 'locked'},
                trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['later']}))
    classify(m, 'later_hp', 'b', 'included')
    out = run(t, r, m)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [460, 510]
    assert {s['source_value'] for s in out['sampling_trace'] if s.get('rule_id') == 'convert' and s['cycle'] == 0} == {36000, 41000}


def test_trigger_hp_preserves_pre_activation_state_instead_of_new_dynamic_hp():
    t, r, m = setup()
    hp_to_atk(t, r, m, sample='trigger')
    out = run(t, r, m)
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == 400


@pytest.mark.parametrize('stat,base,total', [('hp', 15000, 30000), ('def', 500, 1000)])
def test_hp_def_scaling_and_percentage_projection_match_actual_core(stat, base, total):
    t, r, m = setup()
    t['hits'][0]['scaling_stat'] = stat
    add(m, rule('pct', 'benefit.stat_pct.' + stat, .2, domain='provider'))
    classify(m, 'pct', 'a', 'included')
    out = run(t, r, m)
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == total + .2 * base
    rows = [row for receipt in out['attribute_ledger']['receipts'] for row in receipt['contributions']
            if row.get('application_base_value') == base]
    assert rows and all(row['raw_evaluated_value'] == .2 for row in rows)


@pytest.mark.parametrize('alias,expected', [('base_hp', 15000), ('base_def', 500)])
def test_base_alias_cannot_read_buffed_total(alias, expected):
    t, r, m = setup()
    add(m, rule('reader', 'benefit.base_add.all', value(alias, 1), domain='provider'))
    out = run(t, r, m)
    assert next(s for s in out['sampling_trace'] if s.get('rule_id') == 'reader')['source_value'] == expected


def test_nonextra_excludes_conversion_while_total_can_read_unknown_classification():
    t, r, m = setup()
    add(m, rule('hp', 'benefit.stat_add.hp', 1000, domain='provider'),
        rule('reader', 'benefit.stat_add.atk', value('hp', .01, basis='nonextra'), domain='provider'))
    classify(m, 'hp', 'a', 'excluded'); classify(m, 'reader', 'a', 'unknown')
    assert run(t, r, m)['result']['hits'][0]['attribute_inputs']['scaling'] == 400
    m['attribute_model']['effect_classifications'][0]['nonextra_inclusion'] = 'unknown'
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and any(i['code'] == 'attribute_classification_unknown' for i in out['issues'])
    m['rules'][1]['effects'][0]['value']['basis'] = 'total'
    assert run(t, r, m)['result']['hits'][0]['attribute_inputs']['scaling'] == 410


def test_cross_stat_true_cycle_is_not_solved_with_caps_and_zero_factor():
    t, r, m = setup()
    add(m, rule('atk', 'benefit.stat_add.atk', value('hp', 0, max=0), domain='provider'),
        rule('hp', 'benefit.stat_add.hp', value('atk', 1), domain='provider'))
    classify(m, 'atk', 'a', 'excluded'); classify(m, 'hp', 'a', 'excluded')
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and any(i['code'] == 'attribute_dependency_cycle' for i in out['issues'])


def test_unconsumed_other_actor_invalid_hp_closure_does_not_block():
    t, r, m = setup()
    add(m, rule('unrelated', 'benefit.stat_add.hp', value('hp', 1), owner='b', domain='provider',
                beneficiaries={'mode': 'self', 'scope': 'locked'}))
    classify(m, 'unrelated', 'b', 'unknown')
    assert run(t, r, m)['result']['hits'][0]['attribute_inputs']['scaling'] == 100


def test_er_ratio_is_not_percent_points_and_resource_uses_same_total_once():
    t, r, m = setup()
    for c in r['characters']:
        c['initial_energy'] = 0
    r['events'] = [
        {'id': 'ball', 'type': 'energy_spawn', 'source_kind': 'skill', 't': .25, 'order': 0, 'repeat': {'mode': 'each_cycle'},
         'action_id': 'cast', 'element': 'cryo', 'particle_type': 'particle', 'count': 1, 'lost_count': 0,
         'status': 'assumed', 'source': 'Synthetic particle'},
        {'id': 'catch', 'type': 'energy_credit', 'credit_at': .3, 'order': 0, 'repeat': {'mode': 'each_cycle'},
         'action_id': 'cast', 'spawn_id': 'ball', 'count': 1, 'status': 'assumed', 'source': 'Synthetic catch'}]
    r['energy_recharge_windows'] = [{'id': 'window', 'character': 'a', 'start': 0, 'end': 1,
         'delta': .2, 'repeat': {'mode': 'once'}, 'status': 'assumed', 'source': 'Synthetic +20pp ER'}]
    add(m, rule('er', 'benefit.energy_recharge.all', value('hp', .00001), domain='provider'),
        rule('atk', 'benefit.stat_add.atk', value('energy_recharge', 100), domain='provider'))
    classify(m, 'er', 'a', 'included'); classify(m, 'atk', 'a', 'excluded')
    out = run(t, r, m)
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == 250
    credit = next(e for e in out['rotation_check']['event_log'] if e['id'] == 'catch')
    a = next(e for e in credit['energy'] if e['character'] == 'a')
    assert a['energy_recharge'] == pytest.approx(1.5)
    assert a['gain'] == pytest.approx(4.5)
    assert any(s.get('consumer') == 'energy_credit' and s.get('attribute_receipt_id') for s in out['sampling_trace'])


@pytest.mark.parametrize('reverse,expected', [(False, 460), (True, 400)])
def test_multistat_callback_order_uses_actual_hp_footprint(reverse, expected):
    t, r, m = setup((.2, .4))
    trigger = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    add(m, rule('hp', 'benefit.stat_pct.hp', .4, trigger=trigger, domain='provider'),
        rule('atk', 'benefit.stat_add.atk', value('hp', .01, sample='trigger'), trigger=trigger, domain='provider'))
    classify(m, 'hp', 'a', 'included'); classify(m, 'atk', 'a', 'excluded')
    before, after = ('atk', 'hp') if reverse else ('hp', 'atk')
    m['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v2', 'members': ['hp', 'atk'],
        'edges': [{'before_rule_id': before, 'after_rule_id': after, 'assumption_id': 'order'}],
        'assumptions': [{'id': 'order', 'note': 'Synthetic explicit callback order'}]}
    out = run(t, r, m)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [100, expected]
    assert any(d['stat'] == 'hp' for p in out['attribute_callbacks']['plans']
               for n in p.get('materialized_input', {}).get('nodes', []) for rd in n['reads'] for d in rd['current_dependencies'])


def test_negative_hp_delta_allowed_but_negative_total_is_not():
    t, r, m = setup()
    t['hits'][0]['scaling_stat'] = 'hp'
    add(m, rule('hp', 'benefit.stat_add.hp', -1000, domain='provider'))
    classify(m, 'hp', 'a', 'included')
    assert run(t, r, m)['result']['hits'][0]['attribute_inputs']['scaling'] == 29000
    m['rules'][0]['effects'][0]['value'] = -40000
    out = execute_mechanics(t, r, m)
    assert out['result'] is None


def test_missing_hp_base_is_only_required_for_percentage():
    t, r, m = setup()
    del t['characters'][0]['stats']['base_hp']
    hp_to_atk(t, r, m)
    # Provider b still has its base, so an unrelated a base is not required.
    assert run(t, r, m)['result']['hits'][0]['attribute_inputs']['scaling'] == 460
    del t['characters'][1]['stats']['base_hp']
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and any(i['code'] == 'attribute_basis_missing' for i in out['issues'])


@pytest.mark.parametrize('hit_time,known', [(.05, False), (.3, True)])
def test_named_hp_snapshot_is_exact_and_future_sample_never_falls_back(hit_time, known):
    from tests.test_snapshot_captures import capture
    t, r, m = setup((hit_time,))
    r['actions'][0]['occupation_end'] = .4
    m['snapshot_captures'] = [capture(offset=.2)]
    condition(m, 'late', .25)
    add(m, rule('late', 'benefit.stat_add.hp', 10000, owner='b', domain='provider',
                beneficiaries={'mode': 'self', 'scope': 'locked'},
                trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['late']}),
        rule('read', 'benefit.stat_add.atk', value('hp', .01, sample='event', event_id='sample'), owner='b', domain='provider',
             beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}))
    classify(m, 'late', 'b', 'included'); classify(m, 'read', 'b', 'excluded')
    out = execute_mechanics(t, r, m)
    if known:
        assert out['result']['hits'][0]['attribute_inputs']['scaling'] == 400
        assert any(s.get('rule_id') == 'read' and s['source_value'] == 30000 for s in out['sampling_trace'])
    else:
        assert out['result'] is None
        assert any(i['code'] in ('attribute_frame_missing', 'snapshot_event_unexecuted') for i in out['issues'])


def test_historical_callback_read_does_not_require_order_against_current_hp_writer():
    from tests.test_snapshot_captures import capture
    t, r, m = setup((.2, .4))
    r['actions'][0]['occupation_end'] = .5
    m['snapshot_captures'] = [capture(offset=.1)]
    trigger = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    add(m, rule('hp', 'benefit.stat_add.hp', 10000, trigger=trigger, domain='provider'),
        rule('atk', 'benefit.stat_add.atk', value('hp', .01, sample='event', event_id='sample'), trigger=trigger, domain='provider'))
    classify(m, 'hp', 'a', 'included'); classify(m, 'atk', 'a', 'excluded')
    m['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v2', 'members': ['hp', 'atk'],
                               'edges': [], 'assumptions': []}
    out = run(t, r, m)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [100, 400]
    reads = [rd for p in out['attribute_callbacks']['plans'] for n in p['materialized_input']['nodes'] for rd in n['reads']]
    assert reads and all(rd['sample_scope'] == 'historical' and not rd['current_dependencies'] for rd in reads)


def test_transitive_cross_stat_outsider_is_not_mistaken_for_an_independent_writer():
    t, r, m = setup((.2, .4))
    trigger = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    add(m, rule('hp_em', 'benefit.em.all', value('hp', .01), domain='provider'),
        rule('hp', 'benefit.stat_add.hp', 10000, trigger=trigger, domain='provider'),
        rule('atk', 'benefit.stat_add.atk', value('em', 1, sample='trigger'), trigger=trigger, domain='provider'))
    classify(m, 'hp_em', 'a', 'included'); classify(m, 'hp', 'a', 'included'); classify(m, 'atk', 'a', 'excluded')
    m['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v2', 'members': ['atk'], 'edges': [], 'assumptions': []}
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and any(i['code'] == 'causal_nonmember_interference' for i in out['issues'])
    m['attribute_callbacks'].update(members=['hp', 'atk'],
        edges=[{'before_rule_id': 'hp', 'after_rule_id': 'atk', 'assumption_id': 'order'}],
        assumptions=[{'id': 'order', 'note': 'Synthetic writer before transitive read'}])
    out = run(t, r, m)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [100, 700]


def test_double_swirl_packet_claims_both_real_occurrences_and_refreshes_once():
    from tests.test_elemental_multiaura_v4 import aura, model
    t, r, m = setup()
    r['actions'][0]['repeat'] = {'mode': 'once'}
    t['hits'][0]['element'] = 'anemo'
    m['elemental_model'] = model([aura('hydro', .2), aura('cryo', .7)], freezable=False)
    m['hit_events'][0]['application'] = {'profile_id': 'anemo', 'target_id': 'enemy', 'landed': True}
    add(m, rule('share', 'benefit.em.all', 50, domain='provider',
                beneficiaries={'mode': 'characters', 'characters': ['b'], 'scope': 'locked'},
                trigger={'event': 'elemental_reaction', 'phase': 'post', 'reaction_types': ['swirl']}))
    classify(m, 'share', 'a', 'included')
    m['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v2', 'members': ['share'], 'edges': [], 'assumptions': []}
    out = run(t, r, m)
    trace = [row for row in out['attribute_callbacks']['trace'] if row['type'] == 'callback_committed']
    reactions = [row for row in out['mechanics_trace'] if row.get('type') == 'elemental_reaction']
    assert len(trace) == len(reactions) == 2
    assert len({row['callback_run_id'] for row in trace}) == 2
    assert len(out['attribute_callbacks']['plans'][0]['materialized_input']['nodes']) == 2
    values = [r['value'] for r in out['attribute_ledger']['receipts']
              if r['query']['actor'] == 'b' and r['query']['stat'] == 'em' and r.get('status') == 'known']
    # A refresh source never becomes a two-layer +100 EM delivery.
    assert not values or max(values) == 70


def test_new_report_boundary_includes_origin_zero_settlement_at_period_with_attribute_proof():
    t, r, m = setup((10,))
    r['actions'][0]['repeat'] = {'mode': 'once'}
    r['damage_report'] = {'contract': 'damage-report-window-v1', 'mode': 'explicit', 'end': 10}
    out = run(t, r, m)
    assert [h['id'] for h in out['result']['hits']] == ['h0']
    assert out['result']['hits'][0]['attribute_inputs']['scaling'] == 100
    assert out['result']['summary']['dps'] == pytest.approx(4.5)
    assert any(receipt['query']['purpose'] == 'damage_projection' for receipt in out['attribute_ledger']['receipts'])
