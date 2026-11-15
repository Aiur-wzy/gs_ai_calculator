"""Independent bounded authority claims; no counter execution is implied."""
import copy

import pytest

from genshin_engine import state_runtime
from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
from tests.test_state_ordered_steps import ordered_runtime
from tests.test_state_runtime import config, event


def test_equal_batch_content_from_another_clock_is_not_authority():
    first, second = ordered_runtime(), ordered_runtime()
    a = first.start('field', event(), eligible=True)['ordered_steps']
    b = second.start('field', event(), eligible=True)['ordered_steps']
    assert a == b
    with pytest.raises(StateRuntimeError) as exc:
        second.consume_ordered_steps(a)
    assert exc.value.issue['code'] == 'state_control_authority'
    assert second.consume_ordered_steps(b)
    assert first.consume_ordered_steps(a)


def test_current_clock_generation_does_not_rewrite_unclaimed_old_steps():
    clock = ordered_runtime()
    old = clock.start('field', event(), eligible=True)['ordered_steps']
    old_saved = copy.deepcopy(old)
    new = clock.start('field', event(500_000, '0:again'), eligible=True)['ordered_steps']
    old_steps = clock.consume_ordered_steps(old)
    new_steps = clock.consume_ordered_steps(new)
    assert old == old_saved
    assert old_steps[0]['generation_uid'] == new_steps[0]['generation_uid']
    assert old_steps[0]['generation_uid'] != new_steps[-1]['generation_uid']
    assert [s['kind'] for s in new_steps[:3]] == ['lifecycle_closing', 'dispatch', 'lifecycle_end']


def test_invalid_mutation_does_not_claim_or_skip_an_authorized_batch():
    clock = ordered_runtime()
    batch = clock.start('field', event(), eligible=True)['ordered_steps']
    original = copy.deepcopy(batch)
    batch['steps'][0]['context']['event_id'] = 'forged'
    with pytest.raises(StateRuntimeError):
        clock.consume_ordered_steps(batch)
    batch.clear()
    batch.update(original)
    assert clock.consume_ordered_steps(batch)[0]['sequence'] == 1
    with pytest.raises(StateRuntimeError):
        clock.consume_ordered_steps(batch)


def test_empty_signed_batches_count_toward_budget_and_retry_keeps_sequence(monkeypatch):
    clock = StateRuntime(config(), character_ids=['a'], emit_ordered_steps=True)
    # Rejected start itself costs one bounded transition, plus its signed batch.
    monkeypatch.setitem(state_runtime.LIMITS, 'events', 4)
    first = clock.start('field', event(), eligible=False)['ordered_steps']
    second = clock.start('field', event(1, '0:no2'), eligible=False)['ordered_steps']
    with pytest.raises(StateRuntimeError) as exc:
        clock.start('field', event(2, '0:no3'), eligible=False)
    assert exc.value.issue['code'] == 'state_event_limit'
    assert clock.consume_ordered_steps(first) == clock.consume_ordered_steps(second) == []
    monkeypatch.setitem(state_runtime.LIMITS, 'events', 6)
    third = clock.start('field', event(2, '0:no3'), eligible=False)['ordered_steps']
    assert third['sequence'] == 3 and clock.consume_ordered_steps(third) == []
