"""Independent pure causal normalization and single-occurrence scope checks."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_engine.attribute_causality import (
    AttributeCallbackPlanner,
    AttributeCausalityStopped,
)
from tests.test_attribute_causality import code, model, node, pair, run


def test_same_parent_distinct_reaction_occurrences_cannot_share_single_reaction_batch():
    a, b = node('a', write='a'), node('b', write='b')
    for item, uid in ((a, 'reaction-one'), (b, 'reaction-two')):
        item['callback'].update(kind='reaction_post', actual_run_id=uid, event_id=uid)
    out = run(model(a, b))
    assert out['status'] != 'ready'
    assert 'batches' not in out


def test_footprint_and_activation_set_reordering_preserves_full_receipt():
    writer = node('写者', write='a', activation_keys=('mod:z', 'mod:a'))
    reader = node('读者', read=('a', 'nonextra'), dependencies=(('b', 'total'), ('c', 'nonextra')))
    second_read = copy.deepcopy(reader['reads'][0])
    second_read['id'] = 'other-read'
    reader['reads'].append(second_read)
    data = model(writer, reader, edges=[('写者', '读者')])
    before = copy.deepcopy(data)
    reversed_data = copy.deepcopy(data)
    reversed_data['nodes'].reverse()
    for item in reversed_data['nodes']:
        item['activation_keys'].reverse()
        item['reads'].reverse()
        for read in item['reads']:
            read['current_dependencies'].reverse()
    assert run(data) == run(reversed_data)
    assert data == before


def test_excluded_nonextra_write_still_conflicts_on_shared_unique_component():
    a, b = node('a', write='a', inclusion='excluded'), node('b', write='a', inclusion='excluded')
    b['writes'][0]['component'] = copy.deepcopy(a['writes'][0]['component'])
    reader = node('reader', read=('a', 'nonextra'))
    out = run(model(a, b, reader))
    assert out['status'] == 'pending' and code(out) == 'causal_mutation_conflict'


def test_opaque_actual_parent_and_same_reaction_multiple_listeners_are_consistent():
    a, b = node('a', write='a'), node('b', read=('a', 'total'))
    data = model(a, b, edges=[('a', 'b')])
    data['parent']['actual_run_id'] = '@actual:父'
    for item in data['nodes']:
        item['callback'].update(kind='reaction_post', actual_run_id='@actual:反应',
                                parent_actual_run_id='@actual:父', event_id='reaction.route')
    out = run(data)
    assert out['status'] == 'ready' and out['batches'] == [['a'], ['b']]


@pytest.mark.parametrize('change', [
    lambda p: p['nodes'][1]['reads'][0]['current_dependencies'].append({'actor': 'a', 'stat': 'em', 'basis': 'nonextra'}),
    lambda p: p['nodes'][0]['activation_keys'].extend(['same', 'same']),
])
def test_duplicate_footprint_claim_cannot_be_normalized_away(change):
    data = pair(edges=[('writer', 'reader')])
    change(data)
    out = run(data)
    assert out['status'] == 'invalid' and code(out) == 'causal_duplicate_identity'


def test_one_execution_retains_work_from_invalid_attempt_before_valid_parent():
    data = pair(edges=[('writer', 'reader')])
    data['nodes'][0]['callback']['origin'] = 999
    planner = AttributeCallbackPlanner()
    failure = planner.plan(data)
    first_usage = planner.usage()['execution_work']
    success = planner.plan(model(node('valid')))
    assert failure['status'] == 'invalid' and first_usage > 0
    assert success['status'] == 'ready'
    assert success['work']['execution_total'] == first_usage+success['work']['this_plan']


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf8'))


def test_success_and_nonterminal_failure_charge_actual_utf8_output_bytes():
    planner = AttributeCallbackPlanner()
    failure = planner.plan(model(node('读者', read=('a', 'nonextra')), node('写者', write='a')))
    assert failure['status'] == 'pending' and not failure.get('terminal')
    assert planner.usage()['receipt_bytes'] == encoded_size(failure)
    success = planner.plan(model(node('独立节点')))
    assert success['status'] == 'ready'
    assert planner.usage()['receipt_bytes'] == encoded_size(failure)+encoded_size(success)


def test_exhaustion_emits_exactly_one_terminal_diagnostic_then_refuses_input_traversal(monkeypatch):
    data = model(node('valid'))
    first_size = encoded_size(run(data))
    planner = AttributeCallbackPlanner(limits={'receipt_bytes': first_size+10})
    assert planner.plan(data)['status'] == 'ready'
    terminal = planner.plan(pair())
    assert terminal.get('terminal') and code(terminal) == 'causal_output_limit'
    assert not {'batches', 'relations', 'materialized_input', 'fingerprint'} & terminal.keys()
    usage = planner.usage()
    assert usage['receipt_bytes'] == first_size
    assert usage['terminal_diagnostic_bytes'] == encoded_size(terminal) <= 2048
    monkeypatch.setattr(planner, '_normalize', lambda *args: pytest.fail('Stopped planner traversed input'))
    with pytest.raises(AttributeCausalityStopped):
        planner.plan(data)
    assert planner.usage() == usage
