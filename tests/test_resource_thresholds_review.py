"""Independent R0 candidate tests; no production source edits or dispatch authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_threshold_schema import THRESHOLD_LIMITS
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from tests.test_resource_counters import definitions
from tests.test_resource_thresholds import declaration, image, make, program
from tests.test_shared_pool_v2 import spend


@pytest.mark.parametrize('operation', ['spend', 'grant', 'consume'])
def test_pure_candidate_cannot_accept_more_atoms_than_requested(operation):
    _, pools, _ = definitions()
    if operation == 'grant':
        pools['operations'][0].update(operation='grant', amount=8)
        pools['operations'][0].pop('spend_policy')
        pools['pools'][0]['initial']['team'] = 0
    config = declaration(pools, down=True)
    if operation == 'consume':
        config['observers'][0]['causes'] = {'operation_ids': [], 'consumer_ids': ['consumer']}
    observer = program(config, pools)
    raw = SharedPoolLedger(pools, character_ids=['a', 'b'])
    rows = ([raw.try_consume('consumer', event_id='hit', run_id='0:hit', t_us=0, actor='a', eligible=True)]
            if operation == 'consume' else spend(raw))
    assert rows[0]['accepted'] > 0
    rows[0]['requested'] = rows[0]['accepted'] - 1
    with pytest.raises(SharedPoolError):
        observer.plan_commit(observer.initial_state(), {'receipt_start': 1, 'receipt_end': 1, 'receipts': rows})


def test_public_work_failure_keeps_cooldowns_and_all_internal_cursors_unchanged():
    ledger, budget, _ = make(counter=True)
    before = (image(ledger, budget), copy.deepcopy(ledger._cooldowns), ledger._counter_receipt_bytes)
    budget.limit = budget.used + 2
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert (image(ledger, budget), ledger._cooldowns, ledger._counter_receipt_bytes) == before
    budget.limit += 1
    spend(ledger)
    assert ledger.observer_snapshot()['last_ledger_sequence'] == 1


def test_failed_observer_evaluation_limit_preserves_resource_and_counter_prefix(monkeypatch):
    ledger, budget, _ = make(counter=True)
    before = image(ledger, budget)
    monkeypatch.setitem(THRESHOLD_LIMITS, 'receipts', 0)
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert image(ledger, budget) == before


def test_observer_definition_cannot_be_reused_with_changed_full_pool_registry():
    _, pools, _ = definitions()
    observer = program(declaration(pools), pools)
    changed = copy.deepcopy(pools)
    changed['operations'][0]['amount'] += 1
    with pytest.raises(SharedPoolError):
        SharedPoolLedger(changed, character_ids=['a', 'b'], observer_program=observer)


def test_committed_observer_byte_charge_matches_actual_utf8_array():
    from genshin_engine.shared_pools import _canonical

    ledger, _, _ = make()
    spend(ledger)
    assert ledger._observer_receipt_bytes == len(_canonical(ledger.observer_receipts()))
    assert ledger.observer_receipts()[0]['source_approval'] == 'not_evaluated_by_engine'
    assert ledger.observer_receipts()[0]['authority'] == 'audit_no_dispatch_authority'


@pytest.mark.parametrize('operation', ['spend', 'set'])
def test_pure_candidate_rejects_partial_quantity_not_allowed_by_operation(operation):
    _, pools, _ = definitions()
    if operation == 'set':
        pools['operations'][0]['operation'] = 'set'
        pools['operations'][0].pop('spend_policy')
    observer = program(declaration(pools, down=True), pools)
    raw = SharedPoolLedger(pools, character_ids=['a', 'b'])
    rows = spend(raw)
    assert rows[0]['requested'] == 8
    rows[0]['accepted'] = 5
    rows[0]['after'] = rows[0]['before'] - 5 if operation == 'spend' else 5
    rows[0]['delta'] = rows[0]['after'] - rows[0]['before']
    with pytest.raises(SharedPoolError):
        observer.plan_commit(observer.initial_state(), {'receipt_start': 1, 'receipt_end': 1, 'receipts': rows})
