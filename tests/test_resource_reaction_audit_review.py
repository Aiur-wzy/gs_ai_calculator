"""Independent pure slot protocol tests; caller contexts do not certify events."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_reaction_program import Program, ReactionResourceError
from tests.test_resource_reaction_audit import context, prepare, ready


def test_program_replacement_cannot_change_the_bound_target_keys():
    values = ready()
    audit, _, op, pool = values
    # Use a real validated Program with the same operation/pool but a different
    # whole model, rather than a mock returning forged keys.
    from tests.test_resource_reaction_program import model
    other = model()
    other['shared_pools']['pools'][0].update(scope='per_character', initial={'a': 0, 'b': 0, 'c': 0})
    # The original audit itself was team-scoped, so its supplied material must
    # remain unchanged; foreign Program.verify_operation should reject it.
    audit.program = Program(other, ['a', 'b', 'c'])
    with pytest.raises(ReactionResourceError):
        prepare(values, audit.begin(context()))
    assert op['pool_id'] == pool['id']


def test_equivalent_operation_foreign_whole_graph_program_is_rejected():
    from genshin_engine.resource_reaction_audit import Audit
    from tests.test_resource_reaction_audit import Ledger
    from tests.test_resource_reaction_program import model
    value = model()
    pool = value['shared_pools']['pools'][0]
    original = Program(value, ['a', 'b'])
    ledger = Ledger()
    audit = Audit(original, ledger, lambda _: None)
    changed = copy.deepcopy(value)
    changed['rules'][0]['effects'][0]['value'] += 1
    audit.program = Program(changed, ['a', 'b'])
    with pytest.raises(ReactionResourceError):
        audit.prepare(audit.begin(context()), 'refill', 'match',
                      value['shared_pools']['operations'][0], pool, ledger)


def test_cursor_getter_cannot_export_a_planned_row_during_commit():
    values = ready()
    audit, ledger, _, _ = values
    slot = prepare(values, audit.begin(context()))
    ledger.cursor = 1
    def getter():
        with pytest.raises(ReactionResourceError, match='重入'):
            audit.report()
        return ledger.cursor
    ledger.receipt_cursor = getter
    audit.committed(slot)
    assert audit.report()['operations'][0]['state'] == 'committed'


@pytest.mark.parametrize('decision', ['no_match', 'unknown_condition'])
def test_selector_decision_does_not_borrow_cursor_or_activate_resources(decision):
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    prepare(values, scope, decision)
    audit.finish_scan(scope)
    audit.finish_callback(scope)
    out = audit.report()
    assert ledger.reads == 0 and ledger.cursor == 0
    assert out['operations'][0]['state'] == decision
    assert out['occurrences'][0]['callback_complete'] is True
    assert out['verification_scope'] == 'caller_supplied_context_slots_only'


@pytest.mark.parametrize('bad', [True, 10**400, -1])
def test_bad_postcommit_cursor_keeps_committed_inconsistent_without_raw_value(bad):
    values = ready()
    audit, ledger, _, _ = values
    slot = prepare(values, audit.begin(context()))
    ledger.cursor = bad
    with pytest.raises(ReactionResourceError):
        audit.committed(slot)
    out = audit.report()
    assert out['operations'][0]['state'] == 'committed_inconsistent'
    assert out['operations'][0]['actual_end'] is None
    assert out['termination'] == 'resource_reaction_cursor'


def test_scope_report_is_detached_from_caller_context_and_original_material():
    values = ready()
    audit, _, op, _ = values
    ctx = context()
    scope = audit.begin(ctx)
    prepare(values, scope, 'no_match')
    before = copy.deepcopy(ctx)
    ctx['parent_event_id'] = 'changed'
    op['source_id'] = 'changed'
    out = audit.report()
    assert out['occurrences'][0]['parent_event_id'] == before['parent_event_id']
    assert out['operations'][0]['source_id'] != 'changed'


@pytest.mark.parametrize('cursor', [None, False, 10**400])
def test_match_requires_a_real_bounded_precommit_cursor(cursor):
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    ledger.cursor = cursor
    with pytest.raises(ReactionResourceError):
        prepare(values, scope)
    out = audit.report()
    assert out['operations'] == [] and len(out['occurrences']) == 1
    assert out['termination'] == 'resource_reaction_cursor'


def test_precommit_cursor_exception_preserves_scope_without_planned_slot():
    values = ready()
    audit, ledger, _, _ = values
    scope = audit.begin(context())
    def failed_getter():
        raise RuntimeError('independent protocol failure')
    ledger.receipt_cursor = failed_getter
    with pytest.raises(ReactionResourceError):
        prepare(values, scope)
    out = audit.report()
    assert out['operations'] == [] and len(out['occurrences']) == 1
    assert out['termination'] == 'resource_reaction_cursor'
