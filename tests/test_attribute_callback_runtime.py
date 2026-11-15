"""Real-queue C2 oracles, first recorded before runtime implementation."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_callback_runtime import (
    AttributeCallbackRuntime,
    validate_bindings,
)
from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
from tests.test_attribute_graph_runtime import classify, conversion, enable_graph
from tests.test_elemental_swirl import swirl_model
from tests.test_mechanics_execution import add, condition, rule
from tests.test_swirl_dynamic_v3 import sampled, setup


def callback_case(order='write_before_read'):
    t, r, m = setup((.2, .4), element='cryo')
    enable_graph(t, r, m, a=80, b=0)
    add(m, conversion('share', owner='a', recipient='a', sample='trigger', factor=.2,
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}),
        rule('ordinary', 'benefit.em.all', 120,
            beneficiaries={'mode': 'self', 'scope': 'locked'},
            trigger={'event': 'elemental_reaction', 'phase': 'post'}))
    classify(m, 'share', 'a', 'excluded')
    classify(m, 'ordinary', 'a', 'included')
    before, after = ('ordinary', 'share') if order == 'write_before_read' else ('share', 'ordinary')
    m['attribute_callbacks'] = {
        'contract': 'mechanics-attribute-callbacks-v1', 'members': ['share', 'ordinary'],
        'edges': [{'before_rule_id': before, 'after_rule_id': after, 'assumption_id': 'fixture-order'}],
        'assumptions': [{'id': 'fixture-order', 'note': 'Synthetic explicit order; no game-source claim.'}]}
    return t, r, m


@pytest.mark.parametrize('order,source_value,second_swirl', [
    ('write_before_read', 200, 240), ('read_before_write', 80, 216)])
def test_actual_post_order_controls_trigger_sample_without_retroactive_parent(order, source_value, second_swirl):
    t, r, m = callback_case(order)
    legacy_model = copy.deepcopy(m)
    legacy_model.pop('attribute_callbacks')
    legacy = execute_mechanics(t, r, legacy_model)
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert out['result'] is not None
    assert [x['value'] for x in sampled(out)] == [80, second_swirl]
    assert {x['source_value'] for x in out['sampling_trace'] if x['rule_id'] == 'share'} == {source_value}
    # First parent and its Swirl already own the pre-callback reaction sample.
    for key in ('h0', 'h0:reaction_damage:0'):
        expected = [x for x in legacy['result']['hits'] if x['id'] == key]
        assert len(expected) == 1
        actual = [x for x in out['result']['hits'] if x['id'] == key]
        assert actual == expected


def test_missing_related_order_does_not_fall_back_to_rule_id_sorting():
    t, r, m = callback_case()
    m['attribute_callbacks'].update(edges=[], assumptions=[])
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(x['code'] == 'causal_order_missing' for x in out['issues'])


def test_native_caller_cannot_claim_source_approval_or_supply_dependency_rows():
    t, r, m = callback_case()
    m['attribute_callbacks']['assumptions'][0]['status'] = 'approved'
    m['attribute_callbacks']['current_dependencies'] = []
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid' and out['result'] is None


def test_no_contract_retains_original_queue_boundary():
    t, r, m = callback_case()
    m.pop('attribute_callbacks')
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven')
    assert [x['value'] for x in sampled(out)] == [80, 216]
    assert {x['source_value'] for x in out['sampling_trace'] if x['rule_id'] == 'share'} == {80}


def test_outsider_related_writer_stops_before_post_activation_and_keeps_parent():
    t, r, m = callback_case()
    m['attribute_callbacks'].update(members=['share'], edges=[], assumptions=[])
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'causal_nonmember_interference' for i in out['issues'])
    assert any(x['type'] == 'hit' and x['event_id'] == 'h0' for x in out['mechanics_trace'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_disjoint_unknown_outsider_does_not_block_known_actor():
    t, r, m = callback_case()
    add(m, rule('outsider', 'benefit.em.all', 20, owner='b',
        beneficiaries={'mode': 'self', 'scope': 'locked'}, trigger={'event': 'hit', 'phase': 'post'}))
    # No component declaration is needed to prove that this potential writer is
    # disjoint from all causal reads/writes of a. A later read of b would block.
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 240]


@pytest.mark.parametrize('inclusion,basis,status', [('unknown', 'total', 'known'),
    ('unknown', 'nonextra', 'pending'), ('excluded', 'nonextra', 'known')])
def test_classification_is_consulted_only_by_actual_basis(inclusion, basis, status):
    t, r, m = callback_case()
    m['rules'][0]['effects'][0]['value']['basis'] = basis
    m['attribute_model']['effect_classifications'][1]['nonextra_inclusion'] = inclusion
    if inclusion == 'excluded':
        m['attribute_callbacks'].update(edges=[], assumptions=[])
    out = execute_mechanics(t, r, m)
    if status == 'pending':
        assert out['status'] == 'pending' and out['result'] is None
        assert any(x['code'] == 'attribute_classification_unknown' for x in out['issues'])
    else:
        assert out['status'] in ('passed', 'unproven'), out['issues']
        assert [x['value'] for x in sampled(out)] == [80, 216 if inclusion == 'excluded' else 240]


def test_static_component_duplicate_is_not_hidden_by_nonextra_exclusion():
    t, r, m = callback_case()
    item = m['attribute_model']['effect_classifications'][1]
    item['nonextra_inclusion'] = 'excluded'
    m['attribute_model']['initial_basis'][0]['included_components'] = [
        {'component': item['component'], 'value': 0, 'nonextra_inclusion': 'excluded'}]
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(x['code'] == 'attribute_duplicate_delivery' for x in out['issues'])


def test_cooldown_rejection_is_collected_once_and_does_not_fall_back():
    t, r, m = callback_case()
    m['initial_state']['rules'][1]['cooldown_remaining'] = .3
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 96]
    rejected = [x for x in out['mechanics_trace'] if x['type'] == 'trigger_rejected'
                and x['rule_id'] == 'ordinary' and x['t'] == .2]
    assert len(rejected) == 1
    directory = out['attribute_callbacks']['trace'][0]['directory']
    assert next(x for x in directory if x['rule_id'] == 'ordinary')['decision'] == 'internal_cooldown'


@pytest.mark.parametrize('mutation', ['model', 'directory', 'plan', 'parent', 'events', 'fake_token'])
def test_phase_authority_rejects_mutation_or_foreign_objects(monkeypatch, mutation):
    original = AttributeCallbackRuntime.claim
    def corrupt(self, token, event, phase, parent):
        if mutation == 'model':
            self.executor.model['rules'][0]['source'] += 'changed'
        elif mutation == 'directory':
            token.directory[0]['decision'] = 'no_match'
        elif mutation == 'plan':
            token.plan['batches'] = []
        elif mutation == 'parent':
            token.parent['run_id'] = 'foreign'
        elif mutation == 'events':
            token.events[0]['conditions']['forged'] = True
        else:
            token = object()
        return original(self, token, event, phase, parent)
    monkeypatch.setattr(AttributeCallbackRuntime, 'claim', corrupt)
    out = execute_mechanics(*callback_case())
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(i['code'] == 'causal_authority' for i in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_second_batch_failure_keeps_first_actual_commit_and_parent_prefix(monkeypatch):
    original = AttributeCallbackRuntime._charge
    def stop_after_commit(self, count=1):
        if any(row['type'] == 'callback_committed' for row in self.trace):
            raise AttributeLedgerError('causal_runtime_work_limit', 'Synthetic exhausted execution budget', status='pending')
        return original(self, count)
    monkeypatch.setattr(AttributeCallbackRuntime, '_charge', stop_after_commit)
    out = execute_mechanics(*callback_case())
    assert out['status'] == 'pending' and out['result'] is None
    committed = [x for x in out['attribute_callbacks']['trace'] if x['type'] == 'callback_committed']
    assert [x['rule_id'] for x in committed] == ['ordinary']
    assert [x['rule_id'] for x in out['mechanics_trace'] if x['type'] == 'activation'] == ['ordinary']
    assert any(x['type'] == 'hit' and x['event_id'] == 'h0' for x in out['mechanics_trace'])
    assert out['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_missed_parent_has_no_callback_plan_or_activation():
    t, r, m = callback_case()
    m['hit_events'][0]['application']['landed'] = False
    out = execute_mechanics(t, r, m)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert not any(x['type'] == 'callback_plan' and x['parent_run_id'] == '0:h0'
                   for x in out['attribute_callbacks']['trace'])
    assert not any(x['type'] == 'activation' and x['rule_id'] == 'share' for x in out['mechanics_trace'])


def _add_disjoint_callback(args):
    t, _r, m = args
    m['elemental_model'] = swirl_model(None)
    m['elemental_model']['ruleset'] = 'gauge-swirl-dynamic-v3'
    for hit, descriptor in zip(t['hits'], m['hit_events'], strict=True):
        descriptor['application'] = {'profile_id': hit['element'], 'target_id': 'enemy', 'landed': True}
    for char in t['characters']:
        char['stats']['em'] = 80
    m['attribute_model'] = {'contract': 'attribute-ledger-v2', 'initial_basis': [
        {'actor': c['id'], 'included_components': [], 'nonextra': {'mode': 'panel_total',
            'status': 'assumed', 'source': 'Synthetic unchanged seed'}} for c in t['characters']],
        'effect_classifications': []}
    add(m, rule('causal-em', 'benefit.em.all', 120, trigger={'event': 'hit', 'phase': 'post'}))
    classify(m, 'causal-em', 'a', 'included')
    m['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v1',
        'members': ['causal-em'], 'edges': [], 'assumptions': []}
    return args


@pytest.mark.parametrize('timer_order', [5, 20])
def test_state_observer_once_and_same_tick_timer_on_both_sides_unchanged(monkeypatch, timer_order):
    from tests.test_state_runtime_execution import field_case
    args = _add_disjoint_callback(field_case(timer_order=timer_order))
    args[0]['hits'][0]['t'] = 1
    args[2]['hit_events'][0]['order'] = 12
    legacy_args = copy.deepcopy(args)
    legacy_args[2].pop('attribute_callbacks')
    legacy = execute_mechanics(*legacy_args)
    observed = []
    original = StateRuntimeMechanics.observe
    def observe(self, event, phase, parent, *, successful):
        queued = original(self, event, phase, parent, successful=successful)
        if event['event'] == 'hit':
            observed.append((parent['run_id'], phase, len(queued)))
        return queued
    monkeypatch.setattr(StateRuntimeMechanics, 'observe', observe)
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert out['result']['hits'] == legacy['result']['hits']
    assert out['state_runtime_trace'] == legacy['state_runtime_trace']
    assert len(observed) == len(set(observed)) == 10 and all(q == 0 for _, _, q in observed)


def test_pool_pre_hit_read_post_transaction_order_preserves_real_counter_values():
    from tests.test_resource_counter_view_execution import reads, view_case
    args = _add_disjoint_callback(view_case(times=(.2, .3)))
    m = args[2]
    m['state_runtime']['timers'] = []
    operation = m['shared_pools']['operations'][0]
    operation['trigger'] = {'event': 'hit', 'phase': 'pre', 'event_ids': ['h0']}
    later = copy.deepcopy(operation)
    later.update(id='later', order=2)
    later['trigger']['phase'] = 'post'
    m['shared_pools']['operations'].append(later)
    m['resource_counters']['counters'][0]['operation_ids'].append('later')
    legacy_args = copy.deepcopy(args)
    legacy_args[2].pop('attribute_callbacks')
    legacy = execute_mechanics(*legacy_args)
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == [81, 117]
    assert [x['reading']['counter_cursor']['ledger_sequence'] for x in reads(out)] == [1, 2]
    assert out['shared_pool_trace'] == legacy['shared_pool_trace']
    assert out['resource_counters']['trace'] == legacy['resource_counters']['trace']


def test_same_batch_members_use_one_prestate_without_observed_event_authority(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    t, r, m = callback_case()
    m['attribute_model']['effect_classifications'][1]['nonextra_inclusion'] = 'excluded'
    m['attribute_callbacks'].update(edges=[], assumptions=[])
    captured = []
    original = _Mechanics._commit_callback
    def commit(self, rule, event, beneficiaries, snapshot, parent):
        if self.time == 200000:
            captured.append(snapshot['attribute_frame']['frame'])
        return original(self, rule, event, beneficiaries, snapshot, parent)
    monkeypatch.setattr(_Mechanics, '_commit_callback', commit)
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert len(captured) == 2 and captured[0] == captured[1]
    assert 'observation' not in captured[0]
    callbacks = out['attribute_callbacks']['plans'][0]['materialized_input']['nodes']
    hit = next(x for x in callbacks if x['callback']['kind'] == 'hit_post')
    reaction = next(x for x in callbacks if x['callback']['kind'] == 'reaction_post')
    assert hit['callback']['actual_run_id'] == '0:h0'
    assert reaction['callback']['actual_run_id'] == '0:h0:reaction:0'


def test_same_time_next_parent_observes_commit_but_does_not_alias_old_trigger():
    t, r, m = callback_case()
    t['hits'][1]['t'] = .2
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 240]
    assert {p['materialized_input']['parent']['actual_run_id'] for p in out['attribute_callbacks']['plans'][:2]} == {'0:h0', '0:h1'}
    assert {x['source_value'] for x in out['sampling_trace'] if x['rule_id'] == 'share'} == {200}


def test_expired_writer_does_not_change_already_captured_share_or_reappear_early():
    t, r, m = callback_case()
    m['rules'][1]['timing']['duration'] = .2
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 120]
    assert {x['source_value'] for x in out['sampling_trace'] if x['rule_id'] == 'share'} == {200}


@pytest.mark.parametrize('known', [False, True])
def test_complete_qualification_directory_never_rechecks_false_or_unknown(monkeypatch, known):
    from genshin_engine.mechanics import _Mechanics
    t, r, m = callback_case()
    m['rules'][1]['trigger']['condition'] = 'enabled'
    condition(m, 'enabled', .1 if known else .6)
    if known:
        m['condition_events'][0]['value'] = False
    calls = []
    original = _Mechanics._filter_decision
    def decision(self, selector, event, rid, **kwargs):
        if rid == 'ordinary' and self.time == 200000 and event['event'] == 'elemental_reaction':
            calls.append(event['id'])
        return original(self, selector, event, rid, **kwargs)
    monkeypatch.setattr(_Mechanics, '_filter_decision', decision)
    out = execute_mechanics(t, r, m)
    assert calls == ['h0:reaction:0']
    if known:
        assert out['result'] is not None, out['issues']
        assert [x['value'] for x in sampled(out)] == [80, 96]
        directory = out['attribute_callbacks']['trace'][0]['directory']
        assert next(x for x in directory if x['rule_id'] == 'ordinary')['decision'] == 'no_match'
        assert not any(x['type'] == 'activation' and x['rule_id'] == 'ordinary' for x in out['mechanics_trace'])
    else:
        assert out['status'] == 'pending' and out['result'] is None
        assert any(x['code'] == 'causal_qualification_unknown' for x in out['issues'])
        assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


def test_commit_token_cannot_be_replayed_after_completion(monkeypatch):
    original = AttributeCallbackRuntime.commit
    def duplicate(self, token):
        original(self, token)
        original(self, token)
    monkeypatch.setattr(AttributeCallbackRuntime, 'commit', duplicate)
    out = execute_mechanics(*callback_case())
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(i['code'] == 'causal_authority' for i in out['issues'])
    assert [x['rule_id'] for x in out['mechanics_trace'] if x['type'] == 'activation'] == ['ordinary', 'share']


def test_explicit_actor_frame_seed_and_basis_are_preserved_in_dependency_reports():
    out = execute_mechanics(*callback_case())
    assert out['result'] is not None, out['issues']
    report = out['attribute_callbacks']
    assert report['source_authority'] == 'caller_assumed_order_not_game_source_approval'
    assert all(p['materialized_input']['parent']['actual_run_id'] for p in report['plans'])
    assert all(f['query']['basis'] == 'nonextra' and f['query']['actor'] == 'a'
               and f['query']['frame']['state_epoch'] >= 0 for f in report['footprints'])
    assert all('parent_event_id' in row and 'time_us' in row and 'origin' in row for row in report['trace'])
    assert all('frame_id' in row and 'owner' in row and 'source_id' in row
               for row in report['trace'] if row['type'] == 'callback_committed')


@pytest.mark.parametrize('provider', ['a', 'b'])
def test_unrelated_historical_reader_stays_on_original_phase(provider):
    t, r, m = callback_case()
    outsider = conversion('historical', owner=provider, recipient='b', sample='event',
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']})
    outsider['effects'][0]['value']['event_id'] = 'cast'
    add(m, outsider)
    classify(m, 'historical', provider, 'included')
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 240]
    assert any(x['type'] == 'activation' and x['rule_id'] == 'historical' for x in out['mechanics_trace'])


def test_historical_reader_with_related_output_must_join_ordered_scope():
    t, r, m = callback_case()
    outsider = conversion('historical', owner='b', recipient='a', sample='event',
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h0']})
    outsider['effects'][0]['value']['event_id'] = 'cast'
    add(m, outsider)
    classify(m, 'historical', 'b', 'included')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(x['code'] == 'causal_nonmember_interference' for x in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])


@pytest.mark.parametrize('introduced_in_post', [False, True])
def test_indirect_current_conversion_connects_other_actor_reader_to_member_write(introduced_in_post):
    t, r, m = callback_case()
    link = conversion('link', owner='a', recipient='b', sample='current')
    if introduced_in_post:
        link['trigger'] = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    add(m, link, rule('indirect-reader', owner='b', benefit='benefit.dmg_bonus.all',
        value={'kind': 'stat', 'stat': 'em', 'sample': 'trigger', 'subject': 'owner', 'basis': 'nonextra', 'factor': .01},
        beneficiaries={'mode': 'self', 'scope': 'locked'}, trigger={'event': 'elemental_reaction', 'phase': 'post'}))
    classify(m, 'link', 'a', 'included')
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(x['code'] == 'causal_nonmember_interference' for x in out['issues'])
    assert not any(x['type'] == 'activation' and x['rule_id'] == 'indirect-reader' for x in out['mechanics_trace'])


def test_unrelated_unclassified_current_chain_is_not_numerically_evaluated():
    t, r, m = callback_case()
    # b's unclassified self conversion is cyclic if read, but cannot consume or
    # affect a. C2 proves only independence and leaves normal use-site guards.
    add(m, conversion('unclassified-link', owner='b', recipient='b', basis='total', sample='current'),
        rule('unused-reader', owner='b', value={'kind': 'stat', 'stat': 'em', 'sample': 'trigger',
            'subject': 'owner', 'basis': 'total', 'factor': 1}, beneficiaries={'mode': 'self', 'scope': 'locked'},
            trigger={'event': 'hit', 'phase': 'post'}))
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [x['value'] for x in sampled(out)] == [80, 240]


@pytest.mark.parametrize('field', ['resource_threshold_observers', 'resource_threshold_triggers'])
def test_future_resource_callback_contract_does_not_inherit_observer_proof(field):
    m = callback_case()[2]
    m[field] = {}
    issues = validate_bindings(m)
    assert [row['code'] for row in issues] == ['causal_observer_scope_pending']


@pytest.mark.parametrize('mutation', [
    lambda m: m['attribute_callbacks'].update(members=[]),
    lambda m: m['attribute_callbacks']['members'].append('share'),
    lambda m: m['attribute_callbacks']['members'].append('missing'),
    lambda m: m['attribute_callbacks']['assumptions'].append({'id': 'unused', 'note': 'Not referenced'}),
    lambda m: m['attribute_callbacks']['edges'].append(copy.deepcopy(m['attribute_callbacks']['edges'][0])),
    lambda m: m['attribute_callbacks']['assumptions'][0].update(status='approved'),
])
def test_static_native_contract_is_closed_before_any_execution(mutation):
    args = callback_case()
    mutation(args[2])
    out = execute_mechanics(*args)
    assert out['status'] == 'invalid' and out['result'] is None
    assert out['mechanics_trace'] == []


def test_cycle_has_no_post_execution_prefix():
    t, r, m = callback_case()
    m['attribute_callbacks']['edges'].append({'before_rule_id': 'share', 'after_rule_id': 'ordinary', 'assumption_id': 'fixture-order'})
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(x['code'] == 'causal_cycle' for x in out['issues'])
    assert not any(x['type'] == 'activation' for x in out['mechanics_trace'])
