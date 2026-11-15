"""Opt-in constant EM ledger on real mechanics event frames."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from tests.test_mechanics_execution import (
    add,
    condition,
    damages,
    fixture,
    rule,
    second_character,
)


def enable(t, m, *, nonextra=100, base=0):
    t['characters'][0]['stats']['em'] = 300
    m['attribute_model'] = {'contract': 'attribute-ledger-v1', 'initial_basis': [{
        'actor': 'a', 'included_components': [],
        'nonextra': {'mode': 'explicit', 'value': nonextra, 'status': 'assumed', 'source': 'fixture'},
        'base': {'mode': 'explicit', 'value': base, 'status': 'assumed', 'source': 'fixture'}}],
        'effect_classifications': []}


def classify(m, rid, inclusion='included', *, status='assumed'):
    m['attribute_model']['effect_classifications'].append({
        'effect_ref': {'kind': 'rule', 'rule_id': rid, 'effect_id': 'value'},
        'component': {'source_fingerprint': 'fixture-'+rid, 'provider': 'a', 'part_id': rid, 'effect_id': 'value'},
        'nonextra_inclusion': inclusion, 'status': status, 'source': 'fixture'})


def reader(rid='reader', basis='total', sample='current', **kwargs):
    return rule(rid, 'benefit.base_add.all', {'kind': 'stat', 'subject': 'owner', 'stat': 'em',
        'basis': basis, 'factor': 1, 'sample': sample}, domain='provider', **kwargs)


def run(t, r, m):
    before = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert (t, r, m) == before
    assert out['status'] == 'passed', out['issues']
    return out


@pytest.mark.parametrize(('basis', 'expected'), [('total', 225), ('nonextra', 135), ('base', 45)])
def test_explicit_basis_and_constant_em_are_delivered_once(basis, expected):
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 100), reader(basis=basis))
    classify(m, 'em')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([expected])
    assert out['attribute_ledger']['receipts'][0]['query']['basis'] == basis
    assert out['sampling_trace'][0]['attribute_receipt_id']


@pytest.mark.parametrize('basis', ['total', 'base', 'nonextra'])
def test_unknown_classification_only_blocks_nonextra(basis):
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 100), reader(basis=basis))
    classify(m, 'em', 'unknown')
    out = execute_mechanics(t, r, m)
    if basis == 'nonextra':
        assert out['status'] == 'pending'
        assert out['result'] is None
        assert out['rotation_check']['event_log'][-1]['status'] == 'pending'
    else:
        assert out['status'] == 'passed', out['issues']


def test_trigger_independent_layers_retain_their_nonextra_frame():
    t, r, m = fixture((.2, .4, .6))
    enable(t, m)
    condition(m, 'change', .3)
    add(m, rule('em', 'benefit.em.all', 200, trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['change']}),
        reader(basis='nonextra', sample='trigger', trigger={'event': 'hit', 'phase': 'pre'},
               stacking={'max': 2, 'add': 1, 'policy': 'independent'}))
    classify(m, 'em')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 225, 315])
    assert [s['source_value'] for s in out['sampling_trace'] if s['cycle'] == 0] == [100, 100, 300, 300, 300]


def test_same_tick_post_does_not_rewrite_captured_frame():
    t, r, m = fixture((.2, .2, .4))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 100, trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}),
        reader(basis='nonextra'))
    classify(m, 'em')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 135, 135])
    samples = [s for s in out['sampling_trace'] if s['cycle'] == 0]
    assert samples[0]['attribute_receipt_id'] != samples[1]['attribute_receipt_id']


def test_missing_active_component_is_pending_but_not_for_base():
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 100), reader())
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending'
    assert any(i['code'] == 'attribute_component_missing' for i in out['issues'])
    m['rules'][-1]['effects'][0]['value']['basis'] = 'base'
    assert execute_mechanics(t, r, m)['status'] == 'passed'


def test_unrelated_unknown_writer_does_not_block_owner_read():
    t, r, m = fixture((.2,))
    enable(t, m)
    second_character(t, r)
    unknown = rule('em', 'benefit.em.all', 100, owner='b', beneficiaries={'mode': 'self', 'scope': 'locked'})
    add(m, unknown, reader(basis='nonextra'))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90])


def test_basis_is_rejected_without_opt_in_and_for_other_stat():
    t, _r, m = fixture((.2,))
    add(m, reader())
    assert validate_mechanics_model(m)['status'] == 'invalid'
    enable(t, m)
    m['rules'][0]['effects'][0]['value']['stat'] = 'atk'
    assert validate_mechanics_model(m)['status'] == 'invalid'


def test_wrong_provider_and_dangling_classification_are_invalid():
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 100), reader())
    classify(m, 'em')
    m['attribute_model']['effect_classifications'][0]['component']['provider'] = 'other'
    assert execute_mechanics(t, r, m)['status'] == 'invalid'


def test_event_capture_preserves_old_nonextra_after_gain_expires():
    from tests.test_snapshot_captures import capture
    t, r, m = fixture((.2, .4))
    enable(t, m)
    m['snapshot_captures'] = [capture(offset=.1)]
    source = rule('em', 'benefit.em.all', 100, duration=.3)
    read_rule = reader(basis='nonextra', sample='event')
    read_rule['effects'][0]['value']['event_id'] = 'sample'
    add(m, source, read_rule)
    classify(m, 'em')
    out = run(t, r, m)
    assert damages(out) == pytest.approx([135, 135])
    receipts = out['attribute_ledger']['receipts']
    assert all(x['query']['frame']['phase'] == 'capture' for x in receipts)
    assert len([x for x in receipts if x['query']['frame']['origin'] == 0]) == 1


def test_swirl_frame_precedes_parent_post_and_trigger_frame():
    from tests.test_swirl_dynamic_v3 import sampled, setup
    t, r, m = setup((.2, .3))
    enable(t, m)
    add(m, rule('em', 'benefit.em.all', 200, trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}),
        reader(basis='nonextra', sample='trigger', trigger={'event': 'elemental_reaction', 'phase': 'post'}))
    classify(m, 'em')
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert [s['value'] for s in sampled(out)] == [300, 500]
    # First reaction's new reader is absent from its reaction-created snapshot.
    assert [s['source_value'] for s in out['sampling_trace'] if s['cycle'] == 0] == [300, 300]
    receipts = {x['receipt_id']: x for x in out['attribute_ledger']['receipts']}
    assert receipts[sampled(out)[0]['attribute_receipt_id']]['query']['frame']['phase'] == 'reaction_created'
    assert receipts[out['sampling_trace'][0]['attribute_receipt_id']]['query']['frame']['phase'] == 'post'


@pytest.mark.parametrize('constraint', ['filter', 'quota', 'hit_match'])
def test_context_limited_em_never_uses_a_raw_attribute_read(constraint):
    t, r, m = fixture((.2,))
    enable(t, m)
    source = rule('em', 'benefit.em.all', 100)
    matches = None
    if constraint == 'filter':
        source['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre'}
    elif constraint == 'quota':
        source['quota'] = {'capacity': 1, 'scope': 'team', 'refresh': 'reset', 'consume': {'event': 'hit', 'phase': 'pre'}}
    else:
        matches = [{'rule_id': 'em', 'effect_id': 'value', 'hit_id': 'h0', 'operation': 'include'}]
    add(m, source, reader())
    classify(m, 'em')
    out = execute_mechanics(t, r, m, matches)
    assert out['status'] == 'pending', out['issues']
    assert any(x['code'] == 'attribute_context_unsupported' for x in out['issues'])
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_pending_basis_and_panel_total_conflicting_static_facts():
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, reader(basis='nonextra'))
    basis = m['attribute_model']['initial_basis'][0]
    basis['nonextra']['status'] = 'pending'
    assert execute_mechanics(t, r, m)['status'] == 'pending'
    basis['nonextra'] = {'mode': 'panel_total', 'status': 'assumed', 'source': 'fixture'}
    out = run(t, r, m)
    assert damages(out) == pytest.approx([180])
    basis['included_components'] = [{'component': {'source_fingerprint': 'static', 'provider': 'a', 'part_id': 'p', 'effect_id': 'e'},
                                     'value': 20, 'nonextra_inclusion': 'excluded'}]
    assert execute_mechanics(t, r, m)['status'] == 'invalid'


def test_shared_execution_work_exhaustion_keeps_previous_receipts(monkeypatch):
    t, r, m = fixture((.2, .4, .6))
    enable(t, m)
    add(m, reader())
    m.setdefault('execution_options', {'schema_version':1, 'attribute_limits':{}})['attribute_limits']['execution_work'] = 7
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending', out['issues']
    assert out['result'] is None
    # Second read cannot register a frame after the first successful receipt.
    assert len(out['attribute_ledger']['receipts']) == 1
    usage = out['attribute_ledger']['usage']
    assert usage['execution_work'] == usage['capture_work'] + usage['query_work'] + usage['registration_work']
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_runtime_layers_keep_stable_sum_equal_to_actual_core():
    from tests.test_swirl_dynamic_v3 import check_sample_projection, setup
    t, r, m = setup((.2,))
    enable(t, m)
    t['characters'][0]['stats']['em'] = 1e16
    add(m, rule('negative', 'benefit.em.all', -1e16),
        rule('small', 'benefit.em.all', .1, stacking={'max': 6, 'add': 6, 'policy': 'refresh'}))
    classify(m, 'negative')
    classify(m, 'small')
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    check_sample_projection(out)


def test_initial_layer_expiry_keeps_surviving_layer_identity():
    t, r, m = fixture((.2, .4))
    enable(t, m)
    em = rule('em', 'benefit.em.all', 20, trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h1']},
              stacking={'max': 2, 'add': 1, 'policy': 'independent'})
    add(m, em, reader(basis='nonextra'))
    classify(m, 'em')
    m['initial_state']['rules'][0]['instances'] = [{'beneficiaries': ['a'], 'remaining': [.3, .5]}]
    out = run(t, r, m)
    assert damages(out) == pytest.approx([108, 99])
    receipts = [x for x in out['attribute_ledger']['receipts'] if x['query']['frame']['origin'] == 0]
    before, after = [x['contributions'] for x in receipts[:2]]
    assert len(before) == 2 and len(after) == 1
    assert after[0]['layer_id'] in {x['layer_id'] for x in before}


def test_capture_and_registration_share_budget_and_usage_is_a_copy():
    from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError
    from tests.test_attribute_ledger import declaration
    ledger = AttributeLedger(declaration(), limits={'execution_work':40000})
    ledger.reserve_capture_work(39999)
    ledger.read_stat('a', 'em', 'total', 'f0')
    with pytest.raises(AttributeLedgerError, match='累计'):
        ledger.reserve_capture_work(1)
    report = ledger.usage()
    assert report['execution_work'] == 40000
    report['limits']['execution_work'] = 50000
    assert ledger.usage()['limits']['execution_work'] == 40000


def test_expiry_manual_window_and_recipient_use_current_frame():
    from tests.test_swirl_dynamic_v3 import manual_em
    t, r, m = fixture((.2, .4))
    enable(t, m)
    t['buffs'] = [manual_em(50, 0, .3, target='a')]
    t['buffs'][0]['extensions'] = {'owner': 'a'}
    m['attribute_model']['effect_classifications'] = [{
        'effect_ref': {'kind': 'manual_buff', 'buff_id': 'em', 'effect_id': 'value'},
        'component': {'source_fingerprint': 'manual-fixture', 'provider': 'a', 'part_id': 'em', 'effect_id': 'value'},
        'nonextra_inclusion': 'included', 'status': 'assumed', 'source': 'fixture'}]
    add(m, reader(basis='nonextra'))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([112.5, 90])


def test_basis_unknown_never_reuses_total_and_native_cannot_supply_total_or_frames():
    t, r, m = fixture((.2,))
    enable(t, m)
    add(m, reader(basis='nonextra'))
    del m['attribute_model']['initial_basis'][0]['nonextra']
    assert execute_mechanics(t, r, m)['status'] == 'pending'
    m['attribute_model']['frames'] = []
    assert execute_mechanics(t, r, m)['status'] == 'invalid'
    del m['attribute_model']['frames']
    m['attribute_model']['initial_basis'][0]['total'] = 999
    assert execute_mechanics(t, r, m)['status'] == 'invalid'


@pytest.mark.parametrize('em_id', ['a_em_first', 'z_em_last'])
@pytest.mark.parametrize('reverse', [False, True])
def test_same_phase_rules_share_prestate_and_current_reads_committed_state(em_id, reverse):
    t, r, m = fixture((.2,))
    enable(t, m)
    sources = [rule(em_id, 'benefit.em.all', 200), reader('first_reader', 'nonextra', 'trigger'),
               reader('second_reader', 'nonextra', 'trigger'), reader('current_reader', 'nonextra')]
    add(m, *(reversed(sources) if reverse else sources))
    classify(m, em_id)
    out = run(t, r, m)
    assert damages(out) == pytest.approx([270])
    rows = {x['rule_id']: x for x in out['sampling_trace'] if x['cycle'] == 0}
    assert rows['first_reader']['source_value'] == rows['second_reader']['source_value'] == 100
    assert rows['first_reader']['attribute_receipt_id'] == rows['second_reader']['attribute_receipt_id']
    assert rows['current_reader']['source_value'] == 300
    assert rows['current_reader']['attribute_receipt_id'] != rows['first_reader']['attribute_receipt_id']


def test_trigger_freeze_does_not_eagerly_evaluate_unrelated_em_overflow():
    t, r, m = fixture((.2, .4))
    enable(t, m)
    second_character(t, r)
    t['characters'][1]['stats']['em'] = 1e308
    condition(m, 'gain', .1)
    add(m, rule('other_em', 'benefit.em.all', 1e308, owner='b',
                beneficiaries={'mode': 'self', 'scope': 'locked'}),
        reader(basis='nonextra', sample='trigger', trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['gain']}))
    out = run(t, r, m)
    assert damages(out) == pytest.approx([90, 90])


def test_runtime_stage_preserves_real_dispatch_identity_with_origin_wrapper():
    from genshin_engine.attribute_runtime import AttributeRuntime
    runtime = object.__new__(AttributeRuntime)
    runtime.epoch = 0
    runtime.stage({'id': 'stable-route', 'origin': 2, 'run_id': '@state_dispatch:first'}, 'post')
    assert runtime.context['run_id'] == '2:@state_dispatch:first'
    assert runtime.actual_event_run_id == '@state_dispatch:first'
    runtime.stage({'id': 'stable-route', 'origin': 2, 'run_id': '@state_dispatch:second'}, 'post')
    assert runtime.context['run_id'] == '2:@state_dispatch:second'
    runtime.stage({'id': 'cast', 'origin': 2, 'run_id': '2:cast'}, 'pre')
    assert runtime.context['run_id'] == '2:cast'
