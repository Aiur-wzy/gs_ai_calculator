"""Proposed L2b real-queue oracles, recorded before runtime implementation."""
from __future__ import annotations

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_runtime import reader
from tests.test_mechanics_execution import (
    add,
    condition,
    damages,
    fixture,
    rule,
    second_character,
)
from tests.test_snapshot_captures import capture
from tests.test_swirl_dynamic_v3 import check_sample_projection, sampled, setup


def enable_graph(t, r, m, a=100, b=200):
    second_character(t, r)
    for char, value in zip(t['characters'], [a, b], strict=True):
        char['stats']['em'] = value
    m['attribute_model'] = {'contract': 'attribute-ledger-v2', 'initial_basis': [
        {'actor': c['id'], 'included_components': [],
         'nonextra': {'mode': 'panel_total', 'status': 'assumed', 'source': 'Synthetic baseline has no extra component'}}
        for c in t['characters']], 'effect_classifications': []}


def classify(m, rid, provider, inclusion='excluded'):
    m['attribute_model']['effect_classifications'].append({
        'effect_ref': {'kind': 'rule', 'rule_id': rid, 'effect_id': 'value'},
        'component': {'source_fingerprint': 'fixture-'+rid, 'provider': provider, 'part_id': rid, 'effect_id': 'value'},
        'nonextra_inclusion': inclusion, 'status': 'assumed', 'source': 'Synthetic classification'})


def conversion(rid='share', owner='b', recipient='a', basis='nonextra', sample='current', factor=.2, **kwargs):
    return rule(rid, 'benefit.em.all', {'kind': 'stat', 'subject': 'owner', 'stat': 'em',
        'basis': basis, 'sample': sample, 'factor': factor}, owner=owner,
        beneficiaries={'mode': 'characters', 'characters': [recipient], 'scope': 'locked'}, **kwargs)


def mutual(t, r, m, basis='nonextra'):
    enable_graph(t, r, m)
    add(m, conversion('b_to_a', basis=basis), conversion('a_to_b', 'a', 'b', basis))
    classify(m, 'b_to_a', 'b')
    classify(m, 'a_to_b', 'a')


def run(t, r, m):
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    return out


def test_real_mutual_nonextra_conversions_feed_swirl_once():
    t, r, m = setup((.2,))
    mutual(t, r, m)
    out = run(t, r, m)
    assert sampled(out)[0]['value'] == 140
    check_sample_projection(out)
    receipts = out['attribute_ledger']['receipts']
    assert any(x['query']['actor'] == 'b' and x['query']['basis'] == 'nonextra' and x['value'] == 200 for x in receipts)
    assert all(x['contract'] == 'attribute-ledger-v2' for x in receipts)


def test_real_total_cycle_keeps_prefix_and_no_damage():
    t, r, m = fixture((.2,))
    mutual(t, r, m, 'total')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending'
    assert out['result'] is None
    assert any(x['code'] == 'attribute_dependency_cycle' for x in out['issues'])
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_trigger_layers_preserve_each_old_provider_frame():
    t, r, m = fixture((.2, .4, .6))
    enable_graph(t, r, m, a=0, b=100)
    condition(m, 'increase', .3)
    condition(m, 'later', .5)
    for rid, event in [('ordinary1', 'increase'), ('ordinary2', 'later')]:
        add(m, rule(rid, 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'},
                    trigger={'event': 'condition', 'phase': 'pre', 'event_ids': [event]}))
        classify(m, rid, 'b', 'included')
    add(m, conversion(sample='trigger', trigger={'event': 'hit', 'phase': 'pre', 'event_ids': ['h0', 'h1']},
                       stacking={'max': 2, 'add': 1, 'policy': 'independent'}), reader())
    classify(m, 'share', 'b')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([54, 81, 81])


def test_event_conversion_missing_is_not_backfilled_from_future_capture():
    t, r, m = fixture((.05, .2))
    enable_graph(t, r, m)
    m['snapshot_captures'] = [capture(offset=.1)]
    source = conversion(sample='event')
    source['effects'][0]['value']['event_id'] = 'sample'
    add(m, source)
    classify(m, 'share', 'b')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending'
    assert out['result'] is None
    assert any(x['code'] in ('attribute_frame_missing', 'snapshot_event_unexecuted') for x in out['issues'])


def test_excluded_unexecuted_branch_does_not_block_unrelated_nonextra_use():
    t, r, m = fixture((.1,))
    enable_graph(t, r, m)
    r['actions'][0]['occupation_end'] = .4
    m['snapshot_captures'] = [capture(offset=.15)]
    source = conversion(owner='a', recipient='b', sample='event')
    source['effects'][0]['value']['event_id'] = 'sample'
    add(m, source, reader(basis='nonextra', owner='b'))
    classify(m, 'share', 'a')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([135])


def test_mixed_self_snapshot_and_current_provider_em_use_different_rows():
    t, r, m = fixture((.2,))
    enable_graph(t, r, m, a=0, b=100)
    condition(m, 'increase', .1)
    t['hits'][0]['reaction'] = {'type': 'amplify', 'factor': 2}
    m['hit_events'][0]['self_snapshot_event'] = 'cast'
    add(m, conversion('self_share', factor=.2, domain='self'), conversion('provider_share', factor=.1, domain='provider'),
        rule('increase', 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'},
             trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['increase']}))
    classify(m, 'self_share', 'b')
    classify(m, 'provider_share', 'b')
    classify(m, 'increase', 'b', 'included')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([45 * 2 * (1 + 2.78 * 50 / (1400 + 50))])
    applied = [x for x in out['mechanics_trace'] if x['type'] == 'effect_applied' and x.get('cycle') == 0]
    assert {x['rule_id']: x['value'] for x in applied} == {'self_share': 20, 'provider_share': 30}


def test_v1_does_not_enable_conversion_dependencies():
    t, r, m = fixture((.2,))
    mutual(t, r, m)
    m['attribute_model']['contract'] = 'attribute-ledger-v1'
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid'
    assert any(x['code'] == 'unsupported_dependency' for x in out['issues'])


def test_old_elemental_v2_keeps_fixed_em_qualification():
    t, r, m = setup((.2,), element='cryo')
    mutual(t, r, m)
    m['elemental_model']['ruleset'] = 'gauge-swirl-v2'
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending'
    assert any(x['code'] == 'swirl_dynamic_em' for x in out['issues'])


def test_new_execution_rebuilds_panel_total_inputs():
    t, r, m = setup((.2,))
    mutual(t, r, m)
    assert sampled(run(t, r, m))[0]['value'] == 140
    t['characters'][1]['stats']['em'] = 400
    assert sampled(run(t, r, m))[0]['value'] == 180


def test_graph_frame_budget_preserves_executed_prefix(monkeypatch):
    t, r, m = fixture((.2, .4))
    mutual(t, r, m)
    m.setdefault('execution_options', {'schema_version':1, 'attribute_limits':{}})['attribute_limits']['frames'] = 1
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending'
    assert out['result'] is None
    assert out['attribute_ledger']['usage']['frames'] == 1
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_old_self_qualification_reads_later_actually_captured_event():
    t, r, m = fixture((.3,))
    enable_graph(t, r, m, a=0, b=100)
    r['actions'][0]['occupation_end'] = .4
    m['snapshot_captures'] = [capture(offset=.2)]
    m['hit_events'][0]['self_snapshot_event'] = 'cast'
    condition(m, 'increase', .1)
    source = conversion(sample='event')
    source['effects'][0]['value']['event_id'] = 'sample'
    add(m, source, rule('gain', 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'},
                       trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['increase']}))
    classify(m, 'share', 'b')
    classify(m, 'gain', 'b', 'included')
    out = run(t, r, m)
    applied = [x for x in out['mechanics_trace'] if x['type'] == 'effect_applied' and x.get('rule_id') == 'share']
    assert all(x['value'] == 60 for x in applied)
    top = next(x for x in out['attribute_ledger']['receipts'] if x['query']['purpose'] == 'damage_projection')
    row = next(x for x in top['contributions'] if x['decision'] == 'included')
    child = next(x for x in out['attribute_ledger']['receipts'] if x['receipt_id'] == row['input_receipt_id'])
    assert child['query']['frame']['observation']['event_id'] == 'sample'
    assert row['projection_of']['frame_id'] == top['query']['frame']['context']['self_frame_id']
    assert child['value'] == 300


def test_inapplicable_filtered_em_does_not_block_separate_projection_member():
    t, r, m = fixture((.2,))
    enable_graph(t, r, m)
    limited = rule('limited', 'benefit.em.all', 500, beneficiaries={'mode': 'self', 'scope': 'locked'})
    limited['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'damage_type_ids': ['damage.elemental_burst']}
    add(m, conversion(), limited)
    classify(m, 'share', 'b')
    classify(m, 'limited', 'a', 'included')
    out = run(t, r, m)
    applied = [x for x in out['mechanics_trace'] if x['type'] == 'effect_applied']
    assert all(x['rule_id'] == 'share' and x['value'] == 40 for x in applied)


def test_applicable_filter_is_still_context_pending():
    t, r, m = fixture((.2,))
    enable_graph(t, r, m)
    source = conversion()
    source['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'damage_type_ids': ['damage.elemental_skill']}
    add(m, source)
    classify(m, 'share', 'b')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(x['code'] == 'attribute_context_unsupported' for x in out['issues'])


def test_nonem_output_missing_event_has_controlled_prefix():
    t, r, m = fixture((.05, .2))
    enable_graph(t, r, m)
    m['snapshot_captures'] = [capture(offset=.1)]
    source = reader(sample='event')
    source['effects'][0]['value']['event_id'] = 'sample'
    add(m, source)
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_graph_export_budget_cannot_publish_partial_untraceable_damage(monkeypatch):
    t, r, m = fixture((.2,))
    mutual(t, r, m)
    m.setdefault('execution_options', {'schema_version':1, 'attribute_limits':{}})['attribute_limits']['output_bytes'] = 2000
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(x['code'] == 'attribute_output_limit' for x in out['issues'])


def test_same_phase_trigger_conversions_share_prestate_not_sorted_rule_order():
    t, r, m = fixture((.2,))
    mutual(t, r, m, 'total')
    for source in m['rules']:
        source['effects'][0]['value']['sample'] = 'trigger'
    add(m, reader('read_a', owner='a'), reader('read_b', owner='b'))
    out = run(t, r, m)
    samples = [x for x in out['sampling_trace'] if x['cycle'] == 0]
    assert {x['rule_id']: x['source_value'] for x in samples} == {'read_a': 140, 'read_b': 220, 'b_to_a': 200}
    origins = {x['query']['frame']['id'] for x in out['attribute_ledger']['receipts']
               if x['query']['actor'] in ('a', 'b') and x.get('value') in (100, 200) and not x['contributions']}
    assert len(origins) == 3  # one shared pre-state per independently simulated origin


def test_parent_post_provider_change_does_not_rewrite_reaction_created_graph():
    t, r, m = setup((.2,))
    mutual(t, r, m)
    add(m, rule('post_gain', 'benefit.em.all', 200, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'},
                trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}))
    classify(m, 'post_gain', 'b', 'included')
    out = run(t, r, m)
    assert sampled(out)[0]['value'] == 140
    assert sampled(out)[0]['contract'] == 'attribute-em-sample-v2'
    check_sample_projection(out)
