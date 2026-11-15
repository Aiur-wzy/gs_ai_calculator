"""Slot state, shared budget and failure-prefix tests; no queue authority."""
import copy

import pytest

from genshin_engine.resource_reaction_audit import Audit, AuditStopped
from genshin_engine.resource_reaction_program import Program, ReactionResourceError
from tests.test_resource_reaction_program import model


class Ledger:
    def __init__(self):
        self.cursor = 0
        self.reads = 0

    def receipt_cursor(self):
        self.reads += 1
        return self.cursor


def context(index=0):
    return {'parent_event_id': 'h', 'parent_run_id': f'{index}:h', 'reaction_event_id': 'r',
            'reaction_run_id': f'opaque-{index}', 'target_id': 'enemy', 'owner': 'a', 'frontline': 'b',
            'origin': index, 'time_us': 200000, 'reaction_type': 'swirl', 'trigger_element': 'anemo',
            'aura_element': 'pyro', **{k: 'a' * 64 for k in (
                'parent_fingerprint', 'application_fingerprint', 'reaction_fingerprint')}}


def ready(reserve=lambda _: None):
    value, ledger = model(), Ledger()
    audit = Audit(Program(value, ['a', 'b']), ledger, reserve)
    return audit, ledger, value['shared_pools']['operations'][0], value['shared_pools']['pools'][0]


def prepare(values, scope, decision='match', **kwargs):
    audit, ledger, operation, pool = values
    return audit.prepare(scope, 'refill', decision, operation, pool, kwargs.get('ledger', ledger))


def test_commit_then_threshold_failure_keeps_real_span():
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    slot = prepare(values, scope)
    ledger.cursor = 1  # The one external ledger mutation has returned.
    audit.committed(slot)
    # A later threshold failure means finish_scan/callback were never reached.
    report = audit.report()
    assert report['operations'][0]['state'] == 'committed'
    assert report['operations'][0]['actual_end'] == 1
    assert report['occurrences'][0]['resource_scan_complete'] is False
    assert report['occurrences'][0]['callback_complete'] is False
    assert report['source_authority'] == 'not_evaluated'


def test_no_match_does_not_even_read_ledger_cursor():
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    prepare(values, scope, 'no_match')
    audit.finish_scan(scope)
    audit.finish_callback(scope)
    report = audit.report()
    assert ledger.reads == 0
    assert report['operations'][0]['ledger_begin'] is None
    assert report['operations'][0]['state'] == 'no_match'


def test_precommit_cursor_failure_keeps_scope_and_does_not_publish_planned_slot():
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    def fail():
        raise RuntimeError('injected cursor failure')
    ledger.receipt_cursor = fail
    with pytest.raises(ReactionResourceError) as exc:
        prepare(values, scope)
    assert exc.value.issue['severity'] == 'pending'
    report = audit.report()
    assert report['termination'] == 'resource_reaction_cursor'
    assert report['operations'] == [] and len(report['occurrences']) == 1


@pytest.mark.parametrize('failure', ['ledger_rejected', 'ledger_budget', 'event_budget'])
def test_ledger_failure_is_never_a_committed_grant(failure):
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    slot = prepare(values, scope)
    audit.failed(slot, failure)
    row = audit.report()['operations'][0]
    assert ledger.cursor == 0 and row['state'] == 'failed' and row['actual_end'] is None


def test_cursor_mismatch_keeps_actual_end_and_committed_inconsistent():
    values = ready()
    audit, ledger, _, _ = values
    slot = prepare(values, audit.begin(context()))
    ledger.cursor = 2
    with pytest.raises(ReactionResourceError):
        audit.committed(slot)
    row = audit.report()['operations'][0]
    assert row['state'] == 'committed_inconsistent' and row['actual_end'] == 2


@pytest.mark.parametrize('token', ['scope', 'slot', 'ledger'])
def test_copied_or_foreign_objects_cannot_borrow_authority(token):
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    if token == 'scope':
        with pytest.raises(ReactionResourceError): prepare(values, copy.copy(scope))
    elif token == 'ledger':
        with pytest.raises(ReactionResourceError): prepare(values, scope, ledger=Ledger())
    else:
        slot = prepare(values, scope)
        ledger.cursor = 1
        with pytest.raises(ReactionResourceError): audit.committed(copy.copy(slot))


def test_repeated_prepare_consumes_work_without_duplicate_slot():
    values = ready()
    audit = values[0]
    scope = audit.begin(context())
    prepare(values, scope, 'no_match')
    before = audit._work
    with pytest.raises(ReactionResourceError): prepare(values, scope, 'no_match')
    report = audit.report()
    assert len(report['operations']) == 1 and report['usage']['work_consumed'] > before


def test_reserve_failure_cannot_publish_second_scope():
    calls = []
    def reserve(count):
        calls.append(count)
        if len(calls) == 2: raise RuntimeError('fixture budget')
    audit, _, _, _ = ready(reserve)
    audit.begin(context())
    with pytest.raises(RuntimeError): audit.begin(context(1))
    assert len(audit.report()['occurrences']) == 1


def test_reserve_reentry_cannot_create_another_scope():
    values = ready()
    audit = values[0]
    def reserve(_):
        with pytest.raises(ReactionResourceError, match='重入'):
            audit.begin(context(99))
    audit.reserve_events = reserve
    audit.begin(context())
    assert len(audit.report()['occurrences']) == 1


def test_commit_during_reserve_reentry_cannot_change_old_slot():
    values = ready()
    audit = values[0]
    slot = prepare(values, audit.begin(context()))
    def reserve(_):
        with pytest.raises(ReactionResourceError, match='重入'):
            audit.committed(slot)
    audit.reserve_events = reserve
    audit.begin(context(1))
    assert audit.report()['operations'][0]['state'] == 'planned'


def test_report_seals_runtime_and_is_not_an_unlimited_output_api():
    audit, _, _, _ = ready()
    audit.begin(context())
    audit.report()
    with pytest.raises(AuditStopped): audit.report()
    with pytest.raises(AuditStopped): audit.begin(context(1))


def test_bad_raw_context_still_consumes_failed_attempt_work():
    audit, _, _, _ = ready()
    before = audit._work
    invalid = context()
    invalid['extra'] = invalid
    with pytest.raises(ReactionResourceError): audit.begin(invalid)
    assert audit._work > before


def test_unhashable_operation_id_is_controlled():
    values = ready()
    audit, ledger, op, pool = values
    scope = audit.begin(context())
    with pytest.raises(ReactionResourceError):
        audit.prepare(scope, [], 'match', op, pool, ledger)


def test_first_commit_survives_second_operation_slot_limit(monkeypatch):
    value = model()
    second = copy.deepcopy(value['shared_pools']['operations'][0])
    second.update(id='second', source_id='source:second', order=1)
    value['shared_pools']['operations'].append(second)
    value['resource_reaction_contract']['operation_ids'].append('second')
    ledger = Ledger()
    audit = Audit(Program(value, ['a', 'b']), ledger, lambda _: None)
    pool = value['shared_pools']['pools'][0]
    scope = audit.begin(context())
    slot = audit.prepare(scope, 'refill', 'match', value['shared_pools']['operations'][0], pool, ledger)
    ledger.cursor = 1
    audit.committed(slot)
    monkeypatch.setitem(audit.limits, 'slots', 2)
    with pytest.raises(ReactionResourceError): audit.prepare(scope, 'second', 'match', second, pool, ledger)
    report = audit.report()
    assert len(report['operations']) == 1 and report['operations'][0]['state'] == 'committed'
    assert report['termination'] == 'resource_reaction_limit' and ledger.cursor == 1


def test_multikey_is_a_single_span_not_repeated_attempts():
    value = model()
    pool = value['shared_pools']['pools'][0]
    pool.update(scope='per_character', initial={'a': 0, 'b': 0})
    ledger, calls = Ledger(), []
    audit = Audit(Program(value, ['a', 'b']), ledger, calls.append)
    scope = audit.begin(context())
    slot = audit.prepare(scope, 'refill', 'match', value['shared_pools']['operations'][0], pool, ledger)
    ledger.cursor = 2
    audit.committed(slot)
    audit.finish_scan(scope)
    audit.finish_callback(scope)
    report = audit.report()
    assert calls == [1, 1]
    assert len(report['operations']) == 1
    assert report['operations'][0]['keys'] == ['a', 'b'] and report['operations'][0]['actual_end'] == 2


def test_failed_material_change_is_charged_and_does_not_read_ledger():
    values = ready()
    audit, ledger, op, _ = values
    scope = audit.begin(context())
    before = audit._work
    op['source'] = {'cycle': op}
    with pytest.raises(ReactionResourceError): prepare(values, scope)
    assert audit._work > before and ledger.reads == 0


def test_final_report_size_is_below_preallocated_catalog_bytes():
    from genshin_engine.resource_reaction_program import _canonical
    values = ready()
    audit, ledger, _, _ = values
    ctx = context()
    ctx['parent_event_id'] = '中\\"' * 100
    slot = prepare(values, audit.begin(ctx))
    ledger.cursor = 1
    audit.committed(slot)
    report = audit.report()
    assert len(_canonical(report)) <= report['usage']['catalog_bytes_reserved']


def test_cursor_read_failure_after_return_does_not_report_uncommitted():
    values = ready()
    audit, ledger, _, _ = values
    slot = prepare(values, audit.begin(context()))
    ledger.cursor = 1
    def fail(): raise ValueError('injected getter')
    ledger.receipt_cursor = fail
    with pytest.raises(ReactionResourceError): audit.committed(slot)
    row = audit.report()['operations'][0]
    assert row['state'] == 'committed_inconsistent' and row['failure_class'] == 'cursor_unavailable'


def test_global_budget_stop_keeps_prefix_and_prevents_new_input_work(monkeypatch):
    values = ready()
    audit, ledger, _, _ = values
    slot = prepare(values, audit.begin(context()))
    ledger.cursor = 1
    audit.committed(slot)
    monkeypatch.setitem(audit.limits, 'work', audit._work + audit._tickets)
    with pytest.raises(ReactionResourceError): audit.begin(context(1))
    before = audit._work
    with pytest.raises(AuditStopped): audit.begin(object())
    report = audit.report()
    assert audit._work == before and report['operations'][0]['state'] == 'committed'


def test_true_unknown_decision_is_distinct_and_has_no_ledger_read():
    values = ready()
    audit, ledger, _, _ = values
    prepare(values, audit.begin(context()), 'unknown_condition')
    row = audit.report()['operations'][0]
    assert row['state'] == 'unknown_condition' and ledger.reads == 0
