"""Actual queue obligations, before/after the opt-in RQ1 audit adapter."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.reaction_observation_compat import reaction_observation_view
from tests.test_reaction_operation_capabilities import setup


def enable(values):
    value = copy.deepcopy(values)
    selected = [o['id'] for o in value[2]['shared_pools']['operations']
                if o['trigger']['event'] == 'elemental_reaction']
    value[2]['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': selected}
    for operation in value[2]['shared_pools']['operations']:
        if operation['id'] in selected:
            operation['trigger']['reaction_types'] = ['swirl']
    return value


def test_actual_reaction_slots_exist_and_do_not_retroactively_grant_parent():
    values = enable(setup())
    before = copy.deepcopy(values)
    result = execute_mechanics(*values)
    assert result['result'] is not None, result['issues']
    audit = result['resource_reactions']
    assert audit['source_authority'] == 'caller_assumed'
    assert audit['execution'] == 'mechanics_event_queue'
    assert audit['verification_status'] == 'verified'
    assert audit['usage']['verification_work_reserved'] == 0
    assert audit['program']['program_fingerprint'] == audit['program_fingerprint']
    assert audit['verified_window'] == 'complete_reported_input'
    assert len(json.dumps(audit, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= audit['usage']['catalog_bytes_reserved']
    assert len(audit['occurrences']) == 3
    assert len(audit['operations']) == 3
    assert [r['state'] for r in audit['operations']] == ['committed'] * 3
    assert all(r['resource_scan_complete'] and r['callback_complete'] for r in audit['occurrences'])
    assert [h['expected'] for h in result['result']['hits'] if h['id'] in ('h0', 'h1')] == [45, 90]
    assert values == before


@pytest.mark.parametrize('name', ['threshold-observer-only', 'unrelated-typed-threshold', 'timer-counter', 'c2-pool'])
def test_legitimate_existing_combinations_keep_numerical_and_resource_receipts(name):
    baseline = json.loads((Path(__file__).parent / 'fixtures/resource_reactions/legal-combinations-before.json').read_text(encoding='utf-8'))['cases'][name]
    result = execute_mechanics(*enable(baseline['input']))
    assert result['result'] is not None, result['issues']
    assert reaction_observation_view(result, baseline['input'][1])['result']['hits'] == baseline['output']['result']['hits']
    assert result['shared_pool_trace'] == baseline['output']['shared_pool_trace']
    assert result['resource_reactions']['occurrences']


def _independent_budget_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode("utf8")


def _pre_reference_event(event):
    """Remove only the new exact event identity from the callback vocabulary."""
    from genshin_engine.runtime_event_match import validate_reference
    value = copy.deepcopy(event)
    if "execution_ref" in value:
        reference = value.pop("execution_ref")
        assert validate_reference(reference)
        assert reference["contract"] == "runtime-damage-match-v2"
        assert reference["character"] == value["character"]
        assert value["target_id"] == value["simulated_reaction"]["target_id"]
        assert reference["target_id"] == value.pop("target_id")
    return value


def _pre_reference_token(token):
    value = copy.deepcopy({key: getattr(token, key) for key in
        ("parent", "events", "directory", "capture_fingerprint", "plan", "candidates")})
    original = copy.deepcopy(value)
    value["events"] = [_pre_reference_event(row) for row in value["events"]]
    for row in [*value["directory"], *value["candidates"].values()]:
        row["event"] = _pre_reference_event(row["event"])
    return original, value


def _view_at_pre_budget_metadata_boundary(actual, historical, *, token_work=0):
    """Validate new budget/per-edge metering, then compare every legacy field."""
    if "attribute_ledger" not in historical:
        return actual
    limits = {"initials": 128, "contributions": 65536, "frames": 16384,
              "declaration_bytes": 16 * 1024 * 1024, "query_work": 1048576,
              "declaration_depth": 64, "registration_work": 1048576,
              "execution_work": 1048576, "receipt_bytes": 32 * 1024 * 1024}
    graph_limits = {"dependency_depth": 64, "output_bytes": 32 * 1024 * 1024}
    budget = actual["attribute_execution_budget"]
    assert budget == actual["result"]["attribute_execution_budget"]
    assert set(budget) == {"contract", "policy", "model_contract", "usage_fingerprint",
                           "declaration_fingerprint", "binding_fingerprint"}
    assert budget["contract"] == "attribute-execution-budget-v1"
    assert actual["result"]["calculation_producer_contract"] == "genshin-calculation-result-v2"
    assert budget["model_contract"] == actual["attribute_ledger"]["contract"]
    policy = budget["policy"]
    assert set(policy) == {"contract", "requested", "effective", "policy_fingerprint"}
    assert policy["contract"] == "attribute-budget-policy-v1"
    assert policy["requested"] == {}
    assert policy["effective"] == {**limits, **graph_limits}
    assert policy["policy_fingerprint"] == _independent_budget_hash(
        {key: value for key, value in policy.items() if key != "policy_fingerprint"})
    assert budget["usage_fingerprint"] == _independent_budget_hash(actual["attribute_ledger"]["usage"])
    assert budget["declaration_fingerprint"] == _independent_budget_hash(actual["attribute_ledger"]["declarations"])
    assert budget["binding_fingerprint"] == _independent_budget_hash(
        {key: value for key, value in budget.items() if key != "binding_fingerprint"})
    projected = copy.deepcopy(actual)
    del projected["attribute_execution_budget"]
    del projected["result"]["attribute_execution_budget"]
    del projected["result"]["calculation_producer_contract"]
    pairs = ((projected["attribute_ledger"], historical["attribute_ledger"]),
             (projected["result"]["attribute_ledger"], historical["result"]["attribute_ledger"]))
    for current, _ in pairs:
        assert current["usage"]["limits"] == limits
        assert current["usage"]["graph_limits"] == graph_limits
    for current, previous in pairs:
        current["usage"]["limits"] = copy.deepcopy(previous["usage"]["limits"])
        current["usage"]["graph_limits"] = copy.deepcopy(previous["usage"]["graph_limits"])
    if "attribute_callbacks" in historical:
        # R13 meters each actually expanded callback edge. The frozen oracle
        # predates that reservation. Verify the exact added work from the
        # already-bound materialized plans; never hide arbitrary work drift.
        edge_work = sum(len(row["materialized_input"]["edges"])
                        for row in actual["attribute_callbacks"]["plans"])
        callbacks = ((projected["attribute_callbacks"], historical["attribute_callbacks"]),
                     (projected["result"]["attribute_callbacks"], historical["result"]["attribute_callbacks"]))
        for current, previous in callbacks:
            assert current["usage"]["execution_work"] == previous["usage"]["execution_work"] + edge_work + token_work
        # deepcopy deliberately preserves the top/result audit alias.
        for current in {id(row): row for row, _ in callbacks}.values():
            previous = historical["attribute_callbacks"]
            trace_bytes = sum(len(_canonical_bytes(row)) for row in current["trace"])
            for row in current["trace"]:
                for candidate in row.get("directory", []):
                    candidate["event"] = _pre_reference_event(candidate["event"])
            old_bytes = sum(len(_canonical_bytes(row)) for row in current["trace"])
            assert current["usage"]["output_bytes"] == previous["usage"]["output_bytes"] + trace_bytes - old_bytes
            current["usage"]["output_bytes"] -= trace_bytes - old_bytes
            current["usage"]["execution_work"] -= edge_work + token_work
    return projected


def test_no_contract_output_remains_complete_exact(monkeypatch):
    from genshin_engine.attribute_callback_runtime import AttributeCallbackRuntime
    original_hash = AttributeCallbackRuntime._token_hash
    added_token_work = []
    def measured_hash(runtime, token):
        current, previous = _pre_reference_token(token)
        added_token_work.append(len(_canonical_bytes(current)) // 1024 - len(_canonical_bytes(previous)) // 1024)
        return original_hash(runtime, token)
    monkeypatch.setattr(AttributeCallbackRuntime, "_token_hash", measured_hash)
    baseline = json.loads((Path(__file__).parent / 'fixtures/resource_reactions/legal-combinations-before.json').read_text(encoding='utf-8'))['cases']
    for item in baseline.values():
        added_token_work.clear()
        actual = execute_mechanics(*item['input'])
        observed = reaction_observation_view(actual, item['input'][1])
        assert _view_at_pre_budget_metadata_boundary(observed, item['output'], token_work=sum(added_token_work)) == item['output']
        if 'attribute_callbacks' in observed:
            # The compatibility view must not hide an unrelated cost increase.
            for field in ('execution_work', 'output_bytes'):
                changed = copy.deepcopy(observed)
                changed['attribute_callbacks']['usage'][field] += 1
                with pytest.raises(AssertionError):
                    _view_at_pre_budget_metadata_boundary(changed, item['output'], token_work=sum(added_token_work))


def test_exactly_one_selector_and_ledger_call_per_actual_occurrence(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    from genshin_engine.shared_pools import SharedPoolLedger
    calls, mutations = [], []
    original = _Mechanics._filter_decision
    apply = SharedPoolLedger.apply_operation
    def select(self, selector, event, rule_id, **kwargs):
        if rule_id == 'shared_pools.operations.refill' and event['event'] == 'elemental_reaction':
            calls.append(event['id'])
        return original(self, selector, event, rule_id, **kwargs)
    def mutate(self, operation_id, **kwargs):
        mutations.append((operation_id, kwargs['run_id']))
        return apply(self, operation_id, **kwargs)
    monkeypatch.setattr(_Mechanics, '_filter_decision', select)
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', mutate)
    output = execute_mechanics(*enable(setup()))
    assert output['result'] is not None, output['issues']
    assert len(calls) == len(mutations) == 3
    assert len(set(calls)) == 3


def test_no_match_has_no_ledger_or_cursor_reads(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolLedger
    values = enable(setup())
    values[2]['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['melt']
    def forbidden(*args, **kwargs):
        raise AssertionError('No matching operation must not read the ledger cursor or mutate the pool.')
    monkeypatch.setattr(SharedPoolLedger, 'receipt_cursor', forbidden)
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', forbidden)
    output = execute_mechanics(*values)
    assert output['result'] is not None, output['issues']
    assert [r['state'] for r in output['resource_reactions']['operations']] == ['no_match'] * 3


def test_actual_reaction_actor_does_not_borrow_source_owner_identity():
    from tests.test_mechanics_execution import second_character
    values = enable(setup())
    second_character(values[0], values[1])
    values[2]['shared_pools']['operations'][0]['trigger']['characters'] = ['b']
    out = execute_mechanics(*values)
    assert out['result'] is not None, out['issues']
    assert all(r['owner'] == 'a' for r in out['resource_reactions']['occurrences'])
    assert all(r['state'] == 'no_match' for r in out['resource_reactions']['operations'])


def test_foreign_original_ledger_definition_cannot_borrow_live_operation(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def changed(self, *args):
        scope = original(self, *args)
        self.audit._bound_ledger._operations['refill']['amount'] += 1
        return scope
    monkeypatch.setattr(Runtime, 'begin', changed)
    out = execute_mechanics(*enable(setup()))
    assert out['status'] == 'pending' and out['result'] is None
    assert any(r['code'] == 'resource_reaction_program_changed' for r in out['issues'])
    assert not [r for r in out['shared_pool_trace'] if r['type'] == 'operation']


def test_copied_application_return_object_cannot_initiate_actual_scope(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def copied(self, event, hit, reaction_event, parent, application, reaction):
        copy_app = copy.deepcopy(application)
        return original(self, event, hit, reaction_event, parent, copy_app, copy_app['reaction'])
    monkeypatch.setattr(Runtime, 'begin', copied)
    out = execute_mechanics(*enable(setup()))
    assert out['status'] == 'pending' and out['result'] is None
    assert any(r['code'] == 'resource_reaction_actual_identity' for r in out['issues'])
    assert not [r for r in out['shared_pool_trace'] if r['type'] == 'operation']


def test_ledger_failure_has_failed_slot_parent_damage_and_no_fabricated_grant(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
    def fail(*args, **kwargs):
        raise SharedPoolError('pool_receipt_limit', '工程预算失败', severity='pending')
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', fail)
    output = execute_mechanics(*enable(setup()))
    assert output['status'] == 'pending' and output['result'] is None
    assert output['resource_reactions']['operations'][0]['state'] == 'failed'
    assert any(row['type'] == 'hit' for row in output['mechanics_trace'])
    assert not [row for row in output['shared_pool_trace'] if row['type'] == 'operation']
    assert output['rotation_check']['event_log'][-1]['status'] == 'pending'


def test_audit_slot_limit_stops_before_ledger_but_keeps_parent_prefix(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolLedger
    values = enable(setup())
    values[2]["execution_options"] = {"schema_version": 1, "resource_limits": {"slots": 1}}
    def forbidden(*args, **kwargs):
        raise AssertionError('The audit must reserve its operation slot before the ledger.')
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', forbidden)
    output = execute_mechanics(*values)
    assert output['status'] == 'pending' and output['result'] is None
    assert output['resource_reactions']['budget_policy']['requested'] == {'slots': 1}
    assert output['resource_reactions']['operations'] == []
    assert len(output['resource_reactions']['occurrences']) == 1
    assert any(row['type'] == 'hit' for row in output['mechanics_trace'])


def test_after_commit_cursor_failure_preserves_real_ledger_trace(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolLedger
    original = SharedPoolLedger.receipt_cursor
    reads = 0
    def fail_after(self):
        nonlocal reads
        reads += 1
        if reads == 2:
            raise RuntimeError('injected post-commit getter failure')
        return original(self)
    monkeypatch.setattr(SharedPoolLedger, 'receipt_cursor', fail_after)
    output = execute_mechanics(*enable(setup()))
    assert output['status'] == 'pending' and output['result'] is None
    assert output['resource_reactions']['operations'][0]['state'] == 'committed_inconsistent'
    operations = [r for r in output['shared_pool_trace'] if r['type'] == 'operation']
    assert len(operations) == 1 and operations[0]['accepted'] == 3
    assert output['shared_pool_state']['balances']['pool']['team'] == 3


@pytest.mark.parametrize('failure', ['exception', None])
def test_precommit_cursor_failure_is_controlled_pending_with_parent_prefix(monkeypatch, failure):
    from genshin_engine.shared_pools import SharedPoolLedger
    def fail(self):
        if failure is None:
            return
        raise RuntimeError('injected pre-commit cursor error')
    monkeypatch.setattr(SharedPoolLedger, 'receipt_cursor', fail)
    output = execute_mechanics(*enable(setup()))
    assert output['status'] == 'pending' and output['result'] is None
    assert output['rotation_check']['event_log'][-1]['status'] == 'pending'
    assert not [r for r in output['shared_pool_trace'] if r['type'] == 'operation']
    assert output['resource_reactions']['occurrences']
    assert output['resource_reactions']['operations'] == []


def test_final_verification_budget_failure_keeps_committed_rows_without_dps(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.attach
    def limited(self, output):
        self.audit._work_limit = self.audit._work + self.audit._tickets
        original(self, output)
    monkeypatch.setattr(Runtime, 'attach', limited)
    output = execute_mechanics(*enable(setup()))
    assert output['status'] == 'pending' and output['result'] is None
    assert output['resource_reactions']['verification_status'] == 'verification_pending'
    assert [r['state'] for r in output['resource_reactions']['operations']] == ['committed'] * 3
    assert len([r for r in output['shared_pool_trace'] if r['type'] == 'operation']) == 3


def test_full_model_actual_zero_reactions_is_valid_complete_zero():
    values = enable(setup())
    values[2]['elemental_model']['target']['initial_aura'] = None
    output = execute_mechanics(*values)
    assert output['result'] is not None, output['issues']
    assert output['resource_reactions']['verification_status'] == 'verified'
    assert output['resource_reactions']['occurrences'] == []
    assert output['resource_reactions']['operations'] == []


def test_threshold_failure_after_commit_keeps_real_grant_and_parent_damage(monkeypatch):
    from genshin_engine.resource_threshold_runtime import ThresholdRuntime
    from genshin_engine.shared_pools import SharedPoolError
    baseline = json.loads((Path(__file__).parent / 'fixtures/resource_reactions/legal-combinations-before.json').read_text(encoding='utf-8'))['cases']['unrelated-typed-threshold']
    original = ThresholdRuntime.after_operation
    def injected(self, ctx, event, phase, parent, operation_id, run_id):
        if operation_id == 'independent-income':
            raise SharedPoolError('threshold_dispatch_failed', '工程注入：资源提交后的阈值失败', severity='pending')
        return original(self, ctx, event, phase, parent, operation_id, run_id)
    monkeypatch.setattr(ThresholdRuntime, 'after_operation', injected)
    out = execute_mechanics(*enable(baseline['input']))
    assert out['status'] == 'pending' and out['result'] is None
    audit = out['resource_reactions']
    assert audit['operations'][0]['state'] == 'committed'
    assert not audit['occurrences'][0]['resource_scan_complete']
    assert not audit['occurrences'][0]['callback_complete']
    assert audit['verification_status'] == 'verified'
    assert any(r['type'] == 'hit' for r in out['mechanics_trace'])
    assert any(r['type'] == 'operation' and r['operation_id'] == 'independent-income' and r['accepted'] == 1
               for r in out['shared_pool_trace'])


def test_later_operation_slot_failure_keeps_prior_commit(monkeypatch):
    values = enable(setup())
    later = copy.deepcopy(values[2]['shared_pools']['operations'][0])
    later.update(id='second', source_id='binding:second', order=50)
    values[2]['shared_pools']['operations'].append(later)
    values[2]['resource_reaction_contract']['operation_ids'].append('second')
    values[2]['execution_options'] = {'schema_version': 1, 'resource_limits': {'slots': 2}}
    out = execute_mechanics(*values)
    assert out['status'] == 'pending' and out['result'] is None
    assert [r['state'] for r in out['resource_reactions']['operations']] == ['committed']
    assert [r['operation_id'] for r in out['shared_pool_trace'] if r['type'] == 'operation'] == ['refill']
    assert out['shared_pool_state']['balances']['pool']['team'] == 3


def test_a_historical_counter_frame_and_reaction_created_em_keep_original_order():
    from tests.test_mechanics_execution import condition
    from tests.test_resource_counter_view_execution import view_case
    from tests.test_swirl_dynamic_v3 import setup as elemental_case
    args = view_case(times=(.16, .25, .35))
    model = args[2]
    model['resource_counter_views']['schema_version'] = 2
    effect = model['rules'][0]['effects'][0]
    effect['domain'] = 'self'
    effect['value']['sample'] = 'self_frame'
    condition(model, 'output', .15, 5)
    source = elemental_case()[2]
    model['elemental_model'] = copy.deepcopy(source['elemental_model'])
    for descriptor in model['hit_events']:
        descriptor['self_snapshot_event'] = 'output'
        descriptor['application'] = copy.deepcopy(source['hit_events'][0]['application'])
    for hit in args[0]['hits']:
        hit['element'] = 'anemo'
    operation = copy.deepcopy(model['shared_pools']['operations'][0])
    operation.update(id='reaction-income', source_id='binding:reaction-income', operation='grant', amount=3,
        order=50, trigger={'event': 'elemental_reaction', 'phase': 'post', 'reaction_types': ['swirl']})
    operation.pop('spend_policy', None)
    model['shared_pools']['operations'].append(operation)
    before = execute_mechanics(*args)
    out = execute_mechanics(*enable(args))
    assert before['result'] is not None, before['issues']
    assert out['result'] is not None, out['issues']
    assert out['result']['hits'] == before['result']['hits']
    assert out['shared_pool_trace'] == before['shared_pool_trace']
    assert out['resource_counter_views']['trace'] == before['resource_counter_views']['trace']
    assert [r['reading']['raw_atoms'] for r in out['resource_counter_views']['trace'] if r['cycle'] == 0] == [8, 8, 8]


def test_multikey_grant_uses_one_call_one_cd_and_complete_span(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolLedger
    from tests.test_mechanics_execution import second_character
    values = enable(setup())
    second_character(values[0], values[1])
    config = values[2]['shared_pools']
    config['pools'][0].update(scope='per_character', initial={'a': 0, 'b': 0})
    config['operations'][0]['cooldown'].update(seconds=1)
    config['consumers'] = []
    values[2]['rules'][0].pop('resource_gate')
    calls = []
    original = SharedPoolLedger.apply_operation
    def counted(self, operation_id, **kwargs):
        calls.append(kwargs['run_id'])
        return original(self, operation_id, **kwargs)
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', counted)
    output = execute_mechanics(*values)
    assert output['result'] is not None, output['issues']
    rows = output['resource_reactions']['operations']
    assert len(calls) == len(rows) == 3
    assert all(r['actual_end'] - r['ledger_begin'] == 2 and r['keys'] == ['a', 'b'] for r in rows)
    granted = [r for r in output['shared_pool_trace'] if r['type'] == 'operation']
    assert [r['accepted'] for r in granted] == [3, 3, 0, 0, 0, 0]


@pytest.mark.parametrize('change', ['nested_owner', 'drop_callback_and_audit', 'drop_decision', 'duplicate_span',
    'key', 'wrong_parent', 'unknown_grant', 'drop_raw_receipt'])
def test_final_bidirectional_verification_rejects_trace_tampering(change):
    from genshin_engine.resource_reaction_execution import verify
    from genshin_engine.resource_reaction_program import Program, ReactionResourceError
    from genshin_engine.resource_reaction_runtime import digest
    values = enable(setup())
    out = execute_mechanics(*values)
    audit = out['resource_reactions']
    element = out['elemental_trace']
    if change == 'nested_owner': element[0]['reaction']['owner'] = 'b'
    elif change == 'drop_callback_and_audit':
        uid = audit['occurrences'][0]['reaction_run_id']
        out['mechanics_trace'] = [r for r in out['mechanics_trace'] if not (r['type'] == 'elemental_reaction' and r['run_id'] == uid)]
        audit['occurrences'].pop(0)
        audit['operations'].pop(0)
        out['shared_pool_trace'] = [r for r in out['shared_pool_trace'] if r['run_id'] != uid]
    elif change == 'drop_decision': audit['operations'].pop()
    elif change == 'duplicate_span':
        audit['operations'][1]['ledger_begin'] = audit['operations'][0]['ledger_begin']
        audit['operations'][1]['actual_end'] = audit['operations'][0]['actual_end']
    elif change == 'key': out['shared_pool_trace'][1]['key'] = 'unknown'
    elif change == 'wrong_parent': audit['occurrences'][0]['parent_run_id'] = 'fake'
    elif change == 'unknown_grant': out['shared_pool_trace'].append(copy.deepcopy(out['shared_pool_trace'][1]))
    elif change == 'drop_raw_receipt': out['shared_pool_trace'].pop(1)
    with pytest.raises(ReactionResourceError):
        verify(Program(values[2], ['a', 'b']).report(), audit['occurrences'], audit['operations'],
               out['rotation_check']['event_log'], out['mechanics_trace'], element, out['shared_pool_trace'], digest,
               runtime_events=[{'kind': 'mechanics_hit', 'id': row['hit_id'], 'data': {
                   'action_id': row['action_id'], 'descriptor': row,
                   'hit': next(h for h in values[0]['hits'] if h['id'] == row['hit_id'])}}
                   for row in values[2]['hit_events']])


@pytest.mark.parametrize('missed', [False, True])
def test_complete_zero_reaction_parent_still_requires_its_application(monkeypatch, missed):
    from genshin_engine.resource_reaction_runtime import Runtime
    values = enable(setup())
    values[2]['elemental_model']['target']['initial_aura'] = None
    if missed:
        values[2]['hit_events'][0]['application']['landed'] = False
    original = Runtime.attach
    def removed(self, output):
        run = self.context.elemental.trace[0]['run_id']
        self.context.elemental.trace[:] = [r for r in self.context.elemental.trace if r.get('run_id') != run]
        self.context.trace[:] = [r for r in self.context.trace
            if not (r.get('type') == 'elemental_application' and r.get('run_id') == run)]
        original(self, output)
    monkeypatch.setattr(Runtime, 'attach', removed)
    out = execute_mechanics(*values)
    assert out['result'] is None and out['resource_reactions']['verification_status'] == 'verification_pending'


def test_pending_parent_before_application_does_not_require_unexecuted_attempt(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    from genshin_engine.shared_pools import SharedPoolError
    original = _Mechanics.phase
    def before(self, event, phase, parent, **kwargs):
        if event['event'] == 'hit' and phase == 'pre':
            raise SharedPoolError('pool_event_limit', 'engineering pre-attempt stop', severity='pending')
        return original(self, event, phase, parent, **kwargs)
    monkeypatch.setattr(_Mechanics, 'phase', before)
    out = execute_mechanics(*enable(setup()))
    assert out['result'] is None and out['status'] == 'pending'
    assert not out.get('elemental_trace')
    assert out['resource_reactions']['verification_status'] == 'verified'
    assert out['resource_reactions']['verified_window'] == 'executed_prefix'


def test_final_scan_exhausted_work_rejects_before_scalar_encoding(monkeypatch):
    from genshin_engine import resource_reaction_program
    from genshin_engine.resource_reaction_runtime import Runtime
    original_attach = Runtime.attach
    original_encode = resource_reaction_program._canonical
    visits = []
    def attach(self, output):
        self.audit._work_limit = self.audit._work + self.audit._tickets
        with monkeypatch.context() as local:
            def encoded(value):
                visits.append(type(value).__name__)
                return original_encode(value)
            local.setattr(resource_reaction_program, '_canonical', encoded)
            original_attach(self, output)
    monkeypatch.setattr(Runtime, 'attach', attach)
    out = execute_mechanics(*enable(setup()))
    assert out['result'] is None and out['resource_reactions']['verification_status'] == 'verification_pending'
    assert visits == []


def test_known_callback_edge_work_projection_does_not_hide_unexpected_work():
    baseline = json.loads((Path(__file__).parent / 'fixtures/resource_reactions/legal-combinations-before.json').read_text(encoding='utf-8'))['cases']['c2-pool']
    actual = reaction_observation_view(execute_mechanics(*baseline['input']), baseline['input'][1])
    actual['attribute_callbacks']['usage']['execution_work'] += 1
    with pytest.raises(AssertionError):
        _view_at_pre_budget_metadata_boundary(actual, baseline['output'])
