"""Anonymous causal resource and direct energy settlement regressions."""
import copy

import pytest

from genshin_engine.execution_control import ExecutionCancelled, execution_control_scope
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_budget import normalize_resource_budget
from genshin_engine.resource_reaction_audit import Audit
from genshin_engine.resource_reaction_program import Program
from tests.test_mechanics_execution import EVIDENCE, add, condition, fixture, rule
from tests.test_reaction_operation_capabilities import setup
from tests.test_resource_reaction_audit import Ledger, context
from tests.test_resource_reaction_program import model
from tests.test_resource_threshold_execution import case


def direct_case():
    t, r, m = fixture((.2,))
    r['characters'][0].update(initial_energy=0, energy_recharge=2)
    condition(m, 'tick', .1)
    add(m, rule('refund', source_id='binding:refund', trigger={'event': 'condition', 'phase': 'post', 'event_ids': ['tick']},
        resources=[{'id': 'emission', 'kind': 'energy_direct', 'delay': .05, 'amounts': {'a': 3}, 'source_part': 'refund'}]))
    m['resource_ownership'] = [{'rule_id': 'refund', 'source_part': 'refund', 'source_ref': {
        'contract': 'resource-trigger-source-v1', 'kind': 'current_trigger'}, 'replaces_event_ids': []}]
    return t, r, m


def test_no_action_condition_has_real_cause_and_direct_energy_multiplies_er_once():
    args = direct_case()
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    row = next(e for e in out['rotation_check']['event_log'] if e['type'] == 'energy_direct')
    gain = row['energy'][0]
    assert gain['base_amount'] == 3 and gain['energy_recharge'] == 2
    assert gain['raw'] == 6 and gain['gain'] == 6
    assert row['resource_cause']['event_id'] == 'tick'
    assert row['resource_cause']['run_id'] == '0:tick'
    assert row['resource_cause']['action_id'] is None
    assert args == before


def test_direct_er_window_is_sampled_at_credit_and_capacity_overflow_recorded():
    args = direct_case()
    args[1]['energy_recharge_windows'] = [{'id': 'er', 'character': 'a', 'start': .12, 'end': .18, 'delta': 2, 'repeat': {'mode': 'each_cycle'}, **EVIDENCE}]
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    gain = next(e for e in out['rotation_check']['event_log'] if e['type'] == 'energy_direct')['energy'][0]
    assert gain['energy_recharge'] == 4 and gain['raw'] == 12
    assert gain['gain'] == 10 and gain['overflow'] == 2


def test_rule_aliases_cannot_duplicate_same_source_benefit():
    t, r, m = direct_case()
    alias = copy.deepcopy(m['rules'][0]); alias['id'] = 'alias'
    add(m, alias)
    owner = copy.deepcopy(m['resource_ownership'][0]); owner['rule_id'] = 'alias'
    m['resource_ownership'].append(owner)
    out = execute_mechanics(t, r, m)
    assert out['result'] is None
    assert any(i['code'] == 'duplicate_delivery' for i in out['issues'])


def test_resource_budget_instances_are_independent_and_cancel_before_prepare():
    a = Audit(Program(model(), ['a', 'b']), Ledger(), lambda _: None, limits={'slots': 1})
    b = Audit(Program(model(), ['a', 'b']), Ledger(), lambda _: None)
    assert a.limits['slots'] == 1 and b.limits['slots'] == 20000
    cancelled = [False]
    with execution_control_scope(lambda: cancelled[0]):
        cancelled[0] = True
        with pytest.raises(ExecutionCancelled):
            a.begin(context())
    report = a.report()
    assert report['termination'] == 'resource_execution_cancelled'
    assert report['occurrences'] == []
    assert report['budget_policy']['contract'] == 'resource-execution-budget-v1'


def test_saved_budget_roundtrip_unknown_policy_rejected_and_concurrent_limits_isolated():
    import json
    from concurrent.futures import ThreadPoolExecutor

    from genshin_engine.resource_budget import validate_saved_resource_budget
    policy = normalize_resource_budget({'work': 64000000, 'slots': 80000, 'position_work': 80000})
    assert validate_saved_resource_budget(json.loads(json.dumps(policy))) == policy
    bad = copy.deepcopy(policy); bad['contract'] = 'future-unknown'
    with pytest.raises(ValueError):
        validate_saved_resource_budget(bad)
    def execute(slots):
        audit = Audit(Program(model(), ['a', 'b']), Ledger(), lambda _: None, limits={'slots': slots})
        audit.begin(context())
        return audit.report()['budget_policy']['effective']['slots']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(execute, [1, 2])) == [1, 2]


def reaction_threshold_case():
    t, r, m = setup()
    m['shared_pools']['consumers'] = []
    m['shared_pools']['schema_version'] = 2
    m['shared_pools']['pools'][0]['unit_scale'] = 1
    m['rules'] = []; m['initial_state']['rules'] = []
    m['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    m['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    _, _, threshold = case()
    m['resource_threshold_observers'] = threshold['resource_threshold_observers']
    m['resource_threshold_observers']['observers'][0]['threshold_atoms'] = 1
    m['resource_threshold_triggers'] = threshold['resource_threshold_triggers']
    m['resource_threshold_triggers']['schema_version'] = 4
    add(m, threshold['rules'][0])
    return t, r, m


def test_actual_reaction_committed_resource_crossing_activates_only_later_benefit():
    out = execute_mechanics(*reaction_threshold_case())
    assert out['result'] is not None, out['issues']
    assert out['resource_reactions']['verification_status'] == 'verified'
    rows = out['resource_threshold_triggers']['trace']
    assert len(rows) == 1 and rows[0]['outcome'] == 'activated'
    assert rows[0]['source_run_id'] == out['resource_reactions']['occurrences'][0]['reaction_run_id']


def test_reaction_threshold_requires_new_version_and_actual_audit_contract():
    args = reaction_threshold_case()
    args[2]['resource_threshold_triggers']['schema_version'] = 3
    out = execute_mechanics(*args)
    assert out['result'] is None and any(i['code'] == 'threshold_dispatch_origin' for i in out['issues'])
    issue = next(i for i in out['issues'] if i['code'] == 'threshold_dispatch_origin')
    assert 'v4' in issue['message'] and '实际反应' in issue['message']


def test_runtime_semantic_match_resolves_actual_hit_without_guessing_generated_id():
    t, r, m = fixture((.2, .3))
    add(m, rule())
    match = {'rule_id': 'boost', 'effect_id': 'value', 'operation': 'exclude', 'event_ref': {
        'contract': 'runtime-damage-match-v1', 'source_action_id': 'cast', 'character': 'a',
        'damage_system': 'classic', 'origin': 0, 'time_us': 200000}}
    out = execute_mechanics(t, r, m, [match])
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == [45, 90]


@pytest.mark.parametrize('times,code', [((.3,), 'runtime_match_no_match'), ((.2, .2), 'runtime_match_ambiguous')])
def test_runtime_match_zero_or_multiple_candidates_never_selects_first(times, code):
    t, r, m = fixture(times)
    add(m, rule())
    match = {'rule_id': 'boost', 'effect_id': 'value', 'operation': 'exclude', 'event_ref': {
        'contract': 'runtime-damage-match-v1', 'source_action_id': 'cast', 'character': 'a',
        'damage_system': 'classic', 'origin': 0, 'time_us': 200000}}
    out = execute_mechanics(t, r, m, [match])
    assert out['status'] == 'pending'
    assert any(i['code'] == code for i in out['issues'])


def test_resource_handler_exposes_valid_typed_pool_input():
    from genshin_app.equipment.agent_execution_materialization import (
        HANDLER_REGISTRY,
        agent_execution_materialization_capabilities,
    )
    from genshin_app.equipment.agent_execution_runtime import runtime_handler_capability
    caps = agent_execution_materialization_capabilities()
    payload = caps['families']['resource_operation']['minimal_payload']
    assert HANDLER_REGISTRY['resource_operation'].validate_payload(payload, 'test') == payload
    assert runtime_handler_capability('resource_operation', 1)['status'] == 'supported'


def test_resource_operation_adapter_installs_then_actual_ledger_consumes():
    from types import SimpleNamespace

    from genshin_app.equipment.agent_execution_materialization import (
        agent_execution_materialization_capabilities,
    )
    from genshin_app.equipment.agent_execution_runtime import (
        _install_resource_operation_entry,
    )
    payload = copy.deepcopy(agent_execution_materialization_capabilities()['families']['resource_operation']['minimal_payload'])
    payload['shared_pools']['pools'][0]['owner'] = 'a'
    t, r, m = fixture((.2,))
    entry = {'entry_id': 'grant', 'family': 'resource_operation', 'coverage_keys': ['grant'], 'payload': payload}
    ctx = SimpleNamespace(mechanics=m, rotation=r, rows_by_id={})
    _install_resource_operation_entry(ctx, entry)
    assert ctx.rows_by_id['grant']['status'] == 'installed'
    out = execute_mechanics(t, r, ctx.mechanics)
    assert out['result'] is not None, out['issues']
    receipts = [e for e in out['shared_pool_trace'] if e['type'] == 'operation' and e['cycle'] == 0]
    assert len(receipts) == 1 and receipts[0]['accepted'] == 1


def test_same_action_independent_source_resources_coexist_without_free_text_dedup():
    args = direct_case()
    m = args[2]
    first = m['rules'][0]
    first['trigger'] = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    m['resource_ownership'][0].pop('source_ref')
    m['resource_ownership'][0]['action_id'] = 'cast'
    other = copy.deepcopy(first)
    other.update(id='independent', source_id='binding:independent', source=first['source'])
    add(m, other)
    owner = copy.deepcopy(m['resource_ownership'][0]); owner['rule_id'] = 'independent'
    m['resource_ownership'].append(owner)
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    rows = [e for e in out['rotation_check']['event_log'] if e['type'] == 'energy_direct' and e['cycle'] == 0]
    assert len(rows) == 2
    assert {r['resource_cause']['source_id'] for r in rows} == {'binding:refund', 'binding:independent'}


@pytest.mark.parametrize('independent', [False, True])
def test_shared_pool_operation_alias_cannot_repay_same_benefit(independent):
    from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
    from tests.test_shared_pools import config
    pools = config(); pools['consumers'] = []
    op = pools['operations'][0]
    alias = copy.deepcopy(op); alias.update(id='alias', order=op['order'] + 1)
    if independent:
        alias['source_id'] += ':independent-part'
    pools['operations'].append(alias)
    ledger = SharedPoolLedger(pools, character_ids=['a', 'b'], rule_ids=[])
    context = {'event_id': 'h', 'run_id': '0:h', 't_us': 1, 'phase': op['trigger']['phase'],
        'event_kind': op['trigger']['event'], 'keys': ['team'], 'eligible': True}
    ledger.apply_operation(op['id'], **context)
    before = ledger.snapshot()
    if independent:
        assert ledger.apply_operation('alias', **context)
    else:
        with pytest.raises(SharedPoolError) as exc:
            ledger.apply_operation('alias', **context)
        assert exc.value.issue['code'] == 'pool_duplicate_benefit'
        detail = exc.value.issue['details']
        assert detail['incoming_operation_id'] == 'alias'
        assert detail['existing_operation_ids'] == [op['id']]
        assert detail['pool_id'] == op['pool_id'] and detail['source_id'] == op['source_id']
        assert detail['run_id'] == '0:h' and detail['keys'] == ['team']
        assert ledger.snapshot() == before


def test_actual_reaction_resource_can_start_a_finite_lifecycle():
    from genshin_engine.state_runtime_mechanics import route_event_id
    from tests.test_resource_threshold_lifecycle_execution import life_case
    args = reaction_threshold_case()
    m = args[2]
    reference = life_case(counter=False)[2]
    field = copy.deepcopy(reference['state_runtime']['lifecycles'][-1])
    binding = m['resource_threshold_triggers']['bindings'][0]
    field.update(source_id=binding['source_id'], start_order=binding['order'], end_order=20,
        start={'kind': 'resource_threshold', 'binding_id': binding['id']})
    timer = copy.deepcopy(reference['state_runtime']['timers'][-1])
    timer['source_id'] = binding['source_id']
    m['state_runtime'] = {'schema_version': 2, 'lifecycles': [field], 'timers': [timer]}
    binding['payload'] = {'kind': 'start_lifecycle', 'ref_id': field['id']}
    target = m['rules'][0]
    target['trigger'] = {'event': 'condition', 'phase': 'pre', 'event_ids': [route_event_id('dispatch_rule', 'boost')]}
    target['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre'}
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    completed = [r for r in out['resource_threshold_triggers']['trace'] if r['type'] == 'threshold_dispatch_completed']
    assert len(completed) == 1 and completed[0]['outcome'] == 'lifecycle_started'
    assert out['resource_reactions']['verification_status'] == 'verified'


@pytest.mark.parametrize('source_ref', [{'source_action_id': 'cast'}, {'source_event_id': 'generated'}])
def test_runtime_match_consumes_real_generated_stellar_hit(source_ref):
    from tests.test_direct_stellar_damage_contract import direct_stellar_profile
    from tests.test_elemental_multiaura_v4 import aura
    from tests.test_special_reaction_mechanics_projection import (
        _application_fixture,
        _enable,
    )
    t, r, m = _application_fixture('electro', initial=[aura('cryo', .8, 'a')], times=(.2, 1.0))
    r['abilities'][0].update(variant='tap', effective_level=10)
    _enable(m, 'stellar_superconduct')
    t['hits'][1]['element'] = 'cryo'
    m['hit_events'][1]['application'] = {'profile_id': 'cryo', 'target_id': 'enemy', 'landed': True, 'area_id': 'battlefield.primary'}
    m['special_reaction_events'] = [{'id': 'generated', 'kind': 'direct_stellar_damage',
        'action_id': 'cast', 'target_id': 'enemy', 'area_id': 'battlefield.primary', 't': 4.2, 'order': 20,
        'ability_profile': direct_stellar_profile(character_id='a', ability_id='e'), **EVIDENCE}]
    add(m, rule('star', benefit='benefit.stellar_mult.all', value=2, duration=10))
    m['rules'][-1]['effects'][0]['filter'] = {'event': 'reaction_damage', 'phase': 'pre'}
    baseline = execute_mechanics(t, r, m)
    match = {'rule_id': 'star', 'effect_id': 'value', 'operation': 'exclude', 'event_ref': {
        'contract': 'runtime-damage-match-v1', **source_ref, 'character': 'a',
        'damage_system': 'stellar', 'origin': 0, 'time_us': 4200000, 'reaction_type': 'stellar_superconduct'}}
    out = execute_mechanics(t, r, m, [match])
    assert out['result'] is not None, out['issues']
    refs = [x for x in out['mechanics_trace'] if x['type'] == 'runtime_match_resolution']
    assert refs[0]['status'] == 'resolved'
    hid = refs[0]['candidates'][0][1]
    before = next(h['expected'] for h in baseline['result']['hits'] if h['id'] == hid)
    after = next(h['expected'] for h in out['result']['hits'] if h['id'] == hid)
    assert before > after
