from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_callback_runtime import AttributeCallbackRuntime
from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_callback_runtime import callback_case
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_runtime import reader
from tests.test_beneficiary_eligibility_core import QUALIFY, checked, scene
from tests.test_mechanics_execution import add, condition, rule, second_character
from tests.test_swirl_dynamic_v3 import sampled, setup


def em_scene(contract=None, *, sample='current', scope='locked'):
    t, r, m = scene(scope=scope)
    for char in t['characters']:
        char['stats']['em'] = 100
    m['rules'][0]['effects'][0].update(benefit_type_id='benefit.em.all', value=200)
    read = reader(owner='b', duration=5, sample=sample)
    if sample == 'event':
        condition(m, 'observed', .55)
        read['effects'][0]['value']['event_id'] = 'observed'
        read['trigger'] = {'event': 'condition', 'phase': 'post', 'event_ids': ['observed']}
    if contract:
        m['attribute_model'] = {'contract': contract, 'initial_basis': [
            {'actor': c['id'], 'included_components': [],
             'nonextra': {'mode': 'panel_total', 'status': 'assumed', 'source': 'synthetic'}}
            for c in t['characters']], 'effect_classifications': []}
        classify(m, 'qualified', 'a', 'included')
        if contract == 'attribute-ledger-v3':
            for initial in m['attribute_model']['initial_basis']:
                initial['stat'] = 'em'
            m['attribute_model']['initial_basis'].extend(
                {'actor': c['id'], 'stat': 'atk', 'included_components': []} for c in t['characters'])
    else:
        read['effects'][0]['value'].pop('basis')
    add(m, read)
    return t, r, m


@pytest.mark.parametrize('contract', [None, 'attribute-ledger-v1', 'attribute-ledger-v2', 'attribute-ledger-v3'])
def test_background_em_is_available_until_right_endpoint(contract):
    out = checked(*em_scene(contract))
    samples = [s['source_value'] for s in out['sampling_trace'] if s['rule_id'] == 'reader' and s['cycle'] == 0]
    assert samples == [100, 300, 300, 300, 300, 100, 100, 100]
    if contract:
        for receipt in out['attribute_ledger']['receipts']:
            assert all(c.get('component', {}).get('recipient') != 'a' for c in receipt['contributions']
                       if c.get('component', {}).get('effect_id') == 'value')


@pytest.mark.parametrize('contract', [None, 'attribute-ledger-v1', 'attribute-ledger-v2', 'attribute-ledger-v3'])
def test_event_snapshot_retains_qualified_recipient_after_expiry(contract):
    out = checked(*em_scene(contract, sample='event'))
    assert {s['source_value'] for s in out['sampling_trace'] if s['rule_id'] == 'reader' and s['cycle'] == 0} == {300}


def test_snapshot_current_frontline_constraint_is_evaluated_in_that_frame():
    out = checked(*em_scene('attribute-ledger-v2', scope='current_frontline'))
    samples = [s['source_value'] for s in out['sampling_trace'] if s['rule_id'] == 'reader' and s['cycle'] == 0]
    assert samples == [100, 300, 100, 100, 100, 100, 100, 100]


def test_dynamic_swirl_reads_only_actual_qualified_sources():
    t, r, m = setup((.2,))
    second_character(t, r)
    add(m, rule('self-em', 'benefit.em.all', 200, beneficiaries={'mode': 'self', 'scope': 'locked'}),
        rule('teammate-em', 'benefit.em.all', 200,
            beneficiaries={'mode': 'frontline', 'scope': 'locked', 'eligibility': copy.deepcopy(QUALIFY)}))
    out = checked(t, r, m)
    assert [s['value'] for s in sampled(out)] == [200]
    assert not any(x['type'] == 'activation' and x['rule_id'] == 'teammate-em' for x in out['mechanics_trace'])


def test_callback_empty_member_is_committed_without_numerical_read():
    t, r, m = callback_case()
    m['rules'][0]['beneficiaries'] = {'mode': 'frontline', 'scope': 'locked', 'eligibility': copy.deepcopy(QUALIFY)}
    out = checked(t, r, m)
    assert not any(x['rule_id'] == 'share' for x in out['sampling_trace'])
    assert not any(x['type'] == 'activation' and x['rule_id'] == 'share' for x in out['mechanics_trace'])
    assert any(x['type'] == 'callback_committed' and x['rule_id'] == 'share' for x in out['attribute_callbacks']['trace'])
    nodes = [n for p in out['attribute_callbacks']['plans'] for n in p.get('nodes', [])]
    # Actual callback completion must remain even for an empty write footprint.
    assert [s['value'] for s in sampled(out)] == [80, 200]
    assert all(not n.get('reads') and not n.get('writes') for n in nodes if n.get('rule_id') == 'share')


def test_callback_qualification_mutation_does_not_reuse_old_authority(monkeypatch):
    t, r, m = callback_case()
    m['rules'][0]['beneficiaries'] = {'mode': 'frontline', 'scope': 'locked', 'eligibility': copy.deepcopy(QUALIFY)}
    old = AttributeCallbackRuntime.claim
    def corrupt(self, token, event, phase, parent):
        self.executor.rules['share']['beneficiaries']['eligibility']['exclude_owner'] = False
        return old(self, token, event, phase, parent)
    monkeypatch.setattr(AttributeCallbackRuntime, 'claim', corrupt)
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    assert any(i['code'] == 'causal_authority' for i in out['issues'])


def test_real_other_recipient_unknown_writer_still_blocks_graph_read():
    t, r, m = em_scene('attribute-ledger-v2')
    m['attribute_model']['effect_classifications'] = []
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    assert any(i['code'] == 'attribute_component_missing' for i in out['issues'])
    assert any(x['type'] == 'hit' and x['event_id'] == 'h0' for x in out['mechanics_trace'])


def test_indirect_other_actor_dependency_is_not_removed_by_owner_exclusion():
    t, r, m = em_scene('attribute-ledger-v2')
    m['rules'][1]['owner'] = 'a'
    add(m, rule('b-to-a', 'benefit.em.all', {'kind': 'stat', 'subject': 'owner', 'stat': 'em',
        'sample': 'current', 'basis': 'total', 'factor': 1}, owner='b', duration=5,
        beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}))
    classify(m, 'b-to-a', 'b', 'included')
    known = checked(t, r, m)
    samples = [s['source_value'] for s in known['sampling_trace'] if s['rule_id'] == 'reader' and s['cycle'] == 0]
    assert samples == [200, 400, 400, 400, 400, 200, 200, 200]
    m['attribute_model']['effect_classifications'] = [x for x in m['attribute_model']['effect_classifications']
                                                    if x['effect_ref']['rule_id'] != 'qualified']
    unknown = execute_mechanics(t, r, m)
    assert unknown['result'] is None
    assert any(i['code'] == 'attribute_component_missing' for i in unknown['issues'])
