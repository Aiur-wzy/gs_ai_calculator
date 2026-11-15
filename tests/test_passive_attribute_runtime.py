"""Fresh database-compiled reads on the actual queue, not copied constants."""
from __future__ import annotations

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import classify, conversion
from tests.test_mechanics_execution import add, condition, rule, second_character
from tests.test_passive_attribute_source import compile_source
from tests.test_snapshot_captures import capture
from tests.test_swirl_dynamic_v3 import check_sample_projection, sampled, setup

FACTOR = .00039999998989515007


def case():
    t, r, m = setup((.2, .4, .65, .8, .9, .95), element='cryo')
    second_character(t, r)
    t['characters'][0]['stats']['em'] = 200
    t['characters'][1]['stats']['em'] = 500
    m['attribute_model'] = {'contract': 'attribute-ledger-v2', 'initial_basis': [
        {'actor': 'a', 'included_components': [], 'nonextra': {'mode': 'explicit', 'value': 100, 'status': 'assumed', 'source': 'Known fixture composition'}},
        {'actor': 'b', 'included_components': [], 'nonextra': {'mode': 'panel_total', 'status': 'assumed', 'source': 'Known fixture composition'}}],
        'effect_classifications': []}
    m['elemental_model']['profiles'].append({'id': 'no_cryo', 'element': 'cryo', 'application': 'none',
        'status': 'assumed', 'source': 'Synthetic no application contact'})
    for i in (1, 2, 4, 5):
        t['hits'][i]['element'] = 'cryo'
        m['hit_events'][i]['application'] = {'profile_id': 'no_cryo', 'target_id': 'enemy', 'landed': True}
    r['actions'][0]['occupation_end'] = .5
    m['snapshot_captures'] = [capture(offset=.3)]
    m['hit_events'][4]['self_snapshot_event'] = 'sample'
    condition(m, 'ordinary-start', .1)
    ordinary = rule('ordinary', 'benefit.em.all', 50, duration=.5,
        beneficiaries={'mode': 'self', 'scope': 'locked'},
        trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['ordinary-start']})
    shared = conversion('share', owner='b', recipient='a', factor=.2)
    add(m, ordinary, shared, *compile_source()['rules'])
    classify(m, 'ordinary', 'a', 'included')
    classify(m, 'share', 'b', 'excluded')
    return t, r, m


def test_compiled_nonextra_ignores_extra_sharing_and_refreshes_only_on_next_reaction():
    out = execute_mechanics(*case())
    assert out['result'] is not None, out['issues']
    by_id = {h['id']: h['expected'] for h in out['result']['hits']}
    assert by_id['h1'] == pytest.approx(45 * (1 + 150 * FACTOR))
    assert by_id['h2'] == pytest.approx(45 * (1 + 150 * FACTOR))  # EM already expired, passive retained.
    assert by_id['h4'] == pytest.approx(45 * (1 + 150 * FACTOR))  # Old self capture did not gain later refresh.
    assert by_id['h5'] == pytest.approx(45 * (1 + 100 * FACTOR))
    assert [s['value'] for s in sampled(out)] == [350, 300]  # Swirl uses total, independently.
    check_sample_projection(out)
    rows = [x for x in out['sampling_trace'] if x['rule_id'].startswith('passive.attr.')]
    assert {x['source_value'] for x in rows} == {100, 150}
    assert all(x['expression']['basis'] == 'nonextra' and x['attribute_receipt_id'] for x in rows)


def test_same_source_uses_explicit_initial_nonextra_not_total():
    t, r, m = case()
    m['attribute_model']['initial_basis'][0]['nonextra']['value'] = 75
    out = execute_mechanics(t, r, m)
    by_id = {h['id']: h['expected'] for h in out['result']['hits']}
    assert by_id['h1'] == pytest.approx(45 * (1 + 125 * FACTOR))
    assert by_id['h5'] == pytest.approx(45 * (1 + 75 * FACTOR))
    assert [s['value'] for s in sampled(out)] == [350, 300]


def test_actual_unknown_writer_blocks_only_its_nonextra_dependency():
    t, r, m = case()
    m['attribute_model']['effect_classifications'][0]['nonextra_inclusion'] = 'unknown'
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and out['status'] == 'pending'
    assert any(x['code'] == 'attribute_classification_unknown' for x in out['issues'])


def test_unrelated_unknown_writer_does_not_block_provider_nonextra():
    t, r, m = case()
    add(m, rule('unrelated', 'benefit.em.all', 20, owner='b',
                beneficiaries={'mode': 'self', 'scope': 'locked'}))
    classify(m, 'unrelated', 'b', 'unknown')
    # The excluded conversion is pruned from a NONEXTRA; its TOTAL reads b TOTAL, not b NONEXTRA.
    share = next(x for x in m['rules'] if x['id'] == 'share')
    share['effects'][0]['value']['basis'] = 'total'
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert next(h for h in out['result']['hits'] if h['id'] == 'h1')['expected'] == pytest.approx(45 * (1 + 150 * FACTOR))
