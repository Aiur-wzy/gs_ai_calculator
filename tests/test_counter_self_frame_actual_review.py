"""Independent real-queue oracle targets; run v2 cases only in reviewed overlay."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import action, add, condition, rule
from tests.test_resource_counters import definitions
from tests.test_shared_pool_v2_execution import periodic_case
from tests.test_shared_pools import config


def arguments(*, historical=True, layers=1):
    # 31 atoms, scale10; spend6 at .1/.2/.3 -> counter6/12/18.
    # Flat additive damage input = scaled counter *20. Base100,
    # resistance/defence multiplier .45: damage =45+9*raw/10 per layer.
    args = periodic_case(times=(.17, .27, .37), initial=31)
    model = args[2]
    model['shared_pools']['operations'][0]['amount'] = 6
    model['resource_counters'] = definitions()[0]
    model['resource_counter_views'] = {'schema_version': 2, 'views': [{
        'id': 'independent-read', 'name_zh': '独立实际累计', 'owner': 'a', 'source_id': 'independent',
        'counter_id': 'count', 'key': {'kind': 'total'}, 'transform': {'kind': 'scaled'},
        'status': 'assumed', 'source': 'Engineering arithmetic only; no game source'}]}
    gain = rule('independent', benefit='benefit.base_add.all', domain='self', value={
        'kind': 'counter_view', 'view_id': 'independent-read', 'sample': 'self_frame', 'factor': 20})
    add(model, gain)
    if layers == 2:
        gain['stacking'].update(max=2, policy='independent')
        model['initial_state']['rules'][0].update(cooldown_remaining=100, instances=[{
            'beneficiaries': ['a'], 'remaining': [.22, .9], 'quota': {}}])
    if historical:
        condition(model, 'independent-output', .15, 90)
        for descriptor in model['hit_events']:
            descriptor['self_snapshot_event'] = 'independent-output'
    return args


def legacy(args):
    result = copy.deepcopy(args)
    result[2]['resource_counter_views']['schema_version'] = 1
    for gain in result[2]['rules']:
        gain['effects'][0]['domain'] = 'provider'
        gain['effects'][0]['value']['sample'] = 'current'
    return result


def actual(args, expected, matches=None):
    before = copy.deepcopy(args)
    output = execute_mechanics(*args, matches=matches)
    assert args == before
    assert output['result'] is not None, output['issues']
    assert [h['expected'] for h in output['result']['hits']] == pytest.approx(expected)
    return output


def reads(output):
    return [r for r in output['resource_counter_views']['trace'] if r['cycle'] == 0]


def test_public_v1_independent_numeric_control():
    output = actual(legacy(arguments()), [50.4, 55.8, 61.2])
    assert [r['reading']['raw_atoms'] for r in reads(output)] == [6, 12, 18]


def test_executed_history_uses_six_atoms_while_current_counter_advances():
    output = actual(arguments(), [50.4, 50.4, 50.4])
    assert [r['reading']['raw_atoms'] for r in reads(output)] == [6, 6, 6]
    assert output['resource_counters']['state']['values']['count']['total'] == 18


def test_old_two_layers_remain_two_after_current_layer_expires():
    args = arguments(layers=2)
    actual(legacy(args), [55.8, 55.8, 61.2])
    output = actual(args, [55.8, 55.8, 55.8])
    assert all(r['layer_count'] == 2 for r in reads(output))
    assert all(r['single_value'] == pytest.approx(12) for r in reads(output))


def replacement(*, gated):
    args = arguments()
    gain = args[2]['rules'][0]
    gain['timing']['duration'] = .2
    gain['trigger']['action_ids'].append('replacement')
    args[1]['actions'].append(action('replacement', start=.24, end=.25, order=40, repeat={'mode': 'once'}))
    if gated:
        consumer = config()['consumers'][0]
        consumer.update(id='independent-consumer', source_id='independent',
            eligibility_rule_id='independent', actor_relation='party_member', amount=1)
        args[2]['shared_pools']['consumers'] = [consumer]
        gain['resource_gate'] = {'consumer_id': consumer['id']}
    return args


def test_historical_member_survives_new_instance_when_no_current_gate():
    args = replacement(gated=False)
    actual(legacy(args), [50.4, 55.8, 61.2])
    output = actual(args, [50.4, 50.4, 50.4])
    assert len(reads(output)) == 3


def test_current_new_instance_debit_cannot_authorize_old_member_even_with_include():
    args = replacement(gated=True)
    overrides = [{'rule_id': 'independent', 'effect_id': 'value', 'hit_id': 'h1', 'operation': 'include'}]
    output = actual(args, [50.4, 45, 45], overrides)
    consumers = [r for r in output['shared_pool_trace'] if r['type'] == 'consume' and r['cycle'] == 0]
    assert [r['accepted'] for r in consumers] == [1, 1, 1]
    assert len(reads(output)) == 1


def same_time_arguments():
    args = arguments(historical=False)
    model = args[2]
    model['state_runtime']['timers'] = []
    first = model['shared_pools']['operations'][0]
    first['trigger'] = {'event': 'hit', 'phase': 'pre', 'event_ids': ['h0']}
    later = copy.deepcopy(first)
    later.update(id='after-output', order=20)
    later['trigger']['phase'] = 'post'
    model['shared_pools']['operations'].append(later)
    model['resource_counters']['counters'][0]['operation_ids'].append(later['id'])
    for descriptor in model['hit_events'][1:]:
        descriptor['self_snapshot_event'] = 'h0'
    return args


def test_same_time_pre_and_post_spends_capture_only_pre_transaction():
    args = same_time_arguments()
    actual(legacy(args), [50.4, 55.8, 55.8])
    output = actual(args, [50.4, 50.4, 50.4])
    assert [r['reading']['raw_atoms'] for r in reads(output)] == [6, 6, 6]
    assert output['resource_counters']['state']['values']['count']['total'] == 12


def test_observation_cannot_certify_cached_ledger_after_executor_ledger_replacement(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    from genshin_engine.shared_pools import SharedPoolLedger

    original = _Mechanics._observed_snapshot
    swapped = []

    def observe(ctx, event):
        if event['id'] == 'independent-output':
            prior = ctx.shared_pools.ledger
            ctx.shared_pools.ledger = SharedPoolLedger(ctx.shared_pools.config,
                character_ids=list(ctx.data['chars']), rule_ids=list(ctx.rules),
                counter_program=ctx.shared_pools.counter_program)
            assert ctx.shared_pools.ledger is not prior
            swapped.append(True)
        return original(ctx, event)

    monkeypatch.setattr(_Mechanics, '_observed_snapshot', observe)
    output = execute_mechanics(*arguments())
    assert swapped
    assert output['result'] is None
    assert output['status'] in ('pending', 'invalid')
    assert output['issues']


def test_observation_cannot_certify_detached_runtime_reader_definition(monkeypatch):
    from genshin_engine.mechanics import _Mechanics

    original = _Mechanics._observed_snapshot

    def altered(ctx, event):
        if event['id'] == 'independent-output':
            runtime_rule = copy.deepcopy(ctx.rules['independent'])
            runtime_rule['effects'][0]['value']['factor'] = 40
            ctx.rules['independent'] = runtime_rule
            assert ctx.model['rules'][0]['effects'][0]['value']['factor'] == 20
        return original(ctx, event)

    monkeypatch.setattr(_Mechanics, '_observed_snapshot', altered)
    output = execute_mechanics(*arguments())
    assert output['result'] is None, output['resource_counter_views']['trace']
    assert output['status'] in ('pending', 'invalid')
    assert output['issues']
