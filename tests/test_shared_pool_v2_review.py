"""Independent atom-accounting checks; no game/scheduler eligibility claim."""
import copy

import pytest

from genshin_engine import shared_pools
from genshin_engine.shared_pools import SharedPoolError
from tests.test_shared_pool_v2 import scaled, spend
from tests.test_shared_pools import consume, ledger


def test_byte_budget_failure_is_atomic_after_a_completed_prefix(monkeypatch):
    config = scaled(initial=20)
    config['pools'][0].update(scope='per_character', initial={'a': 20, 'b': 20})
    config['operations'][0]['cooldown'] = {'seconds': .1, 'anchor': 'success'}
    model = ledger(config)
    spend(model, keys=['a', 'b'])
    before, trace = model.snapshot(), model.receipts()
    monkeypatch.setitem(shared_pools.LIMITS, 'receipt_bytes', model._receipt_bytes + 1)
    with pytest.raises(SharedPoolError) as exc:
        spend(model, t=100_000, run='0:second', keys=['a', 'b'])
    assert exc.value.issue['code'] == 'pool_receipt_limit'
    assert model.snapshot() == before and model.receipts() == trace
    monkeypatch.setitem(shared_pools.LIMITS, 'receipt_bytes', 64 * 1024 * 1024)
    assert [row['accepted'] for row in spend(model, t=100_000, run='0:second', keys=['b', 'a'])] == [8, 8]


def test_actual_atoms_do_not_depend_on_display_scale_or_dictionary_order():
    config = scaled(amount='all_available')
    config['pools'][0].update(scope='per_character', initial={'a': 3, 'b': 16})
    other = copy.deepcopy(config)
    other['pools'][0]['initial'] = {'b': 16, 'a': 3}
    other['pools'][0]['unit_scale'] = 1000
    a, b = ledger(config), ledger(other)
    ar, br = spend(a, keys=['b', 'a']), spend(b, keys=['a', 'b'])
    assert [(r['key'], r['before'], r['accepted'], r['after']) for r in ar] == [
        (r['key'], r['before'], r['accepted'], r['after']) for r in br]
    assert [r['unit_scale'] for r in ar] == [10, 10]
    assert [r['unit_scale'] for r in br] == [1000, 1000]
    assert a.snapshot()['balances'] == b.snapshot()['balances']


def test_scaled_pre_consumer_post_share_clock_and_pre_cannot_rewind():
    config = scaled(initial=3)
    grant = copy.deepcopy(config['operations'][0])
    grant.update(id='pre-grant', operation='grant', amount=13, order=0,
                 trigger={'event': 'elemental_reaction', 'phase': 'pre'})
    grant.pop('spend_policy')
    config['operations'].append(grant)
    model = ledger(config)
    row = model.apply_operation('pre-grant', event_id='tick', run_id='0:tick', t_us=0,
                                phase='pre', event_kind='elemental_reaction', keys=['team'], eligible=True)[0]
    first = consume(model, event='tick', run='0:tick', t=0)
    post = spend(model)[0]
    assert (row['after'], first['after'], post['after']) == (16, 15, 7)
    before, trace = model.snapshot(), model.receipts()
    with pytest.raises(SharedPoolError) as exc:
        model.apply_operation('pre-grant', event_id='tick', run_id='0:tick', t_us=0,
                              phase='pre', event_kind='elemental_reaction', keys=['team'], eligible=True)
    assert exc.value.issue['code'] == 'pool_event_phase_order'
    assert model.snapshot() == before and model.receipts() == trace
    assert consume(model, event='tick', run='0:tick', t=0) == first


def test_scaled_declaration_and_returned_receipts_cannot_mutate_live_units():
    config = scaled(initial=10)
    model = ledger(config)
    config['pools'][0]['unit_scale'] = 1
    first = consume(model, t=0)
    first['accepted'] = 999
    first['unit_scale'] = 1
    snapshot = model.snapshot()
    snapshot['unit_scales']['pool'] = 1
    second = spend(model)[0]
    assert second['unit_scale'] == 10 and second['before'] == 9
    assert model.receipts()[0]['accepted'] == 1
    assert model.snapshot()['unit_scales']['pool'] == 10


def test_same_run_retargeting_does_not_consume_a_second_key():
    config = scaled()
    config['pools'][0].update(scope='per_character', initial={'a': 10, 'b': 10})
    config['operations'][0]['targets'] = {'mode': 'characters', 'characters': ['a']}
    model = ledger(config)
    spend(model, keys=['a'])
    before, trace = model.snapshot(), model.receipts()
    with pytest.raises(SharedPoolError):
        spend(model, keys=['b'])
    assert model.snapshot() == before and model.receipts() == trace
