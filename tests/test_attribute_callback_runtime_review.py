"""Independent real-queue callback scope and actual-prefix checks; no game claim."""
from __future__ import annotations

import copy

import pytest

from genshin_engine import attribute_callback_runtime as runtime_module
from genshin_engine.attribute_callback_runtime import AttributeCallbackRuntime
from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_callback_runtime import callback_case
from tests.test_attribute_graph_runtime import classify, conversion
from tests.test_mechanics_execution import add, condition, rule
from tests.test_swirl_dynamic_v3 import sampled


def run(args):
    result = execute_mechanics(*args)
    assert result['result'] is not None, result['issues']
    return result


@pytest.mark.parametrize('provider', ['a', 'b'])
def test_historical_nonmember_does_not_rebind_old_read_to_current_write(provider):
    t, r, m = callback_case()
    m['attribute_model']['initial_basis'][1]['nonextra'] = {
        'mode': 'explicit', 'value': 500, 'status': 'assumed', 'source': 'Synthetic known independent seed'}
    t['characters'][1]['stats']['em'] = 500
    outsider = conversion('old', owner=provider, recipient='b', sample='event',
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']})
    outsider['effects'][0]['value']['event_id'] = 'cast'
    add(m, outsider)
    classify(m, 'old', provider, 'included')
    out = run((t, r, m))
    assert [row['value'] for row in sampled(out)] == [80, 240]
    assert any(x['type'] == 'activation' and x['rule_id'] == 'old' for x in out['mechanics_trace'])
    # No b hit consumes this isolated effect. It must remain lazy rather than
    # being evaluated and rebound merely to build a's callback proof.
    assert not any(x['rule_id'] == 'old' for x in out['sampling_trace'])
    assert outsider['effects'][0]['value']['event_id'] == 'cast'


@pytest.mark.parametrize('basis,inclusion,blocked', [
    ('base', 'unknown', False), ('nonextra', 'excluded', False),
    ('nonextra', 'unknown', True), ('total', 'excluded', True)])
def test_potential_indirect_nonmember_read_preserves_basis_pruning(basis, inclusion, blocked):
    t, r, m = callback_case()
    link = conversion('link', owner='a', recipient='b', sample='current')
    add(m, link, rule('reader', owner='b', value={'kind': 'stat', 'stat': 'em',
        'subject': 'owner', 'sample': 'trigger', 'basis': basis, 'factor': .01},
        beneficiaries={'mode': 'self', 'scope': 'locked'},
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}))
    classify(m, 'link', 'a', inclusion)
    if basis == 'base':
        m['attribute_model']['initial_basis'][1]['base'] = {
            'mode': 'explicit', 'value': 0, 'status': 'assumed', 'source': 'Synthetic base, independent of conversions'}
    out = execute_mechanics(t, r, m)
    if blocked:
        assert out['status'] == 'pending' and out['result'] is None
        assert any(i['code'] == 'causal_nonmember_interference' for i in out['issues'])
        assert not any(x['type'] == 'activation' and x['rule_id'] == 'ordinary' for x in out['mechanics_trace'])
    else:
        assert out['result'] is not None, out['issues']
        assert [row['value'] for row in sampled(out)] == [80, 240]


def test_post_new_current_writer_is_considered_before_it_has_an_instance():
    args = callback_case()
    m = args[2]
    add(m, conversion('new-link', owner='a', recipient='b', sample='current',
        trigger={'event': 'elemental_reaction', 'phase': 'post'}),
        rule('reader', owner='b', value={'kind': 'stat', 'stat': 'em', 'subject': 'owner',
            'sample': 'trigger', 'basis': 'nonextra', 'factor': .01},
            beneficiaries={'mode': 'self', 'scope': 'locked'}, trigger={'event': 'hit', 'phase': 'post'}))
    classify(m, 'new-link', 'a', 'included')
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'causal_nonmember_interference' for i in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_unknown_related_nonmember_cannot_be_removed_as_if_false():
    args = callback_case()
    condition(args[2], 'never-provided', .6)
    add(args[2], rule('maybe', 'benefit.em.all', 500,
        beneficiaries={'mode': 'self', 'scope': 'locked'},
        trigger={'event': 'elemental_reaction', 'phase': 'post', 'condition': 'never-provided'}))
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'causal_nonmember_interference' for i in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_declaration_list_permutation_preserves_numeric_order_and_parent_sample():
    args = callback_case()
    expected = run(copy.deepcopy(args))
    args[2]['rules'].reverse()
    args[2]['attribute_callbacks']['members'].reverse()
    actual = run(args)
    assert [(h['id'], h['expected'], h['zones']) for h in actual['result']['hits']] == [
        (h['id'], h['expected'], h['zones']) for h in expected['result']['hits']]
    assert [x['rule_id'] for x in actual['attribute_callbacks']['trace']
            if x['type'] == 'callback_committed' and x['parent_event_id'] == 'h0'] == ['ordinary', 'share']


def test_copied_callback_capability_is_not_accepted_even_when_content_matches(monkeypatch):
    original = AttributeCallbackRuntime.claim
    def copied(self, token, event, phase, parent):
        return original(self, copy.deepcopy(token), event, phase, parent)
    monkeypatch.setattr(AttributeCallbackRuntime, 'claim', copied)
    out = execute_mechanics(*callback_case())
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(i['code'] == 'causal_authority' for i in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_output_budget_failure_keeps_actual_parent_but_no_unrecorded_commit(monkeypatch):
    monkeypatch.setattr(runtime_module, 'MAX_BYTES', 1)
    out = execute_mechanics(*callback_case())
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'causal_runtime_output_limit' for i in out['issues'])
    assert any(x['type'] == 'hit' and x['event_id'] == 'h0' for x in out['mechanics_trace'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])
    assert out['attribute_callbacks']['usage']['output_bytes'] <= 1
