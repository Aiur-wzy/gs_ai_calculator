"""Independent ledger ownership and signed-batch boundaries, without real queue hook."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
from genshin_engine.resource_threshold_runtime import ThresholdRuntime
from genshin_engine.shared_pools import SharedPoolError, _canonical
from tests.test_resource_threshold_authority import apply, claim, make
from tests.test_resource_threshold_dispatch import empty, model


def test_runtime_program_cannot_be_rebound_to_different_checked_target_graph():
    ledger, _ = make()
    altered = model()
    altered['rules'][0]['timing']['duration'] = 7
    other = ThresholdTriggerProgram(altered, character_ids=['a', 'b'])
    apply(ledger)
    before = copy.deepcopy(ledger.threshold_dispatch_report())
    with pytest.raises(SharedPoolError):
        runtime = ThresholdRuntime(other, SimpleNamespace(ledger=ledger))
        ctx = SimpleNamespace(time=0, frontline='a', conditions={}, rules=copy.deepcopy(other._rules),
            state={'boost': empty()}, trace=[], attribute_runtime=None, attribute_layer_serial=0,
            beneficiaries=lambda *args: ['a'], _reserve_events=lambda *args: None)
        runtime.after_operation(ctx, {'id': 'hit', 'event': 'hit', 'origin': 0}, 'pre',
            {'id': 'hit', 'run_id': '0:hit', 'time': 0, 'origin': 0}, 'refill', '0:hit')
    assert ledger.threshold_dispatch_report() == before


def test_wrong_command_id_cannot_consume_actual_batch_and_correct_retry_is_allowed():
    ledger, _ = make()
    apply(ledger)
    authority = ledger._dispatch_authority
    report = copy.deepcopy(ledger.threshold_dispatch_report())
    with pytest.raises(SharedPoolError):
        authority.claim(authority._last_batch, event_id='hit', run_id='0:hit', t_us=0, phase='pre', command_id='other')
    assert ledger.threshold_dispatch_report() == report
    assert len(claim(ledger)) == 1


def test_mutated_original_payload_cannot_be_claimed_and_restoration_keeps_budget():
    ledger, budget = make()
    apply(ledger)
    authority = ledger._dispatch_authority
    original = authority._last_batch
    used = budget.used
    target = original['commands'][0]['payload']['ref_id']
    original['commands'][0]['payload']['ref_id'] = 'forged'
    with pytest.raises(SharedPoolError):
        claim(ledger)
    assert authority._next_claim == 1 and budget.used == used
    original['commands'][0]['payload']['ref_id'] = target
    assert claim(ledger)[0]['payload']['ref_id'] == target and budget.used == used


def test_non_crossing_operation_advances_observer_without_signing_dispatch():
    data = model()
    data['shared_pools']['operations'][0]['operation'] = 'set'
    ledger, _ = make(data)
    # Set to 1 enters no >=50 threshold. Change is a valid non-crossing commit.
    apply(ledger)
    assert ledger.threshold_dispatch_report()['issued'] == []
    assert ledger._dispatch_authority._last_batch is None
    assert ledger.threshold_dispatch_report()['reserved_bytes'] == 4
    assert ledger.observer_snapshot()['last_ledger_sequence'] == 1
    assert len(_canonical(ledger.threshold_dispatch_report()['issued'])) == 2
